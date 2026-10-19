"""Stage 2: saved panels, crops, labeled pages, and manual fixes. No model imports."""
import math
import os
from pathlib import Path
import shutil
import tempfile
import uuid

from PIL import Image, ImageDraw, ImageFont
from app import storage

ALLOWED_CHAPTERS = {'Chapter 1', 'Chapter 12'}


def review_context(manga_id, chapter_name, page_name=None):
    policy = storage.read_json(storage.DATA / 'model-validation.json', {})
    if manga_id != policy.get('allowed_manga_id') or chapter_name not in ALLOWED_CHAPTERS:
        raise PermissionError('Stage 2 is restricted to the approved manga’s Chapter 1 and Chapter 12.')
    manga = next((m for m in storage.read_json(storage.DATA / 'library.json', []) if m['id'] == manga_id), None)
    if manga is None:
        raise FileNotFoundError('Manga not found.')
    folder = storage.local_path(manga['folder']) / chapter_name
    if folder.is_symlink() or not folder.is_dir():
        raise FileNotFoundError('Chapter folder not found.')
    chapter_pages = storage.pages(folder)
    if page_name is None:
        return manga, chapter_pages
    page = next((p for p in chapter_pages if p.name == page_name), None)
    if page is None:
        raise FileNotFoundError('Page not found.')
    return manga, page


def page_id(page):
    if not page.stem.isdigit():
        raise ValueError('Stage 2 requires numbered page filenames.')
    return f'p{int(page.stem):03}'


def chapter_root(manga, chapter):
    return storage.OUTPUTS / manga['id'] / chapter / 'stage2'


def record_path(manga, chapter, page):
    return chapter_root(manga, chapter) / page_id(page) / 'page.json'


def geometry_errors(boxes, width, height):
    errors = []
    for i, box in enumerate(boxes, 1):
        if not isinstance(box, (list, tuple)) or len(box) != 4 or not all(isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) for x in box):
            errors.append(f'Panel {i} must have four finite coordinates.')
            continue
        x1,y1,x2,y2 = box
        if not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height):
            errors.append(f'Panel {i} must have positive area and stay inside the page.')
    return errors


def flag_reasons(boxes, width, height, zero_panels=False):
    reasons = []
    if zero_panels or not boxes:
        reasons.append('0 panels: whole page used as one panel.')
    if width > height:
        reasons.append(f'Spread: width {width}px is greater than height {height}px.')
    for i, (x1,y1,x2,y2) in enumerate(boxes, 1):
        bw,bh = x2-x1,y2-y1
        area = bw*bh/(width*height)
        if len(boxes)>1 and area>=0.9:
            reasons.append(f'Panel {i} covers {area:.1%} of the page while other boxes exist (at least 90%).')
        if bw < width*0.1:
            reasons.append(f'Panel {i} width is {bw/width:.1%} of the page (under 10%).')
        if bh < height*0.1:
            reasons.append(f'Panel {i} height is {bh/height:.1%} of the page (under 10%).')
        if area < 0.03:
            reasons.append(f'Panel {i} area is {area:.1%} of the page (under 3%).')
    for i, a in enumerate(boxes):
        for j in range(i+1,len(boxes)):
            b=boxes[j]
            overlap_w=min(a[2],b[2])-max(a[0],b[0])
            overlap_h=min(a[3],b[3])-max(a[1],b[1])
            if overlap_w > width*0.05 and overlap_h > height*0.05:
                reasons.append(f'Panels {i+1} and {j+1} overlap: {overlap_w/width:.1%} of page width, {overlap_h/height:.1%} of page height (over 5% in both dimensions).')
    return reasons


def padded_crop_box(box, width, height):
    x1,y1,x2,y2=box
    px,py=(x2-x1)*0.01,(y2-y1)*0.01
    return [max(0,math.floor(x1-px)),max(0,math.floor(y1-py)),min(width,math.ceil(x2+px)),min(height,math.ceil(y2+py))]


def draw_ids(image, panels):
    labeled=image.copy()
    draw=ImageDraw.Draw(labeled)
    size=max(30,round(min(image.size)*0.045))
    font=ImageFont.load_default(size=size)
    stroke=max(3,round(image.width/250))
    for panel in panels:
        x1,y1,x2,y2=panel['box']
        draw.rectangle((x1,y1,x2,y2),outline='#e01827',width=stroke)
        label=panel['id']
        text_box=draw.textbbox((0,0),label,font=font)
        tw,th=text_box[2]-text_box[0]+16,text_box[3]-text_box[1]+12
        x=max(0,min(x1+stroke,image.width-tw))
        y=max(0,min(y1+stroke,image.height-th))
        draw.rectangle((x,y,x+tw,y+th),fill='#e01827')
        draw.text((x+8,y+6-text_box[1]),label,fill='white',font=font)
    return labeled


