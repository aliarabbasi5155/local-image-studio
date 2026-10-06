import asyncio
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import aiohttp
import numpy as np
from PIL import Image, ImageDraw

from studio.automask import AutomaticSelectionError, selection_image, validate_selection
from studio.masking import blend_selected, masked_size, engine_placement_guide
from studio.engine import QUALITIES, build_graph
from studio.server import STATE
from studio.tests.test_masking import selection
from studio.tests import test_projects as fixtures


class AutomaticSelectionTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.ProjectTests.asyncSetUp
    asyncTearDown = fixtures.ProjectTests.asyncTearDown
    upload = fixtures.ProjectTests.upload
    complete = fixtures.ProjectTests.complete

    async def test_generation_waits_for_selection_and_cancelled_waiting_job_never_starts(self):
        source=await self.upload()
        response=await self.client.post('/api/generate',json={'mode':'edit','source_id':source['id'],'prompt':'Change shirt','quality':'fast'})
        job_id=(await response.json())['jobs'][0]
        engine=self.app[STATE]['engine'];started=[]
        async def run(job):
            started.append(job['id']);self.store.update_job(job['id'],status='complete',finished=1)
        engine.run_job=run
        await engine.resource_lock.acquire()
        worker=asyncio.create_task(engine.worker())
        try:
            for _ in range(40):
                if self.store.job(job_id)['phase']=='Waiting for automatic selection':break
                await asyncio.sleep(.01)
            self.assertEqual(started,[])
            self.assertEqual((await self.client.post(f'/api/jobs/{job_id}/cancel',json={})).status,200)
            engine.resource_lock.release()
            for _ in range(40):
                if self.store.job(job_id)['status']=='cancelled':break
                await asyncio.sleep(.01)
            self.assertEqual(started,[])
            self.assertEqual(self.store.job(job_id)['status'],'cancelled')
        finally:
            if engine.resource_lock.locked():engine.resource_lock.release()
            worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):await worker

    async def test_selects_only_saved_source_and_does_not_create_an_edit(self):
        source = await self.upload()
        seen = []
        async def select(path, target, protect):
            self.assertTrue(self.app[STATE]['engine'].resource_lock.locked())
            with Image.open(path) as image:
                self.assertEqual(image.size, (240,160))
            seen.append((target,protect))
            return {'candidates':[], 'faces':[], 'seconds':9, 'cached':False}
        self.app[STATE]['automask'].select = select
        path = f"/api/uploads/{source['id']}/auto-mask"
        response = await self.client.post(path,json={'target':'  hand  ','protect_faces':True})
        self.assertEqual(response.status,200)
        self.assertEqual((await response.json())['seconds'],9)
        self.assertEqual(seen,[('hand',True)])
        self.assertFalse(self.app[STATE]['engine'].resource_lock.locked())
        self.assertEqual(self.store.rows('SELECT * FROM masks'),[])
        self.assertEqual(self.store.rows('SELECT * FROM jobs'),[])
        self.assertEqual((await self.client.post('/api/uploads/missing/auto-mask',json={'target':'shirt'})).status,404)
        self.assertEqual((await self.client.post(path,json={'target':'shirt'},headers={'Origin':'http://wrong.example'})).status,403)
        await self.client.post('/api/logout',json={})
        self.assertEqual((await self.client.post(path,json={'target':'shirt'})).status,401)

    async def test_input_errors_busy_engine_and_unavailable_selector_release_lock(self):
        source = await self.upload()
        path = f"/api/uploads/{source['id']}/auto-mask"
        for payload in (None,[],{'target':7},{'target':'x'*81},{'target':'shirt\n'},{'target':'','protect_faces':False},{'target':'hand','protect_faces':'yes'}):
            self.assertEqual((await self.client.post(path,json=payload)).status,400)
        engine = self.app[STATE]['engine']
        async with engine.resource_lock:
            self.assertEqual((await self.client.post(path,json={'target':'shirt'})).status,409)
        async def unavailable(*args):
            raise ValueError('Automatic selection is unavailable. Use the brush.')
        self.app[STATE]['automask'].select = unavailable
        response = await self.client.post(path,json={'target':''})
        self.assertEqual(response.status,400)
        self.assertIn('brush',(await response.json())['error'])
        self.assertFalse(engine.resource_lock.locked())
        async def busy(*args):
            raise AutomaticSelectionError('Your Mac is making an image.',409)
        self.app[STATE]['automask'].select=busy
        self.assertEqual((await self.client.post(path,json={'target':'shirt'})).status,409)
        self.store.add_jobs([{'mode':'create','prompt':'Test','seed':0,'project_id':'unused'}])
        job = self.store.one('SELECT id FROM jobs')
        self.store.update_job(job['id'],status='running')
        self.assertEqual((await self.client.post(path,json={'target':'shirt'})).status,409)

    async def mask(self, source, box):
        form=aiohttp.FormData()
        form.add_field('mask',selection(240,160,box),filename='mask.png',content_type='image/png')
        return await (await self.client.post(f"/api/uploads/{source['id']}/mask",data=form)).json()

    async def test_protected_faces_cannot_overlap_and_selection_recipe_is_saved(self):
        source,other = await self.upload(),await self.upload()
        selected = await self.mask(source,(30,80,180,140))
        face = await self.mask(source,(65,20,105,70))
        wrong = await self.mask(other,(65,20,105,70))
        overlapping = await self.mask(source,(65,80,105,110))
        metadata={'model':'SAM 3 Q8','model_revision':'pinned','target':'shirt','mode':'change','extra_room':12,'protect_faces':True}
        payload={'mode':'edit','source_id':source['id'],'prompt':'Change shirt to yellow','quality':'fast',
                 'mask_id':selected['id'],'mask_protection_id':face['id'],'mask_selection':metadata}
        for protection in (wrong['id'],overlapping['id'],'missing'):
            self.assertEqual((await self.client.post('/api/generate',json=dict(payload,mask_protection_id=protection))).status,400)
        for bad in ([],dict(metadata,mode='unknown'),dict(metadata,extra_room=100),dict(metadata,protect_faces=1),dict(metadata,model={})):
            self.assertEqual((await self.client.post('/api/generate',json=dict(payload,mask_selection=bad))).status,400)
        self.assertEqual(self.store.rows('SELECT * FROM jobs'),[])
        response=await self.client.post('/api/generate',json=payload)
        self.assertEqual(response.status,202)
        job=self.store.job((await response.json())['jobs'][0])
        recipe=job['recipe']['masked_edit']
        self.assertEqual(recipe['selection'],metadata)
        self.assertEqual(recipe['protection_mask_id'],face['id'])
        self.assertEqual(recipe['protection_sha256'],face['sha256'])
        # A placement-only edit can explicitly disable face protection.
        add=dict(metadata,mode='add',target='',protect_faces=False)
        response=await self.client.post('/api/generate',json=dict(payload,mask_selection=add,mask_protection_id=None))
        self.assertEqual(response.status,202)
        for box in ([0,0,300,100],[0,0,0,20],[0.5,0,10,20],[],[-1,0,20,20]):
            self.assertEqual((await self.client.post('/api/generate',json=dict(payload,mask_selection=dict(add,placement=box)))).status,400)
        add['placement']=[30,80,180,140]
        response=await self.client.post('/api/generate',json=dict(payload,mask_selection=add,mask_protection_id=None))
        self.assertEqual(response.status,202)
        guided=self.store.job((await response.json())['jobs'][0])['recipe']
        self.assertEqual(guided['masked_edit']['selection']['placement'],add['placement'])
        self.assertEqual(guided['masked_edit']['placement_guidance'],'visual_reference_v1')
        graph,workflow=build_graph(guided,self.app[STATE]['engine'].project_root,self.app[STATE]['engine'].templates,
                                   'source.png','mask.png','reference.png','web-studio/guide.png')
        self.assertIn('guide.png',graph['2']['inputs']['image_paths'])
        self.assertIn('red dot',graph['2']['inputs']['prompt'])
        self.assertTrue(graph['2']['inputs']['prompt'].startswith(payload['prompt']))
        self.assertEqual(graph['2']['inputs']['image'],['14',0])
        self.assertEqual(guided['prompt'],payload['prompt'])
        self.complete(guided['image_id'],seconds=42)
        estimate_payload={'mode':'edit','source_id':source['id'],'quality':'fast','mask_bounds':selected['bounds'],'mask_placement_guidance':True}
        estimate=await (await self.client.post('/api/estimate',json=estimate_payload)).json()
        self.assertEqual(estimate['samples'],1)
        self.assertEqual(estimate['seconds'],42)
        ordinary=await (await self.client.post('/api/estimate',json=dict(estimate_payload,mask_placement_guidance=False))).json()
        self.assertEqual(ordinary['samples'],0)


