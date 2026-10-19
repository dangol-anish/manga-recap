"""Stage 3: Chapter 1 export and validated narration import; no inference."""
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import tempfile
import zipfile
from app import storage, review

CHAPTER = 'Chapter 1'
CAST_FIELDS = ('name','gender','age_group','role','look')


def context(manga_id, chapter):
    if chapter != CHAPTER:
        raise PermissionError('Stage 3 is approved for Chapter 1 only.')
    return review.review_context(manga_id,chapter)


def root(manga_id):
    return storage.OUTPUTS / manga_id / CHAPTER / 'stage3'


def state_path(manga_id):
    # Narration and manga cast are committed together in one atomic JSON write.
    return storage.DATA / 'manga' / manga_id / 'narration.json'


def load_state(manga_id):
    state=storage.read_json(state_path(manga_id))
    if state is not None:
        return state
    cast=storage.read_json(storage.DATA/'manga'/manga_id/'cast.json',[])
    if isinstance(cast,dict):cast=cast.get('cast',[])
    if not isinstance(cast,list):
        raise ValueError('Saved manga cast must be a JSON list.')
    return {'schema_version':3,'cast':cast,'chapters':{}}


def batches(manga_id,chapter):
    manga,chapter_pages=context(manga_id,chapter)
    result=[]
    for start in range(0,len(chapter_pages),20):
        group=chapter_pages[start:start+20]
        page_records=[]
        ids=[]
        for page in group:
            record=storage.read_json(review.record_path(manga,chapter,page))
            if record is None:
                raise FileNotFoundError(f'{page.name}: Stage 2 labeled page is missing.')
            panels=sorted(record['panels'],key=lambda p:p['order'])
            ids.extend(p['id'] for p in panels)
            page_records.append({'page':page.name,'revision':record['revision']})
        if len(ids)!=len(set(ids)):
            raise ValueError('Stage 2 has duplicate panel IDs. Fix those before export.')
        fingerprint=hashlib.sha256(json.dumps(page_records,sort_keys=True).encode()+json.dumps(ids).encode()).hexdigest()
        result.append({'id':f'batch-{start//20+1:03}','pages':[p.name for p in group],
                       'page_records':page_records,'panel_ids':ids,'fingerprint':fingerprint})
    return result


def find_batch(manga_id,chapter,batch_id,fingerprint=None):
    batch=next((b for b in batches(manga_id,chapter) if b['id']==batch_id),None)
    if batch is None:raise FileNotFoundError('Batch not found.')
    if fingerprint is not None and fingerprint!=batch['fingerprint']:
        raise FileExistsError('Panel labels changed after export. Download this batch again and use its current IDs.')
    return batch


def _digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def story_context(manga_id,chapter,batch_id):
    current=batches(manga_id,chapter)
    state=load_state(manga_id)
    saved=state['chapters'].get(chapter,{}).get('batches',{})
    preceding=[]
    digest=_digest([])
    for batch in current:
        if batch['id']==batch_id:
            ending=[beat['narration'] for item in preceding for beat in item['beats']][-3:]
            return {'token':digest,'previous_narration_ending':ending,
                    'continuity':preceding[-1]['continuity'] if preceding else {'summary':'','open_threads':[]},
                    'preceding_batches':[b['id'] for b in preceding]}
        entry=saved.get(batch['id'])
        if not entry or entry.get('format')!='beats-v1' or entry['fingerprint']!=batch['fingerprint'] or entry.get('context_token')!=digest:
            raise FileExistsError(f'Import the current story-beat reply for {batch["id"]} first so this prompt can continue its story.')
        preceding.append(dict(entry,id=batch['id']))
        digest=_digest(preceding)
    raise FileNotFoundError('Batch not found.')


def prompt_text(manga_id,chapter,batch_id='batch-001',final_recap=False):
    batch=find_batch(manga_id,chapter,batch_id)
    previous=story_context(manga_id,chapter,batch_id)
    master=(storage.ROOT/'prompts/narration_master.md').read_text()
    cast=load_state(manga_id)['cast']
    sections=[master,'CURRENT_BATCH: '+batch_id,'FINAL_RECAP: '+str(bool(final_recap)).lower(),
              'VALID_PANEL_IDS (exact IDs, in reading order):\n'+json.dumps(batch['panel_ids']),
              'Previous narration ending and saved story context:\n'+json.dumps(previous,ensure_ascii=False,indent=2)]
    if cast:sections.append('Saved cast list:\n'+json.dumps({'cast':cast},ensure_ascii=False,indent=2))
    return '\n\n'.join(sections)+'\n'


