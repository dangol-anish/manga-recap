from contextlib import nullcontext
from pathlib import Path
import tempfile
import time
import unittest
import uuid
from unittest.mock import patch, Mock
from PIL import Image,ImageDraw
from fastapi.testclient import TestClient
from app import storage,detection, detection_worker as worker, panel_preparation as p
from tests.test_detection_stage3 import FakeDetector
from app.main import app


class PreparationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'outputs');self.root=Path(self.temp.name)
        self.patches=[patch.object(storage,'ROOT',self.root),patch.object(storage,'DATA',self.root/'data'),patch.object(storage,'OUTPUTS',self.root/'outputs'),patch.object(worker,'keep_awake',return_value=nullcontext())]
        for value in self.patches:value.start()
        self.source=self.root/'manga/Sample/Chapter 1';self.source.mkdir(parents=True)
        for name in ('0000.png','0001.png'):
            image=Image.new('RGB',(100,150),'white');draw=ImageDraw.Draw(image);draw.rectangle((0,0,44,149),fill='red');draw.rectangle((55,0,99,149),fill='blue');image.save(self.source/name)
        self.ident=detection.catalog()['manga'][0]['chapters'][0]['id'];p.active=None;p.stop_event.clear();detection.process=detection.active_id=None
        self.detect()
        self.client=TestClient(app)

    def tearDown(self):
        p.active=None;p.stop_event.clear()
        for value in reversed(self.patches):value.stop()
        self.temp.cleanup()

    def detect(self,factory=FakeDetector):
        job={'id':uuid.uuid4().hex,'status':'running','direction':'rtl','elapsed_seconds':0,'chapters':detection.selection([self.ident],'rtl')}
        detection.save(job);self.assertEqual(worker.run(job,detector_factory=factory),0)

    def prepare(self):
        chapter,files,report=p.context(self.ident);ids=p.page_ids(files)
        records=[p.prepare_one(chapter,page,ids[page.name],report)[0] for page in files]
        p.chapter_data(self.ident,persist=True);return chapter,records

    def test_crops_ids_padding_and_labeled_assets(self):
        chapter,records=self.prepare();record=records[0]
        self.assertEqual([x['id'] for x in record['panels']],['p0000-1','p0000-2'])
        self.assertEqual(record['panels'][0]['crop_box'],[54,0,100,150])
        directory=p.record_path(chapter,'p0000').parent/record['revision']
        with Image.open(directory/'crops/p0000-1.png') as image:self.assertEqual(image.size,(46,150));self.assertEqual(image.getpixel((30,70)),(0,0,255))
        self.assertTrue((directory/'labeled.png').is_file());self.assertTrue((directory/'thumbnail.jpg').is_file())
        self.assertEqual(p.chapter_data(self.ident)['report']['panels'],4)

    def test_reads_saved_detections_without_model_or_source_changes(self):
        before={path.name:path.read_bytes() for path in self.source.iterdir()}
        model=Mock(side_effect=AssertionError('No inference in Stage 4'))
        with patch('app.detector.Detector',model):self.prepare()
        model.assert_not_called();self.assertEqual({path.name:path.read_bytes() for path in self.source.iterdir()},before)

    def test_prepare_resume_preserves_edits_and_does_not_regenerate_other_page(self):
        chapter,records=self.prepare();old_second=records[1]['revision'];record=records[0]
        response=self.client.post(f'/api/preparation/chapters/{self.ident}/pages/p0000',json={'revision':record['revision'],'boxes':[record['panels'][1]['box'],record['panels'][0]['box']]})
        self.assertEqual(response.status_code,200);edited=response.json();self.assertTrue(edited['edited'])
        chapter,again=self.prepare();self.assertEqual(again[0]['revision'],edited['revision']);self.assertEqual(again[1]['revision'],old_second)
        self.assertEqual(again[0]['panels'][0]['box'],record['panels'][1]['box'])

    def test_saved_reorder_updates_crop_contents(self):
        chapter,records=self.prepare();record=records[0]
        response=self.client.post(f'/api/preparation/chapters/{self.ident}/pages/p0000',json={'revision':record['revision'],'boxes':[record['panels'][1]['box'],record['panels'][0]['box']]})
        self.assertEqual(response.status_code,200)
        crop=self.client.get(f'/api/preparation/chapters/{self.ident}/pages/p0000/image?kind=crop&panel_id=p0000-1')
        from io import BytesIO
        with Image.open(BytesIO(crop.content)) as image:self.assertEqual(image.getpixel((20,70)),(255,0,0))

    def test_add_resize_delete_and_stale_revision(self):
        _,records=self.prepare();path=f'/api/preparation/chapters/{self.ident}/pages/p0000';revision=records[0]['revision']
        body={'revision':revision,'boxes':[[0,0,100,60],[0,60,100,150],[10,10,40,40]]}
        response=self.client.post(path,json=body);self.assertEqual(response.status_code,200);self.assertEqual(len(response.json()['panels']),3)
        self.assertEqual(self.client.post(path,json=body).status_code,409)
        response=self.client.post(path,json={'revision':response.json()['revision'],'boxes':[[0,0,100,150]]})
        self.assertEqual(len(response.json()['panels']),1)

    def test_delete_all_creates_flagged_whole_page_panel(self):
        _,records=self.prepare();response=self.client.post(f'/api/preparation/chapters/{self.ident}/pages/p0000',json={'revision':records[0]['revision'],'boxes':[]})
        self.assertEqual(response.status_code,200);record=response.json()
        self.assertTrue(record['zero_panels_fallback']);self.assertTrue(record['flagged']);self.assertEqual(record['panels'][0]['box'],[0,0,100,150])

    def test_flagged_stage3_uses_reviewable_whole_page_fallback(self):
        class Empty(FakeDetector):
            def detect(self,image,direction):return []
        # Remove only this fixture's prior detection outputs so the fake zero-panel
        # result is exercised instead of reusing the valid fixture cache.
        import shutil
        chapter,_,_=p.context(self.ident);shutil.rmtree(detection.output(chapter))
        self.detect(Empty);_,records=self.prepare()
        self.assertTrue(all(r['zero_panels_fallback'] and r['flagged'] for r in records))
        self.assertTrue(any('Stage 3:' in reason for reason in records[0]['flag_reasons']))

    def test_invalid_geometry_never_commits(self):
        chapter,records=self.prepare();before=records[0]['revision']
        response=self.client.post(f'/api/preparation/chapters/{self.ident}/pages/p0000',json={'revision':before,'boxes':[[-1,0,100,150]]})
        self.assertEqual(response.status_code,400);self.assertEqual(p.safe_record(p.record_path(chapter,'p0000'))['revision'],before)

    def test_flagged_pages_are_first_and_overlap_rule_preserved(self):
        _,records=self.prepare()
        result=self.client.post(f'/api/preparation/chapters/{self.ident}/pages/p0001',json={'revision':records[1]['revision'],'boxes':[[0,0,100,80],[0,79,100,150]]})
        self.assertEqual(result.status_code,200);self.assertFalse(any('overlap:' in r for r in result.json()['flag_reasons']))
        result=self.client.post(f'/api/preparation/chapters/{self.ident}/pages/p0001',json={'revision':result.json()['revision'],'boxes':[[0,0,100,100],[0,80,100,150]]})
        self.assertTrue(any('overlap:' in r for r in result.json()['flag_reasons']))
        self.assertEqual(p.chapter_data(self.ident)['pages'][0]['page'],'0001.png')

    def test_source_changes_preserve_manual_assets_and_block_stale_save(self):
        chapter,records=self.prepare()
        result=self.client.post(f'/api/preparation/chapters/{self.ident}/pages/p0000',json={'revision':records[0]['revision'],'boxes':[[0,0,100,150]]})
        saved=result.json();Image.new('RGB',(100,150),'black').save(self.source/'0000.png')
        with self.assertRaises(ValueError):p.prepare_one(chapter,self.source/'0000.png','p0000',p.context(self.ident)[2])
        self.assertEqual(p.safe_record(p.record_path(chapter,'p0000'))['revision'],saved['revision'])
        self.assertEqual(self.client.post(f'/api/preparation/chapters/{self.ident}/pages/p0000',json={'revision':saved['revision'],'boxes':[[0,0,100,150]]}).status_code,400)

    def test_missing_assets_are_rebuilt_from_manual_boxes(self):
        chapter,records=self.prepare();record=records[0]
        response=self.client.post(f'/api/preparation/chapters/{self.ident}/pages/p0000',json={'revision':record['revision'],'boxes':[[0,0,100,150]]});record=response.json()
        (p.record_path(chapter,'p0000').parent/record['revision']/'labeled.png').unlink()
        _,records=self.prepare();self.assertTrue(records[0]['edited']);self.assertEqual(len(records[0]['panels']),1)
        self.assertTrue(p.assets_ready(chapter,records[0]))

    def test_failed_generation_keeps_old_manifest_and_other_assets(self):
        chapter,records=self.prepare();record=records[0]
        with patch.object(p.review,'draw_ids',side_effect=OSError('disk failure')):
            response=self.client.post(f'/api/preparation/chapters/{self.ident}/pages/p0000',json={'revision':record['revision'],'boxes':[[0,0,100,150]]})
        self.assertEqual(response.status_code,400);self.assertEqual(p.safe_record(p.record_path(chapter,'p0000'))['revision'],record['revision']);self.assertTrue(p.assets_ready(chapter,record))

    def test_catalog_does_not_prepare_automatically_and_requires_stage3(self):
        self.assertEqual(self.client.get('/api/preparation/catalog').status_code,200)
        self.assertFalse((storage.DATA/'preparation/jobs').exists())
        chapter,_,_=p.context(self.ident);self.assertFalse(p.root(chapter).exists())
        (detection.output(chapter)/'report.json').unlink()
        self.assertEqual(self.client.post('/api/preparation/jobs',json={'chapter_ids':[self.ident]}).status_code,400)

    def test_background_stop_resume_and_persistent_recovery(self):
        entry={'id':self.ident,'name':'Chapter 1','manga':'Sample','total_pages':2,'status':'pending','results':[]}
        job={'id':uuid.uuid4().hex,'status':'running','chapters':[entry],'elapsed_seconds':0}
        p.save_job(job);p.active={'id':job['id'],'started':time.monotonic(),'job':job}
        original=p.prepare_one;calls=0
        def stop_after_page(*args):
            nonlocal calls
            value=original(*args);calls+=1
            if calls==1:p.stop_event.set()
            return value
        with patch.object(p,'prepare_one',side_effect=stop_after_page):p.run(job)
        self.assertEqual(job['status'],'paused');self.assertEqual(p.summary(job)['prepared_pages'],1)
        p.stop_event.clear();entry['results']=[];p.active={'id':job['id'],'started':time.monotonic(),'job':job};p.run(job)
        self.assertEqual(job['status'],'complete');self.assertEqual(p.summary(job)['prepared_pages'],2)
        self.assertTrue(entry['results'][0]['cached'])
        job['status']='running';p.save_job(job);p.recover();self.assertEqual(p.load_job(job['id'])['status'],'paused')

    def test_api_images_native_folder_and_revision_checks(self):
        chapter,records=self.prepare();record=records[0];base=f'/api/preparation/chapters/{self.ident}/pages/p0000'
        self.assertEqual(self.client.get(base).status_code,200)
        for kind in ('source','thumbnail','labeled'):self.assertEqual(self.client.get(base+'/image?kind='+kind+'&revision='+record['revision']).status_code,200)
        self.assertEqual(self.client.get(base+'/image?revision=stale').status_code,404)
        self.assertEqual(self.client.get(base+'/image?kind=crop&panel_id=../../secret').status_code,404)
        with patch.object(p,'open_system_folder') as open_folder:
            self.assertEqual(self.client.post(f'/api/preparation/chapters/{self.ident}/open-folder').status_code,200);open_folder.assert_called_once_with(p.root(chapter))
        self.assertIn('contact-sheet',self.client.get('/panels').text)

    def test_active_preparation_blocks_other_tasks_and_edits(self):
        from app.tasks import ensure_idle
        _,records=self.prepare();p.active={'id':'a'*32,'started':time.monotonic()}
        with self.assertRaises(FileExistsError):ensure_idle()
        response=self.client.post(f'/api/preparation/chapters/{self.ident}/pages/p0000',json={'revision':records[0]['revision'],'boxes':[]})
        self.assertEqual(response.status_code,409)

    def test_real_background_preparation_thread_finishes_without_inference(self):
        with patch('app.detector.Detector',side_effect=AssertionError('Stage 4 must not load Magi')):
            response=self.client.post('/api/preparation/jobs',json={'chapter_ids':[self.ident]})
            self.assertEqual(response.status_code,200)
            p.thread.join(timeout=5)
        self.assertFalse(p.thread.is_alive());self.assertIsNone(p.active)
        job=self.client.get('/api/preparation/jobs').json()[0]
        self.assertEqual(job['status'],'complete');self.assertEqual(job['prepared_pages'],2)

    def test_editor_source_matches_detection_pixels_even_with_exif_rotation(self):
        image=Image.new('RGB',(100,150),'white');exif=image.getexif();exif[274]=6
        image.save(self.source/'0000.png',format='JPEG',exif=exif)
        self.detect();self.prepare()
        response=self.client.get(f'/api/preparation/chapters/{self.ident}/pages/p0000/image?kind=source')
        from io import BytesIO
        with Image.open(BytesIO(response.content)) as opened:
            self.assertEqual(opened.size,(100,150));self.assertIsNone(opened.getexif().get(274))
        with Image.open(self.source/'0000.png') as original:self.assertEqual(original.getexif().get(274),6)

    def test_fallback_page_ids_are_unique_and_zero_based(self):
        files=[self.source/'page_2.jpg',self.source/'page_10.png']
        self.assertEqual(list(p.page_ids(files).values()),['p0000','p0001'])


if __name__=='__main__':unittest.main()
