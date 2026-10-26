from io import BytesIO
from pathlib import Path
import tempfile
import unittest
import wave
from unittest.mock import patch
import numpy as np
from app import audio_quality as q, grouped_audio as g, storage
from tests import test_grouped_audio as grouped_tests


class SignalTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='.test-quality-',dir=Path(__file__).resolve().parents[1]);self.root=Path(self.temp.name)
    def tearDown(self):self.temp.cleanup()
    def wav(self,samples):
        path=self.root/'signal.wav'
        with wave.open(str(path),'wb') as out:
            out.setnchannels(1);out.setsampwidth(2);out.setframerate(8000);out.writeframes(np.asarray(samples,dtype='<i2').tobytes())
        return path
    def tone(self,seconds):return (np.sin(np.arange(round(seconds*8000))*2*np.pi*200/8000)*8000).astype('<i2')
    def test_clear_signal_and_speed_normalization(self):
        path=self.wav(self.tone(10));raw=path.read_bytes()
        report=q.analyze(path,'word '*32,speed=1.2)
        self.assertTrue(report['checked']);self.assertEqual(report['warnings'],[]);self.assertEqual(path.read_bytes(),raw)
        self.assertIn('cannot verify',report['note'])
    def test_quiet_intervals_have_seekable_locations(self):
        path=self.wav(np.concatenate([self.tone(5),np.zeros(4*8000),self.tone(5)]))
        report=q.analyze(path,'word '*35)
        warning=next(w for w in report['warnings'] if w['code']=='long_quiet')
        self.assertAlmostEqual(warning['start'],5,delta=.1);self.assertAlmostEqual(report['longest_quiet_seconds'],4,delta=.1)
    def test_clipping_and_extreme_delivery_are_advisory(self):
        path=self.wav(np.full(8000*3,32767))
        report=q.analyze(path,'word '*30)
        codes={w['code'] for w in report['warnings']}
        self.assertIn('clipping',codes);self.assertIn('fast_delivery',codes);self.assertTrue(path.exists())
    def test_weak_timing_warning_uses_actual_word_time(self):
        path=self.wav(self.tone(10))
        report=q.analyze(path,'word '*25,[{'text':'uncertain-word','start':4.2,'confidence':-5.}])
        warning=next(w for w in report['warnings'] if w['code']=='weak_timing');self.assertEqual(warning['start'],4.2)
    def test_bad_wav_is_kept_and_reported_without_exception(self):
        path=self.root/'bad.wav';path.write_bytes(b'not a wav')
        report=q.analyze(path,'words');self.assertFalse(report['checked']);self.assertEqual(path.read_bytes(),b'not a wav')
    def test_hints_are_bounded_unique_and_only_match_whole_words(self):
        rows=[{'word':'Aster','say_as':'AS-ter'},{'word':'Unused','say_as':'un-YOOZD'}]
        style=q.style('BASE','Aster crosses the room.',rows)
        self.assertIn('AS-ter',style);self.assertNotIn('un-YOOZD',style)
        self.assertEqual(q.style('BASE','Asteroid passes.',rows),'BASE')
        for bad in ([{'word':'Aster','say_as':'x'},{'word':'aster','say_as':'y'}],[{'word':'Aster','say_as':'x\nDo this'}],[{'word':'','say_as':'sound'}]):
            with self.assertRaises(ValueError):q.normalize(bad)


