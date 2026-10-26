"""Stage 4: saved detections -> crops, labeled pages and independent manual edits."""
from app import errors
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
import time
import uuid

from PIL import Image
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from app import storage, detection, renumbering, review
from app.detection_worker import source_hash, signature, cache_valid
from app.runtime import open_system_folder
from app.read_cache import memoized_read

router = APIRouter(prefix='/api/preparation')
lock = threading.RLock()
active = None
thread = None
stop_event = threading.Event()


def job_path(ident):
    if len(ident) != 32 or any(c not in '0123456789abcdef' for c in ident): raise ValueError('Invalid preparation job.')
    return storage.DATA / 'preparation/jobs' / (ident + '.json')


def load_job(ident):
    value = storage.read_json(job_path(ident))
    if not value: raise ValueError('Preparation job not found.')
    return value


@memoized_read
def context(ident, require_complete=True):
    for manga in detection.catalog()['manga']:
        for item in manga['chapters']:
            if item['id'] != ident: continue
            chapter = {**item, 'manga_id': manga['id'], 'manga': manga['name'], 'manga_folder': manga['folder']}
            folder = renumbering.folder_path(item['folder'])
            report = storage.read_json(detection.output(chapter) / 'report.json', {})
            chapter['direction'] = report.get('reading_direction', 'rtl')
            files = renumbering.images(folder)
            expected = {p.name for p in files}
            found = {r['page'] for r in report.get('page_results', [])}
            if require_complete and (item['pending'] or report.get('status') != 'complete' or found != expected):
                raise ValueError('Finish Stage 3 detection for the current chapter images before preparation.')
            return chapter, files, report
    raise ValueError('Chapter folder not found. Refresh the list.')


def root(chapter):
    path = detection.output(chapter).parent / 'panels'
    if path.is_symlink(): raise ValueError('Unsafe panel output folder.')
    return path


def page_ids(files):
    numeric = all(p.stem.isdigit() for p in files) and len({int(p.stem) for p in files}) == len(files)
    return {p.name: f'p{int(p.stem) if numeric else i:04d}' for i,p in enumerate(files)}


def record_path(chapter, pid):
    if len(pid) < 5 or pid[0] != 'p' or not pid[1:].isdigit(): raise ValueError('Invalid page ID.')
    path = root(chapter) / 'pages' / pid
    if path.is_symlink() or not path.resolve().is_relative_to(root(chapter).resolve()): raise ValueError('Unsafe page output folder.')
    return path / 'page.json'


def safe_record(path):
    if path.is_symlink(): raise ValueError('Unsafe page manifest.')
    return storage.read_json(path)


def binding(chapter, page, report):
    checksum = source_hash(page)
    stamp = signature(checksum, chapter['direction'])
    result = next((r for r in report.get('page_results', []) if r['page'] == page.name), None)
    if not result or result.get('signature') != stamp:
        raise ValueError(f'{page.name}: source or detection settings changed. Run Stage 3 again.')
    saved = storage.read_json(detection.output(chapter) / 'pages' / (page.name + '.json'))
    if result['status'] == 'valid':
        if not cache_valid(saved, page, checksum, chapter['direction'], stamp):
            raise ValueError(f'{page.name}: saved panel detection is invalid or stale. Run Stage 3 again.')
        boxes = [p['box'] for p in saved['panels']]
        reasons = []
    else:
        boxes = []
        reasons = list(result.get('reasons', []))
    fingerprint = sha256(json.dumps([stamp,boxes,reasons], sort_keys=True).encode()).hexdigest()
    return checksum, fingerprint, boxes, reasons


def assets_ready(chapter, record):
    try:
        folder = record_path(chapter, record['page_id']).parent / record['revision']
        required = [folder/'labeled.png', folder/'thumbnail.jpg'] + [folder/'crops'/(p['id']+'.png') for p in record['panels']]
        return all(p.resolve().is_relative_to(root(chapter).resolve()) and p.is_file() and not p.is_symlink() for p in required)
    except (KeyError, ValueError): return False


