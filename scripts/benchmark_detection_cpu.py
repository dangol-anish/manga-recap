"""Explicit isolated CPU comparison; never opens or changes chapter work."""
import json
import statistics
import time
from pathlib import Path

from PIL import Image, ImageDraw

from app.detector import Detector, REVISION
from app.storage import ROOT


def main():
    import torch
    image = Image.new('RGB', (700, 1000), 'white')
    draw = ImageDraw.Draw(image)
    for rect in [(30, 30, 330, 460), (370, 30, 670, 460), (30, 500, 670, 970)]:
        draw.rectangle(rect, outline='black', width=5)
        draw.ellipse((rect[0]+40, rect[1]+50, rect[0]+120, rect[1]+130), outline='black', width=4)
        draw.line((rect[0]+80, rect[1]+130, rect[0]+80, rect[1]+240), fill='black', width=4)
    detector = Detector()
    folder = ROOT / 'outputs' / 'cpu-benchmarks' / str(time.time_ns())
    folder.mkdir(parents=True)
    report = {'kind': 'synthetic CPU comparison, not a chapter benchmark', 'revision': REVISION,
              'device': detector.device, 'runs': [], 'report': str(folder / 'report.json')}
    baseline = None
    # Forward/reverse passes help reveal warm-up and temperature effects.
    for threads in (2, 4, 8, 8, 4, 2):
        torch.set_num_threads(threads)
        started = time.perf_counter()
        boxes = detector.detect(image, 'rtl')
        seconds = time.perf_counter() - started
        if baseline is None:
            baseline = boxes
        row = {'threads': threads, 'seconds': round(seconds, 3), 'panels': boxes,
               'identical_to_baseline': boxes == baseline}
        report['runs'].append(row)
        (folder / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps(row), flush=True)
    report['median_seconds'] = {str(n): round(statistics.median(r['seconds'] for r in report['runs'] if r['threads'] == n), 3) for n in (2, 4, 8)}
    (folder / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
