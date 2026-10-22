"""Stage 3 queue/controller. Heavy model work lives in a separate process."""
from copy import deepcopy
from hashlib import sha256
from pathlib import PurePosixPath
import subprocess
import sys
import time
import uuid

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from app import storage, renumbering
from app.detector import MODEL_ID, REVISION
from app.runtime import acquire_server_lock, open_system_folder
from app.chapter_names import chapter_sort_key
from app.read_cache import memoized_read

router = APIRouter(prefix='/api/detection')
process = None
active_id = None


def base(): return storage.DATA / 'detection'


def job_path(ident):
    if len(ident) != 32 or any(c not in '0123456789abcdef' for c in ident): raise ValueError('Invalid detection job.')
    return base() / 'jobs' / (ident + '.json')


def load(ident):
    value = storage.read_json(job_path(ident))
    if not value: raise ValueError('Detection job not found.')
    return value


def save(job):
    job['updated_at'] = time.time()
    storage.write_json(job_path(job['id']), job)


def stop_path(ident): return job_path(ident).with_suffix('.stop')


def worker_lock():
    base().mkdir(parents=True, exist_ok=True)
    return acquire_server_lock(base() / 'worker.lock')


def busy():
    if process is not None and process.poll() is None: return True
    try: worker_lock().close()
    except RuntimeError: return True
    return False


def ready():
    source = storage.ROOT / 'vendor/magiv3'
    installed = all((source / name).is_file() for name in ('model.safetensors', 'utils.py', 'config.json'))
    validation = storage.read_json(storage.DATA / 'model-validation.json', {})
    validated = (validation.get('panel_only_validated') is True
                 and validation.get('revision', REVISION) == REVISION
                 and validation.get('model', MODEL_ID) == MODEL_ID)
    return {'ready': installed and validated, 'model': MODEL_ID, 'revision': REVISION, 'message': 'Magi v3 ready · normal detection, panel boxes only' if installed and validated else 'Install Magi v3, then run python -m scripts.validate_model using this repository’s virtual environment. See README installation steps.'}


@memoized_read
def catalog():
    settings = storage.read_json(base() / 'settings.json', {})
    library = storage.read_json(storage.DATA / 'library.json', [])
    groups = {}
    for chapter in renumbering.folders():
        path = PurePosixPath(chapter['folder'])
        parent = str(path.parent) if len(path.parts) > 2 else str(path)
        manga = next((m for m in library if m['folder'].replace('\\', '/') == parent), None)
        if parent not in groups:
            groups[parent] = {'id': manga['id'] if manga else sha256(parent.encode()).hexdigest()[:32], 'name': manga['name'] if manga else PurePosixPath(parent).name, 'folder': parent, 'direction': settings.get(parent, manga.get('direction', 'rtl') if manga else 'rtl'), 'chapters': []}
        groups[parent]['chapters'].append(chapter)
    return {'manga': list(groups.values()), 'detector': ready()}


def output(chapter):
    # All components originate from catalogued folders and internal manga IDs.
    ident, name = chapter['manga_id'], chapter['name']
    if not ident.isalnum() or name != PurePosixPath(name).name or name.startswith('.') or '\\' in name:
        raise ValueError('Invalid detection destination.')
    path = storage.OUTPUTS / ident / name / 'detection'
    current = storage.ROOT
    # Reject symlinks even if an output path was manually changed.
    if not path.resolve().is_relative_to(storage.OUTPUTS.resolve()): raise ValueError('Unsafe detection destination.')
    for component in path.relative_to(storage.ROOT).parts:
        current /= component
        if current.is_symlink(): raise ValueError('Detection output cannot use symbolic links.')
    return path


