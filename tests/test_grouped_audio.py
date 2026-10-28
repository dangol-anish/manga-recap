import base64
import json
from datetime import datetime, timezone
from pathlib import Path
import shutil
import threading
import unittest
import wave
from unittest.mock import patch
import numpy as np
from app import grouped_audio as g, forced_timing as timing, speech_stage6 as speech, storage
from tests import test_speech_stage6 as speech_tests


class GroupedAudioTests(unittest.TestCase):
    ready=speech_tests.SpeechTests.ready; detect=speech_tests.SpeechTests.detect; prepare=speech_tests.SpeechTests.prepare
    payload=speech_tests.SpeechTests.payload; status=speech_tests.SpeechTests.status; submit=speech_tests.SpeechTests.submit
    fake_speech=speech_tests.SpeechTests.fake_speech
    def setUp(self):
        speech_tests.SpeechTests.setUp(self)
        g.active=g.worker=None;g.cancel.clear()
        self.key=patch.object(g,'api_key',return_value='fixture-key');self.key.start()
        self.before=speech.story.state_path(self.mid).read_bytes()
        self.calls=[]
    def tearDown(self):
        g.cancel.set()
        if g.worker and g.worker.is_alive():g.worker.join(10)
        g.active=g.worker=None;g.cancel.clear();self.key.stop()
        speech_tests.SpeechTests.tearDown(self)
    def fake_gemini(self,text,target):
        self.calls.append(text)
        with wave.open(str(target),'wb') as out:
            out.setnchannels(1);out.setsampwidth(2);out.setframerate(24000);out.writeframes(b'\x00\x00'*24000*4)
        return {'duration':4.,'provider':'gemini','voice':g.VOICE}
    def fake_timing(self,path,text,cancel,progress):
        tokens=speech.alignment.script_tokens(text)
        return [{'text':text[a:b],'char_start':a,'char_end':b,'start':i*.4,'end':i*.4+.2} for i,(_,a,b) in enumerate(tokens)]
    def begin(self):
        value=g.start(self.mid,[self.chapter],True)
        g.worker.join(15);self.assertFalse(g.active)
        return g.load(value['id'])
    def mocks(self):
        return patch.object(g,'gemini',side_effect=self.fake_gemini),patch.object(timing.Aligner,'__init__',return_value=None),patch.object(timing.Aligner,'align',self.fake_timing)
    def test_complete_recording_feeds_existing_video_and_retains_old_assets(self):
        old=speech.root(self.mid,self.chapter);old.mkdir(parents=True);(old/'keep.mp3').write_bytes(b'old')
        a,b,c=self.mocks()
        with a,b,c:job=self.begin()
        self.assertEqual(job['status'],'complete',job['message']);self.assertEqual(len(self.calls),1)
        self.assertEqual((old/'keep.mp3').read_bytes(),b'old');self.assertEqual(speech.story.state_path(self.mid).read_bytes(),self.before)
        current=speech.status(self.mid,self.chapter)
        self.assertTrue(current['grouped']);self.assertEqual(current['ready'],2);self.assertTrue(current['full']['ready'])
        from app.video_stage7 import source
        video=source(self.mid,self.chapter)[0];self.assertEqual(len(video),2)
        for clip in video:self.assertTrue(Path(clip['_audio']).is_file())
        self.assertEqual(self.client.post(self.speech_api+'/generate',json={'revision':current['revision']}).status_code,400)
        response=self.client.get(f'/api/grouped-audio/jobs/{job["id"]}/audio',headers={'Range':'bytes=0-99'})
        self.assertEqual(response.status_code,206);self.assertEqual(len(response.content),100)
        directory=storage.OUTPUTS/'Audio'/'Sample'
        self.assertTrue((directory/'Chapter1-Audio.mp3').exists());self.assertEqual(len(list(directory.glob('Chapter1-Beat*-Audio.mp3'))),2)
    def test_default_speed_prepares_new_audio_and_keeps_original(self):
        a,b,c=self.mocks()
        with a,b,c:job=self.begin()
        self.assertEqual(job['status'],'complete',job['message']);self.assertEqual(job['speed'],1.1)
        section=job['sections'][0];raw=g.metadata_file(job,section['original_recording']);paced=g.metadata_file(job,section['recording'])
        self.assertNotEqual(raw,paced);self.assertAlmostEqual(section['recording']['duration'],4/1.1,delta=.15)
        self.assertEqual(speech.status(self.mid,self.chapter)['pacing']['speed'],1.1)
        before=raw.read_bytes();calls=len(self.calls)
        g.prepare_speed(job,section)
        self.assertEqual(raw.read_bytes(),before);self.assertEqual(len(self.calls),calls)
    def test_old_job_keeps_normal_speed_after_default_change(self):
        with patch.object(g,'DEFAULT_SPEED',1.),patch.object(g,'launch',side_effect=lambda job:(g.save({**job,'status':'paused'}) or job)):
            job=g.start(self.mid,[self.chapter],True)
        a,b,c=self.mocks()
        with a,b,c:
            g.resume(job['id']);g.worker.join(15)
        job=g.load(job['id']);self.assertEqual(job['status'],'complete',job['message'])
        self.assertEqual(job['speed'],1.);self.assertEqual(job['sections'][0]['recording']['duration'],4.)
        self.assertNotIn('original_recording',job['sections'][0]);self.assertEqual(speech.status(self.mid,self.chapter)['pacing']['speed'],1.)
    def test_completed_selection_is_reused_without_another_request(self):
        a,b,c=self.mocks()
        with a,b,c:job=self.begin()
        with patch.object(g,'gemini',side_effect=AssertionError('duplicate request')):
            again=g.start(self.mid,[self.chapter],True)
        self.assertEqual(again['id'],job['id']);self.assertEqual(len(self.calls),1)
    def test_recording_returned_before_checkpoint_is_recovered(self):
        with patch.object(g,'launch',side_effect=lambda job:(g.save({**job,'status':'paused'}) or job)):
            job=g.start(self.mid,[self.chapter],True)
        folder=g.directory(job['id']);folder.mkdir(parents=True)
        self.fake_gemini(job['sections'][0]['text'],folder/'returned.wav')
        job['status']='paused';job['sections'][0].update(pending_file='returned.wav',pending_provider='gemini');g.save(job)
        with patch.object(g,'gemini',side_effect=AssertionError('must recover WAV')),patch.object(timing.Aligner,'__init__',return_value=None),patch.object(timing.Aligner,'align',self.fake_timing):
            g.resume(job['id']);g.worker.join(15)
        saved=g.load(job['id']);self.assertEqual(saved['status'],'complete',saved['message']);self.assertEqual(len(self.calls),1)
    def test_timing_failure_retains_recording_and_resume_does_not_call_gemini(self):
        with patch.object(g,'gemini',side_effect=self.fake_gemini),patch.object(timing.Aligner,'__init__',return_value=None),patch.object(timing.Aligner,'align',side_effect=ValueError('fixture timing failed')):job=self.begin()
        self.assertEqual(job['status'],'failed');self.assertTrue(job['sections'][0]['recording']);self.assertEqual(len(self.calls),1)
        with patch.object(g,'gemini',side_effect=AssertionError('must reuse WAV')),patch.object(timing.Aligner,'__init__',return_value=None),patch.object(timing.Aligner,'align',self.fake_timing):
            g.resume(job['id']);g.worker.join(15)
        job=g.load(job['id']);self.assertEqual(job['status'],'complete',job['message']);self.assertEqual(len(self.calls),1)
    def test_stop_preserves_returned_recording_and_excludes_other_tasks(self):
        entered=threading.Event();release=threading.Event()
        def slow(text,target):entered.set();release.wait(5);return self.fake_gemini(text,target)
        with patch.object(g,'gemini',side_effect=slow):
            value=g.start(self.mid,[self.chapter],True);self.assertTrue(entered.wait(3))
            from app.tasks import ensure_idle
            with self.assertRaises(FileExistsError):ensure_idle()
            self.assertEqual(self.client.post('/api/grouped-audio/jobs/'+value['id']+'/stop',json={}).status_code,200)
            release.set();g.worker.join(10)
        job=g.load(value['id']);self.assertEqual(job['status'],'paused');self.assertTrue(job['sections'][0]['recording']);self.assertFalse(job['sections'][0]['timing'])
    def test_no_automatic_brian_on_quota_failure_and_reset_guard(self):
        with patch.object(g,'gemini',side_effect=g.AudioError('daily_quota','Daily limit',g.pacific_clock()[1])):job=self.begin()
        self.assertEqual(job['status'],'failed');self.assertEqual(self.said,[])
        with patch.object(g,'choose_key',side_effect=g.AudioError('all_keys_unavailable','All keys are paused')):
            with self.assertRaises(g.AudioError):g.resume(job['id'])
        with patch.object(g,'launch',side_effect=lambda job:job) as launch:
            result=g.resume(job['id'],brian=True)
        self.assertEqual(result['engine'],'brian');launch.assert_called_once()
    def test_corrupt_recording_and_stale_source_block_resume_without_generation(self):
        with patch.object(g,'gemini',side_effect=self.fake_gemini),patch.object(timing.Aligner,'__init__',return_value=None),patch.object(timing.Aligner,'align',side_effect=ValueError('fail')):job=self.begin()
        file=g.metadata_file(job,job['sections'][0]['recording']);file.write_bytes(b'changed')
        with self.assertRaises(FileExistsError):g.resume(job['id'])
        payload=self.payload();payload['beats'][0]['narration']='A changed event.';payload['beats'][0]['anchors'][0]['phrase']='A changed event'
        self.assertEqual(self.submit(payload).status_code,200)
        with self.assertRaises(FileExistsError):g.resume(job['id'])
        self.assertEqual(len(self.calls),1)
    def test_restart_marks_paused_and_never_calls_provider(self):
        with patch.object(g,'launch',side_effect=lambda job:(g.save({**job,'status':'running'}) or job)):
            job=g.start(self.mid,[self.chapter],True)
        g.recover();self.assertEqual(g.load(job['id'])['status'],'paused');self.assertEqual(self.calls,[])
    def test_read_only_status_never_synthesizes(self):
        self.assertIsNone(self.client.get('/api/grouped-audio/status').json());self.assertEqual(self.calls,[])
    def test_api_parses_error_without_leaking_key_and_saves_wav(self):
        file=self.root/'sample.wav';self.fake_gemini('A moment.',file);raw=file.read_bytes();file.unlink()
        import httpx
        response=httpx.Response(200,json={'status':'completed','steps':[{'type':'model_output','content':[{'type':'audio','data':base64.b64encode(raw).decode()}]}]})
        with patch.object(g.httpx,'post',return_value=response) as post:
            meta=g.gemini('A moment.',file)
        self.assertEqual(meta['duration'],4);self.assertEqual(file.read_bytes(),raw)
        args=post.call_args;self.assertNotIn('fixture-key',args.args[0]);self.assertEqual(args.kwargs['json']['input'][0]['content'][0]['text'],'A moment.')
        for code in (401,403,429,500,400):self.assertNotIn('secret',str(g.classify(code,{'error':{'message':'secret'}},'secret')))
    def test_local_daily_budget_is_persistent(self):
        for i in range(10):g.reserve('test')
        with self.assertRaises(g.AudioError) as error:g.reserve('test')
        self.assertEqual(error.exception.code,'daily_quota')


