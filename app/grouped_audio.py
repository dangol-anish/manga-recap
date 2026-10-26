"""Quota-aware Gemini narration across chapters, with independent saved revisions."""
import base64
import copy
from datetime import datetime, timedelta, timezone
import hashlib
from io import BytesIO
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
import wave
from functools import wraps

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from app import errors, storage, narration_stage5 as story, alignment, audio, video
from app.chapter_names import chapter_sort_key
from app.audio_folders import safe_name
from app.runtime import open_system_folder
from app.read_cache import memoized_read

router = APIRouter(prefix='/api/grouped-audio')
MODEL = 'gemini-3.8-flash-tts'
VOICE = 'Charon'
STYLE = 'Read the complete transcript verbatim in one consistent male storyteller voice. Natural conversational delivery, restrained emotion appropriate to the story, brief sentence pauses. Keep the same voice, accent and recording tone. About 160 words per minute. No introduction, summary, added words or omitted passages.'
VERSION = 1
DEFAULT_SPEED = 1.2
LEGACY_BACKUP_PACING = {'rate': '+0%', 'speed': 1., 'sentence_pause': .18}
active = worker = None
lock = threading.RLock()
cancel = threading.Event()


def safe(path):
    path = Path(path)
    if not path.resolve().is_relative_to(storage.ROOT.resolve()): raise ValueError('Audio files must stay inside the repository.')
    for part in (path, *path.parents):
        if part == storage.ROOT: break
        if part.is_symlink(): raise ValueError('Shortcuts are not supported in audio storage.')
    return path


def check_id(value):
    if not isinstance(value, str) or not re.fullmatch('[a-f0-9]{32}', value): raise ValueError('Audio selection not found.')
    return value


def path(ident): return safe(storage.DATA / 'grouped-audio' / (check_id(ident) + '.json'))
def directory(ident): return safe(storage.OUTPUTS / 'grouped-audio' / check_id(ident))
def load(ident):
    job = storage.read_json(path(ident))
    if not job: raise ValueError('Audio selection not found.')
    return job

def save(job): storage.write_json(path(job['id']), job)
def checksum(path):
    from app.read_cache import file_hash
    return file_hash(path)


def credentials():
    """Read credentials at use time; never put their values into job records."""
    values = {}
    file = safe(storage.ROOT / '.env')
    if file.is_file():
        for line in file.read_text(encoding='utf-8').splitlines():
            match = re.fullmatch(r"\s*(GEMINI_API_KEY(?:_\d+)?|GOOGLE_API_KEY)\s*=\s*(.*?)\s*", line)
            if match: values[match[1]] = match[2].strip('"' + "'")
    for name, value in os.environ.items():
        if re.fullmatch(r'GEMINI_API_KEY(?:_\d+)?|GOOGLE_API_KEY', name): values[name] = value.strip()
    names = sorted(values, key=lambda n: (0, 0) if n == 'GEMINI_API_KEY' else (0, 1) if n == 'GOOGLE_API_KEY' else (1, int(n.rsplit('_', 1)[1])))
    seen = set(); result = []
    for name in names:
        key = values[name]
        if key and key not in seen:
            result.append((name, key)); seen.add(key)
    return result


def api_key():
    keys = credentials()
    if not keys: raise AudioError('api_key_missing', 'Gemini API keys are missing. Add GEMINI_API_KEY or GEMINI_API_KEY_2, _3, … to the ignored .env file, then resume.')
    return keys[0][1]


def api_keys():
    # Keep the single-key hook used by existing integrations and fixture tests.
    first = api_key()
    configured = credentials()
    return configured if any(key == first for _, key in configured) else [('GEMINI_API_KEY', first)]


def key_token(key): return hashlib.sha256(key.encode()).hexdigest()


def key_states(): return storage.read_json(safe(storage.DATA / 'gemini-key-state.json'), {})


def unavailable(label, key, states, usage):
    now = time.time(); entry = states.get(key_token(key), {})
    if entry.get('blocked') or entry.get('until', 0) > now:
        return {'label': label, 'reason': entry['reason'], 'reset_at': datetime.fromtimestamp(entry['until'], timezone.utc).isoformat() if entry.get('until') else None}
    day, reset = pacific_clock(); ledger = usage.get(key_token(key), {})
    if ledger.get('day') == day and ledger.get('requests', 0) >= 10:
        return {'label': label, 'reason': 'Local daily request limit reached', 'reset_at': reset}
    return None


