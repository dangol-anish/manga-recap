import os
import unittest
from unittest.mock import patch

from app.detector import cpu_threads


class CpuBudgetTests(unittest.TestCase):
    def test_windows_budget_and_small_machines(self):
        with patch.dict(os.environ, {}, clear=True), patch('app.detector.sys.platform', 'win32'):
            for available, expected in ((32, 12), (16, 12), (8, 6), (2, 2), (1, 1), (None, 2)):
                with self.subTest(available=available), patch('app.detector.os.cpu_count', return_value=available):
                    self.assertEqual(cpu_threads(), expected)

    def test_mac_unchanged(self):
        with patch.dict(os.environ, {}, clear=True), patch('app.detector.sys.platform', 'darwin'), patch('app.detector.os.cpu_count', return_value=16):
            self.assertEqual(cpu_threads(), 2)

    def test_explicit_budget_and_invalid_values(self):
        with patch('app.detector.os.cpu_count', return_value=16):
            for value, expected in (('2', 2), ('4', 4), ('100', 16)):
                with patch.dict(os.environ, {'MAGI_CPU_THREADS': value}):
                    self.assertEqual(cpu_threads(), expected)
            for value in ('0', '-1', 'fast'):
                with patch.dict(os.environ, {'MAGI_CPU_THREADS': value}), self.assertRaises(ValueError):
                    cpu_threads()
