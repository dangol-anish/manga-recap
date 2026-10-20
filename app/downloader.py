"""Restart-safe image downloads. No panel detection or media conversion."""
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

import httpx
from PIL import Image
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app import storage, download_sources as sources
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
    for path in (base() / 'jobs').glob('*.json'):
        job = storage.read_json(path)
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
    chapters = [c for c in catalog['chapters'] if c['id'] in chosen]
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


async def transfer(job):
    async with httpx.AsyncClient(timeout=httpx.Timeout(20, connect=10), follow_redirects=False) as client:
        # Discover all selected chapters first, so the download progress has a real denominator.
        for chapter in job['chapters']:
            if stop_event.is_set(): return
            job['message'] = f'Loading page list: {chapter["name"]}'
            save(job)
            try:
                urls = await sources.image_urls(client, chapter, job['host'])
                old = chapter['pages']
                if old and [u.split('?', 1)[0] for u in urls] != [p['url'].split('?', 1)[0] for p in old]:
                    raise ValueError('Source page order changed. Saved images were kept; start a new selection after reviewing the source.')
                chapter['pages'] = [{**(old[i] if old else {}), 'url': url, 'status': 'pending'} for i, url in enumerate(urls)]
                chapter['discovered'] = True
                chapter.pop('error', None)
            except Exception as exc:
                chapter.update(status='failed', error=str(exc), discovered=False)
            save(job)
        for chapter in job['chapters']:
            if stop_event.is_set(): return
            if not chapter.get('discovered'): continue
            folder = destination(chapter['folder'])
            folder.mkdir(parents=True, exist_ok=True)
            chapter['status'] = 'running'
            for index, page in enumerate(chapter['pages']):
                if stop_event.is_set(): return
                path = destination(chapter['folder'] + '/' + page['file']) if page.get('file') else None
                page['status'] = 'complete' if path and verified(path, page) else 'pending'
            save(job)
            # Three requests at once keeps memory/network usage modest on a weak laptop.
            sem = asyncio.Semaphore(3)
            async def one(index, page):
                async with sem:
                    if stop_event.is_set() or page.get('status') == 'complete': return
                    for attempt in range(3):
                        if stop_event.is_set(): return
                        with lock:
                            page.update(status='downloading', error='')
                            job['message'] = f'{chapter["name"]} · downloading page {index + 1}'
                            save(job)
                        temporary = folder / f'.{index + 1:03d}-{job["id"]}.part'
                        try:
                            data = await sources.fetch(client, page['url'], f'https://{job["host"]}/', sources.MAX_IMAGE)
                            with Image.open(BytesIO(data)) as image:
                                ext = {'JPEG': '.jpg', 'PNG': '.png', 'WEBP': '.webp'}.get(image.format)
                                if not ext or image.width * image.height > 60_000_000: raise ValueError('Unsupported or oversized page image.')
                                image.verify()
                            # Verify the pixel stream too; an HTML error page never counts as an image.
                            with Image.open(BytesIO(data)) as image: image.load()
                            filename = f'{index + 1:03d}{ext}'
                            target = destination(chapter['folder'] + '/' + filename)
                            if target.exists() and page.get('file') != filename:
                                raise ValueError('An untracked file exists at this destination; it was not overwritten.')
                            # A chapter folder belongs to one resumable job, claimed before transfer.
                            temporary.write_bytes(data)
                            with temporary.open('rb') as stream:
                                import os
                                os.fsync(stream.fileno())
                            # Persist the intended file/hash before publishing it. If the server
                            # stops between rename and completion, resume can adopt that exact file.
                            with lock:
                                page.update(file=filename, bytes=len(data), sha256=sha256(data).hexdigest())
                                save(job)
                            temporary.replace(target)
                            with lock:
                                page.update(status='complete', error='')
                                save(job)
                            return
                        except Exception as exc:
                            with lock:
                                page.update(status='failed', error=str(exc))
                                save(job)
                            if attempt < 2:
                                for _ in range(10 * (attempt + 1)):
                                    if stop_event.is_set(): return
                                    await asyncio.sleep(.1)
                        finally:
                            temporary.unlink(missing_ok=True)
            await asyncio.gather(*(one(i, p) for i, p in enumerate(chapter['pages'])))
            chapter['status'] = 'complete' if all(p.get('status') == 'complete' for p in chapter['pages']) else 'failed'
            save(job)


def run(job):
    global active
    try:
        asyncio.run(transfer(job))
        with lock:
            if stop_event.is_set(): job.update(status='paused', message='Stopped. Verified pages are safe; resume to continue.')
            elif all(c['status'] == 'complete' for c in job['chapters']): job.update(status='complete', message='All selected chapters downloaded and verified.')
            else: job.update(status='failed', message='Some pages or chapters failed. Resume to retry; completed images are preserved.')
    except Exception as exc:
        with lock: job.update(status='failed', message=str(exc))
    finally:
        with lock:
            job['elapsed_seconds'] += time.monotonic() - active['started']
            save(job)
            active = None


def start(job):
    global active, thread
    from app.tasks import lock as task_lock, ensure_idle
    with task_lock, lock:
        ensure_idle()
        for chapter in job['chapters']:
            if (destination(chapter['folder']) / '.renumbering.json').exists():
                raise ValueError('This chapter has been cleaned or renumbered. Download resume is disabled to protect your edits.')
        # Preflight every destination before claiming any folders.
        for chapter in job['chapters']:
            folder = destination(chapter['folder'])
            owner = folder / '.download-owner.json'
            current = storage.read_json(owner)
            if current and current['job_id'] != job['id']:
                raise ValueError('This chapter already has a download job. Resume that job in Recent downloads.')
            if not current and folder.exists() and any(folder.iterdir()): raise ValueError('Destination contains existing files. They were left unchanged.')
        for chapter in job['chapters']:
            folder = destination(chapter['folder'])
            folder.mkdir(parents=True, exist_ok=True)
            storage.write_json(folder / '.download-owner.json', {'job_id': job['id']})
        stop_event.clear()
        job.update(status='running', message='Loading selected chapter page lists…')
        save(job)
        active = {'id': job['id'], 'started': time.monotonic(), 'job': job}
        thread = threading.Thread(target=run, args=(job,), daemon=True)
        thread.start()
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
        return [summary(storage.read_json(p)) for p in sorted((base() / 'jobs').glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True)]


@router.post('/jobs')
def new_api(body: DownloadInput):
    from app.tasks import lock as task_lock, ensure_idle
    try:
        with task_lock, lock:
            ensure_idle()
            for path in (base() / 'jobs').glob('*.json'):
                previous = storage.read_json(path)
                if previous['catalog_id'] == body.catalog_id and {c['id'] for c in previous['chapters']} == set(body.chapter_ids):
                    return start(previous)
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