class SelectionPixelsTests(unittest.TestCase):
    def test_preview_input_is_bounded_without_changing_the_saved_original(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'original.png'
            Image.new('RGB',(4096,3072),'navy').save(path)
            before=path.read_bytes()
            with Image.open(io.BytesIO(selection_image(path))) as preview:
                self.assertEqual(preview.size,(1024,768))
            self.assertEqual(path.read_bytes(),before)

    def test_face_hole_stays_pixel_exact_even_when_model_changes_the_entire_patch(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            (root/'uploads').mkdir();(root/'masks').mkdir()
            yy,xx=np.mgrid[:384,:512]
            original=Image.fromarray(np.stack((xx%256,yy%256,(xx+yy)%256),axis=2).astype(np.uint8))
            mask=Image.new('L',original.size,0);draw=ImageDraw.Draw(mask)
            draw.rectangle((80,40,410,335),fill=255);draw.ellipse((190,55,285,150),fill=0)
            original.save(root/'uploads/s.png');mask.save(root/'masks/m.png')
            source={'width':512,'height':384}
            plan=masked_size(source,[80,40,411,336],8,'fast','square',QUALITIES,{})
            Image.new('RGB',(plan['generation_width'],plan['generation_height']),'magenta').save(root/'patch.png')
            recipe=dict(plan,prompt='Change clothing',source_id='s',masked_edit={'mask_id':'m','crop':plan['processing']['crop'],
                'canvas_content':plan['processing']['canvas_content'],'feather':8,'blend_mode':'adaptive',
                'bounds':[80,40,411,336],'color_match':False,'align':False})
            blend_selected(root/'patch.png',root,recipe)
            with Image.open(root/'patch.png') as output:
                pixels=np.asarray(output)
            outside=np.asarray(mask)==0
            self.assertTrue(np.array_equal(pixels[outside],np.asarray(original)[outside]))
            self.assertGreater(np.count_nonzero(np.any(pixels!=np.asarray(original),axis=2)),20000)

    def test_short_names_and_face_only_detection_are_valid(self):
        self.assertEqual(validate_selection({'target':'hand','protect_faces':False}),('hand',False))
        self.assertEqual(validate_selection({'target':''}),('',True))

    def test_placement_guide_uses_the_same_canvas_and_correct_destination(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'uploads').mkdir();(root/'masks').mkdir()
            Image.new('RGB',(320,240),(80,90,100)).save(root/'uploads/s.png')
            mask=Image.new('L',(320,240),0);ImageDraw.Draw(mask).rectangle((100,90,180,150),fill=255);mask.save(root/'masks/m.png')
            recipe={'source_id':'s','generation_width':320,'generation_height':240,
                'masked_edit':{'crop':[0,0,320,240],'canvas_content':[0,0,320,240],'mask_id':'m','selection':{'placement':[100,90,180,150]}}}
            with Image.open(io.BytesIO(engine_placement_guide(root,recipe))) as guide:
                self.assertEqual(guide.size,(320,240))
                self.assertEqual(guide.getpixel((0,0)),(80,90,100))
                self.assertEqual(guide.getpixel((100,90)),(80,90,100))
                self.assertEqual(guide.getpixel((140,120)),(255,20,40))
