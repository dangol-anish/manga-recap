"""Actionable, secret-safe errors for HTTP requests and background workers."""
from collections import OrderedDict
from datetime import datetime, timezone
import errno
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import traceback
import uuid

from app import storage

_lock = threading.RLock()
_recent = OrderedDict()
_warnings = OrderedDict()
STAGES = {
    'downloader': 'Stage 1: Downloader', 'renumbering': 'Stage 2: Renumbering',
    'detection': 'Stage 3: Panel Detection', 'preparation': 'Stage 4: Panel Review',
    'story': 'Stage 5: Narration', 'speech': 'Stage 6: Audio', 'grouped-audio': 'Stage 6: Gemini Audio',
    'cinema': 'Stage 7: Video', 'stitch': 'Stage 8: Stitch', 'shorts': 'Shorts',
    'memory': 'Story memory', 'library': 'Library', 'startup': 'App startup',
}
SAVED = {
    'downloader': 'Already verified downloads are retained; an unfinished page may need downloading again.',
    'renumbering': 'Some filenames may be in the saved renumbering transaction. Keep the folder and temporary files together; resume recovery instead of renaming them yourself.',
    'detection': 'Completed panel detections are retained. The failed page is not valid output.',
    'preparation': 'Previously saved panel fixes and page assets are retained; the failed page may still need preparation.',
    'story': 'A rejected import does not replace saved narration. Check the batch status before submitting again.',
    'speech': 'Completed clips and older revisions are retained. Check chapter progress before retrying speech.',
    'grouped-audio': 'Returned recordings are retained. Local timing retries reuse saved recordings; a timed-out Google request may still count toward quota.',
    'cinema': 'Existing audio and earlier finished videos are retained. An unfinished replacement is not a finished video.',
    'stitch': 'Source chapter videos are unchanged. Only the unfinished combined video needs another attempt.',
    'shorts': 'Original chapter work and saved Shorts clips are retained; the unfinished short is not published.',
}


def clean(value, limit=4000):
    text = str(value)
    secrets = [v for k, v in os.environ.items() if any(s in k.upper() for s in ('API_KEY', 'TOKEN', 'SECRET')) and len(v) > 8]
    try:
        env = storage.ROOT / '.env'
        if not env.is_symlink() and env.is_file():
            for line in env.read_text(encoding='utf-8').splitlines():
                if '=' in line and any(s in line.split('=', 1)[0].upper() for s in ('KEY', 'TOKEN', 'SECRET')):
                    value = line.split('=', 1)[1].strip().strip('"' + "'")
                    if len(value) > 8: secrets.append(value)
    except (OSError, UnicodeError): pass
    for secret in secrets: text = text.replace(secret, '[hidden]')
    text = re.sub(r'(?:AQ\.[A-Za-z0-9_-]{20,}|AIza[A-Za-z0-9_-]{25,})', '[hidden key]', text)
    text = re.sub(r'(https?://[^\s?#]+)\?[^\s]+', r'\1?[hidden query]', text)
    return text[:limit]


def location(path):
    try: return Path(path).resolve().relative_to(storage.ROOT.resolve()).as_posix()
    except (ValueError, OSError): return clean(path, 600)


class SavedDataError(ValueError):
    def __init__(self, path, original):
        self.resource = location(path)
        self.code = 'saved_data_corrupt'
        problem = f'line {original.lineno}, column {original.colno}: {original.msg}' if isinstance(original, json.JSONDecodeError) else 'invalid UTF-8 text encoding'
        super().__init__(f'Saved JSON file {self.resource} is unreadable at {problem}. The file was not replaced or reset.')