def key_status():
    states = key_states(); usage = storage.read_json(safe(storage.DATA / 'gemini-quota.json'), {})
    keys = [{'label': label, 'available': True} if not (issue := unavailable(label, key, states, usage)) else {**issue, 'available': False} for label, key in credentials()]
    return {'configured': len(keys), 'available': sum(k['available'] for k in keys), 'keys': keys}


def choose_key(tried):
    states = key_states(); usage = storage.read_json(safe(storage.DATA / 'gemini-quota.json'), {})
    issues = []
    for label, key in api_keys():
        issue = unavailable(label, key, states, usage)
        if issue: issues.append(issue); continue
        if key_token(key) not in tried: return label, key
    resets = [i['reset_at'] for i in issues if i.get('reset_at')]
    message = 'No Gemini key is available. ' + '; '.join(i['label'] + ': ' + i['reason'] for i in issues)
    raise AudioError('all_keys_unavailable', message + '. Saved recordings are safe. Resume after a quota reset, update rejected keys, or explicitly choose Brian for remaining sections.', min(resets) if resets else None)


def pause_key(key, error, data):
    states = key_states(); entry = {'reason': str(error), 'code': error.code}
    if error.code == 'daily_quota': entry.update(reason='Daily Google project quota reached', until=datetime.fromisoformat(error.reset).timestamp())
    elif error.code == 'temporary_rate_limit':
        delay = 60.
        details = (data.get('error') or {}).get('details', [])
        for detail in details:
            match = re.fullmatch(r'(\d+(?:\.\d+)?)s', str(detail.get('retryDelay', '')))
            if match: delay = max(delay, min(86400, float(match[1])))
        entry.update(reason='Temporary request/token limit; cooling down', until=time.time() + delay)
    elif error.code=='free_tier_unavailable': entry.update(reason='Free tier unavailable for this Google project; replace its key',blocked=True)
    else: entry.update(reason='Key rejected or TTS model access unavailable', blocked=True)
    states[key_token(key)] = entry
    storage.write_json(safe(storage.DATA / 'gemini-key-state.json'), states)


class AudioError(Exception):
    def __init__(self, code, message, reset=None):
        super().__init__(message); self.code = code; self.reset = reset


def pacific_clock(now=None):
    """Modern US DST rules, also on Windows without an IANA timezone database."""
    now = now or datetime.now(timezone.utc); year = now.year
    march = datetime(year, 3, 1, 10, tzinfo=timezone.utc); march += timedelta(days=(6 - march.weekday()) % 7 + 7)
    november = datetime(year, 11, 1, 9, tzinfo=timezone.utc); november += timedelta(days=(6 - november.weekday()) % 7)
    offset = 7 if march <= now < november else 8
    day = (now - timedelta(hours=offset)).date()
    guess = datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=7)
    next_offset = 7 if march <= guess < november else 8
    reset = datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=next_offset)
    return day.isoformat(), reset.isoformat()


def reserve(key):
    today, reset = pacific_clock(); filename = safe(storage.DATA / 'gemini-quota.json')
    data = storage.read_json(filename, {}); token = hashlib.sha256(key.encode()).hexdigest()
    usage = data.get(token, {})
    if usage.get('day') != today: usage = {'day': today, 'requests': 0}
    if usage['requests'] >= 10:
        raise AudioError('daily_quota', 'Gemini’s daily audio limit has been reached (10 requests for this key in the app). Completed sections are saved. Resume after the reset, or explicitly choose Brian for the remaining sections.', reset)
    usage['requests'] += 1; usage['last_request'] = time.time(); data[token] = usage; storage.write_json(filename, data)


def quota_wait():
    # Stay below the observed 3 requests/minute even after very short sections.
    for _ in range(21):
        if cancel.wait(1): raise InterruptedError()