def prompt_file(manga_id,chapter,batch_id='batch-001',final_recap=False):
    text=prompt_text(manga_id,chapter,batch_id,final_recap)
    path=root(manga_id)/'prompts'/batch_id/'narration_prompt.md'
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(dir=path.parent,prefix='.prompt-',suffix='.tmp')
    try:
        with os.fdopen(fd,'w') as stream:stream.write(text)
        os.replace(name,path)
        if batch_id=='batch-001' and not final_recap:
            legacy=root(manga_id)/'narration_prompt.md'
            legacy.write_text(text)
    finally:
        if os.path.exists(name):os.unlink(name)
    return path


def export_zip(manga_id,chapter,batch_id,fingerprint):
    batch=find_batch(manga_id,chapter,batch_id,fingerprint)
    target=root(manga_id)/'exports'/(batch_id+'-'+fingerprint+'.zip')
    if target.exists():return target
    target.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(dir=target.parent,prefix='.zip-',suffix='.tmp');os.close(fd)
    try:
        # PNGs are already compressed; storing them avoids CPU-heavy recompression.
        with zipfile.ZipFile(name,'w',compression=zipfile.ZIP_STORED) as archive:
            for record in batch['page_records']:
                page=record['page']
                path=review.asset_path(manga_id,chapter,page,'labeled',record['revision'])
                archive.write(path,arcname=review.page_id(Path(page))+'.png')
        os.replace(name,target)
    finally:
        if os.path.exists(name):os.unlink(name)
    return target


def pending_path(manga_id,batch_id):
    return root(manga_id)/'pending'/(batch_id+'.json')


def status(manga_id,chapter):
    current=batches(manga_id,chapter)
    state=load_state(manga_id)
    saved=state['chapters'].get(chapter,{'batches':{}})
    total_beats=selected=0
    for batch in current:
        imported=saved.get('batches',{}).get(batch['id'])
        try:
            previous=story_context(manga_id,chapter,batch['id'])
            batch.update(ready=True,context_token=previous['token'],previous_context=previous,blocked_reason='')
        except FileExistsError as exc:
            batch.update(ready=False,context_token=None,previous_context=None,blocked_reason=str(exc))
        batch['imported']=bool(imported and imported.get('format')=='beats-v1' and imported['fingerprint']==batch['fingerprint'] and batch['ready'] and imported.get('context_token')==batch['context_token'])
        batch['stale']=bool(imported and not batch['imported'])
        batch['beat_count']=len(imported.get('beats',[])) if batch['imported'] else 0
        batch['selected_count']=sum(len(b['panel_ids']) for b in imported['beats']) if batch['imported'] else 0
        batch['skipped_count']=len(batch['panel_ids'])-batch['selected_count'] if batch['imported'] else None
        batch['final_recap']=imported.get('final_recap',False) if imported else False
        total_beats+=batch['beat_count'];selected+=batch['selected_count']
        draft=storage.read_json(pending_path(manga_id,batch['id']))
        batch['pending']=draft.get('validation') if draft and draft.get('format')=='beats-v1' and draft['fingerprint']==batch['fingerprint'] and draft.get('context_token')==batch['context_token'] else None
    return {'chapter':chapter,'pages':sum(len(b['pages']) for b in current),
            'panels':sum(len(b['panel_ids']) for b in current),'cast_count':len(state['cast']),
            'imported_beats':total_beats,'selected_panels':selected,'batches':current}