def generate_page(manga, chapter, page, boxes, *, edited=False, zero_panels=False):
    """Commit the manifest only after all new assets exist, then retire old assets."""
    with Image.open(page) as original:
        image=original.convert('RGB')
    width,height=image.size
    errors=geometry_errors(boxes,width,height)
    if errors:
        raise ValueError(' '.join(errors))
    fallback=zero_panels or not boxes
    if not boxes:
        boxes=[[0.,0.,float(width),float(height)]]
    reasons=flag_reasons(boxes,width,height,zero_panels=fallback)
    pid=page_id(page)
    panels=[{'id':f'{pid}-{i}','order':i,'box':list(map(float,box)),
             'crop_box':padded_crop_box(box,width,height)} for i,box in enumerate(boxes,1)]
    directory=record_path(manga,chapter,page).parent
    directory.mkdir(parents=True,exist_ok=True)
    revision=uuid.uuid4().hex
    staging=Path(tempfile.mkdtemp(prefix='.build-',dir=directory))
    version=directory/revision
    try:
        crops=staging/'crops';crops.mkdir()
        for panel in panels:
            image.crop(tuple(panel['crop_box'])).save(crops/(panel['id']+'.png'))
        labeled=draw_ids(image,panels)
        labeled.save(staging/'labeled.png')
        labeled.thumbnail((480,720))
        labeled.save(staging/'thumbnail.jpg',quality=85)
        os.replace(staging,version)
        record={'schema_version':2,'page':page.name,'page_id':pid,'width':width,'height':height,
                'reading_direction':manga['direction'],'revision':revision,'edited':edited,
                'zero_panels_fallback':fallback,'flagged':bool(reasons),'flag_reasons':reasons,'panels':panels}
        storage.write_json(directory/'page.json',record)
    except Exception:
        shutil.rmtree(staging,ignore_errors=True)
        shutil.rmtree(version,ignore_errors=True)
        raise
    # A committed page has exactly one active crop/labeled asset set.
    for old in directory.iterdir():
        if old.is_dir() and old.name != revision:
            shutil.rmtree(old)
    return record


def prepare_chapter(manga_id, chapter):
    manga,chapter_pages=review_context(manga_id,chapter)
    for page in chapter_pages:
        current=storage.read_json(record_path(manga,chapter,page))
        if current is not None:
            continue  # Preserve manual fixes across restarts/rebuilds.
        saved=storage.read_json(storage.page_output(manga,chapter,page.name))
        if saved is None:
            raise FileNotFoundError(f'{chapter}/{page.name}: saved panel JSON is missing. Detection was not rerun.')
        panels=sorted(saved['panels'],key=lambda p:p['order'])
        generate_page(manga,chapter,page,[p['box'] for p in panels])
    return chapter_report(manga_id,chapter)


def chapter_report(manga_id, chapter):
    manga,chapter_pages=review_context(manga_id,chapter)
    records=[]
    for page in chapter_pages:
        record=storage.read_json(record_path(manga,chapter,page))
        if record is None:
            raise FileNotFoundError('Stage 2 page assets have not been prepared.')
        records.append(record)
    report={'chapter':chapter,'reading_direction':manga['direction'],'pages':len(records),
            'panels':sum(len(r['panels']) for r in records),
            'flagged':[{'page':r['page'],'reasons':r['flag_reasons']} for r in records if r['flagged']]}
    storage.write_json(chapter_root(manga,chapter)/'_report.json',report)
    records.sort(key=lambda r:(not r['flagged'],storage.natural_key(Path(r['page']))))
    return {'manga':manga,'report':report,'pages':records}


def save_page(manga_id,chapter,page_name,revision,boxes):
    manga,page=review_context(manga_id,chapter,page_name)
    current=storage.read_json(record_path(manga,chapter,page))
    if current is None:
        raise FileNotFoundError('Review page not found.')
    if revision != current['revision']:
        raise FileExistsError('This page changed in another tab. Reload before saving your fixes.')
    record=generate_page(manga,chapter,page,boxes,edited=True)
    chapter_report(manga_id,chapter)
    return record


def asset_path(manga_id,chapter,page_name,kind,revision=None,panel_id=None):
    manga,page=review_context(manga_id,chapter,page_name)
    if kind=='source':
        return page
    record=storage.read_json(record_path(manga,chapter,page))
    if not record or (revision is not None and revision!=record['revision']):
        raise FileNotFoundError('Image version not found; reload the page.')
    folder=record_path(manga,chapter,page).parent/record['revision']
    if kind=='labeled':return folder/'labeled.png'
    if kind=='thumbnail':return folder/'thumbnail.jpg'
    if kind=='crop' and panel_id in {p['id'] for p in record['panels']}:
        return folder/'crops'/(panel_id+'.png')
    raise FileNotFoundError('Image not found.')
