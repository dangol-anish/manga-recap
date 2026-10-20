"""Sequential Stage 3 worker, with atomic page commits and per-page checkpoints."""
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import tempfile
import time

from PIL import Image
from app import storage, detection, renumbering
from app.detector import Detector, MODEL_ID, REVISION, PROMPT
from app.page_processing import page_record
from app.validation import panel_errors, saved_page_errors
from app.previews import draw_preview
from app.worker import keep_awake

MODE = 'normal-panel-label-filter'


def source_hash(path):
    h = sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''): h.update(chunk)
    return h.hexdigest()


def signature(checksum, direction):
    return sha256(json.dumps([checksum, direction, MODEL_ID, REVISION, MODE, PROMPT]).encode()).hexdigest()


def read_safe(path, default=None):
    try: return default if path.is_symlink() else storage.read_json(path, default)
    except (OSError, ValueError): return default


def preview(image, boxes, target, stamp):
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(dir=target.parent, prefix='.', suffix='.jpg')
    os.close(descriptor)
    temporary = Path(name)
    try:
        safe_boxes = [b for b in boxes if isinstance(b, (list, tuple)) and len(b) == 4 and all(isinstance(v, (float,int)) and math.isfinite(v) for v in b)]
        draw_preview(image, safe_boxes, temporary)
        old_stamp = read_safe(target.with_suffix('.metadata.json'), {}).get('signature')
        if old_stamp and old_stamp != stamp and target.is_file() and not target.is_symlink():
            history = target.parent.parent / 'history/previews'
            history.mkdir(parents=True, exist_ok=True)
            os.replace(target, history / (sha256(target.name.encode()).hexdigest()[:16] + '.' + old_stamp + '.jpg'))
        os.replace(temporary, target)
        storage.write_json(target.with_suffix('.metadata.json'), {'signature': stamp})
    finally: temporary.unlink(missing_ok=True)


def cache_valid(record, page, checksum, direction, stamp):
    return (isinstance(record, dict) and record.get('signature') == stamp and record.get('source_sha256') == checksum and record.get('reading_direction') == direction and record.get('model') == MODEL_ID and record.get('model_revision') == REVISION and not saved_page_errors(record, page) and [p.get('order') for p in record['panels']] == list(range(1,len(record['panels'])+1)))


def archive(target, folder):
    if target.is_symlink(): raise ValueError('Unsafe page JSON destination.')
    if target.exists():
        history = folder / 'history'
        history.mkdir(parents=True, exist_ok=True)
        # Archive only this generated JSON, never original manga images.
        os.replace(target, history / (target.name + '.' + str(time.time_ns()) + '.json'))


def chapter_summary(chapter):
    results = chapter.get('results', [])
    return {'chapter': chapter['name'], 'pages': len(chapter['pages']), 'processed_pages': len(results), 'valid_pages': sum(r['status'] == 'valid' for r in results), 'flagged_pages': sum(r['status'] == 'flagged' for r in results), 'total_panels': sum(r['panels'] for r in results if r['status'] == 'valid'), 'average_seconds_per_page': round(sum(r['seconds'] for r in results)/len(results), 3) if results else 0, 'total_time_seconds': round(chapter.get('elapsed_seconds',0), 3), 'cached_pages': sum(r.get('cached', False) for r in results), 'reading_direction': chapter['direction']}


def checkpoint(job, chapter, folder):
    chapter['summary'] = chapter_summary(chapter)
    storage.write_json(folder / 'report.json', {'schema_version': 1, 'model': MODEL_ID, 'model_revision': REVISION, 'detection_mode': MODE, 'reading_direction': chapter['direction'], 'status': chapter['status'], **chapter['summary'], 'flagged': [r for r in chapter['results'] if r['status'] == 'flagged'], 'page_results': chapter['results']})
    detection.save(job)


