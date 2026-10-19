"""Shared validity gate for saving, resuming, and counting completed pages."""
import math


def panel_errors(boxes, width, height):
    errors = []
    if not boxes:
        return ['No panels detected.']
    if width <= 0 or height <= 0:
        return ['Invalid page dimensions.']
    for index, box in enumerate(boxes, 1):
        if not isinstance(box, (list, tuple)) or len(box) != 4:
            errors.append(f'Panel {index}: expected four coordinates.')
            continue
        if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in box):
            errors.append(f'Panel {index}: non-finite or invalid coordinates.')
            continue
        x1, y1, x2, y2 = box
        if not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height):
            errors.append(f'Panel {index}: box is outside page bounds or has no positive area.')
        if (x2 - x1) * (y2 - y1) < width * height * 0.01:
            errors.append(f'Panel {index}: box covers less than 1% of the page area.')
    return errors


def saved_page_errors(saved, page):
    from PIL import Image
    if not isinstance(saved, dict):
        return ['Missing page JSON.']
    if saved.get('detection_mode') != 'normal-panel-label-filter':
        return ['Page was not produced by validated normal detection.']
    try:
        with Image.open(page) as image:
            width, height = image.size
        if (saved['width'], saved['height']) != (width, height):
            return ['Saved dimensions do not match the page.']
        return panel_errors([p['box'] for p in saved['panels']], width, height)
    except (KeyError, TypeError, OSError) as exc:
        return [f'Invalid page JSON: {exc}']