def classify(status, data, key=''):
    error = data.get('error', {}) if isinstance(data, dict) else {}
    message = str(error.get('message', ''))
    if key: message = message.replace(key, '[hidden]')
    details = json.dumps(error.get('details', [])); evidence = message + details
    if status == 403 and 'free tier is not available' in evidence.lower():
        return AudioError('free_tier_unavailable','Google says this project has no free-tier access. Replace this key with one from a free-tier eligible project. Billing was not enabled; completed recordings are saved.')
    if status in (401, 403) or re.search(r'API_KEY_INVALID|API key not valid|API key expired', evidence, re.I):
        return AudioError('api_key_rejected', 'Google rejected the API key or this project’s access. Check the key and model access in AI Studio. No billing was enabled. Saved sections are safe.')
    if status == 429:
        if re.search(r'PerDay|per.day|daily|requests.per.day', evidence, re.I):
            return AudioError('daily_quota', 'Gemini’s daily audio quota has been reached. This quota is shared by the Google project, including other API keys. Completed sections are saved. Resume after reset or explicitly choose Brian for the remaining sections.', pacific_clock()[1])
        return AudioError('temporary_rate_limit', 'Google temporarily limited this request (requests or tokens per minute, or available capacity). Completed sections are saved. Wait at least a minute, then resume. This was not a crash.')
    if status == 404: return AudioError('model_unavailable', 'The configured Gemini speech model is unavailable to this project. Check its access in AI Studio. Completed recordings are safe; no automatic provider change was made.')
    if status >= 500: return AudioError('google_unavailable', 'Google’s speech service is temporarily unavailable. Completed sections are saved. Resume later; the app will not automatically spend more requests.')
    return AudioError('google_request_rejected', f'Google rejected this audio request (HTTP {status}). Check model availability and the saved script. Completed sections are saved. No automatic retry was made.')


def gemini(text, target):
    payload = {'model': MODEL, 'input': [{'type': 'user_input', 'content': [{'type': 'text', 'text': text,
        'annotations': [{'type': 'speech_metadata', 'style': STYLE}]}]}], 'response_format': {'type': 'audio', 'mime_type': 'audio/wav'},
        'generation_config': {'speech_config': [{'voice': VOICE}]}}
    tried = set()
    while True:
        if cancel.is_set(): raise InterruptedError()
        label, key = choose_key(tried)
        tried.add(key_token(key))
        try: reserve(key)
        except AudioError as error:
            if error.code != 'daily_quota': raise
            pause_key(key, error, {}); continue
        try:
            response = httpx.post('https://generativelanguage.googleapis.com/v1beta/interactions', headers={'x-goog-api-key': key}, json=payload, timeout=900)
        except httpx.TimeoutException:
            raise AudioError('network_timeout', 'Google did not finish responding before the time limit. It may have counted the request. Saved sections are safe. No key rotation or automatic duplicate request was made; resume explicitly.') from None
        except httpx.HTTPError:
            raise AudioError('connection_failed', 'Could not connect to Google. Saved sections are safe. No key rotation or automatic duplicate request was made; check your internet connection and resume.') from None
        try: data = response.json()
        except ValueError: raise AudioError('invalid_response', 'Google returned an unreadable response. Saved sections are safe. No automatic duplicate request was made.') from None
        if response.status_code == 200: break
        error = classify(response.status_code, data, key)
        if error.code not in ('daily_quota', 'temporary_rate_limit', 'api_key_rejected', 'model_unavailable', 'free_tier_unavailable'): raise error
        pause_key(key, error, data)
    blocks = [c for s in data.get('steps', []) if s.get('type') == 'model_output' for c in s.get('content', []) if c.get('type') == 'audio']
    if data.get('status') != 'completed' or not blocks:
        raise AudioError('incomplete_recording', 'Google did not complete this recording. Earlier sections are safe. Review or resume this section; no incomplete audio was published.')
    try:
        raw = base64.b64decode(blocks[-1]['data'], validate=True)
        if not raw.startswith(b'RIFF'): raise ValueError()
        with wave.open(BytesIO(raw), 'rb') as stream:
            duration = stream.getnframes() / stream.getframerate()
            if len(stream.readframes(stream.getnframes())) != stream.getnframes() * stream.getnchannels() * stream.getsampwidth(): raise ValueError()
        if not math.isfinite(duration) or duration < max(1, len(text.split()) / 5): raise ValueError()
    except (ValueError, KeyError, wave.Error):
        target.unlink(missing_ok=True)
        raise AudioError('incomplete_recording', 'Google returned invalid or unexpectedly short audio. Earlier sections are safe. This recording was not accepted.') from None
    temporary = target.with_suffix('.tmp')
    try:
        with temporary.open('xb') as out:
            out.write(raw); out.flush(); os.fsync(out.fileno())
        temporary.replace(target)
    finally: temporary.unlink(missing_ok=True)
    return {'duration': duration, 'usage': data.get('usage'), 'provider': 'gemini', 'voice': VOICE, 'key_label': label}


