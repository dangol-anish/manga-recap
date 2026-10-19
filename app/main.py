from contextlib import asynccontextmanager
import fcntl
import json
import subprocess
import sys
import threading
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.storage import ROOT, DATA, OUTPUTS, DIRECTIONS, chapters, pages, local_path, page_output, job_path, read_json, write_json
from app.validation import saved_page_errors
from app.authorization import processing_allowed

mutex = threading.RLock()
process = None
active_job = None


def reconcile():
    global process, active_job
    if process is not None and process.poll() is not None:
        job = read_json(active_job)
        if job['status'] == 'running':
            job.update(status='paused', message='Worker stopped; resume this chapter to continue.')
            write_json(active_job, job)
        process = active_job = None


def stop():
    global process, active_job
    reconcile()
    if process is None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
    job = read_json(active_job)
    if job['status'] != 'complete':
        job.update(status='paused', message='Stopped. Completed page JSONs are safe; resume to continue.')
        write_json(active_job, job)
    process = active_job = None


@asynccontextmanager
async def lifespan(app):
    DATA.mkdir(exist_ok=True)
    lock = open(DATA / 'server.lock', 'a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        raise RuntimeError('Another manga-recap server is already running.')
    # A separate worker can survive a forced server kill. Terminate that precise
    # repo worker before recovering its job, so two processes never write a page.
    for path in OUTPUTS.glob('*/*/_job.json'):
        job = read_json(path)
        if job['status'] == 'running':
            pid = job.get('pid')
            if pid:
                result = subprocess.run(['ps', '-p', str(pid), '-o', 'command='], capture_output=True, text=True)
                expected = f'{ROOT / ".venv/bin/python"} -m app.worker {path}'
                if result.stdout.strip() == expected:
                    import os, signal, time
                    os.kill(pid, signal.SIGTERM)
                    for _ in range(50):
                        try:
                            os.kill(pid, 0)
                        except ProcessLookupError:
                            break
                        time.sleep(0.1)
                    else:
                        try:
                            os.kill(pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
            job.update(status='paused', message='Interrupted by shutdown. Resume this chapter to continue.')
            write_json(path, job)
    try:
        yield
    finally:
        with mutex:
            stop()
        lock.close()


app = FastAPI(title='Manga Recap Library', lifespan=lifespan)
app.mount('/static', StaticFiles(directory=ROOT / 'app/static'), name='static')


@app.middleware('http')
async def local_requests(request: Request, call_next):
    # Prevent remote websites from issuing requests to this local app.
    from starlette.responses import JSONResponse
    host = request.headers.get('host', '').split(':')[0]
    if host not in {'127.0.0.1', 'localhost', 'testserver'}:
        return JSONResponse({'detail': 'Local access only.'}, status_code=403)
    origin = request.headers.get('origin')
    if origin and origin not in {f'http://{request.headers.get("host")}', f'https://{request.headers.get("host")}'}:
        return JSONResponse({'detail': 'Use this app from its local page.'}, status_code=403)
    return await call_next(request)


@app.get('/')
def library_page():
    return FileResponse(ROOT / 'app/static/index.html')


class MangaInput(BaseModel):
    folder: str = Field(min_length=1, max_length=4096)
    direction: str = 'rtl'


def library():
    return read_json(DATA / 'library.json', [])


def find_manga(manga_id):
    manga = next((m for m in library() if m['id'] == manga_id), None)
    if manga is None:
        raise HTTPException(404, 'Manga not found.')
    return manga


def find_chapter(manga, name):
    try:
        return next(p for p in chapters(manga) if p.name == name)
    except (StopIteration, ValueError):
        raise HTTPException(404, 'Chapter not found. Check that its folder still exists.')


@app.post('/api/manga', status_code=201)
def add_manga(body: MangaInput):
    with mutex:
        if body.direction not in DIRECTIONS:
            raise HTTPException(400, 'Direction must be rtl or ltr.')
        try:
            folder = local_path(body.folder.strip())
            manga = {'id': uuid.uuid4().hex, 'name': folder.name, 'folder': str(folder.relative_to(ROOT)), 'direction': body.direction}
            if not chapters(manga):
                raise ValueError('No chapter folders containing PNG/JPG/WebP pages were found.')
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        entries = library()
        if any(m['folder'] == manga['folder'] for m in entries):
            raise HTTPException(409, 'That manga is already in your Library.')
        entries.append(manga)
        write_json(DATA / 'library.json', entries)
        return manga


@app.get('/api/library')
def get_library():
    with mutex:
        reconcile()
        entries = []
        for manga in library():
            item = dict(manga, chapters=[])
            try:
                for chapter in chapters(manga):
                    page_results = []
                    for page in pages(chapter):
                        output = page_output(manga, chapter.name, page.name)
                        saved = read_json(output)
                        if saved_page_errors(saved, page):
                            saved = None
                        page_results.append({'page': page.name, 'done': saved is not None, 'panels': len(saved['panels']) if saved else None, 'seconds': saved['seconds'] if saved else None})
                    job = read_json(job_path(manga, chapter.name), {})
                    flagged = {p['page']: p for p in job.get('flagged', [])}
                    for result in page_results:
                        result['flagged'] = result['page'] in flagged and not result['done']
                    done = sum(p['done'] for p in page_results)
                    item['chapters'].append({
                        'name': chapter.name, 'allowed': processing_allowed(manga['id'], chapter.name), 'total': len(page_results), 'done': done,
                        'review_ready': (OUTPUTS / manga['id'] / chapter.name / 'stage2/_report.json').exists() and chapter.name in {'Chapter 1','Chapter 12'},
                        'flagged': sum(p.get('flagged', False) for p in page_results),
                        'panels': sum(p['panels'] or 0 for p in page_results),
                        'seconds': round(sum(p['seconds'] or 0 for p in page_results), 3),
                        'pages': page_results, 'job': job,
                        'status': 'complete' if done == len(page_results) else job.get('status', 'ready'),
                    })
            except (ValueError, OSError, json.JSONDecodeError) as exc:
                item['error'] = str(exc)
            entries.append(item)
        validation = read_json(DATA / 'model-validation.json', {})
        return {'manga': entries, 'busy': process is not None, 'detector_ready': validation.get('panel_only_validated', False), 'validation_message': validation.get('reason', 'Panel-only model validation is required before processing.')}


@app.post('/api/manga/{manga_id}/chapters/{chapter_name}/start')
def start_chapter(manga_id: str, chapter_name: str):
    global process, active_job
    with mutex:
        reconcile()
        if process is not None:
            raise HTTPException(409, 'A chapter is already running. Stop it or wait for completion.')
        validation = read_json(DATA / 'model-validation.json', {})
        if not validation.get('panel_only_validated', False):
            raise HTTPException(409, validation.get('reason', 'Panel-only Magi v3 must pass validation before processing.'))
        if not processing_allowed(manga_id, chapter_name):
            raise HTTPException(403, 'Only Chapter 1 and Chapter 12 of the approved manga may be processed.')
        manga = find_manga(manga_id)
        chapter = find_chapter(manga, chapter_name)
        if all(not saved_page_errors(read_json(page_output(manga, chapter.name, p.name)), p) for p in pages(chapter)):
            return {'status': 'complete'}
        job_file = job_path(manga, chapter.name)
        job = {'status': 'running', 'manga': manga, 'chapter': chapter.name, 'message': 'Starting Magi v3', 'processed': 0, 'panels_found': 0, 'elapsed_seconds': 0}
        write_json(job_file, job)
        # The start gate prevents the child overwriting the initial PID update.
        gate = job_file.with_name('_start')
        gate.unlink(missing_ok=True)
        try:
            process = subprocess.Popen([str(ROOT / '.venv/bin/python'), '-m', 'app.worker', str(job_file)], cwd=ROOT)
            active_job = job_file
            job['pid'] = process.pid
            write_json(job_file, job)
            gate.touch()
        except Exception:
            stop()
            job.update(status='error', message='Could not start the chapter worker.')
            write_json(job_file, job)
            raise
        return {'status': 'running'}


@app.post('/api/stop')
def stop_chapter():
    with mutex:
        stop()
    return {'status': 'paused'}

# Stage 2 review APIs use only saved panel boxes and source page images.
from app import review


def review_error(exc):
    if isinstance(exc, PermissionError):
        return HTTPException(403, str(exc))
    if isinstance(exc, FileExistsError):
        return HTTPException(409, str(exc))
    if isinstance(exc, FileNotFoundError):
        return HTTPException(404, str(exc))
    return HTTPException(400, str(exc))


@app.get('/review/{manga_id}/{chapter_name}')
def review_page(manga_id: str, chapter_name: str):
    try:
        review.review_context(manga_id, chapter_name)
    except (PermissionError, FileNotFoundError, ValueError) as exc:
        raise review_error(exc)
    return FileResponse(ROOT / 'app/static/review.html')


@app.get('/api/review/{manga_id}/{chapter_name}')
def get_review(manga_id: str, chapter_name: str):
    with mutex:
        try:
            return review.chapter_report(manga_id, chapter_name)
        except (PermissionError, FileNotFoundError, ValueError) as exc:
            raise review_error(exc)


class PanelFixes(BaseModel):
    revision: str = Field(min_length=1, max_length=64)
    boxes: list[list[float]] = Field(max_length=200)


@app.post('/api/review/{manga_id}/{chapter_name}/{page_name}')
def save_review(manga_id: str, chapter_name: str, page_name: str, body: PanelFixes):
    with mutex:
        reconcile()
        if process is not None:
            raise HTTPException(409, 'Wait for chapter processing to finish before editing.')
        try:
            return review.save_page(manga_id, chapter_name, page_name, body.revision, body.boxes)
        except (PermissionError, FileNotFoundError, FileExistsError, ValueError) as exc:
            raise review_error(exc)


@app.get('/api/review/{manga_id}/{chapter_name}/{page_name}/image')
def review_image(manga_id: str, chapter_name: str, page_name: str, kind: str, revision: str | None = None, panel_id: str | None = None):
    with mutex:
        try:
            path = review.asset_path(manga_id, chapter_name, page_name, kind, revision, panel_id)
        except (PermissionError, FileNotFoundError, ValueError) as exc:
            raise review_error(exc)
    return FileResponse(path, headers={'Cache-Control': 'no-cache' if kind == 'source' else 'private, max-age=31536000, immutable'})

# Stage 3 is strictly restricted to Chapter 1.
from app import narration
from fastapi.responses import JSONResponse


@app.get('/narration/{manga_id}/{chapter_name}')
def narration_page(manga_id: str, chapter_name: str):
    try:
        narration.context(manga_id, chapter_name)
    except (PermissionError, FileNotFoundError, ValueError) as exc:
        raise review_error(exc)
    return FileResponse(ROOT / 'app/static/narration.html')


@app.get('/api/narration/{manga_id}/{chapter_name}')
def narration_status(manga_id: str, chapter_name: str):
    with mutex:
        try:
            return narration.status(manga_id, chapter_name)
        except (PermissionError, FileNotFoundError, ValueError) as exc:
            raise review_error(exc)


@app.get('/api/narration/{manga_id}/{chapter_name}/prompt')
def narration_prompt(manga_id: str, chapter_name: str, batch_id: str = "batch-001", final_recap: bool = False):
    with mutex:
        try:
            path = narration.prompt_file(manga_id, chapter_name, batch_id, final_recap)
        except (PermissionError, FileNotFoundError, FileExistsError, ValueError) as exc:
            raise review_error(exc)
    return FileResponse(path, media_type='text/markdown', filename='narration_prompt.md', headers={'Cache-Control':'no-store'})


@app.get('/api/narration/{manga_id}/{chapter_name}/{batch_id}/export')
def narration_export(manga_id: str, chapter_name: str, batch_id: str, fingerprint: str):
    with mutex:
        try:
            path = narration.export_zip(manga_id, chapter_name, batch_id, fingerprint)
        except (PermissionError, FileNotFoundError, FileExistsError, ValueError) as exc:
            raise review_error(exc)
    return FileResponse(path, media_type='application/zip', filename=f'Chapter-1-{batch_id}.zip')


class NarrationImport(BaseModel):
    fingerprint: str = Field(min_length=64,max_length=64)
    reply: str = Field(min_length=1,max_length=2_000_000)
    corrections_only: bool = False
    final_recap: bool = False
    context_token: str | None = Field(default=None,min_length=64,max_length=64)


@app.post('/api/narration/{manga_id}/{chapter_name}/{batch_id}/import')
def narration_import(manga_id: str, chapter_name: str, batch_id: str, body: NarrationImport):
    with mutex:
        try:
            result = narration.import_reply(manga_id, chapter_name, batch_id, body.fingerprint, body.reply, body.corrections_only, body.final_recap, body.context_token)
        except (PermissionError, FileNotFoundError, FileExistsError, ValueError) as exc:
            raise review_error(exc)
    return JSONResponse(result,status_code=200 if result['ok'] else 422)


# Stage 4 uses only imported Chapter 1 narration; no image processing.
from app import audio


@app.get('/audio/{manga_id}/{chapter_name}')
def audio_page(manga_id: str, chapter_name: str):
    try:
        narration.context(manga_id, chapter_name)
    except (PermissionError, FileNotFoundError, ValueError) as exc:
        raise review_error(exc)
    return FileResponse(ROOT / 'app/static/audio.html')


@app.get('/api/audio/{manga_id}/{chapter_name}')
def audio_status(manga_id: str, chapter_name: str):
    try:
        return audio.status(manga_id, chapter_name)
    except (PermissionError, FileNotFoundError, FileExistsError, ValueError) as exc:
        raise review_error(exc)


@app.post('/api/audio/{manga_id}/{chapter_name}/generate')
def audio_generate(manga_id: str, chapter_name: str):
    try:
        return audio.start(manga_id, chapter_name)
    except (PermissionError, FileNotFoundError, FileExistsError, ValueError) as exc:
        raise review_error(exc)


@app.post('/api/audio/{manga_id}/{chapter_name}/beats/{number}/regenerate')
def audio_regenerate(manga_id: str, chapter_name: str, number: int):
    try:
        return audio.start(manga_id, chapter_name, number)
    except (PermissionError, FileNotFoundError, FileExistsError, ValueError) as exc:
        raise review_error(exc)


@app.get('/api/audio/{manga_id}/{chapter_name}/beats/{number}/audio')
def audio_file(manga_id: str, chapter_name: str, number: int):
    try:
        path = audio.asset(manga_id, chapter_name, number)
    except (PermissionError, FileNotFoundError, FileExistsError, ValueError) as exc:
        raise review_error(exc)
    return FileResponse(path, media_type='audio/mpeg', headers={'Cache-Control': 'no-store'})


from app import video


@app.get('/video/{manga_id}/{chapter_name}')
def video_page(manga_id: str, chapter_name: str):
    try:
        narration.context(manga_id, chapter_name)
    except (PermissionError, FileNotFoundError, ValueError) as exc:
        raise review_error(exc)
    return FileResponse(ROOT / 'app/static/video.html')


@app.get('/api/video/{manga_id}/{chapter_name}')
def video_status(manga_id: str, chapter_name: str):
    try:
        return video.status(manga_id, chapter_name)
    except (PermissionError, FileNotFoundError, FileExistsError, ValueError) as exc:
        raise review_error(exc)


class VideoTiming(BaseModel):
    source_token: str = Field(min_length=64,max_length=64)
    number: int = Field(ge=1)
    switches: list[float]


@app.post('/api/video/{manga_id}/{chapter_name}/timing')
def video_timing(manga_id: str, chapter_name: str, body: VideoTiming):
    try:
        return video.save(manga_id, chapter_name, body.source_token, body.number, body.switches)
    except (PermissionError, FileNotFoundError, FileExistsError, ValueError) as exc:
        raise review_error(exc)


class VideoExport(BaseModel):
    render_token: str = Field(min_length=64,max_length=64)


@app.post('/api/video/{manga_id}/{chapter_name}/export')
def video_export(manga_id: str, chapter_name: str, body: VideoExport):
    try:
        return video.start(manga_id, chapter_name, body.render_token)
    except (PermissionError, FileNotFoundError, FileExistsError, ValueError) as exc:
        raise review_error(exc)


@app.get('/api/video/{manga_id}/{chapter_name}/mp4')
def video_mp4(manga_id: str, chapter_name: str, token: str, download: bool = False):
    try:
        path = video.asset(manga_id, chapter_name, token)
    except (PermissionError, FileNotFoundError, FileExistsError, ValueError) as exc:
        raise review_error(exc)
    return FileResponse(path, media_type='video/mp4', filename='Chapter-1.mp4' if download else None,
                        headers={'Cache-Control':'no-store'})
