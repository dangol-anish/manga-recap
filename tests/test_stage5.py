import copy
import json
import shutil
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from fastapi.testclient import TestClient
from app import storage, video, main


class VideoTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=storage.ROOT);self.root=Path(self.tmp.name)
        self.output=patch.object(storage,'OUTPUTS',self.root);self.output.start()
        self.wav=self.root/'test.wav'
        with wave.open(str(self.wav),'wb') as f:
            f.setparams((1,2,24000,0,'NONE','not compressed'));f.writeframes(b'\0\0'*48000)
        self.panels=[]
        for i,color in enumerate(['red','blue']):
            path=self.root/f'{i}.png';Image.new('RGB',(80,160),color).save(path)
            self.panels.append({'id':f'p001-{i+1}','revision':'revision','url':'/test','_crop':str(path)})
        self.beats=[{'number':1,'token':'a'*64,'narration':'Controlled audio test.','panel_ids':['p001-1','p001-2'],
                     'revision':'revision','silent':False,'duration':2.0,'_audio':str(self.wav),'panels':self.panels}]
        self.source=patch.object(video,'source',side_effect=lambda *args:(copy.deepcopy(self.beats),'a'*64));self.source.start()
        video.jobs.clear()

    def tearDown(self):
        self.source.stop();self.output.stop();self.tmp.cleanup();video.jobs.clear()

    def test_equal_timing_and_save(self):
        initial=video.timeline('test','Chapter 1');self.assertEqual(initial['beats'][0]['switches'],[1.0])
        changed=video.save('test','Chapter 1','a'*64,1,[.6])
        self.assertEqual(changed['beats'][0]['switches'],[.6]);self.assertNotEqual(initial['render_token'],changed['render_token'])
        self.assertNotIn('_audio',changed['beats'][0]);self.assertNotIn('_crop',changed['beats'][0]['panels'][0])
        self.assertEqual(video.timeline('test','Chapter 1')['beats'][0]['switches'],[.6])

    def test_invalid_times_and_stale_requests(self):
        for values in [[],[-1],[0],[2],[float('nan')],[True],[1,1]]:
            with self.assertRaises(ValueError):video.save('test','Chapter 1','a'*64,1,values)
        with self.assertRaises(FileExistsError):video.save('test','Chapter 1','b'*64,1,[.5])
        with self.assertRaises(FileExistsError):video.start('test','Chapter 1','b'*64)
        with self.assertRaises(FileNotFoundError):video.asset('test','Chapter 1',video.timeline('test','Chapter 1')['render_token'])

    def test_sources_reset_timing_and_stale_export(self):
        video.save('test','Chapter 1','a'*64,1,[.5])
        with patch.object(video,'source',side_effect=lambda *args:(copy.deepcopy(self.beats),'b'*64)):
            state=video.timeline('test','Chapter 1');self.assertTrue(state['timing_reset']);self.assertEqual(state['beats'][0]['switches'],[1])
            with self.assertRaises(FileExistsError):video.asset('test','Chapter 1','a'*64)

    def test_other_chapter_blocked(self):
        self.source.stop()
        try:
            c=TestClient(main.app)
            self.assertEqual(c.get('/video/test/Chapter%2012').status_code,403)
            self.assertEqual(c.get('/api/video/test/Chapter%2012').status_code,403)
            self.assertEqual(c.post('/api/video/test/Chapter%2012/timing',json={'source_token':'a'*64,'number':1,'switches':[]}).status_code,403)
            self.assertEqual(c.post('/api/video/test/Chapter%2012/export',json={'render_token':'a'*64}).status_code,403)
            self.assertEqual(c.get('/api/video/test/Chapter%2012/mp4?token='+'a'*64).status_code,403)
        finally:self.source.start()

    def test_real_mp4_switches_and_audio_stream(self):
        if not shutil.which('ffmpeg'):self.skipTest('FFmpeg not installed')
        video.save('test','Chapter 1','a'*64,1,[.6])
        state=video.timeline('test','Chapter 1');video.jobs['test']={'running':True,'duration':2,'message':''}
        video.run('test','Chapter 1',state)
        self.assertFalse(video.jobs['test'].get('error'),video.jobs['test'])
        path=video.asset('test','Chapter 1',state['render_token'])
        for t,channel in [(.2,0),(1.2,2)]:
            data=subprocess.run(['ffmpeg','-v','error','-ss',str(t),'-i',str(path),'-frames:v','1','-vf','scale=1:1','-f','rawvideo','-pix_fmt','rgb24','-'],capture_output=True,check=True).stdout
            self.assertGreater(data[channel],max(data[(channel+1)%3],data[(channel+2)%3])+30)
        response=TestClient(main.app).get('/api/video/test/Chapter%201/mp4?token='+state['render_token'],headers={'Range':'bytes=0-99'})
        self.assertEqual(response.status_code,206);self.assertEqual(len(response.content),100)
        video.save('test','Chapter 1','a'*64,1,[.8])
        with self.assertRaises(FileExistsError):video.asset('test','Chapter 1',state['render_token'])

    def test_changed_timing_during_render_cannot_publish(self):
        if not shutil.which('ffmpeg'):self.skipTest('FFmpeg not installed')
        state=video.timeline('test','Chapter 1');video.save('test','Chapter 1','a'*64,1,[.6])
        video.jobs['test']={'running':True,'duration':2,'message':''}
        video.run('test','Chapter 1',state)
        self.assertTrue(video.jobs['test']['error']);self.assertFalse(video.export_path('test',state).exists())
