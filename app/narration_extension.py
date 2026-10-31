"""Explicitly paired, loopback-only extension transport. Never handles Google login."""
import base64
import copy
import json
import re
import secrets
import threading
import time
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError
from app import storage
from app.studio_browser import Attention, page_problem
from app.studio_responses import decode_reply, reply_tokens

router = APIRouter(prefix='/api/narration-extension')
lock = threading.RLock()
pair_code = None
pair_deadline = 0
connection = None
pending = None


def local_request(request):
    if request.headers.get('host') not in {'127.0.0.1:8000', 'localhost:8000', 'testserver'}:
        raise HTTPException(403, 'Open the app at http://127.0.0.1:8000.')
    origin = request.headers.get('origin')
    if origin and origin not in {'http://127.0.0.1:8000', 'http://localhost:8000'}:
        raise HTTPException(403, 'Use the local Narration page for this action.')
    if request.headers.get('sec-fetch-site') == 'cross-site':
        raise HTTPException(403, 'Use the local Narration page for this action.')


def extension_id(request):
    ident = request.headers.get('x-narration-extension', '')
    origin = request.headers.get('origin')
    if not re.fullmatch('[a-p]{32}', ident) or origin not in (None, 'chrome-extension://' + ident):
        raise HTTPException(403, 'Only the paired narration extension can connect.')
    if request.headers.get('host') not in {'127.0.0.1:8000', 'testserver'}:
        raise HTTPException(403, 'Use the loopback app address.')
    return ident


def authenticate(request):
    ident = extension_id(request)
    supplied = request.headers.get('authorization', '')
    with lock:
        if not connection or connection['id'] != ident or not secrets.compare_digest(supplied, 'Bearer ' + connection['token']):
            raise HTTPException(403, 'Pair the extension again from the Narration page.')
        connection['seen'] = time.monotonic()


@router.get('/status')
def status(request: Request):
    local_request(request)
    return connection_view()


def connection_view():
    with lock:
        return {'connected': bool(connection and time.monotonic() - connection['seen'] < 15),
                'paired': bool(connection)}


@router.post('/heartbeat')
def heartbeat(request: Request):
    authenticate(request)
    return {'alive':True}


class Progress(BaseModel):
    id:str=Field(pattern='^[a-f0-9]{32}$')
    step:str=Field(max_length=40)


@router.post('/progress')
def progress(body:Progress,request:Request):
    authenticate(request)
    from app.browser_narration import ACTIVITIES
    if body.step not in ACTIVITIES:raise HTTPException(400,'Unknown narration activity.')
    with lock:
        if not pending or pending['id']!=body.id or not pending['claimed'] or 'result' in pending:
            raise HTTPException(409,'This browser action is no longer active.')
        pending['progress']=body.step
    return {'saved':True}


@router.post('/code')
def code(request: Request):
    global pair_code, pair_deadline
    local_request(request)
    with lock:
        pair_code = secrets.token_urlsafe(24)
        pair_deadline = time.monotonic() + 300
        return {'code': pair_code, 'expires_seconds': 300}


class Pair(BaseModel):
    code: str = Field(min_length=1, max_length=100)


@router.post('/pair')
def pair(body: Pair, request: Request):
    global connection, pair_code
    ident = extension_id(request)
    with lock:
        if not pair_code or time.monotonic() > pair_deadline or not secrets.compare_digest(body.code, pair_code):
            raise HTTPException(403, 'Get a new pairing code from the local Narration page.')
        if pending and pending.get('claimed') and 'result' not in pending:
            raise HTTPException(409, 'Stop the current queue before changing its browser connection.')
        connection = {'id': ident, 'token': secrets.token_urlsafe(32), 'seen': time.monotonic()}
        pair_code = None
        return {'token': connection['token']}


@router.post('/disconnect')
def disconnect(request: Request):
    global connection, pair_code
    local_request(request)
    from app import browser_narration
    job = browser_narration.current()
    if job and job['status'] in {'running', 'stopping'}:
        raise HTTPException(409, 'Stop the narration queue before disconnecting.')
    with lock:
        connection = pair_code = None
    return {'disconnected': True}


@router.post('/poll')
def poll(request: Request):
    authenticate(request)
    with lock:
        if not pending or pending.get('claimed') or 'result' in pending:
            return {'command': None}
        pending['claimed'] = True  # One delivery only, including after worker/browser crashes.
        return {'command': {k: copy.deepcopy(pending[k]) for k in ('id', 'action', 'payload', 'expiresAt')}}


