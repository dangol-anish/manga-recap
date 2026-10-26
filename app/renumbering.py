"""Explicit, journaled, collision-safe renumbering of repo-local chapter images."""
from app import errors
from hashlib import sha256
from pathlib import PurePosixPath
import os
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from app import storage

router = APIRouter(prefix='/api/renumbering')
EXTENSIONS = storage.IMAGE_EXTENSIONS | {'.gif', '.bmp', '.tif', '.tiff', '.avif'}


def folder_path(relative):
    if not isinstance(relative, str) or '\\' in relative or ':' in relative:
        raise ValueError('Choose chapter folders inside this repository’s manga folder.')
    parts = PurePosixPath(relative).parts
    if len(parts) < 2 or parts[0] != 'manga' or any(p.startswith('.') for p in parts):
        raise ValueError('Choose chapter folders inside manga/.')
    current = storage.ROOT
    for part in parts:
        current /= part
        if current.is_symlink(): raise ValueError('Symbolic-link folders cannot be renumbered.')
    if not current.is_dir(): raise ValueError('Chapter folder no longer exists. Refresh the list.')
    return current


def images(folder):
    return sorted((p for p in folder.iterdir() if not p.name.startswith('.') and not p.is_symlink() and p.is_file() and p.suffix.lower() in EXTENSIONS), key=lambda p: (storage.natural_key(p), p.name))


def document(kind, ident):
    if len(ident) != 32 or any(c not in '0123456789abcdef' for c in ident):
        raise ValueError('Invalid renumbering record.')
    return storage.DATA / 'renumbering' / kind / (ident + '.json')


def read_document(kind, ident):
    value = storage.read_json(document(kind, ident))
    if not value: raise ValueError('Renumbering record not found. Preview again.')
    return value


def marker(folder):
    path = folder / '.renumbering.json'
    if path.is_symlink(): raise ValueError('Unsafe renumbering marker.')
    return storage.read_json(path)


def snapshot(folder):
    return [{'name': p.name, 'size': p.stat().st_size, 'mtime_ns': p.stat().st_mtime_ns} for p in images(folder)]


def folders():
    root = storage.ROOT / 'manga'
    if not root.exists(): return []
    if root.is_symlink(): raise ValueError('manga/ cannot be a symbolic link.')
    result = []
    for directory, dirs, _ in os.walk(root, followlinks=False):
        path = storage.ROOT / directory
        dirs[:] = [d for d in dirs if not d.startswith('.') and not (path / d).is_symlink()]
        if path == root: continue
        relative = path.relative_to(storage.ROOT).as_posix()
        page_files = images(path)
        saved = marker(path)
        pending = saved and saved.get('status') != 'complete'
        if page_files or pending:
            result.append({'id': sha256(relative.encode()).hexdigest()[:32], 'folder': relative, 'manga': path.parent.name if path.parent != root else path.name, 'name': path.name, 'images': len(page_files), 'pending': bool(pending)})
    from app.chapter_names import chapter_sort_key
    return sorted(result, key=lambda c: (str(PurePosixPath(c['folder']).parent), chapter_sort_key(c['name'])))


def mapping(files):
    return [{'from': p['name'], 'to': f'{i:04d}{PurePosixPath(p["name"]).suffix}'} for i, p in enumerate(files)]


def preview(ids):
    catalog = {entry['id']: entry for entry in folders()}
    if not ids or len(set(ids)) != len(ids) or any(ident not in catalog for ident in ids):
        raise ValueError('Choose valid chapter folders from the list.')
    plan = {'id': uuid.uuid4().hex, 'status': 'preview', 'chapters': []}
    for ident in ids:
        entry = catalog[ident]
        folder = folder_path(entry['folder'])
        saved = marker(folder)
        if saved and saved.get('status') != 'complete':
            transaction = read_document('transactions', saved['id'])
            entry = {**entry, 'transaction': transaction['id'], 'files': [], 'mapping': [{'from': e['from'], 'to': e['to']} for e in transaction['entries']]}
        else:
            files = snapshot(folder)
            entry = {**entry, 'files': files, 'mapping': mapping(files)}
        entry['images'] = len(entry['mapping'])
        entry['changes'] = sum(p['from'] != p['to'] for p in entry['mapping'])
        plan['chapters'].append(entry)
    storage.write_json(document('plans', plan['id']), plan)
    return plan


def digest(path):
    if path.is_symlink() or not path.is_file(): raise ValueError('A source file is missing or unsafe.')
    h = sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''): h.update(chunk)
    return h.hexdigest()


def move_exclusive(source, target, checksum):
    """Hard-link then unlink: unlike POSIX rename, this never replaces a target."""
    if target.is_symlink() or source.is_symlink(): raise ValueError('Symbolic-link images cannot be renamed.')
    if not source.exists():
        if target.is_file() and digest(target) == checksum: return  # interrupted after unlink
        raise ValueError('An image disappeared during renumbering. Existing files were preserved.')
    if digest(source) != checksum: raise ValueError('An image changed during renumbering. Preview again after recovery.')
    if target.exists():
        if os.path.samefile(source, target):
            source.unlink()  # interrupted between link and unlink
            return
        raise FileExistsError(f'{target.name} already exists; it was not overwritten.')
    try:
        os.link(source, target, follow_symlinks=False)
    except FileExistsError:
        raise FileExistsError(f'{target.name} already exists; it was not overwritten.')
    except OSError as exc:
        raise OSError('Safe renaming requires a local filesystem supporting hard links (APFS/NTFS). Your images were preserved.') from exc
    source.unlink()


