"""Authorized fixed-scope review: Chapter 12, pages 001–006. Does not enable processing."""
from pathlib import Path
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image, ImageDraw, ImageFont
from app.detector import Detector, MODEL_ID, REVISION
from app.storage import ROOT, DATA, read_json, write_json, page_output
from app.validation import panel_errors
from app.page_processing import page_record

FOLDER = 'manga/Mushoku Tensei - Jobless Reincarnation'
CHAPTER = 'Chapter 12'
PAGE_NAMES = tuple(f'{n:03}.png' for n in range(1, 7))
REPORT = ROOT / 'reports' / 'chapter12-retest.json'


from app.previews import draw_preview


def run():
    manga = next(m for m in read_json(DATA / 'library.json', []) if m['folder'] == FOLDER)
    # An approved benchmark does not authorize the app to run any chapter.
    write_json(DATA / 'model-validation.json', {
        'panel_only_validated': False,
        'reason': 'Processing disabled pending approval of the Chapter 12 pages 001–006 previews.',
    })
    report = {'model': MODEL_ID, 'revision': REVISION, 'chapter': CHAPTER,
              'reading_direction': manga['direction'], 'scope': list(PAGE_NAMES),
              'status': 'running', 'pages': [], 'flagged': []}
    write_json(REPORT, report)
    started = time.perf_counter()
    detector = Detector()
    report.update(device=detector.device, model_load_seconds=round(detector.load_seconds, 3))
    for name in PAGE_NAMES:
        page = ROOT / FOLDER / CHAPTER / name
        destination = page_output(manga, CHAPTER, name)
        preview = destination.parent / 'previews' / name
        page_started = time.perf_counter()
        try:
            with Image.open(page) as original:
                image = original.convert('L').convert('RGB')
            boxes = detector.detect(image, manga['direction'])
            elapsed = round(time.perf_counter() - page_started, 3)
            reasons = panel_errors(boxes, image.width, image.height)
            draw_preview(image, boxes, preview)
            result = {'page': name, 'panels': len(boxes), 'seconds': elapsed,
                      'preview': str(preview.relative_to(ROOT)),
                      'status': 'flagged' if reasons else 'valid'}
            if reasons:
                destination.unlink(missing_ok=True)
                result['reasons'] = reasons
                report['flagged'].append(result.copy())
            else:
                write_json(destination, page_record(page, image, boxes, detector, elapsed, manga['direction']))
                result['json'] = str(destination.relative_to(ROOT))
            report['pages'].append(result)
            print(f'{CHAPTER}/{name}: {result["status"]}, panels={len(boxes)}, time={elapsed:.3f}s', flush=True)
        except Exception as exc:
            result = {'page': name, 'status': 'flagged', 'seconds': round(time.perf_counter()-page_started, 3), 'reasons': [f'{type(exc).__name__}: {exc}']}
            destination.unlink(missing_ok=True)
            report['pages'].append(result)
            report['flagged'].append(result.copy())
            print(f'{CHAPTER}/{name}: FLAGGED: {result["reasons"]}', flush=True)
        write_json(REPORT, report)
    report.update(status='review_required', valid_pages=sum(p['status']=='valid' for p in report['pages']),
                  elapsed_seconds=round(time.perf_counter()-started, 3), processing_enabled=False)
    write_json(REPORT, report)
    print(f'Valid pages={report["valid_pages"]}/6, flagged={len(report["flagged"])}, total time={report["elapsed_seconds"]:.3f}s. Processing remains disabled.', flush=True)

if __name__ == '__main__':
    run()
