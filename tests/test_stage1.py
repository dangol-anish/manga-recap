from tests.support import create_symlink
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from contextlib import nullcontext

from fastapi.testclient import TestClient
from PIL import Image
from app import main, storage, worker
from app.detector import retain_panels
from app.validation import panel_errors


class StageOneTests(unittest.TestCase):
    def setUp(self):
        # All test data stays inside this repo and is deleted after each case.
        self.temp = tempfile.TemporaryDirectory(dir=storage.ROOT)
        self.root = Path(self.temp.name)
        self.manga_folder = self.root/'manga'/'Example'
        self.chapter = self.manga_folder/'Chapter001'
        self.chapter.mkdir(parents=True)
        for name in ('001.png', '002.png', '010.png'):
            Image.new('RGB', (100, 200), 'white').save(self.chapter/name)
        self.patches = [patch.object(module, name, value) for module, name, value in [
            (storage,'ROOT',self.root),(storage,'DATA',self.root/'data'),(storage,'OUTPUTS',self.root/'outputs'),
            (main,'ROOT',self.root),(main,'DATA',self.root/'data'),(main,'OUTPUTS',self.root/'outputs'),
            (worker,'ROOT',self.root),
        ]]
        self.patches += [patch('app.worker.require_authorized'), patch('app.worker.keep_awake', nullcontext)]
        for p in self.patches:p.start()
        self.client = TestClient(main.app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None,None,None)
        for p in reversed(self.patches):p.stop()
        self.temp.cleanup()

    def add(self):
        response=self.client.post('/api/manga',json={'folder':'manga/Example','direction':'rtl'})
        self.assertEqual(response.status_code,201,response.text)
        return response.json()

    def test_library_add_order_and_no_automatic_processing(self):
        manga=self.add()
        self.assertIsNone(main.process)
        result=self.client.get('/api/library').json()['manga'][0]
        self.assertEqual(result['direction'],'rtl')
        chapter=result['chapters'][0]
        self.assertEqual([p['page'] for p in chapter['pages']],['001.png','002.png','010.png'])
        self.assertEqual(chapter['done'],0)
        self.assertEqual(self.client.post('/api/manga',json={'folder':'manga/Example'}).status_code,409)
        self.assertEqual(self.client.post('/api/manga',json={'folder':'manga/Example','direction':'invalid'}).status_code,400)

    def test_outside_paths_and_symlinks_rejected(self):
        with self.assertRaises(ValueError):storage.local_path('../other-project')
        create_symlink(self.manga_folder/'linked.png', self.chapter/'001.png')
        self.assertEqual(storage.pages(self.manga_folder),[])
        self.assertEqual(self.client.post('/api/manga',json={'folder':'../other-project'}).status_code,400)
        self.assertEqual(self.client.post('/api/manga',json={'folder':'manga/Example'},headers={'Origin':'https://other.example'}).status_code,403)

    def test_resume_skips_saved_pages_and_preserves_them(self):
        manga=self.add()
        first=storage.page_output(manga,'Chapter001','001.png')
        storage.write_json(first,{'width':100,'height':200,'detection_mode':'normal-panel-label-filter','panels':[{'order':1,'box':[0,0,20,20]}],'seconds':1.1})
        original=first.read_bytes()
        job=storage.job_path(manga,'Chapter001')
        storage.write_json(job,{'manga':manga,'chapter':'Chapter001','status':'running'})
        job.with_name('_start').touch()
        class FakeDetector:
            device='test'; load_seconds=0
            def detect(self,image,direction):return [[0,0,100,200]]
        with patch('app.detector.Detector',FakeDetector):
            self.assertEqual(worker.run(job),0)
        self.assertEqual(first.read_bytes(),original)
        self.assertTrue(first.parent.joinpath('previews/001.png').exists())
        finished=storage.read_json(job)
        self.assertEqual((finished['processed'],finished['skipped'],finished['panels_found']),(2,1,3))
        self.assertEqual(self.client.get('/api/library').json()['manga'][0]['chapters'][0]['done'],3)
        with patch('app.detector.Detector',side_effect=AssertionError('must not load model')):
            self.assertEqual(worker.run(job),0)

    def test_failure_never_writes_partial_page_and_can_resume(self):
        manga=self.add();job=storage.job_path(manga,'Chapter001')
        storage.write_json(job,{'manga':manga,'chapter':'Chapter001','status':'running'})
        job.with_name('_start').touch()
        class FailingDetector:
            device='test';load_seconds=0
            def detect(self,image,direction):raise RuntimeError('Inference interrupted')
        with patch('app.detector.Detector',FailingDetector):self.assertEqual(worker.run(job),1)
        self.assertEqual(storage.read_json(job)['status'],'error')
        self.assertFalse(storage.page_output(manga,'Chapter001','001.png').exists())

    def test_atomic_save_and_stop(self):
        path=self.root/'data'/'atomic.json'
        storage.write_json(path,{'value':1})
        with self.assertRaises(ValueError):storage.write_json(path,{'value':float('nan')})
        self.assertEqual(storage.read_json(path),{'value':1})
        self.assertEqual(list(path.parent.glob('*.tmp')),[])
        manga=self.add();job=storage.job_path(manga,'Chapter001')
        storage.write_json(job,{'status':'running'})
        from unittest.mock import Mock
        child=Mock();child.poll.return_value=None
        main.process=child;main.active_job=job
        self.assertEqual(self.client.post('/api/stop').status_code,200)
        child.terminate.assert_called_once()
        self.assertEqual(storage.read_json(job)['status'],'paused')

    def test_processing_blocked_until_model_validation_passes(self):
        manga=self.add()
        response=self.client.post(f'/api/manga/{manga["id"]}/chapters/Chapter001/start')
        self.assertEqual(response.status_code,409)
        self.assertIsNone(main.process)
        self.assertFalse(self.client.get('/api/library').json()['detector_ready'])

    def test_chapter_allowlist_blocks_unapproved_chapter_and_manga(self):
        manga=self.add()
        storage.write_json(storage.DATA/'model-validation.json',{
            'panel_only_validated':True,'allowed_manga_id':manga['id'],
            'allowed_chapters':['Chapter 1','Chapter 12']})
        from app.authorization import processing_allowed
        self.assertTrue(processing_allowed(manga['id'],'Chapter001'))
        self.assertFalse(processing_allowed(manga['id'],'Chapter 12'))
        self.assertFalse(processing_allowed(manga['id'],'Chapter 2'))
        self.assertFalse(processing_allowed('another-manga','Chapter 1'))
        response=self.client.post(f'/api/manga/{manga["id"]}/chapters/Missing/start')
        self.assertEqual(response.status_code,403)
        self.assertIsNone(main.process)

    def test_only_exact_panel_label_survives(self):
        text='<panel><text><character><tail><panels>'
        positions=[0,7,13,24,30]
        groups=[[[0,0,80,90]], [[1,1,2,2]], [[3,3,4,4]], [[5,5,6,6]], [[7,7,8,8]]]
        self.assertEqual(retain_panels(text,groups,[(p,p) for p in positions]),[[0.,0.,80.,90.]])

    def test_panel_sanity_checks(self):
        self.assertTrue(panel_errors([],100,100))
        self.assertTrue(panel_errors([[-1,0,100,100]],100,100))
        self.assertTrue(panel_errors([[0,0,101,100]],100,100))
        self.assertTrue(panel_errors([[0,0,9,10]],100,100))
        self.assertTrue(panel_errors([[0,0,float('nan'),100]],100,100))
        self.assertTrue(panel_errors([[10,10,5,5]],100,100))
        self.assertEqual(panel_errors([[0,0,10,10]],100,100),[])
        self.assertEqual(panel_errors([[0,0,100,100]],100,100),[])

    def test_invalid_json_does_not_count_as_complete(self):
        manga=self.add()
        path=storage.page_output(manga,'Chapter001','001.png')
        storage.write_json(path,{'width':100,'height':200,'detection_mode':'normal-panel-label-filter','panels':[{'box':[0,0,1,1]}],'seconds':0.1})
        chapter=self.client.get('/api/library').json()['manga'][0]['chapters'][0]
        self.assertEqual(chapter['done'],0)
        self.assertFalse(chapter['pages'][0]['done'])

    def test_worker_flags_invalid_detections_without_page_json(self):
        manga=self.add();job=storage.job_path(manga,'Chapter001')
        storage.write_json(job,{'manga':manga,'chapter':'Chapter001','status':'running'})
        job.with_name('_start').touch()
        class InvalidDetector:
            device='test';load_seconds=0
            def detect(self,image,direction):return [[0,0,1,1]]
        with patch('app.detector.Detector',InvalidDetector):worker.run(job)
        self.assertEqual(len(storage.read_json(job)['flagged']),3)
        self.assertEqual(list(job.parent.glob('*.png.json')),[])

    def test_official_sorting_both_directions(self):
        source=Path(__file__).resolve().parents[1]/'vendor/magiv3/utils.py'
        spec=importlib.util.spec_from_file_location('magi_test_utils',source)
        utils=importlib.util.module_from_spec(spec);spec.loader.exec_module(utils)
        boxes=[[10,10,40,40],[60,10,90,40],[10,60,90,90]]
        self.assertEqual(utils.sort_panels(boxes),[1,0,2])
        mirrored=[[100-x2,y1,100-x1,y2] for x1,y1,x2,y2 in boxes]
        self.assertEqual(utils.sort_panels(mirrored),[0,1,2])

if __name__=='__main__':unittest.main()
