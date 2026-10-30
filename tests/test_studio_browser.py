"""Exercise the visible-page adapter against a synthetic site; all network blocked."""
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from app.studio_browser import StudioBrowser, Attention, NEW_CHAT


class StudioPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise unittest.SkipTest('Optional Playwright package is not installed; no download attempted.')
        os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH',str(Path(__file__).resolve().parents[1]/'.cache/ui-browsers'))
        cls.runtime=sync_playwright().start()
        if not Path(cls.runtime.chromium.executable_path).exists():
            cls.runtime.stop()
            raise unittest.SkipTest('Optional Chromium is not installed; no download attempted.')
        try:cls.browser=cls.runtime.chromium.launch(headless=True)
        except Exception:
            cls.runtime.stop();raise

    @classmethod
    def tearDownClass(cls):
        cls.browser.close();cls.runtime.stop()

    def setUp(self):
        self.context=self.browser.new_context()
        self.page=self.context.new_page()
        self.adapter=StudioBrowser();self.adapter.page=self.page
        self.cancel=threading.Event()
        self.temp=tempfile.TemporaryDirectory()
        self.reply={'batch_receipt':{'batch_id':'batch-001'},'beats':[{'panel_ids':['p0000-1'],'narration':'Synthetic fixture.'}]}
        html='''<!doctype html><main>
<button>Gemini 3.8 Flash gemini-3.8-flash</button>
<div role="combobox" aria-label="Thinking Level">Medium</div>
<button role="switch" aria-label="Structured outputs" aria-checked="false" id="structured">Structured outputs</button>
<div id="editor" hidden><button role="tab">Code Editor</button><textarea aria-label="Enter a JSON schema"></textarea><button aria-label="Save the current schema" id="save">Save</button></div>
<textarea aria-label="Enter a prompt"></textarea><input type="file" multiple><button id="run">Run</button><section id="responses"></section>
</main><script>
document.querySelector('#structured').onclick=()=>{document.querySelector('#structured').setAttribute('aria-checked','true');document.querySelector('#editor').hidden=false;};
document.querySelector('#save').onclick=()=>document.querySelector('#editor').hidden=true;
document.querySelector('#run').onclick=()=>{const pre=document.createElement('pre');pre.textContent=REPLY;document.querySelector('#responses').append(pre);};
</script>'''.replace('REPLY',json.dumps(json.dumps(self.reply)))
        self.context.route('**/*',lambda route:route.fulfill(body=html,content_type='text/html'))

    def tearDown(self):self.context.close();self.temp.cleanup()

    def test_upload_json_settings_send_and_complete_reply(self):
        photos=[]
        for name in ('p0000.jpg','p0001.jpg'):
            path=Path(self.temp.name)/name;path.write_bytes(b'synthetic fixture, never sent to Google');photos.append(path)
        self.adapter.prepare('Synthetic prompt',photos,self.cancel)
        self.assertEqual(self.page.get_by_role('textbox',name='Enter a prompt').input_value(),'Synthetic prompt')
        self.assertEqual(self.page.locator('input[type=file]').evaluate('(e)=>e.files.length'),2)
        before=self.adapter.visible_replies();self.assertEqual(before,[])
        self.adapter.send()
        result=self.adapter.receive(before,self.cancel,lambda url:None,timeout=10)
        self.assertEqual(json.loads(result),self.reply)

    def test_settings_change_and_login_pause_never_submit(self):
        self.page.goto(NEW_CHAT)
        self.page.get_by_role('button',name='Gemini 3.8 Flash gemini-3.8-flash').evaluate('(e)=>e.textContent="Other model"')
        with self.assertRaises(Attention) as error:self.adapter.configure()
        self.assertEqual(error.exception.code,'settings')
        self.assertEqual(self.page.locator('#responses').inner_text(),'')
        self.page.goto('https://accounts.google.com/synthetic-login')
        with self.assertRaises(Attention) as error:self.adapter.check()
        self.assertEqual(error.exception.code,'login')

    def test_recovery_requires_a_saved_conversation_not_new_chat(self):
        for url in (NEW_CHAT,'https://evil.invalid/prompts/x','https://aistudio.google.com/prompts/x?token=private'):
            with self.assertRaises(Attention):self.adapter.recover_page(url)

    def test_cancel_before_submission(self):
        self.cancel.set()
        with self.assertRaises(InterruptedError):self.adapter.prepare('Not submitted',[],self.cancel)
        self.assertEqual(self.page.locator('#responses').inner_text(),'')
