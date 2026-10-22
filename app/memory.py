"""Bounded, manga-scoped context assembled from original saved imports."""
import json
import re
from app import storage
from app.chapter_names import chapter_sort_key

CONTEXT_LIMIT=18000


def digest(value):
    from app.narration import _digest
    return _digest(value)


def ordered(state,current=None):
    return sorted(set(state['chapters'])|({current} if current else set()),key=chapter_sort_key)


def lineage(mid,chapter,batch_id,backend=None):
    from app import narration
    narration=backend or narration
    state=narration.load_state(mid);history=[];prior=None
    for name in ordered(state,chapter):
        if chapter_sort_key(name)>chapter_sort_key(chapter):break
        saved=state['chapters'].get(name,{}).get('batches',{})
        try:current=narration.batches(mid,name)
        except (FileNotFoundError,ValueError) as exc:
            raise FileExistsError(f'{name}: earlier story assets are missing; restore them before continuing.') from exc
        preceding=[]
        def token():
            return digest({'earlier_chapters':prior,'batches':preceding}) if prior else digest(preceding)
        for batch in current:
            if name==chapter and batch['id']==batch_id:return state,history,token(),prior
            entry=saved.get(batch['id'])
            if not entry or entry.get('format')!='beats-v1' or entry.get('fingerprint')!=batch['fingerprint'] or entry.get('context_token')!=token():
                raise FileExistsError(f'Import the current story-beat reply for {name} / {batch["id"]} first. Earlier story or panel changes require dependent replies to be refreshed.')
            preceding.append(dict(entry,id=batch['id']))
            history.append({'chapter':name,'batch_id':batch['id'],**entry})
        prior=digest([prior,{'chapter':name,'batches':preceding}])
    raise FileNotFoundError('Batch not found.')


def packet(mid,chapter,batch_id,backend=None):
    from app import narration
    narration=backend or narration
    state,history,token,seed=lineage(mid,chapter,batch_id,backend)
    ending=[b['narration'] for item in history for b in item['beats']][-3:]
    latest=history[-1]['continuity'] if history else {'summary':'','open_threads':[]}
    cast=list(state.get('seed_cast',[]))
    # Legacy imports stored only one aggregate cast. Expose it only after its
    # original final import, never to prompts for earlier pages.
    cutoff=state.get('legacy_cast_after')
    if cutoff and any([h['chapter'],h['batch_id']]==cutoff for h in history):cast=state.get('legacy_cast',[])
    elif not state['chapters']:cast=state.get('cast',[])
    for entry in history:cast=narration.merge_cast(cast,entry.get('cast',[]))
    summaries={};facts=[];uncertainties=[]
    for h in history:
        summaries[h['chapter']]={'chapter':h['chapter'],'summary':h.get('chapter_summary') or h['continuity']['summary']}
        for f in h.get('memory',[]):
            item={'chapter':h['chapter'],'batch_id':h['batch_id'],**f}
            (facts if f['status']=='confirmed' else uncertainties).append(item)
        for b in h['beats']:
            if b.get('unsure'):uncertainties.append({'chapter':h['chapter'],'batch_id':h['batch_id'],'text':b['unsure'],'panel_ids':b['panel_ids'],'status':'uncertain'})
    terms=set(re.findall(r'\w+',(' '.join(ending)+' '+latest['summary']+' '+' '.join(latest['open_threads'])).casefold()))
    def relevance(item):return len(terms & set(re.findall(r'\w+',json.dumps(item,ensure_ascii=False).casefold())))
    def bounded(items,limit):
        result=[];used=2
        for item in items:
            size=len(json.dumps(item,ensure_ascii=False))+2
            if used+size<=limit:result.append(item);used+=size
        return result
    # Stable relevance + recency selects facts; original records are never compressed or rewritten.
    seen=set();unique=[]
    for fact in reversed(facts):
        key=fact['text'].strip().casefold()
        if key not in seen:unique.append(fact);seen.add(key)
    ranked=sorted(unique,key=relevance,reverse=True)
    confirmed=bounded(ranked,4200);uncertain=bounded(reversed(uncertainties),1800)
    chapter_rows=list(summaries.values());recent=[{'chapter':r['chapter'],'summary':r['summary'][:1200]} for r in chapter_rows[-2:]]
    older=bounded([{'chapter':r['chapter'],'summary_excerpt':r['summary'][:180]} for r in reversed(chapter_rows[:-2])],2500)
    cast_ranked=sorted(reversed(cast),key=relevance,reverse=True);selected_cast=bounded(cast_ranked,3500)
    data={'token':token,'chapter_seed':seed,'previous_narration_ending':[text[-500:] for text in ending],
          'continuity':{'summary':latest['summary'][:1800],'open_threads':[x[:180] for x in latest['open_threads'][:10]]},
          'preceding_batches':[h['chapter']+' / '+h['batch_id'] for h in history[-6:]],
          'confirmed_facts':confirmed,'uncertain_claims':uncertain,'recent_chapters':recent,'older_chapter_excerpts':older,'cast':selected_cast,
          'coverage':{'saved_batches':len(history),'facts_saved':len(facts),'facts_included':len(confirmed),'uncertainties_saved':len(uncertainties),'uncertainties_included':len(uncertain),'chapters_saved':len(chapter_rows),'older_chapters_included':len(older),'cast_saved':len(cast),'cast_included':len(selected_cast)},
          'instructions':'Use confirmed facts as prior context only. Uncertain claims override summaries and cast; neither import nor repetition verifies a claim. Older excerpts may be incomplete. Read current images for new events. This packet is sufficient to start a fresh AI chat.'}
    for key in ('older_chapter_excerpts','confirmed_facts','cast','uncertain_claims','recent_chapters'):
        while data[key] and len(json.dumps(data,ensure_ascii=False))>CONTEXT_LIMIT:data[key].pop()
    data['coverage'].update(facts_included=len(data['confirmed_facts']),cast_included=len(data['cast']),uncertainties_included=len(data['uncertain_claims']),older_chapters_included=len(data['older_chapter_excerpts']))
    assert len(json.dumps(data,ensure_ascii=False))<=CONTEXT_LIMIT
    return data


