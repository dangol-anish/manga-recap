"""Rendered Stage 5 feedback with intercepted fixture APIs; no live app or Google."""
import json
import os
from pathlib import Path
import unittest


class FeedbackBrowserTests(unittest.TestCase):
    def test_actions_progress_warnings_and_responsive_toasts(self):
        try:from playwright.sync_api import sync_playwright
        except ImportError:self.skipTest('Optional development browser package is unavailable.')
        from scripts.check_ui import fixture
        root=Path(__file__).resolve().parents[1];static=root/'app/static'
        os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH',str(root/'.cache/ui-browsers'))
        html=fixture('story.html').decode().replace('</body>','<script src="/static/narration_feedback.js"></script><script src="/static/browser_narration.js"></script></body>')
        job=None;calls=[];catalog_failure=False;clear_failure=True
        with sync_playwright() as runtime:
            if not Path(runtime.chromium.executable_path).exists():self.skipTest('Development Chromium unavailable; no download attempted.')
            browser=runtime.chromium.launch(headless=True)
            try:
                context=browser.new_context(viewport={'width':1280,'height':900})
                def route(route):
                    nonlocal job
                    request=route.request;url=request.url;path=url.split('19742',1)[-1].split('?',1)[0]
                    if not url.startswith('http://127.0.0.1:19742/'):
                        route.abort();return
                    calls.append((request.method,path))
                    if path=='/fixture/story.html':route.fulfill(body=html,content_type='text/html');return
                    if path.startswith('/static/'):
                        asset=static/Path(path).name
                        if asset.suffix not in {'.js','.css'}:route.abort();return
                        route.fulfill(body=asset.read_bytes(),content_type='text/javascript' if asset.suffix=='.js' else 'text/css');return
                    value={};status=200
                    if path=='/api/story/catalog':
                        if catalog_failure:status=503;value={'detail':'Synthetic catalog unavailable'}
                        else:value=[{'id':'fixture','name':'Fixture manga','chapters':[{'name':'1','ready':True},{'name':'2','ready':False}]}]
                    elif path=='/api/narration-extension/status':value={'paired':True,'connected':True}
                    elif path=='/api/browser-narration':
                        if request.method=='POST':
                            job={'id':'f'*32,'status':'running','phase':'awaiting_reply','cursor':0,'elapsed_seconds':240,'activity_elapsed_seconds':185,
                                 'activity':{'step':'generating','label':'Waiting for Gemini to finish'},'browser_connection':{'paired':True,'connected':True},
                                 'items':[{'chapter':'1','batch_id':'batch-001','status':'running'}],'message':'Synthetic fixture only'}
                        value=job
                    elif path.endswith('/clear'):
                        if clear_failure:status=503;value={'detail':'Synthetic clear unavailable; queue retained'}
                        else:job=None;value={'cleared':True,'message':'Queue cleared. Saved narration retained.'}
                    elif path.endswith('/stop'):job={**job,'status':'paused','attention':'stopped'};value=job
                    route.fulfill(status=status,body=json.dumps(value),content_type='application/json')
                context.route('**/*',route)
                page=context.new_page();page.goto('http://127.0.0.1:19742/fixture/story.html')
                page.locator('#browser-narration-panel').evaluate('(e)=>e.open=true')
                page.locator('#browser-manga').select_option('fixture')
                page.locator('#browser-chapters input').first.check()
                page.locator('#browser-start').click()
                page.locator('#browser-activity').get_by_text('Chapter 1 · Batch 1 · Waiting for Gemini to finish').wait_for()
                self.assertIn('Recorded active time: 4m',page.locator('#browser-elapsed').inner_text())
                self.assertIn('longer than usual',page.locator('#browser-stall-warning').inner_text())
                self.assertTrue(page.locator('#browser-stop').is_enabled());self.assertTrue(page.locator('#browser-start').is_disabled())
                self.assertEqual(calls.count(('POST','/api/browser-narration')),1)
                self.assertTrue(page.locator('#browser-clear').is_hidden())
                page.locator('#browser-stop').click()
                page.locator('#browser-activity').get_by_text('Paused — waiting for your action',exact=False).wait_for()
                self.assertTrue(page.locator('#browser-stall-warning').is_hidden())
                self.assertTrue(page.locator('#narration-toasts').get_by_text('Stop requested.',exact=False).is_visible())
                catalog_failure=True;page.locator('#browser-catalog-refresh').click()
                page.locator('#narration-toasts [data-kind="error"]').get_by_text('Synthetic catalog unavailable').wait_for()
                self.assertTrue(page.locator('#browser-chapters input').first.is_checked())
                self.assertTrue(page.locator('#browser-catalog-refresh').is_enabled())
                page.locator('#browser-clear').click()
                page.locator('#narration-toasts [data-kind="error"]').get_by_text('Synthetic clear unavailable; queue retained').wait_for()
                self.assertTrue(page.locator('#browser-job').is_visible())
                self.assertTrue(page.locator('#browser-clear').is_enabled())
                clear_failure=False;job={**job,'status':'complete'}
                page.locator('#browser-job-refresh').click()
                page.locator('#browser-activity').get_by_text('All selected batches saved',exact=False).wait_for()
                page.locator('#browser-clear').click()
                page.locator('#browser-job').wait_for(state='hidden')
                page.locator('#narration-toasts').get_by_text('Queue cleared. Saved narration retained.').wait_for()
                self.assertTrue(page.locator('#browser-chapters input').first.is_checked())
                self.assertTrue(page.locator('#browser-start').is_enabled())
                page.reload();page.locator('#browser-job').wait_for(state='hidden')
                for width in (1280,390):
                    page.set_viewport_size({'width':width,'height':900})
                    page.evaluate("()=>{for(let i=0;i<5;i++)window.NarrationFeedback.show('Synthetic long message '.repeat(12),'error');}")
                    self.assertEqual(page.locator('.narration-toast').count(),3)
                    self.assertFalse(page.evaluate('document.documentElement.scrollWidth>innerWidth'))
                    box=page.locator('.narration-toasts').bounding_box();self.assertGreaterEqual(box['x'],0);self.assertLessEqual(box['x']+box['width'],width+1)
                self.assertEqual(calls.count(('POST','/api/browser-narration')),1,'Feedback must not resubmit work')
                self.assertEqual(sum(method=='POST' for method,path in calls),4,'Only explicit Start, Stop and two Clear clicks may write')
                context.close()
            finally:browser.close()


if __name__=='__main__':unittest.main()
