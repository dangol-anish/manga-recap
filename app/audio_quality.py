"""Pronunciation hints and bounded local signal checks; never synthesize or rewrite audio."""
import math
import re
import unicodedata
import wave
import numpy as np
from app import storage

VERSION = 1


def profile_path(mid):
    from app import grouped_audio as g, detection
    g.check_id(mid)
    if not any(m['id']==mid for m in detection.catalog()['manga']):
        raise ValueError('Choose a manga in this project before saving pronunciation hints.')
    return g.safe(storage.DATA/'manga'/mid/'pronunciation.json')


def normalize(rows):
    if not isinstance(rows,list) or len(rows)>50:raise ValueError('Use at most 50 pronunciation hints per manga.')
    result=[];seen=set()
    for index,row in enumerate(rows,1):
        if not isinstance(row,dict) or set(row)!={'word','say_as'}:raise ValueError(f'Hint {index}: provide the word and how to say it.')
        values=[]
        for field,limit in (('word',80),('say_as',100)):
            value=row[field]
            if not isinstance(value,str):raise ValueError(f'Hint {index}: both fields must be text.')
            if any(unicodedata.category(c).startswith('C') for c in value):raise ValueError(f'Hint {index}: keep the pronunciation on one line without control characters.')
            value=value.strip()
            if not 1<=len(value)<=limit or not any(c.isalpha() for c in value):raise ValueError(f'Hint {index}: {field} needs 1–{limit} characters including a letter.')
            if any(c in '<>{}[]' for c in value):raise ValueError(f'Hint {index}: use a simple spelling or phonetic pronunciation, without markup.')
            values.append(value)
        key=unicodedata.normalize('NFC',values[0]).casefold()
        if key in seen:raise ValueError('Each word can have only one pronunciation hint: '+values[0])
        seen.add(key);result.append(dict(zip(('word','say_as'),values)))
    return result


def profile(mid):
    from app.narration import _digest
    value=storage.read_json(profile_path(mid),{'items':[]})
    if not isinstance(value,dict) or not isinstance(value.get('items'),list):
        raise ValueError('Saved pronunciation hints are invalid. Restore pronunciation.json from a backup; it was not reset.')
    items=normalize(value['items'])
    return {'items':items,'revision':_digest(items)}


def save_profile(mid,revision,items):
    from app.tasks import lock,ensure_idle
    with lock:
        ensure_idle();current=profile(mid)
        if revision!=current['revision']:raise FileExistsError('Pronunciation hints changed in another tab. Reload them before saving.')
        rows=normalize(items)
        storage.write_json(profile_path(mid),{'version':1,'items':rows})
        return profile(mid)


def matching(text,rows):
    return sorted([row for row in normalize(rows) if re.search(r'(?<!\w)'+re.escape(row['word'])+r'(?!\w)',text,re.IGNORECASE)],key=lambda r:unicodedata.normalize('NFC',r['word']).casefold())


def style(base,text,rows):
    selected=matching(text,rows)
    if not selected:return base
    import json
    return base+'\nPronunciation guidance only: use these pronunciations when their exact words occur in the transcript. Do not speak this guidance, add explanations, substitute written words or change any transcript content. Keep pronunciations consistent.\n'+json.dumps(selected,ensure_ascii=False)


def edges(text,count=28):
    from app.alignment import script_tokens
    tokens=script_tokens(text)
    if not tokens:return {'opening':'','ending':''}
    return {'opening':text[:tokens[min(count,len(tokens))-1][2]],'ending':text[tokens[max(0,len(tokens)-count)][1]:]}