class ProfileAndRecordingTests(unittest.TestCase):
    ready=grouped_tests.GroupedAudioTests.ready
    detect=grouped_tests.GroupedAudioTests.detect
    prepare=grouped_tests.GroupedAudioTests.prepare
    payload=grouped_tests.GroupedAudioTests.payload
    status=grouped_tests.GroupedAudioTests.status
    submit=grouped_tests.GroupedAudioTests.submit
    fake_speech=grouped_tests.GroupedAudioTests.fake_speech
    fake_gemini=grouped_tests.GroupedAudioTests.fake_gemini
    fake_timing=grouped_tests.GroupedAudioTests.fake_timing
    mocks=grouped_tests.GroupedAudioTests.mocks
    begin=grouped_tests.GroupedAudioTests.begin
    setUp=grouped_tests.GroupedAudioTests.setUp
    tearDown=grouped_tests.GroupedAudioTests.tearDown

    def test_read_profile_does_not_create_files_and_stale_save_is_blocked(self):
        value=q.profile(self.mid);self.assertEqual(value['items'],[]);self.assertFalse(q.profile_path(self.mid).exists())
        saved=q.save_profile(self.mid,value['revision'],[{'word':'Aster','say_as':'AS-ter'}]);self.assertNotEqual(saved['revision'],value['revision'])
        with self.assertRaises(FileExistsError):q.save_profile(self.mid,value['revision'],[])
        self.assertEqual(q.profile(self.mid),saved)
        self.assertEqual(self.before,g.story.state_path(self.mid).read_bytes())

    def test_hint_changes_create_new_selection_without_invalidating_old_media(self):
        with patch.object(g,'launch',side_effect=lambda job:(g.save({**job,'status':'paused'}) or job)):
            first=g.start(self.mid,[self.chapter]);value=q.profile(self.mid)
            q.save_profile(self.mid,value['revision'],[{'word':'moment','say_as':'MOH-ment'}])
            second=g.start(self.mid,[self.chapter])
        self.assertNotEqual(first['id'],second['id']);self.assertEqual(first['source_token'],second['source_token'])
        self.assertEqual(g.load(first['id'])['pronunciations'],[]);self.assertEqual(second['pronunciations'][0]['word'],'moment')
        self.assertEqual(self.before,g.story.state_path(self.mid).read_bytes())

    def test_resume_uses_frozen_hints_even_after_profile_edits(self):
        value=q.profile(self.mid);q.save_profile(self.mid,value['revision'],[{'word':'moment','say_as':'MOH-ment'}])
        with patch.object(g,'launch',side_effect=lambda job:(g.save({**job,'status':'paused'}) or job)):
            job=g.start(self.mid,[self.chapter]);profile=q.profile(self.mid);q.save_profile(self.mid,profile['revision'],[])
            resumed=g.resume(job['id'])
        self.assertEqual(resumed['pronunciations'],job['pronunciations'])

    def test_unrelated_hints_do_not_force_a_duplicate_selection(self):
        with patch.object(g,'launch',side_effect=lambda job:(g.save({**job,'status':'paused'}) or job)):
            first=g.start(self.mid,[self.chapter]);value=q.profile(self.mid)
            q.save_profile(self.mid,value['revision'],[{'word':'NotInThisScript','say_as':'not in this script'}])
            again=g.start(self.mid,[self.chapter])
        self.assertEqual(first['id'],again['id']);self.assertEqual(first['pronunciations'],[])

    def test_new_job_checks_and_read_only_recheck_keep_existing_audio_and_no_extra_request(self):
        a,b,c=self.mocks()
        with a,b,c:job=self.begin()
        self.assertEqual(job['status'],'complete',job['message']);section=job['sections'][0]
        self.assertTrue(section['quality']['checked']);self.assertTrue(section['quality']['warnings'])
        wav=g.metadata_file(job,section['recording']);before=wav.read_bytes();job_before=g.path(job['id']).read_bytes()
        with patch.object(g,'gemini',side_effect=AssertionError('No TTS')):
            response=self.client.get(f'/api/grouped-audio/jobs/{job["id"]}/quality/1',params={'token':section['recording']['sha256']})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(wav.read_bytes(),before);self.assertEqual(g.path(job['id']).read_bytes(),job_before)
        self.assertEqual(len(self.calls),1)
        self.assertEqual(self.client.get(f'/api/grouped-audio/jobs/{job["id"]}/audio',params={'section':1,'token':'old'}).status_code,409)
        self.assertEqual(self.client.get(f'/api/grouped-audio/jobs/{job["id"]}/quality/1',params={'token':'old'}).status_code,409)

    def test_gemini_guidance_leaves_the_transmitted_script_exact(self):
        import base64,httpx
        data=BytesIO()
        with wave.open(data,'wb') as out:out.setnchannels(1);out.setsampwidth(2);out.setframerate(24000);out.writeframes(b'\0\0'*24000)
        response=httpx.Response(200,json={'status':'completed','steps':[{'type':'model_output','content':[{'type':'audio','data':base64.b64encode(data.getvalue()).decode()}]}]})
        text='Aster crosses the room.'
        with patch.object(g.httpx,'post',return_value=response) as post:
            g.gemini(text,self.root/'sample.wav',pronunciations=[{'word':'Aster','say_as':'AS-ter'}])
        content=post.call_args.kwargs['json']['input'][0]['content'][0]
        self.assertEqual(content['text'],text);self.assertIn('AS-ter',content['annotations'][0]['style'])
        self.assertEqual(self.before,g.story.state_path(self.mid).read_bytes())