def save_transaction(transaction, folder):
    storage.write_json(document('transactions', transaction['id']), transaction)
    storage.write_json(folder / '.renumbering.json', {'id': transaction['id'], 'status': transaction['status']})


def execute(chapter):
    folder = folder_path(chapter['folder'])
    saved = marker(folder)
    if chapter.get('transaction'):
        if not saved or saved['id'] != chapter['transaction']: raise ValueError('Pending transaction changed. Preview again.')
        tx = read_document('transactions', chapter['transaction'])
    else:
        if saved and saved.get('status') != 'complete': raise ValueError('This folder has an interrupted operation. Refresh and preview to continue it.')
        if snapshot(folder) != chapter['files']: raise ValueError('Files changed since the preview. Preview again before renumbering.')
        original_names = {entry['from'] for entry in chapter['mapping']}
        for entry in chapter['mapping']:
            target = folder / entry['to']
            if target.is_symlink() or (target.exists() and (target.name not in original_names or not target.is_file())):
                raise FileExistsError(f'{target.name} is occupied; it was not overwritten.')
        tx = {'id': uuid.uuid4().hex, 'folder': chapter['folder'], 'status': 'running', 'phase': 'staging', 'entries': [{**item, 'sha256': digest(folder / item['from'])} for item in chapter['mapping']]}
        # Plan records which transaction owns this folder before any file moves.
        chapter['transaction'] = tx['id']
        save_transaction(tx, folder)
    if tx['status'] == 'complete': return {'folder': tx['folder'], 'images': len(tx['entries']), 'renamed': sum(e['from'] != e['to'] for e in tx['entries']), 'status': 'complete'}
    staging = folder / ('.renumber-' + tx['id'])
    if staging.is_symlink(): raise ValueError('Unsafe temporary renumbering folder.')
    staging.mkdir(exist_ok=True)
    tx['status'] = 'running'
    save_transaction(tx, folder)
    try:
        if tx['phase'] == 'staging':
            for index, entry in enumerate(tx['entries']):
                move_exclusive(folder / entry['from'], staging / str(index), entry['sha256'])
            if images(folder): raise ValueError('New images appeared during renumbering. They were not overwritten. Remove the conflicting additions before continuing.')
            tx['phase'] = 'publishing'
            save_transaction(tx, folder)
        for index, entry in enumerate(tx['entries']):
            move_exclusive(staging / str(index), folder / entry['to'], entry['sha256'])
        tx.update(status='complete', phase='complete')
        save_transaction(tx, folder)
        staging.rmdir()
        return {'folder': tx['folder'], 'images': len(tx['entries']), 'renamed': sum(e['from'] != e['to'] for e in tx['entries']), 'status': 'complete'}
    except Exception:
        tx['status'] = 'interrupted'
        try:save_transaction(tx, folder)
        except Exception as checkpoint_error:errors.warning(checkpoint_error,'renumbering','Save renumbering recovery transaction',folder)
        raise


def apply(ident):
    from app.tasks import lock, ensure_idle
    with lock:
        ensure_idle()
        plan = read_document('plans', ident)
        if plan['status'] == 'complete': return plan
        results = []
        for chapter in plan['chapters']:
            try:
                results.append(execute(chapter))
            except (ValueError, OSError) as exc:
                detail=errors.describe(exc,'renumbering','Renumber selected images',chapter['folder'])
                results.append({'folder': chapter['folder'], 'status': 'failed', 'error': detail['message'],'error_details':detail})
            storage.write_json(document('plans', ident), plan)
        plan.update(status='complete' if all(r['status'] == 'complete' for r in results) else 'needs_attention', results=results)
        storage.write_json(document('plans', ident), plan)
        return plan


class Selection(BaseModel):
    chapter_ids: list[str] = Field(min_length=1, max_length=2000)


class PlanInput(BaseModel):
    plan_id: str


@router.get('/chapters')
def chapters_api():
    try: return folders()
    except (OSError, ValueError) as exc: raise HTTPException(400, str(exc))


@router.post('/preview')
def preview_api(body: Selection):
    try: return preview(body.chapter_ids)
    except (OSError, ValueError) as exc: raise HTTPException(400, str(exc))


@router.post('/apply')
def apply_api(body: PlanInput):
    try: return apply(body.plan_id)
    except FileExistsError as exc: raise HTTPException(409, str(exc))
    except (OSError, ValueError) as exc: raise HTTPException(400, str(exc))


@router.post('/renumber')
def renumber_api(body: Selection):
    from app.tasks import lock, ensure_idle
    try:
        with lock:
            ensure_idle()
            return apply(preview(body.chapter_ids)['id'])
    except FileExistsError as exc: raise HTTPException(409, str(exc))
    except (OSError, ValueError) as exc: raise HTTPException(400, str(exc))


@router.post('/plans/{ident}/folders/{index}/open')
def open_result_folder(ident: str, index: int):
    from app.runtime import open_system_folder
    import subprocess
    try:
        plan = read_document('plans', ident)
        results = plan.get('results', [])
        if index < 0 or index >= len(results) or results[index]['status'] != 'complete':
            raise ValueError('Choose a successfully renumbered chapter.')
        folder = folder_path(results[index]['folder'])
        open_system_folder(folder)
        return {'opened': True}
    except ValueError as exc: raise HTTPException(400, str(exc))
    except (OSError, subprocess.SubprocessError) as exc:
        raise HTTPException(500, 'Could not open the folder. Please try again.') from exc
