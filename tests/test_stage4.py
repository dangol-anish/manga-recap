import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from fastapi.testclient import TestClient
from app import audio, storage, main


class AudioTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=storage.ROOT)
        self.root=Path(self.tmp.name)
        self.output_patch=patch.object(storage,'OUTPUTS',self.root)
        self.output_patch.start()
        self.current=[{'number':1,'panel_ids':['p001-1'],'narration':'First controlled test.', 'token':'a'*64},
                      {'number':2,'panel_ids':['p001-2'],'narration':'', 'token':'b'*64}]
        self.beat_patch=patch.object(audio,'beats',return_value=self.current)
        self.beat_patch.start();audio.jobs.clear()
        self.mp3=patch('mutagen.mp3.MP3',return_value=SimpleNamespace(info=SimpleNamespace(length=2.5)))
        self.mp3.start()

    def tearDown(self):
        self.mp3.stop();self.beat_patch.stop();self.output_patch.stop();self.tmp.cleanup();audio.jobs.clear()

    async def fake(self,text,path):path.write_bytes(b'controlled audio fixture')

    def test_generation_and_stale_text(self):
        with patch.object(audio,'synthesize',side_effect=self.fake):audio.generate_one('test','Chapter 1',self.current[0])
        state=audio.status('test','Chapter 1')
        self.assertEqual(state['ready'],1);self.assertEqual(state['spoken'],1)
        self.assertTrue(state['beats'][1]['silent'])
        self.current[0]=dict(self.current[0],token='c'*64,narration='Changed text.')
        self.assertEqual(audio.status('test','Chapter 1')['ready'],0)
        with self.assertRaises(FileNotFoundError):audio.asset('test','Chapter 1',1)

    def test_failed_regeneration_preserves_old_clip(self):
        with patch.object(audio,'synthesize',side_effect=self.fake):audio.generate_one('test','Chapter 1',self.current[0])
        old=audio.asset('test','Chapter 1',1)
        with patch.object(audio,'synthesize',side_effect=RuntimeError('Controlled offline failure')):
            with self.assertRaises(RuntimeError):audio.generate_one('test','Chapter 1',self.current[0])
        self.assertEqual(audio.asset('test','Chapter 1',1),old)
        self.assertFalse(list(audio.root('test').glob('.*.tmp')))

    def test_retry_skips_completed_and_regenerate_only_one(self):
        with patch.object(audio,'synthesize',side_effect=self.fake):audio.generate_one('test','Chapter 1',self.current[0])
        with self.assertRaises(ValueError):audio.start('test','Chapter 1')
        with patch('threading.Thread') as thread:
            audio.start('test','Chapter 1',1)
            args=thread.call_args.kwargs['args']
            self.assertEqual(len(args[2]),1);self.assertTrue(args[3]);thread.return_value.start.assert_called_once()
        with self.assertRaises(FileExistsError):audio.start('test','Chapter 1',1)

    def test_failure_job_and_invalid_beat(self):
        with self.assertRaises(FileNotFoundError):audio.start('test','Chapter 1',99)
        audio.jobs['test']={'running':True,'completed':0}
        with patch.object(audio,'generate_one',side_effect=RuntimeError('Controlled failure')):
            audio.run('test','Chapter 1',self.current[:1],False)
        self.assertFalse(audio.jobs['test']['running']);self.assertTrue(audio.jobs['test']['error'])

    def test_other_chapters_rejected(self):
        self.beat_patch.stop()
        client=TestClient(main.app)
        for suffix,method in [('',client.get),('/generate',client.post),('/beats/1/regenerate',client.post),('/beats/1/audio',client.get)]:
            self.assertEqual(method('/api/audio/test/Chapter%2012'+suffix).status_code,403)
        self.assertEqual(client.get('/audio/test/Chapter%2012').status_code,403)
        self.beat_patch.start()

    def test_audio_asset_range_request(self):
        with patch.object(audio,'synthesize',side_effect=self.fake):audio.generate_one('test','Chapter 1',self.current[0])
        response=TestClient(main.app).get('/api/audio/test/Chapter%201/beats/1/audio',headers={'Range':'bytes=0-4'})
        self.assertEqual(response.status_code,206);self.assertEqual(response.content,b'contr')

    def test_pause_processing_shortens_gap_without_speeding_tones(self):
        import math, struct, wave, shutil
        if not shutil.which('ffmpeg'):
            self.skipTest('FFmpeg not installed')
        source=self.root/'pause-test.wav'
        with wave.open(str(source),'wb') as out:
            out.setparams((1,2,24000,0,'NONE','not compressed'))
            tone=b''.join(struct.pack('<h',int(12000*math.sin(2*math.pi*440*i/24000))) for i in range(9600))
            out.writeframes(tone+b'\x00\x00'*19200+tone)
        self.mp3.stop()
        try:
            audio.shorten_pauses(source)
            from mutagen.mp3 import MP3
            self.assertGreater(MP3(source).info.length,0.85)
            self.assertLess(MP3(source).info.length,1.1)
        finally:
            self.mp3.start()

    def test_voice_and_pause_settings_invalidate_cached_audio(self):
        self.beat_patch.stop()
        try:
            payload={'chapters':{'Chapter 1':{'batches':{'batch-001':{'beats':[self.current[0]]}}}}}
            with patch.object(audio.narration,'status',return_value={'batches':[{'id':'batch-001','imported':True}]}), patch.object(audio.narration,'load_state',return_value=payload):
                original=audio.beats('test','Chapter 1')[0]['token']
                with patch.object(audio,'VOICE','en-US-TestNeural'):
                    self.assertNotEqual(original,audio.beats('test','Chapter 1')[0]['token'])
                with patch.object(audio,'PAUSE_PROFILE','different pause settings'):
                    self.assertNotEqual(original,audio.beats('test','Chapter 1')[0]['token'])
        finally:
            self.beat_patch.start()
