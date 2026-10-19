"""Chapter 1 speech only. Sequential online synthesis; atomic local MP3s."""
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
# Keep the voice's delivery speed; shorten actual quiet gaps instead of changing text.
PAUSE_PROFILE = 'pcm-rms-v1:threshold=-45dB:min-gap=0.20:keep=0.10:window=0.01'
lock = threading.RLock()
jobs = {}


def root(manga_id):
    return narration.root(manga_id) / 'audio'


def manifest(manga_id):
    return storage.read_json(root(manga_id) / 'audio.json', {'clips': {}})


def beats(manga_id, chapter):
    status = narration.status(manga_id, chapter)  # Enforces Chapter 1 and registered input.
    if not status['batches'] or not all(b['imported'] for b in status['batches']):
        raise FileExistsError('Import both current Chapter 1 narration batches before generating audio.')
    state = narration.load_state(manga_id)
    result = []
    for batch in status['batches']:
        for beat in state['chapters'][chapter]['batches'][batch['id']]['beats']:
            token = hashlib.sha256(json.dumps([VOICE, PAUSE_PROFILE, beat['panel_ids'], beat['narration']], ensure_ascii=False).encode()).hexdigest()
            result.append({'number': len(result)+1, 'panel_ids': beat['panel_ids'],
                           'narration': beat['narration'], 'token': token})
    return result


def clip_path(manga_id, clip):
    name = clip.get('file', '')
    if not name or Path(name).name != name or not name.endswith('.mp3'):
        return None
    path = root(manga_id) / name
    return path if path.is_file() and path.stat().st_size > 0 else None


def status(manga_id, chapter):
    with lock:
        current = beats(manga_id, chapter)
        saved = manifest(manga_id)['clips']
        job = jobs.get(manga_id, {'running': False, 'message': 'Ready to generate Chapter 1 audio.'})
        for beat in current:
            clip = saved.get(beat['token'], {})
            beat['silent'] = not beat['narration'].strip()
            beat['ready'] = not beat['silent'] and clip_path(manga_id, clip) is not None
            beat['duration'] = clip.get('duration') if beat['ready'] else None
            beat['revision'] = clip.get('file') if beat['ready'] else None
        return {'chapter': chapter, 'voice': VOICE, 'beats': current,
                'ready': sum(b['ready'] for b in current),
                'spoken': sum(not b['silent'] for b in current), 'job': dict(job)}


def shorten_pauses(target):
    """Shorten quiet runs, retaining 50 ms at each edge; never accelerate speech."""
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
        pieces = []
        cursor = 0
        for start, end in zip(starts, ends):
            if end-start < 4800:  # Preserve gaps shorter than 200 ms.
                continue
            # Retain both quiet edges so joins do not cut into neighboring words.
            pieces.append(samples[cursor:start+1200])
            cursor = end-1200
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
    finally:
        processed.unlink(missing_ok=True)


async def synthesize(text, target):
    import edge_tts
    await asyncio.wait_for(edge_tts.Communicate(text, VOICE).save(str(target)), timeout=120)
    shorten_pauses(target)


def generate_one(manga_id, chapter, beat):
    from mutagen.mp3 import MP3
    directory = root(manga_id)
    directory.mkdir(parents=True, exist_ok=True)
    revision = beat['token'] + '-' + uuid.uuid4().hex
    temporary = directory / ('.' + revision + '.tmp')
    target = directory / (revision + '.mp3')
    started = time.monotonic()
    try:
        asyncio.run(synthesize(beat['narration'], temporary))
        info = MP3(temporary).info
        if info.length <= 0:
            raise ValueError('The speech service returned empty audio.')
        with lock:
            current = beats(manga_id, chapter)
            if not any(b['token'] == beat['token'] for b in current):
                raise FileExistsError('Narration changed during generation. Generate audio again for the current beats.')
            temporary.replace(target)
            saved = manifest(manga_id)
            saved['clips'][beat['token']] = {'file': target.name, 'duration': info.length, 'voice': VOICE, 'pause_filter': PAUSE_PROFILE}
            storage.write_json(directory / 'audio.json', saved)
        print(f'Audio beat {beat["number"]}: {info.length:.1f}s audio, {time.monotonic()-started:.1f}s generation', flush=True)
    finally:
        temporary.unlink(missing_ok=True)


def run(manga_id, chapter, selected, regenerate):
    started = time.monotonic()
    try:
        for index, beat in enumerate(selected, 1):
            with lock:
                jobs[manga_id].update(message=f'Generating beat {beat["number"]} ({index}/{len(selected)})…')
                existing = manifest(manga_id)['clips'].get(beat['token'], {})
            if regenerate or clip_path(manga_id, existing) is None:
                generate_one(manga_id, chapter, beat)
            with lock:
                jobs[manga_id]['completed'] = index
        with lock:
            jobs[manga_id].update(message=f'Finished {len(selected)} beat(s) in {time.monotonic()-started:.1f}s.', error=False)
    except Exception as exc:
        with lock:
            jobs[manga_id].update(message=f'Audio generation stopped: {type(exc).__name__}: {exc}. Completed clips are safe; retry to resume.', error=True)
    finally:
        with lock:
            jobs[manga_id]['running'] = False


def start(manga_id, chapter, number=None):
    with lock:
        if jobs.get(manga_id, {}).get('running'):
            raise FileExistsError('Audio generation is already running. Wait for it to finish.')
        current = status(manga_id, chapter)['beats']
        if number is not None and not 1 <= number <= len(current):
            raise FileNotFoundError('Beat not found.')
        selected = [b for b in current if not b['silent'] and (b['number'] == number if number is not None else not b['ready'])]
        if not selected:
            raise ValueError('No speech to generate. Empty narration beats are intentionally silent; completed clips can be regenerated individually.')
        jobs[manga_id] = {'running': True, 'completed': 0, 'total': len(selected), 'message': 'Starting speech generation…', 'error': False}
        threading.Thread(target=run, args=(manga_id, chapter, selected, number is not None), daemon=True).start()
        return dict(jobs[manga_id])


def asset(manga_id, chapter, number):
    with lock:
        current = beats(manga_id, chapter)
        if not 1 <= number <= len(current):
            raise FileNotFoundError('Beat not found.')
        clip = manifest(manga_id)['clips'].get(current[number-1]['token'], {})
        path = clip_path(manga_id, clip)
        if path is None:
            raise FileNotFoundError('Generate audio for this beat first.')
        return path
