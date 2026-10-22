from pathlib import Path
import os
import tempfile
import unittest
from unittest.mock import patch

from app import read_cache


class ReadCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)/'page.png'
        self.path.write_bytes(b'first content')
        read_cache._cached_hash.cache_clear()

    def tearDown(self):
        read_cache._cached_hash.cache_clear();self.tmp.cleanup()

    def test_get_reuses_hash_but_mutation_always_reads_content(self):
        with patch.object(read_cache,'_hash_file',wraps=read_cache._hash_file) as reads:
            with read_cache.read_snapshot():first=read_cache.file_hash(self.path)
            with read_cache.read_snapshot():self.assertEqual(read_cache.file_hash(self.path),first)
            self.assertEqual(reads.call_count,1)
            read_cache.file_hash(self.path);read_cache.file_hash(self.path)
            self.assertEqual(reads.call_count,3)

    def test_same_size_edit_with_restored_mtime_invalidates_hash(self):
        with read_cache.read_snapshot():first=read_cache.file_hash(self.path)
        stat=self.path.stat()
        self.path.write_bytes(b'other content')
        os.utime(self.path,ns=(stat.st_atime_ns,stat.st_mtime_ns))
        with read_cache.read_snapshot():self.assertNotEqual(read_cache.file_hash(self.path),first)

    def test_atomic_replacement_and_mid_read_change_are_detected(self):
        with read_cache.read_snapshot():first=read_cache.file_hash(self.path)
        replacement=self.path.with_suffix('.new');replacement.write_bytes(b'newer content')
        replacement.replace(self.path)
        with read_cache.read_snapshot():self.assertNotEqual(read_cache.file_hash(self.path),first)
        def changed(path):
            path.write_bytes(b'changed');return 'unused'
        read_cache._cached_hash.cache_clear()
        with read_cache.read_snapshot(),patch.object(read_cache,'_hash_file',side_effect=changed):
            with self.assertRaises(FileExistsError):read_cache.file_hash(self.path)

    def test_request_memo_does_not_leak_mutations_or_survive_request(self):
        calls=[]
        @read_cache.memoized_read
        def value(key):calls.append(key);return [{'id':key}]
        with read_cache.read_snapshot():
            value('chapter')[0]['id']='modified'
            self.assertEqual(value('chapter'),[{'id':'chapter'}])
            self.assertEqual(calls,['chapter'])
        with read_cache.read_snapshot():value('chapter')
        value('chapter');self.assertEqual(len(calls),3)

    def test_windows_does_not_trust_creation_time_for_cross_request_hashes(self):
        with patch.object(read_cache.sys,'platform','win32'),patch.object(read_cache,'_hash_file',wraps=read_cache._hash_file) as reads:
            with read_cache.read_snapshot():read_cache.file_hash(self.path)
            with read_cache.read_snapshot():read_cache.file_hash(self.path)
            self.assertEqual(reads.call_count,2)
