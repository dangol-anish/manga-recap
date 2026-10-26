from contextlib import asynccontextmanager
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
from app.runtime import acquire_server_lock

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
    try:
        DATA.mkdir(exist_ok=True)
        lock = acquire_server_lock(DATA / 'server.lock')
    except Exception as exc:
        raise RuntimeError(errors.describe(exc,'startup','Open local app storage',DATA)['message']) from exc
    try:
        # A separate worker can survive a forced server kill. Terminate that precise
        # repo worker before recovering its job, so two processes never write a page.
        for path in OUTPUTS.glob('*/*/_job.json'):
            try: job = read_json(path)
            except (ValueError,OSError) as exc:
                try:acquire_server_lock(path.with_name('_worker.lock')).close()
                except RuntimeError as busy:
                    raise RuntimeError(errors.describe(busy,'startup','Check earlier worker before recovery',path)['message']) from busy
                errors.warning(exc,'startup','Recover historical worker',path);continue
            if not isinstance(job,dict) or 'status' not in job:
                errors.warning(ValueError('Saved historical worker has no valid status. File was retained.'),'startup','Recover historical worker',path);continue
            if job['status'] == 'running':
                pid = job.get('pid')
                if sys.platform == 'win32':
                    try:
                        acquire_server_lock(path.with_name('_worker.lock')).close()
                    except RuntimeError as exc:
                        lock.close()
                        raise RuntimeError('An earlier Library worker is still finishing. Wait for it to exit before restarting the app; do not run a second copy.') from exc
                if pid and sys.platform != 'win32':
                    result = subprocess.run(['ps', '-p', str(pid), '-o', 'command='], capture_output=True, text=True, encoding='utf-8', errors='replace')
                    expected = f'{sys.executable} -m app.worker {path}'
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
        from importlib import import_module
        modules = [import_module('app.' + name) for name in ('maintenance','downloader','detection','panel_preparation','speech_stage6','video_stage7','batch_work','stitch','shorts','grouped_audio')]
        try:
            for module in modules:
                try: module.recover()
                except Exception as exc: errors.warning(exc,'startup','Recover ' + module.__name__.split('.')[-1],module.__name__)
            yield
        finally:
            for module in reversed(modules):
                try: module.shutdown()
                except Exception as exc: errors.warning(exc,'startup','Stop ' + module.__name__.split('.')[-1])
            with mutex:
                stop()
    finally:
        lock.close()



app = FastAPI(title='Manga Recap Library', lifespan=lifespan)
app.mount('/static', StaticFiles(directory=ROOT / 'app/static'), name='static')
from app import errors
errors.install(app)


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
    try:
        from app import maintenance
        if request.method not in {'GET', 'HEAD', 'OPTIONS'} and not request.url.path.startswith('/api/storage/'):
            if maintenance.recovery_required:
                raise FileExistsError('Restore recovery needs attention. Open Backup & storage before changing saved work.')
            if maintenance.active is not None:
                raise FileExistsError('Backup, restore or cleanup is running. Wait or stop it before changing saved work.')
        if request.method == 'GET':
            from app.read_cache import read_snapshot
            with read_snapshot(): response = await call_next(request)
        else: response = await call_next(request)
    except Exception as exc:
        stage, operation = errors.context(request.url.path, request.method)
        value = errors.describe(exc, stage, operation)
        response = JSONResponse({'detail': value['message'], 'error': value}, status_code=value['status'])
    reference = errors.latest_warning()
    if reference: response.headers['X-App-Warning'] = reference
    return response


@app.get('/')
def library_page():
    return FileResponse(ROOT / 'app/static/downloader.html')


@app.get('/renumbering')
def renumbering_page():
    return FileResponse(ROOT / 'app/static/renumbering.html')


@app.get('/library')
def existing_library_page():
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
            manga = {'id': uuid.uuid4().hex, 'name': folder.name, 'folder': folder.relative_to(ROOT).as_posix(), 'direction': body.direction, 'pacing':dict(audio.DEFAULT_PACING)}
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
                        'review_ready': (OUTPUTS / manga['id'] / chapter.name / 'stage2/_report.json').exists(),
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
        return {'manga': entries, 'busy': process is not None, 'detector_ready': validation.get('panel_only_validated', False), 'validation_message': 'Magi v3 ready · only panel boxes are saved. Choose any registered chapter.' if validation.get('panel_only_validated') else validation.get('reason', 'Panel-only model validation is required before processing.')}


@app.post('/api/manga/{manga_id}/chapters/{chapter_name}/start')
def start_chapter(manga_id: str, chapter_name: str):
    global process, active_job
    from app.tasks import lock as task_lock, ensure_idle
    with task_lock, mutex:
        reconcile()
        if process is not None:
            raise HTTPException(409, 'A chapter is already running. Stop it or wait for completion.')
        try:ensure_idle()
        except FileExistsError as exc:raise HTTPException(409,str(exc))
        validation = read_json(DATA / 'model-validation.json', {})
        if not validation.get('panel_only_validated', False):
            raise HTTPException(409, validation.get('reason', 'Panel-only Magi v3 must pass validation before processing.'))
        if not processing_allowed(manga_id, chapter_name):
            raise HTTPException(403, 'Select a registered chapter with validated panel detection.')
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
            process = subprocess.Popen([sys.executable, '-m', 'app.worker', str(job_file)], cwd=ROOT)
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