def source(mid, chapters):
    if not chapters or len(chapters) != len(set(chapters)): raise ValueError('Choose each chapter once.')
    items = []; manga = ''
    with story.lock:
        saved = story.load_state(mid)
        for chapter in sorted(chapters, key=chapter_sort_key):
            current = story.status(mid, chapter); manga = current['manga']
            if not current['batches'] or not all(b['imported'] for b in current['batches']): raise FileExistsError(chapter + ': import all current narration batches first.')
            beats = []
            for batch in current['batches']:
                for b in saved['chapters'][chapter]['batches'][batch['id']]['beats']:
                    beats.append({**copy.deepcopy(b), 'number': len(beats) + 1, 'chapter': chapter})
            if not any(b['narration'].strip() for b in beats): raise ValueError(chapter + ': no spoken narration.')
            items.append({'chapter': chapter, 'beats': beats, 'binding': [(b['id'], b['fingerprint'], b['context_token']) for b in current['batches']]})
    return {'manga_id': mid, 'manga': manga, 'chapters': items}


def sections(snapshot):
    beats = [b for c in snapshot['chapters'] for b in c['beats'] if b['narration'].strip()]
    total = sum(len(b['narration'].split()) for b in beats); count = max(1, math.ceil(total / 1250)); groups = []; group = []; words = 0
    remaining = total
    for beat in beats:
        size = len(beat['narration'].split())
        if size > 1400: raise ValueError('A single story beat exceeds the safe section length. Shorten that narration beat first.')
        target = remaining / max(1, count - len(groups))
        if group and len(groups) < count - 1 and (words + size > 1400 or abs(words - target) <= abs(words + size - target)):
            groups.append(group); remaining -= words; group = []; words = 0
        if group and words + size > 1400:
            groups.append(group); remaining -= words; group = []; words = 0
        group.append(beat); words += size
    if group: groups.append(group)
    result = []
    for group in groups:
        text = ''; refs = []
        for beat in group:
            if text: text += '\n\n'
            start = len(text); text += beat['narration']
            refs.append({'chapter': beat['chapter'], 'number': beat['number'], 'start': start, 'end': len(text)})
        result.append({'number': len(result) + 1, 'text': text, 'refs': refs, 'words': len(text.split()), 'recording': None, 'timing': None})
    return result


def fresh(job):
    if story._digest(source(job['source']['manga_id'], [c['chapter'] for c in job['source']['chapters']])) != job['source_token']:
        raise FileExistsError('Selected narration or prepared panels changed. Start a new audio selection. Existing recordings are retained.')


def metadata_file(job, metadata):
    target = safe(directory(job['id']) / metadata['file'])
    if not target.is_file() or checksum(target) != metadata['sha256']: raise FileExistsError('A saved recording changed or is missing. Restore it or start a new selection; no automatic paid retry was made.')
    return target


def record_section(job, section):
    base = directory(job['id']); base.mkdir(parents=True, exist_ok=True)
    file = base / ('section-' + str(section['number']) + '-' + uuid.uuid4().hex + '.wav')
    section['pending_file'] = file.name; section['pending_provider'] = job['engine']; save(job)
    storage.write_json(base / ('section-' + str(section['number']) + '-script.json'), {'text': section['text'], 'refs': section['refs']})
    if job['engine'] == 'gemini': meta = gemini(section['text'], file)
    else:
        mp3 = file.with_suffix('.mp3')
        settings = dict(job.get('backup_pacing', LEGACY_BACKUP_PACING))
        spoken = alignment.speech(section['text'], mp3, settings, audio.DEFAULT_VOICE)
        result = subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-i', str(mp3), '-ar', '24000', '-ac', '1', str(file)], capture_output=True, timeout=120)
        errors.checked_process(result,'Save Brian backup recording',file)
        # Edge timestamps are already accurate; avoid the local model for backup sections.
        section['timing'] = spoken['words']; meta = {'provider': 'brian', 'voice': audio.DEFAULT_VOICE, 'duration': spoken['duration'], 'speed': settings['speed'], 'pacing': settings}
    meta.update(file=file.name, sha256=checksum(file)); section['recording'] = meta; save(job)