class Result(BaseModel):
    id: str = Field(pattern='^[a-f0-9]{32}$')
    value: dict = Field(default_factory=dict)


class Permit(BaseModel):
    id: str = Field(pattern='^[a-f0-9]{32}$')


@router.post('/permit')
def permit(body: Permit, request: Request):
    authenticate(request)
    from app import browser_narration
    with lock:
        if not pending or pending['id'] != body.id or pending['action'] != 'send' or not pending['claimed'] or browser_narration.cancel.is_set() or time.time()*1000 > pending['expiresAt']:
            raise HTTPException(409, 'This submission was stopped or expired. Nothing should be sent.')
    return {'permitted': True}


@router.post('/result')
async def result(request: Request):
    authenticate(request)
    # Bound snapshots before decoding; photos travel in the opposite direction.
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 8_000_000: raise HTTPException(413, 'Browser response is too large; inspect the last reply manually.')
    try: body = Result.model_validate_json(raw)
    except ValidationError: raise HTTPException(400, 'The browser acknowledgement is malformed. Inspect the last action before resuming.') from None
    with lock:
        if not pending or pending['id'] != body.id or not pending.get('claimed'):
            raise HTTPException(409, 'This browser command expired. It will not be resent.')
        if 'result' not in pending: pending['result'] = body.value
    return {'saved': True}


@router.post('/open-folder')
def open_folder(request: Request):
    local_request(request)
    import os
    import subprocess
    import sys
    path = storage.ROOT / 'browser-extension'
    if sys.platform == 'darwin': subprocess.run(['open', str(path)], check=True, timeout=10)
    elif sys.platform == 'win32': os.startfile(str(path))
    else: raise HTTPException(400, 'Use the browser-extension folder inside this repository.')
    return {'opened': True}


def command(action, payload=None, cancel=None, timeout=35):
    global pending
    with lock:
        if not connection or time.monotonic() - connection['seen'] > 15:
            raise Attention('The browser extension is disconnected. Open its popup in your usual browser, connect it to this app, then Resume. Nothing was resent.', 'extension', action == 'prepare')
        if pending is not None:
            raise Attention('An earlier browser command is still unresolved. Stop the queue and inspect AI Studio before resuming.', 'recovery')
        entry = {'id': secrets.token_hex(16), 'action': action, 'payload': payload or {}, 'claimed': False,
                 'expiresAt': int((time.time()+timeout)*1000)}
        pending = entry
    deadline = time.monotonic() + timeout
    reported=None
    try:
        while time.monotonic() < deadline:
            if cancel and cancel.is_set(): raise InterruptedError('Stopped. Inspect AI Studio before retrying an unfinished request.')
            with lock:
                activity=entry.get('progress')
                if 'result' in entry:
                    value = entry['result']
                    if value.get('error'):
                        raise Attention(str(value['error'])[:1800], value.get('code', 'browser'), bool(value.get('retry_safe')))
                    return value
            if activity and activity!=reported:
                from app.browser_narration import report_activity
                report_activity(activity);reported=activity
            time.sleep(.1)
        raise Attention('The extension did not confirm its last action. Check the AI Studio tab before resuming; the app will not automatically repeat that action.', 'recovery')
    finally:
        with lock:
            if pending is entry: pending = None


