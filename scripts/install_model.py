"""Download the pinned official model directly into this repository."""
import json
import os
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
REVISION = '49c73a225122d53adbaa26d53868be81a57706e2'
MODEL_ID = 'ragavsachdeva/magiv3'
DEST = ROOT / 'vendor' / 'magiv3'

def install():
    DEST.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(f'https://huggingface.co/api/models/{MODEL_ID}/revision/{REVISION}') as response:
        info = json.load(response)
    for entry in info['siblings']:
        name = entry['rfilename']
        if name == '.gitattributes':
            continue
        target = DEST / name
        if target.exists() and target.stat().st_size:
            continue
        print(f'Downloading {name}', flush=True)
        tmp = target.with_suffix(target.suffix + '.part')
        urllib.request.urlretrieve(f'https://huggingface.co/{MODEL_ID}/resolve/{REVISION}/{name}', tmp)
        os.replace(tmp, target)
    (DEST / 'source-info.json').write_text(json.dumps(info, indent=2))
    print(f'Installed {MODEL_ID}@{REVISION} in {DEST}', flush=True)

if __name__ == '__main__':
    install()