def prepare_speed(job, section):
    """Make a pitch-preserving playback copy before measuring phrase times."""
    meta = section['recording']; speed = job.get('speed', 1.)
    if meta['provider'] != 'gemini' or speed == 1. or meta.get('speed') == speed: return
    original = metadata_file(job, meta)
    target = directory(job['id']) / ('paced-' + uuid.uuid4().hex + '.wav')
    temporary = target.with_name('.' + target.name)
    try:
        result = subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-i', str(original), '-af', f'atempo={speed}', '-ar', '24000', '-ac', '1', str(temporary)], capture_output=True, timeout=180)
        errors.checked_process(result,'Prepare faster playback copy; original recording is retained',original)
        with wave.open(str(temporary), 'rb') as stream: duration = stream.getnframes() / stream.getframerate()
        if not math.isfinite(duration) or abs(duration - meta['duration'] / speed) > .15: raise ValueError('Faster audio duration did not match the selected speed. Original audio is retained.')
        temporary.replace(target)
        section['original_recording'] = dict(meta)
        section['recording'] = {**meta, 'file': target.name, 'sha256': checksum(target), 'duration': duration, 'speed': speed}
        section['timing'] = None
        save(job)
    finally: temporary.unlink(missing_ok=True)


def publish_chapters(job):
    """Publish only complete chapters to separate stores; old manifests stay intact."""
    mid = job['source']['manga_id']; index_path = safe(storage.DATA / 'gemini-chapters.json')
    index = storage.read_json(index_path, {})
    for chapter in job['source']['chapters']:
        name = chapter['chapter']; selected = [(s, r) for s in job['sections'] for r in s['refs'] if r['chapter'] == name]
        if any(not s['recording'] or not s['timing'] for s, r in selected): continue
        unique = {s['number']: s for s, r in selected}
        chapter_token = story._digest([job['source_token'], name, [(s['recording']['sha256'], story._digest(s['timing'])) for s in unique.values()]])
        if index.get(mid + '/' + name, {}).get('token') == chapter_token:
            from app import speech_stage6 as speech
            speech.build_full(mid, name, chapter_token)
            continue
        folder = safe(directory(job['id']) / ('chapter-' + str([c['chapter'] for c in job['source']['chapters']].index(name))))
        folder.mkdir(parents=True, exist_ok=True); clips = {}; beats = []
        by_number = {r['number']: (s, r) for s, r in selected}
        for original in chapter['beats']:
            beat = copy.deepcopy(original); beat['silent'] = not beat['narration'].strip(); beat['batch_id'] = 'grouped-audio'; beat['pacing'] = {'speed': job.get('speed', 1.), 'mode': 'natural'}
            beat['token'] = story._digest([chapter_token, beat['number']]); beat['voice'] = VOICE
            if not beat['silent']:
                section, ref = by_number[beat['number']]; recording = metadata_file(job, section['recording']); words = section['timing']
                at = section['refs'].index(ref); begin = 0 if at == 0 else next(w['start'] for w in words if w['char_start'] >= ref['start'])
                end = section['recording']['duration'] if at == len(section['refs']) - 1 else next(w['start'] for w in words if w['char_start'] >= section['refs'][at + 1]['start'])
                local = [{**w, 'char_start': w['char_start'] - ref['start'], 'char_end': w['char_end'] - ref['start'], 'start': max(0, w['start'] - begin), 'end': min(end - begin, w['end'] - begin)} for w in words if ref['start'] <= w['char_start'] < ref['end']]
                issues, anchors = alignment.validate_map(beat, {'beat': beat['number'], 'anchors': beat.get('anchors', [])})
                if issues: raise AudioError('phrase_mapping', 'Saved narration phrase mapping needs review: ' + '; '.join(issues[:2]) + '. The Gemini recording is saved; no new request is needed.')
                timed = alignment.anchor_times(beat, anchors, local, end - begin)
                target = folder / (beat['token'] + '.mp3')
                if not target.exists():
                    temporary = target.with_name('.' + uuid.uuid4().hex + '.mp3')
                    try:
                        result = subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-ss', str(begin), '-i', str(recording), '-t', str(end - begin), '-ar', '24000', '-ac', '1', '-codec:a', 'libmp3lame', '-b:a', '96k', str(temporary)], capture_output=True, timeout=120)
                        errors.checked_process(result,'Prepare chapter voice clips',recording)
                        from mutagen.mp3 import MP3
                        if abs(MP3(temporary).info.length - (end - begin)) > .2: raise ValueError('Chapter clip duration did not match its speech timing.')
                        temporary.replace(target)
                    finally: temporary.unlink(missing_ok=True)
                beat['voice'] = section['recording']['voice']
                if section['recording']['provider'] == 'brian': beat['pacing'] = dict(section['recording'].get('pacing', LEGACY_BACKUP_PACING))
                clips[beat['token']] = {'token': beat['token'], 'file': target.name, 'sha256': checksum(target), 'duration': end - begin,
                    'voice': beat['voice'], 'pacing': beat['pacing'], 'words': local, 'timing_anchors': timed, 'provider': section['recording']['provider']}
            beats.append(beat)
        voices = list(dict.fromkeys(b['voice'] for b in beats if not b['silent']))
        display_voice = 'Gemini Charon' if voices == [VOICE] else 'Brian Multilingual' if voices == [audio.DEFAULT_VOICE] else 'Gemini Charon + Brian'
        overlay = {'token': chapter_token, 'folder': folder.relative_to(storage.ROOT).as_posix(),
            'manifest': {'clips': clips, 'voice': display_voice, 'pacing': {'speed': job.get('speed', 1.), 'mode': 'natural'}},
            'source': {'manga': job['source']['manga'], 'chapter': name, 'voice': display_voice, 'pacing': {'speed': job.get('speed', 1.), 'mode': 'natural'}, 'beats': beats, 'revision': chapter_token},
            'job_id': job['id']}
        fresh(job)
        storage.write_json(folder / 'manifest.json', overlay['manifest'])
        index[mid + '/' + name] = overlay; storage.write_json(index_path, index)
        from app import speech_stage6 as speech
        speech.build_full(mid, name, chapter_token)