def validate_payload(payload,expected):
    problems={};errors=[];bad=set();normalized=[]
    ranks={pid:i for i,pid in enumerate(expected)}
    def issue(pid,message,index):
        problems.setdefault(pid,[]).append(message);bad.add(index)
    if not isinstance(payload,dict):
        return {'ok':False,'issues':{},'errors':['The reply must be a JSON object.'],'fix_ids':[],'fix_beats':[]},[],[],None
    if 'panels' in payload:errors.append('Old panel-by-panel format: use beats with panel_ids, narration and unsure.')
    cast=payload.get('cast',[])
    if not isinstance(cast,list):errors.append('cast must be an array.')
    else:
        for i,item in enumerate(cast,1):
            if not isinstance(item,dict) or any(not isinstance(item.get(field,''),str) for field in CAST_FIELDS):
                errors.append(f'cast entry {i} must contain string fields: '+', '.join(CAST_FIELDS)+'.')
    continuity=payload.get('continuity')
    if not isinstance(continuity,dict) or not isinstance(continuity.get('summary'),str) or not isinstance(continuity.get('open_threads'),list) or not all(isinstance(t,str) for t in continuity.get('open_threads',[])):
        errors.append('continuity must contain a string summary and an array of string open_threads.')
        continuity=None
    else:continuity={k:continuity[k] for k in ('summary','open_threads')}
    beats=payload.get('beats')
    if not isinstance(beats,list) or not beats:
        errors.append('beats must be a non-empty array. Select useful panels; coverage of every panel is not required.')
        beats=[]
    locations={};flat=[]
    for i,beat in enumerate(beats,1):
        if not isinstance(beat,dict):
            errors.append(f'Beat {i} must be an object.');bad.add(i);normalized.append(None);continue
        ids=beat.get('panel_ids')
        if not isinstance(ids,list) or not ids or not all(isinstance(pid,str) for pid in ids):
            errors.append(f'Beat {i} needs a non-empty array of string panel_ids.');bad.add(i);normalized.append(None);continue
        for pid in ids:
            locations.setdefault(pid,[]).append(i)
            if pid not in ranks:issue(pid,f'Beat {i}: unknown ID; not in this batch manifest.',i)
            else:flat.append((pid,i))
        text=beat.get('narration')
        if not isinstance(text,str):
            errors.append(f'Beat {i}: narration must be a string.');bad.add(i)
        elif len(text.split())>40*len(ids):
            for pid in ids:issue(pid,f'Beat {i}: {len(text.split())} words exceeds the {40*len(ids)}-word budget (40 per selected panel).',i)
        if not isinstance(beat.get('unsure',''),str):
            errors.append(f'Beat {i}: unsure must be a string.');bad.add(i)
        normalized.append({'panel_ids':ids,'narration':text,'unsure':beat.get('unsure','')})
    for pid,indexes in locations.items():
        if len(indexes)>1:
            for i in set(indexes):issue(pid,'Repeated ID: each selected panel may be used only once across all beats.',i)
    for n,(pid,i) in enumerate(flat):
        for other,j in flat[n+1:]:
            if ranks[pid]>ranks[other]:
                issue(pid,f'Beat {i}: out of manifest reading order.',i)
                issue(other,f'Beat {j}: out of manifest reading order.',j)
    problems={pid:list(dict.fromkeys(messages)) for pid,messages in problems.items()}
    valid_cast=[{field:item.get(field,'') for field in CAST_FIELDS} for item in cast] if isinstance(cast,list) and not any(e.startswith('cast') for e in errors) else []
    return {'ok':not problems and not errors,'issues':problems,'errors':errors,
            'fix_ids':[pid for pid in expected if pid in problems],'fix_beats':sorted(bad)},normalized,valid_cast,continuity


def redo_message(validation,payload,expected):
    lines=['Correct only the affected story beats or metadata below. Do not rewrite unaffected beats. Return JSON only.']
    lines += [pid+': '+' '.join(reasons) for pid,reasons in validation['issues'].items()]
    lines += validation['errors']
    if validation['fix_beats']:
        lines.append('Replace these current draft beat numbers: '+', '.join(map(str,validation['fix_beats']))+'.')
        for index in validation['fix_beats']:
            lines.append(f'Current draft beat {index}: '+json.dumps(payload['beats'][index-1],ensure_ascii=False))
        lines.append('Each correction must include replace_beat with its current draft number, panel_ids, narration and unsure. To remove an unnecessary affected beat, return panel_ids: [] and narration: "" for that replace_beat.')
    else:lines.append('No existing beat needs replacement. Return beats: [] and fix only the reported cast/continuity/format issues. If there were no beats, provide a full reply instead.')
    lines += ['VALID_PANEL_IDS: '+json.dumps(expected),
              'Use only these exact IDs, once each in reading order. Panels may be skipped. Do not reuse IDs from unaffected beats. Ground every claim in the pages; omit or mark uncertainty instead of inventing details.',
              'Do not add a batch/chapter conclusion. The narration budget is 40 words per selected panel in each beat.',
              'Correction format: {"beats":[{"replace_beat":2,"panel_ids":["EXACT_ID_FROM_MANIFEST"],"narration":"...","unsure":""}],"continuity":{"summary":"...","open_threads":[]}}. Include cast only if it needs updating.']
    return '\n'.join(lines)


def merge_cast(existing,incoming):
    result=[dict(item) for item in existing]
    def key(item):
        name=item.get('name','').strip().casefold()
        return ('name',name) if name else ('unnamed',tuple(item.get(field,'') for field in CAST_FIELDS[1:]))
    for item in incoming:
        if not any(item.values()):continue
        match=next((old for old in result if key(old)==key(item)),None)
        if match is None:result.append(dict(item))
        else:match.update({k:v for k,v in item.items() if v})
    return result


