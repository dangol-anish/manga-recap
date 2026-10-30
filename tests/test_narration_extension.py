"""Loopback pairing and one-delivery transport; no Google/browser/media calls."""
import threading
import time
import unittest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app import narration_extension as bridge
from app.studio_browser import Attention


class ExtensionTests(unittest.TestCase):
    def setUp(self):
        bridge.connection=bridge.pending=bridge.pair_code=None
        app=FastAPI();app.include_router(bridge.router)
        self.client=TestClient(app)
        self.headers={'X-Narration-Extension':'a'*32,'Origin':'chrome-extension://'+'a'*32}

    def tearDown(self):
        bridge.connection=bridge.pending=bridge.pair_code=None

    def pair(self):
        code=self.client.post('/api/narration-extension/code').json()['code']
        response=self.client.post('/api/narration-extension/pair',json={'code':code},headers=self.headers)
        self.assertEqual(response.status_code,200,response.text)
        self.headers['Authorization']='Bearer '+response.json()['token']
        return code

    def test_pairing_is_one_time_origin_bound_and_secret_free_status(self):
        code=self.pair()
        self.assertEqual(self.client.post('/api/narration-extension/pair',json={'code':code},headers=self.headers).status_code,403)
        wrong={**self.headers,'Origin':'https://evil.invalid'}
        self.assertEqual(self.client.post('/api/narration-extension/poll',json={},headers=wrong).status_code,403)
        wrong={**self.headers,'X-Narration-Extension':'b'*32,'Origin':'chrome-extension://'+'b'*32}
        self.assertEqual(self.client.post('/api/narration-extension/poll',json={},headers=wrong).status_code,403)
        status=self.client.get('/api/narration-extension/status').json()
        self.assertEqual(status,{'connected':True,'paired':True})
        self.assertNotIn('token',str(status))

    def test_web_origins_cannot_get_pair_codes_or_control_bridge(self):
        for path in ('code','disconnect','open-folder'):
            self.assertEqual(self.client.post('/api/narration-extension/'+path,headers={'Origin':'https://evil.invalid'}).status_code,403)
        self.assertEqual(self.client.post('/api/narration-extension/code',headers={'Host':'evil.invalid'}).status_code,403)
        self.assertEqual(self.client.post('/api/narration-extension/pair',json={'code':'x'},headers={'Origin':'https://evil.invalid','X-Narration-Extension':'a'*32}).status_code,403)

    def test_expired_pairing_and_server_restart_require_new_code(self):
        code=self.client.post('/api/narration-extension/code').json()['code'];bridge.pair_deadline=0
        self.assertEqual(self.client.post('/api/narration-extension/pair',json={'code':code},headers=self.headers).status_code,403)
        self.pair();bridge.connection=None
        self.assertEqual(self.client.post('/api/narration-extension/poll',json={},headers=self.headers).status_code,403)

    def test_command_delivered_once_and_duplicate_ack_is_idempotent(self):
        self.pair();out=[]
        thread=threading.Thread(target=lambda:out.append(bridge.command('snapshot',timeout=2)));thread.start()
        for _ in range(50):
            if bridge.pending:break
            time.sleep(.01)
        command=self.client.post('/api/narration-extension/poll',json={},headers=self.headers).json()['command']
        self.assertEqual(command['action'],'snapshot');self.assertIn('expiresAt',command)
        self.assertIsNone(self.client.post('/api/narration-extension/poll',json={},headers=self.headers).json()['command'])
        body={'id':command['id'],'value':{'url':'https://aistudio.google.com/prompts/test','text':'Fixture'}}
        self.assertEqual(self.client.post('/api/narration-extension/result',json=body,headers=self.headers).status_code,200)
        # Once consumed, a late ack can be rejected, but it can never execute again.
        thread.join(3);self.assertEqual(out,[body['value']]);self.assertIsNone(bridge.pending)
        self.assertEqual(self.client.post('/api/narration-extension/result',json=body,headers=self.headers).status_code,409)

    def test_timeout_or_disconnect_never_marks_snapshot_safe_to_resend(self):
        with self.assertRaises(Attention) as error:bridge.command('snapshot',timeout=.01)
        self.assertFalse(error.exception.retry_safe)
        self.pair()
        with self.assertRaises(Attention) as error:bridge.command('send',timeout=.01)
        self.assertFalse(error.exception.retry_safe);self.assertIsNone(bridge.pending)
        self.assertIsNone(self.client.post('/api/narration-extension/poll',json={},headers=self.headers).json()['command'])

    def test_stopped_or_expired_submission_has_no_permit(self):
        from app import browser_narration as queue
        self.pair();ident='f'*32
        bridge.pending={'id':ident,'action':'send','claimed':True,'expiresAt':time.time()*1000+5000}
        queue.cancel.set()
        try:self.assertEqual(self.client.post('/api/narration-extension/permit',json={'id':ident},headers=self.headers).status_code,409)
        finally:queue.cancel.clear()
        bridge.pending['expiresAt']=0
        self.assertEqual(self.client.post('/api/narration-extension/permit',json={'id':ident},headers=self.headers).status_code,409)

    def test_bad_acknowledgement_does_not_complete_a_command(self):
        self.pair()
        self.assertEqual(self.client.post('/api/narration-extension/result',content=b'{broken',headers={**self.headers,'Content-Type':'application/json'}).status_code,400)
        self.assertIsNone(bridge.pending)

    def test_authenticated_progress_and_heartbeat_do_not_deliver_or_submit_commands(self):
        self.pair();ident='f'*32
        bridge.pending={'id':ident,'action':'prepare','claimed':True}
        response=self.client.post('/api/narration-extension/progress',json={'id':ident,'step':'waiting_upload'},headers=self.headers)
        self.assertEqual(response.status_code,200);self.assertEqual(bridge.pending['progress'],'waiting_upload')
        self.assertNotIn('result',bridge.pending)
        self.assertEqual(self.client.post('/api/narration-extension/progress',json={'id':ident,'step':'arbitrary-step'},headers=self.headers).status_code,400)
        bridge.connection['seen']=0
        self.assertFalse(self.client.get('/api/narration-extension/status').json()['connected'])
        self.assertEqual(self.client.post('/api/narration-extension/heartbeat',json={},headers=self.headers).status_code,200)
        self.assertTrue(self.client.get('/api/narration-extension/status').json()['connected'])
        self.assertTrue(bridge.pending['claimed'])
        wrong={**self.headers,'Origin':'https://evil.invalid'}
        self.assertEqual(self.client.post('/api/narration-extension/heartbeat',json={},headers=wrong).status_code,403)

    def test_full_app_middleware_accepts_only_scoped_extension_transport(self):
        from app.main import app
        from app import maintenance
        from unittest.mock import patch
        client=TestClient(app)
        with patch.object(maintenance,'active',None),patch.object(maintenance,'recovery_required',False):
            code=client.post('/api/narration-extension/code').json()['code']
            response=client.post('/api/narration-extension/pair',json={'code':code},headers=self.headers)
            self.assertEqual(response.status_code,200,response.text)
            self.headers['Authorization']='Bearer '+response.json()['token']
            response=client.post('/api/narration-extension/poll',json={},headers=self.headers)
            self.assertEqual(response.status_code,200,response.text)
            for path in ('/api/narration-extension/code','/api/narration-extension/disconnect','/api/browser-narration'):
                self.assertEqual(client.post(path,json={},headers=self.headers).status_code,403)
            forged={**self.headers,'Origin':'https://evil.invalid'}
            self.assertEqual(client.post('/api/narration-extension/poll',json={},headers=forged).status_code,403)


if __name__=='__main__':unittest.main()
