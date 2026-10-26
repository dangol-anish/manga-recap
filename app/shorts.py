"""Independent Shorts projects built from selected, current narration snapshots."""
from app import errors
import copy
import json
import math
from pathlib import Path
import re
import shutil
import tempfile
import threading
import time
import uuid

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from app import storage, narration_stage5 as story, panel_preparation as panels, audio, alignment, video
from app.chapter_names import chapter_sort_key
from app.runtime import open_system_folder
from app.speech_stage6 import checksum

router = APIRouter(prefix='/api/shorts')
lock = threading.RLock()
active = None
worker = None
cancel = threading.Event()
VERSION = 1
MIN_SECONDS, MAX_SECONDS = 30, 120


def safe(path):
    path = Path(path)
    if not path.is_absolute(): path = storage.ROOT / path
    if not path.resolve().is_relative_to(storage.ROOT.resolve()):
        raise ValueError('Shorts files must stay inside this repository.')
    for part in (path, *path.parents):
        if part == storage.ROOT: break
        if part.is_symlink(): raise ValueError('Shortcuts are not supported in Shorts storage.')
    return path


def ident(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{32}', value):
        raise ValueError('Shorts project not found.')
    return value


def root(pid): return safe(storage.DATA / 'shorts' / ident(pid))
def output(pid): return safe(storage.OUTPUTS / 'Shorts' / ident(pid))
def save(project): storage.write_json(root(project['id']) / 'project.json', project)


def load(pid):
    project = storage.read_json(safe(root(pid) / 'project.json'))
    if not project: raise ValueError('Shorts project not found.')
    return project


def catalog():
    groups = panels.catalog()
    for group in groups:
        for chapter in group['chapters']:
            chapter['shorts_ready'] = False
            chapter['reason'] = 'Finish panel preparation and import all narration batches.'
            if chapter['ready'] and chapter['prepared_pages'] == chapter['images']:
                try:
                    state = story.status(group['id'], chapter['name'])
                    chapter['shorts_ready'] = bool(state['batches'] and all(b['imported'] for b in state['batches']) and state['imported_beats'])
                    if chapter['shorts_ready']: chapter['reason'] = ''
                except (ValueError, OSError) as exc: chapter['reason'] = str(exc)
    return groups


def snapshot(mid, chapters):
    """Read only. Narration and prepared image hashes bind every source reference."""
    if not chapters or len(chapters) != len(set(chapters)):
        raise ValueError('Choose each chapter once.')
    result = []; manga = None
    with story.lock, panels.lock:
        for chapter in sorted(chapters, key=chapter_sort_key):
            status = story.status(mid, chapter)
            if not status['batches'] or not all(b['imported'] for b in status['batches']):
                raise FileExistsError(chapter + ': import all current narration batches first.')
            manga = status['manga']; saved = story.load_state(mid)['chapters'][chapter]['batches']
            info, files, _ = story.resolve(mid, chapter); images = {}
            for page in files:
                record = panels.safe_record(panels.record_path(info, panels.page_ids(files)[page.name]))
                directory = panels.record_path(info, record['page_id']).parent / record['revision']
                labeled = safe(directory / 'labeled.png'); labeled_hash = checksum(labeled)
                for p in sorted(record['panels'], key=lambda row: row['order']):
                    crop = safe(directory / 'crops' / (p['id'] + '.png'))
                    images[p['id']] = {'panel_id': p['id'], 'page_id': record['page_id'],
                        'crop': crop.relative_to(storage.ROOT).as_posix(), 'crop_sha256': checksum(crop),
                        'labeled': labeled.relative_to(storage.ROOT).as_posix(), 'labeled_sha256': labeled_hash}
            beats = []; provenance = []
            for batch in status['batches']:
                entry = saved[batch['id']]
                provenance.append([batch['id'], batch['fingerprint'], batch['context_token']])
                for beat in entry['beats']:
                    beats.append({'beat': len(beats) + 1, 'narration': beat['narration'],
                        'unsure': beat.get('unsure', ''), 'panel_ids': beat['panel_ids'],
                        'panels': [images[p] for p in beat['panel_ids']]})
            if not beats: raise ValueError(chapter + ': no saved story beats.')
            last = saved[status['batches'][-1]['id']]
            result.append({'chapter': chapter, 'beats': beats, 'provenance': provenance,
                'context_before': status['batches'][0]['previous_context'],
                'cast': last.get('cast', []), 'continuity': last.get('continuity', {}),
                'chapter_summary': last.get('chapter_summary', '')})
    return {'manga_id': mid, 'manga': manga, 'chapters': result}


def receipt(project):
    return {'project_id': project['id'], 'source_token': project['source_token']}


def bundle(project):
    source = copy.deepcopy(project['source'])
    for chapter in source['chapters']:
        for beat in chapter['beats']:
            beat.pop('panels')  # Internal paths and hashes never need to reach Gemini.
        chapter.pop('provenance')
    return {'receipt': receipt(project), 'source': source}


def create(mid, chapters, name):
    from app.tasks import lock as task_lock, ensure_idle
    with task_lock, lock:
        ensure_idle(); source = snapshot(mid, chapters)
        project = {'id': uuid.uuid4().hex, 'name': name.strip() or source['manga'] + ' shorts',
            'created_at': time.time(), 'source': source, 'source_token': story._digest(source),
            'revision': uuid.uuid4().hex, 'shorts': [], 'job': None, 'schema_version': VERSION,
            'voice': audio.DEFAULT_VOICE, 'pacing': dict(audio.DEFAULT_PACING)}
        save(project)
        return public(project)


def fresh(project):
    source = snapshot(project['source']['manga_id'], [c['chapter'] for c in project['source']['chapters']])
    if story._digest(source) != project['source_token']:
        raise FileExistsError('Selected narration or panels changed. Create a new Shorts package; this project and its files are retained.')


def schema():
    def obj(properties): return {'type': 'object', 'properties': properties, 'required': list(properties)}
    text = {'type': 'string'}
    segment = obj({'chapter': text, 'source_beat': {'type': 'integer'}, 'panel_id': text, 'narration': text})
    proposal = obj({'title': text, 'why_it_works': text, 'segments': {'type': 'array', 'items': segment}})
    return obj({'receipt': obj({'project_id': text, 'source_token': text}),
                'shorts': {'type': 'array', 'items': proposal}})


def prompt(project):
    return (storage.ROOT / 'prompts/shorts_master.md').read_text(encoding='utf-8') + '\n\nCOPY THIS RECEIPT EXACTLY:\n' + json.dumps(receipt(project))


def strict_json(text):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value: raise ValueError('Duplicate JSON key: ' + key)
            value[key] = item
        return value
    def invalid_constant(value): raise ValueError('Non-finite JSON number: ' + value)
    return json.loads(text, object_pairs_hook=pairs, parse_constant=invalid_constant)


def validate(project, payload):
    errors = []; cleaned = []
    if not isinstance(payload, dict) or set(payload) != {'receipt', 'shorts'}:
        return ['Return an object containing receipt and shorts only.'], []
    if payload['receipt'] != receipt(project):
        return ['This reply belongs to a different or older Shorts package. Use the matching package and prompt.'], []
    proposals = payload['shorts']
    if not isinstance(proposals, list) or len(proposals) > 8:
        return ['shorts must be an array of zero to eight proposals.'], []
    chapters = {c['chapter']: c for c in project['source']['chapters']}
    ranks = {c: i for i, c in enumerate(chapters)}
    for index, proposal in enumerate(proposals, 1):
        label = f'Short {index}'
        if not isinstance(proposal, dict) or set(proposal) != {'title', 'why_it_works', 'segments'}:
            errors.append(label + ': provide title, why_it_works and segments only.'); continue
        if any(not isinstance(proposal[k], str) or not proposal[k].strip() or len(proposal[k]) > limit for k, limit in [('title', 120), ('why_it_works', 800)]):
            errors.append(label + ': add a title (up to 120 characters) and explanation (up to 800).')
        segments = proposal['segments']
        if not isinstance(segments, list) or not 1 <= len(segments) <= 80:
            errors.append(label + ': provide one to 80 spoken segments.'); continue
        last = (-1, -1, -1); count = 0; rows = []
        for number, row in enumerate(segments, 1):
            prefix = f'{label}, segment {number}: '
            if not isinstance(row, dict) or set(row) != {'chapter', 'source_beat', 'panel_id', 'narration'}:
                errors.append(prefix + 'provide chapter, source_beat, panel_id and narration.'); continue
            chapter = row['chapter']; n = row['source_beat']; pid = row['panel_id']; text = row['narration']
            if not isinstance(chapter, str) or chapter not in chapters or type(n) is not int or not 1 <= n <= len(chapters[chapter]['beats']):
                errors.append(prefix + 'unknown chapter or source beat.'); continue
            beat = chapters[chapter]['beats'][n - 1]
            if not isinstance(pid, str) or pid not in beat['panel_ids']:
                errors.append(prefix + f'{pid}: not a panel of {chapter}, source beat {n}.'); continue
            position = (ranks[chapter], n, beat['panel_ids'].index(pid))
            if position < last: errors.append(prefix + 'events move backwards. Keep the source reading order.')
            last = position
            if not isinstance(text, str) or not text.strip() or len(text) > 1500:
                errors.append(prefix + 'add a non-empty spoken sentence or phrase.'); continue
            words = alignment.script_tokens(text); count += len(words)
            if not 1 <= len(words) <= 40:
                errors.append(prefix + 'use one to 40 words for this panel.')
            rows.append({**row, 'narration': text.strip()})
        if not 60 <= count <= 280:
            errors.append(label + f': {count} words; use 60–280 words for an approximately 30–120 second short.')
        cleaned.append({'title': proposal['title'], 'why_it_works': proposal['why_it_works'], 'segments': rows,
                        'word_count': count, 'estimated_seconds': round(count / 2.4)})
    return errors, cleaned


def import_reply(pid, revision, text, replace_id=None):
    from app.tasks import lock as task_lock, ensure_idle
    with task_lock, lock:
        ensure_idle(); project = load(pid)
        if project['revision'] != revision: raise FileExistsError('Project changed in another tab. Refresh before importing.')
        fresh(project)
        try: payload = strict_json(text)
        except (ValueError, RecursionError) as exc: return {'ok': False, 'errors': ['Invalid JSON: ' + str(exc)]}
        errors, proposals = validate(project, payload)
        if errors: return {'ok': False, 'errors': errors}
        # Immutable proposal sets retain all existing results and clips on re-import.
        if project['shorts']: storage.write_json(root(pid) / 'history' / (project['revision'] + '.json'), project)
        replacements = [{**p, 'id': uuid.uuid4().hex, 'clips': {}, 'result': None} for p in proposals]
        if replace_id is not None:
            original = find_short(project, replace_id)
            if len(replacements) != 1: return {'ok': False, 'errors': ['A visual-check reply must contain exactly one corrected short.']}
            project['shorts'][project['shorts'].index(original)] = replacements[0]
        else:
            project['shorts'] = replacements
        project['revision'] = uuid.uuid4().hex; project['job'] = None; save(project)
        return {'ok': True, 'project': public(project)}


def find_short(project, sid):
    for short in project['shorts']:
        if short['id'] == sid: return short
    raise ValueError('Short not found.')


def panel(project, row):
    chapter = next(c for c in project['source']['chapters'] if c['chapter'] == row['chapter'])
    return next(p for p in chapter['beats'][row['source_beat'] - 1]['panels'] if p['panel_id'] == row['panel_id'])


def clip_token(project, short, i):
    return story._digest([VERSION, project['source_token'], short['segments'][i], project['voice'], project['pacing']])


def clip_ready(pid, clip, token):
    try:
        path = safe(output(pid) / clip['file'])
        words = clip['words']; duration = clip['duration']
        return (clip['token'] == token and path.is_file() and checksum(path) == clip['sha256'] and
            isinstance(duration, (int, float)) and math.isfinite(duration) and duration > 0 and bool(words) and
            all(0 <= w['start'] < w['end'] <= duration + .001 for w in words))
    except (KeyError, TypeError, ValueError, OSError): return False


def result_path(project, short):
    result = short.get('result')
    if not result: raise ValueError('Create this short first.')
    path = safe(output(project['id']) / result['file'])
    if not path.is_file() or checksum(path) != result['sha256']:
        raise FileExistsError('The saved short was moved or changed. Create it again; your other files are safe.')
    return path


def public(project):
    result = {k: copy.deepcopy(project[k]) for k in ('id', 'name', 'created_at', 'revision', 'job', 'voice', 'pacing')}
    result.update(manga=project['source']['manga'], chapters=[c['chapter'] for c in project['source']['chapters']],
                  receipt=receipt(project), shorts=[], stale=False, stale_reason='', busy=active is not None)
    # A running job owns a frozen snapshot and validates again before publication.
    # Do not rehash every selected chapter on every progress poll.
    if active != project['id']:
        try: fresh(project)
        except (ValueError, OSError) as exc: result.update(stale=True, stale_reason=str(exc))
    for short in project['shorts']:
        item = {k: copy.deepcopy(short[k]) for k in ('id', 'title', 'why_it_works', 'segments', 'word_count', 'estimated_seconds')}
        item['ready'] = False
        if short.get('result'):
            try:
                result_path(project, short); item['ready'] = not result['stale']; item['duration'] = short['result']['duration']
                item['video_url'] = f'/api/shorts/projects/{project["id"]}/shorts/{short["id"]}/video?v={short["result"]["sha256"]}'
            except (ValueError, OSError) as exc:
                detail=errors.warning(exc,'shorts','Read saved short',short['title']);item['unavailable_reason']=detail['message']
        item['clips_ready'] = sum(clip_ready(project['id'], short['clips'].get(str(i), {}), clip_token(project, short, i)) for i in range(len(short['segments'])))
        result['shorts'].append(item)
    return result


def projects():
    directory = safe(storage.DATA / 'shorts')
    return [public(load(p.parent.name)) for p in sorted(directory.glob('*/project.json'), key=lambda p: p.stat().st_mtime, reverse=True) if not p.is_symlink()]


def visual_check(pid, sid):
    from app.tasks import lock as task_lock, ensure_idle
    with task_lock, lock:
        ensure_idle(); project = load(pid); fresh(project); short = find_short(project, sid)
        directory = safe(output(pid) / 'Visual checks' / sid / uuid.uuid4().hex)
        directory.mkdir(parents=True)
        seen = set(); files = []
        for row in short['segments']:
            source = panel(project, row); key = (row['chapter'], source['page_id'])
            if key in seen: continue
            seen.add(key)
            # Numeric prefixes avoid Windows filename restrictions and collisions.
            name = f'{len(seen):03d}-{source["page_id"]}.png'
            shutil.copyfile(safe(storage.ROOT / source['labeled']), directory / name)
            files.append({'file': name, 'chapter': row['chapter'], 'page_id': source['page_id']})
        text = ('Check this proposed short against the attached labeled manga pages. File mapping:\n' + json.dumps(files, ensure_ascii=False) +
                '\nVerify every segment matches its panel and that the story and character claims are supported. Do not invent unseen events. '
                'If it is correct, return the same complete reply. Otherwise revise only unsupported narration or omit unsupported segments, '
                'using only panel/source-beat pairs in the original package. Keep source order, 60–280 words total, at most 40 words per segment. '
                'Return the original receipt and a shorts array containing this one complete corrected proposal.\n' +
                json.dumps({'receipt': receipt(project), 'shorts': [{k: short[k] for k in ('title', 'why_it_works', 'segments')}]}, ensure_ascii=False))
        fresh(project); open_system_folder(directory)
        return {'prompt': text, 'pages': len(files)}


def update_short(pid, sid, revision, segments):
    from app.tasks import lock as task_lock, ensure_idle
    with task_lock, lock:
        ensure_idle(); project = load(pid); fresh(project)
        if revision != project['revision']: raise FileExistsError('Project changed. Refresh before saving.')
        short = find_short(project, sid)
        errors, clean = validate(project, {'receipt': receipt(project), 'shorts': [{'title': short['title'], 'why_it_works': short['why_it_works'], 'segments': segments}]})
        if errors: return {'ok': False, 'errors': errors}
        storage.write_json(root(pid) / 'history' / (project['revision'] + '.json'), project)
        short.update(clean[0]); short['result'] = None  # old files/clips are retained
        project['revision'] = uuid.uuid4().hex; save(project)
        return {'ok': True, 'project': public(project)}


def run(pid, selected):
    global active
    try:
        from app import shorts_render
        for sid in selected:
            if cancel.is_set(): raise InterruptedError()
            with lock:
                project = load(pid); fresh(project); short = find_short(project, sid)
                if short.get('result'):
                    try:
                        result_path(project, short)
                        project['job']['completed'].append(sid); save(project); continue
                    except (ValueError, OSError): pass
                project['job'].update(current_short=short['title'], progress=0, message='Creating voice clips…'); save(project)
            directory = safe(output(pid) / sid); directory.mkdir(parents=True, exist_ok=True)
            for i, row in enumerate(short['segments']):
                if cancel.is_set(): raise InterruptedError()
                token = clip_token(project, short, i)
                if clip_ready(pid, short['clips'].get(str(i), {}), token): continue
                with tempfile.TemporaryDirectory(dir=directory, prefix='.voice-') as temporary:
                    path = Path(temporary) / 'clip.mp3'
                    spoken = alignment.speech(row['narration'], path, dict(project['pacing']), project['voice'])
                    if cancel.is_set(): raise InterruptedError()
                    tokens = alignment.script_tokens(row['narration'])
                    words = spoken['words']; duration = len(video.pcm(path)) / 48000
                    if [w['text'] for w in words] != [row['narration'][a:b] for _, a, b in tokens]:
                        raise ValueError('Speech timestamps did not match the short script.')
                    target = directory / (token + '-' + uuid.uuid4().hex + '.mp3')
                    clip = {'file': target.relative_to(output(pid)).as_posix(), 'token': token, 'sha256': checksum(path),
                        'duration': duration, 'words': words, 'voice': project['voice'], 'pacing': dict(project['pacing'])}
                    if not math.isfinite(duration) or duration <= 0 or not words or any(not 0 <= w['start'] < w['end'] <= duration + .001 for w in words):
                        raise ValueError('Speech returned invalid timing.')
                    # The clip belongs to the frozen source/script snapshot. A full
                    # source check before final publication catches external changes.
                    path.replace(target)
                    with lock:
                        project = load(pid); short = find_short(project, sid); short['clips'][str(i)] = clip
                        project['job'].update(message=f'Voice clip {i + 1} of {len(short["segments"])} saved.'); save(project)
            duration = sum(short['clips'][str(i)]['duration'] for i in range(len(short['segments'])))
            if not MIN_SECONDS <= duration <= MAX_SECONDS:
                raise ValueError(f'This short is {duration:.1f} seconds. Edit its script to fit 30–120 seconds, then try again. Saved clips are retained.')
            if cancel.is_set(): raise InterruptedError()
            with tempfile.TemporaryDirectory(dir=directory, prefix='.video-') as temporary:
                work = Path(temporary)
                def progress(value):
                    with lock:
                        current = load(pid); current['job'].update(message='Creating vertical video…', progress=value); save(current)
                result = shorts_render.render(project, short, work, cancel, progress)
                fresh(project)
                if cancel.is_set(): raise InterruptedError()
                from app.audio_folders import safe_name
                stem = safe_name(short['title'])[:60] + '-' + uuid.uuid4().hex[:12]
                target = directory / (stem + '.mp4')
                captions = directory / (stem + '-captions.json')
                shutil.copyfile(work / 'captions.json', captions); result.replace(target)
                with lock:
                    project = load(pid); short = find_short(project, sid)
                    short['result'] = {'file': target.relative_to(output(pid)).as_posix(), 'sha256': checksum(target), 'duration': duration,
                        'captions_file': captions.relative_to(output(pid)).as_posix()}
                    project['job']['completed'].append(sid); save(project)
        with lock:
            project = load(pid); project['job'].update(status='complete', progress=1, message='Your selected shorts are ready.'); save(project)
    except Exception as exc:
        with lock:
            project = load(pid); project['job'].update(status='paused' if isinstance(exc, InterruptedError) else 'failed',
                message='Stopped safely. Saved clips will be reused when you try again.' if isinstance(exc, InterruptedError) else str(exc))
            if not isinstance(exc,InterruptedError): errors.failure(project['job'],exc,'shorts','Create selected short',project['job'].get('current_short'))
            errors.save_final(save,project,'shorts')
    finally:
        with lock: active = None


def start(pid, revision, ids):
    global active, worker
    from app.tasks import lock as task_lock, ensure_idle
    with task_lock, lock:
        ensure_idle(); project = load(pid); fresh(project)
        if project['revision'] != revision: raise FileExistsError('Project changed. Refresh before creating shorts.')
        if not ids or len(ids) != len(set(ids)): raise ValueError('Choose each short once.')
        for sid in ids: find_short(project, sid)
        if not shutil.which('ffmpeg') or not shutil.which('ffprobe'): raise ValueError('Install the free FFmpeg tools first.')
        project['job'] = {'status': 'running', 'selected': ids, 'completed': [], 'progress': 0, 'message': 'Starting selected shorts…'}
        save(project); cancel.clear(); active = pid
        worker = threading.Thread(target=run, args=(pid, ids), daemon=True)
        try: worker.start()
        except Exception:
            active = None; project['job'].update(status='failed', message='Could not start. Try again.'); save(project); raise
        return public(project)


def recover():
    for path in safe(storage.DATA / 'shorts').glob('*/project.json'):
        try:
            project=load(path.parent.name)
            if project.get('job') and project['job']['status'] in ('running','stopping'):
                project['job'].update(status='paused',message='The app closed. Saved clips are safe; select shorts and try again.');save(project)
        except (ValueError,OSError,KeyError,TypeError) as exc:errors.warning(exc,'shorts','Recover Shorts project',path)


def shutdown():
    cancel.set()
    if worker and worker.is_alive(): worker.join(130)


class Selection(BaseModel):
    manga_id: str = Field(min_length=1, max_length=128)
    chapters: list[str] = Field(min_length=1, max_length=500)
    name: str = Field(default='', max_length=120)


class Reply(BaseModel):
    revision: str = Field(min_length=32, max_length=32)
    reply: str = Field(min_length=1, max_length=2000000)
    replace_id: str | None = None


class Creation(BaseModel):
    revision: str = Field(min_length=32, max_length=32)
    short_ids: list[str] = Field(min_length=1, max_length=8)


class Edit(BaseModel):
    revision: str = Field(min_length=32, max_length=32)
    segments: list[dict] = Field(min_length=1, max_length=80)


def error(exc): return HTTPException(409 if isinstance(exc, FileExistsError) else 400, str(exc))


# Endpoints use one wrapper so malformed inputs cannot expose tracebacks.
def guarded(fn, *args):
    try: return fn(*args)
    except (ValueError, OSError) as exc: raise error(exc)


@router.get('/catalog')
def catalog_api(): return guarded(catalog)


@router.get('/projects')
def projects_api(): return guarded(projects)


@router.post('/projects')
def create_api(body: Selection): return guarded(create, body.manga_id, body.chapters, body.name)


@router.get('/schema')
def schema_api(): return schema()


@router.get('/projects/{pid}')
def status_api(pid: str): return guarded(lambda: public(load(pid)))


@router.get('/projects/{pid}/package')
def package_api(pid: str):
    def get():
        project = load(pid); fresh(project)
        return JSONResponse(bundle(project), headers={'Content-Disposition': 'attachment; filename="shorts-source.json"'})
    return guarded(get)


@router.get('/projects/{pid}/prompt')
def prompt_api(pid: str): return guarded(lambda: {'prompt': prompt(load(pid))})


@router.post('/projects/{pid}/import')
def import_api(pid: str, body: Reply): return guarded(import_reply, pid, body.revision, body.reply, body.replace_id)


@router.post('/projects/{pid}/create')
def start_api(pid: str, body: Creation): return guarded(start, pid, body.revision, body.short_ids)


@router.post('/projects/{pid}/stop')
def stop_api(pid: str):
    def stop():
        with lock:
            if active != pid: raise ValueError('This project is not running.')
            cancel.set(); project = load(pid); project['job'].update(status='stopping', message='Stopping after the current operation…'); save(project)
            return public(project)
    return guarded(stop)


@router.post('/projects/{pid}/open-folder')
def folder_api(pid: str):
    def folder():
        load(pid); directory = output(pid); directory.mkdir(parents=True, exist_ok=True); open_system_folder(directory)
        return {'opened': True}
    return guarded(folder)


@router.post('/projects/{pid}/shorts/{sid}/visual-check')
def visual_api(pid: str, sid: str): return guarded(visual_check, pid, sid)


@router.post('/projects/{pid}/shorts/{sid}/edit')
def edit_api(pid: str, sid: str, body: Edit): return guarded(update_short, pid, sid, body.revision, body.segments)


@router.get('/projects/{pid}/shorts/{sid}/panel/{number}')
def image_api(pid: str, sid: str, number: int):
    def image():
        project = load(pid); short = find_short(project, sid)
        if not 0 <= number < len(short['segments']): raise ValueError('Panel not found.')
        source = panel(project, short['segments'][number]); path = safe(storage.ROOT / source['crop'])
        if checksum(path) != source['crop_sha256']: raise FileExistsError('This panel changed. Create a new Shorts package.')
        return FileResponse(path, media_type='image/png')
    return guarded(image)


@router.get('/projects/{pid}/shorts/{sid}/video')
def video_api(pid: str, sid: str, download: bool = False):
    def video_file():
        project = load(pid); fresh(project); path = result_path(project, find_short(project, sid))
        return FileResponse(path, media_type='video/mp4', filename='Short-' + sid[:8] + '.mp4' if download else None)
    return guarded(video_file)
