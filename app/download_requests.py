"""Shared, restart-safe source pacing. Never bypass a source's access limits."""
import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from email.utils import parsedate_to_datetime
import errno
import math
import threading
import time
from urllib.parse import urlsplit

from app import storage
from app.runtime import acquire_server_lock

INTERVAL = 2.0
DEFAULT_COOLDOWN = 120.0
_context = ContextVar('download_request_context', default=None)
_lock = threading.RLock()


class DownloadStopped(Exception):
    pass


class SourceCooldown(ValueError):
    code = 'source_rate_limit'

    def __init__(self, host, until, reported=False):
        self.host, self.reset, self.reported = host, until, reported
        super().__init__(f'{host} is temporarily limiting downloads. The shared cooldown ends at ' +
                         time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime(until)) +
                         ('. This waiting time came from the source.' if reported else '. The source did not give a usable waiting time; the app is using a conservative cooldown.'))
        self.recovery = 'Wait for the displayed cooldown. Running downloads retry automatically up to twice; if paused, resume the original job afterward. Verified images are retained.'


@contextmanager
def request_context(stopped, waiting):
    token = _context.set((stopped, waiting))
    try:
        yield
    finally:
        _context.reset(token)


def source_key(url, referer):
    host = (urlsplit(referer).hostname or '').lower().removeprefix('www.')
    if host not in ('weebcentral.com', 'mangapill.com'):
        host = (urlsplit(url).hostname or '').lower().removeprefix('www.')
    if not host or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789.-' for c in host):
        raise ValueError('Cannot pace an invalid source host.')
    return host


def state_path():
    return storage.DATA / 'downloader' / 'request-limits.json'


@contextmanager
def state_lock():
    """Brief OS lock shared by catalog, download and research processes."""
    path = state_path().with_suffix('.lock');path.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        try:
            handle = acquire_server_lock(path)
        except RuntimeError as exc:
            cause = exc.__cause__
            if getattr(cause, 'errno', None) in (errno.EACCES, errno.EAGAIN) or getattr(cause, 'winerror', None) in (33, 36):
                yield None
                return
            raise
        try:
            yield handle
        finally:
            handle.close()


def read_state():
    value = storage.read_json(state_path()) or {'hosts': {}}
    if not isinstance(value, dict) or not isinstance(value.get('hosts'), dict):
        raise ValueError('Saved download cooldown data is invalid. Keep request-limits.json for repair; do not reset downloaded chapters.')
    for host, entry in value['hosts'].items():
        if not isinstance(entry, dict) or any(not isinstance(entry.get(k, 0), (int, float)) or not math.isfinite(entry.get(k, 0)) or entry.get(k, 0)<0 for k in ('next_request', 'until', 'strikes', 'last_limit')):
            raise ValueError('Saved download cooldown values are invalid for '+str(host))
    return value


def retry_delay(headers, now):
    value = str(headers.get('Retry-After') or '').strip()
    try:
        seconds = float(value)
        if math.isfinite(seconds) and seconds >= 0:
            time.gmtime(now+seconds)  # Reject unrepresentable/malformed deadlines.
            return max(INTERVAL, seconds), True
    except (TypeError, ValueError, OverflowError, OSError):
        pass
    try:
        target = parsedate_to_datetime(value)
        if target.tzinfo is None:raise ValueError('Missing timezone')
        # A response Date avoids shortening the server's delay due to local
        # clock skew. Persist a local absolute deadline for restart recovery.
        origin = parsedate_to_datetime(headers['Date']).timestamp() if headers.get('Date') else now
        return max(INTERVAL, target.timestamp()-origin), True
    except (TypeError, ValueError, OverflowError, KeyError, AttributeError):
        return DEFAULT_COOLDOWN, False


async def before(url, referer):
    host = source_key(url, referer);context = _context.get();notified = False
    while True:
        if context and context[0]():raise DownloadStopped()
        with state_lock() as handle:
            if handle is None:
                delay = .1;entry = None
            else:
                state = read_state();entry = state['hosts'].setdefault(host, {})
                now = time.time();until = entry.get('until', 0)
                if until > now:
                    if not context:raise SourceCooldown(host, until, entry.get('reported', False))
                    if not notified:
                        context[1](host, until, entry.get('reported', False));notified = True
                    delay = until-now
                else:
                    delay = max(0, entry.get('next_request', 0)-now)
                    if delay <= 0:
                        entry['next_request'] = now+INTERVAL
                        storage.write_json(state_path(), state)
                        if notified:context[1](host, None, False)
                        return
        await asyncio.sleep(min(.25, delay))


async def limited(url, referer, headers):
    host = source_key(url, referer)
    while True:
        with state_lock() as handle:
            if handle is not None:
                state = read_state();entry = state['hosts'].setdefault(host, {});now = time.time()
                delay, reported = retry_delay(headers, now)
                strikes = int(entry.get('strikes', 0))+1 if now-entry.get('last_limit', 0)<3600 else 1
                if not reported:delay = min(1800, delay*2**min(strikes-1, 4))
                entry.update(until=max(entry.get('until', 0), now+delay), reported=reported,
                             strikes=strikes, last_limit=now)
                storage.write_json(state_path(), state)
                return SourceCooldown(host, entry['until'], reported)
        await asyncio.sleep(.1)
