"""Restart-safe image downloads. No panel detection or media conversion."""
from app import errors
import asyncio
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import re
import os
import subprocess
import sys
import threading
import time
import uuid
import math

import httpx
from PIL import Image
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app import storage, download_sources as sources, download_requests as requests
from app.chapter_names import chapter_folder_name

router = APIRouter(prefix='/api/downloader')
lock = threading.RLock()
active = None
stop_event = threading.Event()
thread = None


def base(): return storage.DATA / 'downloader'


def record_path(kind, ident):
    if not re.fullmatch(r'[a-f0-9]{32}', ident): raise ValueError('Invalid download ID.')
    return base() / kind / (ident + '.json')


def load(kind, ident):
    value = storage.read_json(record_path(kind, ident))
    if not value: raise ValueError('Download record not found.')
    return value


def save(job):
    job['updated_at'] = time.time()
    storage.write_json(record_path('jobs', job['id']), job)


def cleared_history():
    value = storage.read_json(base() / 'history.json')
    if value is None: return {}
    if not isinstance(value, dict) or any(not re.fullmatch(r'[a-f0-9]{32}', key) or not isinstance(stamp,(int,float)) or not math.isfinite(stamp) or stamp<0 for key,stamp in value.items()):
        raise ValueError('Saved download history settings are invalid. Download records and manga files are intact; keep history.json for repair.')
    return value


def set_cleared(ident, cleared):
    job = load('jobs', ident)
    if cleared and (job.get('status') in ('running','stopping') or active and active['id']==ident):
        raise FileExistsError('Stop this download before clearing it from the list. Saved images will be kept.')
    history = cleared_history()
    if cleared: history[ident] = time.time()
    else: history.pop(ident, None)
    storage.write_json(base() / 'history.json', history)
    return {'id': ident, 'cleared': cleared, 'files_deleted': False}


def destination(relative):
    path = storage.ROOT / relative
    if not path.resolve().is_relative_to((storage.ROOT / 'manga').resolve()):
        raise ValueError('Download destination must remain inside manga/.')
    current = storage.ROOT
    for part in Path(relative).parts:
        if part in ('..', '.') or part.startswith('.'): raise ValueError('Unsafe download destination.')
        current /= part
        if current.is_symlink(): raise ValueError('Download destination cannot contain symbolic links.')
    return path


def slug(name):
    return re.sub(r'[^\w .()-]+', '-', name, flags=re.UNICODE).strip(' .-')[:80] or 'Manga'


def verified(path, entry):
    if not entry or path.is_symlink() or not path.is_file(): return False
    return path.stat().st_size == entry.get('bytes') and sha256(path.read_bytes()).hexdigest() == entry.get('sha256')


def summary(job):
    value = deepcopy(job)
    value['chapters'].sort(key=sources.chapter_sort)
    chapters = value['chapters']
    value['total_pages'] = sum(len(c.get('pages', [])) for c in chapters)
    value['verified_pages'] = sum(sum(p.get('status') == 'complete' for p in c.get('pages', [])) for c in chapters)
    value['failed_pages'] = sum(sum(p.get('status') == 'failed' for p in c.get('pages', [])) for c in chapters)
    value['discovered_chapters'] = sum(c.get('discovered', False) for c in chapters)
    value['completed_chapters'] = sum(c.get('status') == 'complete' for c in chapters)
    value['resume_blocked'] = any((destination(c['folder']) / '.renumbering.json').exists() for c in chapters)
    value['elapsed_seconds'] = round(value.get('elapsed_seconds', 0) + (time.monotonic() - active['started'] if active and active['id'] == value['id'] else 0), 1)
    for chapter in chapters:
        chapter['total_pages'] = len(chapter.get('pages', []))
        chapter['verified_pages'] = sum(p.get('status') == 'complete' for p in chapter.get('pages', []))
        chapter['errors'] = [{'page': i + 1, 'reason': p.get('error', '')} for i, p in enumerate(chapter.get('pages', [])) if p.get('status') == 'failed']
        chapter.pop('pages', None)
    return value


