"""Rebuilt narration stage, using only current Stage 4 prepared assets."""
import json
import os
import sys
import tempfile
import threading
import shutil
from io import BytesIO
from pathlib import Path
from PIL import Image
from fastapi import APIRouter
from fastapi.responses import FileResponse, Response, JSONResponse
from pydantic import BaseModel, Field
from app import errors, storage, panel_preparation as panels, detection, narration, memory
from app.read_cache import memoized_read

router=APIRouter(prefix='/api/story')
lock=threading.RLock()
_digest=narration._digest
merge_cast=narration.merge_cast


@memoized_read
def resolve(mid,chapter):
    for manga in detection.catalog()['manga']:
        if manga['id']==mid:
            for item in manga['chapters']:
                if item['name']==chapter:
                    return panels.context(item['id'])
    raise ValueError('Chapter not found in this repository.')


def state_path(mid):
    return storage.DATA/'manga'/mid/'story-stage5.json'


@memoized_read
def load_state(mid):
    saved=storage.read_json(state_path(mid))
    if saved is not None:return saved
    # A standalone imported cast is a deliberate seed. Do not leak a legacy
    # aggregate cast from later chapters into the beginning of this workflow.
    cast=storage.read_json(storage.DATA/'manga'/mid/'cast.json',[])
    if isinstance(cast,dict):cast=cast.get('cast',[])
    if not isinstance(cast,list):raise ValueError('Saved cast must be an array.')
    return {'schema_version':6,'cast':cast,'seed_cast':cast,'chapters':{},'legacy_cast':[],'legacy_cast_after':None}


def root(mid,chapter):
    info,_,_=resolve(mid,chapter)
    return detection.output(info).parent/'narration'


def pending_path(mid,bid,chapter):
    if not bid.startswith('batch-') or not bid[6:].isdigit():raise ValueError('Invalid batch ID.')
    return root(mid,chapter)/'pending'/(bid+'.json')


@memoized_read
def batches(mid,chapter):
    info,files,report=resolve(mid,chapter);ids=panels.page_ids(files);records=[]
    if not files:raise ValueError('No chapter pages found.')
    for page in files:
        record=panels.safe_record(panels.record_path(info,ids[page.name]))
        if not record or not panels.assets_ready(info,record):raise ValueError(f'{page.name}: finish Stage 4 preparation first.')
        checksum,fingerprint,_,_=panels.binding(info,page,report)
        if record['source_sha256']!=checksum or record['detection_fingerprint']!=fingerprint:
            raise FileExistsError(f'{page.name}: prepared panels are stale. Update Stage 4 first.')
        records.append(record)
    result=[]
    for start in range(0,len(records),20):
        group=records[start:start+20]
        page_records=[{'page':r['page'],'page_id':r['page_id'],'revision':r['revision']} for r in group]
        allowed=[p['id'] for r in group for p in sorted(r['panels'],key=lambda p:p['order'])]
        if len(allowed)!=len(set(allowed)):raise ValueError('Duplicate panel IDs in prepared pages.')
        result.append({'id':f'batch-{start//20+1:03d}','pages':[r['page'] for r in group],
                       'page_records':page_records,'panel_ids':allowed,'fingerprint':_digest([page_records,allowed])})
    return result


def find_batch(mid,chapter,bid,fingerprint=None):
    batch=next((b for b in batches(mid,chapter) if b['id']==bid),None)
    if not batch:raise ValueError('Batch not found.')
    if fingerprint is not None and fingerprint!=batch['fingerprint']:
        raise FileExistsError('Prepared panels changed. Export this batch and copy its prompt again.')
    return batch


@memoized_read
def story_context(mid,chapter,bid):
    return memory.packet(mid,chapter,bid,backend=sys.modules[__name__])


def receipt(mid,chapter,batch,previous):
    return {'manga_id':mid,'chapter':chapter,'batch_id':batch['id'],'fingerprint':batch['fingerprint'],'context_token':previous['token']}


