"""Queue dismissal uses temporary files only; no browser or model calls."""
import copy
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app import browser_narration as q, storage


class ClearTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.patch=patch.object(storage,'DATA',Path(self.temp.name)/'data');self.patch.start()
        self.old=(q.active,q.latest,q.worker,q.checkpoint_failed)
        q.active=q.latest=q.worker=None;q.checkpoint_failed=False;q.cancel.clear();q.resume_signal.clear()
        self.job={'schema_version':1,'id':'a'*32,'manga_id':'b'*32,'items':[], 'cursor':0,'phase':'ready','status':'failed','expected_revision':'c'*64,'prompt_revision':'d'*64}
        q.save(self.job);storage.write_json(q.directory()/'current.json',self.job['id'])
        self.assets=[storage.DATA/'narration.json',storage.DATA/'memory.json',q.directory()/'replies'/'raw.txt']
        for path in self.assets:path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'Synthetic saved work')
        app=FastAPI();app.include_router(q.router);self.client=TestClient(app)

    def tearDown(self):
        q.cancel.set();q.resume_signal.set()
        if q.worker and isinstance(q.worker,threading.Thread):q.worker.join(4)
        q.active,q.latest,q.worker,q.checkpoint_failed=self.old
        q.cancel.clear();q.resume_signal.clear();self.patch.stop();self.temp.cleanup()

    def test_clear_failed_completed_and_paused_keeps_all_files_and_stays_cleared(self):
        for status in ('failed','complete','paused'):
            self.job['status']=status;q.save(self.job);storage.write_json(q.directory()/'current.json',self.job['id'])
            before={p:p.read_bytes() for p in [*self.assets,q.job_path(self.job['id'])]}
            response=self.client.post(f"/api/browser-narration/{self.job['id']}/clear")
            self.assertEqual(response.status_code,200,response.text);self.assertTrue(response.json()['cleared'])
            self.assertIsNone(q.current());q.recover();self.assertIsNone(q.current())
            self.assertIsNone(self.client.get('/api/browser-narration').json())
            self.assertEqual(before,{p:p.read_bytes() for p in before})

    def test_reject_running_stopping_and_wrong_identity(self):
        for status in ('running','stopping'):
            self.job['status']=status;q.save(self.job)
            with self.assertRaises(FileExistsError):q.clear_queue(self.job['id'])
            self.assertEqual(q.current()['status'],status)
        with self.assertRaises(ValueError):q.clear_queue('e'*32)

    def test_pointer_write_failure_does_not_hide_queue(self):
        before=q.current()
        with patch.object(storage,'write_json',side_effect=OSError('Synthetic disk failure')):
            with self.assertRaises(OSError):q.clear_queue(self.job['id'])
        self.assertEqual(q.current(),before)

    def test_paused_worker_finishes_before_pointer_is_cleared(self):
        self.job['status']='paused';q.save(self.job);q.active=self.job
        def worker():
            q.resume_signal.wait(2)
            with q.lock:q.save(self.job);q.active=None
        q.worker=threading.Thread(target=worker);q.worker.start()
        q.clear_queue(self.job['id'])
        self.assertFalse(q.worker.is_alive());self.assertIsNone(q.current())
        self.assertTrue(q.job_path(self.job['id']).exists())

    def test_busy_worker_cannot_be_detached(self):
        class Busy:
            def is_alive(self):return True
            def join(self,timeout):pass
        q.worker=Busy()
        with self.assertRaises(FileExistsError):q.clear_queue(self.job['id'])
        self.assertIsNotNone(q.current())


if __name__=='__main__':unittest.main()