def summary(job):
    value = deepcopy(job)
    if value['status'] == 'running' and stop_path(value['id']).exists():
        value.update(status='stopping', message='Finishing the current page before stopping…')
    if value['status'] in ('running', 'stopping') and value.get('run_started_at'):
        value['elapsed_seconds'] = round(max(value.get('elapsed_seconds', 0), value.get('elapsed_base', 0) + time.time() - value['run_started_at']), 1)
    value['total_pages'] = sum(len(c['pages']) for c in value['chapters'])
    results = [r for c in value['chapters'] for r in c.get('results', [])]
    value['completed_pages'] = len(results)
    value['valid_pages'] = sum(r['status'] == 'valid' for r in results)
    value['flagged_pages'] = len(results) - value['valid_pages']
    value['panels_found'] = sum(r['panels'] for r in results if r['status'] == 'valid')
    value['average_seconds_per_page'] = round(sum(r['seconds'] for r in results) / len(results), 3) if results else 0
    value['completed_chapters'] = sum(c['status'] == 'complete' for c in value['chapters'])
    return value


def reconcile():
    global process, active_id
    if process is not None and process.poll() is not None:
        job = load(active_id)
        if job['status'] in ('running', 'stopping'):
            stopped = stop_path(job['id']).exists()
            job.update(status='paused' if stopped else 'failed', message='Stopped. Resume to continue; completed pages are safe.' if stopped else f'Worker exited ({process.returncode}). Saved pages are safe. Check the job log in its output data folder and resume.')
            save(job)
        process = active_id = None


def recover():
    for path in (base() / 'jobs').glob('*.json'):
        job = storage.read_json(path)
        if job['status'] in ('running', 'stopping'):
            stop_path(job['id']).touch()
            job.update(status='paused', message='Interrupted by shutdown. Resume to verify saved results and continue.')
            save(job)


def shutdown():
    if process is not None and process.poll() is None:
        stop_path(active_id).touch()
        try: process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.terminate()
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired: process.kill(); process.wait()
        reconcile()


def selection(ids, direction):
    if direction not in storage.DIRECTIONS: raise ValueError('Reading direction must be rtl or ltr.')
    all_chapters = {c['id']: (m, c) for m in catalog()['manga'] for c in m['chapters']}
    if not ids or len(set(ids)) != len(ids) or any(i not in all_chapters for i in ids): raise ValueError('Choose valid chapter folders.')
    selected = [all_chapters[i] for i in ids]
    if len({m['id'] for m,c in selected}) != 1: raise ValueError('Choose chapters from one manga per detection job.')
    result = []
    for manga, chapter in sorted(selected, key=lambda pair: chapter_sort_key(pair[1]['name'])):
        if chapter['pending']: raise ValueError('Finish interrupted renumbering before detection.')
        folder = renumbering.folder_path(chapter['folder'])
        result.append({'id': chapter['id'], 'manga_id': manga['id'], 'manga_folder': manga['folder'], 'manga': manga['name'], 'folder': chapter['folder'], 'name': folder.name, 'direction': direction, 'status': 'pending', 'pages': [p.name for p in renumbering.images(folder)], 'results': [], 'elapsed_seconds': 0})
    return result


def launch(job):
    global process, active_id
    from app.tasks import lock, ensure_idle
    with lock:
        reconcile(); ensure_idle()
        if not ready()['ready']: raise ValueError(ready()['message'])
        # Re-read source lists on every start/resume. Content hashes are checked by the worker.
        refreshed = selection([c['id'] for c in job['chapters']], job['direction'])
        previous = {c['id']: c for c in job['chapters']}
        for c in refreshed: c['elapsed_seconds'] = previous[c['id']].get('elapsed_seconds', 0)
        job['chapters'] = refreshed
        worker_lock().close()
        job.update(status='running', message='Checking saved pages…', current_page=None, run_started_at=time.time(), elapsed_base=job.get('elapsed_seconds', 0))
        save(job)
        stop_path(job['id']).unlink(missing_ok=True)
        gate = job_path(job['id']).with_suffix('.start'); gate.unlink(missing_ok=True)
        log_path = job_path(job['id']).with_suffix('.log')
        try:
            with log_path.open('ab') as log:
                process = subprocess.Popen([sys.executable, '-m', 'app.detection_worker', str(job_path(job['id']))], cwd=storage.ROOT, stdout=log, stderr=subprocess.STDOUT)
            active_id = job['id']
            job['pid'] = process.pid; save(job); gate.touch()
        except Exception:
            if process is not None and process.poll() is None: process.terminate(); process.wait()
            process = active_id = None
            job.update(status='failed', message='Could not start the detection worker.'); save(job)
            raise
        return summary(job)


