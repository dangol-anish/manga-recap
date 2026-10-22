"""Explicit synthetic validation for a fresh clone; never reads manga chapters."""
import json
import time

from PIL import Image, ImageDraw
from app import storage
from app.detector import Detector, MODEL_ID, REVISION, retain_panels
from app.validation import panel_errors


def validate(detector_factory=None):
    # Exercise the same filter used by normal inference with mixed labels.
    labels = '<text><panel><character>'
    positions = [labels.index(label) for label in ('<text>', '<panel>', '<character>')]
    filtered = retain_panels(labels, [[[1, 1, 2, 2]], [[0, 0, 100, 100]], [[3, 3, 4, 4]]],
                             [(p, p) for p in positions])
    if filtered != [[0., 0., 100., 100.]]:
        raise ValueError('Panel label filtering failed; detection remains unvalidated.')
    with Image.new('RGB', (700, 1000), 'white') as image:
        draw = ImageDraw.Draw(image)
        for box in ((30, 30, 330, 460), (370, 30, 670, 460), (30, 500, 670, 970)):
            draw.rectangle(box, outline='black', width=5)
            draw.ellipse((box[0]+40, box[1]+50, box[0]+120, box[1]+130), outline='black', width=4)
            draw.line((box[0]+80, box[1]+130, box[0]+80, box[1]+240), fill='black', width=4)
        started = time.perf_counter()
        detector = (detector_factory or Detector)()
        boxes = detector.detect(image, 'rtl')  # Normal detection; filter discards other labels.
        errors = panel_errors(boxes, *image.size)
        if errors:
            raise ValueError('Synthetic model validation failed: ' + '; '.join(errors))
    report = {'panel_only_validated': True, 'model': MODEL_ID, 'revision': REVISION,
              'detection_mode': 'normal-panel-label-filter', 'kind': 'synthetic validation',
              'panels_found': len(boxes), 'device': detector.device,
              'seconds': round(time.perf_counter()-started, 3)}
    # Keep any old provenance fields; only a successful check changes readiness.
    previous = storage.read_json(storage.DATA/'model-validation.json', {})
    storage.write_json(storage.DATA/'model-validation.json', {**previous, **report})
    return report


if __name__ == '__main__':
    print(json.dumps(validate(), indent=2))