def prompt_text(mid,chapter,bid,final_recap=False):
    batch=find_batch(mid,chapter,bid);previous=story_context(mid,chapter,bid)
    master=(storage.ROOT/'prompts/narration_master.md').read_text(encoding='utf-8')
    return '\n\n'.join([master,'MANGA: '+resolve(mid,chapter)[0]['manga']+' / CHAPTER: '+chapter,
        'CURRENT_BATCH: '+bid,'FINAL_RECAP: '+str(bool(final_recap)).lower(),
        'CURRENT_PAGES (upload all these labeled images):\n'+json.dumps([r['page_id']+'.jpg' for r in batch['page_records']]),
        'VALID_PANEL_IDS (exact IDs, in reading order):\n'+json.dumps(batch['panel_ids']),
        'Previous narration ending, saved cast and bounded story context:\n'+json.dumps(previous,ensure_ascii=False,indent=2),
        'BATCH_RECEIPT (copy exactly into batch_receipt):\n'+json.dumps(receipt(mid,chapter,batch,previous))])+'\n'


def jpeg_photo(info,record):
    source=panels.record_path(info,record['page_id']).parent/record['revision']/'labeled.png'
    if source.is_symlink() or not source.resolve().is_relative_to(panels.root(info).resolve()):raise ValueError('Unsafe labeled asset.')
    # Full resolution and no chroma subsampling keep small text and red labels clear.
    # Explicit RGB encoding strips source metadata and never changes prepared assets.
    with Image.open(source) as original, original.convert('RGB') as image:
        image.info.clear()
        buffer=BytesIO()
        image.save(buffer,format='JPEG',quality=95,subsampling=0,progressive=False)
        return buffer.getvalue()


def export_photos(mid,chapter,bid,fingerprint):
    batch=find_batch(mid,chapter,bid,fingerprint);info,_,_=resolve(mid,chapter)
    target=root(mid,chapter)/'exports'/(bid+'-'+fingerprint+'-jpeg-v1')
    target.parent.mkdir(parents=True,exist_ok=True)
    if target.is_symlink():raise ValueError('Unsafe export folder.')
    expected=[r['page_id']+'.jpg' for r in batch['page_records']]
    staging=Path(tempfile.mkdtemp(dir=target.parent,prefix='.photos-'))
    try:
        for record in batch['page_records']:
            (staging/(record['page_id']+'.jpg')).write_bytes(jpeg_photo(info,record))
        find_batch(mid,chapter,bid,fingerprint)
        if target.is_dir() and sorted(p.name for p in target.iterdir())==sorted(expected) and all(not p.is_symlink() for p in target.iterdir()):
            if all(panels.source_hash(target/name)==panels.source_hash(staging/name) for name in expected):return target
            raise ValueError('Export photos were modified. Keep those files elsewhere and remove this export folder before exporting again; prepared pages are unchanged.')
        if target.exists():raise ValueError('Export folder was modified. Keep its original photos intact.')
        os.replace(staging,target)
    finally:
        if staging.exists():shutil.rmtree(staging)
    return target


def status(mid,chapter):
    current=batches(mid,chapter);state=load_state(mid);saved=state['chapters'].get(chapter,{}).get('batches',{})
    for batch in current:
        entry=saved.get(batch['id'])
        try:
            previous=story_context(mid,chapter,batch['id'])
            batch.update(ready=True,context_token=previous['token'],previous_context=previous,blocked_reason='')
        except FileExistsError as exc:batch.update(ready=False,context_token=None,previous_context=None,blocked_reason=str(exc))
        imported=bool(entry and entry.get('fingerprint')==batch['fingerprint'] and batch['ready'] and entry.get('context_token')==batch['context_token'])
        draft=storage.read_json(pending_path(mid,batch['id'],chapter))
        pending=None
        if draft and draft.get('fingerprint')==batch['fingerprint'] and draft.get('context_token')==batch['context_token']:
            pending=dict(draft['validation'])
            pending['redo_message']=narration.redo_message(pending,draft['payload'],batch['panel_ids'])
        batch.update(imported=imported,stale=bool(entry and not imported),final_recap=entry.get('final_recap',False) if entry else False,
            beat_count=len(entry['beats']) if imported else 0,
            selected_count=sum(len(b['panel_ids']) for b in entry['beats']) if imported else 0,
            pending=pending)
    info,_,_=resolve(mid,chapter)
    return {'manga':info['manga'],'chapter':chapter,'pages':sum(len(b['pages']) for b in current),'panels':sum(len(b['panel_ids']) for b in current),
            'imported_beats':sum(b['beat_count'] for b in current),'batches':current,'revision':_digest(state)}