# Narration export and validated import.
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
    return FileResponse(path, media_type='application/zip', filename=f'{chapter_name}-{batch_id}.zip')


class NarrationImport(BaseModel):
    fingerprint: str = Field(min_length=64,max_length=64)
    reply: str = Field(min_length=1,max_length=2_000_000)
    corrections_only: bool = False
    final_recap: bool = False
    context_token: str | None = Field(default=None,min_length=64,max_length=64)
    require_receipt: bool = True


@app.post('/api/narration/{manga_id}/{chapter_name}/{batch_id}/import')
def narration_import(manga_id: str, chapter_name: str, batch_id: str, body: NarrationImport):
    with mutex:
        try:
            result = narration.import_reply(manga_id, chapter_name, batch_id, body.fingerprint, body.reply, body.corrections_only, body.final_recap, body.context_token, body.require_receipt)
        except (PermissionError, FileNotFoundError, FileExistsError, ValueError) as exc:
            raise review_error(exc)
    return JSONResponse(result,status_code=200 if result['ok'] else 422)


# Speech uses imported chapter narration only; no image processing.
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


@app.get('/api/video/{manga_id}/{chapter_name}/frame')
def video_frame(manga_id: str, chapter_name: str, panel_id: str, revision: str, style: str):
    try:
        path = video.frame_asset(manga_id,chapter_name,panel_id,revision,style)
    except (PermissionError,FileNotFoundError,FileExistsError,ValueError) as exc:
        raise review_error(exc)
    return FileResponse(path,media_type='image/png',headers={'Cache-Control':'private, max-age=31536000, immutable'})


@app.get('/api/video/{manga_id}/{chapter_name}/mp4')
def video_mp4(manga_id: str, chapter_name: str, token: str, download: bool = False):
    try:
        path = video.asset(manga_id, chapter_name, token)
    except (PermissionError, FileNotFoundError, FileExistsError, ValueError) as exc:
        raise review_error(exc)
    return FileResponse(path, media_type='video/mp4', filename=f'{chapter_name}.mp4' if download else None,
                        headers={'Cache-Control':'no-store'})


from app import alignment


@app.get('/api/alignment/{manga_id}/{chapter_name}/{number}')
def alignment_status(manga_id: str, chapter_name: str, number: int):
    try:
        return alignment.status(manga_id,chapter_name,number)
    except (PermissionError,FileNotFoundError,FileExistsError,ValueError) as exc:
        raise review_error(exc)


@app.get('/api/alignment/{manga_id}/{chapter_name}/{number}/prompt')
def alignment_prompt(manga_id: str, chapter_name: str, number: int):
    from fastapi.responses import Response
    try:
        text=alignment.prompt_text(manga_id,chapter_name,number)
    except (PermissionError,FileNotFoundError,FileExistsError,ValueError) as exc:
        raise review_error(exc)
    return Response(text,media_type='text/markdown',headers={'Content-Disposition':f'attachment; filename="beat-{number:03}-alignment-prompt.md"','Cache-Control':'no-store'})


class PhraseMapInput(BaseModel):
    source_token: str = Field(min_length=64,max_length=64)
    reply: str = Field(min_length=1,max_length=200_000)


@app.post('/api/alignment/{manga_id}/{chapter_name}/{number}/map')
def alignment_map(manga_id: str, chapter_name: str, number: int, body: PhraseMapInput):
    try:
        result=alignment.save_map(manga_id,chapter_name,number,body.source_token,body.reply)
    except (PermissionError,FileNotFoundError,FileExistsError,ValueError) as exc:
        raise review_error(exc)
    return JSONResponse(result,status_code=200 if result['ok'] else 422)


class AlignmentTrialInput(BaseModel):
    source_token: str = Field(min_length=64,max_length=64)


@app.post('/api/alignment/{manga_id}/{chapter_name}/{number}/trial')
def alignment_trial(manga_id: str, chapter_name: str, number: int, body: AlignmentTrialInput):
    try:
        return alignment.start(manga_id,chapter_name,number,body.source_token)
    except (PermissionError,FileNotFoundError,FileExistsError,ValueError) as exc:
        raise review_error(exc)


@app.get('/api/alignment/{manga_id}/{chapter_name}/{number}/trial/{revision}/{kind}')
def alignment_trial_asset(manga_id: str, chapter_name: str, number: int, revision: str, kind: str):
    try:
        path=alignment.asset(manga_id,chapter_name,number,revision,kind)
    except (PermissionError,FileNotFoundError,FileExistsError,ValueError) as exc:
        raise review_error(exc)
    return FileResponse(path,media_type='video/mp4' if kind=='mp4' else 'audio/mpeg',headers={'Cache-Control':'no-store'})


