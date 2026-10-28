"""Exact phrase anchors and real speech timestamps for any registered chapter."""
from app import errors
from app.tasks import lock as task_lock, ensure_idle
import asyncio
import json
import math
from pathlib import Path
import re
import shutil
import tempfile
import threading
import unicodedata
import uuid

from app import audio, video, storage

lock = threading.RLock()
jobs = {}

def job_key(mid, chapter):
    return mid if chapter=='Chapter 1' else (mid,chapter)


def root(mid, chapter='Chapter 1'):
    return video.root(mid, chapter)/'alignment'


def context(mid, chapter, number):
    beats, _ = video.source(mid, chapter)
    if not 1 <= number <= len(beats):raise FileNotFoundError('Beat not found.')
    beat = beats[number-1]
    token = video.digest([beat['token'],[(p['id'],p['revision']) for p in beat['panels']]])
    return beat, token


def validate_map(beat, payload):
    errors=[]
    if not isinstance(payload,dict):return ['Reply must be a JSON object.'],[]
    if set(payload) != {'beat','anchors'}:
        errors.append('Return only beat and anchors. Narration, cast and continuity cannot be changed here.')
    if isinstance(payload.get('beat'),bool) or payload.get('beat')!=beat['number']:
        errors.append(f'Reply must identify beat {beat["number"]}.')
    anchors=payload.get('anchors')
    if not isinstance(anchors,list):return errors+['anchors must be an array.'],[]
    expected=beat['panel_ids']
    actual=[a.get('panel_id') if isinstance(a,dict) else None for a in anchors]
    # Object order is formatting; preserve every explicit panel/phrase pairing.
    if len(actual)==len(expected) and len(set(expected))==len(expected) and all(actual.count(pid)==1 for pid in expected):
        by_id={a['panel_id']:a for a in anchors}
        anchors=[by_id[pid] for pid in expected]
        actual=list(expected)
    if actual!=expected:
        errors.append('Panel IDs must appear exactly once, in this order: '+', '.join(expected)+'. Received: '+', '.join(str(x) for x in actual)+'.')
    normalized=[];previous_end=0;previous_pid=None;previous_phrase=None
    for i,anchor in enumerate(anchors):
        if not isinstance(anchor,dict) or set(anchor)!={'panel_id','phrase'}:
            errors.append(f'Anchor {i+1}: provide panel_id and phrase only.');continue
        pid=anchor['panel_id'];phrase=anchor['phrase']
        if not isinstance(phrase,str) or not phrase.strip():
            errors.append(f'{pid}: phrase must be non-empty.');continue
        starts=[m.start() for m in re.finditer(re.escape(phrase),beat['narration'])]
        if len(starts)!=1:
            errors.append(f'{pid}: phrase must occur exactly once, copied verbatim from this beat; found {len(starts)} matches.');continue
        start=starts[0];end=start+len(phrase)
        if (start and beat['narration'][start-1].isalnum() and phrase[0].isalnum()) or (end<len(beat['narration']) and beat['narration'][end].isalnum() and phrase[-1].isalnum()):
            errors.append(f'{pid}: phrase must begin and end on whole words.')
        if i and start<previous_end:
            # Switches depend on phrase STARTS. A long earlier phrase may safely
            # end at the next switch without changing either panel's start time.
            previous=normalized[-1] if normalized else None
            prefix=beat['narration'][previous['char_start']:start].rstrip() if previous and start>previous['char_start'] else ''
            if (previous and previous['panel_id']==previous_pid and re.search(r'\w',prefix)
                    and len(list(re.finditer(re.escape(prefix),beat['narration'])))==1
                    and not (start and beat['narration'][start-1].isalnum() and phrase[0].isalnum())):
                previous.update(phrase=prefix,char_end=previous['char_start']+len(prefix))
            else:
                errors.append(f'{previous_pid} → {pid}: panel reading order requires {previous_pid} first, but its phrase {previous_phrase!r} follows or overlaps {phrase!r}. Re-map these panels from the images, revise this beat to match reading order, or omit an unnecessary panel. Do not swap IDs onto unrelated events.')
        if not re.search(r'\w',phrase):errors.append(f'{pid}: phrase must contain a spoken word.')
        previous_end=end;previous_pid=pid;previous_phrase=phrase
        normalized.append({'panel_id':pid,'phrase':phrase,'char_start':start,'char_end':end})
    return errors,normalized


