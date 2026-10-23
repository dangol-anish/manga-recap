"""Batch narration and persistent, manga-scoped story context."""
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import tempfile
import zipfile
from app import storage, review

CAST_FIELDS = ('name','gender','age_group','role','look')


def context(manga_id, chapter):
    return review.review_context(manga_id,chapter)


def root(manga_id, chapter='Chapter 1'):
    return storage.OUTPUTS / manga_id / chapter / 'stage3'


def state_path(manga_id):
    # Narration and manga cast are committed together in one atomic JSON write.
    return storage.DATA / 'manga' / manga_id / 'narration.json'


def load_state(manga_id):
    state=storage.read_json(state_path(manga_id))
    if state is not None:
        if 'legacy_cast_after' not in state:
            from app.memory import ordered
            entries=[(c,b) for c in ordered(state) for b in sorted(state['chapters'][c].get('batches',{}))]
            state['legacy_cast_after']=list(entries[-1]) if entries else None
            state['legacy_cast']=state.get('cast',[]) if entries else []
        return state
    cast=storage.read_json(storage.DATA/'manga'/manga_id/'cast.json',[])
    if isinstance(cast,dict):cast=cast.get('cast',[])
    if not isinstance(cast,list):
        raise ValueError('Saved manga cast must be a JSON list.')
    return {'schema_version':6,'cast':cast,'chapters':{},'legacy_cast_after':None,'legacy_cast':[]}


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
    from app.memory import packet
    return packet(manga_id,chapter,batch_id)


def prompt_text(manga_id,chapter,batch_id='batch-001',final_recap=False):
    batch=find_batch(manga_id,chapter,batch_id)
    previous=story_context(manga_id,chapter,batch_id)
    master=(storage.ROOT/'prompts/narration_master.md').read_text(encoding='utf-8')
    cast=previous['cast']
    sections=[master,'MANGA: '+manga_id+' / CHAPTER: '+chapter,'CURRENT_BATCH: '+batch_id,'FINAL_RECAP: '+str(bool(final_recap)).lower(),
              'VALID_PANEL_IDS (exact IDs, in reading order):\n'+json.dumps(batch['panel_ids']),
              'Previous narration ending and saved story context:\n'+json.dumps(previous,ensure_ascii=False,indent=2)]
    sections.append('BATCH_RECEIPT (copy exactly into batch_receipt in your reply):\n'+json.dumps({'manga_id':manga_id,'chapter':chapter,'batch_id':batch_id,'fingerprint':batch['fingerprint'],'context_token':previous['token']}))
    if cast:sections.append('Saved cast list:\n'+json.dumps({'cast':cast},ensure_ascii=False,indent=2))
    return '\n\n'.join(sections)+'\n'


def prompt_file(manga_id,chapter,batch_id='batch-001',final_recap=False):
    text=prompt_text(manga_id,chapter,batch_id,final_recap)
    path=root(manga_id,chapter)/'prompts'/batch_id/'narration_prompt.md'
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(dir=path.parent,prefix='.prompt-',suffix='.tmp')
    try:
        with os.fdopen(fd,'w', encoding='utf-8') as stream:stream.write(text)
        os.replace(name,path)
        if batch_id=='batch-001' and not final_recap:
            legacy=root(manga_id,chapter)/'narration_prompt.md'
            legacy.write_text(text, encoding='utf-8')
    finally:
        if os.path.exists(name):os.unlink(name)
    return path


def export_zip(manga_id,chapter,batch_id,fingerprint):
    batch=find_batch(manga_id,chapter,batch_id,fingerprint)
    target=root(manga_id,chapter)/'exports'/(batch_id+'-'+fingerprint+'.zip')
    if target.exists():return target
    target.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(dir=target.parent,prefix='.zip-',suffix='.tmp');os.close(fd)
    try:
        # PNGs are already compressed; storing them avoids CPU-heavy recompression.
        with zipfile.ZipFile(name,'w',compression=zipfile.ZIP_STORED) as archive:
            for record in batch['page_records']:
                page=record['page']
                path=review.asset_path(manga_id,chapter,page,'labeled',record['revision'])
                archive.write(path,arcname=review.page_id(review.review_context(manga_id,chapter,page)[1])+'.png')
        os.replace(name,target)
    finally:
        if os.path.exists(name):os.unlink(name)
    return target


