import copy
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from app import batch_work as batch, storage


class Engine:
    def __init__(self, root, kind):
        self.root=root;self.kind=kind;self.worker=None;self.stop_event=threading.Event();self.cancel=threading.Event()
        self.started=[];self.states={};self.failed=set();self.hold=None;self.entered=threading.Event();self.assembled=[]
    def state(self, chapter):
        return self.states.setdefault(chapter,dict(revision='a'*64,render_token='b'*64,ready=0,spoken=2,
            full=dict(available=False,ready=False)))
    def status(self, mid, chapter): return copy.deepcopy(self.state(chapter))
    def job_path(self, ident):return self.root/(ident+'.json')
    def start(self, mid, chapter, token):
        from app.tasks import ensure_idle
        ensure_idle()
        self.started.append(chapter);self.stop_event.clear();self.cancel.clear()
        ident=str(len(self.started));self.entered.set()
        def run():
            if self.hold:self.hold.wait(3)
            stopped=self.stop_event.is_set() or self.cancel.is_set()
            status='paused' if stopped else 'failed' if chapter in self.failed else 'complete'
            if status=='complete':self.state(chapter).update(ready=2 if self.kind=='audio' else True,full=dict(available=True,ready=True))
            storage.write_json(self.job_path(ident),dict(status=status,message='Test '+status))
        self.worker=threading.Thread(target=run);self.worker.start()
        return dict(id=ident)
    def build_full(self, mid, chapter, token):
        self.assembled.append(chapter);self.state(chapter)['full']['ready']=True


class BatchWorkTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.engine=Engine(self.root,'audio')
        self.patches=[patch.object(storage,'DATA',self.root),patch.object(batch,'engine',return_value=self.engine)]
        for p in self.patches:p.start()
        batch.active=None;batch.worker=None;batch.cancel.clear()
    def tearDown(self):
        batch.cancel.set()
        if self.engine.hold:self.engine.hold.set()
        if batch.worker:batch.worker.join(5)
        batch.active=None;batch.worker=None;batch.cancel.clear()
        for p in reversed(self.patches):p.stop()
        self.temp.cleanup()
    def finish(self):
        batch.worker.join(5);self.assertFalse(batch.worker.is_alive());return batch.view(self.engine.kind)
    def test_selection_runs_in_natural_order_and_owns_task_gate(self):
        result=batch.start('audio','manga',['Chapter 10','Chapter 2','Chapter 1'])
        self.assertEqual(len(result['items']),3)
        job=self.finish();self.assertEqual(job['status'],'complete')
        self.assertEqual(self.engine.started,['Chapter 1','Chapter 2','Chapter 10'])
        self.assertTrue(all(i['progress']==1 for i in job['items']))
    def test_stop_resume_preserves_finished_chapters_and_blocks_other_tasks(self):
        self.engine.hold=threading.Event()
        job=batch.start('audio','manga',['Chapter 1','Chapter 2']);self.engine.entered.wait(2)
        from app.tasks import ensure_idle
        with self.assertRaises(FileExistsError):ensure_idle()
        batch.stop('audio',job['id']);time.sleep(.3);self.engine.hold.set()
        self.assertEqual(self.finish()['status'],'paused')
        self.engine.hold=None
        batch.resume('audio',job['id']);self.assertEqual(self.finish()['status'],'complete')
    def test_failure_continues_other_chapters_resume_only_unfinished(self):
        self.engine.failed.add('Chapter 1');job=batch.start('audio','manga',['Chapter 1','Chapter 2'])
        self.assertEqual(self.finish()['status'],'failed');self.engine.failed.clear()
        batch.resume('audio',job['id']);self.assertEqual(self.finish()['status'],'complete')
        self.assertEqual(self.engine.started,['Chapter 1','Chapter 2','Chapter 1'])
    def test_stale_resume_rejected_and_existing_work_not_restarted(self):
        self.engine.failed.add('Chapter 2');job=batch.start('audio','manga',['Chapter 1','Chapter 2']);self.finish()
        self.engine.state('Chapter 2')['revision']='c'*64
        with self.assertRaises(FileExistsError):batch.resume('audio',job['id'])
        self.assertEqual(self.engine.started,['Chapter 1','Chapter 2'])
    def test_recovery_requires_explicit_resume(self):
        job=dict(id='old',kind='audio',status='running',manga_id='manga',items=[dict(chapter='Chapter 1',token='a'*64,status='running')])
        batch.save(job);batch.recover();recovered=batch.view('audio')
        self.assertEqual(recovered['status'],'paused');self.assertEqual(recovered['items'][0]['status'],'waiting');self.assertEqual(self.engine.started,[])
    def test_ready_audio_assembled_without_regenerating_clips(self):
        self.engine.state('Chapter 1').update(ready=2,full=dict(available=True,ready=False))
        batch.start('audio','manga',['Chapter 1']);self.assertEqual(self.finish()['status'],'complete')
        self.assertEqual(self.engine.assembled,['Chapter 1']);self.assertEqual(self.engine.started,[])
    def test_video_uses_existing_current_video_without_rendering(self):
        self.engine.kind='video';self.engine.state('Chapter 1')['ready']=True
        batch.start('video','manga',['Chapter 1','Chapter 2']);self.assertEqual(self.finish()['status'],'complete')
        self.assertEqual(self.engine.started,['Chapter 2'])
    def test_duplicate_selection_rejected_before_start(self):
        with self.assertRaises(ValueError):batch.start('audio','manga',['Chapter 1','Chapter 1'])
        self.assertEqual(self.engine.started,[])


class BatchIntegrationTests(unittest.TestCase):
    from tests.test_speech_stage6 import SpeechTests as _fixture
    ready=_fixture.ready
    detect=_fixture.detect
    prepare=_fixture.prepare
    payload=_fixture.payload
    status=_fixture.status
    submit=_fixture.submit
    fake_speech=_fixture.fake_speech

    def setUp(self):
        self._fixture.setUp(self)
        batch.active=None;batch.worker=None;batch.cancel.clear()

    def tearDown(self):
        batch.shutdown();batch.active=None;batch.worker=None;batch.cancel.clear()
        self._fixture.tearDown(self)

    def test_real_audio_worker_and_routes_keep_script_and_reuse_completed_audio(self):
        from app import narration_stage5 as story, speech_stage6 as speech
        original=story.state_path(self.mid).read_bytes()
        self.assertIsNone(self.client.get('/api/batch-work/audio').json())
        response=self.client.post('/api/batch-work/audio',json=dict(manga_id=self.mid,chapters=[self.chapter]))
        self.assertEqual(response.status_code,200,response.text)
        batch.worker.join(10);self.assertIsNone(batch.active)
        self.assertEqual(self.client.get('/api/batch-work/audio').json()['status'],'complete')
        state=speech.status(self.mid,self.chapter);self.assertTrue(state['full']['ready']);self.assertEqual(state['ready'],state['spoken'])
        self.assertEqual(story.state_path(self.mid).read_bytes(),original)
        spoken=len(self.said)
        response=self.client.post('/api/batch-work/audio',json=dict(manga_id=self.mid,chapters=[self.chapter]))
        self.assertEqual(response.status_code,200,response.text);batch.worker.join(10)
        self.assertEqual(batch.view('audio')['status'],'complete');self.assertEqual(len(self.said),spoken)
        self.assertEqual(self.client.post('/api/batch-work/audio',json=dict(manga_id=self.mid,chapters=[])).status_code,422)