def recover():
    for path, job in errors.recovery_records(base() / 'jobs','downloader'):
        if job.get('status') in ('running', 'stopping'):
            job.update(status='paused', message='Interrupted. Resume to verify saved pages and continue.')
            save(job)


def shutdown():
    stop_event.set()
    if thread and thread.is_alive(): thread.join(timeout=25)


async def discover(url):
    result = await sources.catalog(url)
    result['id'] = sha256(result['url'].encode()).hexdigest()[:32]
    storage.write_json(record_path('catalogs', result['id']), result)
    return result


def create(catalog_id, chapter_ids):
    catalog = load('catalogs', catalog_id)
    chosen = set(chapter_ids)
    chapters = sorted((c for c in catalog['chapters'] if c['id'] in chosen), key=sources.chapter_sort)
    if not chapters or len(chapters) != len(chosen): raise ValueError('Choose valid chapters from the loaded manga.')
    folder = f'manga/{slug(catalog["title"])}-{catalog["host"].split(".")[0]}-{catalog_id[:8]}'
    destination(folder)
    names = [chapter_folder_name(chapter['name']) for chapter in chapters]
    if len({name.casefold() for name in names}) != len(names):
        raise ValueError('Multiple selected releases have the same chapter number. Select one release per chapter; no suffixes will be added.')
    ident = uuid.uuid4().hex
    job = {'id': ident, 'catalog_id': catalog_id, 'title': catalog['title'], 'host': catalog['host'], 'source': catalog['source'], 'source_url': catalog['url'], 'folder': folder, 'status': 'paused', 'message': 'Ready to download.', 'elapsed_seconds': 0, 'chapters': []}
    for chapter in chapters:
        # IDs remain in the manifest; readable folders never get generated suffixes.
        job['chapters'].append({**chapter, 'name': chapter_folder_name(chapter['name']), 'folder': folder + '/' + chapter_folder_name(chapter['name']), 'status': 'pending', 'pages': []})
    save(job)
    return job


def wait_notice(job, host, until, reported):
    if until is None:
        job.pop('source_wait', None)
        job['message'] = 'Continuing ' + job.get('current_request', 'selected downloads') + '.'
    else:
        job['source_wait'] = {'host': host, 'until': until, 'reported': reported}
        job['message'] = f'{host} is limiting requests. Waiting until {time.strftime("%H:%M:%S UTC", time.gmtime(until))}; this download will continue automatically unless stopped.'
    save(job)


async def request_with_cooldown(job, label, call):
    job['current_request'] = label
    for attempt in range(3):
        if stop_event.is_set(): raise requests.DownloadStopped()
        try:
            return await call()
        except requests.SourceCooldown as exc:
            wait_notice(job, exc.host, exc.reset, exc.reported)
            job['source_wait']['retry'] = attempt + 1
            save(job)
            if attempt == 2: raise
            # Never retry early. Stop remains responsive during long cooldowns.
            while time.time() < exc.reset:
                if stop_event.is_set(): raise requests.DownloadStopped()
                await asyncio.sleep(min(.25, exc.reset-time.time()))
            wait_notice(job, exc.host, None, False)


def verify_chapter(chapter):
    for page in chapter.get('pages', []):
        path = destination(chapter['folder'] + '/' + page['file']) if page.get('file') else None
        page['status'] = 'complete' if path and verified(path, page) else 'pending'
        if page['status'] == 'complete':
            page.pop('error', None); page.pop('error_details', None)
    return bool(chapter.get('pages')) and all(p['status'] == 'complete' for p in chapter['pages'])


async def transfer(job):
    with requests.request_context(stop_event.is_set, lambda host, until, reported: wait_notice(job, host, until, reported)):
        try:
            await transfer_requests(job)
        except requests.DownloadStopped:
            return


