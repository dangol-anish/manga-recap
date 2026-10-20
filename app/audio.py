"""Chapter-scoped speech. Sequential online synthesis; atomic local MP3s."""
from app.tasks import lock as task_lock, ensure_idle
import asyncio
import hashlib
import json
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

from app import narration, storage

VOICE = 'en-US-AndrewMultilingualNeural'
DEFAULT_PACING={'rate':'+20%','speed':1.2,'sentence_pause':.18}
# Base trimming profile; approved per-chapter speed/period pauses live in pacing.json.
PAUSE_PROFILE = 'pcm-rms-v1:threshold=-45dB:min-gap=0.20:keep=0.10:window=0.01'
lock = threading.RLock()
jobs = {}

def job_key(mid, chapter):
    return mid if chapter=='Chapter 1' else (mid,chapter)


def root(manga_id, chapter='Chapter 1'):
    return narration.root(manga_id, chapter) / 'audio'


def pacing(manga_id, chapter='Chapter 1'):
    saved=storage.read_json(root(manga_id,chapter)/'pacing.json')
    if saved is not None:return saved
    manga=next((m for m in storage.read_json(storage.DATA/'library.json',[]) if m['id']==manga_id),{})
    if manga.get('pacing'):return dict(manga['pacing'])
    # Older libraries retain their saved preference without rewriting their data.
    profiles=[p for p in (storage.OUTPUTS/manga_id).glob('*/stage3/audio/pacing.json') if p.resolve().is_relative_to(storage.ROOT)]
    return storage.read_json(max(profiles,key=lambda p:p.stat().st_mtime_ns),{}) if profiles else {}


def manifest(manga_id, chapter='Chapter 1'):
    return storage.read_json(root(manga_id, chapter) / 'audio.json', {'clips': {}})


def beats(manga_id, chapter):
    status = narration.status(manga_id, chapter)  # Requires fresh imported narration.
    if not status['batches'] or not all(b['imported'] for b in status['batches']):
        raise FileExistsError('Import all current narration batches before generating audio.')
    state = narration.load_state(manga_id)
    result = []
    for batch in status['batches']:
        for beat in state['chapters'][chapter]['batches'][batch['id']]['beats']:
            token_data=[VOICE, PAUSE_PROFILE, beat['panel_ids'], beat['narration']]
            if beat.get('anchors'):token_data.append(beat['anchors'])
            token = hashlib.sha256(json.dumps(token_data, ensure_ascii=False).encode()).hexdigest()
            result.append({'number': len(result)+1, 'panel_ids': beat['panel_ids'],
                           'narration': beat['narration'], 'token': token, **({'anchors':beat['anchors']} if beat.get('anchors') else {})})
    return result


def clip_path(manga_id, clip, chapter='Chapter 1'):
    name = clip.get('file', '')
    if not name or Path(name).name != name or not name.endswith('.mp3'):
        return None
    path = root(manga_id, chapter) / name
    return path if path.is_file() and path.stat().st_size > 0 else None


def status(manga_id, chapter):
    with lock:
        current = beats(manga_id, chapter)
        saved = manifest(manga_id, chapter)['clips']
        settings = pacing(manga_id, chapter)
        job = jobs.get(job_key(manga_id,chapter), {'running': False, 'message': 'Ready to generate chapter audio.'})
        for beat in current:
            clip = saved.get(beat['token'], {})
            beat['silent'] = not beat['narration'].strip()
            beat['ready'] = not beat['silent'] and clip_path(manga_id, clip, chapter) is not None and clip.get('pacing', {}) == settings
            beat['duration'] = clip.get('duration') if beat['ready'] else None
            beat['revision'] = clip.get('file') if beat['ready'] else None
            if beat['ready'] and clip.get('timing_anchors'):beat['timing_anchors']=clip['timing_anchors']
        return {'chapter': chapter, 'voice': VOICE, 'pacing': settings, 'beats': current,
                'ready': sum(b['ready'] for b in current),
                'spoken': sum(not b['silent'] for b in current), 'job': dict(job)}