class Reply(BaseModel):
    fingerprint:str=Field(min_length=64,max_length=64)
    context_token:str=Field(min_length=64,max_length=64)
    revision:str=Field(min_length=64,max_length=64)
    reply:str=Field(min_length=1,max_length=1000000)
    corrections_only:bool=False
    final_recap:bool=False


error = errors.request_error


@router.get('/catalog')
def catalog_api():
    groups=panels.catalog()
    for group in groups:
        for chapter in group['chapters']:
            chapter['ready']=bool(chapter['ready'] and chapter['prepared_pages']==chapter['images'] and chapter['images'])
    return groups




class MemoryReview(BaseModel):
    revision: str = Field(min_length=64,max_length=64)
    id: str = Field(min_length=64,max_length=64)
    pinned: bool
    verified: bool
    resolved: bool


@router.get('/{mid}/memory')
def memory_review_status(mid:str):
    from app import story_memory
    try:
        with lock,panels.lock:
            if not any(m['id']==mid for m in detection.catalog()['manga']):raise ValueError('Manga not found. Choose it again in Narration.')
            return JSONResponse(story_memory.overview(mid),headers={'Cache-Control':'no-store'})
    except (ValueError,OSError) as exc:raise error(exc)


@router.post('/{mid}/memory')
def memory_review_save(mid:str,body:MemoryReview):
    from app import story_memory
    from app.tasks import lock as task_lock,ensure_idle
    try:
        with task_lock,lock,panels.lock:
            ensure_idle()
            if not any(m['id']==mid for m in detection.catalog()['manga']):raise ValueError('Manga not found. Choose it again in Narration.')
            return story_memory.update(mid,body.revision,body.id,body.pinned,body.verified,body.resolved)
    except (ValueError,OSError) as exc:raise error(exc)


@router.get('/{mid}/{chapter}')
def status_api(mid:str,chapter:str):
    try:
        with lock,panels.lock:return status(mid,chapter)
    except (ValueError,OSError) as exc:raise error(exc)


def preview(mid, chapter, bid):
    """Read current imported text and visual spans without changing saved work."""
    from app.alignment import validate_map
    current = status(mid, chapter)
    batch = next((b for b in current['batches'] if b['id'] == bid), None)
    if not batch or not batch['imported']:
        raise FileExistsError('Import the current reply for this batch before reviewing its story. Outdated replies cannot be previewed.')
    entry = load_state(mid)['chapters'][chapter]['batches'][bid]
    info, _, _ = resolve(mid, chapter)
    token = _digest([current['revision'], batch['fingerprint'], batch['context_token']])
    page_records = {r['page_id']: r for r in batch['page_records']}
    offset = sum(b['beat_count'] for b in current['batches'] if b['id'] < bid and b['imported'])
    beats = []
    for index, saved in enumerate(entry['beats']):
        text = saved['narration']
        anchors = []
        if text.strip():
            issues, anchors = validate_map({**saved, 'number': index+1},
                                          {'beat': index+1, 'anchors': saved.get('anchors', [])})
            if issues:
                raise ValueError(f'Beat {offset+index+1}: saved phrase anchors need checking. ' + ' '.join(issues))
        selected = []
        for position, pid in enumerate(saved['panel_ids']):
            page_id = pid.rsplit('-', 1)[0]
            record = page_records.get(page_id)
            if not record:
                raise ValueError('A selected panel is missing from the current batch: ' + pid)
            start = 0 if position == 0 else anchors[position]['char_start'] if anchors else 0
            end = anchors[position+1]['char_start'] if anchors and position+1 < len(anchors) else len(text)
            selected.append({'id': pid, 'page': record['page'], 'page_id': page_id,
                             'revision': record['revision'], 'narration': text[start:end],
                             'phrase': anchors[position]['phrase'] if anchors else ''})
        beats.append({'number': offset+index+1, 'narration': text, 'unsure': saved.get('unsure', ''),
                      'silent': not text.strip(), 'panels': selected})
    return {'batch_id': bid, 'chapter': chapter, 'token': token, 'beats': beats,
            'selected_panels': sum(len(b['panels']) for b in beats),
            'skipped_panels': len(batch['panel_ids'])-sum(len(b['panels']) for b in beats)}