async def transfer_requests(job):
    # Repair legacy queue order by moving chapter records together with their
    # existing paths, page hashes and progress. Never reorder pages in a chapter.
    job['chapters'].sort(key=sources.chapter_sort)
    async with httpx.AsyncClient(timeout=httpx.Timeout(20, connect=10), follow_redirects=False) as client:
        # Hash-check local work first. Complete chapters need no source request,
        # even if a previous page-list refresh failed or the app was interrupted.
        complete = set()
        for chapter in job['chapters']:
            if stop_event.is_set(): return
            if verify_chapter(chapter):
                chapter.update(status='complete', discovered=True)
                chapter.pop('error', None); chapter.pop('error_details', None)
                complete.add(chapter['id'])
        save(job)
        for chapter in job['chapters']:
            if stop_event.is_set(): return
            if chapter['id'] in complete: continue
            job['message'] = f'Loading page list: {chapter["name"]}'
            save(job)
            try:
                urls = await request_with_cooldown(job, 'page list for '+chapter['name'], lambda: sources.image_urls(client, chapter, job['host']))
                old = chapter['pages']
                if old and [u.split('?', 1)[0] for u in urls] != [p['url'].split('?', 1)[0] for p in old]:
                    raise ValueError('Source page order changed. Saved images were kept; start a new selection after reviewing the source.')
                chapter['pages'] = [{**(old[i] if old else {'status': 'pending'}), 'url': url} for i, url in enumerate(urls)]
                chapter['discovered'] = True
                chapter.pop('error', None); chapter.pop('error_details', None)
            except requests.DownloadStopped:
                raise
            except Exception as exc:
                detail = errors.describe(exc, 'downloader', 'Load chapter page list', chapter['name'])
                chapter.update(status='failed', error=detail['message'], error_details=detail, discovered=False)
                if isinstance(exc, requests.SourceCooldown):
                    save(job); raise
            save(job)
        for chapter in job['chapters']:
            if stop_event.is_set(): return
            if chapter['id'] in complete or not chapter.get('discovered'): continue
            folder = destination(chapter['folder'])
            folder.mkdir(parents=True, exist_ok=True)
            chapter['status'] = 'running'
            save(job)
            # Sequential, paced requests prevent queued images from continuing
            # to hammer a source after one request receives HTTP 429.
            for index, page in enumerate(chapter['pages']):
                if stop_event.is_set(): return
                if page.get('status') == 'complete': continue
                for attempt in range(3):
                    if stop_event.is_set(): return
                    page.update(status='downloading', error='')
                    job['message'] = f'{chapter["name"]} · downloading page {index + 1}'
                    save(job)
                    temporary = folder / f'.{index + 1:03d}-{job["id"]}.part'
                    try:
                        data = await request_with_cooldown(job, chapter['name']+' / page '+str(index+1), lambda: sources.fetch(client, page['url'], f'https://{job["host"]}/', sources.MAX_IMAGE))
                        with Image.open(BytesIO(data)) as image:
                            ext = {'JPEG': '.jpg', 'PNG': '.png', 'WEBP': '.webp'}.get(image.format)
                            if not ext or image.width * image.height > 60_000_000: raise ValueError('Unsupported or oversized page image.')
                            image.verify()
                        with Image.open(BytesIO(data)) as image: image.load()
                        filename = f'{index + 1:03d}{ext}'
                        target = destination(chapter['folder'] + '/' + filename)
                        if target.exists() and page.get('file') != filename:
                            raise ValueError('An untracked file exists at this destination; it was not overwritten.')
                        with temporary.open('wb') as stream:
                            stream.write(data); stream.flush(); os.fsync(stream.fileno())
                        page.update(file=filename, bytes=len(data), sha256=sha256(data).hexdigest())
                        save(job)
                        temporary.replace(target)
                        page.update(status='complete', error=''); page.pop('error_details', None)
                        save(job)
                        break
                    except requests.DownloadStopped:
                        raise
                    except Exception as exc:
                        detail = errors.describe(exc, 'downloader', 'Download page', chapter['name'] + ' / page ' + str(index + 1))
                        page.update(status='failed', error=detail['message'], error_details=detail)
                        save(job)
                        if isinstance(exc, requests.SourceCooldown):
                            chapter['status']='failed';save(job);raise
                        if detail['code'] in ('disk_full','permission_denied','file_in_use','conflict','source_blocked','source_rate_limit'): break
                        if attempt < 2:
                            for _ in range(10 * (attempt + 1)):
                                if stop_event.is_set(): return
                                await asyncio.sleep(.1)
                    finally:
                        temporary.unlink(missing_ok=True)
            chapter['status'] = 'complete' if all(p.get('status') == 'complete' for p in chapter['pages']) else 'failed'
            save(job)