def pending_path(manga_id,batch_id,chapter='Chapter 1'):
    return root(manga_id,chapter)/'pending'/(batch_id+'.json')


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
        draft=storage.read_json(pending_path(manga_id,batch['id'],chapter))
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
        if 'anchors' in beat and isinstance(text,str) and text.strip():
            from app.alignment import validate_map
            anchor_errors,anchors=validate_map({'number':i,'panel_ids':ids,'narration':text},{'beat':i,'anchors':beat['anchors']})
            if anchor_errors:
                errors.extend(f'Beat {i} anchors: '+e for e in anchor_errors);bad.add(i)
            else:normalized[-1]['anchors']=[{'panel_id':a['panel_id'],'phrase':a['phrase']} for a in anchors]

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
    beats=payload.get('beats',[]) if isinstance(payload,dict) else []
    beats=beats if isinstance(beats,list) else []
    required=sorted(set(validation['fix_beats']))
    lines=[
        'TASK: REPAIR THE FAILED DRAFT. This is a PARTIAL CORRECTION request, not a new full-batch narration request.',
        'Use the original uploaded manga photos and the original batch prompt for evidence and story style. The correction output contract below replaces the original full-reply output schema for this turn.',
        'Do not return the entire original beats array. Do not return unaffected beats. Do not renumber original beats after leaving some out.',
        'Every replacement MUST contain replace_beat as a JSON INTEGER identifying its ORIGINAL draft position. A replacement without replace_beat is invalid. Do not use id, beat, beat_number, a string number, or the replacement array position instead.',
        'REQUIRED_REPLACE_BEAT_NUMBERS: '+json.dumps(required),
        'Return exactly one replacement for EACH number in REQUIRED_REPLACE_BEAT_NUMBERS, in that order, with no duplicate or additional numbers. Keep these prefilled numbers unchanged in the template below.',
        'ERRORS TO FIX:'
    ]
    lines += list(dict.fromkeys(validation['errors']))
    lines += [pid+': '+' '.join(reasons) for pid,reasons in validation['issues'].items()]
    if not beats:
        lines += ['There is no usable draft to patch. Return a full reply following the original batch prompt instead, and import it with Corrections only OFF.']
        return '\n'.join(lines)
    template=[]
    for index in required:
        old=beats[index-1] if 0<index<=len(beats) and isinstance(beats[index-1],dict) else {}
        item={'replace_beat':index,'panel_ids':old.get('panel_ids',[]),'narration':old.get('narration',''),'unsure':old.get('unsure',''),'anchors':old.get('anchors',[])}
        template.append(item)
    frozen=[{'original_beat':i,'panel_ids':beat.get('panel_ids',[])} for i,beat in enumerate(beats,1) if i not in required and isinstance(beat,dict)]
    lines += [
        'FROZEN_UNAFFECTED_BEATS (already retained by the app; DO NOT include them in your reply):\n'+json.dumps(frozen),
        'VALID_PANEL_IDS (authoritative reading order):\n'+json.dumps(expected),
        'REPAIR RULES:',
        '- panel_ids selects only the panels actually displayed in a beat. You may omit unnecessary panels. Do not list every panel you read for context.',
        '- For each non-silent replacement, panel_ids MUST exactly equal the list of anchors panel_id values: same IDs, same count, same order. If only three useful anchors fit, select only those three panels; do not keep extra unanchored panels. Recheck story grounding and the 40-word-per-selected-panel budget after changing selection.',
        '- Every anchor phrase is copied verbatim from its own narration, occurs exactly once there, and begins/ends on whole words. The first panel starts at time zero automatically; its phrase may follow a short lead-in. Later phrases advance without overlap. Never invent matching words or add filler to satisfy anchors.',
        '- Mentally insert replacements at their ORIGINAL replace_beat positions among the frozen beats, then flatten ALL selected panel_ids. Every ID must occur once and move strictly forward through VALID_PANEL_IDS. Check boundaries between beats, not just the inside of each beat. An earlier skipped ID cannot reappear after a later ID.',
        '- If two affected beats cross in reading order, repair their grouping and supported narration together. Do not just swap ID labels onto the wrong events. Frozen beats and their IDs cannot be changed or reused.',
        '- Preserve narration where compatible with evidence and order. Revise it when needed for correct grouping or fewer selected panels. Keep the story continuous; no unsupported claims or batch closing.',
        '- To delete an affected beat, keep its ORIGINAL replace_beat and return panel_ids: [], narration: "", unsure: "", anchors: []. Never renumber later replacements.',
        'REPLACEMENT_TEMPLATE (copied from the FAILED draft, so its content still needs repair; replace_beat numbers are already correct):\n'+json.dumps({'beats':template},ensure_ascii=False),
        'Reply with the repaired template JSON only. The top-level beats array contains only the numbered replacements above. Existing cast, continuity, chapter_summary, memory and batch_receipt are retained automatically. Include a top-level metadata field only if an error above requires correcting it, and preserve its supported content.',
        'FINAL OUTPUT CHECK: the replace_beat list equals REQUIRED_REPLACE_BEAT_NUMBERS exactly; all required non-silent anchors match panel_ids exactly; phrases match narration; the combined frozen-plus-replaced sequence has no duplicate or backwards ID; word limits pass. Return no markdown, explanations, or full-batch reply.'
    ]
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


