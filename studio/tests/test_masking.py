import io
import json
import tempfile
import unittest
from pathlib import Path

import aiohttp
import numpy as np
from PIL import Image, ImageChops, ImageDraw, PngImagePlugin

from studio.masking import engine_mask, engine_reference, masked_size, feather_mask, align_patch, match_colors, match_local_colors, automatic_feather, blend_selected, normalize_mask, protect_unselected
from studio.engine import QUALITIES
from studio.media import save_jpeg
from studio.server import STATE
from studio.storage import Store
from studio.tests import test_projects as project_fixtures
from studio.tests import test_studio as engine_fixtures


def selection(width, height, box=None):
    image = Image.new('L',(width,height),0)
    if box:
        ImageDraw.Draw(image).rectangle(box,fill=255)
    stream = io.BytesIO();image.save(stream,'PNG')
    return stream.getvalue()


class MaskAPITests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = project_fixtures.ProjectTests.asyncSetUp
    asyncTearDown = project_fixtures.ProjectTests.asyncTearDown
    upload = project_fixtures.ProjectTests.upload
    complete = project_fixtures.ProjectTests.complete

    async def mask(self, source, content=None):
        form = aiohttp.FormData()
        form.add_field('mask',content or selection(source['width'],source['height'],(100,60,120,80)),filename='mask.png',content_type='image/png')
        return await self.client.post('/api/uploads/'+source['id']+'/mask',data=form)

    async def test_selected_area_keeps_original_size_and_branches_with_saved_mask(self):
        root = await self.upload()
        mask = await (await self.mask(root)).json()
        self.assertEqual(mask['bounds'],[100,60,121,81])
        saved = await (await self.client.get('/api/masks/'+mask['id'])).json()
        self.assertEqual(saved['sha256'],mask['sha256'])
        self.assertEqual((await self.client.get(saved['url'])).status,200)
        response = await self.client.post('/api/generate',json={'mode':'edit','source_id':root['id'],'prompt':'Add a flower',
            'mask_id':mask['id'],'mask_feather':8,'output_size':'4k','resize_mode':'direct','quality':'fast','variations':2})
        self.assertEqual(response.status,202)
        ids = (await response.json())['jobs']
        for image_id in ids:
            recipe = self.store.job(image_id)['recipe']
            self.assertEqual((recipe['width'],recipe['height']),(240,160))
            self.assertEqual(recipe['masked_edit']['crop'],[4,0,213,160])
            self.assertEqual(recipe['masked_edit']['version'],2)
            self.assertEqual(recipe['masked_edit']['blend_mode'],'adaptive')
            self.assertGreater(recipe['masked_edit']['blend_width'],0)
            self.assertEqual(recipe['processing']['method'],'masked_patch_v2')
            self.assertEqual(recipe['masked_edit']['mask_id'],mask['id'])
            self.assertEqual(recipe['parent_version_id'],root['id'])
            self.assertLess(recipe['generation_width']*recipe['generation_height'],240*160)
        detail = await (await self.client.get('/api/projects/'+root['project_id'])).json()
        self.assertEqual([v['parent_id'] for v in detail['versions'][1:]],[root['id'],root['id']])
        estimate = await self.client.post('/api/estimate',json={'mode':'edit','project_id':root['project_id'],'version_id':root['id'],
            'quality':'fast','mask_bounds':mask['bounds'],'mask_feather':8})
        self.assertEqual(estimate.status,200)
        await self.client.post('/api/logout',json={})
        self.assertEqual((await self.client.get(saved['url'])).status,401)
        self.assertEqual((await self.client.get('/api/masks/'+mask['id'])).status,401)
        self.assertEqual((await self.mask(root)).status,401)

    async def test_empty_wrong_size_and_cross_photo_masks_are_rejected_without_jobs(self):
        root,other = await self.upload(),await self.upload()
        for content in (selection(240,160),selection(100,100,(10,10,30,30)),b'not an image'):
            self.assertEqual((await self.mask(root,content)).status,400)
        self.assertEqual(self.store.rows('SELECT * FROM masks'),[])
        self.assertEqual(list((self.store.directory/'masks').glob('*.png')),[])
        mask = await (await self.mask(root)).json()
        for source_id,mode,feather in ((other['id'],'edit',8),(root['id'],'create',8),(root['id'],'edit',-1),(root['id'],'edit',33),(root['id'],'edit',False)):
            response = await self.client.post('/api/generate',json={'mode':mode,'source_id':source_id,'prompt':'Edit','mask_id':mask['id'],'mask_feather':feather})
            self.assertEqual(response.status,400)
        for settings in ({'mask_detail':'unknown'},{'mask_context':'unknown'},{'mask_align':1},{'mask_color_match':'yes'},{'mask_blend_mode':'unknown'}):
            response=await self.client.post('/api/generate',json={'mode':'edit','source_id':root['id'],'prompt':'Edit','mask_id':mask['id'],**settings})
            self.assertEqual(response.status,400)
        self.assertEqual(self.store.rows('SELECT * FROM jobs'),[])
        for bounds in ([],[-1,0,10,10],[0,0,500,160],[0,0,0,10],[0.1,0,10,10]):
            response = await self.client.post('/api/estimate',json={'mode':'edit','source_id':root['id'],'mask_bounds':bounds})
            self.assertEqual(response.status,400)

    async def test_deleting_a_version_keeps_mask_until_whole_project_is_deleted(self):
        root = await self.upload()
        mask = await (await self.mask(root)).json()
        response = await self.client.post('/api/generate',json={'mode':'edit','source_id':root['id'],'prompt':'Edit','mask_id':mask['id']})
        image_id = (await response.json())['jobs'][0]
        self.complete(image_id)
        pid = root['project_id']
        self.assertEqual((await self.client.delete(f'/api/projects/{pid}/versions/{image_id}',json={'confirm':True})).status,200)
        self.assertEqual((await self.client.get(mask['url'])).status,200)
        self.assertEqual((await self.client.delete('/api/projects/'+pid,json={'confirm':True})).status,200)
        self.assertEqual((await self.client.get(mask['url'])).status,404)
        self.assertEqual(self.store.rows('SELECT * FROM masks'),[])
        self.assertEqual(list((self.store.directory/'masks').glob('*')),[])


