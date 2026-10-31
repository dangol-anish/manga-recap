"""Only temporary synthetic chapters and a fake AI Studio. Never live generation."""
import copy
import json
import threading
import time
import unittest
from contextlib import nullcontext
from unittest.mock import patch
from tests import test_narration_stage5 as story_tests
from app import browser_narration as queue, storage, tasks
from app.studio_browser import Attention, json_objects, page_problem


class FakeBrowser:
    sent = []
    failure = None
    invalid = False
    def __enter__(self): return self
    def __exit__(self,*args): pass
    def prepare(self,prompt,photos,cancel,fresh=True,reply_mode=None):
        self.prompt=prompt
        self.photos=photos
        self.fresh=fresh
    def visible_replies(self): return []
    def send(self):
        self.sent.append((self.prompt,list(self.photos),self.fresh))
        if self.failure: raise self.failure
        return 'https://aistudio.google.com/prompts/fixture'
    def receive(self,before,cancel,checkpoint,timeout=1200):
        checkpoint('https://aistudio.google.com/prompts/fixture?secret=hidden')
        if self.prompt.startswith('TASK: REPAIR') and not self.prompt.startswith('TASK: REPAIR COMPLETED RESPONSE FORMAT'):
            return json.dumps({'beats':[{'replace_beat':1,'panel_ids':['p0000-1'],'narration':'A new moment begins.','unsure':'','anchors':[{'panel_id':'p0000-1','phrase':'A new moment'}]}]})
        receipt=json.loads(self.prompt.split('BATCH_RECEIPT (copy exactly into batch_receipt):\n')[1])
        allowed=json.loads(self.prompt.split('VALID_PANEL_IDS (exact IDs, in reading order):\n')[1].split('\n')[0])
        pid='not-a-panel' if self.invalid else allowed[0]
        return json.dumps({'batch_receipt':receipt,'cast':[],'beats':[{'panel_ids':[pid],'narration':'A new moment begins.','unsure':'','anchors':[{'panel_id':pid,'phrase':'A new moment'}]}],
                           'continuity':{'summary':'A moment.','open_threads':[]},'chapter_summary':'A moment.', 'memory':[]})
    def recover_page(self,url): self.recovered=url