def run(job):
    global active
    try:
        asyncio.run(transfer(job))
        with lock:
            if stop_event.is_set(): job.update(status='paused', message='Stopped. Verified pages are safe; resume to continue.')
            elif all(c['status'] == 'complete' for c in job['chapters']):
                job.update(status='complete', message='All selected chapters downloaded and verified.')
                job.pop('source_wait', None)
            else: job.update(status='failed', message='Some pages or chapters failed. Resume to retry; completed images are preserved.')
    except requests.SourceCooldown as exc:
        with lock:
            job['source_wait'] = {**job.get('source_wait', {}), 'host': exc.host, 'until': exc.reset, 'reported': exc.reported}
            job.update(status='paused', message=str(exc)+' Resume the original download after this time. Completed images are safe.')
            job['error_details']=errors.describe(exc,'downloader','Wait for source cooldown',job.get('current_request'))
    except Exception as exc:
        with lock:
            job['status']='failed'; errors.failure(job, exc, 'downloader', 'Download selected chapters', job.get('folder'))
    finally:
        with lock:
            job['elapsed_seconds'] += time.monotonic() - active['started']
            try: errors.save_final(save, job, 'downloader')
            finally: active = None


def start(job):
    global active, thread
    from app.tasks import lock as task_lock, ensure_idle
    with task_lock, lock:
        ensure_idle()
        job['chapters'].sort(key=sources.chapter_sort)
        for chapter in job['chapters']:
            if (destination(chapter['folder']) / '.renumbering.json').exists():
                raise ValueError('This chapter has been cleaned or renumbered. Download resume is disabled to protect your edits.')
        # A folder keeps its original owner, plus a pointer to the latest saved
        # chapter state. Different selections can reuse it without broadening
        # the selection or rewriting historical jobs.
        claims = []
        hydrated = []
        for chapter in job['chapters']:
            folder = destination(chapter['folder'])
            owner = folder / '.download-owner.json'
            current = storage.read_json(owner)
            cached = deepcopy(chapter)
            if current:
                donor = load('jobs', current.get('state_job_id', current['job_id']))
                matches = [c for c in donor['chapters'] if c['id']==chapter['id'] and c['folder']==chapter['folder'] and c['url']==chapter['url']]
                if donor['catalog_id'] != job['catalog_id'] or len(matches)!=1:
                    raise ValueError('This chapter folder belongs to a different source or release. Existing files were kept; select the original release or use another destination.')
                cached = deepcopy(matches[0])
            if not current and folder.exists() and any(folder.iterdir()): raise ValueError('Destination contains existing files. They were left unchanged.')
            hydrated.append(cached)
            claims.append(current or {'job_id':job['id']})
        job['chapters'] = hydrated
        # Save the reusable snapshot before publishing a pointer to it. A crash
        # between claims leaves every pointer referring to a complete record.
        save(job)
        for chapter, claim in zip(job['chapters'], claims):
            folder = destination(chapter['folder'])
            folder.mkdir(parents=True, exist_ok=True)
            storage.write_json(folder / '.download-owner.json', {**claim, 'state_job_id':job['id']})
        stop_event.clear()
        if job['id'] in cleared_history(): set_cleared(job['id'], False)
        job.update(status='running', message='Loading selected chapter page lists…')
        save(job)
        active = {'id': job['id'], 'started': time.monotonic(), 'job': job}
        thread = threading.Thread(target=run, args=(job,), daemon=True)
        try: thread.start()
        except Exception as exc:
            active=None; job['status']='failed'; errors.failure(job, exc, 'downloader', 'Start download worker'); errors.save_final(save, job, 'downloader'); raise
        return summary(job)


