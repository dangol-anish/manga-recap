"""Synthetic inference check; not a replacement for a manga chapter benchmark."""
import json
from pathlib import Path
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image, ImageDraw
from app.detector import Detector, REVISION
from app.storage import ROOT, write_json

image = Image.new('RGB', (700, 1000), 'white')
draw = ImageDraw.Draw(image)
for rect in [(30,30,330,460),(370,30,670,460),(30,500,670,970)]:
    draw.rectangle(rect, outline='black', width=5)
    draw.ellipse((rect[0]+40,rect[1]+50,rect[0]+120,rect[1]+130), outline='black', width=4)
    draw.line((rect[0]+80,rect[1]+130,rect[0]+80,rect[1]+240), fill='black', width=4)
detector=Detector()
started=time.perf_counter()
boxes=detector.detect(image, 'rtl')
report={'kind':'synthetic smoke check, not manga benchmark','revision':REVISION,'device':detector.device,'load_seconds':round(detector.load_seconds,3),'page_seconds':round(time.perf_counter()-started,3),'panels':boxes}
write_json(ROOT/'reports/smoke-model.json',report)
print(json.dumps(report,indent=2),flush=True)