def generate(chapter, page, pid, boxes, checksum, detection_fingerprint, *, edited=False, zero_panels=False, detection_reasons=None):
    with Image.open(page) as original: image = original.convert('RGB')
    image.info.clear()  # Keep the same pixel coordinate space; do not carry EXIF rotation into assets.
    try:
        width,height = image.size
        errors = review.geometry_errors(boxes,width,height)
        if errors: raise ValueError(' '.join(errors))
        fallback = zero_panels or not boxes
        if not boxes: boxes = [[0.,0.,float(width),float(height)]]
        panels = [{'id':f'{pid}-{i}', 'order':i, 'box':list(map(float,b)), 'crop_box':review.padded_crop_box(b,width,height)} for i,b in enumerate(boxes,1)]
        reasons = review.flag_reasons(boxes,width,height,zero_panels=fallback)
        if fallback and not edited:
            reasons.extend('Stage 3: ' + reason for reason in (detection_reasons or []))
        directory = record_path(chapter,pid).parent
        directory.mkdir(parents=True,exist_ok=True)
        revision = uuid.uuid4().hex
        staging = Path(tempfile.mkdtemp(prefix='.build-',dir=directory))
        version = directory/revision
        try:
            (staging/'crops').mkdir()
            for panel in panels:
                crop = image.crop(panel['crop_box'])
                try: crop.save(staging/'crops'/(panel['id']+'.png'))
                finally: crop.close()
            labeled = review.draw_ids(image,panels)
            try:
                labeled.save(staging/'labeled.png')
                labeled.thumbnail((480,720))
                labeled.save(staging/'thumbnail.jpg',quality=85)
            finally: labeled.close()
            if source_hash(page) != checksum: raise ValueError('The source image changed while preparing this page. Run Stage 3 again.')
            os.replace(staging,version)
            record = {'schema_version':1,'page':page.name,'page_id':pid,'width':width,'height':height,'reading_direction':chapter['direction'],'revision':revision,'edited':edited,'source_sha256':checksum,'detection_fingerprint':detection_fingerprint,'zero_panels_fallback':fallback,'flagged':bool(reasons),'flag_reasons':reasons,'panels':panels}
            old = safe_record(directory/'page.json')
            if old:
                storage.write_json(directory/'history'/(old['revision']+'.json'),old)
            storage.write_json(directory/'page.json',record)
            return record
        except Exception:
            shutil.rmtree(staging,ignore_errors=True)
            shutil.rmtree(version,ignore_errors=True)
            raise
    finally: image.close()


def chapter_data(ident, persist=False):
    chapter,files,_ = context(ident, require_complete=False)
    ids = page_ids(files)
    records = [record for page in files if (record:=safe_record(record_path(chapter,ids[page.name]))) is not None]
    report = {'chapter':chapter['name'],'reading_direction':chapter['direction'],'pages':len(files),'prepared_pages':len(records),'panels':sum(len(r['panels']) for r in records),'flagged':[{'page':r['page'],'reasons':r['flag_reasons']} for r in records if r['flagged']]}
    if persist: storage.write_json(root(chapter)/'report.json',report)
    records.sort(key=lambda r:(not r['flagged'],storage.natural_key(Path(r['page']))))
    return {'chapter_id':ident,'manga':{'id':chapter['manga_id'],'name':chapter['manga'],'direction':chapter['direction']},'report':report,'pages':records}


def catalog():
    groups = detection.catalog()['manga']
    for manga in groups:
        for item in manga['chapters']:
            chapter = {**item,'manga_id':manga['id']}
            source = storage.read_json(detection.output(chapter)/'report.json',{})
            prepared = storage.read_json(root(chapter)/'report.json',{})
            expected = {p.name for p in renumbering.images(renumbering.folder_path(item['folder']))}
            item['ready'] = not item['pending'] and source.get('status') == 'complete' and {r['page'] for r in source.get('page_results',[])} == expected
            item['detected_pages'] = len(source.get('page_results',[]))
            item['prepared_pages'] = prepared.get('prepared_pages',0)
            item['panels'] = prepared.get('panels',0)
            item['flagged_pages'] = len(prepared.get('flagged',[]))
    return groups


def prepare_one(chapter,page,pid,report):
    checksum,fingerprint,boxes,reasons = binding(chapter,page,report)
    current = safe_record(record_path(chapter,pid))
    if current:
        same = current['source_sha256'] == checksum and current['detection_fingerprint'] == fingerprint and current['reading_direction'] == chapter['direction']
        if same and assets_ready(chapter,current): return current,True
        if current['edited'] and not same:
            raise ValueError(f'{page.name}: source/detection changed for a manually edited page. Its saved fixes were preserved.')
        if same and current['edited']:
            boxes = [p['box'] for p in current['panels']]
            return generate(chapter,page,pid,boxes,checksum,fingerprint,edited=True,zero_panels=current['zero_panels_fallback']),False
    return generate(chapter,page,pid,boxes,checksum,fingerprint,zero_panels=not boxes,detection_reasons=reasons),False