class SourceInput(BaseModel):
    url: str = Field(min_length=1, max_length=2048)


class DownloadInput(BaseModel):
    catalog_id: str
    chapter_ids: list[str] = Field(min_length=1, max_length=2000)


@router.post('/catalog')
async def catalog_api(body: SourceInput):
    try: return await discover(body.url)
    except Exception as exc: raise HTTPException(400, str(exc))


@router.get('/jobs')
def jobs_api():
    with lock:
        history = cleared_history()
        return [{**job, 'cleared_at': history.get(job['id'])} for job in reversed(errors.job_list(base()/'jobs','downloader',summary))]


@router.post('/history/clear')
def clear_history_api():
    with lock:
        history = cleared_history(); count = 0
        for job in errors.job_list(base()/'jobs','downloader',summary):
            if not isinstance(job.get('id'),str) or not re.fullmatch(r'[a-f0-9]{32}',job['id']): continue
            if job.get('status') in ('running','stopping') or active and active['id']==job['id']: continue
            if job['id'] not in history: history[job['id']] = time.time(); count += 1
        storage.write_json(base() / 'history.json', history)
        return {'cleared': count, 'files_deleted': False}


@router.post('/jobs/{ident}/clear')
def clear_job_api(ident: str):
    with lock:
        try: return set_cleared(ident, True)
        except FileExistsError as exc: raise HTTPException(409, str(exc))
        except ValueError as exc: raise HTTPException(400, str(exc))


@router.post('/jobs/{ident}/restore')
def restore_job_api(ident: str):
    with lock:
        try: return set_cleared(ident, False)
        except ValueError as exc: raise HTTPException(400, str(exc))


@router.post('/jobs')
def new_api(body: DownloadInput):
    from app.tasks import lock as task_lock, ensure_idle
    try:
        with task_lock, lock:
            ensure_idle()
            # Always snapshot exactly this selection. Chapter state comes from
            # each folder's latest saved download, including cleared history.
            return start(create(body.catalog_id, body.chapter_ids))
    except FileExistsError as exc: raise HTTPException(409, str(exc))
    except ValueError as exc: raise HTTPException(400, str(exc))


@router.post('/jobs/{ident}/resume')
def resume_api(ident: str):
    try: return start(load('jobs', ident))
    except FileExistsError as exc: raise HTTPException(409, str(exc))
    except ValueError as exc: raise HTTPException(400, str(exc))


@router.post('/jobs/{ident}/stop')
def stop_api(ident: str):
    with lock:
        try: job = load('jobs', ident)
        except ValueError as exc: raise HTTPException(404, str(exc))
        if active and active['id'] == ident:
            stop_event.set()
            job = active['job']
            job.update(status='stopping', message='Stopping after current requests finish…')
            save(job)
        return summary(job)


@router.post('/jobs/{ident}/open-folder')
def open_folder_api(ident: str):
    try:
        job = load('jobs', ident)
        relative = job['chapters'][0]['folder'] if len(job['chapters']) == 1 else job['folder']
        folder = destination(relative)
        if not folder.is_dir():
            raise ValueError('The download folder does not exist yet. Start the download first.')
        if sys.platform == 'darwin':
            subprocess.run(['open', str(folder)], check=True, timeout=10)
        elif sys.platform == 'win32':
            os.startfile(str(folder))
        else:
            raise ValueError('Opening folders is supported on macOS and Windows.')
        return {'opened': True, 'folder': relative}
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except (OSError, subprocess.SubprocessError) as exc:
        raise HTTPException(500, 'Could not open the system file manager. Please try again.') from exc