class MaskPixelTests(unittest.TestCase):
    @staticmethod
    def uneven_color_fixture():
        width,height=512,384;yy,xx=np.mgrid[:height,:width]
        pixels=np.stack((105+25*np.sin(xx/31),118+20*np.cos(yy/27),130+10*np.sin((xx+yy)/25)),axis=2)
        original=Image.fromarray(np.clip(pixels,0,255).astype(np.uint8))
        ideal=original.copy();ImageDraw.Draw(ideal).ellipse((205,150,300,245),fill=(220,40,30))
        cast=np.stack((25*(xx/width)**2-14*(yy/height),-12*(xx/width)+22*(yy/height)**2,17*np.cos(xx/width*np.pi)*np.sin(yy/height*np.pi/2)),axis=2)
        patch=Image.fromarray(np.clip(np.asarray(ideal,dtype=float)+cast,0,255).astype(np.uint8))
        return original,ideal,patch

    def test_local_color_match_reduces_corner_casts_for_different_hand_painted_outlines(self):
        original,ideal,patch=self.uneven_color_fixture()
        for dx,dy in ((0,0),(14,-12),(-12,14)):
            mask=Image.new('L',original.size,0)
            ImageDraw.Draw(mask).rounded_rectangle((125+dx,75+dy,380+dx,315+dy),radius=18,fill=255)
            global_patch,_=match_colors(original,patch,mask)
            corrected,diagnostics=match_local_colors(original,global_patch,mask)
            self.assertTrue(diagnostics['applied'])
            # Independently known desired colors at all four inside corners.
            regions=[(126+dx,76+dy,155+dx,105+dy),(350+dx,76+dy,379+dx,105+dy),
                     (126+dx,285+dy,155+dx,314+dy),(350+dx,285+dy,379+dx,314+dy)]
            def error(image,region):
                return np.abs(np.asarray(image.crop(region),dtype=float)-np.asarray(ideal.crop(region),dtype=float)).mean()
            before=sum(error(global_patch,region) for region in regions)
            after=sum(error(corrected,region) for region in regions)
            self.assertLess(after,before*.4)
            self.assertEqual(corrected.getpixel((255,192)),global_patch.getpixel((255,192)))

    def test_local_color_match_skips_unchanged_or_unreliable_context(self):
        original,ideal,patch=self.uneven_color_fixture()
        mask=Image.new('L',original.size,0);ImageDraw.Draw(mask).rectangle((125,75,380,315),fill=255)
        for reference,candidate,selection in ((original,ideal,mask),(original,patch,Image.new('L',original.size,255)),
                (Image.new('RGB',original.size,'black'),Image.new('RGB',original.size,'white'),mask)):
            corrected,diagnostics=match_local_colors(reference,candidate,selection)
            self.assertFalse(diagnostics['applied'])
            self.assertEqual(corrected.tobytes(),candidate.tobytes())

    def test_automatic_blend_preserves_unpainted_pixels_recolor_and_saved_manual_behavior(self):
        original,ideal,patch=self.uneven_color_fixture()
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'masks').mkdir();(root/'uploads').mkdir()
            original.save(root/'uploads/s.png')
            mask=Image.new('L',original.size,0);ImageDraw.Draw(mask).rounded_rectangle((125,75,380,315),radius=18,fill=255);mask.save(root/'masks/m.png')
            base={'source_id':'s','width':512,'height':384,'generation_width':512,'generation_height':384,
                  'masked_edit':{'version':2,'mask_id':'m','crop':[0,0,512,384],'canvas_content':[0,0,512,384],
                                 'feather':8,'align':False,'color_match':True}}
            for mode in ('adaptive','manual',None):
                recipe=json.loads(json.dumps(base))
                if mode:recipe['masked_edit'].update(blend_mode=mode,blend_width=16)
                metadata=PngImagePlugin.PngInfo();metadata.add_text('studio',json.dumps(recipe))
                path=root/'result.png';patch.save(path,pnginfo=metadata)
                diagnostics=blend_selected(path,root,recipe)
                with Image.open(path) as result:
                    unpainted=np.asarray(mask)==0
                    self.assertTrue(np.array_equal(np.asarray(result)[unpainted],np.asarray(original)[unpainted]))
                    self.assertEqual(json.loads(result.info['studio']),recipe)
                    self.assertGreater(result.getpixel((255,192))[0],160)
                    self.assertLess(result.getpixel((255,192))[1],100)
                    if mode=='adaptive':
                        self.assertTrue(diagnostics['local_color_match']['applied'])
                        self.assertEqual(diagnostics['blending']['width'],16)
                    else:
                        self.assertNotIn('local_color_match',diagnostics)
                        self.assertEqual(diagnostics['blending']['width'],8)

    def test_automatic_transition_scales_for_large_photos_and_small_canvases(self):
        small=automatic_feather([30,30,130,130],[0,0,160,160],[0,0,160,160])
        large=automatic_feather([300,300,2300,2300],[0,0,2700,2700],[0,0,512,512])
        self.assertEqual(small,8)
        self.assertGreater(large,small)
        self.assertLessEqual(large,64)
        self.assertEqual(automatic_feather([0,0,8192,8192],[0,0,8192,8192],[0,0,512,512]),64)
        narrow=automatic_feather([20,20,32,2020],[0,0,64,2048],[0,0,64,512])
        mask=Image.new('L',(64,2048),0);ImageDraw.Draw(mask).rectangle((20,20,31,2019),fill=255)
        self.assertEqual(feather_mask(mask,narrow).getpixel((26,1000)),255)

    def test_matched_reference_canvas_uses_padding_instead_of_aspect_distortion_or_center_crop(self):
        for dimensions in ((751,497),(1152,1728),(4096,2711)):
            source={'width':dimensions[0],'height':dimensions[1]}
            bounds=[dimensions[0]//4,dimensions[1]//4,dimensions[0]*3//4,dimensions[1]*3//4]
            sizes=[masked_size(source,bounds,8,'high','portrait',QUALITIES,{},detail=detail) for detail in ('quick','balanced','maximum')]
            self.assertLessEqual(max(sizes[0]['generation_width'],sizes[0]['generation_height']),512)
            self.assertLessEqual(max(sizes[1]['generation_width'],sizes[1]['generation_height']),768)
            self.assertLessEqual(sizes[0]['generation_width']*sizes[0]['generation_height'],sizes[2]['generation_width']*sizes[2]['generation_height'])
            with tempfile.TemporaryDirectory() as folder:
                root=Path(folder);(root/'uploads').mkdir()
                image=Image.new('RGB',dimensions,'navy');ImageDraw.Draw(image).line((0,0,*dimensions),fill='orange',width=12);image.save(root/'uploads/s.png')
                sizing=sizes[1];selection=dict(sizing['processing']);recipe=dict(sizing,source_id='s',masked_edit=selection)
                with Image.open(io.BytesIO(engine_reference(root,recipe))) as reference:
                    self.assertEqual(reference.size,(recipe['generation_width'],recipe['generation_height']))
                    self.assertTrue(all(value%16==0 for value in reference.size))
                    x,y,width,height=selection['crop'];left,top,cw,ch=selection['canvas_content']
                    expected=image.crop((x,y,x+width,y+height)).resize((cw,ch),Image.Resampling.LANCZOS)
                    self.assertEqual(reference.crop((left,top,left+cw,top+ch)).tobytes(),expected.tobytes())

    def test_inward_feather_is_soft_at_boundary_and_never_changes_unpainted_pixels(self):
        mask=Image.new('L',(100,80),0);ImageDraw.Draw(mask).rectangle((25,20,75,60),fill=255)
        softened=feather_mask(mask,8)
        self.assertEqual(softened.getpixel((24,40)),0)
        self.assertLess(softened.getpixel((25,40)),16)
        self.assertGreater(softened.getpixel((28,40)),softened.getpixel((25,40)))
        self.assertEqual(softened.getpixel((40,40)),255)
        self.assertEqual(feather_mask(Image.new('L',(40,40),255),8).getextrema(),(255,255))

    def test_maximum_canvas_never_triggers_the_model_reference_area_cap(self):
        for width in range(3900,4097,7):
            sizing=masked_size({'width':width,'height':4031},[0,0,width,4031],8,'high','landscape',QUALITIES,{},detail='maximum')
            self.assertLessEqual(sizing['generation_width']*sizing['generation_height'],1024*1024)

    def test_color_matching_uses_context_and_preserves_requested_recolor(self):
        yy,xx=np.mgrid[:192,:256];pixels=np.stack((60+xx*.4,70+yy*.5,90+(xx+yy)*.2),axis=-1).astype(np.uint8)
        original=Image.fromarray(pixels);patch=original.copy();ImageDraw.Draw(patch).rectangle((90,60,150,120),fill=(220,40,30))
        shifted=patch.point([min(255,value+offset) for offset in (12,16,8) for value in range(256)])
        mask=Image.new('L',original.size,0);ImageDraw.Draw(mask).rectangle((80,50,160,130),fill=255)
        matched,diagnostics=match_colors(original,shifted,mask)
        self.assertTrue(diagnostics['applied'])
        self.assertLess(np.mean(np.abs(np.asarray(matched,dtype=float)-np.asarray(patch,dtype=float))),2)
        self.assertLess(np.max(np.abs(np.array(matched.getpixel((110,90)))-[220,40,30])),3)
        unchanged,diagnostics=match_colors(original,patch,mask)
        self.assertFalse(diagnostics['applied'])
        self.assertEqual(unchanged.tobytes(),patch.tobytes())
        _,diagnostics=match_colors(original,shifted,Image.new('L',original.size,255))
        self.assertFalse(diagnostics['applied'])

    def test_alignment_corrects_small_context_drift_without_guessing_on_flat_photos(self):
        original=Image.new('RGB',(256,192),(110,120,130));draw=ImageDraw.Draw(original)
        for x in range(20,256,31):draw.line((x,0,x,192),fill=(220,190,160),width=4)
        for y in range(10,192,29):draw.line((0,y,256,y),fill=(45,70,95),width=3)
        mask=Image.new('L',original.size,0);ImageDraw.Draw(mask).rectangle((90,55,155,135),fill=255)
        shifted=ImageChops.offset(original,4,-3);ImageDraw.Draw(shifted).rectangle((105,70,140,120),fill='red')
        aligned,diagnostics=align_patch(original,shifted,mask)
        self.assertTrue(diagnostics['applied'])
        context=np.asarray(mask)==0;context[:8]=context[-8:]=False;context[:,:8]=context[:,-8:]=False
        before=np.mean(np.abs(np.asarray(original,dtype=float)[context]-np.asarray(shifted,dtype=float)[context]))
        after=np.mean(np.abs(np.asarray(original,dtype=float)[context]-np.asarray(aligned,dtype=float)[context]))
        self.assertLess(after,before*.5)
        _,diagnostics=align_patch(Image.new('RGB',original.size,'navy'),shifted,mask)
        self.assertFalse(diagnostics['applied'])

    def test_patch_blend_preserves_exact_original_pixels_and_records_options_and_diagnostics(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'masks').mkdir();(root/'uploads').mkdir()
            original=Image.new('RGB',(240,160),'coral');original.save(root/'uploads/s.png')
            result=normalize_mask(selection(240,160,(100,60,120,80)),{'width':240,'height':160},root/'masks/m.png')
            size=masked_size({'width':240,'height':160},result['bounds'],8,'fast','square',QUALITIES,{})
            recipe=dict(size,source_id='s',masked_edit=dict(size['processing'],version=2,mask_id='m',sha256=result['sha256'],feather=8,color_match=False,align=False))
            metadata=PngImagePlugin.PngInfo();metadata.add_text('studio',json.dumps(recipe));metadata.add_text('workflow','{}')
            path=root/'patch.png';Image.new('RGB',(recipe['generation_width'],recipe['generation_height']),'navy').save(path,pnginfo=metadata)
            blend_selected(path,root,recipe)
            with Image.open(path) as final,Image.open(root/'masks/m.png') as mask:
                self.assertEqual(final.size,original.size)
                unpainted=np.asarray(mask)==0
                self.assertTrue(np.array_equal(np.asarray(final)[unpainted],np.asarray(original)[unpainted]))
                self.assertEqual(final.getpixel((110,70)),(0,0,128))
                self.assertEqual(json.loads(final.info['studio']),recipe)
                self.assertIn('studio_mask_diagnostics',final.info)

    def test_downsampled_mask_and_exact_unselected_pixels_and_png_metadata(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder);(root/'masks').mkdir();(root/'uploads').mkdir()
            result = normalize_mask(selection(2048,1024,(500,200,600,300)),{'width':4096,'height':2048},root/'masks/m.png')
            self.assertEqual(result['bounds'],[1000,400,1202,602])
            source = Image.new('RGB',(4096,2048),'coral');source.save(root/'uploads/s.png')
            recipe = {'source_id':'s','masked_edit':{'mask_id':'m','sha256':result['sha256'],'crop':[936,336,330,330],'feather':8}}
            data = engine_mask(root,recipe)
            with Image.open(io.BytesIO(data)) as alpha:
                self.assertEqual(alpha.size,(330,330))
                self.assertEqual(alpha.getpixel((0,0))[3],255)
                self.assertEqual(alpha.getpixel((120,120))[3],0)
                self.assertGreater(alpha.getpixel((64,64))[3],0)
            metadata = PngImagePlugin.PngInfo()
            for key in ('studio','prompt','workflow'):metadata.add_text(key,json.dumps({'test':key}))
            path=root/'final.png';Image.new('RGB',source.size,'navy').save(path,pnginfo=metadata)
            protect_unselected(path,root,recipe)
            with Image.open(path) as final,Image.open(root/'masks/m.png') as mask:
                # Expected image is independent of compositor implementation.
                expected=source.copy();expected.paste(Image.new('RGB',(202,202),'navy'),(1000,400))
                self.assertEqual(final.tobytes(),expected.tobytes())
                self.assertEqual(json.loads(final.info['workflow']),{'test':'workflow'})
                self.assertEqual(mask.getpixel((999,400)),0)

    def test_mask_deletion_recovery_restores_only_existing_records(self):
        with tempfile.TemporaryDirectory() as folder:
            store=Store(folder);path=store.directory/'masks/m.png'
            path.write_bytes(selection(64,64,(10,10,20,20)))
            store.execute('INSERT INTO masks VALUES (?,?,?,?,?,?,?,?)',('m','s',0,64,64,'[10,10,21,21]','test',.1))
            path.rename(path.with_name(path.name+'.deleting'));store.close()
            store=Store(folder);self.assertTrue(path.is_file())
            path.rename(path.with_name(path.name+'.deleting'));store.execute('DELETE FROM masks');store.close()
            store=Store(folder);self.assertFalse(path.exists());self.assertEqual(list(path.parent.glob('*')),[]);store.close()


class MaskEngineTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = engine_fixtures.EngineTests.asyncSetUp
    asyncTearDown = engine_fixtures.EngineTests.asyncTearDown
    wait_images = engine_fixtures.EngineTests.wait_images

    async def test_worker_uses_crop_and_saved_mask_preserves_pixels_and_lineage(self):
        stream=io.BytesIO();Image.new('RGB',(751,497),'coral').save(stream,'PNG')
        form=aiohttp.FormData();form.add_field('image',stream.getvalue(),filename='original.png',content_type='image/png')
        source=await (await self.client.post('/api/uploads',data=form)).json()
        form=aiohttp.FormData();form.add_field('mask',selection(751,497,(300,200,340,240)),filename='mask.png',content_type='image/png')
        mask=await (await self.client.post('/api/uploads/'+source['id']+'/mask',data=form)).json()
        response=await self.client.post('/api/generate',json={'mode':'edit','prompt':'Add flowers','quality':'fast','source_id':source['id'],
            'mask_id':mask['id'],'mask_feather':0,'mask_blend_mode':'manual','mask_color_match':False,'mask_align':False,'variations':1})
        self.assertEqual(response.status,202)
        image_id=(await response.json())['jobs'][0]
        image,=await self.wait_images(1)
        graph=self.submitted[0]['prompt'];recipe=image['recipe']
        self.assertEqual(graph['2']['inputs']['image'],['14',0])
        self.assertNotIn('10',graph)
        self.assertTrue(graph['14']['inputs']['image'].endswith('-reference.png'))
        self.assertEqual(graph['12']['inputs']['image'],['15',0])
        self.assertEqual(graph['13']['inputs']['mask'],['11',1])
        self.assertEqual(graph['9']['inputs']['images'],['13',0])
        self.assertEqual(len(self.upload_names),3)
        self.assertTrue(self.upload_names[1].endswith('-mask.png'))
        self.assertEqual(recipe['parent_version_id'],source['id'])
        content=await (await self.client.get('/media/image/'+image_id)).read()
        with Image.open(io.BytesIO(content)) as saved:
            expected=Image.new('RGB',(751,497),'coral');expected.paste(Image.new('RGB',(41,41),'navy'),(300,200))
            # JPEG encoding is deterministic: the saved photo is exactly the encoded pixel-exact blend.
            encoded=io.BytesIO();save_jpeg(expected,encoded)
            self.assertEqual(saved.size,expected.size)
            self.assertEqual(saved.tobytes(),Image.open(encoded).tobytes())
            self.assertEqual(json.loads(saved.info['comment']),recipe)
            workflow=self.submitted[0]['extra_data']['extra_pnginfo']['workflow'];nodes={n['id']:n for n in workflow['nodes']}
            for link in workflow['links']:
                identity,origin,slot,target,target_slot,kind=link
                self.assertIn(identity,nodes[origin]['outputs'][slot]['links'])
                self.assertEqual(nodes[target]['inputs'][target_slot]['link'],identity)
            self.assertEqual(nodes[13]['type'],'ImageCompositeMasked')