def context(url, method='GET'):
    parts = str(url).split('?', 1)[0].strip('/').split('/')
    stage = parts[1] if len(parts) > 1 and parts[0] == 'api' else 'library'
    aliases = {'audio': 'speech', 'video': 'cinema', 'review': 'preparation', 'alignment': 'speech', 'narration': 'story', 'manga': 'library'}
    stage = aliases.get(stage, stage)
    if stage == 'batch-work': stage = 'speech' if 'audio' in parts else 'cinema'
    last = parts[-1] if parts else ''
    actions = {'catalog': 'Load manga and chapters', 'import': 'Import narration', 'generate': 'Create audio', 'export': 'Create video',
        'open-folder': 'Open saved folder', 'open-root-folder': 'Open saved folder', 'folder': 'Open saved folder',
        'resume': 'Resume saved work', 'retry': 'Retry unfinished work', 'stop': 'Stop safely', 'timing': 'Save panel timing',
        'renumber': 'Renumber selected images', 'start': 'Start selected work', 'keys': 'Check saved key availability',
        'full': 'Join full chapter audio', 'mp4': 'Load finished video', 'audio': 'Load audio', 'photos': 'Load labeled photos', 'prompt': 'Load narration prompt'}
    operation = actions.get(last, 'Load saved work' if method == 'GET' else 'Save or start selected work')
    if last=='export' and stage=='story':operation='Export labeled photos'
    if stage=='shorts':
        operation={'import':'Import Shorts proposals','create':'Create selected shorts','edit':'Save short script','visual-check':'Prepare selected visual check','package':'Download source narration','projects':'Load Shorts projects' if method=='GET' else 'Create Shorts project'}.get(last,operation)
    if stage=='renumbering' and last in ('preview','apply'):operation='Plan safe renumbering' if last=='preview' else 'Renumber selected images'
    if last=='open':operation='Open saved folder'
    if last=='jobs' and method=='POST':operation={'downloader':'Download selected chapters','detection':'Detect selected panels','preparation':'Prepare selected panels','stitch':'Combine selected videos'}.get(stage,operation)
    if stage=='preparation' and 'pages' in parts and method=='POST':operation='Save page panel edits'
    if 'batch-work' in parts and method=='POST' and last in ('audio','video'):operation='Create selected chapter audio' if last=='audio' else 'Create selected chapter videos'
    return stage, operation


def chain(exc):
    seen = set(); result = []
    while exc is not None and id(exc) not in seen:
        result.append(exc); seen.add(id(exc)); exc = exc.__cause__ or exc.__context__
    return result


