from tests.support import create_symlink
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from app import storage,stitch
from app.main import app

@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'),'FFmpeg required')
class StitchTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.out=self.root/'outputs';self.data=self.root/'data'
        self.patches=[patch.object(storage,'ROOT',self.root),patch.object(storage,'OUTPUTS',self.out),patch.object(storage,'DATA',self.data),patch('app.detection.catalog',return_value={'manga':[]})]
        for p in self.patches:p.start()
        self.folder=self.out/'Videos/Manga';self.folder.mkdir(parents=True)
        storage.write_json(self.data/'video-folders.json',{'manga':{'manga':{'folder':'Manga','chapters':{}}}})
        for i,color in enumerate(('red','blue'),1):
            subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i',f'color=c={color}:s=160x90:r=24','-f','lavfi','-i','sine=frequency=440:sample_rate=24000','-t','0.6','-c:v','libx264','-preset','ultrafast','-threads','1','-pix_fmt','yuv420p','-c:a','aac','-ac','1',str(self.folder/f'Chapter{i}-Video.mp4')],check=True,capture_output=True)
        stitch.active=None;stitch.worker=None;stitch.cancel.clear();self.client=TestClient(app)
    def tearDown(self):
        stitch.shutdown();stitch.active=None;stitch.worker=None;stitch.cancel.clear();stitch.probe.cache_clear()
        for p in reversed(self.patches):p.stop()
        self.temp.cleanup()
    def finish(self):
        stitch.worker.join(15);self.assertFalse(stitch.worker.is_alive());return stitch.status()['job']
    def test_real_join_order_duration_native_playback_and_original_preservation(self):
        original={p:p.read_bytes() for p in self.folder.glob('*.mp4')}
        groups=self.client.get('/api/stitch/catalog').json();videos=groups[0]['videos']
        self.assertEqual([v['filename'] for v in videos],['Chapter1-Video.mp4','Chapter2-Video.mp4'])
        self.assertIsNone(stitch.status()['job'])
        r=self.client.post('/api/stitch/start',json={'manga_id':'manga','video_ids':[v['id'] for v in reversed(videos)]})
        self.assertEqual(r.status_code,200,r.text);job=self.finish();self.assertEqual(job['status'],'complete',job)
        self.assertEqual([v['filename'] for v in job['items']],[v['filename'] for v in videos])
        self.assertAlmostEqual(job['duration'],sum(v['duration'] for v in videos),delta=.5)
        path=self.root/job['output'];self.assertEqual(path.parent,self.folder)
        probe=stitch.probe(str(path),job['output_sha256'])
        # Remuxed MP4s can report a different nominal frame-rate estimate at
        # chapter boundaries. Packet payloads must remain identical and ordered.
        self.assertEqual(probe['signature'][:6]+probe['signature'][7:],videos[0]['signature'][:6]+videos[0]['signature'][7:])
        import json
        def packets(file):
            r=subprocess.run(['ffprobe','-v','error','-show_packets','-show_data_hash','sha256','-show_entries','packet=stream_index,data_hash','-of','json',str(file)],capture_output=True,text=True,check=True, encoding='utf-8', errors='replace')
            return json.loads(r.stdout)['packets']
        # Concat may inject H.264 headers; decoded pictures must be identical.
        def frames(file):
            r=subprocess.run(['ffmpeg','-v','error','-i',str(file),'-map','0:v:0','-f','framemd5','-'],capture_output=True,text=True,check=True, encoding='utf-8', errors='replace')
            return [line.split(',')[-1].strip() for line in r.stdout.splitlines() if not line.startswith('#')]
        self.assertEqual(frames(path),[frame for item in videos for frame in frames(self.root/item['relative'])])
        expected=[p['data_hash'] for item in videos for p in packets(self.root/item['relative']) if p['stream_index']==1]
        actual=[p['data_hash'] for p in packets(path) if p['stream_index']==1]
        self.assertEqual(actual,expected)
        response=self.client.get('/api/stitch/jobs/'+job['id']+'/video',headers={'Range':'bytes=0-99'})
        self.assertEqual(response.status_code,206);self.assertEqual(len(response.content),100)
        self.assertTrue(all(p.read_bytes()==content for p,content in original.items()))
        # Repeat output does not overwrite a user-edited prior combined video.
        path.write_bytes(b'user change');stitch.start('manga',[v['id'] for v in videos]);second=self.finish()
        self.assertEqual(second['status'],'complete');self.assertNotEqual(second['output'],job['output']);self.assertEqual(path.read_bytes(),b'user change')
    def test_changed_or_unknown_selection_is_rejected(self):
        videos=stitch.catalog()[0]['videos'];(self.folder/videos[0]['filename']).write_bytes(b'changed')
        with self.assertRaises(FileExistsError):stitch.start('manga',[v['id'] for v in videos])
        self.assertIsNone(stitch.active)
        self.assertEqual(self.client.post('/api/stitch/start',json={'manga_id':'manga','video_ids':[]}).status_code,422)
    def test_conflicting_task_and_recovery(self):
        videos=stitch.catalog()[0]['videos']
        job={'id':'a'*32,'status':'running','created_at':0,'items':videos}
        stitch.save(job);stitch.recover();self.assertEqual(stitch.load(job['id'])['status'],'paused');self.assertIsNone(stitch.active)
        with patch('app.speech_stage6.active',{'id':'other'}):
            with self.assertRaises(FileExistsError):stitch.start('manga',[v['id'] for v in videos])
    def test_mismatched_settings_rejected_before_work(self):
        videos=stitch.catalog()[0]['videos'];videos[0]['signature']=[]
        with patch.object(stitch,'catalog',return_value=[{'id':'manga','folder':'outputs/Videos/Manga','videos':videos}]):
            with self.assertRaises(ValueError):stitch.start('manga',[v['id'] for v in videos])
    def test_path_escape_and_symlink_rejected(self):
        with self.assertRaises(ValueError):stitch.source_path('../escape.mp4')
        target=self.folder/'Shortcut-Video.mp4';create_symlink(target, self.folder/'Chapter1-Video.mp4')
        with self.assertRaises(ValueError):stitch.source_path(str(target.relative_to(self.root)))

    def test_cancel_before_combining_keeps_chapter_files_and_no_partial_result(self):
        videos=stitch.catalog()[0]['videos']
        original={p:p.read_bytes() for p in self.folder.glob('*.mp4')}
        job={'id':'b'*32,'status':'running','created_at':0,'items':videos,'manga_folder':'Manga'}
        stitch.cancel.set();stitch.run(job)
        self.assertEqual(stitch.load(job['id'])['status'],'paused')
        self.assertEqual(list(self.folder.glob('Combined*.mp4')),[])
        self.assertEqual(list(self.folder.glob('.stitch-*')),[])
        self.assertTrue(all(p.read_bytes()==data for p,data in original.items()))
