"""Read-only Shorts story, caption and pacing inspection. Never synthesizes media."""
import math
from app import alignment, shorts_render


def review(project, short):
    from app import shorts
    shorts.fresh(project)
    speed = project.get('pacing', {}).get('speed', 1.0)
    if type(speed) not in (int,float) or not math.isfinite(speed) or speed <= 0:
        raise ValueError('This Shorts project has invalid saved voice speed. Restore its project settings; existing media is unchanged.')
    if not short.get('segments'):raise ValueError('This saved short has no script segments. Restore its earlier project revision; existing media is unchanged.')
    rows=[]; elapsed=0; all_measured=True; notices=[]; total_words=0
    for index, segment in enumerate(short['segments']):
        words=alignment.script_tokens(segment['narration']); total_words+=len(words)
        clip=short.get('clips',{}).get(str(index),{})
        measured=shorts.clip_ready(project['id'],clip,shorts.clip_token(project,short,index))
        if measured and [w.get('text') for w in clip['words']] != [segment['narration'][a:b] for _,a,b in words]: measured=False
        all_measured &= measured
        duration=clip['duration'] if measured else len(words)/(2.4*speed)
        timing=clip['words'] if measured else [{'text':segment['narration'][a:b], 'start':i*duration/len(words), 'end':(i+1)*duration/len(words)} for i,(_,a,b) in enumerate(words)]
        chapter=next(c for c in project['source']['chapters'] if c['chapter']==segment['chapter'])
        beat=chapter['beats'][segment['source_beat']-1]
        cues=shorts_render.caption_groups(timing,elapsed,elapsed+duration)
        row={**segment,'number':index+1,'start':elapsed,'end':elapsed+duration,'duration':duration,'measured':measured,
            'source_narration':beat['narration'],'source_unsure':beat.get('unsure',''),
            'captions':cues, 'panel_url':f'/api/shorts/projects/{project["id"]}/shorts/{short["id"]}/panel/{index}'}
        rows.append(row);elapsed+=duration
        if duration<.7 or duration>6:
            notices.append({'segment':index+1,'text':f'{"Measured" if measured else "Estimated"} panel hold is {duration:.1f}s; review {"the quick flash" if duration<.7 else "whether this hold feels slow"}.'})
        if beat.get('unsure'):
            notices.append({'segment':index+1,'text':'The original narration records uncertainty. Check this claim against its manga pages.'})
        if index and (segment['chapter'],segment['panel_id'])==(short['segments'][index-1]['chapter'],short['segments'][index-1]['panel_id']):
            notices.append({'segment':index+1,'text':'This continues on the same panel as the previous segment; there is no new visual at this boundary.'})
    if elapsed<30 or elapsed>120:
        notices.insert(0,{'segment':None,'text':f'{"Measured" if all_measured else "Estimated"} length is {elapsed:.1f}s, outside 30–120s. '+('Edit the script before creating it again; saved recordings stay safe.' if all_measured else 'This is only an estimate; the generated voice determines the final length.')})
    return {'project_id':project['id'],'short_id':short['id'],'revision':project['revision'],'title':short['title'],
        'opening':rows[0]['narration'],'ending':rows[-1]['narration'],'word_count':total_words,'duration':elapsed,
        'all_measured':bool(all_measured),'measured_segments':sum(r['measured'] for r in rows),'speed':speed,
        'estimated_range':None if all_measured else [total_words/(3*speed),total_words/(2*speed)],
        'segments':rows,'notices':notices,
        'guidance':'This checks saved sources and timing, not factual accuracy or viral potential. Source narration can itself contain AI mistakes. Caption chunks use the video caption grouping rule; estimated timestamps and browser layout are not a rendered video.'}
