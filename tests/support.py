"""Fixtures that account for optional Windows symlink privileges."""
import unittest


def create_symlink(link, target, **kwargs):
    try:
        link.symlink_to(target, **kwargs)
    except OSError as exc:
        if getattr(exc, 'winerror', None) == 1314:
            raise unittest.SkipTest('Windows symlink privilege unavailable; enable Developer Mode to run this symlink fixture.') from exc
        raise