@router.get('/{mid}/{chapter}/{bid}/preview')
def preview_api(mid: str, chapter: str, bid: str):
    try:
        with lock, panels.lock:
            return JSONResponse(preview(mid, chapter, bid), headers={'Cache-Control': 'no-store'})
    except (ValueError, OSError) as exc:
        raise error(exc)


@router.get('/{mid}/{chapter}/{bid}/preview/{pid}')
def preview_panel_api(mid: str, chapter: str, bid: str, pid: str, token: str):
    try:
        with lock, panels.lock:
            current = preview(mid, chapter, bid)
            if token != current['token']:
                raise FileExistsError('Narration or panels changed. Refresh the story preview before checking these images.')
            selected = next((p for b in current['beats'] for p in b['panels'] if p['id'] == pid), None)
            if not selected:
                raise ValueError('This panel is not selected in the saved narration batch.')
            info, _, _ = resolve(mid, chapter)
            target = panels.record_path(info, selected['page_id']).parent / selected['revision'] / 'crops' / (pid+'.png')
            if target.is_symlink() or not target.is_file() or not target.resolve().is_relative_to(panels.root(info).resolve()):
                raise ValueError('The saved panel crop is missing or unsafe. Check Stage 4 preparation.')
            return FileResponse(target, media_type='image/png', headers={'Cache-Control': 'no-store'})
    except (ValueError, OSError) as exc:
        raise error(exc)


@router.get('/{mid}/{chapter}/{bid}/prompt')
def prompt_api(mid:str,chapter:str,bid:str,final_recap:bool=False):
    try:
        with lock,panels.lock:text=prompt_text(mid,chapter,bid,final_recap)
        return Response(text,media_type='text/plain',headers={'Cache-Control':'no-store'})
    except (ValueError,OSError) as exc:raise error(exc)


class ExportRequest(BaseModel):
    fingerprint:str=Field(min_length=64,max_length=64)


@router.post('/{mid}/{chapter}/{bid}/export')
def export_api(mid:str,chapter:str,bid:str,body:ExportRequest):
    from app.tasks import lock as task_lock,ensure_idle
    from app.runtime import open_system_folder
    try:
        with task_lock,lock,panels.lock:
            ensure_idle();path=export_photos(mid,chapter,bid,body.fingerprint)
        open_system_folder(path)
        return {'folder':path.relative_to(storage.ROOT).as_posix(),'photos':len(list(path.glob('*.jpg')))}
    except (ValueError,OSError) as exc:raise error(exc)


@router.get('/{mid}/{chapter}/{bid}/photos/{pid}')
def photo_api(mid:str,chapter:str,bid:str,pid:str,fingerprint:str):
    try:
        with lock,panels.lock:
            batch=find_batch(mid,chapter,bid,fingerprint);info,_,_=resolve(mid,chapter)
            record=next((r for r in batch['page_records'] if r['page_id']==pid),None)
            if not record:raise ValueError('Photo is not in this batch.')
            return Response(jpeg_photo(info,record),media_type='image/jpeg',headers={'Cache-Control':'no-store','Content-Disposition':f'attachment; filename="{pid}.jpg"'})
    except (ValueError,OSError) as exc:raise error(exc)


@router.post('/{mid}/{chapter}/{bid}/import')
def import_api(mid:str,chapter:str,bid:str,body:Reply):
    from app.tasks import lock as task_lock,ensure_idle
    try:
        with task_lock,lock,panels.lock:
            ensure_idle();resolve(mid,chapter)
            if body.revision!=_digest(load_state(mid)):raise FileExistsError('Saved narration changed in another tab. Reload the chapter before importing.')
            result=narration.import_reply(mid,chapter,bid,body.fingerprint,body.reply,body.corrections_only,body.final_recap,body.context_token,True,backend=sys.modules[__name__])
            return JSONResponse(result,status_code=200 if result['ok'] else 422)
    except (ValueError,OSError) as exc:raise error(exc)
