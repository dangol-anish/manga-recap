from PIL import ImageDraw, ImageFont
import math

def draw_preview(image, boxes, target):
    preview = image.copy()
    draw = ImageDraw.Draw(preview)
    size = max(24, image.width // 35)
    font = ImageFont.load_default(size=size)
    stroke = max(3, image.width // 220)
    for index, (x1, y1, x2, y2) in enumerate(boxes, 1):
        # Invalid geometry is flagged by the caller; do not invent a box.
        if not all(math.isfinite(v) for v in (x1,y1,x2,y2)) or x2 <= x1 or y2 <= y1:
            continue
        # Draw only retained panel boxes; never draw other model detections.
        draw.rectangle((x1, y1, x2, y2), outline='red', width=stroke)
        x, y = max(0, x1 + stroke), max(0, y1 + stroke)
        text = str(index)
        bounds = draw.textbbox((x, y), text, font=font)
        draw.rectangle((bounds[0]-3, bounds[1]-3, bounds[2]+3, bounds[3]+3), fill='red')
        draw.text((x, y), text, fill='white', font=font)
    target.parent.mkdir(parents=True, exist_ok=True)
    preview.save(target)