def import_reply(manga_id,chapter,batch_id,fingerprint,reply,corrections_only=False,final_recap=False,context_token=None,require_receipt=False,backend=None):
    import sys
    engine=backend or sys.modules[__name__]
    batch=engine.find_batch(manga_id,chapter,batch_id,fingerprint)
    previous=engine.story_context(manga_id,chapter,batch_id)
    if context_token is not None and context_token!=previous['token']:
        raise FileExistsError('Earlier narration changed. Download a fresh batch prompt before importing this reply.')
    expected=batch['panel_ids'];pending_file=engine.pending_path(manga_id,batch_id,chapter)
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
    # A complete fresh-chat reply has an unambiguous schema. Validate it as a
    # full reply even when the previous failure left the patch checkbox checked.
    full_fields={'batch_receipt','cast','beats','continuity','chapter_summary','memory'}
    complete_reply=(isinstance(payload,dict) and full_fields<=payload.keys()
                    and isinstance(payload.get('beats'),list) and bool(payload['beats'])
                    and all(isinstance(beat,dict) and 'replace_beat' not in beat for beat in payload['beats']))
    if corrections_only and complete_reply:corrections_only=False
    reply_mode='corrections' if corrections_only else 'full'
    if corrections_only:
        if not pending or not pending['validation'].get('can_correct'):raise ValueError('No correctable current draft exists. Generate and paste a full reply from the current prompt.')
        required=set(pending['validation']['fix_beats']);used=set();invalid=[]
        replacements=payload.get('beats') if isinstance(payload,dict) else None
        if isinstance(replacements,list):
            for position,beat in enumerate(replacements,1):
                index=beat.get('replace_beat') if isinstance(beat,dict) else None
                if not isinstance(index,int) or isinstance(index,bool) or index not in required or index in used:
                    invalid.append(position)
                else:used.add(index)
        if not isinstance(replacements,list) or invalid:
            numbers=', '.join(map(str,sorted(required))) or 'none (metadata correction only)'
            message='This is not a numbered partial correction. Each replacement needs a unique integer replace_beat from: '+numbers+'.'
            if invalid:message+=' Check replacement entries: '+', '.join(map(str,invalid))+'.'
            message+=' If this is a full batch reply, include batch_receipt, cast, beats, continuity, chapter_summary and memory; the app will recognize it automatically.'
            return {'ok':False,'reply_mode':'corrections','issues':{},'errors':[message],
                    'fix_ids':[],'fix_beats':sorted(required),'can_correct':True,
                    'redo_message':redo_message(pending['validation'],pending['payload'],expected)}
        candidate=json.loads(json.dumps(pending['payload']))
        for beat in replacements:
            candidate['beats'][beat['replace_beat']-1]={k:v for k,v in beat.items() if k!='replace_beat'}
        for key in ('cast','continuity','chapter_summary','memory','batch_receipt'):
            if key in payload:candidate[key]=payload[key]
        # Explicit empty corrections remove an affected beat without requiring coverage.
        candidate['beats']=[b for b in candidate['beats'] if not (isinstance(b,dict) and b.get('panel_ids')==[] and b.get('narration')=='')]
        payload=candidate
    expected_receipt={'manga_id':manga_id,'chapter':chapter,'batch_id':batch_id,'fingerprint':fingerprint,'context_token':previous['token']}
    supplied=payload.get('batch_receipt') if isinstance(payload,dict) else None
    if isinstance(supplied,dict) and supplied!=expected_receipt:
        if all(supplied.get(k)==expected_receipt[k] for k in ('manga_id','chapter','batch_id')):
            raise FileExistsError('This AI reply used older panels or story context. Download the current prompt and generate a new full reply; do not just replace its receipt.')
        message='This reply belongs to another manga, chapter or batch. Generate a new full reply using the current batch images and prompt; do not just relabel its receipt.'
        return {'ok':False,'issues':{},'errors':[message],'fix_ids':[],'fix_beats':[],'can_correct':False,'redo_message':message}
    validation,beats,cast,continuity=validate_payload(payload,expected)
    validation['reply_mode']=reply_mode
    receipt={'manga_id':manga_id,'chapter':chapter,'batch_id':batch_id,'fingerprint':fingerprint,'context_token':previous['token']}
    if isinstance(payload,dict) and (require_receipt or 'batch_receipt' in payload) and payload.get('batch_receipt')!=receipt:
        validation['errors'].append('batch_receipt is missing or does not match this manga, chapter, batch and context. Copy BATCH_RECEIPT exactly from the current prompt: '+json.dumps(receipt))
    try:
        from app.memory import normalize
        unsure_ids=[pid for beat in beats if beat and beat.get('unsure') for pid in beat['panel_ids']]
        facts=normalize(payload.get('memory',[]),expected,unsure_ids) if isinstance(payload,dict) else []
        summary=payload.get('chapter_summary','') if isinstance(payload,dict) else ''
        if not isinstance(summary,str) or len(summary)>1200:raise ValueError('chapter_summary must be a string of at most 1200 characters.')
    except ValueError as exc:validation['errors'].append(str(exc));facts=[];summary=''
    if require_receipt and isinstance(payload,dict):
        for field in ('chapter_summary','memory'):
            if field not in payload:validation['errors'].append(f'{field} is required by the fresh-chat format. Return the requested memory metadata without rewriting narration.')
        for index,beat in enumerate(beats,1):
            if beat and isinstance(beat.get('narration'),str) and beat['narration'].strip() and not beat.get('anchors') and index not in validation['fix_beats']:
                validation['errors'].append(f'Beat {index}: add exact phrase anchors for every selected panel, keeping its narration unchanged.')
                validation['fix_beats']=sorted(set(validation['fix_beats'])|{index})
    validation['ok']=not validation['errors'] and not validation['issues']
    if not validation['ok']:
        validation.update(can_correct=isinstance(payload,dict) and isinstance(payload.get('beats'),list) and bool(payload['beats']),
                          redo_message=redo_message(validation,payload,expected))
        storage.write_json(pending_file,{'format':'beats-v1','fingerprint':fingerprint,'context_token':previous['token'],
                           'payload':payload,'validation':validation})
        return validation
    state=engine.load_state(manga_id);state['schema_version']=6
    chapter_state=state['chapters'].setdefault(chapter,{'batches':{}})
    old=chapter_state['batches'].get(batch_id)
    if old and state.get('legacy_cast_after')==[chapter,batch_id]:
        old=dict(old,cast=old.get('cast',state.get('legacy_cast',[])))
        state.update(legacy_cast=[],legacy_cast_after=None)
    if old:chapter_state.setdefault('history',{}).setdefault(batch_id,[]).append(old)
    chapter_state['batches'][batch_id]={'format':'beats-v1','fingerprint':fingerprint,'context_token':previous['token'],
                                      'beats':beats,'continuity':continuity,'cast':cast,'chapter_summary':summary,'memory':facts,'final_recap':bool(final_recap)}
    combined=[];preceding=[];seed=previous.get('chapter_seed')
    for item in engine.batches(manga_id,chapter):
        digest=_digest({'earlier_chapters':seed,'batches':preceding}) if seed else _digest(preceding)
        entry=chapter_state['batches'].get(item['id'])
        if not entry or entry.get('fingerprint')!=item['fingerprint'] or entry.get('context_token')!=digest:break
        combined.extend(entry['beats']);preceding.append(dict(entry,id=item['id']))
    chapter_state['beats']=combined
    state['cast']=merge_cast(state['cast'],cast)
    storage.write_json(engine.state_path(manga_id),state)
    pending_file.unlink(missing_ok=True)
    selected=sum(len(b['panel_ids']) for b in beats)
    return {'ok':True,'reply_mode':reply_mode,'imported_beats':len(beats),'selected_panels':selected,'skipped_panels':len(expected)-selected,
            'chapter_beats':len(combined),'cast_count':len(state['cast'])}