@memoized_read
def chapter_state(mid, chapter):
    record = storage.read_json(safe(storage.DATA / 'gemini-chapters.json'), {}).get(mid + '/' + chapter)
    if not record: return None
    try: fresh(load(record['job_id']))
    except (ValueError, OSError): return None
    safe(storage.ROOT / record['folder']); return record


def assemble(job):
    target = directory(job['id']) / ('Full-audio-' + uuid.uuid4().hex + '.mp3')
    with tempfile.TemporaryDirectory(dir=directory(job['id']), prefix='.full-') as temporary:
        work = Path(temporary); pcm = work / 'full.pcm'
        with pcm.open('wb') as out:
            for section in job['sections']:
                if cancel.is_set(): raise InterruptedError()
                result = subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-i', str(metadata_file(job, section['recording'])), '-f', 's16le', '-ac', '1', '-ar', '24000', '-'], stdout=out, stderr=subprocess.PIPE, timeout=120)
                errors.checked_process(result,'Join selected audio sections')
        result = subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-f', 's16le', '-ar', '24000', '-ac', '1', '-i', str(pcm), '-codec:a', 'libmp3lame', '-b:a', '128k', str(target)], capture_output=True, timeout=180)
        errors.checked_process(result,'Save full selection audio',target)
    fresh(job); job['full'] = {'file': target.name, 'sha256': checksum(target)}; save(job)
    # Keep a readable flat copy beside the manga's chapter audio.
    copy_full(job)


def copy_full(job):
    from app.audio_folders import checked, publish
    target = metadata_file(job, job['full'])
    from app.audio_folders import sync
    parent = sync(job['source']['manga_id'])
    entry = storage.read_json(storage.DATA / 'audio-folders.json', {}).get('manga', {}).get(job['source']['manga_id'])
    if not entry: raise ValueError('The manga audio folder is not available yet. Resume to try again.')
    folder = checked(parent / entry['folder'])
    publish(target, folder, 'Selected chapters-Audio', job['full']['sha256'], {})


