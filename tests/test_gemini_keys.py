import base64
from datetime import datetime,timezone
from io import BytesIO
import json,os,tempfile,unittest,wave
from pathlib import Path
from unittest.mock import patch
import httpx
from app import grouped_audio as g,storage

class KeyRotationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1],prefix='.test-keys-');self.root=Path(self.temp.name)
        self.patches=[patch.object(storage,'ROOT',self.root),patch.object(storage,'DATA',self.root/'data'),patch.object(storage,'OUTPUTS',self.root/'outputs'),patch.dict(os.environ,{},clear=True)]
        for p in self.patches:p.start()
        self.keys=['fixture-first-secret','fixture-second-secret'];self.configure();g.cancel.clear()
        buffer=BytesIO()
        with wave.open(buffer,'wb') as out:out.setnchannels(1);out.setsampwidth(2);out.setframerate(24000);out.writeframes(b'\0\0'*24000)
        self.response=httpx.Response(200,json={'status':'completed','steps':[{'type':'model_output','content':[{'type':'audio','data':base64.b64encode(buffer.getvalue()).decode()}]}]})
    def tearDown(self):
        g.cancel.clear()
        for p in reversed(self.patches):p.stop()
        self.temp.cleanup()
    def configure(self):
        (self.root/'.env').write_text('\n'.join(f'GEMINI_API_KEY{ "_"+str(i+1) if i else ""}={key}' for i,key in enumerate(self.keys)),encoding='utf-8')
    def error(self,status,message,details=None):return httpx.Response(status,json={'error':{'message':message,'details':details or []}})
    def generate(self):return g.gemini('Hello there.',self.root/'returned.wav')
    def test_daily_failure_switches_and_persists_without_secrets(self):
        with patch.object(g.httpx,'post',side_effect=[self.error(429,'RequestsPerDay'),self.response]) as post:result=self.generate()
        self.assertEqual([c.kwargs['headers']['x-goog-api-key'] for c in post.call_args_list],self.keys)
        self.assertEqual(result['key_label'],'GEMINI_API_KEY_2');self.assertTrue((self.root/'returned.wav').exists())
        status=g.key_status();self.assertEqual(status['available'],1);self.assertIn('Daily',status['keys'][0]['reason'])
        for key in self.keys:self.assertNotIn(key,json.dumps(g.key_states()))
        self.assertEqual(g.choose_key(set())[1],self.keys[1])
    def test_minute_cooldown_obeys_retry_delay_and_expires(self):
        with patch.object(g.time,'time',return_value=100),patch.object(g.httpx,'post',side_effect=[self.error(429,'tokens per minute',[{'retryDelay':'120s'}]),self.response]):self.generate()
        self.assertEqual(g.key_states()[g.key_token(self.keys[0])]['until'],220)
        with patch.object(g.time,'time',return_value=200):self.assertEqual(g.choose_key(set())[1],self.keys[1])
        with patch.object(g.time,'time',return_value=221):self.assertEqual(g.choose_key(set())[1],self.keys[0])
    def test_all_daily_keys_exhausted_pause_with_reset(self):
        with patch.object(g.httpx,'post',return_value=self.error(429,'daily quota')) as post:
            with self.assertRaises(g.AudioError) as error:self.generate()
        self.assertEqual(post.call_count,2);self.assertEqual(error.exception.code,'all_keys_unavailable');self.assertTrue(error.exception.reset)
        self.assertEqual(g.key_status()['available'],0);self.assertFalse((self.root/'returned.wav').exists())
    def test_local_budget_skips_key_before_sending(self):
        for _ in range(10):g.reserve(self.keys[0])
        with patch.object(g.httpx,'post',return_value=self.response) as post:self.generate()
        self.assertEqual(post.call_count,1);self.assertEqual(post.call_args.kwargs['headers']['x-goog-api-key'],self.keys[1])
    def test_rejected_key_is_disabled_but_other_key_works(self):
        with patch.object(g.httpx,'post',side_effect=[self.error(400,'API key not valid'),self.response]):self.generate()
        self.assertTrue(g.key_states()[g.key_token(self.keys[0])]['blocked'])
        self.assertEqual(g.key_status()['available'],1)
    def test_timeout_never_retries_with_another_key(self):
        with patch.object(g.httpx,'post',side_effect=httpx.ReadTimeout('ambiguous')) as post:
            with self.assertRaises(g.AudioError) as error:self.generate()
        self.assertEqual(post.call_count,1);self.assertEqual(error.exception.code,'network_timeout');self.assertEqual(g.key_status()['available'],2)
    def test_server_error_does_not_duplicate_request(self):
        with patch.object(g.httpx,'post',return_value=self.error(503,'busy')) as post:
            with self.assertRaises(g.AudioError) as error:self.generate()
        self.assertEqual(post.call_count,1);self.assertEqual(error.exception.code,'google_unavailable')
    def test_numbered_only_keys_duplicates_and_environment_override(self):
        (self.root/'.env').write_text('GEMINI_API_KEY_10=ten\nGEMINI_API_KEY_2=two\nGEMINI_API_KEY_3=two\n',encoding='utf-8')
        with patch.dict(os.environ,{'GEMINI_API_KEY_10':'override'}):
            self.assertEqual(g.credentials(),[('GEMINI_API_KEY_2','two'),('GEMINI_API_KEY_10','override')]);self.assertEqual(g.api_key(),'two')
    def test_stop_blocks_request_before_rotation(self):
        g.cancel.set()
        with patch.object(g.httpx,'post') as post:
            with self.assertRaises(InterruptedError):self.generate()
        post.assert_not_called()
    def test_new_key_can_be_added_after_exhaustion(self):
        for key in self.keys:
            g.pause_key(key,g.AudioError('daily_quota','Daily',g.pacific_clock()[1]),{})
        with self.assertRaises(g.AudioError):g.choose_key(set())
        self.keys.append('fixture-new-secret');self.configure()
        self.assertEqual(g.choose_key(set())[1],self.keys[-1])