def read_map(mid, token, number, chapter='Chapter 1'):
    saved=storage.read_json(root(mid, chapter)/f'beat-{number:03}-map.json')
    if saved and saved.get('source_token')==token:return saved
    try:beat=next((b for b in audio.beats(mid,chapter) if b['number']==number),None)
    except (FileNotFoundError,FileExistsError,ValueError):return None
    if beat and beat.get('anchors'):
        errors,anchors=validate_map(beat,{'beat':number,'anchors':beat['anchors']})
        if not errors:return {'source_token':token,'beat':number,'anchors':anchors}
    return None


def save_map(mid,chapter,number,token,reply):
    with lock:
        beat,current=context(mid,chapter,number)
        if token!=current:raise FileExistsError('The script or panel revisions changed. Download a fresh alignment prompt.')
        def unique(pairs):
            value={}
            for k,v in pairs:
                if k in value:raise ValueError(f'Duplicate JSON key: {k}')
                value[k]=v
            return value
        try:payload=json.loads(reply,object_pairs_hook=unique)
        except ValueError as exc:return {'ok':False,'errors':['Invalid JSON: '+str(exc)]}
        errors,anchors=validate_map(beat,payload)
        if errors:return {'ok':False,'errors':errors}
        saved={'source_token':token,'beat':number,'anchors':anchors}
        storage.write_json(root(mid, chapter)/f'beat-{number:03}-map.json',saved)
        return {'ok':True,'anchors':anchors}


def prompt_text(mid,chapter,number):
    beat,token=context(mid,chapter,number)
    return ('Match the supplied labeled manga panels to this EXISTING narration. This is an alignment task, not a rewrite.\n'
            'Inspect the images before mapping. Do not infer story content from IDs or prior knowledge. Keep every script word unchanged.\n'
            'For each selected panel, copy a short exact phrase where that panel should FIRST appear. The first panel is displayed from time zero, even if its identifying phrase follows a short lead-in. '
            'Every phrase must occur exactly once in the script, start/end on whole words, and follow script order without overlap. '
            'Use every selected panel exactly once in the supplied order. A panel may remain through multiple sentences; a sentence may contain several panel switches at distinct phrases. '
            'Choose phrases based on what the images actually show, not evenly spaced words or sentence counts. Do not invent panels or add events. '
            'If no honest ordered mapping is possible, return {"error":"Explain which panel or script claim cannot be matched"} instead of guessing; that error is not importable.\n'
            'Return JSON only: {"beat":NUMBER,"anchors":[{"panel_id":"EXACT_ID","phrase":"exact unique phrase from the script"}]}. '
            'No narration, cast, continuity, or timestamps in the reply.\n\n'
            +json.dumps({'beat':number,'source_token':token,'script':beat['narration'],'selected_panel_ids':beat['panel_ids'],
                         'pages_to_attach':sorted(set(p['id'].split('-')[0] for p in beat['panels']))},ensure_ascii=False,indent=2)+'\n')


def adjusted_time(t,removed):
    return t-sum(max(0,min(t,cut['end'])-cut['start']) for cut in removed)+sum(cut.get('added',0) for cut in removed if t>=cut['start'])


def script_tokens(text):
    return [(unicodedata.normalize('NFKC',m.group()).casefold().replace('’',"'"),m.start(),m.end())
            for m in re.finditer(r"\w+(?:['’]\w+)*",text)]


def aligned_words(text,events,removed,duration):
    expected=script_tokens(text);found=[]
    for event in events:
        start=event['offset']/10_000_000;end=start+event['duration']/10_000_000
        if not math.isfinite(start) or not math.isfinite(end) or start<0 or end<=start:
            raise ValueError('Speech service returned invalid word times.')
        for token,_,_ in script_tokens(event['text']):found.append((token,start,end))
    if [x[0] for x in found]!=[x[0] for x in expected]:
        raise ValueError('Speech word metadata does not match the frozen script. No guessed timing was saved.')
    words=[];last=-1
    for (token,cs,ce),(_,start,end) in zip(expected,found):
        a=adjusted_time(start,removed);b=adjusted_time(end,removed)
        if a<last or a>=duration or b>duration+.15:
            raise ValueError('Speech word times fall outside the shortened audio or move backwards.')
        words.append({'text':text[cs:ce],'char_start':cs,'char_end':ce,'start':max(0,a),'end':min(duration,b),
                      'raw_start':start,'raw_end':end})
        last=a
    return words