def run(job):
    global active
    aligner = None
    try:
        fresh(job)
        for section in job['sections']:
            if cancel.is_set(): raise InterruptedError()
            if not section['recording']:
                job.update(current_section=section['number'], message=f'Creating section {section["number"]} of {len(job["sections"])} with {"Gemini" if job["engine"] == "gemini" else "Brian"}…'); save(job)
                if job['engine'] == 'gemini' and section['number'] > 1: quota_wait()
                record_section(job, section)
            metadata_file(job, section['recording'])
            prepare_speed(job, section)
            if cancel.is_set(): raise InterruptedError()
            if not section['timing']:
                job.update(message='Recording saved. Loading the free local speech timing model (about 360 MB on first use)…'); save(job)
                from app.forced_timing import Aligner
                def progress(message): job.update(message=message); save(job)
                try:
                    if aligner is None: aligner = Aligner()
                    section['timing'] = aligner.align(metadata_file(job, section['recording']), section['text'], cancel, progress)
                except InterruptedError: raise
                except Exception as exc: raise AudioError('local_timing', 'The recording is saved, but local timing failed: ' + str(exc) + ' Resume retries timing without another Gemini request.') from None
                save(job)
            publish_chapters(job)
        if cancel.is_set(): raise InterruptedError()
        if not job.get('full'):
            job.update(message='Joining your completed sections into one full audio track…'); save(job); assemble(job)
        copy_full(job)
        from app.audio_folders import sync
        sync(job['source']['manga_id'])
        job.update(status='complete', error=None, message='All selected chapters are ready as one full audio track. Their panel timings are also available on Video.')
    except InterruptedError:
        job.update(status='paused', message='Stopped safely. Returned recordings and completed timing are saved. Resume continues with the missing work.')
    except Exception as exc:
        job['status']='failed'
        errors.failure(job,exc,'grouped-audio','Create or time narration section', 'section ' + str(job.get('current_section') or '?'))
    finally:
        aligner = None
        with lock:
            try: errors.save_final(save,job,'grouped-audio')
            finally: active = None


def launch(job):
    global active, worker
    cancel.clear(); job.update(status='running', error=None); save(job); active = job['id']
    worker = threading.Thread(target=run, args=(job,), daemon=True)
    try: worker.start()
    except Exception:
        active = None; job.update(status='failed', message='Could not start audio creation. Try again.'); save(job); raise
    return view(job)


def start(mid, chapters, confirmed=True):
    from app.tasks import lock as task_lock, ensure_idle
    with task_lock, lock:
        ensure_idle(); api_key()
        if not shutil.which('ffmpeg'): raise ValueError('Install FFmpeg first.')
        snapshot = source(mid, chapters)
        token = story._digest(snapshot)
        for filename in sorted(safe(storage.DATA / 'grouped-audio').glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True):
            previous = load(filename.stem)
            if previous.get('source_token') == token and previous.get('engine') == 'gemini' and previous.get('model') == MODEL and previous.get('speed', 1.) == DEFAULT_SPEED:
                if previous['status'] in ('failed', 'paused'): return view(previous)
                if previous['status'] == 'complete' and previous.get('full'):
                    metadata_file(previous, previous['full'])
                    for section in previous['sections']: metadata_file(previous, section['recording'])
                    return view(previous)
        job = {'id': uuid.uuid4().hex, 'created_at': time.time(), 'source': snapshot, 'source_token': token, 'sections': sections(snapshot),
            'engine': 'gemini', 'model': MODEL, 'voice': VOICE, 'speed': DEFAULT_SPEED, 'backup_pacing': dict(audio.DEFAULT_PACING), 'full': None, 'status': 'waiting', 'error': None, 'message': 'Starting selected narration…'}
        return launch(job)


