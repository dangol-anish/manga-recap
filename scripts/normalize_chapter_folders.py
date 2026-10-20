"""One-time migration for downloader-owned chapter folders from earlier versions.
Run only while the app is stopped: python -m scripts.normalize_chapter_folders
"""
from pathlib import Path
import uuid
from app import storage
from app.chapter_names import chapter_folder_name
from app.downloader import destination


def migrate():
    jobs = [storage.read_json(p) for p in (storage.DATA / 'downloader/jobs').glob('*.json')]
    moves = {}
    for job in jobs:
        for chapter in job['chapters']:
            old = chapter['folder']
            new = job['folder'] + '/' + chapter_folder_name(chapter['name'])
            if old == new: continue
            source, target = destination(old), destination(new)
            if not source.exists(): continue
            owner = storage.read_json(source / '.download-owner.json', {})
            if owner.get('job_id') not in {j['id'] for j in jobs}:
                raise ValueError(f'{old} is not a known downloader-owned folder; nothing was renamed.')
            if target.exists() or target.is_symlink():
                raise ValueError(f'{new} already exists; nothing was overwritten or merged.')
            moves[old] = new
    if len({new.casefold() for new in moves.values()}) != len(moves):
        raise ValueError('Two folders would have the same chapter name; nothing was renamed.')
    if not moves: return []
    # Existing downstream output folders would need a broader migration; do not break them.
    for manga in storage.read_json(storage.DATA / 'library.json', []):
        for old in moves:
            if manga['folder'] == str(Path(old).parent).replace('\\', '/') and (storage.OUTPUTS / manga['id'] / Path(old).name).exists():
                raise ValueError('This chapter has downstream outputs; migrate those references before renaming.')
    documents = {p: storage.read_json(p) for p in storage.DATA.rglob('*.json') if 'folder-migrations' not in p.parts}
    journal_path = storage.DATA / 'folder-migrations' / (uuid.uuid4().hex + '.json')
    journal = {'status': 'started', 'moves': moves, 'original_documents': {p.relative_to(storage.DATA).as_posix(): value for p, value in documents.items()}}
    storage.write_json(journal_path, journal)
    def rewrite(value):
        if isinstance(value, str):
            for old, new in moves.items():
                if value == old or value.startswith(old + '/'): return new + value[len(old):]
            return value
        if isinstance(value, list): return [rewrite(v) for v in value]
        if isinstance(value, dict):
            result = {k: rewrite(v) for k,v in value.items()}
            if value.get('folder') in moves and 'name' in result:
                result['name'] = Path(moves[value['folder']]).name
            return result
        return value
    done = []
    try:
        for old, new in moves.items():
            destination(old).rename(destination(new))
            done.append((old, new))
        for path, value in documents.items():
            updated = rewrite(value)
            if updated != value: storage.write_json(path, updated)
    except Exception:
        for path, value in documents.items(): storage.write_json(path, value)
        for old, new in reversed(done):
            if destination(old).exists(): raise RuntimeError('Rollback destination is occupied. Original records are backed up in data/folder-migrations/.')
            destination(new).rename(destination(old))
        journal['status'] = 'rolled_back'
        storage.write_json(journal_path, journal)
        raise
    journal['status'] = 'complete'
    storage.write_json(journal_path, journal)
    return list(moves.items())


if __name__ == '__main__':
    for old, new in migrate(): print(f'{old} -> {new}')