def anchor_times(beat,anchors,words,duration):
    rows=[]
    for i,a in enumerate(anchors):
        word=next((w for w in words if w['char_start']>=a['char_start'] and w['char_start']<a['char_end']),None)
        if word is None:raise ValueError(f'{a["panel_id"]}: phrase could not be located in timed speech.')
        rows.append(dict(a,start=0.0 if i==0 else word['start']))
    video.validate_switches([r['start'] for r in rows[1:]],len(anchors),duration)
    for i,row in enumerate(rows):row['end']=rows[i+1]['start'] if i+1<len(rows) else duration
    return rows


async def capture(text,target,rate="+0%",voice=None):
    import edge_tts
    events=[]
    async def stream():
        with open(target,'wb') as out:
            async for chunk in edge_tts.Communicate(text,voice or audio.VOICE,rate=rate,boundary='WordBoundary').stream():
                if chunk['type']=='audio':out.write(chunk['data'])
                elif chunk['type']=='WordBoundary':events.append({k:chunk[k] for k in ('text','offset','duration')})
    await asyncio.wait_for(stream(),timeout=120)
    if not events:raise ValueError('The speech service returned no word timing metadata.')
    return events


def trial_token(beat,source_token,mapping,pacing=None):
    value=[source_token,mapping['anchors'],audio.VOICE,audio.PAUSE_PROFILE,'word-boundaries-v1']
    if pacing:value.append(pacing)
    return video.digest(value)


def speech(text, target, pacing=None, voice=None):
    settings=pacing or {}
    events=asyncio.run(capture(text,target,settings.get('rate','+0%'),voice))
    raw_duration=len(video.pcm(target))/48000
    boundaries=[]
    if settings:
        raw_words=aligned_words(text,events,[],raw_duration)
        for i,word in enumerate(raw_words):
            next_word=raw_words[i+1] if i+1<len(raw_words) else None
            tail=text[word['char_end']:next_word['char_start'] if next_word else len(text)]
            if '.' in tail:
                boundaries.append({'start':word['start'],'end':next_word['start'] if next_word else raw_duration})
    pause_warnings=[]
    edits=audio.shorten_pauses(target, boundaries, settings.get('sentence_pause',.18),warnings=pause_warnings)
    duration=len(video.pcm(target))/48000
    return {'raw_duration':raw_duration,'duration':duration,
            'words':aligned_words(text,events,edits,duration),'removed_silence':edits,
            'sentence_pauses':len(boundaries),'pause_warnings':pause_warnings,'pacing':settings}



def status(mid,chapter,number,pacing=None):
    with lock:
        beat,token=context(mid,chapter,number);mapping=read_map(mid,token,number,chapter)
        settings=audio.pacing(mid, chapter) if pacing is None else pacing
        saved=storage.read_json(root(mid, chapter)/f'beat-{number:03}-trial.json')
        ready=bool(saved and mapping and saved.get('trial_token')==trial_token(beat,token,mapping,settings))
        if ready:
            for kind in ('mp3','mp4'):
                ready=ready and (root(mid, chapter)/saved['revision']/('trial.'+kind)).is_file()
        return {'beat':number,'source_token':token,'script':beat['narration'],'panel_ids':beat['panel_ids'],
                'anchors':mapping['anchors'] if mapping else [],'ready':ready,'trial':saved if ready else None,
                'trial_allowed':not beat.get('silent',False),
                'job':dict(jobs.get(job_key(mid,chapter),{'running':False,'message':'Save a phrase map, then generate this beat preview.'}))}