def resume(ident, brian=False):
    from app.tasks import lock as task_lock, ensure_idle
    with task_lock, lock:
        ensure_idle(); job = load(ident); fresh(job)
        if job['status'] not in ('failed', 'paused'): raise ValueError('This selection cannot be resumed.')
        if brian: job['engine'] = 'brian'
        for section in job['sections']:
            if not section['recording'] and section.get('pending_provider') == 'gemini' and section.get('pending_file'):
                returned = safe(directory(job['id']) / section['pending_file'])
                if returned.is_file():
                    try:
                        with wave.open(str(returned), 'rb') as stream: duration = stream.getnframes() / stream.getframerate()
                    except wave.Error: raise ValueError('Saved recording is damaged. Restore it or start a new selection.') from None
                    section['recording'] = {'file': returned.name, 'sha256': checksum(returned), 'provider': 'gemini', 'voice': VOICE, 'duration': duration}
                    save(job)
            if section['recording']: metadata_file(job, section['recording'])
        if job['engine'] == 'gemini' and any(not section['recording'] for section in job['sections']): choose_key(set())
        return launch(job)


def recover():
    for file,job in errors.recovery_records(safe(storage.DATA / 'grouped-audio'),'grouped-audio'):
        if job['status'] in ('running', 'stopping'):
            job.update(status='paused', message='The app closed. Returned recordings are saved. Resume when ready.'); save(job)


def shutdown():
    cancel.set()
    if worker and worker.is_alive(): worker.join(930)


def view(job):
    result = {k: copy.deepcopy(job.get(k)) for k in ('id', 'created_at', 'status', 'message', 'engine', 'error', 'current_section')}
    result.update(speed=job.get('speed', 1.), manga=job['source']['manga'], chapters=[c['chapter'] for c in job['source']['chapters']], busy=active is not None, full=bool(job.get('full')),
                  total_sections=len(job['sections']), completed=sum(bool(s['timing']) for s in job['sections']),
                  sections=[{'number': s['number'], 'words': s['words'], 'chapters': list(dict.fromkeys(r['chapter'] for r in s['refs'])),
                             'recorded': bool(s['recording']), 'timed': bool(s['timing']), 'provider': s['recording']['provider'] if s['recording'] else None, 'key_label': s['recording'].get('key_label') if s['recording'] else None,
                             'duration': s['recording']['duration'] if s['recording'] else None} for s in job['sections']])
    return result


def latest():
    files = sorted(safe(storage.DATA / 'grouped-audio').glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True)
    return view(load(active)) if active else view(load(files[0].stem)) if files else None


def endpoint(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        from app.read_cache import read_snapshot
        try:
            with read_snapshot(): return fn(*args, **kwargs)
        except (ValueError, OSError, AudioError) as exc: raise HTTPException(409 if isinstance(exc, FileExistsError) else 400, str(exc))
    return wrapped


class Selection(BaseModel):
    manga_id: str = Field(min_length=1, max_length=128)
    chapters: list[str] = Field(min_length=1, max_length=500)
    free_tier_confirmed: bool = True


@router.get('/keys')
@endpoint
def keys_api(): return key_status()


@router.get('/status')
@endpoint
def status_api(): return latest()


@router.post('/start')
@endpoint
def start_api(body: Selection): return start(body.manga_id, body.chapters, body.free_tier_confirmed)


@router.post('/jobs/{ident}/{action}')
@endpoint
def action_api(ident: str, action: str):
    if action in ('resume', 'brian'): return resume(ident, action == 'brian')
    if action == 'stop':
        with lock:
            if active != ident: raise ValueError('This audio selection is not running.')
            cancel.set(); job = load(ident); job.update(status='stopping', message='Stopping safely. If Google is already generating a section, its returned recording will be saved before stopping.'); save(job); return view(job)
    if action == 'folder':
        load(ident); open_system_folder(directory(ident)); return {'opened': True}
    raise ValueError('Unknown audio action.')


@router.get('/jobs/{ident}/audio')
@endpoint
def audio_api(ident: str, section: int | None = None, download: bool = False):
    job = load(ident); fresh(job)
    if section is None:
        if not job.get('full'): raise ValueError('Finish all selected sections first.')
        metadata = job['full']; mime = 'audio/mpeg'
    else:
        if not 1 <= section <= len(job['sections']): raise ValueError('Section not found.')
        metadata = job['sections'][section - 1]['recording']; mime = 'audio/wav'
        if not metadata: raise ValueError('This section has not returned yet.')
    file = metadata_file(job, metadata)
    return FileResponse(file, media_type=mime, filename='Selected-chapters-Audio.mp3' if download and section is None else None)
