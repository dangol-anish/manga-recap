"""Persistent chapter queues using the existing, isolated audio/video workers."""
from app import errors
import copy
import threading
import time
import uuid
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from app import storage

router = APIRouter(prefix='/api/batch-work')
lock = threading.RLock()
active = None
worker = None
cancel = threading.Event()


def engine(kind):
    from app import speech_stage6, video_stage7
    if kind == 'audio': return speech_stage6
    if kind == 'video': return video_stage7
    raise ValueError('Choose audio or video.')


def path(kind):
    engine(kind)
    target = storage.DATA / 'batch-work' / (kind + '.json')
    if target.is_symlink() or target.parent.is_symlink(): raise ValueError('Unsafe chapter queue.')
    return target


def save(job): storage.write_json(path(job['kind']), job)


def recover():
    for kind in ('audio','video'):
        try:
            job=storage.read_json(path(kind))
            if job and job['status'] in ('running','stopping'):
                job['status']='paused'
                for item in job['items']:
                    if item['status']=='running':item.update(status='waiting',message='Stopped. Resume to continue.')
                save(job)
        except (ValueError,OSError,KeyError,TypeError) as exc:errors.warning(exc,'speech' if kind=='audio' else 'cinema','Recover selected chapter queue',path(kind))


def snapshot(kind, mid, chapter):
    module = engine(kind)
    state = module.status(mid, chapter)
    return state, state['revision' if kind == 'audio' else 'render_token']


def view(kind):
    with lock:
        job = copy.deepcopy(storage.read_json(path(kind)))
    if not job: return None
    for item in job['items']:
        if item['status'] != 'running': continue
        try:
            state, _ = snapshot(kind, job['manga_id'], item['chapter'])
            current = state.get('job') or {}
            if kind == 'audio':
                item['progress'] = state['ready'] / max(1, state['spoken'])
                item['message'] = f"{state['ready']} of {state['spoken']} voice clips ready"
            else:
                item['progress'] = current.get('progress', 0)
                item['message'] = current.get('message', 'Creating video…')
        except (ValueError, OSError): pass
    return job


def run(job):
    global active
    module = engine(job['kind'])
    try:
        for item in job['items']:
            if cancel.is_set(): break
            if item['status'] == 'complete': continue
            with lock:
                item.update(status='running', message='Starting…', progress=0); save(job)
            try:
                state, token = snapshot(job['kind'], job['manga_id'], item['chapter'])
                if token != item['token']:
                    raise FileExistsError('This chapter changed. Start a new selection to use the updated version.')
                if job['kind'] == 'audio' and state['ready'] == state['spoken']:
                    if state['full']['available'] and not state['full']['ready']:
                        module.build_full(job['manga_id'], item['chapter'], token)
                elif job['kind'] == 'video' and state['ready']:
                    pass
                else:
                    if cancel.is_set(): break
                    child = module.start(job['manga_id'], item['chapter'], token)
                    while module.worker and module.worker.is_alive():
                        if cancel.is_set():
                            if job['kind'] == 'audio': module.stop_event.set()
                            else: module.cancel.set()
                        module.worker.join(.25)
                    saved = storage.read_json(module.job_path(child['id']))
                    if saved['status'] != 'complete':
                        raise RuntimeError(saved.get('message', 'Stopped before finishing.'))
                    if job['kind'] == 'audio':
                        finished, _ = snapshot(job['kind'], job['manga_id'], item['chapter'])
                        if finished['full']['available'] and not finished['full']['ready']:
                            if cancel.is_set(): raise InterruptedError('Stopped before joining the audio clips. Resume to finish.')
                            module.build_full(job['manga_id'], item['chapter'], token)
                with lock:
                    item.update(status='complete', message='Ready', progress=1); save(job)
            except Exception as exc:
                with lock:
                    item.update(status='waiting' if cancel.is_set() else 'failed')
                    errors.failure(item,exc,'speech' if job['kind']=='audio' else 'cinema','Create selected chapter',item['chapter']);save(job)
        with lock:
            for item in job['items']:
                if item['status'] == 'running': item.update(status='waiting', message='Stopped. Resume to continue.')
            remaining = any(i['status'] != 'complete' for i in job['items'])
            job['status'] = 'paused' if cancel.is_set() and remaining else 'failed' if remaining else 'complete'
            save(job)
    except Exception as exc:
        job['status']='failed';errors.failure(job,exc,'speech' if job['kind']=='audio' else 'cinema','Create selected chapter queue')
    finally:
        with lock:
            try:
                if job['status'] in ('running','stopping'):job['status']='paused'
                errors.save_final(save,job,'speech' if job['kind']=='audio' else 'cinema')
            finally:active=None


def launch(job):
    global active, worker
    cancel.clear(); job['status'] = 'running'; save(job); active = job
    worker = threading.Thread(target=run, args=(job,), daemon=True)
    try: worker.start()
    except Exception:
        active = None; job['status'] = 'paused'; save(job); raise
    return copy.deepcopy(job)


def start(kind, mid, chapters):
    from app.tasks import lock as task_lock, ensure_idle
    with task_lock, lock:
        ensure_idle(); engine(kind)
        if len(chapters) != len(set(chapters)): raise ValueError('Select each chapter only once.')
        from app.chapter_names import chapter_sort_key
        items = []
        for chapter in sorted(chapters, key=chapter_sort_key):
            _, token = snapshot(kind, mid, chapter)
            items.append(dict(chapter=chapter, token=token, status='waiting', progress=0, message='Waiting'))
        return launch(dict(id=uuid.uuid4().hex, kind=kind, manga_id=mid, created_at=time.time(), items=items))


def resume(kind, ident):
    from app.tasks import lock as task_lock, ensure_idle
    with task_lock, lock:
        ensure_idle(); job = storage.read_json(path(kind))
        if not job or job['id'] != ident or job['status'] not in ('paused', 'failed'):
            raise ValueError('This selection cannot be resumed.')
        for item in job['items']:
            if item['status'] == 'complete': continue
            _, token = snapshot(kind, job['manga_id'], item['chapter'])
            if token != item['token']: raise FileExistsError(item['chapter'] + ' changed. Start a new selection for the updated chapter.')
            item.update(status='waiting', message='Waiting')
        return launch(job)


def stop(kind, ident):
    with lock:
        if active is None or active['id'] != ident or active['kind'] != kind:
            raise ValueError('This selection is not running.')
        cancel.set(); active['status'] = 'stopping'; save(active)
        return copy.deepcopy(active)


def shutdown():
    cancel.set()
    if worker and worker.is_alive(): worker.join(140)


class Selection(BaseModel):
    manga_id: str = Field(min_length=1, max_length=128)
    chapters: list[str] = Field(min_length=1, max_length=500)


def error(exc): return HTTPException(409 if isinstance(exc, FileExistsError) else 400, str(exc))


@router.get('/{kind}')
def status_api(kind: str):
    try: return view(kind)
    except (ValueError, OSError) as exc: raise error(exc)


@router.post('/{kind}')
def start_api(kind: str, body: Selection):
    try: return start(kind, body.manga_id, body.chapters)
    except (ValueError, OSError) as exc: raise error(exc)


@router.post('/{kind}/{ident}/{action}')
def action_api(kind: str, ident: str, action: str):
    try:
        if action == 'stop': return stop(kind, ident)
        if action == 'resume': return resume(kind, ident)
        raise ValueError('Unknown action.')
    except (ValueError, OSError) as exc: raise error(exc)