def run(mid,chapter,beat,token,mapping,pacing=None):
    settings=audio.pacing(mid, chapter) if pacing is None else pacing
    try:
        root(mid, chapter).mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='trial-',dir=root(mid, chapter)) as name:
            work=Path(name);mp3=work/'trial.mp3'
            spoken=speech(beat['narration'],mp3,settings)
            raw_duration=spoken['raw_duration'];duration=spoken['duration']
            words=spoken['words'];removed=spoken['removed_silence']
            rows=anchor_times(beat,mapping['anchors'],words,duration)
            sample=dict(beat,_audio=str(mp3),duration=duration,switches=[r['start'] for r in rows[1:]])
            with lock:jobs[job_key(mid,chapter)]['message']='Rendering the single-beat phrase-aligned preview…'
            video.render_to({'beats':[sample],'duration':duration},work)
            revision=uuid.uuid4().hex
            result={'revision':revision,'trial_token':trial_token(beat,token,mapping,settings),'beat':beat['number'],
                    'voice':audio.VOICE,'pause_profile':audio.PAUSE_PROFILE,'script':beat['narration'],
                    'pacing':settings,'sentence_pauses':spoken['sentence_pauses'],'visual_style':dict(video.VISUAL_STYLE),'motion_style':dict(video.MOTION_STYLE),'transition_style':dict(video.TRANSITION_STYLE),
                    'duration':duration,'raw_duration':raw_duration,'words':words,'removed_silence':removed,
                    'anchors':rows,'equal_switches':[duration*i/len(rows) for i in range(1,len(rows))]}
            with lock:
                current,newtoken=context(mid,chapter,beat['number'])
                now=read_map(mid,newtoken,beat['number'],chapter)
                if not now or trial_token(current,newtoken,now,settings)!=result['trial_token']:
                    raise FileExistsError('Script, panels or phrase mapping changed during generation. Retry the current version.')
                target=root(mid, chapter)/revision;target.mkdir()
                shutil.copyfile(mp3,target/'trial.mp3');shutil.copyfile(work/'chapter.mp4',target/'trial.mp4')
                storage.write_json(target/'metadata.json',result)
                storage.write_json(root(mid, chapter)/f'beat-{beat["number"]:03}-trial.json',result)
                jobs[job_key(mid,chapter)].update(message=f'Beat {beat["number"]} trial ready. The full chapter has not been regenerated.',error=False)
    except Exception as exc:
        with lock:jobs[job_key(mid,chapter)].update(message=errors.describe(exc,'speech','Create phrase alignment trial',chapter)['message'],error=True)
    finally:
        with lock:jobs[job_key(mid,chapter)]['running']=False


def start(mid,chapter,number,token):
    with task_lock, lock:
        ensure_idle()
        beat,current=context(mid,chapter,number)
        if current!=token:raise FileExistsError('Sources changed. Reload the phrase map.')
        if jobs.get(job_key(mid,chapter),{}).get('running'):raise FileExistsError('An alignment trial is already running.')
        mapping=read_map(mid,current,number,chapter)
        if not mapping:raise ValueError('Save a valid phrase map first.')
        jobs[job_key(mid,chapter)]={'running':True,'message':f'Generating only beat {number} with word timestamps…','error':False}
        threading.Thread(target=run,args=(mid,chapter,beat,current,mapping),daemon=True).start()
        return dict(jobs[job_key(mid,chapter)])


def asset(mid,chapter,number,revision,kind):
    state=status(mid,chapter,number)
    if kind not in ('mp3','mp4') or not state['ready'] or state['trial']['revision']!=revision:
        raise FileNotFoundError('Current alignment trial not found. Generate it first.')
    return root(mid, chapter)/revision/('trial.'+kind)


