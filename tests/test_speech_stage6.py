import json
from pathlib import Path
import shutil
import subprocess
import threading
import time
import unittest
from unittest.mock import patch
from mutagen.mp3 import MP3
from app import speech_stage6 as speech, storage, narration_stage5 as story, alignment
from tests import test_narration_stage5 as story_tests


class SpeechTests(unittest.TestCase):
    ready=story_tests.StoryTests.ready
    detect=story_tests.StoryTests.detect
    prepare=story_tests.StoryTests.prepare
    payload=story_tests.StoryTests.payload
    status=story_tests.StoryTests.status
    submit=story_tests.StoryTests.submit

    def setUp(self):
        story_tests.StoryTests.setUp(self)
        speech.active=None;speech.worker=None;speech.stop_event.clear()
        self.ready();payload=self.payload();ids=self.status()['batches'][0]['panel_ids']
        payload['beats'].append({'panel_ids':[ids[1]],'narration':'Another moment follows.','unsure':'','anchors':[{'panel_id':ids[1],'phrase':'Another moment'}]})
        self.assertEqual(self.submit(payload).status_code,200)
        self.speech_api=f'/api/speech/{self.mid}/{self.chapter}'
        self.sample=self.root/'sample.mp3'
        if shutil.which('ffmpeg'):
            subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','sine=frequency=440:duration=1.2','-ar','24000','-ac','1','-codec:a','libmp3lame',str(self.sample)],check=True,capture_output=True)
        self.said=[]
        self.synth=patch.object(speech.alignment,'speech',side_effect=self.fake_speech);self.synth.start()

    def tearDown(self):
        speech.stop_event.set()
        if speech.worker and speech.worker.is_alive():speech.worker.join(5)
        speech.active=None;speech.worker=None;speech.stop_event.clear();self.synth.stop()
        story_tests.StoryTests.tearDown(self)

    def fake_speech(self,text,target,pacing,voice=None):
        if not self.sample.exists():raise unittest.SkipTest('FFmpeg required for clip tests')
        self.said.append((text,pacing));shutil.copyfile(self.sample,target)
        duration=MP3(target).info.length
        words=[{'text':text[a:b],'char_start':a,'char_end':b,'start':i*.2,'end':i*.2+.15,'raw_start':i*.2,'raw_end':i*.2+.15} for i,(_,a,b) in enumerate(alignment.script_tokens(text))]
        return {'words':words,'duration':duration,'removed_silence':[],'sentence_pauses':1}

    def current(self):return speech.status(self.mid,self.chapter)

    def finish(self):
        if speech.worker:speech.worker.join(8)
        self.assertFalse(speech.active)
        return self.current()

    def generate(self,number=None):
        path=self.speech_api+('/generate' if number is None else f'/beats/{number}/regenerate')
        response=self.client.post(path,json={'revision':self.current()['revision']})
        self.assertEqual(response.status_code,200,response.text)
        return self.finish()

    def test_opening_status_is_read_only_and_uses_new_narration(self):
        before=story.state_path(self.mid).read_bytes()
        response=self.client.get(self.speech_api);self.assertEqual(response.status_code,200)
        s=response.json();self.assertEqual(len(s['beats']),2);self.assertEqual(s['ready'],0)
        self.assertEqual(s['voice'],'en-US-BrianMultilingualNeural')
        self.assertEqual(s['pacing'],{'rate':'+20%','speed':1.2,'sentence_pause':.18})
        self.assertEqual(self.said,[]);self.assertFalse(speech.root(self.mid,self.chapter).exists())
        self.assertEqual(story.state_path(self.mid).read_bytes(),before)
        self.assertEqual(self.client.get('/api/speech/catalog').json()[0]['chapters'][0]['audio_ready'],True)

    def test_generate_exact_script_and_real_asset_with_timestamps(self):
        before=story.state_path(self.mid).read_bytes();s=self.generate()
        self.assertEqual(s['ready'],2);self.assertEqual(s['job']['status'],'complete')
        self.assertEqual([t for t,_ in self.said],[b['narration'] for b in s['beats']])
        self.assertTrue(all(p==speech.PACING for _,p in self.said))
        clip=speech.manifest(self.mid,self.chapter)['clips'][s['beats'][0]['token']]
        self.assertTrue(clip['words']);self.assertEqual(clip['timing_anchors'][0]['start'],0)
        b=s['beats'][0];response=self.client.get(self.speech_api+'/beats/1/audio',params={'token':b['token'],'file':b['file']})
        self.assertEqual(response.status_code,200);self.assertEqual(response.content,self.sample.read_bytes())
        self.assertEqual(story.state_path(self.mid).read_bytes(),before)

    def test_retry_skips_ready_clips_and_individual_regeneration_preserves_others(self):
        first=self.generate();old=[b['file'] for b in first['beats']]
        with patch.object(speech.alignment,'speech',side_effect=RuntimeError('offline')):
            failed=self.generate(1)
        self.assertEqual(failed['job']['status'],'failed');self.assertEqual([b['file'] for b in failed['beats']],old)
        again=self.generate(1)
        self.assertNotEqual(again['beats'][0]['file'],old[0]);self.assertEqual(again['beats'][1]['file'],old[1])
        self.assertTrue((speech.root(self.mid,self.chapter)/old[0]).exists())
        self.assertEqual(len(speech.manifest(self.mid,self.chapter)['history'][again['beats'][0]['token']]),1)

    def test_failure_keeps_completed_clips_and_resume_finishes_remaining(self):
        counter=0
        def fail_second(*args):
            nonlocal counter
            counter+=1
            if counter==2:raise RuntimeError('offline')
            return self.fake_speech(*args)
        with patch.object(speech.alignment,'speech',side_effect=fail_second):s=self.generate()
        self.assertEqual(s['ready'],1);self.assertEqual(s['job']['status'],'failed')
        first=s['beats'][0]['file'];called=len(self.said)
        response=self.client.post(self.speech_api+f'/jobs/{s["job"]["id"]}/resume',json={'revision':s['revision']})
        self.assertEqual(response.status_code,200,response.text);s=self.finish()
        self.assertEqual(s['ready'],2);self.assertEqual(s['beats'][0]['file'],first);self.assertEqual(len(self.said),called+1)

    def test_stop_waits_for_current_beat_and_other_tasks_are_blocked(self):
        entered=threading.Event();release=threading.Event()
        def slow(*args):
            entered.set();release.wait(5);return self.fake_speech(*args)
        with patch.object(speech.alignment,'speech',side_effect=slow):
            response=self.client.post(self.speech_api+'/generate',json={'revision':self.current()['revision']});self.assertEqual(response.status_code,200)
            self.assertTrue(entered.wait(3))
            from app.tasks import ensure_idle
            with self.assertRaises(FileExistsError):ensure_idle()
            stopped=self.client.post('/api/speech/jobs/'+response.json()['id']+'/stop',json={})
            self.assertEqual(stopped.status_code,200);self.assertEqual(stopped.json()['status'],'stopping')
            release.set();s=self.finish()
        self.assertEqual(s['job']['status'],'paused');self.assertEqual(s['ready'],1)
        self.assertEqual(len(s['job']['completed_numbers']),1)

    def test_stale_narration_blocks_old_audio_and_resume(self):
        s=self.generate();old=s['beats'][0];self.assertTrue(old['ready'])
        payload=self.payload();payload['beats'][0]['narration']='An updated moment begins.';payload['beats'][0]['anchors'][0]['phrase']='An updated moment'
        self.assertEqual(self.submit(payload).status_code,200)
        current=self.current();self.assertEqual(current['ready'],0)
        response=self.client.get(self.speech_api+'/beats/1/audio',params={'token':old['token'],'file':old['file']})
        self.assertEqual(response.status_code,409)
        self.assertEqual(self.client.post(self.speech_api+'/generate',json={'revision':s['revision']}).status_code,409)
        self.assertTrue((speech.root(self.mid,self.chapter)/old['file']).is_file())

    def test_missing_or_stale_batch_prevents_speech(self):
        path=story.state_path(self.mid);value=storage.read_json(path);value['chapters'][self.chapter]['batches']['batch-001']['fingerprint']='0'*64;storage.write_json(path,value)
        self.assertEqual(self.client.get(self.speech_api).status_code,409)
        self.assertFalse(self.client.get('/api/speech/catalog').json()[0]['chapters'][0]['audio_ready'])
        self.assertEqual(self.said,[])

    def test_restart_recovery_never_starts_tts(self):
        job={'id':'a'*32,'status':'running','created_at':1,'manga_id':self.mid,'chapter':self.chapter}
        speech.save_job(job);speech.recover()
        saved=storage.read_json(speech.job_path(job['id']));self.assertEqual(saved['status'],'paused');self.assertEqual(self.said,[])

    def test_silent_beats_skip_synthesis(self):
        payload=self.payload();payload['beats'][0]['narration']='';payload['beats'][0].pop('anchors')
        self.assertEqual(self.submit(payload).status_code,200)
        s=self.current();self.assertEqual(s['spoken'],0)
        self.assertEqual(self.client.post(self.speech_api+'/generate',json={'revision':s['revision']}).status_code,400)
        self.assertEqual(self.said,[])

    def test_failed_manifest_commit_retains_previous_clip(self):
        old=self.generate();beat=old['beats'][0];directory=speech.root(self.mid,self.chapter);previous=(directory/'manifest.json').read_bytes()
        real_write=storage.write_json
        def fail_manifest(path,value):
            if Path(path).name=='manifest.json':raise OSError('disk full')
            return real_write(path,value)
        with patch.object(storage,'write_json',side_effect=fail_manifest):s=self.generate(1)
        self.assertEqual(s['job']['status'],'failed');self.assertEqual((directory/'manifest.json').read_bytes(),previous)
        self.assertEqual(s['beats'][0]['file'],beat['file']);self.assertEqual(len([p for p in directory.glob('*.mp3') if not p.name.startswith('chapter-')]),2)

    def test_corrupt_file_and_unsafe_path_are_not_served(self):
        s=self.generate();beat=s['beats'][0]
        (speech.root(self.mid,self.chapter)/beat['file']).write_bytes(b'bad')
        self.assertEqual(self.current()['ready'],1)
        self.assertEqual(self.client.get(self.speech_api+'/beats/1/audio',params={'token':beat['token'],'file':beat['file']}).status_code,409)
        self.assertIsNone(speech.clip_path(self.mid,self.chapter,{'file':'../escape.mp3'}))
        self.assertEqual(self.client.get(self.speech_api+'/beats/2/audio',params={'token':'0'*64,'file':'else.mp3'}).status_code,409)

    def test_native_folder_and_ffmpeg_preflight(self):
        s=self.current()
        with patch.object(speech.shutil,'which',return_value=None):
            response=self.client.post(self.speech_api+'/generate',json={'revision':s['revision']});self.assertEqual(response.status_code,400)
        self.assertEqual(self.said,[])
        self.generate()
        with patch.object(speech,'open_system_folder') as opened:
            self.assertEqual(self.client.post(self.speech_api+'/open-folder',json={}).status_code,200)
            directory=storage.OUTPUTS/'Audio'/'Sample'
            opened.assert_called_once_with(directory)
            self.assertTrue((directory/'Chapter1-Audio.mp3').is_file())
            self.assertEqual(len(list(directory.glob('Chapter1-Beat*-Audio.mp3'))),2)

    def test_multiple_phrase_switches_use_word_timestamps(self):
        payload=self.payload();ids=self.status()['batches'][0]['panel_ids'][:2]
        payload['beats']=[{'panel_ids':ids,'narration':'A new moment begins. Another moment follows.','unsure':'',
                           'anchors':[{'panel_id':ids[0],'phrase':'A new moment'},{'panel_id':ids[1],'phrase':'Another moment follows'}]}]
        self.assertEqual(self.submit(payload).status_code,200)
        s=self.generate();clip=speech.manifest(self.mid,self.chapter)['clips'][s['beats'][0]['token']]
        self.assertEqual([a['panel_id'] for a in clip['timing_anchors']],ids)
        second=clip['timing_anchors'][1];word=next(w for w in clip['words'] if w['text']=='Another')
        self.assertEqual(second['start'],word['start']);self.assertEqual(clip['timing_anchors'][0]['end'],second['start'])
        self.assertEqual(second['end'],clip['duration'])

    def test_corrupt_completed_clip_is_retried_on_resume(self):
        counter=0
        def fail_second(*args):
            nonlocal counter
            counter+=1
            if counter==2:raise RuntimeError('offline')
            return self.fake_speech(*args)
        with patch.object(speech.alignment,'speech',side_effect=fail_second):s=self.generate()
        path=speech.root(self.mid,self.chapter)/s['beats'][0]['file'];path.write_bytes(b'broken')
        called=len(self.said)
        response=self.client.post(self.speech_api+f'/jobs/{s["job"]["id"]}/resume',json={'revision':s['revision']})
        self.assertEqual(response.status_code,200,response.text);s=self.finish()
        self.assertEqual(s['ready'],2);self.assertEqual(len(self.said),called+2)

    def test_full_audio_joins_current_beats_without_tts_and_supports_seeking(self):
        s=self.generate();self.assertTrue(s['full']['ready']);calls=len(self.said)
        metadata=storage.read_json(speech.root(self.mid,self.chapter)/'chapter.json')
        self.assertEqual([b['beat'] for b in metadata['beats']],[1,2])
        self.assertEqual(metadata['beats'][0]['start'],0)
        self.assertAlmostEqual(metadata['beats'][0]['end'],metadata['beats'][1]['start'])
        self.assertAlmostEqual(metadata['duration'],2.4,places=2)
        response=self.client.post(self.speech_api+'/full',json={'revision':s['revision']})
        self.assertEqual(response.status_code,200);self.assertEqual(len(self.said),calls)
        asset=self.client.get(self.speech_api+'/full/audio',params={'token':s['full']['token']},headers={'Range':'bytes=0-99'})
        self.assertEqual(asset.status_code,206);self.assertEqual(len(asset.content),100)

    def test_regeneration_invalidates_old_full_audio_and_rebuilds_it(self):
        old=self.generate();s=self.generate(1)
        self.assertTrue(s['full']['ready']);self.assertNotEqual(old['full']['token'],s['full']['token'])
        self.assertTrue((speech.root(self.mid,self.chapter)/old['full']['file']).is_file())
        self.assertEqual(self.client.get(self.speech_api+'/full/audio',params={'token':old['full']['token']}).status_code,409)

    def test_full_audio_is_blocked_until_all_clips_are_ready(self):
        s=self.current();self.assertFalse(s['full']['available'])
        self.assertEqual(self.client.post(self.speech_api+'/full',json={'revision':s['revision']}).status_code,400)
        self.assertEqual(self.said,[])

    def test_default_change_preserves_recorded_chapter_pacing_and_tokens(self):
        old_pacing={'rate':'+0%','speed':1.0,'sentence_pause':.18}
        with patch.object(speech,'PACING',old_pacing):before=self.generate()
        directory=speech.root(self.mid,self.chapter);manifest_before=(directory/'manifest.json').read_bytes()
        current=self.current()
        self.assertEqual(current['pacing'],old_pacing);self.assertEqual(current['ready'],2)
        self.assertEqual(current['revision'],before['revision']);self.assertEqual(current['full']['token'],before['full']['token'])
        self.assertEqual((directory/'manifest.json').read_bytes(),manifest_before)
        saved=storage.read_json(directory/'manifest.json');saved.pop('pacing');storage.write_json(directory/'manifest.json',saved)
        self.assertEqual(self.current()['pacing'],old_pacing);self.assertEqual(self.current()['ready'],2)

    def test_default_voice_change_preserves_existing_chapter_and_legacy_manifest(self):
        with patch.object(speech,'VOICE','en-US-AndrewMultilingualNeural'):
            before=self.generate()
        self.assertEqual(self.current()['voice'],'en-US-AndrewMultilingualNeural')
        self.assertEqual(self.current()['ready'],2)
        self.assertEqual(self.current()['revision'],before['revision'])
        self.assertEqual(self.current()['full']['token'],before['full']['token'])
        path=speech.root(self.mid,self.chapter)/'manifest.json'
        saved=storage.read_json(path);saved.pop('voice');storage.write_json(path,saved)
        original=path.read_bytes()
        self.assertEqual(self.current()['voice'],'en-US-AndrewMultilingualNeural')
        self.assertEqual(self.current()['revision'],before['revision'])
        self.assertEqual(self.current()['ready'],2)
        self.assertEqual(path.read_bytes(),original)
        regenerated=self.generate(1)
        self.assertEqual(regenerated['voice'],'en-US-AndrewMultilingualNeural')
        self.assertEqual(regenerated['ready'],2)
