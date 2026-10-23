"""Local installation checks. Never processes manga or contacts TTS services."""
import argparse
from importlib import import_module, metadata
import json
import os
from pathlib import Path
import platform
import shutil
import socket
import struct
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def media_check(directory):
    """A tiny real export exercises codecs, filters, Unicode and spaced paths."""
    from PIL import Image
    from app import video
    work = directory / "Manga café's test"
    work.mkdir()
    crop = work / 'panel.png'
    Image.new('RGB', (160, 240), '#339989').save(crop)
    audio = work / 'voice.mp3'
    subprocess.run(['ffmpeg','-nostdin','-v','error','-f','lavfi','-i','anullsrc=r=24000:cl=mono',
                    '-t','1','-c:a','libmp3lame',str(audio)],capture_output=True,check=True,timeout=30)
    panels = [{'id': f'p0000-{i+1}', '_crop': str(crop), '_frame': str(work / f'frame{i}.png')} for i in range(2)]
    state = {'duration': 1.0, 'beats': [{'duration': 1.0, 'switches': [.5], 'panels': panels, '_audio': str(audio)}]}
    return video.render_to(state, work).is_file()


def run(media=False, model=False):
    results = []
    def check(name, action):
        try:
            detail = action()
            results.append({'name': name, 'ok': True, 'detail': str(detail or 'OK')})
        except Exception as exc:
            results.append({'name': name, 'ok': False, 'detail': f'{type(exc).__name__}: {exc}'})
    def python_check():
        if struct.calcsize('P') != 8 or sys.version_info[:2] != (3, 11):
            raise RuntimeError('Use 64-bit Python 3.11 for this supported setup.')
        return sys.version.split()[0] + ' / ' + platform.machine()
    check('Python 3.11, 64-bit', python_check)
    def versions():
        from packaging.requirements import Requirement
        for line in (ROOT / 'requirements.lock.txt').read_text(encoding='utf-8').splitlines():
            if not line.strip() or line.startswith('#'): continue
            req = Requirement(line)
            if req.marker and not req.marker.evaluate(): continue
            found = metadata.version(req.name)
            if found not in req.specifier:
                raise RuntimeError(f'{req.name}: installed {found}; requires {req.specifier}. Re-run setup-windows.bat.')
        return 'Pinned dependency versions match'
    check('Dependencies', versions)
    def imports():
        from app import detector  # Configure repo-local model/font caches first.
        for module in ('fastapi','uvicorn','PIL','numpy','torch','torchvision','transformers','timm','einops','pytorch_metric_learning','matplotlib','shapely','networkx','httpx','edge_tts','mutagen'):
            import_module(module)
        import torch, torchvision, numpy as np
        torchvision.ops.nms(torch.tensor([[0.,0.,2.,2.]]), torch.tensor([1.]), .5)
        torch.from_numpy(np.zeros(1, dtype=np.float32))
        return 'Imports and PyTorch/torchvision/NumPy interoperation work'
    check('Native libraries', imports)
    def ffmpeg():
        for executable in ('ffmpeg', 'ffprobe'):
            resolved = shutil.which(executable)
            if not resolved: raise RuntimeError(f'{executable} is missing from PATH. Install FFmpeg, then open a new terminal.')
            subprocess.run([resolved, '-version'], capture_output=True, check=True, timeout=20)
        return 'ffmpeg and ffprobe run'
    check('FFmpeg', ffmpeg)
    # A fresh ignored directory is used only for these small synthetic checks.
    try:
        with tempfile.TemporaryDirectory(prefix='.setup-check-', dir=ROOT) as name:
            directory = Path(name)
            def filesystem():
                from app.storage import read_json, write_json
                from app.runtime import acquire_server_lock
                first = directory / 'original'; first.write_bytes(b'fixture')
                linked = directory / 'linked'; os.link(first, linked)
                if not os.path.samefile(first, linked): raise RuntimeError('Hard links are not supported. Use a local NTFS disk on Windows.')
                lockfile = directory / 'lock'
                handle = acquire_server_lock(lockfile)
                try:
                    try: other = acquire_server_lock(lockfile)
                    except RuntimeError: pass
                    else:
                        other.close(); raise RuntimeError('The filesystem did not enforce the worker lock.')
                finally: handle.close()
                record = directory / 'café.json'; write_json(record, {'text': '日本語 — café'})
                write_json(record, {'text': '日本語 — updated'})
                if read_json(record)['text'] != '日本語 — updated': raise RuntimeError('JSON replacement failed')
                return 'Write, hard links, exclusive locks and atomic UTF-8 JSON work'
            check('Repository filesystem', filesystem)
            if media: check('Real MP4 encode and phrase-transition filters', lambda: media_check(directory))
    except OSError as exc:
        results.append({'name': 'Writable repository', 'ok': False, 'detail': str(exc)})
    if model:
        def model_check():
            from scripts.install_model import DEST, REVISION, verified
            info = json.loads((DEST / 'source-info.json').read_text(encoding='utf-8'))
            if info.get('sha') != REVISION: raise RuntimeError('Model revision mismatch. Run scripts.install_model again.')
            for entry in info['siblings']:
                if entry['rfilename'] == '.gitattributes': continue
                if not verified(DEST / entry['rfilename'], entry):
                    raise RuntimeError('Unverified model file: ' + entry['rfilename'] + '. Run scripts.install_model again.')
            return 'Pinned model file sizes and hashes match; no inference was run'
        check('Model integrity', model_check)
    print('Manga Recap installation check')
    for item in results: print(('PASS ' if item['ok'] else 'FAIL ') + item['name'] + ': ' + item['detail'])
    if len(str(ROOT)) > 60: print('NOTE: Use a shorter clone path, such as C:\\manga-recap, to reduce Windows/FFmpeg path-length problems.')
    free = shutil.disk_usage(ROOT).free / 1024**3
    print(f'Free disk space: {free:.1f} GiB. Manga, crops and videos need additional space.')
    with socket.socket() as server:
        try: server.bind(('127.0.0.1', 8000))
        except OSError: print('NOTE: Port 8000 is in use. Stop the earlier app before starting another copy.')
    print('This check does not test live download websites, online speech, or real manga detection.')
    return 0 if all(item['ok'] for item in results) else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--media', action='store_true', help='Render a one-second synthetic MP4, then remove it.')
    parser.add_argument('--model', action='store_true', help='Check the installed official model hashes; no inference.')
    options = parser.parse_args()
    raise SystemExit(run(options.media, options.model))
