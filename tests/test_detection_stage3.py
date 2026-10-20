from copy import deepcopy
from contextlib import nullcontext
from pathlib import Path
import tempfile
import time
import unittest
import uuid
from unittest.mock import patch, Mock

from PIL import Image
from fastapi.testclient import TestClient
from app import storage, detection as d, detection_worker as w, renumbering, downloader
from app.main import app


class FakeDetector:
    calls=[]
    device='fixture'
    load_seconds=0
    def detect(self,image,direction):
        self.calls.append(direction)
        boxes=[[0,0,45,image.height],[55,0,image.width,image.height]]
        return boxes[::-1] if direction=='rtl' else boxes


class DetectionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'outputs')
        self.root=Path(self.temp.name)
        self.patches=[patch.object(storage,'ROOT',self.root),patch.object(storage,'DATA',self.root/'data'),patch.object(storage,'OUTPUTS',self.root/'outputs'),patch.object(w,'keep_awake',return_value=nullcontext())]
        for p in self.patches:p.start()
        self.folder=self.root/'manga'/'Sample'/'Chapter 1';self.folder.mkdir(parents=True)
        for name in ('0000.png','0001.png'):Image.new('RGB',(100,100),'white').save(self.folder/name)
        source=self.root/'vendor/magiv3';source.mkdir(parents=True)
        for name in ('model.safetensors','config.json','utils.py'):(source/name).write_text('fixture only, never loaded')
        storage.write_json(storage.DATA/'model-validation.json',{'panel_only_validated':True})
        FakeDetector.calls=[];d.process=d.active_id=None;downloader.active=None

    def tearDown(self):
        d.process=d.active_id=None;downloader.active=None
        for p in reversed(self.patches):p.stop()
        self.temp.cleanup()

    def job(self,direction='rtl',ids=None):
        ids=ids or [d.catalog()['manga'][0]['chapters'][0]['id']]
        job={'id':uuid.uuid4().hex,'status':'running','direction':direction,'chapters':d.selection(ids,direction),'elapsed_seconds':0,'model':w.MODEL_ID,'revision':w.REVISION}
        d.save(job);return job

    def run_job(self,job,factory=FakeDetector):return w.run(job,detector_factory=factory)

    def reset(self,job,direction=None):
        job['status']='running'
        if direction:job['direction']=direction
        job['chapters']=d.selection([c['id'] for c in job['chapters']],job['direction'])
        d.stop_path(job['id']).unlink(missing_ok=True)
        return job

    def test_catalog_is_read_only_and_remembers_direction(self):
        client=TestClient(app)
        with patch.object(d.subprocess,'Popen') as start:
            response=client.get('/api/detection/catalog')
        self.assertTrue(response.json()['detector']['ready']);start.assert_not_called()
        self.assertEqual(list((storage.DATA/'detection/jobs').glob('*.json')),[])
        storage.write_json(d.base()/'settings.json',{'manga/Sample':'ltr'})
        self.assertEqual(d.catalog()['manga'][0]['direction'],'ltr')

    def test_valid_json_previews_report_and_reading_order(self):
        job=self.job();self.assertEqual(self.run_job(job),0)
        c=job['chapters'][0];folder=d.output(c)
        record=storage.read_json(folder/'pages/0000.png.json')
        self.assertEqual(record['reading_direction'],'rtl');self.assertEqual(record['panels'][0]['box'],[55,0,100,100])
        self.assertEqual(set(record['panels'][0]),{'order','box'})
        self.assertEqual(record['model_revision'],w.REVISION)
        self.assertTrue((folder/'previews/0000.png.jpg').is_file())
        self.assertEqual(storage.read_json(folder/'report.json')['valid_pages'],2)
        self.assertEqual(d.summary(job)['completed_pages'],2)
        self.assertEqual(d.summary(job)['panels_found'],4)

    def test_resume_skips_all_matching_pages_without_loading_model(self):
        job=self.job();self.run_job(job);self.reset(job)
        factory=Mock(side_effect=AssertionError('Model must not load for cached pages'))
        self.assertEqual(self.run_job(job,factory),0);factory.assert_not_called()
        self.assertTrue(all(r['cached'] for r in job['chapters'][0]['results']))

    def test_replaced_same_dimension_image_invalidates_only_that_page(self):
        job=self.job();self.run_job(job);FakeDetector.calls=[]
        Image.new('RGB',(100,100),'black').save(self.folder/'0001.png')
        self.reset(job);self.run_job(job)
        self.assertEqual(len(FakeDetector.calls),1)
        self.assertEqual(len(list((d.output(job['chapters'][0])/'history').glob('*.json'))),1)

    def test_direction_and_model_revision_invalidate_cache(self):
        job=self.job();self.run_job(job);FakeDetector.calls=[]
        self.reset(job,'ltr');self.run_job(job);self.assertEqual(FakeDetector.calls,['ltr','ltr'])
        record_path=d.output(job['chapters'][0])/'pages/0000.png.json'
        saved=storage.read_json(record_path);saved['model_revision']='older';storage.write_json(record_path,saved)
        FakeDetector.calls=[];self.reset(job);self.run_job(job);self.assertEqual(len(FakeDetector.calls),1)

    def test_flagged_pages_have_no_valid_json_and_are_cached(self):
        class Invalid(FakeDetector):
            def detect(self,image,direction):return [[-2,0,20,20],[1,1,2,2]]
        job=self.job();self.run_job(job,Invalid);folder=d.output(job['chapters'][0])
        self.assertEqual(list((folder/'pages').glob('*.json')),[])
        self.assertEqual(d.summary(job)['flagged_pages'],2)
        self.assertEqual(d.summary(job)['panels_found'],0)
        self.assertGreater(len(storage.read_json(folder/'report.json')['flagged'][0]['reasons']),0)
        self.reset(job);factory=Mock(side_effect=AssertionError('Must reuse unchanged flagged result'))
        self.run_job(job,factory);factory.assert_not_called()

    def test_zero_panels_and_corrupt_images_are_flagged(self):
        class Empty(FakeDetector):
            def detect(self,image,direction):return []
        (self.folder/'0001.png').write_bytes(b'not an image')
        job=self.job();self.run_job(job,Empty)
        self.assertEqual(d.summary(job)['flagged_pages'],2)
        self.assertFalse(job['chapters'][0]['results'][1]['preview'])

    def test_missing_cached_preview_is_rebuilt_without_model(self):
        job=self.job();self.run_job(job);p=d.output(job['chapters'][0])/'previews/0000.png.jpg';p.unlink()
        self.reset(job);factory=Mock(side_effect=AssertionError('No model required'));self.run_job(job,factory)
        self.assertTrue(p.exists());factory.assert_not_called()

    def test_stop_after_page_and_resume_preserve_completed_work(self):
        job=self.job()
        class Stops(FakeDetector):
            def detect(self,image,direction):
                d.stop_path(job['id']).touch();return super().detect(image,direction)
        self.run_job(job,Stops);self.assertEqual(job['status'],'paused')
        self.assertEqual(d.summary(job)['completed_pages'],1)
        self.reset(job);FakeDetector.calls=[];self.run_job(job)
        self.assertEqual(job['status'],'complete');self.assertEqual(len(FakeDetector.calls),1)

    def test_model_failure_keeps_saved_results_and_resume_succeeds(self):
        job=self.job();factory=Mock(side_effect=RuntimeError('No model available'))
        self.assertEqual(self.run_job(job,factory),1);self.assertEqual(job['status'],'failed')
        self.reset(job);self.run_job(job);self.assertEqual(job['status'],'complete')

    def test_multiple_chapters_load_one_model_and_remain_isolated(self):
        second=self.folder.parent/'Chapter 2';second.mkdir();Image.new('RGB',(100,100),'white').save(second/'0000.png')
        ids=[c['id'] for c in d.catalog()['manga'][0]['chapters']]
        job=self.job(ids=ids);factory=Mock(side_effect=FakeDetector);self.run_job(job,factory)
        self.assertEqual(factory.call_count,1)
        self.assertEqual(d.summary(job)['completed_chapters'],2)
        self.assertNotEqual(d.output(job['chapters'][0]),d.output(job['chapters'][1]))

    def test_other_manga_cannot_be_mixed_and_pending_rename_blocked(self):
        other=self.root/'manga/Other/Chapter 1';other.mkdir(parents=True);Image.new('RGB',(100,100)).save(other/'0000.png')
        groups=d.catalog()['manga'];ids=[m['chapters'][0]['id'] for m in groups]
        with self.assertRaises(ValueError):d.selection(ids,'rtl')
        storage.write_json(self.folder/'.renumbering.json',{'id':'a'*32,'status':'interrupted'})
        ident=next(c['id'] for m in d.catalog()['manga'] for c in m['chapters'] if c['folder']=='manga/Sample/Chapter 1')
        with self.assertRaisesRegex(ValueError,'Finish interrupted'):d.selection([ident],'rtl')

    def test_new_worker_uses_current_interpreter_and_only_selected_chapter(self):
        client=TestClient(app);ident=d.catalog()['manga'][0]['chapters'][0]['id']
        fake=Mock();fake.pid=123;fake.poll.return_value=None
        with patch.object(d.subprocess,'Popen',return_value=fake) as start:
            response=client.post('/api/detection/jobs',json={'chapter_ids':[ident],'direction':'rtl'})
        self.assertEqual(response.status_code,200)
        args=start.call_args.args[0]
        self.assertEqual(args[0],d.sys.executable);self.assertEqual(args[1:3],['-m','app.detection_worker'])
        self.assertEqual(len(response.json()['chapters']),1)
        stopped=client.post('/api/detection/jobs/'+response.json()['id']+'/stop')
        self.assertEqual(stopped.json()['status'],'stopping')
        self.assertTrue(d.stop_path(response.json()['id']).exists())

    def test_global_busy_gate_and_restart_recovery(self):
        from app.tasks import ensure_idle
        fake=Mock();fake.poll.return_value=None;d.process=fake
        with self.assertRaises(FileExistsError):ensure_idle()
        d.process=None;job=self.job();d.recover()
        self.assertEqual(d.load(job['id'])['status'],'paused')
        self.assertTrue(d.stop_path(job['id']).exists())

    def test_api_previews_open_folder_validation_and_page(self):
        job=self.job();self.run_job(job);client=TestClient(app)
        base='/api/detection/jobs/'+job['id']+'/chapters/0'
        self.assertEqual(client.get(base+'/previews/0').status_code,200)
        self.assertEqual(client.get(base+'/previews/99').status_code,404)
        with patch.object(d,'open_system_folder') as open_folder:
            self.assertEqual(client.post(base+'/open-folder').status_code,200)
            open_folder.assert_called_once_with(d.output(job['chapters'][0]))
        self.assertEqual(client.post('/api/detection/jobs/invalid/resume').status_code,400)
        self.assertEqual(client.post('/api/detection/jobs',json={'chapter_ids':['unknown'],'direction':'rtl'}).status_code,400)
        self.assertEqual(client.get('/detection').status_code,200)
        self.assertIn('page-progress',client.get('/detection').text)

    def test_input_pages_and_legacy_outputs_are_not_changed(self):
        original={p.name:p.read_bytes() for p in self.folder.iterdir()}
        legacy=storage.OUTPUTS/'old/Chapter 1/stage5/video.mp4';legacy.parent.mkdir(parents=True);legacy.write_bytes(b'old video')
        self.run_job(self.job())
        self.assertEqual({p.name:p.read_bytes() for p in self.folder.iterdir()},original)
        self.assertEqual(legacy.read_bytes(),b'old video')

    def test_prior_job_keeps_correct_preview_after_direction_changes(self):
        first=self.job('rtl');self.run_job(first)
        client=TestClient(app)
        endpoint='/api/detection/jobs/'+first['id']+'/chapters/0/previews/0'
        before=client.get(endpoint).content
        second=self.job('ltr');self.run_job(second)
        response=client.get(endpoint)
        self.assertEqual(response.status_code,200);self.assertEqual(response.content,before)

    def test_readiness_rejects_missing_model_and_invalid_selection(self):
        (self.root/'vendor/magiv3/model.safetensors').unlink();self.assertFalse(d.ready()['ready'])
        ident=d.catalog()['manga'][0]['chapters'][0]['id']
        self.assertEqual(TestClient(app).post('/api/detection/jobs',json={'chapter_ids':[ident],'direction':'rtl'}).status_code,400)
        with self.assertRaises(ValueError):d.selection([ident],'unknown')

    def test_real_subprocess_resumes_cached_fixture_without_loading_model(self):
        import subprocess,sys
        job=self.job();self.run_job(job);self.reset(job);d.save(job)
        d.job_path(job['id']).with_suffix('.start').touch()
        program="""import sys
from pathlib import Path
from contextlib import nullcontext
from app import storage
storage.ROOT=Path(sys.argv[1])
storage.DATA=storage.ROOT/'data'
storage.OUTPUTS=storage.ROOT/'outputs'
from app import detection_worker as worker
worker.keep_awake=lambda:nullcontext()
raise SystemExit(worker.main(Path(sys.argv[2])))
"""
        result=subprocess.run([sys.executable,'-c',program,str(self.root),str(d.job_path(job['id']))],cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stderr)
        saved=d.load(job['id']);self.assertEqual(saved['status'],'complete')
        self.assertTrue(all(r['cached'] for r in saved['chapters'][0]['results']))

    def test_worker_lock_prevents_second_worker(self):
        handle=d.worker_lock()
        try:self.assertTrue(d.busy())
        finally:handle.close()
        self.assertFalse(d.busy())


if __name__=='__main__':unittest.main()