def save_job(job):
    job['updated_at']=time.time()
    storage.write_json(job_path(job['id']),job)


def summary(job):
    value=json.loads(json.dumps(job))
    results=[r for c in value['chapters'] for r in c.get('results',[])]
    value.update(total_pages=sum(c['total_pages'] for c in value['chapters']),completed_pages=len(results),prepared_pages=sum(r['status']=='prepared' for r in results),flagged_pages=sum(r.get('flagged',False) for r in results),panels=sum(r.get('panels',0) for r in results))
    if active and active['id']==job['id']:value['elapsed_seconds']=round(time.monotonic()-active['started'],1)
    return value


def run(job):
    global active
    try:
        for entry in job['chapters']:
            if stop_event.is_set():break
            entry['results']=[]
            chapter,files,report=context(entry['id'])
            ids=page_ids(files)
            entry['status']='running'
            for page in files:
                if stop_event.is_set():break
                with lock:
                    job['message']=f'{chapter["name"]} / {page.name}: preparing panels…';save_job(job)
                try:
                    record,cached=prepare_one(chapter,page,ids[page.name],report)
                    result={'page':page.name,'status':'prepared','panels':len(record['panels']),'flagged':record['flagged'],'cached':cached}
                except Exception as exc:
                    detail=errors.describe(exc,'preparation','Prepare panel crops and labeled page',str(page))
                    result={'page':page.name,'status':'error','error':detail['message'],'error_details':detail}
                with lock:entry['results'].append(result);save_job(job)
            chapter_data(entry['id'],persist=True)
            entry['status']='complete' if len(entry['results'])==len(files) and all(r['status']=='prepared' for r in entry['results']) else 'paused' if stop_event.is_set() else 'failed'
        with lock:
            job.update(status='paused' if stop_event.is_set() else 'complete' if all(c['status']=='complete' for c in job['chapters']) else 'failed',message='Stopped. Completed page assets are safe.' if stop_event.is_set() else 'Preparation finished. Review the labeled pages below.')
    except Exception as exc:
        with lock:
            job['status']='failed'; errors.failure(job,exc,'preparation','Prepare selected chapters',job.get('message'))
    finally:
        with lock:
            job['elapsed_seconds']=round(time.monotonic()-active['started'],2)
            try:errors.save_final(save_job,job,'preparation')
            finally:active=None


def start(job):
    global active,thread
    from app.tasks import lock as task_lock,ensure_idle
    with task_lock,lock:
        ensure_idle()
        for c in job['chapters']:
            chapter,files,_=context(c['id']);c.update(total_pages=len(files),results=[],status='pending')
        stop_event.clear();job.update(status='running',message='Preparing selected chapters…');save_job(job)
        active={'id':job['id'],'started':time.monotonic(),'job':job}
        thread=threading.Thread(target=run,args=(job,),daemon=True)
        try:thread.start()
        except Exception as exc:
            active=None; job['status']='failed'; errors.failure(job,exc,'preparation','Start panel preparation');errors.save_final(save_job,job,'preparation');raise
        return summary(job)


def recover():
    for path,job in errors.recovery_records(storage.DATA/'preparation/jobs','preparation'):
        if job['status'] in ('running','stopping'):
            job.update(status='paused',message='Interrupted by shutdown. Resume to continue.');save_job(job)


def shutdown():
    stop_event.set()
    if thread and thread.is_alive():thread.join(timeout=25)


def page_context(ident,pid):
    chapter,files,report=context(ident)
    ids=page_ids(files)
    page=next((p for p in files if ids[p.name]==pid),None)
    if page is None:raise ValueError('Page not found.')
    record=safe_record(record_path(chapter,pid))
    if record is None:raise ValueError('Prepare this chapter first.')
    checksum,fingerprint,_,_=binding(chapter,page,report)
    if record['source_sha256']!=checksum or record['detection_fingerprint']!=fingerprint:raise FileExistsError('The source or detections changed. Existing fixes were preserved; prepare/review the updated source first.')
    return chapter,page,record


class Selection(BaseModel):
    chapter_ids:list[str]=Field(min_length=1,max_length=2000)


class PageEdit(BaseModel):
    revision:str
    boxes:list[list[float]]=Field(max_length=500)


def api_error(exc):
    return HTTPException(409 if isinstance(exc,FileExistsError) else 400,str(exc))


@router.get('/catalog')
def catalog_api():
    try:return catalog()
    except (ValueError,OSError) as exc:raise api_error(exc)


