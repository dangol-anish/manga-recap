from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app import panel_preparation as panels


def windows_lock():
    error = PermissionError('Synthetic Windows lock')
    error.winerror = 5
    return error


class PublicationTests(unittest.TestCase):
    def test_temporary_lock_retries_only_publication(self):
        with tempfile.TemporaryDirectory() as folder:
            staging, version = Path(folder)/'staging', Path(folder)/'version'
            staging.mkdir(); (staging/'panel.png').write_bytes(b'original fixture')
            rename = panels.os.rename
            calls = []
            def locked_once(source, target):
                calls.append((source, target))
                if len(calls) == 1: raise windows_lock()
                return rename(source, target)
            with patch.object(panels.os, 'rename', side_effect=locked_once), patch.object(panels.time, 'sleep') as sleep:
                panels.publish_revision(staging, version)
            self.assertEqual(len(calls), 2);sleep.assert_called_once()
            self.assertEqual((version/'panel.png').read_bytes(), b'original fixture')

    def test_permanent_lock_is_bounded_and_preserves_staging(self):
        with tempfile.TemporaryDirectory() as folder:
            staging, version = Path(folder)/'staging', Path(folder)/'version'
            staging.mkdir()
            with patch.object(panels.os, 'rename', side_effect=windows_lock()) as rename, patch.object(panels.time, 'sleep') as sleep:
                with self.assertRaises(PermissionError):panels.publish_revision(staging, version)
            self.assertEqual(rename.call_count, 8);self.assertEqual(sleep.call_count, 7)
            self.assertTrue(staging.is_dir());self.assertFalse(version.exists())

    def test_existing_revision_is_never_replaced(self):
        with tempfile.TemporaryDirectory() as folder:
            staging, version = Path(folder)/'staging', Path(folder)/'version'
            staging.mkdir();version.mkdir();(version/'saved').write_bytes(b'keep')
            with patch.object(panels.os, 'rename') as rename:
                with self.assertRaises(FileExistsError):panels.publish_revision(staging, version)
            rename.assert_not_called();self.assertEqual((version/'saved').read_bytes(), b'keep')

    def test_other_permission_errors_are_not_retried(self):
        with patch.object(panels.os, 'rename', side_effect=PermissionError('Permanent permission denial')), patch.object(panels.time, 'sleep') as sleep:
            with self.assertRaises(PermissionError):panels.publish_revision(Path('unused-staging'),Path('unused-version'))
            sleep.assert_not_called()