def refresh_preview_visuals(mid, chapter, number):
    """Re-render an existing timed preview, without synthesis or audio changes."""
    with lock:
        state=status(mid,chapter,number)
        if not state['ready']:raise FileNotFoundError('Generate the current timestamped audio first.')
        saved=state['trial']
        if saved.get('visual_style')==video.VISUAL_STYLE and saved.get('motion_style')==video.MOTION_STYLE and saved.get('transition_style')==video.TRANSITION_STYLE:return saved
        beat,token=context(mid,chapter,number)
        mp3=root(mid, chapter)/saved['revision']/'trial.mp3'
    with tempfile.TemporaryDirectory(prefix='restyle-',dir=root(mid, chapter)) as name:
        work=Path(name)
        sample=dict(beat,_audio=str(mp3),duration=saved['duration'],switches=[a['start'] for a in saved['anchors'][1:]])
        video.render_to({'beats':[sample],'duration':saved['duration']},work)
        with lock:
            current=status(mid,chapter,number)
            if not current['ready'] or current['trial']['revision']!=saved['revision'] or current['source_token']!=token:
                raise FileExistsError('Preview sources changed during rendering.')
            result=dict(saved,revision=uuid.uuid4().hex,visual_style=dict(video.VISUAL_STYLE),motion_style=dict(video.MOTION_STYLE),transition_style=dict(video.TRANSITION_STYLE))
            target=root(mid, chapter)/result['revision'];target.mkdir()
            shutil.copyfile(mp3,target/'trial.mp3')
            shutil.copyfile(work/'chapter.mp4',target/'trial.mp4')
            storage.write_json(target/'metadata.json',result)
            storage.write_json(root(mid, chapter)/f'beat-{number:03}-trial.json',result)
            return result


def apply_chapter(mid, chapter, pacing=None):
    """Resume timestamped clips, render, then publish together."""
    original, source_token = video.source(mid, chapter)
    root(mid,chapter).mkdir(parents=True,exist_ok=True)
    previous_pacing=audio.pacing(mid, chapter)
    settings=previous_pacing if pacing is None else pacing
    if (pacing is not None or settings) and settings not in ({'rate':'+10%','speed':1.1,'sentence_pause':.18}, {'rate':'+20%','speed':1.2,'sentence_pause':.18}, {'rate':'+30%','speed':1.3,'sentence_pause':.18}):
        raise ValueError('Choose 1.1×, 1.2× or 1.3× with a 180 ms period pause.')
    original_script = storage.read_json(storage.DATA / 'manga' / mid / 'narration.json')
    mappings = []
    for beat in original:
        _, token = context(mid, chapter, beat['number'])
        if beat.get('silent',False):
            mappings.append((token,None));continue
        mapping = read_map(mid, token, beat['number'], chapter)
        if not mapping:
            raise ValueError(f'Beat {beat["number"]}: save a current phrase map first.')
        mappings.append((token, mapping))
    planned = []
    manifest = audio.manifest(mid, chapter)
    for beat, (token, mapping) in zip(original, mappings):
        number = beat['number']
        if beat.get('silent',False):
            planned.append(dict(beat,switches=[beat['duration']*i/len(beat['panels']) for i in range(1,len(beat['panels']))]));continue
        print(f'Beat {number}/{len(original)}: checking timestamped audio', flush=True)
        if not status(mid, chapter, number, settings)['ready']:
            jobs[job_key(mid,chapter)] = {'running': True, 'message': f'Generating beat {number}', 'error': False}
            run(mid, chapter, beat, token, mapping, settings)
        current = status(mid, chapter, number, settings)
        if not current['ready']:
            raise ValueError(current['job']['message'])
        trial = current['trial']
        path = root(mid, chapter) / trial['revision'] / 'trial.mp3'
        filename = beat['token'] + '-aligned-' + trial['revision'] + '.mp3'
        target = audio.root(mid, chapter) / filename
        shutil.copyfile(path, target)
        duration = len(video.pcm(target)) / 48000
        switches = [a['start'] for a in trial['anchors'][1:]]
        video.validate_switches(switches, len(beat['panels']), duration)
        planned.append(dict(beat, revision=filename, _audio=str(target), duration=duration, switches=switches))
        manifest['clips'][beat['token']] = {'file': filename, 'duration': duration,
                                           'voice': audio.VOICE, 'pause_filter': audio.PAUSE_PROFILE, 'pacing':settings}
        print(f'Beat {number}: ready, {duration:.2f}s, {len(beat["panels"])} panels', flush=True)
    token = video.digest([[b['token'], b['revision'], b['duration'],
                           [(p['id'], p['revision']) for p in b['panels']]] for b in planned])
    state = {'chapter':chapter,'source_token': token, 'beats': planned, 'duration': sum(b['duration'] for b in planned)}
    state['render_token'] = video.render_token(token, planned)
    print('Rendering complete phrase-aligned chapter MP4…', flush=True)
    with tempfile.TemporaryDirectory(prefix='aligned-chapter-', dir=root(mid, chapter)) as name:
        work = Path(name)
        video.render_to(state, work)
        with lock, video.lock, audio.lock:
            if audio.pacing(mid, chapter) != previous_pacing:
                raise FileExistsError('Speech pacing changed during rendering. Previous video retained.')
            if video.source(mid, chapter)[1] != source_token:
                raise FileExistsError('Chapter sources changed during alignment. Previous video retained.')
            if storage.read_json(storage.DATA / 'manga' / mid / 'narration.json') != original_script:
                raise FileExistsError('Narration changed during alignment. Previous video retained.')
            for beat, (map_token, mapping) in zip(original, mappings):
                if not beat.get('silent',False) and read_map(mid, map_token, beat['number'], chapter) != mapping:
                    raise FileExistsError('Phrase map changed during alignment. Previous video retained.')
            updates = {
                audio.root(mid, chapter)/'audio.json': manifest,
                video.root(mid, chapter)/'timing.json': {'source_token': token, 'mode': 'phrase-aligned',
                    'switches': {str(b['number']): b['switches'] for b in planned}},
                video.root(mid, chapter)/'export.json': {'render_token': state['render_token'], 'duration': state['duration'],
                    'width': video.WIDTH, 'height': video.HEIGHT, 'fps': video.FPS,
                    'panels': sum(len(b['panels']) for b in planned), 'alignment': 'phrase'},
            }
            if settings:updates[audio.root(mid, chapter)/'pacing.json']=settings
            previous = {p: storage.read_json(p) for p in updates}
            (work/'chapter.mp4').replace(video.export_path(mid, state))
            try:
                for path, value in updates.items(): storage.write_json(path, value)
            except Exception:
                for path, value in previous.items():
                    if value is None: path.unlink(missing_ok=True)
                    else: storage.write_json(path, value)
                raise
    report = {'chapter': chapter, 'beats': len(planned), 'panels': sum(len(b['panels']) for b in planned),
              'duration': state['duration'], 'voice': audio.VOICE, 'pause_profile': audio.PAUSE_PROFILE, 'pacing':settings,
              'render_token': state['render_token'], 'file': str(video.export_path(mid, state)),
              'script_unchanged': True,
              'notes': ['Original script and panel order preserved.']}
    storage.write_json(root(mid, chapter)/'chapter-alignment.json', report)
    print(json.dumps(report, indent=2), flush=True)
    return report

