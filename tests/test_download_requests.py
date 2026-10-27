import asyncio
from email.utils import formatdate
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx
from app import storage, download_requests as r, download_sources as s, errors


class Clock:
    def __init__(self):self.now=100000.;self.sleeps=[]
    async def sleep(self,seconds):
        self.sleeps.append(seconds);self.now+=seconds


class RequestLimitTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.p=patch.object(storage,'DATA',self.root);self.p.start()
        self.clock=Clock()
        self.t=patch.object(r.time,'time',lambda:self.clock.now);self.t.start()
        self.sleep=patch.object(r.asyncio,'sleep',self.clock.sleep);self.sleep.start()
    def tearDown(self):
        self.sleep.stop();self.t.stop();self.p.stop();self.tmp.cleanup()

    def test_requests_are_spaced_across_clients_and_saved_between_event_loops(self):
        async def request():await r.before('https://cdn.example/a','https://weebcentral.com/')
        asyncio.run(request());first=self.clock.now
        asyncio.run(r.before('https://weebcentral.com/chapters/a','https://weebcentral.com/'))
        self.assertGreaterEqual(self.clock.now-first,r.INTERVAL)
        self.assertEqual(set(r.read_state()['hosts']),{'weebcentral.com'})

    def test_numeric_date_invalid_and_skewed_retry_after(self):
        self.assertEqual(r.retry_delay({'Retry-After':'45'},1000),(45,True))
        self.assertEqual(r.retry_delay({'Retry-After':formatdate(1600,usegmt=True),'Date':formatdate(1000,usegmt=True)},999999),(600,True))
        self.assertEqual(r.retry_delay({'Retry-After':'garbage'},1000),(r.DEFAULT_COOLDOWN,False))
        self.assertEqual(r.retry_delay({'Retry-After':'NaN'},1000),(r.DEFAULT_COOLDOWN,False))

    def test_cooldown_survives_reload_and_blocks_catalog_and_cdn(self):
        exc=asyncio.run(r.limited('https://cdn.example/a','https://weebcentral.com/',{'Retry-After':'60'}))
        self.assertEqual(exc.reset,self.clock.now+60)
        for url in ['https://weebcentral.com/series/a','https://cdn.example/a']:
            with self.assertRaises(r.SourceCooldown):asyncio.run(r.before(url,'https://weebcentral.com/'))
        # An independent source does not inherit another source's restriction.
        asyncio.run(r.before('https://mangapill.com/manga/a','https://mangapill.com/'))

    def test_wait_honors_full_deadline_and_can_be_stopped(self):
        asyncio.run(r.limited('https://weebcentral.com/a','https://weebcentral.com/',{'Retry-After':'5'}))
        notices=[]
        async def waiting():
            with r.request_context(lambda:False,lambda *a:notices.append(a)):
                await r.before('https://cdn.example/a','https://weebcentral.com/')
        asyncio.run(waiting());self.assertGreaterEqual(self.clock.now,100005.)
        self.assertEqual(notices[0][1],100005.);self.assertIsNone(notices[-1][1])
        asyncio.run(r.limited('https://weebcentral.com/a','https://weebcentral.com/',{'Retry-After':'99'}))
        async def stopped():
            with r.request_context(lambda:True,lambda *a:None):
                await r.before('https://weebcentral.com/a','https://weebcentral.com/')
        with self.assertRaises(r.DownloadStopped):asyncio.run(stopped())
        self.assertGreater(r.read_state()['hosts']['weebcentral.com']['until'],self.clock.now)

    def test_429_response_records_deadline_before_raising_and_never_retries_itself(self):
        calls=[]
        def serve(request):calls.append(request);return httpx.Response(429,headers={'Retry-After':'300'})
        async def request():
            async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as client:
                await s.fetch(client,'https://weebcentral.com/chapters/a/images?private=x','https://weebcentral.com/')
        with patch.object(s,'public_url',lambda u:u):
            with self.assertRaises(r.SourceCooldown) as caught:asyncio.run(request())
        self.assertEqual(len(calls),1);self.assertEqual(caught.exception.reset,100300.)
        detail=errors.describe(caught.exception,'downloader',log=False)
        self.assertEqual(detail['code'],'source_rate_limit');self.assertEqual(detail['reset_at'],100300.)
        self.assertNotIn('private=x',detail['message'])

    def test_unknown_reset_backoff_increases_and_query_redaction_is_idempotent(self):
        first=asyncio.run(r.limited('https://weebcentral.com/a','https://weebcentral.com/',{}))
        self.clock.now=first.reset
        second=asyncio.run(r.limited('https://weebcentral.com/a','https://weebcentral.com/',{}))
        self.assertGreater(second.reset-self.clock.now,r.DEFAULT_COOLDOWN)
        text=errors.clean('https://example.com/a?secret=x');self.assertEqual(errors.clean(text),text)

    def test_corrupt_limiter_state_is_not_silently_reset(self):
        p=r.state_path();p.parent.mkdir(parents=True);p.write_text('{broken')
        with self.assertRaises(ValueError):asyncio.run(r.before('https://weebcentral.com/a','https://weebcentral.com/'))
        self.assertEqual(p.read_text(),'{broken')
