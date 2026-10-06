import hashlib
import io
import json
import shutil
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

import aiohttp
from aiohttp.test_utils import TestClient, TestServer
from PIL import Image, PngImagePlugin

from studio.engine import make_thumbnail
from studio.server import STATE, create_app
from studio.storage import Store


class ProjectTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = create_app(self.temp.name,pin="123456",start_worker=False)
        self.store = self.app[STATE]["store"]
        async def request(*args,**kwargs):
            return {"system":{"ram_total":24*1024**3,"ram_free":8*1024**3}}
        self.app[STATE]["engine"].request = request
        self.client = TestClient(TestServer(self.app),cookie_jar=aiohttp.CookieJar(unsafe=True))
        await self.client.start_server()
        await self.client.post('/api/login',json={"pin":"123456"})

    async def asyncTearDown(self):
        await self.client.close()
        self.temp.cleanup()

    async def upload(self):
        stream = io.BytesIO()
        Image.new('RGB',(240,160),'navy').save(stream,'PNG')
        form = aiohttp.FormData()
        form.add_field('image',stream.getvalue(),filename='Example.png',content_type='image/png')
        response = await self.client.post('/api/uploads',data=form)
        self.assertEqual(response.status,201)
        return await response.json()

    def complete(self,image_id,seconds=20):
        job = self.store.job(image_id)
        recipe = job['recipe']
        path = self.store.directory/'images'/(image_id+'.png')
        info = PngImagePlugin.PngInfo()
        info.add_text('studio',json.dumps(recipe))
        Image.new('RGB',(recipe['width'],recipe['height']),'coral').save(path,pnginfo=info)
        make_thumbnail(path,self.store.directory/'thumbs'/(image_id+'.webp'))
        self.store.execute('INSERT INTO images (id,created,filename,recipe) VALUES (?,?,?,?)',(image_id,time.time(),path.name,json.dumps(recipe)))
        self.store.update_job(image_id,status='complete',started=time.time()-seconds,finished=time.time(),progress=1)

    async def edit(self,project_id,version_id,count=1):
        source_response = await self.client.post(f'/api/projects/{project_id}/versions/{version_id}/edit-source',json={})
        self.assertEqual(source_response.status,201)
        source = await source_response.json()
        response = await self.client.post('/api/generate',json={'mode':'edit','prompt':'Add a flower','quality':'fast','output_size':'original','resize_mode':'direct',
            'source_id':source['id'],'project_id':project_id,'variations':count,'seed':40})
        self.assertEqual(response.status,202)
        jobs = (await response.json())['jobs']
        for image_id in jobs:
            self.complete(image_id)
        return jobs

    async def test_upload_is_a_root_before_any_edit_and_each_creation_has_its_own_root(self):
        upload = await self.upload()
        project = await (await self.client.get('/api/projects/'+upload['project_id'])).json()
        self.assertEqual(len(project['versions']),1)
        self.assertEqual(project['root']['kind'],'upload')
        self.assertEqual(project['root']['sequence'],0)
        self.assertTrue(project['root']['ready'])
        favorite = await self.client.patch(f'/api/projects/{upload["project_id"]}/versions/{upload["id"]}',json={'favorite':True})
        self.assertEqual(favorite.status,200)
        favorites = await (await self.client.get('/api/projects?section=favorites')).json()
        self.assertEqual(favorites['total'],1)
        response = await self.client.post('/api/generate',json={'prompt':'A fox','quality':'fast','variations':2})
        ids = (await response.json())['jobs']
        self.assertNotEqual(self.store.job(ids[0])['recipe']['project_id'],self.store.job(ids[1])['recipe']['project_id'])
        library = await (await self.client.get('/api/projects')).json()
        self.assertEqual(library['total'],3)
        self.assertEqual(len(library['projects'][0]['pending']),1)
        estimate = await self.client.post('/api/estimate',json={'mode':'edit','project_id':upload['project_id'],'version_id':upload['id']})
        self.assertEqual(estimate.status,200)
        self.assertIsNone((await estimate.json())['seconds'])

    async def test_branches_deleted_parent_and_reediting_descendant_remain_in_one_project(self):
        root = await self.upload()
        pid = root['project_id']
        one, = await self.edit(pid,root['id'])
        two,three = await self.edit(pid,one,2)
        four, = await self.edit(pid,two)
        five, = await self.edit(pid,four)
        response = await self.client.delete(f'/api/projects/{pid}/versions/{four}',json={'confirm':True})
        self.assertEqual(response.status,200)
        project = await (await self.client.get('/api/projects/'+pid)).json()
        self.assertEqual([v['sequence'] for v in project['versions']],[0,1,2,3,5])
        self.assertEqual([v['parent_id'] for v in project['versions'][2:4]],[one,one])
        child = next(v for v in project['versions'] if v['id']==five)
        self.assertEqual(child['parent']['id'],four)
        self.assertTrue(child['parent']['deleted'])
        self.assertEqual((await self.client.get(child['parent']['url'])).status,200)
        self.assertFalse((self.store.directory/'images'/(four+'.png')).exists())
        self.assertIsNone(self.store.job(four))
        six, = await self.edit(pid,five)
        self.assertEqual(self.store.job(six)['recipe']['parent_version_id'],five)
        self.assertEqual((await (await self.client.get('/api/projects')).json())['total'],1)
        self.assertEqual((await self.client.delete(f'/api/projects/{pid}/versions/{root["id"]}',json={'confirm':True})).status,409)

    async def test_cross_project_source_is_rejected_and_parent_is_derived_from_saved_source(self):
        root,other = await self.upload(),await self.upload()
        response = await self.client.post('/api/generate',json={'mode':'edit','prompt':'Edit','source_id':root['id'],'project_id':other['project_id']})
        self.assertEqual(response.status,400)
        response = await self.client.post('/api/generate',json={'mode':'edit','prompt':'Edit','source_id':root['id'],'project_id':root['project_id'],'parent_image_id':'wrong'})
        job_id = (await response.json())['jobs'][0]
        self.assertEqual(self.store.job(job_id)['recipe']['parent_version_id'],root['id'])
        self.assertIsNone(self.store.job(job_id)['recipe']['parent_image_id'])

    async def test_whole_project_delete_is_confirmed_atomic_and_keeps_other_projects(self):
        root,other = await self.upload(),await self.upload()
        one, = await self.edit(root['project_id'],root['id'])
        two, = await self.edit(root['project_id'],one)
        response = await self.client.delete('/api/projects/'+root['project_id'],json={})
        self.assertEqual(response.status,400)
        response = await self.client.post('/api/generate',json={'mode':'edit','prompt':'Waiting','source_id':root['id']})
        pending_id = (await response.json())['jobs'][0]
        response = await self.client.delete('/api/projects/'+root['project_id'],json={'confirm':True})
        self.assertEqual(response.status,409)
        self.assertTrue((self.store.directory/'images'/(one+'.png')).exists())
        await self.client.post(f'/api/jobs/{pending_id}/cancel',json={})
        source_ids = [r['id'] for r in self.store.rows('SELECT id FROM project_sources WHERE project_id=?',(root['project_id'],))]
        response = await self.client.delete('/api/projects/'+root['project_id'],json={'confirm':True})
        self.assertEqual(response.status,200)
        for image_id in (one,two):
            self.assertIsNone(self.store.image(image_id))
            self.assertFalse((self.store.directory/'images'/(image_id+'.png')).exists())
        for source_id in source_ids:
            self.assertFalse((self.store.directory/'uploads'/(source_id+'.png')).exists())
        self.assertEqual((await self.client.get('/api/projects/'+root['project_id'])).status,404)
        self.assertEqual((await self.client.get('/api/projects/'+other['project_id'])).status,200)
        self.assertEqual((await self.client.get(other['url'])).status,200)

    async def test_timing_range_queue_wait_metrics_and_pin_access(self):
        response = await self.client.post('/api/generate',json={'prompt':'Example','quality':'fast'})
        image_id = (await response.json())['jobs'][0]
        self.complete(image_id,100)
        prediction = await (await self.client.post('/api/estimate',json={'quality':'fast'})).json()
        self.assertGreater(prediction['high'],prediction['seconds'])
        self.assertLess(prediction['low'],prediction['seconds'])
        self.assertEqual(prediction['samples'],1)
        response = await self.client.post('/api/generate',json={'prompt':'Examples','quality':'fast','variations':2})
        ids = (await response.json())['jobs']
        queue = self.app[STATE]['metrics'].queue()
        self.assertEqual(queue[ids[1]]['queue_position'],1)
        self.assertGreater(queue[ids[1]]['remaining_high'],queue[ids[0]]['remaining_high'])
        self.store.update_job(ids[0],started=time.time()-10000)
        self.assertTrue(self.app[STATE]['metrics'].queue()[ids[0]]['overdue'])
        status = await (await self.client.get('/api/metrics')).json()
        self.assertEqual(status['memory_free'],8*1024**3)
        self.assertEqual(status['active_images'],2)
        self.assertAlmostEqual(status['last_image_seconds'],100,delta=1)
        await self.client.post('/api/logout',json={})
        for endpoint in ('/api/metrics','/api/projects','/api/projects/'+image_id):
            self.assertEqual((await self.client.get(endpoint)).status,401)
        self.assertEqual((await self.client.post('/api/estimate',json={})).status,401)

    async def test_legacy_migration_groups_parent_links_without_touching_images_or_recipes(self):
        response = await self.client.post('/api/generate',json={'prompt':'Original','quality':'fast'})
        root_id = (await response.json())['jobs'][0]
        self.complete(root_id)
        pid = self.store.job(root_id)['recipe']['project_id']
        child_id, = await self.edit(pid,root_id)
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder)
            for name in ('images','uploads','thumbs'):
                shutil.copytree(self.store.directory/name,destination/name)
            connection = sqlite3.connect(destination/'studio.sqlite3')
            self.store.db.backup(connection)
            with connection:
                for table in ('versions','project_sources','projects'):
                    connection.execute('DROP TABLE '+table)
                for table in ('jobs','images'):
                    for image_id,raw in connection.execute('SELECT id,recipe FROM '+table).fetchall():
                        recipe = json.loads(raw)
                        recipe.pop('project_id',None);recipe.pop('parent_version_id',None)
                        connection.execute('UPDATE '+table+' SET recipe=? WHERE id=?',(json.dumps(recipe),image_id))
            recipes = dict(connection.execute('SELECT id,recipe FROM images'))
            connection.close()
            hashes = {path.name:hashlib.sha256(path.read_bytes()).hexdigest() for path in (destination/'images').glob('*.png')}
            migrated = Store(destination)
            self.assertEqual(migrated.projects.library()['total'],1)
            project = migrated.projects.detail(root_id)
            self.assertEqual([v['id'] for v in project['versions']],[root_id,child_id])
            self.assertEqual(project['versions'][1]['parent_id'],root_id)
            self.assertEqual(dict((r['id'],r['recipe']) for r in migrated.rows('SELECT id,recipe FROM images')),recipes)
            self.assertEqual({path.name:hashlib.sha256(path.read_bytes()).hexdigest() for path in (destination/'images').glob('*.png')},hashes)
            migrated.close()