def describe(exc, stage='library', operation='Complete selected work', resource=None, status=None, log=True):
    causes = chain(exc); original = causes[1] if len(causes) > 1 and hasattr(exc, 'status_code') else exc
    reason = clean(getattr(original, 'detail', None) or str(original) or type(original).__name__)
    resource = resource or next((getattr(e, 'resource', None) or getattr(e, 'filename', None) for e in causes if getattr(e, 'resource', None) or getattr(e, 'filename', None)), None)
    numbers = {getattr(e, 'errno', None) for e in causes}; windows = {getattr(e, 'winerror', None) for e in causes}
    evidence = ' '.join(str(e) for e in causes).lower()
    code = getattr(original, 'code', None) or 'operation_failed'
    action = 'Follow the message above, then refresh this stage and retry only its unfinished work.'
    http_status = status or 400
    if isinstance(original, SavedDataError) or any(isinstance(e, json.JSONDecodeError) for e in causes):
        code = 'saved_data_corrupt'; action = 'Keep this file intact. Restore its last known good backup or use the diagnostic reference to repair it; do not delete your manga or reset all data.'
    elif errno.ENOSPC in numbers or getattr(errno, 'EDQUOT', -999) in numbers or 112 in windows or 'no space left' in evidence or 'disk full' in evidence:
        code = 'disk_full'; http_status = 507; action = 'Free space on the drive containing this repository, then resume the unfinished work. Keep existing media and temporary recovery files.'
    elif windows.intersection({32, 33}) or 'being used by another process' in evidence:
        code = 'file_in_use'; action = 'Close the file in media players, editors and Explorer preview, then retry. Do not delete the file or its recovery folders.'
    elif any(isinstance(e, PermissionError) for e in causes) or errno.EACCES in numbers or errno.EPERM in numbers:
        code = 'permission_denied'; action = 'Check write permission for the named folder and close programs holding its files. Use a writable local clone; do not disable system security.'
    elif any(isinstance(e, subprocess.TimeoutExpired) for e in causes):
        code = 'tool_timeout'; tool = Path(str(next(e.cmd[0] if isinstance(e.cmd, (list, tuple)) else e.cmd for e in causes if isinstance(e, subprocess.TimeoutExpired)))).name
        reason = f'{tool} exceeded its allowed processing time. ' + reason
        action = 'Check the affected chapter or media file, close other heavy apps, then retry this unfinished task. A request already sent to a speech service may still count toward quota.'
    elif any(isinstance(e, subprocess.CalledProcessError) for e in causes):
        tool_error = next(e for e in causes if isinstance(e, subprocess.CalledProcessError)); command = tool_error.cmd
        tool = Path(str(command[0] if isinstance(command, (list, tuple)) else command)).name
        stderr = tool_error.stderr or tool_error.output or ''
        if isinstance(stderr, bytes): stderr = stderr.decode('utf-8', 'replace')
        reason = clean(f'{tool} exited with code {tool_error.returncode}. {stderr or reason}')
        code = 'external_tool_failed'; action = 'Check the diagnostic details and named input file. For FFmpeg errors, verify FFmpeg/ffprobe are installed and the media opens before retrying.'
    elif 'out of memory' in evidence or 'cannot allocate memory' in evidence or 'not enough memory' in evidence:
        code = 'memory_exhausted'; action = 'Close other heavy apps, keep only one app task running, then resume. If one page or section is unusually large, retry a smaller selection.'
    elif any(isinstance(e, (ModuleNotFoundError, ImportError)) for e in causes):
        code = 'dependency_missing'; action = 'Run this clone’s setup script using its own virtual environment, then restart. Do not copy dependencies from another project.'
    elif any(isinstance(e, FileNotFoundError) for e in causes):
        code = 'file_missing'
        if any(t in evidence for t in ('ffmpeg', 'ffprobe')): action = 'Install the free FFmpeg tools, ensure ffmpeg and ffprobe are on PATH, then restart the app.'
        else: action = 'Restore the named input or saved file to its original location. Refresh this stage; do not retry generation until its source files are available.'
    elif any(getattr(e,'response',None) is not None and getattr(e.response,'status_code',0)>=400 for e in causes):
        response=next(e.response for e in causes if getattr(e,'response',None) is not None and getattr(e.response,'status_code',0)>=400)
        upstream=response.status_code
        code='source_blocked' if upstream in (401,403) else 'source_rate_limit' if upstream==429 else 'source_missing' if upstream==404 else 'source_unavailable'
        reason=clean(f'The external service returned HTTP {upstream}. {reason}')
        action='Open the source website in your external browser to check access, then retry later. The app will not bypass access restrictions.' if upstream in (401,403) else 'Wait for the source service’s limit/reset, then resume missing pages. Do not repeatedly restart the download.' if upstream==429 else 'Check whether the manga/chapter URL still exists; reload the chapter list before retrying.' if upstream==404 else 'Check the external service’s status and your connection, then resume unfinished work later.'
    elif 'noaudioreceived' in evidence or 'no audio was received' in evidence:
        code = 'speech_empty'; action = 'Microsoft returned no speech. Check internet/service availability and the saved voice, then resume missing clips. Existing clips are retained.'
    elif any(t in type(e).__name__.lower() for e in causes for t in ('connecterror', 'connectorerror', 'dns', 'networkerror')):
        code = 'connection_failed'; action = 'Check internet, DNS and firewall access to the named service, then resume. Reopening this page does not resend speech automatically.'
    elif any(isinstance(e, TimeoutError) or 'timeout' in type(e).__name__.lower() for e in causes):
        code = 'service_timeout'; action = 'The service or tool did not respond in time. Check its connection/status, then resume explicitly; a speech request may already have counted.'
    elif any(t in evidence for t in ('image file is truncated', 'cannot identify image file', 'broken data stream', 'decompressionbomb')):
        code = 'image_unreadable'; action = 'Open the named page in your normal image viewer. Restore or re-download that page if damaged, then retry only the affected chapter.'
    elif any(t in evidence for t in ('ffmpeg', 'ffprobe', 'encoder not found', 'unknown encoder', 'invalid data found when processing input')):
        code = 'media_processing'; action = 'Check the named audio/video input and FFmpeg diagnostic details. Confirm both ffmpeg and ffprobe are installed; retry unfinished media after fixing the cause.'
    elif isinstance(original, (KeyError, IndexError, TypeError, AttributeError)):
        code = 'unexpected_data'; http_status = status or 500
        reason = f'{type(original).__name__}: {reason}. The app encountered data in an unexpected shape.'
        action = 'Keep the saved files and report the diagnostic reference. Refresh before retrying; do not clear your manga, narration or media to work around this error.'
    elif isinstance(original, FileExistsError):
        code = 'conflict'; http_status = status or 409
        action = 'Refresh the affected stage and check which files, settings or earlier steps changed. Existing files must not be overwritten to bypass this check.'
    elif isinstance(original, OSError):
        code = 'filesystem_error'; action = 'Check the named path, drive connection, permissions and free space, then resume. Keep all files from any interrupted operation together.'
    elif not isinstance(original, ValueError) and not hasattr(original, 'status_code') and not getattr(original, 'code', None):
        code = 'unexpected_error'; http_status = status or 500
        reason = f'{type(original).__name__}: {reason}'
        action = 'Keep your saved work and report the diagnostic reference with the action you took. Refresh and inspect progress before retrying; do not reset all data.'
    if getattr(original, 'code', None) and not isinstance(original, SavedDataError):
        # Provider errors already carry specific recovery instructions; keep them.
        code = original.code
        action = 'Use the recovery instructions in the provider message above. Brian is used only if you explicitly select it.' if stage == 'grouped-audio' and hasattr(original,'reset') else action
    ref = uuid.uuid4().hex[:12]
    saved = SAVED.get(stage, 'Check the affected file or task status before retrying. No automatic data reset is performed.')
    if 'recover' in operation.lower():action+=' After fixing the recovery problem, restart the app so saved tasks can be checked again.'
    detail = f'{STAGES.get(stage, stage)} — {operation}: {reason}'
    if resource: detail += ' Affected: ' + clean(location(resource)) + '.'
    detail += ' What to do: ' + action + ' Saved work: ' + saved + ' Reference: ' + ref + '.'
    result = {'code': str(code), 'stage': stage, 'operation': operation, 'resource': clean(location(resource)) if resource else None,
        'reason': reason, 'action': action, 'saved_work': saved, 'reference': ref, 'message': clean(detail, 6500),
        'status': http_status, 'reset_at': getattr(original, 'reset', None)}
    if log: record(result, exc)
    return result