def analyze(path,text,timing=None,speed=1.):
    """Signal/timing heuristics, not independent transcription or factual verification."""
    warnings=[]
    def warn(code,message,start=None):
        item={'code':code,'message':message}
        if start is not None:item['start']=round(max(0,start),2)
        warnings.append(item)
    try:
        with wave.open(str(path),'rb') as audio:
            rate=audio.getframerate();width=audio.getsampwidth();channels=audio.getnchannels()
            if width not in (1,2,4) or audio.getcomptype()!='NONE' or not 8000<=rate<=192000 or not 1<=channels<=8:
                raise ValueError('This WAV format is not supported by the bounded local signal checker.')
            declared=audio.getnframes();window=max(1,rate//50);window_samples=window*channels
            total=clipped=quiet_frames=read_frames=0;run=longest=leading=elapsed=longest_start=0.;started=False;first_clip=None
            while raw:=audio.readframes(window*100):
                dtype={1:'u1',2:'<i2',4:'<i4'}[width]
                samples=np.frombuffer(raw,dtype=dtype).astype(np.float32)
                samples=(samples-128)/128 if width==1 else samples/(2**(width*8-1))
                if len(samples)%channels:raise ValueError('The WAV has an incomplete audio frame.')
                hits=np.flatnonzero(np.abs(samples)>=.995)
                if len(hits) and first_clip is None:first_clip=read_frames/rate+int(hits[0])/channels/rate
                read_frames+=len(samples)//channels;total+=len(samples);clipped+=len(hits)
                full=len(samples)//window_samples
                rms=list(np.sqrt(np.mean(samples[:full*window_samples].reshape(full,window_samples)**2,axis=1))) if full else []
                lengths=[window/rate]*full
                rest=samples[full*window_samples:]
                if len(rest):rms.append(float(np.sqrt(np.mean(rest**2))));lengths.append(len(rest)/channels/rate)
                for value,seconds in zip(rms,lengths):
                    if value<.002:
                        run+=seconds;quiet_frames+=seconds*rate
                        if not started:leading+=seconds
                    else:started=True;run=0.
                    elapsed+=seconds
                    if run>longest:longest=run;longest_start=elapsed-run
            if read_frames!=declared or not total:raise ValueError('The WAV is empty or shorter than its header declares.')
        duration=read_frames/rate;words=len(text.split());wpm=words*60/duration;normal_wpm=wpm/max(.1,speed)
        if normal_wpm>240:warn('fast_delivery',f'About {normal_wpm:.0f} words/min before your playback speed adjustment. Listen for rushed or skipped words.')
        if words>=25 and normal_wpm<80:warn('slow_delivery',f'About {normal_wpm:.0f} words/min before your playback speed adjustment. Listen for long pauses or repeated delivery.')
        if clipped/max(1,total)>.005:warn('clipping',f'{clipped/total:.1%} of samples are near the recording limit. Listen for distortion.',first_clip)
        if leading>1.5:warn('leading_quiet',f'About {leading:.1f}s of quiet audio at the start.',0)
        if run>1.5:warn('trailing_quiet',f'About {run:.1f}s of quiet audio at the end; check the next section’s join.',duration-run)
        if longest>3.:warn('long_quiet',f'A quiet stretch lasts about {longest:.1f}s. Check for an unintended gap; a deliberate pause may be fine.',longest_start)
        if duration>10 and quiet_frames/read_frames>.4:warn('mostly_quiet',f'About {quiet_frames/read_frames:.0%} of this recording is very quiet. Check that the full script is audible.')
        weak=[]
        if timing:
            weak=[w.get('text','') for w in timing if isinstance(w.get('confidence'),(int,float)) and math.isfinite(w['confidence']) and w['confidence']< -4.5]
            if weak:warn('weak_timing','Local timing was less certain for '+', '.join(weak[:8])+('.' if len(weak)<=8 else f' and {len(weak)-8} other words.')+' Listen and check nearby panel switches.',next((w.get('start',0) for w in timing if w.get('text')==weak[0]),0))
        return {'version':VERSION,'checked':True,'duration':round(duration,3),'words_per_minute':round(wpm,1),
                'quiet_seconds':round(quiet_frames/rate,2),'longest_quiet_seconds':round(longest,2),
                'warnings':warnings,'note':'Signal and timing checks cannot verify every spoken word, pronunciation, voice consistency or story accuracy. Listen before publishing.'}
    except (OSError,ValueError,wave.Error,EOFError) as exc:
        return {'version':VERSION,'checked':False,'warnings':[{'code':'not_checked','message':'Local signal check could not complete: '+str(exc)+'. The recording is kept; listen manually.'}],
                'note':'No speech was generated, changed or resent by this check.'}