def normalize(items,allowed,unsure_ids=()):
    if not isinstance(items,list) or len(items)>30:raise ValueError('memory must be an array of at most 30 facts.')
    result=[]
    for item in items:
        if not isinstance(item,dict) or not isinstance(item.get('text'),str) or not 1<=len(item['text'].strip())<=400:raise ValueError('Each memory fact needs text of 1–400 characters.')
        if item.get('status') not in {'confirmed','uncertain'}:raise ValueError('Memory status must be confirmed or uncertain.')
        ids=item.get('panel_ids')
        if not isinstance(ids,list) or not ids or any(not isinstance(pid,str) or pid not in allowed for pid in ids):raise ValueError('Each memory fact needs supporting panel_ids from this batch.')
        status='uncertain' if set(ids)&set(unsure_ids) else item['status']
        result.append({'text':item['text'].strip(),'status':status,'panel_ids':list(dict.fromkeys(ids))})
    return result


def overview(mid,query=''):
    from app import narration
    state=narration.load_state(mid);rows=[]
    for chapter in ordered(state):
        for bid,entry in sorted(state['chapters'][chapter].get('batches',{}).items()):
            row={'chapter':chapter,'batch_id':bid,'chapter_summary':entry.get('chapter_summary') or entry.get('continuity',{}).get('summary',''),
                 'memory':entry.get('memory',[]),'cast':entry.get('cast',state.get('legacy_cast',[]) if state.get('legacy_cast_after')==[chapter,bid] else []),'uncertainties':[b['unsure'] for b in entry.get('beats',[]) if b.get('unsure')],
                 'prior_revisions':len(state['chapters'][chapter].get('history',{}).get(bid,[]))}
            if not query or query.casefold() in json.dumps(row,ensure_ascii=False).casefold():rows.append(row)
    return {'revision':digest(state),'chapters':ordered(state),'entries':rows,'context_limit':CONTEXT_LIMIT}


def edit(mid,chapter,bid,revision,summary,items,cast=None):
    from app import narration
    state=narration.load_state(mid)
    if digest(state)!=revision:raise FileExistsError('Story memory changed. Reload before saving.')
    entry=state['chapters'].get(chapter,{}).get('batches',{}).get(bid)
    if not entry:raise FileNotFoundError('Saved story batch not found.')
    if not isinstance(summary,str) or len(summary)>1200:raise ValueError('Chapter summary must be at most 1200 characters.')
    batch=narration.find_batch(mid,chapter,bid)
    facts=normalize(items,batch['panel_ids'])
    previous=json.loads(json.dumps(entry))
    if state.get('legacy_cast_after')==[chapter,bid] and 'cast' not in previous:previous['cast']=state.get('legacy_cast',[])
    entry.update(chapter_summary=summary,memory=facts)
    if cast is not None:
        if not isinstance(cast,list) or len(cast)>100 or any(not isinstance(item,dict) or any(not isinstance(item.get(f,''),str) or len(item.get(f,''))>500 for f in narration.CAST_FIELDS) for item in cast):raise ValueError('Cast must be an array of string-field entries, at most 500 characters per field.')
        entry['cast']=[{f:item.get(f,'') for f in narration.CAST_FIELDS} for item in cast]
        if state.get('legacy_cast_after')==[chapter,bid]:state.update(legacy_cast=[],legacy_cast_after=None)
    if entry!=previous:state['chapters'][chapter].setdefault('history',{}).setdefault(bid,[]).append(previous)
    storage.write_json(narration.state_path(mid),state)
    return overview(mid)