class BrowserNarrationTests(unittest.TestCase):
    ready=story_tests.StoryTests.ready
    status=story_tests.StoryTests.status
    prepare=story_tests.StoryTests.prepare
    detect=story_tests.StoryTests.detect
    def setUp(self):
        story_tests.StoryTests.setUp(self)
        queue.latest=queue.active=queue.worker=None
        queue.checkpoint_failed=False
        queue.cancel.clear();queue.resume_signal.clear()
        FakeBrowser.sent=[];FakeBrowser.failure=None;FakeBrowser.invalid=False
        self.extra=[patch.object(queue,'StudioBrowser',FakeBrowser),patch('app.runtime.keep_awake',return_value=nullcontext())]
        for p in self.extra:p.start()
        self.ready()

    def tearDown(self):
        queue.shutdown()
        for p in reversed(self.extra):p.stop()
        queue.latest=queue.active=queue.worker=None
        queue.checkpoint_failed=False
        story_tests.StoryTests.tearDown(self)

    def wait(self,paused=False):
        # Multi-batch fixtures validate every source repeatedly on Windows.
        # This is a test-worker deadline, independent of AI response timeouts.
        deadline=time.monotonic()+60
        while time.monotonic()<deadline:
            job=queue.current()
            if job and job['status'] in {'paused','failed','complete'}:
                if not paused and queue.worker:queue.worker.join(2)
                return job
            time.sleep(.01)
        self.fail(f'Fixture narration worker did not finish within 60 seconds: {queue.current()}')

    def test_browser_queue_imports_checkpoints_and_reuses_current_work(self):
        queue.start(self.mid,[self.chapter]);job=self.wait()
        self.assertEqual(job['status'],'complete',job)
        self.assertTrue(self.status()['batches'][0]['imported'])
        self.assertEqual(len(FakeBrowser.sent),1)
        self.assertTrue(all(p.suffix=='.jpg' for p in FakeBrowser.sent[0][1]))
        self.assertTrue(list(queue.directory().rglob('reply-0.txt')))
        self.assertNotIn('secret=hidden',json.dumps(job))
        accepted=copy.deepcopy(self.status())
        queue.clear_queue(job['id'])
        self.assertIsNone(queue.current())
        self.assertEqual(self.status(),accepted,'Clearing must preserve accepted narration and context')
        self.assertTrue(list(queue.directory().rglob('reply-0.txt')))
        queue.start(self.mid,[self.chapter]);self.assertEqual(self.wait()['status'],'complete')
        self.assertEqual(len(FakeBrowser.sent),1,'Already accepted current narration must not be resubmitted after clearing')

    def test_extension_adapter_completes_the_validated_queue_contract(self):
        from app import narration_extension as extension
        from pathlib import Path
        schema=storage.ROOT/'browser-extension/narration-schema.json'
        schema.parent.mkdir(exist_ok=True)
        schema.write_bytes((Path(__file__).resolve().parents[1]/'browser-extension/narration-schema.json').read_bytes())
        fake=FakeBrowser();sent=False;actions=[]
        def rpc(action,payload=None,*args,**kwargs):
            nonlocal sent
            actions.append(action)
            if action=='prepare':
                self.assertTrue(all(p['name'].endswith('.jpg') for p in payload['photos']))
                fake.prepare(payload['prompt'],[],queue.cancel,payload['fresh'])
            if action=='send':sent=True;fake.send()
            text=fake.receive([],queue.cancel,lambda url:None) if sent else ''
            return {'protocol':4,'window_visibility':True,'url':'https://aistudio.google.com/prompts/fixture','replies':[{'id':'model-1','text':text,'completed':True}] if sent else [],'finished':True,'generating':False}
        with patch.object(queue,'StudioBrowser',extension.ExtensionBrowser),patch.object(extension,'command',side_effect=rpc):
            queue.start(self.mid,[self.chapter]);job=self.wait()
        self.assertEqual(job['status'],'complete',job)
        self.assertTrue(self.status()['batches'][0]['imported'])
        self.assertEqual(actions.count('send'),1)
        self.assertTrue(list(queue.directory().rglob('reply-0.txt')))

    def test_limit_pauses_and_explicit_resume_is_required(self):
        FakeBrowser.failure=Attention('Usage limit reached.','limit',True)
        queue.start(self.mid,[self.chapter]);job=self.wait(paused=True)
        self.assertEqual(job['attention'],'limit');self.assertIsNone(queue.active)
        self.assertFalse(queue.story.state_path(self.mid).exists())
        self.assertEqual(len(FakeBrowser.sent),1)
        time.sleep(.05);self.assertEqual(len(FakeBrowser.sent),1)
        FakeBrowser.failure=None
        queue.resume(job['id']);self.assertEqual(self.wait()['status'],'complete')
        self.assertEqual(len(FakeBrowser.sent),2)

    def test_all_bad_format_replies_have_a_shared_two_repair_budget(self):
        for bad in ('', 'ok', '{broken', '[]', '{"response":"ok"}'):
            with self.subTest(reply=bad),patch.object(FakeBrowser,'receive',return_value=bad):
                FakeBrowser.sent=[]
                queue.start(self.mid,[self.chapter]);job=self.wait(paused=True)
                self.assertEqual(job['attention'],'validation',job)
                self.assertEqual(len(FakeBrowser.sent),3)
                self.assertEqual(job['correction'],2)
                self.assertEqual(job['phase'],'reply_saved')
                self.assertEqual(len(list((queue.directory()/'replies'/job['id']).rglob('reply-*.txt'))),3)
                self.assertFalse(self.status()['batches'][0]['imported'])
                with self.assertRaises(ValueError):queue.resume(job['id'])
                queue.stop(job['id']);queue.worker.join(3)

    def test_rejected_batch_can_be_retried_without_codex_or_losing_drafts(self):
        with patch.object(FakeBrowser,'receive',return_value='not narration'):
            queue.start(self.mid,[self.chapter]);job=self.wait(paused=True)
        old_files=list((queue.directory()/'replies'/job['id']).rglob('reply-*.txt'))
        result=queue.retry_batch_api(job['id'])
        self.assertEqual(self.wait()['status'],'complete')
        self.assertTrue(all(p.exists() for p in old_files))
        self.assertEqual(queue.current()['items'][0]['take'],1)

    def test_bad_format_can_repair_to_a_valid_full_reply_in_the_same_chat(self):
        original=FakeBrowser.receive;count=0
        def response(browser,*args,**kwargs):
            nonlocal count
            count+=1
            return '{broken' if count==1 else original(browser,*args,**kwargs)
        with patch.object(FakeBrowser,'receive',response):
            queue.start(self.mid,[self.chapter]);job=self.wait()
        self.assertEqual(job['status'],'complete',job)
        self.assertEqual(len(FakeBrowser.sent),2)
        self.assertFalse(FakeBrowser.sent[1][2]);self.assertEqual(FakeBrowser.sent[1][1],[])
        self.assertTrue(self.status()['batches'][0]['imported'])

    def test_retry_second_batch_preserves_the_first_and_skips_its_submission(self):
        self.ready(21);original=FakeBrowser.receive
        def response(browser,*args,**kwargs):
            if '"batch_id": "batch-002"' in browser.prompt:return 'invalid narration'
            return original(browser,*args,**kwargs)
        with patch.object(FakeBrowser,'receive',response):
            queue.start(self.mid,[self.chapter]);job=self.wait(paused=True)
        first=copy.deepcopy(queue.story.load_state(self.mid)['chapters'][self.chapter]['batches']['batch-001'])
        self.assertEqual(job['cursor'],1);self.assertEqual(job['items'][0]['status'],'complete')
        count=len(FakeBrowser.sent);queue.retry_batch_api(job['id'])
        self.assertEqual(self.wait()['status'],'complete')
        self.assertEqual(len(FakeBrowser.sent),count+1)
        self.assertEqual(queue.story.load_state(self.mid)['chapters'][self.chapter]['batches']['batch-001'],first)
        self.assertEqual(len(FakeBrowser.sent[-1][1]),1)

    def test_stop_while_waiting_returns_paused_without_another_submission(self):
        entered=threading.Event()
        def waiting(browser,before,cancel,checkpoint,timeout=1200):
            entered.set();cancel.wait(4)
            raise InterruptedError('Stopped fixture wait')
        with patch.object(FakeBrowser,'receive',waiting):
            job=queue.start(self.mid,[self.chapter]);self.assertTrue(entered.wait(3))
            queue.stop(job['id']);queue.worker.join(4)
        job=queue.current()
        self.assertEqual(job['status'],'paused');self.assertEqual(job['attention'],'stopped')
        self.assertIsNone(job['error']);self.assertEqual(len(FakeBrowser.sent),1)
        self.assertIsNone(queue.active)

    def test_missing_saved_reply_does_not_resend_or_reset_narration(self):
        from app import narration
        with patch.object(narration,'import_reply',side_effect=InterruptedError('fixture import interruption')):
            queue.start(self.mid,[self.chapter]);job=self.wait()
        for path in (queue.directory()/'replies'/job['id']).rglob('reply-0.txt'):path.unlink()
        queue.resume(job['id']);job=self.wait()
        self.assertEqual(job['status'],'paused');self.assertIn('missing or damaged',job['message'])
        self.assertEqual(len(FakeBrowser.sent),1)

    def test_refusal_pauses_without_automatic_repair_and_keeps_raw_reply(self):
        with patch.object(FakeBrowser,'receive',return_value='I cannot assist with this request.'):
            queue.start(self.mid,[self.chapter]);job=self.wait(paused=True)
        self.assertEqual(job['attention'],'response');self.assertEqual(len(FakeBrowser.sent),1)
        self.assertEqual(job['phase'],'reply_saved')
        self.assertTrue(list((queue.directory()/'replies'/job['id']).rglob('reply-0.txt')))

    def test_resume_is_rejected_while_worker_is_stopping(self):
        FakeBrowser.failure=Attention('fixture limit','limit',True)
        queue.start(self.mid,[self.chapter]);job=self.wait(paused=True)
        queue.cancel.set()
        with self.assertRaises(FileExistsError):queue.resume(job['id'])
        self.assertEqual(len(FakeBrowser.sent),1)

    def test_stop_reconciles_running_checkpoint_without_worker(self):
        FakeBrowser.failure=Attention('fixture limit','limit',True)
        queue.start(self.mid,[self.chapter]);job=self.wait(paused=True)
        queue.stop(job['id']);queue.worker.join(3);queue.worker=None
        job=queue.current();job['status']='stopping';queue.save(job)
        stopped=queue.stop(job['id']);self.assertEqual(stopped['status'],'paused')
        self.assertEqual(stopped['attention'],'stopped')

    def test_next_batch_uses_the_just_imported_context(self):
        self.ready(21)
        queue.start(self.mid,[self.chapter]);job=self.wait()
        self.assertEqual(job['status'],'complete',job)
        self.assertEqual(len(FakeBrowser.sent),2)
        self.assertIn('A new moment begins.',FakeBrowser.sent[1][0])
        first=json.loads(FakeBrowser.sent[0][0].split('BATCH_RECEIPT (copy exactly into batch_receipt):\n')[1])
        second=json.loads(FakeBrowser.sent[1][0].split('BATCH_RECEIPT (copy exactly into batch_receipt):\n')[1])
        self.assertNotEqual(first['context_token'],second['context_token'])
        self.assertEqual(second['batch_id'],'batch-002')
        self.assertEqual([len(s[1]) for s in FakeBrowser.sent],[20,1])

    def test_saved_reply_survives_interruption_before_import_without_resend(self):
        from app import narration
        with patch.object(narration,'import_reply',side_effect=InterruptedError('fixture interruption')):
            queue.start(self.mid,[self.chapter]);job=self.wait()
        self.assertEqual(job['phase'],'importing')
        self.assertTrue(list(queue.directory().rglob('reply-0.txt')))
        queue.resume(job['id']);self.assertEqual(self.wait()['status'],'complete')
        self.assertEqual(len(FakeBrowser.sent),1)

    def test_ambiguous_submission_resume_recovers_instead_of_resending(self):
        FakeBrowser.failure=Attention('Browser disconnected during submission.','browser',False)
        queue.start(self.mid,[self.chapter]);job=self.wait(paused=True)
        self.assertEqual(job['phase'],'sending')
        self.assertFalse(job['retry_safe'])
        # This test verifies recovery takes the read path, without any send.
        FakeBrowser.failure=None
        with patch.object(FakeBrowser,'recover_page',side_effect=Attention('Check unfinished request.','recovery')):
            queue.resume(job['id']);self.wait(paused=True)
            self.assertEqual(len(FakeBrowser.sent),1)
        queue.resume(job['id'],retry=True);self.assertEqual(self.wait()['status'],'complete')
        self.assertEqual(len(FakeBrowser.sent),2)

    def test_corrections_use_original_chat_and_existing_validator(self):
        FakeBrowser.invalid=True
        queue.start(self.mid,[self.chapter]);job=self.wait()
        self.assertEqual(job['status'],'complete',job)
        self.assertEqual(len(FakeBrowser.sent),2)
        self.assertTrue(FakeBrowser.sent[0][2]);self.assertFalse(FakeBrowser.sent[1][2])
        self.assertEqual(FakeBrowser.sent[1][1],[])
        self.assertIn('replace_beat',FakeBrowser.sent[1][0])

    def test_changed_sources_block_resuming_without_new_requests(self):
        FakeBrowser.failure=Attention('Usage limit reached.','limit',True)
        queue.start(self.mid,[self.chapter]);job=self.wait(paused=True)
        saved=queue.story.load_state(self.mid);saved['outside_change']=True
        storage.write_json(queue.story.state_path(self.mid),saved)
        with self.assertRaises(FileExistsError):queue.resume(job['id'])
        self.assertEqual(len(FakeBrowser.sent),1)

    def test_recovery_never_launches_a_browser(self):
        with patch.object(queue,'launch',side_effect=AssertionError('No automatic browser')):
            job={'schema_version':1,'id':'f'*32,'manga_id':self.mid,'status':'running','items':[], 'expected_revision':'a'*64,'prompt_revision':'b'*64,'phase':'ready','cursor':0}
            queue.save(job);storage.write_json(queue.directory()/'current.json',job['id']);queue.recover()
            self.assertEqual(queue.current()['status'],'paused')

    def test_reading_status_never_opens_browser_or_submits(self):
        with patch.object(queue,'StudioBrowser',side_effect=AssertionError('Read-only')):
            self.assertIsNone(self.client.get('/api/browser-narration').json())
        self.assertFalse(queue.directory().exists())

    def test_task_exclusion_and_stop_release_busy_state(self):
        FakeBrowser.failure=Attention('Sign in.','login',True)
        queue.start(self.mid,[self.chapter]);job=self.wait(paused=True)
        with patch.object(queue,'active',{'id':'fixture'}):
            with self.assertRaises(FileExistsError):tasks.ensure_idle()
        queue.stop(job['id']);queue.worker.join(3)
        self.assertFalse(queue.worker.is_alive());self.assertIsNone(queue.active)

    def test_checkpoint_failure_releases_busy_state(self):
        original=queue.save
        def failing(job):
            if job.get('phase')=='sending':raise OSError('fixture disk full')
            return original(job)
        with patch.object(queue,'save',side_effect=failing):
            queue.start(self.mid,[self.chapter]);job=self.wait()
        self.assertEqual(job['status'],'paused');self.assertIsNone(queue.active)
        self.assertEqual(FakeBrowser.sent,[],'Never submit when write-ahead checkpoint fails')

    def test_visible_json_parser_and_limit_classification(self):
        reply='{"beats":[],"cast":[]}'
        self.assertEqual(json_objects('unrelated { text\n'+reply+'\nend'),[reply])
        self.assertEqual(page_problem('https://aistudio.google.com/prompts/x','You have reached your daily limit.').code,'limit')
        self.assertIsNone(page_problem('https://aistudio.google.com/prompts/x','Ready for a prompt.'))

    def test_exact_ai_studio_response_envelope_preserves_inner_json(self):
        # Preserve exact script bytes after decoding the transport envelope.
        reply=json.dumps({'beats':[{'narration':'He says "Go!"'}],'cast':[]},ensure_ascii=False)
        self.assertEqual(json_objects(json.dumps({'response':reply})),[reply])
        self.assertEqual(json_objects(json.dumps({'response':'not JSON'})),[])
        self.assertEqual(json_objects(json.dumps({'response':reply,'other':'unexpected'})),[])

    def test_google_sign_in_rejection_is_distinct_from_pending_login(self):
        for text in ('This browser or app may not be secure.', 'Couldn’t sign you in', "Couldn't sign you in"):
            problem=page_problem('https://accounts.google.com/signin',text)
            self.assertEqual(problem.code,'login_rejected')
            self.assertFalse(problem.retry_safe)
            self.assertIn('saved narration and queue progress are retained',str(problem))
        self.assertEqual(page_problem('https://accounts.google.com/signin','Email or phone').code,'login')