def import_reply(manga_id,chapter,batch_id,fingerprint,reply,corrections_only=False,final_recap=False,context_token=None):
    batch=find_batch(manga_id,chapter,batch_id,fingerprint)
    previous=story_context(manga_id,chapter,batch_id)
    if context_token is not None and context_token!=previous['token']:
        raise FileExistsError('Earlier narration changed. Download a fresh batch prompt before importing this reply.')
    expected=batch['panel_ids'];pending_file=pending_path(manga_id,batch_id)
    pending=storage.read_json(pending_file)
    if pending and (pending.get('format')!='beats-v1' or pending['fingerprint']!=fingerprint or pending.get('context_token')!=previous['token']):pending=None
    try:
        def unique_keys(pairs):
            result={}
            for key,value in pairs:
                if key in result:raise ValueError(f'Duplicate JSON key: {key}')
                result[key]=value
            return result
        def invalid_constant(value):
            raise ValueError(f'Invalid JSON constant: {value}')
        payload=json.loads(reply,object_pairs_hook=unique_keys,parse_constant=invalid_constant)
    except (json.JSONDecodeError,ValueError) as exc:
        message=f'Invalid JSON: {exc}'
        return {'ok':False,'issues':{},'errors':[message],'fix_ids':[],'fix_beats':[],
                'can_correct':bool(pending and corrections_only),
                'redo_message':'Fix JSON syntax only, retaining the same story beats and text. Return a valid JSON object.\n'+message}
    if corrections_only:
        if not pending:raise ValueError('No current failed story-beat draft exists. Paste a full reply first.')
        if not isinstance(payload,dict) or not isinstance(payload.get('beats'),list):
            raise ValueError('Corrections need a beats array with replace_beat numbers.')
        candidate=json.loads(json.dumps(pending['payload']))
        required=set(pending['validation']['fix_beats']);used=set();replacement_errors=[]
        for beat in payload['beats']:
            index=beat.get('replace_beat') if isinstance(beat,dict) else None
            if not isinstance(index,int) or isinstance(index,bool) or index not in required or index in used:
                replacement_errors.append('Every replacement must identify a different affected replace_beat number: '+', '.join(map(str,sorted(required)))+'.');continue
            used.add(index)
            candidate['beats'][index-1]={k:v for k,v in beat.items() if k!='replace_beat'}
        if replacement_errors:
            validation=dict(pending['validation']);validation['errors']=replacement_errors
            validation.update(ok=False,can_correct=True,redo_message=redo_message(validation,candidate,expected))
            return validation
        for key in ('cast','continuity'):
            if key in payload:candidate[key]=payload[key]
        # Explicit empty corrections remove an affected beat without requiring coverage.
        candidate['beats']=[b for b in candidate['beats'] if not (isinstance(b,dict) and b.get('panel_ids')==[] and b.get('narration')=='')]
        payload=candidate
    validation,beats,cast,continuity=validate_payload(payload,expected)
    if not validation['ok']:
        validation.update(can_correct=isinstance(payload,dict) and isinstance(payload.get('beats'),list) and bool(payload['beats']),
                          redo_message=redo_message(validation,payload,expected))
        storage.write_json(pending_file,{'format':'beats-v1','fingerprint':fingerprint,'context_token':previous['token'],
                           'payload':payload,'validation':validation})
        return validation
    state=load_state(manga_id);state['schema_version']=4
    chapter_state=state['chapters'].setdefault(chapter,{'batches':{}})
    chapter_state['batches'][batch_id]={'format':'beats-v1','fingerprint':fingerprint,'context_token':previous['token'],
                                      'beats':beats,'continuity':continuity,'final_recap':bool(final_recap)}
    combined=[];digest=_digest([]);preceding=[]
    for item in batches(manga_id,chapter):
        entry=chapter_state['batches'].get(item['id'])
        if not entry or entry.get('format')!='beats-v1' or entry['fingerprint']!=item['fingerprint'] or entry.get('context_token')!=digest:break
        combined.extend(entry['beats']);preceding.append(dict(entry,id=item['id']));digest=_digest(preceding)
    chapter_state['beats']=combined
    state['cast']=merge_cast(state['cast'],cast)
    storage.write_json(state_path(manga_id),state)
    pending_file.unlink(missing_ok=True)
    prompt_file(manga_id,chapter,batch_id,final_recap)
    selected=sum(len(b['panel_ids']) for b in beats)
    return {'ok':True,'imported_beats':len(beats),'selected_panels':selected,'skipped_panels':len(expected)-selected,
            'chapter_beats':len(combined),'cast_count':len(state['cast'])}
