"""Bounded caches for read-only status requests; mutations always hash afresh."""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from functools import lru_cache, wraps
from hashlib import sha256
from pathlib import Path
import sys

_snapshot = ContextVar('read_snapshot', default=None)


@contextmanager
def read_snapshot():
    """Share chapter checks inside one GET; never share mutable status objects."""
    if _snapshot.get() is not None:
        yield
        return
    token = _snapshot.set({})
    try:
        yield
    finally:
        _snapshot.reset(token)


def memoized_read(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        cache = _snapshot.get()
        if cache is None:
            return function(*args, **kwargs)
        key = (function, args, tuple(sorted(kwargs.items())))
        if key not in cache:
            cache[key] = function(*args, **kwargs)
        return deepcopy(cache[key])
    return wrapped


def file_stamp(path):
    stat = path.stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def _hash_file(path):
    digest = sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


@lru_cache(maxsize=2048)
def _cached_hash(path, stamp):
    value = _hash_file(path)
    if file_stamp(path) != stamp:
        raise FileExistsError('A source file changed during validation. Refresh and retry.')
    return value


def file_hash(path):
    path = Path(path)
    # Windows ctime can be a creation timestamp rather than a metadata-change
    # timestamp. Keep its hashes request-local via chapter memoization instead
    # of trusting stat signatures across requests on that platform.
    if _snapshot.get() is None or sys.platform == 'win32':
        stamp = file_stamp(path)
        value = _hash_file(path)
        if file_stamp(path) != stamp:
            raise FileExistsError('A source file changed during validation. Refresh and retry.')
        return value
    stamp = file_stamp(path)
    value = _cached_hash(path, stamp)
    if file_stamp(path) != stamp:
        raise FileExistsError('A source file changed during validation. Refresh and retry.')
    return value