@app.get('/alignment-examples/{manga_id}/{chapter_name}')
def alignment_examples_page(manga_id: str, chapter_name: str):
    try:
        narration.context(manga_id,chapter_name)
    except (PermissionError,FileNotFoundError,ValueError) as exc:
        raise review_error(exc)
    return FileResponse(ROOT / 'app/static/alignment_examples.html')


@app.get('/api/alignment-examples/{manga_id}/{chapter_name}')
def alignment_examples_status(manga_id: str, chapter_name: str):
    try:
        return {'chapter':chapter_name,'examples':[a for b in video.source(manga_id,chapter_name)[0] if (a:=alignment.status(manga_id,chapter_name,b['number']))['ready']]}
    except (PermissionError,FileNotFoundError,FileExistsError,ValueError) as exc:
        raise review_error(exc)


@app.post('/api/panels/{manga_id}/{chapter_name}/prepare')
def prepare_review(manga_id: str,chapter_name: str):
    with mutex:
        reconcile()
        if process is not None:raise HTTPException(409,'Wait for panel detection to finish.')
        try:return review.prepare_chapter(manga_id,chapter_name)
        except (PermissionError,FileNotFoundError,FileExistsError,ValueError) as exc:raise review_error(exc)


@app.get('/memory/{manga_id}')
def memory_page(manga_id: str):
    find_manga(manga_id)
    return FileResponse(ROOT/'app/static/memory.html')


@app.get('/api/memory/{manga_id}')
def memory_status(manga_id: str,q: str=''):
    find_manga(manga_id)
    from app import memory
    with mutex:return memory.overview(manga_id,q[:200])


class MemoryEdit(BaseModel):
    revision: str = Field(min_length=64,max_length=64)
    chapter_summary: str = Field(max_length=1200)
    memory: list[dict] = Field(max_length=30)
    cast: list[dict] | None = Field(default=None,max_length=100)


@app.post('/api/memory/{manga_id}/{chapter_name}/{batch_id}')
def memory_edit(manga_id: str,chapter_name: str,batch_id: str,body: MemoryEdit):
    find_manga(manga_id)
    from app import memory
    with mutex:
        try:return memory.edit(manga_id,chapter_name,batch_id,body.revision,body.chapter_summary,body.memory,body.cast)
        except (FileNotFoundError,FileExistsError,ValueError) as exc:raise review_error(exc)


class ChapterAlignmentInput(BaseModel):
    source_token: str = Field(min_length=64,max_length=64)


@app.post('/api/alignment/{manga_id}/{chapter_name}/apply')
def apply_phrase_timing(manga_id: str,chapter_name: str,body: ChapterAlignmentInput):
    try:return alignment.start_chapter(manga_id,chapter_name,body.source_token)
    except (PermissionError,FileNotFoundError,FileExistsError,ValueError) as exc:raise review_error(exc)


@app.get('/api/alignment-job/{manga_id}/{chapter_name}')
def chapter_alignment_status(manga_id: str,chapter_name: str):
    try:narration.context(manga_id,chapter_name)
    except (PermissionError,FileNotFoundError,ValueError) as exc:raise review_error(exc)
    return alignment.chapter_status(manga_id,chapter_name)

# Stage 1 downloads are independent of panel detection.
from app.downloader import router as downloader_router
app.include_router(downloader_router)

from app.renumbering import router as renumbering_router
app.include_router(renumbering_router)

@app.get("/detection")
def detection_page():
    return FileResponse(ROOT / "app/static/detection.html")

from app.detection import router as detection_router
app.include_router(detection_router)

@app.get("/panels")
def panel_preparation_page():
    return FileResponse(ROOT / "app/static/panel_preparation.html")

from app.panel_preparation import router as preparation_router
app.include_router(preparation_router)


@app.get('/story')
def story_stage5_page():
    return FileResponse(ROOT / 'app/static/story.html')

from app.narration_stage5 import router as story_router
app.include_router(story_router)


@app.get('/speech')
def speech_stage6_page():
    return FileResponse(ROOT / 'app/static/speech.html')

from app.speech_stage6 import router as speech_router
app.include_router(speech_router)


@app.get('/cinema')
def video_stage7_page():
    return FileResponse(ROOT / 'app/static/cinema.html')

from app.video_stage7 import router as cinema_router
app.include_router(cinema_router)


from app.batch_work import router as batch_work_router
app.include_router(batch_work_router)


@app.get('/stitch')
def stitch_page():
    return FileResponse(ROOT/'app/static/stitch.html')


from app.stitch import router as stitch_router
app.include_router(stitch_router)


@app.get('/shorts')
def shorts_page():
    return FileResponse(ROOT / 'app/static/shorts.html')


from app.shorts import router as shorts_router
app.include_router(shorts_router)

from app.grouped_audio import router as grouped_audio_router
app.include_router(grouped_audio_router)
from app.workflow import router as workflow_router
app.include_router(workflow_router)


@app.get('/storage')
def storage_page():
    return FileResponse(ROOT / 'app/static/storage.html')


from app.maintenance import router as maintenance_router
app.include_router(maintenance_router)