@router.get('/jobs')
def jobs_api():
    with lock:return list(reversed(errors.job_list(storage.DATA/'preparation/jobs','preparation',summary)))


@router.post('/jobs')
def start_api(body:Selection):
    try:
        if len(set(body.chapter_ids))!=len(body.chapter_ids):raise ValueError('Select each chapter once.')
        chapters=[]
        for ident in body.chapter_ids:
            chapter,files,_=context(ident);chapters.append({'id':ident,'name':chapter['name'],'manga':chapter['manga'],'total_pages':len(files),'results':[],'status':'pending'})
        return start({'id':uuid.uuid4().hex,'status':'pending','chapters':chapters,'elapsed_seconds':0})
    except (FileExistsError,ValueError,OSError) as exc:raise api_error(exc)


@router.post('/jobs/{ident}/resume')
def resume_api(ident:str):
    try:return start(load_job(ident))
    except (FileExistsError,ValueError,OSError) as exc:raise api_error(exc)


@router.post('/jobs/{ident}/stop')
def stop_api(ident:str):
    with lock:
        try:
            job=load_job(ident)
            if active and active['id']==ident:
                stop_event.set();job=active['job'];job.update(status='stopping',message='Finishing the current page…');save_job(job)
            return summary(job)
        except (ValueError,OSError) as exc:raise api_error(exc)


@router.get('/chapters/{ident}')
def chapter_api(ident:str):
    try:return chapter_data(ident)
    except (ValueError,OSError) as exc:raise api_error(exc)


@router.get('/chapters/{ident}/pages/{pid}')
def page_api(ident:str,pid:str):
    try:return page_context(ident,pid)[2]
    except (ValueError,OSError,FileExistsError) as exc:raise api_error(exc)


@router.post('/chapters/{ident}/pages/{pid}')
def save_page_api(ident:str,pid:str,body:PageEdit):
    from app.tasks import lock as task_lock,ensure_idle
    try:
        with task_lock,lock:
            ensure_idle()
            chapter,page,current=page_context(ident,pid)
            if current['revision']!=body.revision:raise FileExistsError('This page changed in another tab. Reload before saving.')
            record=generate(chapter,page,pid,body.boxes,current['source_sha256'],current['detection_fingerprint'],edited=True)
            chapter_data(ident,persist=True)
            return record
    except (ValueError,OSError,FileExistsError) as exc:raise api_error(exc)


@router.get('/chapters/{ident}/pages/{pid}/image')
def image_api(ident:str,pid:str,kind:str='thumbnail',revision:str|None=None,panel_id:str|None=None):
    try:
        chapter,files,_=context(ident,require_complete=False)
        ids=page_ids(files)
        page=next((p for p in files if ids[p.name]==pid),None)
        if page is None:raise ValueError('Page not found.')
        record=safe_record(record_path(chapter,pid))
        if not record or (revision and revision!=record['revision']):raise ValueError('Image revision changed. Reload the page.')
        folder=record_path(chapter,pid).parent/record['revision']
        if kind=='source':
            # Browsers apply EXIF rotation to JPEGs; the detector uses raw pixels.
            # Serve a metadata-free copy so canvas boxes align with those pixels.
            from io import BytesIO
            with Image.open(page) as original: image=original.convert('RGB')
            try:
                image.info.clear();buffer=BytesIO();image.save(buffer,format='PNG')
                return Response(buffer.getvalue(),media_type='image/png',headers={'Cache-Control':'no-store'})
            finally:image.close()
        elif kind=='thumbnail':target=folder/'thumbnail.jpg'
        elif kind=='labeled':target=folder/'labeled.png'
        elif kind=='crop' and panel_id in {p['id'] for p in record['panels']}:target=folder/'crops'/(panel_id+'.png')
        else:raise ValueError('Unknown image.')
        if target.is_symlink() or not target.is_file():raise ValueError('Image not found.')
        if kind!='source' and not target.resolve().is_relative_to(root(chapter).resolve()):raise ValueError('Unsafe image path.')
        return FileResponse(target,headers={'Cache-Control':'no-store'})
    except (ValueError,OSError) as exc:raise HTTPException(404,str(exc))


@router.post('/chapters/{ident}/open-folder')
def open_folder_api(ident:str):
    import subprocess
    try:
        chapter,_,_=context(ident,require_complete=False)
        folder=root(chapter)
        if not folder.is_dir():raise ValueError('Prepare the chapter first.')
        open_system_folder(folder);return {'opened':True}
    except (ValueError,OSError,subprocess.SubprocessError) as exc:raise api_error(exc)