def run(job, detector_factory=Detector):
    started = time.perf_counter()
    previous_elapsed = job.get('elapsed_seconds', 0)
    detector = None
    try:
        with keep_awake():
            for chapter in job['chapters']:
                if detection.stop_path(job['id']).exists(): break
                source = renumbering.folder_path(chapter['folder'])
                folder = detection.output(chapter)
                folder.mkdir(parents=True, exist_ok=True)
                old_report = read_safe(folder / 'report.json', {})
                old_results = {r['page']: r for r in old_report.get('page_results', [])}
                chapter_started = time.perf_counter()
                chapter_elapsed = chapter.get('elapsed_seconds', 0)
                chapter.update(status='running', results=[])
                checkpoint(job, chapter, folder)
                for page_index, name in enumerate(chapter['pages']):
                    if detection.stop_path(job['id']).exists(): break
                    if Path(name).name != name or '\\' in name: raise ValueError('Invalid page filename.')
                    page = source / name
                    if page.is_symlink(): raise ValueError('Symbolic-link pages cannot be processed.')
                    page_started = time.perf_counter()
                    job.update(current_page=f'{chapter["name"]} / {name}', message='Checking saved page…')
                    detection.save(job)
                    target = folder / 'pages' / (name + '.json')
                    picture = folder / 'previews' / (name + '.jpg')
                    checksum = source_hash(page)
                    stamp = signature(checksum, chapter['direction'])
                    saved = read_safe(target)
                    previous = old_results.get(name)
                    if cache_valid(saved, page, checksum, chapter['direction'], stamp):
                        if read_safe(picture.with_suffix('.metadata.json'), {}).get('signature') != stamp or not picture.exists():
                            with Image.open(page) as image: preview(image.convert('RGB'), [p['box'] for p in saved['panels']], picture, stamp)
                        result = {'page': name, 'index': page_index, 'status': 'valid', 'panels': len(saved['panels']), 'seconds': saved['seconds'], 'signature': stamp, 'cached': True, 'preview': True, 'reasons': []}
                    elif previous and previous.get('signature') == stamp and previous['status'] == 'flagged' and (not previous.get('preview') or (picture.exists() and read_safe(picture.with_suffix('.metadata.json'), {}).get('signature') == stamp)):
                        result = {**previous, 'index': page_index, 'cached': True}
                    else:
                        boxes, image, errors = [], None, []
                        try:
                            with Image.open(page) as original: image = original.convert('RGB')
                        except (OSError, ValueError, Image.DecompressionBombError) as exc: errors = [f'Cannot read page image: {exc}']
                        if not errors:
                            if detector is None:
                                job['message'] = 'Loading Magi v3. The first page may take longer.'; detection.save(job)
                                detector = detector_factory()
                                job.update(device=detector.device, model_load_seconds=round(detector.load_seconds,3))
                            page_started = time.perf_counter()  # model startup is reported separately
                            job['message'] = f'Detecting panels on {name}'; detection.save(job)
                            try:
                                boxes = detector.detect(image.convert('L').convert('RGB'), chapter['direction'])
                                errors = panel_errors(boxes, image.width, image.height)
                            except Exception as exc: errors = [f'{type(exc).__name__}: {exc}']
                        if source_hash(page) != checksum: raise ValueError('A source image changed during detection. Stop editing this chapter and resume.')
                        if image is not None: preview(image, boxes, picture, stamp)
                        seconds = round(time.perf_counter() - page_started, 3)
                        result = {'page': name, 'index': page_index, 'status': 'flagged' if errors else 'valid', 'panels': len(boxes), 'seconds': seconds, 'signature': stamp, 'cached': False, 'preview': image is not None, 'reasons': errors}
                        archive(target, folder)
                        if not errors:
                            record = page_record(page, image, boxes, detector, seconds, chapter['direction'])
                            record.update(source_sha256=checksum, signature=stamp, source_folder=chapter['folder'])
                            storage.write_json(target, record)
                        print(f'{chapter["name"]}/{name}: {result["status"]}, panels={len(boxes)}, time={seconds:.3f}s', flush=True)
                    if source_hash(page) != checksum:
                        archive(target, folder)
                        raise ValueError('A source image changed during detection. Stop editing this chapter and resume.')
                    chapter['results'].append(result)
                    chapter['elapsed_seconds'] = chapter_elapsed + time.perf_counter() - chapter_started
                    job['elapsed_seconds'] = round(previous_elapsed + time.perf_counter() - started,3)
                    job['message'] = f'{name}: {result["status"]}'
                    checkpoint(job, chapter, folder)
                chapter['status'] = 'complete' if len(chapter['results']) == len(chapter['pages']) else 'paused'
                chapter['elapsed_seconds'] = chapter_elapsed + time.perf_counter() - chapter_started
                checkpoint(job, chapter, folder)
                print(json.dumps(chapter['summary']), flush=True)
            completed = all(c['status'] == 'complete' for c in job['chapters'])
            job.update(status='complete' if completed else 'paused', current_page=None, message='Selected chapters finished. Review any flagged pages.' if completed else 'Stopped. Completed pages are safe; resume when ready.')
    except Exception as exc:
        job.update(status='failed', message=f'{type(exc).__name__}: {exc}')
        print(job['message'], flush=True)
    finally:
        job['elapsed_seconds'] = round(previous_elapsed + time.perf_counter() - started,3)
        detection.save(job)
    return 0 if job['status'] in ('complete','paused') else 1


def main(path):
    ident = path.stem
    if path.resolve() != detection.job_path(ident).resolve(): raise ValueError('Invalid worker job path.')
    worker_handle = detection.worker_lock()
    try:
        gate = path.with_suffix('.start')
        deadline = time.monotonic() + 15
        while not gate.exists():
            if time.monotonic() > deadline: raise RuntimeError('Detection worker start gate timed out.')
            time.sleep(.05)
        return run(detection.load(ident))
    finally: worker_handle.close()


if __name__ == '__main__':
    import sys
    raise SystemExit(main(Path(sys.argv[1])))