class Selection(BaseModel):
    chapter_ids: list[str] = Field(min_length=1, max_length=2000)
    direction: str = 'rtl'


@router.get('/catalog')
def catalog_api():
    try: return catalog()
    except (OSError, ValueError) as exc: raise HTTPException(400, str(exc))


@router.get('/jobs')
def jobs_api():
    from app.tasks import lock
    with lock:
        reconcile()
        return [summary(storage.read_json(p)) for p in sorted((base() / 'jobs').glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True)]


@router.post('/jobs')
def start_api(body: Selection):
    from app.tasks import lock, ensure_idle
    try:
        with lock:
            reconcile(); ensure_idle()
            if not ready()['ready']: raise ValueError(ready()['message'])
            chapters = selection(body.chapter_ids, body.direction)
            job = {'id': uuid.uuid4().hex, 'status': 'pending', 'direction': body.direction, 'model': MODEL_ID, 'revision': REVISION, 'chapters': chapters, 'elapsed_seconds': 0}
            result = launch(job)
            settings = storage.read_json(base() / 'settings.json', {}); settings[chapters[0]['manga_folder']] = body.direction
            storage.write_json(base() / 'settings.json', settings)
            return result
    except (FileExistsError, RuntimeError) as exc: raise HTTPException(409, str(exc))
    except (OSError, ValueError) as exc: raise HTTPException(400, str(exc))


@router.post('/jobs/{ident}/resume')
def resume_api(ident: str):
    try: return launch(load(ident))
    except (FileExistsError, RuntimeError) as exc: raise HTTPException(409, str(exc))
    except (OSError, ValueError) as exc: raise HTTPException(400, str(exc))


@router.post('/jobs/{ident}/stop')
def stop_api(ident: str):
    from app.tasks import lock
    try:
        with lock:
            reconcile(); job = load(ident)
            if job['status'] in ('running', 'stopping'):
                stop_path(ident).touch()
                job.update(status='stopping', message='Finishing the current page before stopping…')
            return summary(job)
    except (OSError, ValueError) as exc: raise HTTPException(400, str(exc))


@router.post('/jobs/{ident}/chapters/{index}/open-folder')
def folder_api(ident: str, index: int):
    try:
        job = load(ident)
        if not 0 <= index < len(job['chapters']): raise ValueError('Chapter not found.')
        folder = output(job['chapters'][index])
        if not folder.is_dir(): raise ValueError('No output folder yet. Wait for the worker to start.')
        open_system_folder(folder)
        return {'opened': True}
    except (OSError, ValueError, subprocess.SubprocessError) as exc: raise HTTPException(400, str(exc))


@router.get('/jobs/{ident}/chapters/{index}/previews/{page_index}')
def preview_api(ident: str, index: int, page_index: int):
    try:
        job = load(ident)
        if not 0 <= index < len(job['chapters']): raise ValueError('Chapter not found.')
        c = job['chapters'][index]
        if not 0 <= page_index < len(c['pages']): raise ValueError('Page not found.')
        name = c['pages'][page_index]
        if name != PurePosixPath(name).name or '\\' in name: raise ValueError('Invalid page.')
        target = output(c) / 'previews' / (name + '.jpg')
        result = next((r for r in c.get('results', []) if r['page'] == name), None)
        if not result or not result.get('preview'): raise ValueError('Preview not available yet.')
        metadata = target.with_suffix('.metadata.json')
        stamp = result['signature']
        if metadata.is_symlink(): raise ValueError('Unsafe preview metadata.')
        if storage.read_json(metadata, {}).get('signature') != stamp or not target.is_file():
            target = output(c) / 'history/previews' / (sha256(target.name.encode()).hexdigest()[:16] + '.' + stamp + '.jpg')
        if target.is_symlink() or not target.is_file(): raise ValueError('Preview not available yet.')
        return FileResponse(target, media_type='image/jpeg', headers={'Cache-Control': 'no-store'})
    except (OSError, ValueError) as exc: raise HTTPException(404, str(exc))
