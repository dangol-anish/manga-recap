import json
import os
from pathlib import Path
import re
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data'
OUTPUTS = ROOT / 'outputs'
IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.webp'}
DIRECTIONS = {'rtl', 'ltr'}


def read_json(path, default=None):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (json.JSONDecodeError, UnicodeError) as exc:
        from app.errors import SavedDataError
        raise SavedDataError(path, exc) from exc


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        # Windows readers/virus scanners can briefly deny delete-sharing on a
        # closed-soon JSON handle. Retry only these transient sharing errors;
        # leave the old committed document intact on a persistent failure.
        for attempt in range(8):
            try:
                os.replace(name, path)
                break
            except PermissionError as exc:
                if getattr(exc, 'winerror', None) not in (5, 32, 33) or attempt == 7:
                    raise
                time.sleep(min(.01 * 2**attempt, .1))
    except OSError as exc:
        if not exc.filename:exc.filename=str(path)
        raise
    finally:
        if os.path.exists(name):
            os.unlink(name)


def local_path(value):
    path = (ROOT / value).resolve()
    if not path.is_relative_to(ROOT) or path == ROOT:
        raise ValueError('Choose a manga folder inside this repository.')
    # Keep private app/runtime folders out of the manga library.
    relative = path.relative_to(ROOT)
    if any(part.startswith('.') for part in relative.parts) or relative.parts[0] in {'vendor', 'outputs', 'data', 'app', 'scripts', 'tests'}:
        raise ValueError('Choose a manga input folder, such as manga/MyManga.')
    if not path.is_dir():
        raise ValueError('That folder does not exist.')
    return path


def natural_key(path):
    return [int(x) if x.isdigit() else x.casefold() for x in re.split(r'(\d+)', path.name)]


def pages(chapter):
    return sorted((p for p in chapter.iterdir() if not p.is_symlink() and p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS), key=natural_key)


def chapters(manga):
    from app.chapter_names import chapter_sort_key
    folder = local_path(manga['folder'])
    return sorted((p for p in folder.iterdir() if not p.is_symlink() and p.is_dir() and not p.name.startswith('.') and pages(p)), key=lambda p: chapter_sort_key(p.name))


def page_output(manga, chapter, page):
    return OUTPUTS / manga['id'] / chapter / (page + '.json')


def job_path(manga, chapter):
    return OUTPUTS / manga['id'] / chapter / '_job.json'