class ExtensionBrowser:
    """Same queue contract as the earlier adapter, using an ordinary browser tab."""
    def __enter__(self):
        from app.browser_narration import cancel
        self.cancel = cancel
        hello = command('hello', cancel=self.cancel)
        if hello.get('protocol') != 3:
            raise Attention('Update the installed Manga Recap Narration extension using Reload in your browser extensions page, then Resume. The old reply reader will not be used.', 'extension_update')
        return self

    def __exit__(self, *args):
        # Leave the user's browser and AI Studio reply open for inspection.
        pass

    def snapshot(self):
        value = command('snapshot', cancel=getattr(self, 'cancel', None))
        if value.get('protocol') != 3 or type(value.get('finished')) is not bool or type(value.get('generating')) is not bool or not isinstance(value.get('replies'),list):
            raise Attention('The extension returned an unsupported page snapshot. Reload the extension and resume; no request was resent.', 'extension_update')
        if len(value['replies']) > 100 or any(not isinstance(r,dict) or not isinstance(r.get('id'),str) or not isinstance(r.get('text'),str) for r in value['replies']):
            raise Attention('The extension could not identify model replies reliably. Check its AI Studio tab; no response was imported.', 'response_read')
        problem = page_problem(value.get('url', ''), value.get('status_text', ''))
        if problem: raise problem
        return value

    def prepare(self, prompt, photos, cancel, fresh=True, reply_mode=None):
        files = []
        total = 0
        for path in photos:
            if path.suffix.lower() != '.jpg' or not path.resolve().is_relative_to(storage.OUTPUTS.resolve()) or path.is_symlink():
                raise ValueError('Only the current exported narration photos can be attached.')
            content = path.read_bytes()
            total += len(content)
            if total > 32_000_000: raise ValueError('This photo batch exceeds the browser connection size limit. Use the manual workflow for this batch.')
            files.append({'name': path.name, 'data': base64.b64encode(content).decode('ascii')})
        if cancel.is_set(): raise InterruptedError('Stopped before preparing AI Studio.')
        schema = json.loads((storage.ROOT/'browser-extension/narration-schema.json').read_text(encoding='utf-8'))
        beat_schema = schema['properties']['beats']['items']
        if (reply_mode or ('full' if fresh else 'partial')) == 'full':
            beat_schema['properties'].pop('replace_beat', None)
        else:
            beat_schema['required'].append('replace_beat')
        return command('prepare', {'prompt': prompt, 'photos': files, 'fresh': fresh, 'schema': schema}, cancel, 600).get('url')

    def visible_replies(self):
        return reply_tokens(self.snapshot())

    def send(self):
        return command('send', cancel=getattr(self, 'cancel', None)).get('url', '')

    def receive(self, before, cancel, checkpoint, timeout=600):
        deadline = time.monotonic() + timeout
        previous, stable = None, 0
        idle_since = None
        unknown_since = None
        while time.monotonic() < deadline:
            if cancel.is_set():
                # Queue stop never closes personal tabs. Remote generation may still finish.
                raise InterruptedError('Stopped locally. The last AI Studio request may still finish; inspect it before retrying.')
            value = self.snapshot()
            checkpoint(value.get('url', ''))
            tokens = reply_tokens(value)
            fresh = [(token,item) for token,item in zip(tokens,value['replies']) if token not in before]
            if value.get('finished') and not value.get('generating'):
                unknown_since=None
                idle_since = idle_since or time.monotonic()
                if fresh:
                    token,item = fresh[-1]
                    stable = stable+1 if token == previous else 1
                    previous = token
                    if stable >= 3:
                        from app.browser_narration import report_activity
                        report_activity('reading_reply')
                        self.last_capture = {'url':value.get('url','').split('?',1)[0], 'turn_id':item['id'], 'raw':item['text'], 'finished':True}
                        return decode_reply(item['text'])
                elif time.monotonic()-idle_since >= 15:
                    raise Attention('AI Studio appears finished, but no new model reply can be read. The queue is paused instead of waiting or resending. Inspect the saved conversation; use the explicit retry control only after checking the last request.', 'response_read')
            else:
                stable=0;previous=None;idle_since=None
                if value.get('generating'):unknown_since=None
                else:
                    unknown_since=unknown_since or time.monotonic()
                    if time.monotonic()-unknown_since>=120:
                        raise Attention('AI Studio has shown neither a generation indicator nor a verifiable completed response for two minutes. The queue is paused because its page state is uncertain. Inspect the selected tab before resuming; nothing was resent or imported.', 'response_read')
            cancel.wait(1)
        raise Attention('AI Studio did not finish a new JSON reply within 10 minutes. Inspect its last request; nothing will be resent automatically.', 'timeout')

    def recover_page(self, url):
        parsed = urlparse(url)
        if parsed.scheme != 'https' or parsed.hostname != 'aistudio.google.com' or not parsed.path.startswith('/prompts/') or parsed.query or parsed.fragment:
            raise Attention('No saved conversation address is available. Inspect the AI Studio tab, then explicitly choose to submit this batch again if needed.', 'recovery')
        if parsed.path == '/prompts/new_chat':
            # AI Studio may assign its conversation URL only after the first
            # response finishes. Inspect the already-paired tab; navigating to
            # new_chat would discard the very response we are recovering.
            value=self.snapshot()
            current=urlparse(value.get('url',''))
            if current.scheme!='https' or current.hostname!='aistudio.google.com' or not current.path.startswith('/prompts/') or current.query or current.fragment:
                raise Attention('The paired tab is not showing the unfinished AI Studio conversation. Nothing was resent.', 'recovery')
            return
        command('recover', {'url': url}, cancel=getattr(self, 'cancel', None))