def shorten_pauses(target, sentence_boundaries=None, sentence_pause=.18):
    """Trim measured quiet runs; optionally retain or insert a brief period pause."""
    import numpy as np
    executable = shutil.which('ffmpeg')
    if not executable:
        raise RuntimeError('FFmpeg is required to shorten speech pauses. Install FFmpeg and retry.')
    processed = target.with_name(target.name + '.processed.mp3')
    try:
        decoded = subprocess.run([executable, '-nostdin', '-hide_banner', '-loglevel', 'error',
                                  '-i', str(target), '-f', 's16le', '-ac', '1', '-ar', '24000', '-'],
                                 capture_output=True, timeout=45)
        if decoded.returncode:
            raise RuntimeError('Could not decode speech: ' + decoded.stderr.decode(errors='replace')[-600:])
        samples = np.frombuffer(decoded.stdout, dtype='<i2')
        window = 240  # 10 ms at 24 kHz.
        count = len(samples)//window
        if not count:
            raise ValueError('Speech audio is empty.')
        frames = samples[:count*window].astype(np.float32).reshape(-1, window)
        quiet = np.sqrt(np.mean(frames*frames, axis=1)) < 32768*10**(-45/20)
        boundaries = np.diff(np.r_[False, quiet, False].astype(np.int8))
        starts = np.where(boundaries == 1)[0]*window
        ends = np.where(boundaries == -1)[0]*window
        # Select the longest measured quiet run after each sentence's last word.
        # Speech metadata durations can include trailing silence; use word starts.
        sentence_runs = set()
        for boundary in sentence_boundaries or []:
            candidates = [(int(b-a), i) for i, (a,b) in enumerate(zip(starts,ends))
                          if a/24000 >= boundary['start']-.02 and b/24000 <= boundary['end']+.08]
            if not candidates:
                raise ValueError('No safe quiet gap found after a period; previous audio retained.')
            sentence_runs.add(max(candidates)[1])
        pieces = []
        removed = []
        cursor = 0
        for i, (start, end) in enumerate(zip(starts, ends)):
            length = int(end-start)
            if i in sentence_runs:
                keep = round(sentence_pause*24000)
                if length < keep:
                    middle = int((start+end)//2)
                    pieces.extend([samples[cursor:middle], np.zeros(keep-length, dtype='<i2')])
                    removed.append({'start':middle/24000, 'end':middle/24000, 'added':(keep-length)/24000})
                    cursor = middle
                    continue
            elif length >= 4800:
                keep = 2400  # Other long gaps still keep 100 ms.
            else:
                continue
            left = keep//2; right = keep-left
            pieces.append(samples[cursor:start+left])
            removed.append({'start':(int(start)+left)/24000, 'end':(int(end)-right)/24000})
            cursor = end-right
        pieces.append(samples[cursor:])
        pcm = np.concatenate(pieces).astype('<i2', copy=False).tobytes()
        encoded = subprocess.run([executable, '-nostdin', '-hide_banner', '-loglevel', 'error', '-y',
                                  '-f', 's16le', '-ac', '1', '-ar', '24000', '-i', '-', '-threads', '1',
                                  '-codec:a', 'libmp3lame', '-b:a', '96k', str(processed)],
                                 input=pcm, capture_output=True, timeout=45)
        if encoded.returncode:
            raise RuntimeError('Could not encode speech: ' + encoded.stderr.decode(errors='replace')[-600:])
        from mutagen.mp3 import MP3
        if MP3(processed).info.length <= 0:
            raise ValueError('Pause processing produced empty audio.')
        processed.replace(target)
        return removed
    finally:
        processed.unlink(missing_ok=True)


async def synthesize(text, target):
    import edge_tts
    await asyncio.wait_for(edge_tts.Communicate(text, VOICE).save(str(target)), timeout=120)
    shorten_pauses(target)


def generate_one(manga_id, chapter, beat):
    from mutagen.mp3 import MP3
    directory = root(manga_id, chapter)
    directory.mkdir(parents=True, exist_ok=True)
    revision = beat['token'] + '-' + uuid.uuid4().hex
    temporary = directory / ('.' + revision + '.tmp')
    target = directory / (revision + '.mp3')
    started = time.monotonic()
    try:
        settings = pacing(manga_id, chapter)
        spoken=None
        if settings or beat.get('anchors'):
            from app import alignment
            spoken=alignment.speech(beat['narration'], temporary, settings)
            if beat.get('anchors'):
                _,anchors=alignment.validate_map(beat,{'beat':beat['number'],'anchors':beat['anchors']})
                timed=alignment.anchor_times(beat,anchors,spoken['words'],spoken['duration'])
        else:
            asyncio.run(synthesize(beat['narration'], temporary))
        info = MP3(temporary).info
        if info.length <= 0:
            raise ValueError('The speech service returned empty audio.')
        with lock:
            if pacing(manga_id, chapter) != settings:
                raise FileExistsError('Speech pacing changed during generation. Retry this beat.')
            current = beats(manga_id, chapter)
            if not any(b['token'] == beat['token'] for b in current):
                raise FileExistsError('Narration changed during generation. Generate audio again for the current beats.')
            temporary.replace(target)
            saved = manifest(manga_id, chapter)
            saved['clips'][beat['token']] = {'file': target.name, 'duration': info.length, 'voice': VOICE, 'pause_filter': PAUSE_PROFILE, 'pacing': settings}
            if spoken and beat.get('anchors'):
                saved['clips'][beat['token']].update(timing_anchors=timed,words=spoken['words'])
            storage.write_json(directory / 'audio.json', saved)
        print(f'Audio beat {beat["number"]}: {info.length:.1f}s audio, {time.monotonic()-started:.1f}s generation', flush=True)
    finally:
        temporary.unlink(missing_ok=True)


def run(manga_id, chapter, selected, regenerate):
    started = time.monotonic()
    try:
        for index, beat in enumerate(selected, 1):
            with lock:
                jobs[job_key(manga_id,chapter)].update(message=f'Generating beat {beat["number"]} ({index}/{len(selected)})…')
                existing = manifest(manga_id, chapter)['clips'].get(beat['token'], {})
            if regenerate or clip_path(manga_id, existing, chapter) is None or existing.get('pacing', {}) != pacing(manga_id, chapter):
                generate_one(manga_id, chapter, beat)
            with lock:
                jobs[job_key(manga_id,chapter)]['completed'] = index
        with lock:
            jobs[job_key(manga_id,chapter)].update(message=f'Finished {len(selected)} beat(s) in {time.monotonic()-started:.1f}s.', error=False)
    except Exception as exc:
        with lock:
            jobs[job_key(manga_id,chapter)].update(message=f'Audio generation stopped: {type(exc).__name__}: {exc}. Completed clips are safe; retry to resume.', error=True)
    finally:
        with lock:
            jobs[job_key(manga_id,chapter)]['running'] = False


def start(manga_id, chapter, number=None):
    with task_lock, lock:
        ensure_idle()
        if jobs.get(job_key(manga_id,chapter), {}).get('running'):
            raise FileExistsError('Audio generation is already running. Wait for it to finish.')
        current = status(manga_id, chapter)['beats']
        if number is not None and not 1 <= number <= len(current):
            raise FileNotFoundError('Beat not found.')
        selected = [b for b in current if not b['silent'] and (b['number'] == number if number is not None else not b['ready'])]
        if not selected:
            raise ValueError('No speech to generate. Empty narration beats are intentionally silent; completed clips can be regenerated individually.')
        jobs[job_key(manga_id,chapter)] = {'running': True, 'completed': 0, 'total': len(selected), 'message': 'Starting speech generation…', 'error': False}
        threading.Thread(target=run, args=(manga_id, chapter, selected, number is not None), daemon=True).start()
        return dict(jobs[job_key(manga_id,chapter)])


def asset(manga_id, chapter, number):
    with lock:
        current = beats(manga_id, chapter)
        if not 1 <= number <= len(current):
            raise FileNotFoundError('Beat not found.')
        clip = manifest(manga_id, chapter)['clips'].get(current[number-1]['token'], {})
        path = clip_path(manga_id, clip, chapter)
        if path is None:
            raise FileNotFoundError('Generate audio for this beat first.')
        return path
