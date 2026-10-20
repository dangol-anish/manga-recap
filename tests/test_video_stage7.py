import json
from pathlib import Path
import threading
import unittest
from unittest.mock import patch
from PIL import Image
from io import BytesIO
from app import video_stage7 as cinema,speech_stage6 as speech,storage,video,narration_stage5 as story
from tests import test_speech_stage6 as fixtures


class CinemaTests(unittest.TestCase):
    ready=fixtures.SpeechTests.ready
    detect=fixtures.SpeechTests.detect
    prepare=fixtures.SpeechTests.prepare
    payload=fixtures.SpeechTests.payload
    status=fixtures.SpeechTests.status
    submit=fixtures.SpeechTests.submit
    current=fixtures.SpeechTests.current
    finish=fixtures.SpeechTests.finish
    generate=fixtures.SpeechTests.generate
    fake_speech=fixtures.SpeechTests.fake_speech

    def setUp(self):
        fixtures.SpeechTests.setUp(self);cinema.active=None;cinema.worker=None;cinema.cancel.clear()
        payload=self.payload();ids=self.status()['batches'][0]['panel_ids']
        payload['beats']=[{'panel_ids':ids[:2],'narration':'A new moment begins. Another moment follows.','unsure':'',
                           'anchors':[{'panel_id':ids[0],'phrase':'A new moment'},{'panel_id':ids[1],'phrase':'Another moment follows'}]},
                          {'panel_ids':[ids[2]],'narration':'A later moment arrives.','unsure':'','anchors':[{'panel_id':ids[2],'phrase':'A later moment'}]}]
        self.assertEqual(self.submit(payload).status_code,200);self.generate()
        self.video_api=f'/api/cinema/{self.mid}/{self.chapter}'

    def tearDown(self):
        cinema.cancel.set()
        if cinema.worker and cinema.worker.is_alive():cinema.worker.join(8)
        cinema.active=None;cinema.worker=None;cinema.cancel.clear();fixtures.SpeechTests.tearDown(self)

    def state(self):return cinema.status(self.mid,self.chapter)

    def save(self,values,**overrides):
        s=self.state();body={'source_token':s['source_token'],'render_token':s['render_token'],'number':1,'switches':values,**overrides}
        return self.client.post(self.video_api+'/timing',json=body)

    def render(self):
        response=self.client.post(self.video_api+'/export',json={'render_token':self.state()['render_token']})
        self.assertEqual(response.status_code,200,response.text)
        cinema.worker.join(35);self.assertIsNone(cinema.active)
        return self.state()

    def test_default_uses_saved_phrase_times_and_no_model_or_synthesis(self):
        before=len(self.said)
        with patch.object(speech.alignment,'speech',side_effect=AssertionError('No TTS')):
            response=self.client.get(self.video_api)
        self.assertEqual(response.status_code,200);s=response.json()
        self.assertEqual(s['beats'][0]['switches'],[.8])
        self.assertNotEqual(s['beats'][0]['switches'],[s['beats'][0]['duration']/2])
        self.assertEqual(s['beats'][0]['panels'][0]['motion_kind'],'zoom_in')
        self.assertEqual(s['transition_style']['frames'],4)
        self.assertNotIn('_audio',s['beats'][0]);self.assertNotIn('_crop',s['beats'][0]['panels'][0])
        self.assertIn('/api/speech/',s['preview_audio']);self.assertEqual(len(self.said),before)
        self.assertFalse((cinema.root(self.mid,self.chapter)/'timing.json').exists())
        self.assertFalse(list((storage.DATA/'cinema/jobs').glob('*.json')))

    def test_save_timing_and_concurrent_edits_preserve_sources(self):
        before=story.state_path(self.mid).read_bytes();s=self.state();audio_manifest=(speech.root(self.mid,self.chapter)/'manifest.json').read_bytes()
        changed=self.save([.4]);self.assertEqual(changed.status_code,200,changed.text)
        self.assertEqual(changed.json()['beats'][0]['switches'],[.4]);self.assertNotEqual(changed.json()['render_token'],s['render_token'])
        self.assertEqual(self.save([.5],render_token=s['render_token']).status_code,409)
        self.assertEqual(story.state_path(self.mid).read_bytes(),before)
        self.assertEqual((speech.root(self.mid,self.chapter)/'manifest.json').read_bytes(),audio_manifest)
        restored=self.save(s['beats'][0]['phrase_switches']);self.assertEqual(restored.status_code,200)
        self.assertEqual(restored.json()['beats'][0]['switches'],[.8])

    def test_invalid_timing_is_rejected_without_writing(self):
        for values in [[],[-1],[0],[True],[9],[.001],[.5,.6]]:
            response=self.save(values);self.assertEqual(response.status_code,400,(values,response.text))
        self.assertFalse((cinema.root(self.mid,self.chapter)/'timing.json').exists())

    def test_frame_composition_and_unknown_frame_rejection(self):
        s=self.state();panel=s['beats'][0]['panels'][0]
        response=self.client.get(panel['frame_url']);self.assertEqual(response.status_code,200)
        with Image.open(BytesIO(response.content)) as image:
            self.assertEqual(image.size,(1280,720));self.assertNotEqual(image.getpixel((0,0)),(0,0,0))
        self.assertEqual(self.client.get(self.video_api+'/frame',params={'panel_id':panel['id'],'key':'wrong'}).status_code,409)

    def test_real_mp4_export_has_audio_video_duration_and_does_not_change_audio(self):
        before={p.name:speech.checksum(p) for p in speech.root(self.mid,self.chapter).glob('*.mp3')};calls=len(self.said)
        s=self.render();self.assertTrue(s['ready'],s['job']);self.assertEqual(s['job']['status'],'complete')
        response=self.client.get(self.video_api+'/mp4',params={'token':s['render_token']},headers={'Range':'bytes=0-99'})
        self.assertEqual(response.status_code,206);self.assertEqual(len(response.content),100)
        self.assertEqual({p.name:speech.checksum(p) for p in speech.root(self.mid,self.chapter).glob('*.mp3')},before)
        self.assertEqual(len(self.said),calls)
        old=cinema.export_path(self.mid,self.chapter,s['render_token']);self.assertTrue(old.exists())
        changed=self.save([.4]).json();self.assertFalse(changed['ready']);self.assertTrue(old.exists())
        self.assertEqual(self.client.get(self.video_api+'/mp4',params={'token':s['render_token']}).status_code,409)

    def test_failed_export_retains_previous_mp4(self):
        # A tiny fixture file is enough for publication/failure behavior; the real renderer is tested above.
        def fake(state,work,**kwargs):(work/'chapter.mp4').write_bytes(b'fixture-mp4')
        with patch.object(video,'render_to',side_effect=fake):s=self.render()
        path=cinema.export_path(self.mid,self.chapter,s['render_token']);old=path.read_bytes()
        with patch.object(video,'render_to',side_effect=RuntimeError('encoder failed')):failed=self.render()
        self.assertTrue(failed['ready']);self.assertEqual(failed['job']['status'],'failed');self.assertEqual(path.read_bytes(),old)

    def test_audio_changes_invalidate_saved_timing(self):
        self.save([.4]);old=self.state();self.generate(1)
        s=self.state();self.assertTrue(s['timing_reset']);self.assertEqual(s['beats'][0]['switches'],[.8]);self.assertNotEqual(s['source_token'],old['source_token'])
        self.assertEqual(self.save([.5],source_token=old['source_token']).status_code,409)

    def test_active_render_blocks_other_tasks_and_recovers_after_shutdown(self):
        entered=threading.Event()
        def interrupted(state,work,cancel_event=None):
            entered.set();cancel_event.wait(5);raise InterruptedError('Stopped')
        with patch.object(video,'render_to',side_effect=interrupted):
            response=self.client.post(self.video_api+'/export',json={'render_token':self.state()['render_token']})
            self.assertEqual(response.status_code,200);self.assertTrue(entered.wait(2))
            self.assertEqual(self.save([.4]).status_code,409)
            from app.tasks import ensure_idle
            with self.assertRaises(FileExistsError):ensure_idle()
            cinema.shutdown();self.assertIsNone(cinema.active)
        self.assertEqual(self.state()['job']['status'],'interrupted')
        job={'id':'b'*32,'created_at':1,'status':'running'};storage.write_json(cinema.job_path(job['id']),job);cinema.recover()
        self.assertEqual(storage.read_json(cinema.job_path(job['id']))['status'],'interrupted')

    def test_stale_narration_blocks_preview_and_export(self):
        value=storage.read_json(story.state_path(self.mid));value['chapters'][self.chapter]['batches']['batch-001']['fingerprint']='0'*64
        storage.write_json(story.state_path(self.mid),value)
        self.assertEqual(self.client.get(self.video_api).status_code,409)

    def test_silent_preview_has_padding_matching_export_duration(self):
        payload=self.payload();payload['beats'][0]['narration']='';payload['beats'][0].pop('anchors')
        self.assertEqual(self.submit(payload).status_code,200)
        s=self.state();self.assertIn('/preview-audio?',s['preview_audio'])
        response=self.client.get(s['preview_audio']);self.assertEqual(response.status_code,200)
        import wave
        with wave.open(BytesIO(response.content)) as audio:
            self.assertEqual(audio.getnframes()/audio.getframerate(),s['duration'])
        stale=self.client.get(self.video_api+'/preview-audio',params={'source_token':'0'*64});self.assertEqual(stale.status_code,409)

    def test_failed_publication_restores_prior_export(self):
        def fake(state,work,**kwargs):(work/'chapter.mp4').write_bytes(b'valid-original')
        with patch.object(video,'render_to',side_effect=fake):s=self.render()
        path=cinema.export_path(self.mid,self.chapter,s['render_token']);old=path.read_bytes();metadata=(path.parent/'export.json').read_bytes()
        write=storage.write_json
        def fail_metadata(path,value):
            if Path(path).name=='export.json':raise OSError('disk full')
            return write(path,value)
        def changed(state,work,**kwargs):(work/'chapter.mp4').write_bytes(b'new-replacement')
        with patch.object(video,'render_to',side_effect=changed),patch.object(storage,'write_json',side_effect=fail_metadata):failed=self.render()
        self.assertEqual(failed['job']['status'],'failed');self.assertTrue(failed['ready'])
        self.assertEqual(path.read_bytes(),old);self.assertEqual((path.parent/'export.json').read_bytes(),metadata)
