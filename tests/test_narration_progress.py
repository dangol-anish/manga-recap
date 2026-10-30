"""Read-only progress projection and timing; no real jobs or browser calls."""
import copy
import unittest
from unittest.mock import patch
from app import browser_narration as queue


class ProgressTests(unittest.TestCase):
    def test_elapsed_projection_is_read_only_and_excludes_paused_time(self):
        job={'status':'running','run_started_at':100,'elapsed_seconds':8,'activity':{'step':'generating','started_at':105}}
        before=copy.deepcopy(job)
        view=queue.presentation(job,130)
        self.assertEqual(view['elapsed_seconds'],38);self.assertEqual(view['activity_elapsed_seconds'],25)
        self.assertEqual(job,before)
        queue.finish_timing(job,140);job['status']='paused'
        self.assertEqual(queue.presentation(job,1000)['elapsed_seconds'],48)

    def test_legacy_timing_is_explicitly_unavailable(self):
        view=queue.presentation({'status':'paused'},200)
        self.assertFalse(view['timing_available']);self.assertIsNone(view['activity_elapsed_seconds'])

    def test_unknown_or_nonworker_activity_cannot_write_progress(self):
        with patch.object(queue,'save',side_effect=AssertionError('No progress write allowed')):
            queue.report_activity('unrecognized')
            queue.report_activity('waiting_upload')


if __name__=='__main__':unittest.main()
