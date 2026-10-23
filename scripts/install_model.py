"""Download and verify the pinned official model into this repository only."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import sys
import time
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ''):
    sys.path.insert(0, str(ROOT))
from app.storage import write_json

REVISION = '49c73a225122d53adbaa26d53868be81a57706e2'
MODEL_ID = 'ragavsachdeva/magiv3'
DEST = ROOT / 'vendor' / 'magiv3'


def verified(path, entry):
    if path.is_symlink() or not path.is_file():
        return False
    size = entry.get('size')
    if not isinstance(size, int) or path.stat().st_size != size:
        return False
    lfs = entry.get('lfs') or {}
    expected = lfs.get('sha256') or entry.get('blobId')
    if not expected:
        return False
    digest = hashlib.sha256() if lfs else hashlib.sha1()
    if not lfs:
        digest.update(f'blob {size}\0'.encode('ascii'))
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest() == expected


def install(source_only=False):
    if not DEST.resolve().is_relative_to(ROOT.resolve()) or any(p.is_symlink() for p in (DEST, *DEST.parents) if p.is_relative_to(ROOT)):
        raise ValueError('Model folder cannot be a symbolic link.')
    DEST.mkdir(parents=True, exist_ok=True)
    url = f'https://huggingface.co/api/models/{MODEL_ID}/revision/{REVISION}?blobs=true'
    with urllib.request.urlopen(url, timeout=60) as response:
        info = json.load(response)
    if info.get('sha') != REVISION:
        raise ValueError('The model source did not match the pinned revision.')
    for entry in info['siblings']:
        name = entry['rfilename']
        if source_only and name.endswith(('.safetensors', '.bin', '.pt', '.pth')):
            continue
        if name == '.gitattributes':
            continue
        parts = PurePosixPath(name).parts
        if not parts or PurePosixPath(name).is_absolute() or '\\' in name or ':' in name or any(p in ('.', '..') for p in parts):
            raise ValueError('Unsafe model source filename.')
        target = DEST.joinpath(*parts)
        if any(p.is_symlink() for p in (target, *target.parents) if p.is_relative_to(DEST)):
            raise ValueError('Model files cannot use symbolic links.')
        target.parent.mkdir(parents=True, exist_ok=True)
        if verified(target, entry):
            print(f'Verified {name}', flush=True)
            continue
        tmp = target.with_name(target.name + '.part')
        if tmp.is_symlink():
            raise ValueError('Unsafe partial model file.')
        print(f'Downloading {name}', flush=True)
        for attempt in range(3):
            try:
                url = f'https://huggingface.co/{MODEL_ID}/resolve/{REVISION}/' + urllib.parse.quote(name, safe='/')
                with urllib.request.urlopen(url, timeout=60) as response, tmp.open('wb') as output:
                    for chunk in iter(lambda: response.read(1024 * 1024), b''):
                        output.write(chunk)
                    output.flush()
                    os.fsync(output.fileno())
                if not verified(tmp, entry):
                    raise ValueError(f'Model file failed size/hash verification: {name}')
                os.replace(tmp, target)
                break
            except Exception:
                tmp.unlink(missing_ok=True)
                if attempt == 2:
                    raise
                time.sleep(attempt + 1)
    write_json(DEST / 'source-info.json', info)
    print(f'Verified {MODEL_ID}@{REVISION} in {DEST}' + (' (source only; detection still needs weights)' if source_only else ''), flush=True)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-only', action='store_true', help='For offline fixture tests: omit weights; does not enable detection.')
    install(source_only=parser.parse_args().source_only)
