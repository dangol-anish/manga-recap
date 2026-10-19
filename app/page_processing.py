from app.detector import MODEL_ID, REVISION, PROMPT
from app.validation import panel_errors


def page_record(page, image, boxes, detector, seconds, direction):
    errors = panel_errors(boxes, image.width, image.height)
    if errors:
        raise ValueError('; '.join(errors))
    return {
        'schema_version': 1, 'page': page.name,
        'width': image.width, 'height': image.height,
        'reading_direction': direction,
        'model': MODEL_ID, 'model_revision': REVISION,
        'detection_mode': 'normal-panel-label-filter', 'prompt': PROMPT,
        'device': detector.device, 'seconds': round(seconds, 3),
        'panels': [{'order': i + 1, 'box': box} for i, box in enumerate(boxes)],
    }
