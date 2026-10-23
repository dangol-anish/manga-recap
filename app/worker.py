import os
import subprocess
import sys
import time
from pathlib import Path
from PIL import Image
from app.storage import ROOT, read_json, write_json, pages, page_output
from app.validation import panel_errors, saved_page_errors
from app.page_processing import page_record
from app.previews import draw_preview
from app.authorization import require_authorized
from app.runtime import keep_awake


def run(job_file):
    gate = job_file.with_name('_start')
    deadline = time.monotonic() + 10
    while not gate.exists():
        if time.monotonic() > deadline:
            raise RuntimeError('Worker start gate timed out.')
        time.sleep(0.05)
    job = read_json(job_file)
    manga, chapter = job['manga'], job['chapter']
    require_authorized(manga['id'], chapter)
    started = time.perf_counter()
    processed = skipped = 0
    flagged, results = [], []
    detector = None
    chapter_pages = pages(ROOT / manga['folder'] / chapter)
    job.update(total_pages=len(chapter_pages), keep_awake=sys.platform in ('darwin','win32'), flagged=[], pages=[])
    write_json(job_file, job)
    try:
        with keep_awake():
            for page in chapter_pages:
                # Re-check the restriction before every page, including resume.
                require_authorized(manga['id'], chapter)
                destination = page_output(manga, chapter, page.name)
                preview = destination.parent / 'previews' / page.name
                job.update(status='running', current_page=page.name, message='Loading Magi v3' if detector is None else 'Detecting panels')
                write_json(job_file, job)
                if destination.exists():
                    saved = read_json(destination)
                    errors = saved_page_errors(saved, page)
                    if errors:
                        flagged.append({'page': page.name, 'reasons': errors})
                        with Image.open(page) as original:
                            draw_preview(original.convert('RGB'), [], preview)
                        results.append({'page': page.name, 'status': 'flagged', 'seconds': 0, 'panels': 0})
                    else:
                        if not preview.exists():
                            with Image.open(page) as original:
                                draw_preview(original.convert('RGB'), [p['box'] for p in saved['panels']], preview)
                        results.append({'page': page.name, 'status': 'valid', 'seconds': saved['seconds'], 'panels': len(saved['panels']), 'cached': True})
                        skipped += 1
                else:
                    if detector is None:
                        from app.detector import Detector
                        detector = Detector()
                        job.update(device=detector.device, model_load_seconds=detector.load_seconds)
                    page_started = time.perf_counter()
                    with Image.open(page) as original:
                        image = original.convert('L').convert('RGB')
                    boxes = detector.detect(image, manga['direction'])
                    elapsed = round(time.perf_counter()-page_started, 3)
                    errors = panel_errors(boxes, image.width, image.height)
                    draw_preview(image, boxes, preview)
                    result = {'page': page.name, 'status': 'flagged' if errors else 'valid', 'seconds': elapsed, 'panels': len(boxes), 'cached': False}
                    if errors:
                        flagged.append(dict(result, reasons=errors))
                    else:
                        write_json(destination, page_record(page, image, boxes, detector, elapsed, manga['direction']))
                    results.append(result)
                    processed += 1
                    print(f'{chapter}/{page.name}: {result["status"]}, panels={len(boxes)}, time={elapsed:.3f}s', flush=True)
                job.update(processed=processed, skipped=skipped, flagged=flagged, pages=results, panels_found=sum(p['panels'] for p in results if p['status']=='valid'), elapsed_seconds=round(time.perf_counter()-started,3))
                write_json(job_file, job)
            job.update(status='flagged' if flagged else 'complete', current_page=None, message='Chapter finished' if not flagged else f'Chapter finished; {len(flagged)} page(s) flagged')
    except Exception as exc:
        job.update(status='error', message=f'{type(exc).__name__}: {exc}')
        print(job['message'], flush=True)
    finally:
        elapsed = round(time.perf_counter()-started, 3)
        summary = {
            'chapter': chapter, 'pages': len(chapter_pages),
            'valid_pages': sum(p['status']=='valid' for p in results),
            'flagged_pages': len(flagged),
            'total_panels': sum(p['panels'] for p in results if p['status']=='valid'),
            'average_seconds_per_page': round(sum(p['seconds'] for p in results)/len(results),3) if results else None,
            'total_time_seconds': elapsed, 'cached_pages': skipped,
            'new_pages_processed': processed, 'reading_direction': manga['direction'],
        }
        job.update(summary=summary, processed=processed, skipped=skipped, flagged=flagged, pages=results, elapsed_seconds=elapsed, keep_awake=False)
        write_json(job_file, job)
        write_json(job_file.with_name('_report.json'), dict(summary, status=job['status'], flagged=flagged, page_results=results))
        print(f'{chapter}: pages={summary["pages"]}, valid pages={summary["valid_pages"]}, flagged pages={summary["flagged_pages"]}, total panels={summary["total_panels"]}, average seconds/page={summary["average_seconds_per_page"]}, total time={elapsed:.3f}s (cached pages={skipped})', flush=True)
    return 0 if job['status'] in {'complete','flagged'} else 1


if __name__ == '__main__':
    from app.runtime import acquire_server_lock
    job_file = Path(sys.argv[1])
    handle = acquire_server_lock(job_file.with_name('_worker.lock'))
    try:
        result = run(job_file)
    finally:
        handle.close()
    raise SystemExit(result)