def record(error, exc=None, warning=False):
    item = {**error, 'time': datetime.now(timezone.utc).isoformat()}
    if exc is not None: item['technical_details'] = clean(''.join(traceback.format_exception(type(exc), exc, exc.__traceback__)), 12000)
    with _lock:
        _recent[item['reference']] = item
        while len(_recent) > 100: _recent.popitem(last=False)
        if warning:
            _warnings[item['reference']] = item
            while len(_warnings) > 20: _warnings.popitem(last=False)
        try:
            directory = storage.DATA / 'errors'
            if storage.DATA.is_symlink() or directory.is_symlink(): return
            directory.mkdir(parents=True, exist_ok=True)
            filename = directory / (item['time'][:10] + '.jsonl')
            if filename.is_symlink(): return
            if filename.exists() and filename.stat().st_size > 10_000_000:return
            with filename.open('a', encoding='utf-8') as stream: stream.write(json.dumps(item, ensure_ascii=True) + '\n')
        except OSError:
            # Disk-full/permission failures must still reach the browser in memory.
            pass


def warning(exc, stage, operation, resource=None):
    result = describe(exc, stage, operation, resource, log=False)
    with _lock:
        old=next((v for v in _warnings.values() if all(v.get(k)==result.get(k) for k in ('stage','operation','resource','reason'))),None)
        if old:return old
    record(result, exc, warning=True)
    return result