class PlanningTests(unittest.TestCase):
    def test_sections_cross_chapters_whole_beats_and_bound_words(self):
        chapters=[{'chapter':f'Chapter {i}','beats':[{'chapter':f'Chapter {i}','number':j,'narration':'word '*100} for j in range(1,4)]} for i in range(1,21)]
        s=g.sections({'chapters':chapters});self.assertEqual(len(s),5);self.assertTrue(all(1000<=x['words']<=1400 for x in s))
        self.assertEqual(sum(len(x['refs']) for x in s),60);self.assertTrue(any(len(set(r['chapter'] for r in x['refs']))>1 for x in s))
        for x in s:
            for r in x['refs']:self.assertEqual(x['text'][r['start']:r['end']],'word '*100)
    def test_quota_classification_does_not_call_unknown_429_daily(self):
        self.assertEqual(g.classify(429,{'error':{'details':[{'quotaId':'RequestsPerDayPerProject'}]}}).code,'daily_quota')
        self.assertEqual(g.classify(429,{'error':{'message':'Requests per minute'}}).code,'temporary_rate_limit')
    def test_pacific_reset_dst_and_windows_without_zoneinfo(self):
        for day,expected in [('2026-01-01T20:00:00+00:00','2026-01-02T08:00:00+00:00'),('2026-06-01T20:00:00+00:00','2026-06-02T07:00:00+00:00'),('2026-03-08T07:00:00+00:00','2026-03-08T08:00:00+00:00'),('2026-11-01T06:00:00+00:00','2026-11-01T07:00:00+00:00')]:
            self.assertEqual(g.pacific_clock(datetime.fromisoformat(day))[1],expected)
    def test_ctc_real_emission_path_repeated_letters_and_rejection(self):
        vocab={'_':0,'|':1,'A':2,'L':3,'B':4};text='ALL A';labels=[0,2,0,3,0,3,0,1,0,2,0]
        logp=np.full((len(labels),5),-20.,dtype=np.float32)
        for i,label in enumerate(labels):logp[i,label]=0
        words=timing.ctc_words(logp,text,vocab,0,.02)
        self.assertEqual([w['text'] for w in words],['ALL','A']);self.assertLess(words[0]['end'],words[1]['start'])
        with self.assertRaises(ValueError):timing.ctc_words(np.zeros((2,5),dtype=np.float32),text,vocab,0,.02)
        stop=threading.Event();stop.set()
        with self.assertRaises(InterruptedError):timing.ctc_words(logp,text,vocab,0,.02,stop)
        with self.assertRaises(ValueError):timing.ctc_words(np.full_like(logp,-10),text,vocab,0,.02)
