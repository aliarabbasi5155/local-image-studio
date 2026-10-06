"""Collection behavior, destructive-operation boundaries and measured progress."""
import io
import json
import time
import unittest
import uuid
import zipfile
from types import SimpleNamespace

import aiohttp
from PIL import Image

import test_projects as fixtures
from studio.server import STATE
from studio.storage import Store


class CollectionTests(unittest.IsolatedAsyncioTestCase):
    # Use the fixture methods without inheriting its existing test cases.
    asyncSetUp = fixtures.ProjectTests.asyncSetUp
    asyncTearDown = fixtures.ProjectTests.asyncTearDown
    upload = fixtures.ProjectTests.upload
    complete = fixtures.ProjectTests.complete
    edit = fixtures.ProjectTests.edit

    async def favorite(self, pid, vid, value=True):
        response = await self.client.patch(f'/api/projects/{pid}/versions/{vid}', json={'favorite':value})
        self.assertEqual(response.status,200)

    async def test_favorites_are_individual_images_and_projects_sort_by_last_edit(self):
        root = await self.upload()
        other = await self.upload()
        child, = await self.edit(root['project_id'],root['id'])
        await self.favorite(root['project_id'],root['id'])
        await self.favorite(root['project_id'],child)
        library = await (await self.client.get('/api/projects')).json()
        self.assertEqual([p['id'] for p in library['projects']],[root['project_id'],other['project_id']])
        self.assertEqual(library['projects'][0]['favorite_count'],2)
        self.assertEqual(library['counts']['favorites'],2)
        favorites = await (await self.client.get('/api/favorites')).json()
        self.assertEqual([p['id'] for p in favorites['images']],[child,root['id']])
        self.assertEqual(len(favorites['navigation']),1)
        await self.favorite(root['project_id'],child,False)
        self.assertTrue(self.store.projects.detail(root['project_id'])['favorite'])
        response = await self.client.delete(f'/api/projects/{root["project_id"]}/versions/{child}',json={'confirm':True})
        self.assertEqual(response.status,200)
        library = await (await self.client.get('/api/projects')).json()
        self.assertEqual(library['projects'][0]['id'],other['project_id'])

    async def test_bulk_favorites_validate_every_id_before_mutation_and_apply_scope(self):
        root,other = await self.upload(),await self.upload()
        child, = await self.edit(root['project_id'],root['id'])
        response = await self.client.post('/api/bulk',json={'scope':'images','ids':[child,str(uuid.uuid4())],'action':'favorite','favorite':True})
        self.assertEqual(response.status,400)
        self.assertFalse(self.store.projects.version(child)['favorite'])
        response = await self.client.post('/api/bulk',json={'scope':'projects','ids':[root['project_id']],'action':'favorite','favorite':True})
        self.assertEqual(response.status,200)
        self.assertTrue(self.store.projects.version(child)['favorite'])
        self.assertTrue(self.store.projects.version(root['id'])['favorite'])
        self.assertFalse(self.store.projects.version(other['id'])['favorite'])
        response = await self.client.post('/api/bulk',json={'scope':'images','ids':[child],'action':'favorite','favorite':False})
        self.assertEqual(response.status,200)
        self.assertEqual(self.store.projects.detail(root['project_id'])['favorite_count'],1)
        for ids in ([],['bad'],[True],[root['id']]*501):
            response = await self.client.post('/api/bulk',json={'scope':'images','ids':ids,'action':'delete','confirm':True})
            self.assertEqual(response.status,400)

    async def test_zip_contains_only_selected_full_images_and_png_metadata_or_jpg(self):
        root,other = await self.upload(),await self.upload()
        child, = await self.edit(root['project_id'],root['id'])
        for scope,ids,expected in [('projects',[root['project_id']],{root['id'],child}),('images',[child],{child})]:
            for image_format in ('png','jpg'):
                response = await self.client.post('/api/bulk-download',json={'scope':scope,'ids':ids,'format':image_format})
                self.assertEqual(response.status,200)
                download = await response.json()
                response = await self.client.get(download['url'])
                self.assertEqual(response.status,200)
                self.assertEqual(response.headers['Content-Type'],'application/zip')
                archive = zipfile.ZipFile(io.BytesIO(await response.read()))
                self.assertEqual({name.rsplit('/',1)[1][7:].rsplit('.',1)[0] for name in archive.namelist()},expected)
                for name in archive.namelist():
                    image_id = name.rsplit('/',1)[1][7:].rsplit('.',1)[0]
                    with Image.open(io.BytesIO(archive.read(name))) as image:
                        self.assertEqual(image.size,(240,160))
                        self.assertEqual(image.format,'PNG' if image_format=='png' else 'JPEG')
                        if image_format=='png' and image_id==child:
                            self.assertEqual(json.loads(image.info['studio']),self.store.image(child)['recipe'])
                token = download['url'].rsplit('/',1)[1]
                entry = self.app[STATE]['downloads'].entries[token]
                entry['expires'] = time.time()-1
                self.assertEqual((await self.client.get(download['url'])).status,404)
                self.assertFalse(entry['path'].exists())
        await self.client.post('/api/logout',json={})
        for url in ('/api/favorites',download['url']):
            self.assertEqual((await self.client.get(url)).status,401)
        self.assertEqual((await self.client.post('/api/bulk-download',json={})).status,401)

    async def test_bulk_delete_protects_active_projects_then_removes_only_selected_families(self):
        root,other,kept = await self.upload(),await self.upload(),await self.upload()
        child, = await self.edit(root['project_id'],root['id'])
        response = await self.client.post('/api/generate',json={'mode':'edit','prompt':'Wait','source_id':other['id']})
        job_id = (await response.json())['jobs'][0]
        payload = {'scope':'projects','ids':[root['project_id'],other['project_id']],'action':'delete','confirm':True}
        self.assertEqual((await self.client.post('/api/bulk',json=payload)).status,409)
        self.assertTrue(self.store.projects.version(child))
        await self.client.post('/api/jobs/'+job_id+'/cancel',json={})
        self.assertEqual((await self.client.post('/api/bulk',json=payload)).status,200)
        self.assertEqual(self.store.projects.library()['total'],1)
        self.assertEqual((await self.client.get(kept['url'])).status,200)
        self.assertFalse((self.store.directory/'uploads'/(root['id']+'.png')).exists())

    async def test_bulk_delete_original_and_parent_keeps_descendant_comparisons_then_cleans_empty_project(self):
        response = await self.client.post('/api/generate',json={'prompt':'Original','quality':'fast'})
        root = (await response.json())['jobs'][0]
        self.complete(root)
        child, = await self.edit(root,root)
        grandchild, = await self.edit(root,child)
        payload = {'scope':'images','ids':[root,child],'action':'delete','confirm':True}
        self.assertEqual((await self.client.post('/api/bulk',json=payload)).status,200)
        project = self.store.projects.detail(root)
        self.assertEqual([v['id'] for v in project['versions']],[grandchild])
        self.assertTrue(project['root']['recovered'])
        self.assertEqual((await self.client.get(project['root']['url'])).status,200)
        self.assertEqual((await self.client.get(project['versions'][0]['parent']['url'])).status,200)
        self.assertTrue(project['versions'][0]['parent']['deleted'])
        payload['ids'] = [grandchild]
        self.assertEqual((await self.client.post('/api/bulk',json=payload)).status,200)
        self.assertIsNone(self.store.projects.detail(root))
        self.assertEqual(len(list((self.store.directory/'uploads').glob('*.png'))),0)

    async def test_original_without_children_is_fully_deleted_and_unready_download_is_rejected(self):
        root = await self.upload()
        self.assertEqual((await self.client.delete(f'/api/projects/{root["project_id"]}/versions/{root["id"]}',json={'confirm':True})).status,200)
        self.assertIsNone(self.store.projects.detail(root['project_id']))
        self.assertFalse((self.store.directory/'uploads'/(root['id']+'.png')).exists())
        response = await self.client.post('/api/generate',json={'prompt':'Pending','quality':'fast'})
        job_id = (await response.json())['jobs'][0]
        self.assertEqual((await self.client.post('/api/bulk-download',json={'scope':'images','ids':[job_id]})).status,409)

    async def test_timing_does_not_extrapolate_and_step_progress_clears_between_stages(self):
        response = await self.client.post('/api/generate',json={'prompt':'Original','quality':'fast'})
        root = (await response.json())['jobs'][0]
        self.complete(root,40)
        recipe = self.store.job(root)['recipe']
        metrics = self.app[STATE]['metrics']
        self.assertEqual(metrics.estimate(recipe)['seconds'],40)
        for altered in (dict(recipe,generation_width=4096,generation_height=4096,width=4096,height=4096),
                        dict(recipe,processing={'method':'masked_patch'}),dict(recipe,snofs=False)):
            self.assertIsNone(metrics.estimate(altered)['seconds'])
        response = await self.client.post('/api/generate',json={'prompt':'Progress','quality':'fast'})
        job_id = (await response.json())['jobs'][0]
        prompt_id = str(uuid.uuid4())
        async def events():
            for data in [{'prompt_id':'unrelated','value':3,'max':4},{'prompt_id':prompt_id,'value':2,'max':4}]:
                yield SimpleNamespace(type=aiohttp.WSMsgType.TEXT,data=json.dumps({'type':'progress','data':data}))
        await self.app[STATE]['engine'].watch(events(),job_id,prompt_id)
        self.assertEqual(self.store.job(job_id)['progress_detail'],{'stage':'sampling','value':2,'total':4})
        response = await self.client.get('/api/jobs')
        reported = next(j for j in (await response.json())['jobs'] if j['id']==job_id)
        self.assertEqual(reported['progress_detail']['value'],2)
        self.store.update_job(job_id,phase='Saving your image')
        self.assertIsNone(self.store.job(job_id)['progress_detail'])

    async def test_existing_project_favorite_migrates_to_original_once(self):
        root = await self.upload()
        child, = await self.edit(root['project_id'],root['id'])
        self.store.execute("DELETE FROM studio_migrations WHERE name='image-favorites'")
        self.store.execute('UPDATE projects SET favorite=1 WHERE id=?',(root['project_id'],))
        migrated = Store(self.temp.name)
        self.assertTrue(migrated.projects.version(root['id'])['favorite'])
        self.assertFalse(migrated.projects.version(child)['favorite'])
        with migrated.db:
            migrated.projects.set_favorite(root['id'],False)
        migrated.close()
        restarted = Store(self.temp.name)
        self.assertFalse(restarted.projects.version(root['id'])['favorite'])
        restarted.close()