def failure(job, exc, stage, operation, resource=None):
    result = describe(exc, stage, operation, resource)
    job.update(error=result, message=result['message'])
    return result


def save_final(writer, job, stage):
    try: writer(job)
    except Exception as exc:
        warning(exc, stage, 'Save task progress', job.get('id'))
        return False
    with _lock:
        for ref,item in list(_warnings.items()):
            if item['stage']==stage and item['operation']=='Save task progress' and item.get('resource')==location(job.get('id','')):_warnings.pop(ref,None)
    return True


def recovery_records(directory, stage):
    files=[]
    for candidate in Path(directory).glob('*.json'):
        try:files.append((candidate.lstat().st_mtime,candidate))
        except OSError as exc:warning(exc,stage,'List saved task',candidate)
    for _,path in sorted(files,key=lambda item:item[0]):
        if path.is_symlink():
            warning(ValueError('A saved task points through a symbolic link; it was not opened.'), stage, 'Recover saved task', path); continue
        try:
            job = storage.read_json(path)
            if not isinstance(job, dict) or not isinstance(job.get('status'), str) or not isinstance(job.get('id'), str) or not re.fullmatch('[a-f0-9]{32}',job.get('id','')):
                raise ValueError('Saved task must contain an id and status. The original file was kept intact.')
            with _lock:
                for ref,item in list(_warnings.items()):
                    if item.get('resource')==location(path) and item['operation']=='Recover saved task':_warnings.pop(ref,None)
            yield path, job
        except (ValueError, OSError) as exc: warning(exc, stage, 'Recover saved task', path)


def latest_warning():
    with _lock: return next(reversed(_warnings), None) if _warnings else None


def install(app):
    from fastapi import Request
    from fastapi.exceptions import RequestValidationError
    from starlette.exceptions import HTTPException
    from starlette.responses import JSONResponse

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc):
        stage, operation = context(request.url.path, request.method)
        affected=' / '.join(str(v) for k,v in request.path_params.items() if k in ('chapter','pid','number','ident','sid','reference')) or None
        value = describe(exc, stage, operation, resource=affected, status=exc.status_code)
        return JSONResponse({'detail': value['message'], 'error': value}, status_code=value['status'], headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def invalid_input(request: Request, exc):
        stage, operation = context(request.url.path, request.method)
        fields = '; '.join('.'.join(str(p) for p in item['loc']) + ': ' + item['msg'] for item in exc.errors())
        value = describe(ValueError('Check these input fields: ' + fields), stage, operation, status=422)
        value.update(code='invalid_input', action='Correct the named fields and submit again. No task was started by this rejected request.')
        return JSONResponse({'detail': value['message'], 'error': value}, status_code=422)

    @app.get('/api/errors/{reference}')
    def diagnostic(reference: str):
        with _lock: value = _recent.get(reference)
        if not value: raise HTTPException(404, 'This diagnostic is no longer in memory. Check the ignored data/errors folder for saved details.')
        return value


def checked_process(result, operation, resource=None):
    """Keep the actual tool exit code and stderr instead of dropping them."""
    if not result.returncode: return
    command = getattr(result, 'args', ['media tool'])
    tool = Path(str(command[0] if isinstance(command, (list, tuple)) else command)).name
    output = getattr(result, 'stderr', None) or getattr(result, 'stdout', None) or 'The tool returned no diagnostic output.'
    if isinstance(output, bytes): output = output.decode('utf-8', 'replace')
    problem = ValueError(clean(f'{operation}: {tool} exited with code {result.returncode}. {output}', 3500))
    problem.resource = str(resource) if resource else None
    raise problem from subprocess.CalledProcessError(result.returncode, command, stderr=output)


def job_list(directory, stage, transform=lambda value:value):
    values=[]
    for path,job in recovery_records(directory,stage):
        try: values.append(transform(job))
        except (KeyError,ValueError,TypeError,OSError) as exc: warning(exc,stage,'Read saved task',path)
    return values
