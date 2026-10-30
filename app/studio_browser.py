"""Visible AI Studio controls only. No Gemini API, private endpoints or account rotation."""
import json
import os
import re
import time
from urllib.parse import urlparse
from app import storage

NEW_CHAT = 'https://aistudio.google.com/prompts/new_chat'
MODEL = 'gemini-3.8-flash'


class Attention(RuntimeError):
    def __init__(self, message, code='browser', retry_safe=False):
        super().__init__(message)
        self.code, self.retry_safe = code, retry_safe


def json_objects(text):
    """Read complete visible JSON objects, never JavaScript or page internals."""
    decoder = json.JSONDecoder()
    result = []
    position = 0
    while position < len(text):
        start = text.find('{', position)
        if start < 0: break
        try:
            value, size = decoder.raw_decode(text[start:])
        except ValueError:
            position = start + 1
            continue
        position = start + size
        if isinstance(value, dict) and isinstance(value.get('beats'), list):
            result.append(text[start:position])
        elif isinstance(value, dict) and set(value) == {'response'} and isinstance(value['response'], str):
            # AI Studio's default response:string schema can wrap a complete
            # narration object. Unwrap only this exact transport envelope;
            # leave the narration text and the normal import checks intact.
            try: inner = json.loads(value['response'])
            except ValueError: continue
            if isinstance(inner, dict) and isinstance(inner.get('beats'), list):
                result.append(value['response'])
    return result


def page_problem(url, text):
    host = urlparse(url).hostname
    if host in {'accounts.google.com', 'aistudio.google.com'} and re.search(
            r'(?:this )?browser or app may not be secure|couldn[’\x27]t sign you in', text, re.I):
        return Attention('Google rejected sign-in in the automated narration browser. Repeating sign-in or Resume may give the same rejection. Stop & close the narration browser and use the manual Narration workflow in your usual browser for now. This browser connection needs a different approach; saved narration and queue progress are retained.', 'login_rejected')
    if host != 'aistudio.google.com':
        return Attention('Sign in to AI Studio in the narration browser, then press Resume. No passwords are read by the app.', 'login', True)
    if re.search(r'captcha|verify (?:that )?you are human|unusual traffic|suspicious activity|access restricted', text, re.I):
        return Attention('AI Studio needs a human access/security check. Complete it yourself in the browser before resuming.', 'access', True)
    if re.search(r'quota.{0,80}(?:exceeded|exhausted)|(?:daily|usage|rate) limit (?:reached|exceeded)|too many requests|you(?: have|.ve) reached.{0,60}limit', text, re.I):
        return Attention('AI Studio reports a usage limit. Progress is saved. Wait until access is available, then press Resume; the queue will not repeatedly send requests.', 'limit', True)
    if re.search(r'failed to generate|an internal error occurred|something went wrong|content.{0,25}blocked', text, re.I):
        return Attention('AI Studio did not finish normally. Check the last request in its browser. Nothing will be resent automatically.', 'generation')
    return None