# Separate from per-beat trials: a whole-chapter job remains running between beats.
chapter_jobs={}


def chapter_status(mid,chapter):
    return dict(chapter_jobs.get(job_key(mid,chapter),{'running':False,'message':'Save phrase maps for each spoken beat before rebuilding.'}))


def start_chapter(mid,chapter,source_token):
    with task_lock, lock:
        ensure_idle()
        original,current=video.source(mid,chapter)
        if current!=source_token:raise FileExistsError('Chapter sources changed. Reload before rebuilding.')
        if chapter_status(mid,chapter)['running'] or any(j.get('running') for j in jobs.values()):raise FileExistsError('An alignment job is already running.')
        for beat in original:
            if beat['silent']:continue
            _,token=context(mid,chapter,beat['number'])
            if not read_map(mid,token,beat['number'],chapter):raise ValueError(f'Beat {beat["number"]}: save a phrase map first.')
        key=job_key(mid,chapter);chapter_jobs[key]={'running':True,'message':'Applying phrase maps; timestamped speech and video will be generated.','error':False}
        def work():
            try:
                apply_chapter(mid,chapter)
                chapter_jobs[key].update(message='Phrase-aligned audio and video ready.',error=False)
            except Exception as exc:chapter_jobs[key].update(message=errors.describe(exc,'speech','Match speech to panels',chapter)['message'],error=True)
            finally:chapter_jobs[key]['running']=False
        threading.Thread(target=work,daemon=True).start()
        return chapter_status(mid,chapter)
