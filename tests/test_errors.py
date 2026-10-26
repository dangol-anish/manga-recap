import errno,json,os,subprocess,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from fastapi import FastAPI,HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel,Field
from app import errors,storage
from app.main import local_requests

class ErrorTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='.test-errors-',dir=Path(__file__).resolve().parents[1]);self.root=Path(self.temp.name)
        self.patches=[patch.object(storage,'ROOT',self.root),patch.object(storage,'DATA',self.root/'data'),patch.object(storage,'OUTPUTS',self.root/'outputs')]
        for p in self.patches:p.start()
        with errors._lock:errors._recent.clear();errors._warnings.clear()
    def tearDown(self):
        with errors._lock:errors._recent.clear();errors._warnings.clear()
        for p in reversed(self.patches):p.stop()
        self.temp.cleanup()
    def client(self):
        app=FastAPI();errors.install(app);app.middleware('http')(local_requests)
        return app,TestClient(app)
    def test_disk_full_has_specific_action_and_never_writes_secrets(self):
        key='AIza'+'k'*35;(self.root/'.env').write_text('GEMINI_API_KEY='+key,encoding='utf-8')
        value=errors.describe(OSError(errno.ENOSPC,'No space left '+key),'cinema','Create video','Chapter 4')
        self.assertEqual(value['code'],'disk_full');self.assertIn('Free space',value['action']);self.assertIn('Chapter 4',value['message'])
        data=''.join(p.read_text(encoding='utf-8') for p in (storage.DATA/'errors').glob('*'))
        self.assertNotIn(key,data);self.assertIn('technical_details',data)
    def test_windows_file_lock_differs_from_permission_denied(self):
        locked=PermissionError(errno.EACCES,'Cannot replace file');locked.winerror=32
        self.assertEqual(errors.describe(locked,'renumbering')['code'],'file_in_use')
        self.assertEqual(errors.describe(PermissionError(errno.EACCES,'denied'),'renumbering')['code'],'permission_denied')
    def test_corrupt_json_and_invalid_encoding_name_file_without_modifying_it(self):
        path=storage.DATA/'manga'/'story.json';path.parent.mkdir(parents=True)
        for raw in (b'{oops',b'\xff'):
            path.write_bytes(raw)
            with self.assertRaises(errors.SavedDataError) as caught:storage.read_json(path)
            value=errors.describe(caught.exception,'story','Load narration')
            self.assertEqual(value['code'],'saved_data_corrupt');self.assertIn('data/manga/story.json',value['message']);self.assertEqual(path.read_bytes(),raw)
    def test_corrupt_job_does_not_block_recovery_of_other_jobs(self):
        from app import speech_stage6 as speech
        directory=storage.DATA/'speech/jobs';directory.mkdir(parents=True)
        bad=directory/('b'*32+'.json');bad.write_text('{bad',encoding='utf-8')
        good=directory/('a'*32+'.json');storage.write_json(good,{'id':'a'*32,'status':'running','created_at':1})
        speech.recover()
        self.assertEqual(storage.read_json(good)['status'],'paused');self.assertEqual(bad.read_text(),'{bad');self.assertTrue(errors.latest_warning())
        jobs=speech.jobs();self.assertEqual(len(jobs),1)
    def test_request_failure_in_every_current_stage_is_actionable(self):
        app,client=self.client()
        def failed():raise FileNotFoundError(errno.ENOENT,'Required input is missing','missing-page.png')
        for stage in ('downloader','renumbering','detection','preparation','story','speech','grouped-audio','cinema','stitch','shorts'):
            app.add_api_route('/api/'+stage+'/failure',failed,methods=['GET'])
            response=client.get('/api/'+stage+'/failure');self.assertGreaterEqual(response.status_code,400)
            item=response.json()['error'];self.assertEqual(item['stage'],stage);self.assertEqual(item['code'],'file_missing')
            self.assertIn('missing-page.png',item['message']);self.assertTrue(item['action']);self.assertTrue(item['saved_work']);self.assertEqual(len(item['reference']),12)
    def test_wrapped_http_error_retains_original_disk_cause(self):
        app,client=self.client()
        @app.post('/api/cinema/export')
        def fail():
            try:raise OSError(errno.ENOSPC,'No space left')
            except OSError as exc:raise HTTPException(400,str(exc))
        response=client.post('/api/cinema/export');self.assertEqual(response.status_code,507)
        self.assertEqual(response.json()['error']['code'],'disk_full')
    def test_invalid_request_names_fields_without_echoing_input(self):
        app,client=self.client()
        class Choice(BaseModel):chapters:list[str]=Field(min_length=1)
        @app.post('/api/speech/start')
        def start(body:Choice):raise AssertionError('Must not start')
        response=client.post('/api/speech/start',json={'chapters':[]})
        self.assertEqual(response.status_code,422);self.assertIn('body.chapters',response.json()['detail']);self.assertEqual(response.json()['error']['code'],'invalid_input')
    def test_unexpected_programming_error_has_reference_and_diagnostic(self):
        app,client=self.client()
        @app.get('/api/story/test')
        def fail():raise KeyError('beats')
        value=client.get('/api/story/test').json()['error'];self.assertEqual(value['code'],'unexpected_data');self.assertIn('beats',value['reason'])
        diagnostic=client.get('/api/errors/'+value['reference']);self.assertEqual(diagnostic.status_code,200);self.assertIn('KeyError',diagnostic.json()['technical_details'])
    def test_failed_final_checkpoint_releases_worker_and_warns_browser(self):
        from app import speech_stage6 as speech
        job={'id':'c'*32,'manga_id':'m','chapter':'Chapter 1','source_revision':'r','numbers':[],'completed_numbers':[],'status':'running'}
        speech.active=job
        with patch.object(speech,'source',return_value={'revision':'r','beats':[]}),patch.object(speech,'status',return_value={'full':{'available':False}}),patch.object(speech,'save_job',side_effect=OSError(errno.ENOSPC,'No space left')):
            speech.run(job)
        self.assertIsNone(speech.active);self.assertTrue(errors.latest_warning())
        app,client=self.client()
        @app.get('/api/speech/test')
        def status():return {'ok':True}
        response=client.get('/api/speech/test');ref=response.headers['X-App-Warning'];self.assertEqual(client.get('/api/errors/'+ref).json()['code'],'disk_full')
    def test_diagnostic_logging_failure_does_not_hide_original_error(self):
        with patch.object(Path,'mkdir',side_effect=PermissionError('diagnostic folder denied')):
            result=errors.warning(OSError(errno.ENOSPC,'No space'),'speech','Save task progress')
        self.assertEqual(result['code'],'disk_full');self.assertIn(result['reference'],errors._recent)
    def test_tool_exit_includes_stderr_and_timeout_has_recovery(self):
        result=subprocess.CompletedProcess(['ffmpeg'],1,stderr=b'Unknown encoder: unavailable_codec')
        with self.assertRaises(ValueError) as caught:errors.checked_process(result,'Create video','input.mp3')
        value=errors.describe(caught.exception,'cinema');self.assertEqual(value['code'],'external_tool_failed');self.assertIn('unavailable_codec',value['reason'])
        value=errors.describe(subprocess.TimeoutExpired(['ffprobe'],30),'stitch');self.assertEqual(value['code'],'tool_timeout');self.assertIn('ffprobe',value['reason'])
    def test_warning_deduplicates_and_keeps_original_credential_out(self):
        error=ValueError('bad token AQ.'+'X'*35)
        a=errors.warning(error,'startup','Recover saved task','broken.json');b=errors.warning(error,'startup','Recover saved task','broken.json')
        self.assertEqual(a['reference'],b['reference']);self.assertNotIn('X'*35,json.dumps(a))
    def test_provider_free_tier_error_is_explicit_and_rotatable(self):
        from app import grouped_audio as audio
        exc=audio.classify(403,{'error':{'message':'The Gemini API free tier is not available for this project.'}})
        self.assertEqual(exc.code,'free_tier_unavailable');self.assertIn('Billing was not enabled',str(exc))