class StudioBrowser:
    def __enter__(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise Attention('Install the optional browser package with python -m pip install -r requirements-browser.txt, then install its Chromium browser with python -m playwright install chromium. No speech or models are downloaded.', 'setup', True) from exc
        profile = storage.ROOT / '.cache' / 'narration-browser-profile'
        if profile.is_symlink() or profile.parent.is_symlink(): raise ValueError('Unsafe narration browser profile.')
        profile.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH', str(storage.ROOT/'.cache'/'ui-browsers'))
        self.runtime = sync_playwright().start()
        try:
            self.context = self.runtime.chromium.launch_persistent_context(
                str(profile), headless=False, viewport={'width': 1280, 'height': 900})
            self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
            self.page.set_default_timeout(12000)
        except Exception as exc:
            self.runtime.stop()
            raise Attention('The narration browser could not open. Close an older narration browser using the same profile, or follow docs/BROWSER_NARRATION.md to install Chromium. Check the diagnostic reference for the observed cause.', 'setup', True) from exc
        return self

    def __exit__(self, *args):
        try: self.context.close()
        finally: self.runtime.stop()

    def check(self):
        problem = page_problem(self.page.url, self.page.locator('body').inner_text())
        if problem: raise problem

    def configure(self):
        self.check()
        if not self.page.get_by_role('button', name=re.compile(r'Gemini 3\.8 Flash')).count():
            raise Attention('Choose Gemini 3.8 Flash in the narration browser, then press Resume. The queue never switches models silently.', 'settings', True)
        thinking = self.page.get_by_role('combobox', name='Thinking Level', exact=True)
        if not thinking.count(): raise Attention('Thinking level could not be checked. Open Run settings and select Medium, then resume.', 'settings', True)
        if thinking.inner_text().strip().lower() != 'medium':
            thinking.click()
            self.page.get_by_role('option', name='Medium', exact=True).click()
        for label in ('Code execution', 'Function calling', 'Grounding with Google Search', 'Grounding with Google Maps', 'Browse the url context'):
            control = self.page.get_by_role('switch', name=label, exact=True)
            if control.count() and control.get_attribute('aria-checked') == 'true': control.click()
        switch = self.page.get_by_role('switch', name='Structured outputs', exact=True)
        if not switch.count(): raise Attention('Structured outputs could not be checked. Open Run settings in AI Studio, then resume.', 'settings', True)
        if switch.get_attribute('aria-checked') != 'true': switch.click()
        # Use explicit narration fields. Empty editor contents can restore
        # AI Studio's example response:string schema rather than JSON mode.
        save = self.page.get_by_role('button', name='Save the current schema', exact=True)
        if not save.count():
            self.page.get_by_role('button', name='Edit JSON schema', exact=True).click()
        self.page.get_by_role('tab', name='Code Editor', exact=True).click()
        self.page.get_by_role('textbox', name='Enter a JSON schema', exact=True).fill((storage.ROOT/'browser-extension/narration-schema.json').read_text(encoding='utf-8'))
        save.click()
        if switch.get_attribute('aria-checked') != 'true':
            raise Attention('AI Studio did not enable JSON output. Enable Structured outputs and resume.', 'settings', True)

    def prepare(self, prompt, photos, cancel, fresh=True):
        if fresh: self.page.goto(NEW_CHAT, wait_until='domcontentloaded')
        self.check()
        try: self.page.get_by_role('textbox', name='Enter a prompt', exact=True).wait_for(timeout=30000)
        except Exception as exc:
            self.check()
            raise Attention('AI Studio is not ready for a prompt. Sign in or finish opening its Playground in the narration browser, then resume. Nothing was submitted.', 'settings', True) from exc
        self.configure()
        if cancel.is_set(): raise InterruptedError('Stopped before submitting to AI Studio.')
        box = self.page.get_by_role('textbox', name='Enter a prompt', exact=True)
        box.fill(prompt)
        if photos:
            files = self.page.locator('input[type="file"]')
            if files.count() != 1: raise Attention('AI Studio photo upload controls changed. Check the browser; no request was submitted.', 'upload', True)
            files.set_input_files([str(p) for p in photos])
            if files.evaluate('(element) => element.files.length') != len(photos):
                raise Attention('Not all selected photos reached the browser upload control. Check the attachments; nothing was submitted.', 'upload', True)
            # Wait for a genuinely enabled Run control; no fixed upload delay.
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                if cancel.is_set(): raise InterruptedError('Stopped during photo upload.')
                self.check()
                run = self.page.get_by_role('button', name=re.compile(r'^Run(?:\s|$)'))
                if run.count() == 1 and run.is_enabled() and not self.page.get_by_role('progressbar').count(): break
                self.page.wait_for_timeout(1000)
            else: raise Attention('Photos did not finish uploading within three minutes. Check attachments before resuming.', 'upload', True)
        return self.page.url

    def send(self):
        self.check()
        run = self.page.get_by_role('button', name=re.compile(r'^Run(?:\s|$)'))
        if run.count() != 1 or not run.is_enabled():
            raise Attention('AI Studio Run is unavailable. Check the browser; the queue has not clicked Run.', 'settings', True)
        run.click()
        return self.page.url

    def receive(self, before, cancel, checkpoint, timeout=1200):
        deadline = time.monotonic() + timeout
        previous = None
        stable = 0
        while time.monotonic() < deadline:
            if cancel.is_set():
                stop = self.page.get_by_role('button', name=re.compile(r'^Stop(?:\s|$)'))
                if stop.count() == 1: stop.click()
                raise InterruptedError('Stopped while waiting for AI Studio. Check its last request before explicitly retrying.')
            checkpoint(self.page.url)
            self.check()
            candidates = json_objects(self.page.locator('main').inner_text())
            unseen = [s for s in candidates if s not in before]
            stop = self.page.get_by_role('button', name=re.compile(r'^Stop(?:\s|$)'))
            run = self.page.get_by_role('button', name=re.compile(r'^Run(?:\s|$)'))
            finished = not stop.count() and run.count() == 1
            candidate = unseen[-1] if unseen else None
            stable = stable + 1 if candidate and candidate == previous and finished else 0
            if stable >= 3: return candidate
            previous = candidate
            self.page.wait_for_timeout(1000)
        raise Attention('AI Studio did not provide a complete new JSON response within 20 minutes. Check the last request before retrying; it will not be resent automatically.', 'timeout')

    def visible_replies(self):
        return json_objects(self.page.locator('main').inner_text())

    def recover_page(self, url):
        parsed = urlparse(url)
        if parsed.scheme != 'https' or parsed.hostname != 'aistudio.google.com' or not parsed.path.startswith('/prompts/') or parsed.query or parsed.fragment:
            raise Attention('No saved AI Studio conversation can be reopened safely. Check the last request, then explicitly retry this unfinished request.', 'recovery')
        if parsed.path == '/prompts/new_chat':
            raise Attention('The last submission has no saved conversation address. Check AI Studio before explicitly retrying; no request was resent.', 'recovery')
        self.page.goto(url, wait_until='domcontentloaded')
