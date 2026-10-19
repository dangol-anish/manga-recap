"""Chapter 1 timing and local MP4 export from existing crops and approved audio."""
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
import wave
from urllib.parse import quote, urlencode

from PIL import Image, ImageOps, ImageFilter, ImageEnhance, ImageDraw
from app import audio, narration, review, storage

WIDTH, HEIGHT, FPS = 1280, 720, 24
VISUAL_STYLE = {'version': 1, 'blur': 28, 'brightness': .48, 'foreground_scale': .92,
                'shadow_blur': 24, 'shadow_alpha': 190, 'shadow_y': 14, 'edge_alpha': 60}
MOTION_STYLE = {'version': 4, 'cycle': ['zoom_in','zoom_out','slide_from_left','slide_from_right'],
                'zoom_max': .065, 'zoom_per_second': .014, 'slide_max': .038,
                'slide_per_second': .009, 'sampling': 'fractional_affine_cubic'}
TRANSITION_STYLE = {'version': 1, 'kind': 'crossfade', 'frames': 4}
lock = threading.RLock()
jobs = {}


def root(mid):
    return storage.OUTPUTS / mid / 'Chapter 1' / 'stage5'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def render_token(source_token, beats):
    return digest([source_token, [b['switches'] for b in beats], WIDTH, HEIGHT, FPS, VISUAL_STYLE, MOTION_STYLE, TRANSITION_STYLE])


def motion_amount(kind, duration):
    prefix='zoom' if kind.startswith('zoom') else 'slide'
    return min(MOTION_STYLE[prefix+'_max'], duration*MOTION_STYLE[prefix+'_per_second'])


def motion_plan(state):
    """Round cumulative cut positions, avoiding per-panel rounding drift."""
    result=[]; elapsed=0
    for beat in state['beats']:
        bounds=[0,*beat['switches'],beat['duration']]
        for i,panel in enumerate(beat['panels']):
            start=math.floor((elapsed+bounds[i])*FPS+.5)
            end=math.floor((elapsed+bounds[i+1])*FPS+.5)
            if end<=start:raise ValueError('A panel hold is too short to render.')
            kind=panel.get('motion_kind',MOTION_STYLE['cycle'][len(result)%4])
            if kind not in MOTION_STYLE['cycle']:raise ValueError('Unknown panel motion.')
            result.append({'panel':panel,'kind':kind,'start_frame':start,'frames':end-start,
                           'amount':motion_amount(kind,bounds[i+1]-bounds[i])})
        elapsed+=beat['duration']
    return result


def motion_filter(plan):
    """Repeat stills, then sample a fractional affine camera at each frame.

    Moving zoompan crop coordinates round to pixels (and chroma blocks), which
    makes gentle movement stall and even reverse. Identity repetition avoids
    that crop; perspective performs the actual subpixel sampling.
    """
    def choose(values, variable, boundaries):
        def tree(lo, hi):
            if hi-lo == 1:return values[lo]
            mid=(lo+hi)//2
            return f'if(lt({variable},{boundaries[mid]}),{tree(lo,mid)},{tree(mid,hi)})'
        return tree(0,len(values))
    frames=choose([str(p['frames']) for p in plan], 'in', list(range(len(plan))))
    corners=[[] for _ in range(8)]
    for row in plan:
        amount=row['amount']; kind=row['kind']
        def points(progress):
            z=1+amount*(progress if kind=='zoom_in' else 1-progress if kind=='zoom_out' else 1)
            dx=WIDTH*amount*(progress-.5)*(1 if kind=='slide_from_left' else -1) if kind.startswith('slide') else 0
            x=WIDTH*(1-z)/2+dx; y=HEIGHT*(1-z)/2
            return [x,y,x+WIDTH*z,y,x,y+HEIGHT*z,x+WIDTH*z,y+HEIGHT*z]
        begin,end=points(0),points(1)
        for i,(a,b) in enumerate(zip(begin,end)):
            slope=(b-a)/max(1,row['frames']-1)
            intercept=a-slope*row['start_frame']
            corners[i].append(f'({intercept:.12g}+{slope:.12g}*(in-1))')
    # perspective counts incoming frames from 1; zoompan still indices start at 0.
    starts=[row['start_frame'] for row in plan]
    coordinates=':'.join(f"{key}='{choose(values,'in-1',starts)}'" for key,values in zip(
        ('x0','y0','x1','y1','x2','y2','x3','y3'),corners))
    return (f"zoompan=z=1:x=0:y=0:d='{frames}':s={WIDTH}x{HEIGHT}:fps={FPS},"
            f"format=yuv444p,perspective={coordinates}:sense=destination:eval=frame:interpolation=cubic")


def transition_plan(plan):
    """Dissolve after each saved cut, without shortening or shifting any hold."""
    return [{'start_frame':row['start_frame'],
             'frames':min(TRANSITION_STYLE['frames'],row['frames']-1,previous['frames']//2)}
            for previous,row in zip(plan,plan[1:])]


def render_filter(plan):
    groups=[]
    transitions=transition_plan(plan)
    for delay in range(1,TRANSITION_STYLE['frames']+1):
        for length in range(delay,TRANSITION_STYLE['frames']+1):
            targets=[r['start_frame']+delay-1 for r in transitions if r['frames']==length]
            if targets:groups.append((delay,length,targets))
    if not groups:return '[0:v]'+motion_filter(plan)+'[visual]'
    labels=''.join(f'[history{i}]' for i in range(len(groups)))
    graph=[f'[0:v]{motion_filter(plan)},split={len(groups)+1}[base]{labels}']
    def balanced(terms):
        if len(terms)==1:return terms[0]
        mid=len(terms)//2
        return '('+balanced(terms[:mid])+'+'+balanced(terms[mid:])+')'
    previous='base'
    for i,(delay,length,targets) in enumerate(groups):
        reference=f'delayed{i}';output='visual' if i==len(groups)-1 else f'mixed{i}'
        graph.append(f'[history{i}]tpad=start={delay}:start_mode=clone[{reference}]')
        enable=balanced([f'eq(n,{frame})' for frame in targets])
        # Each delayed input supplies exactly the last outgoing frame during
        # its dissolve phase. Outside those frames blend bypasses processing.
        opacity=delay/(length+1)
        graph.append(f"[{previous}][{reference}]blend=all_mode=normal:all_opacity={opacity:.12g}:shortest=1:enable='{enable}'[{output}]")
        previous=output
    return ';'.join(graph)


def compose_frame(image):
    """One current panel, blurred to cover behind its sharp, unstretched center."""
    image = ImageOps.exif_transpose(image).convert('RGB')
    style = VISUAL_STYLE
    margin = style['blur'] * 2
    background = ImageOps.fit(image, (WIDTH+margin*2, HEIGHT+margin*2), Image.Resampling.LANCZOS)
    background = background.filter(ImageFilter.GaussianBlur(style['blur']))
    canvas = ImageEnhance.Brightness(background.crop((margin, margin, margin+WIDTH, margin+HEIGHT))).enhance(style['brightness'])
    fitted = ImageOps.contain(image, (round(WIDTH*style['foreground_scale']), round(HEIGHT*style['foreground_scale'])), Image.Resampling.LANCZOS)
    x, y = (WIDTH-fitted.width)//2, (HEIGHT-fitted.height)//2
    shadow = Image.new('L', (WIDTH, HEIGHT))
    ImageDraw.Draw(shadow).rectangle((x, y+style['shadow_y'], x+fitted.width, y+fitted.height+style['shadow_y']), fill=style['shadow_alpha'])
    canvas.paste((0, 0, 0), (0, 0, WIDTH, HEIGHT), shadow.filter(ImageFilter.GaussianBlur(style['shadow_blur'])))
    edge = Image.new('L', (WIDTH, HEIGHT))
    ImageDraw.Draw(edge).rectangle((x-1, y-1, x+fitted.width, y+fitted.height), outline=style['edge_alpha'], width=1)
    canvas.paste((255, 255, 255), (0, 0, WIDTH, HEIGHT), edge)
    canvas.paste(fitted, (x, y))
    return canvas


def ensure_frame(panel, fallback=None):
    target = Path(panel.get('_frame') or fallback)
    if not target.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='frame-', dir=target.parent) as name:
            temporary = Path(name)/'frame.png'
            with Image.open(panel['_crop']) as image:
                compose_frame(image).save(temporary)
            temporary.replace(target)
    return target


def pcm(path):
    result = subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-i', str(path),
                             '-f', 's16le', '-ar', '24000', '-ac', '1', '-'], capture_output=True, timeout=60)
    if result.returncode:
        raise ValueError('Could not decode approved audio: ' + result.stderr.decode(errors='replace')[-300:])
    return result.stdout


@lru_cache(maxsize=128)
def decoded_duration(path, size, modified):
    # Actual decoded samples exclude MP3 padding, avoiding drift across beats.
    return len(pcm(path)) / 48000


def source(mid, chapter):
    state = audio.status(mid, chapter)  # Chapter 1 + fresh imported narration only.
    if state['ready'] != state['spoken']:
        raise FileExistsError('Generate all current Chapter 1 audio before making a video.')
    manga, pages = narration.context(mid, chapter)
    panels = {}
    for page in pages:
        record = storage.read_json(review.record_path(manga, chapter, page))
        if not record:
            raise FileNotFoundError('Saved panel crops are missing.')
        for panel in record['panels']:
            panels[panel['id']] = (page.name, record['revision'])
    result = []; panel_number=0
    for beat in state['beats']:
        item = {k: beat[k] for k in ('number', 'token', 'narration', 'panel_ids', 'revision', 'silent')}
        path = None if beat['silent'] else audio.asset(mid, chapter, beat['number'])
        item['duration'] = 2.0 if path is None else decoded_duration(str(path), path.stat().st_size, path.stat().st_mtime_ns)
        if item['duration'] <= len(beat['panel_ids'])/FPS:
            raise ValueError(f'Beat {beat["number"]} is too short to show all selected panels.')
        item['_audio'] = str(path) if path else None
        item['panels'] = []
        for pid in beat['panel_ids']:
            page, revision = panels[pid]
            crop = review.asset_path(mid, chapter, page, 'crop', revision, pid)
            if not crop.is_file():raise FileNotFoundError(f'Missing saved crop: {pid}')
            url = f'/api/review/{quote(mid)}/{quote(chapter)}/{quote(page)}/image?' + urlencode({'kind':'crop','revision':revision,'panel_id':pid})
            frame_key = digest([pid, revision, WIDTH, HEIGHT, VISUAL_STYLE])
            frame_url = f'/api/video/{quote(mid)}/{quote(chapter)}/frame?' + urlencode({'panel_id':pid,'revision':revision,'style':digest(VISUAL_STYLE)})
            item['panels'].append({'id':pid, 'revision':revision, 'url':url, 'frame_url':frame_url,
                                   'motion_kind':MOTION_STYLE['cycle'][panel_number%4],
                                   '_crop':str(crop), '_frame':str(root(mid)/'frames'/(frame_key+'.png'))})
            panel_number+=1
        result.append(item)
    token = digest([[b['token'], b['revision'], b['duration'], [(p['id'],p['revision']) for p in b['panels']]] for b in result])
    return result, token


def validate_switches(values, count, duration):
    if not isinstance(values,list) or len(values)!=count-1:
        raise ValueError(f'Expected {count-1} panel switch times.')
    if any(isinstance(v,bool) or not isinstance(v,(float,int)) or not math.isfinite(v) for v in values):
        raise ValueError('Switch times must be finite numbers.')
    boundaries=[0,*values,duration]
    if any(b-a < 1/FPS-1e-8 for a,b in zip(boundaries,boundaries[1:])):
        raise ValueError('Switch times must increase, stay inside the beat, and leave at least one video frame per panel.')
    return values


def timeline(mid, chapter):
    current, token = source(mid, chapter)
    saved = storage.read_json(root(mid)/'timing.json', {})
    valid = saved.get('source_token') == token
    for beat in current:
        n = len(beat['panels'])
        equal = [beat['duration']*i/n for i in range(1,n)]
        values = saved.get('switches',{}).get(str(beat['number']), equal) if valid else equal
        beat['switches'] = validate_switches(values,n,beat['duration'])
    return {'source_token':token,'render_token':render_token(token,current),'beats':current,
            'duration':sum(b['duration'] for b in current), 'timing_reset':bool(saved and not valid),
            'timing_mode':saved.get('mode','saved timing') if valid else 'equal timing', 'pacing':audio.pacing(mid),
            'motion_style':dict(MOTION_STYLE), 'transition_style':dict(TRANSITION_STYLE)}


def save(mid, chapter, token, number, values):
    with lock:
        state = timeline(mid,chapter)
        if state['source_token'] != token:raise FileExistsError('Narration, audio or panels changed. Reload before editing timing.')
        if not 1 <= number <= len(state['beats']):raise FileNotFoundError('Beat not found.')
        beat = state['beats'][number-1]
        validate_switches(values,len(beat['panels']),beat['duration'])
        switches = {str(b['number']):b['switches'] for b in state['beats']}
        switches[str(number)] = values
        storage.write_json(root(mid)/'timing.json',{'source_token':token,'switches':switches})
        return status(mid,chapter)


def export_path(mid, state):
    return root(mid)/('chapter-1-'+state['render_token']+'.mp4')


def status(mid, chapter):
    with lock:
        state = timeline(mid,chapter)
        state['ready'] = export_path(mid,state).is_file()
        job = dict(jobs.get(mid,{'running':False,'message':'MP4 ready.' if state['ready'] else 'Ready to render Chapter 1.'}))
        if job.get('running') and job.get('progress_path'):
            p = Path(job['progress_path'])
            if p.exists():
                for line in p.read_text().splitlines():
                    if line.startswith('out_time_us='):
                        try:job['progress']=min(.99,int(line.split('=')[1])/1e6/job['duration'])
                        except ValueError:pass
        job.pop('progress_path',None)
        state['job'] = job
        for b in state['beats']:
            b.pop('_audio',None)
            for p in b['panels']:
                p.pop('_crop',None);p.pop('_frame',None)
        return state


def render_to(state, work):
    """Render a supplied snapshot; publication remains the caller's responsibility."""
    lines=['ffconcat version 1.0']; plan=motion_plan(state)
    with wave.open(str(work/'narration.wav'),'wb') as track:
        track.setparams((1,2,24000,0,'NONE','not compressed'))
        for beat in state['beats']:
            data = pcm(beat['_audio']) if beat['_audio'] else b'\0\0'*round(beat['duration']*24000)
            track.writeframes(data)
    for index,entry in enumerate(plan):
        frame=work/f'frame-{index:04}.png'
        cached=ensure_frame(entry['panel'],frame)
        if cached!=frame:shutil.copyfile(cached,frame)
        lines.append(f"file '{frame.name}'")
    (work/'panels.ffconcat').write_text('\n'.join(lines)+'\n')
    with open(work/'ffmpeg.log','w') as log:
        result = subprocess.run(['ffmpeg','-nostdin','-hide_banner','-loglevel','error','-y',
            '-f','concat','-safe','1','-i',str(work/'panels.ffconcat'),'-i',str(work/'narration.wav'),
            '-filter_complex',render_filter(plan),'-map','[visual]','-map','1:a:0','-r',str(FPS),'-fps_mode','cfr','-c:v','libx264',
            '-preset','veryfast','-tune','stillimage','-crf','21','-threads','2','-filter_threads','1','-filter_complex_threads','1',
            '-pix_fmt','yuv420p','-c:a','aac','-b:a','128k','-t',str(state['duration']),
            '-movflags','+faststart','-progress',str(work/'progress.txt'),str(work/'chapter.mp4')],
            stdout=subprocess.DEVNULL,stderr=log,timeout=3600)
    if result.returncode:raise ValueError('FFmpeg export failed: '+(work/'ffmpeg.log').read_text()[-600:])
    probe=subprocess.run(['ffprobe','-v','error','-show_entries','stream=codec_name,codec_type,duration:format=duration',
                          '-of','json',str(work/'chapter.mp4')],capture_output=True,text=True,check=True,timeout=30)
    info=json.loads(probe.stdout)
    if {s['codec_type'] for s in info['streams']}!={'video','audio'} or abs(float(info['format']['duration'])-state['duration'])>.15 or any(abs(float(s['duration'])-state['duration'])>.15 for s in info['streams']):
        raise ValueError('Rendered video failed audio/video duration validation.')
    return work/'chapter.mp4'


def run(mid, chapter, state):
    started = time.monotonic()
    try:
        root(mid).mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='render-',dir=root(mid)) as name:
            work = Path(name)
            with lock:
                jobs[mid].update(message='Rendering MP4…',progress_path=str(work/'progress.txt'))
            render_to(state, work)
            with lock:
                if timeline(mid,chapter)['render_token']!=state['render_token']:
                    raise FileExistsError('Timing or source changed during render. Render the current version again.')
                (work/'chapter.mp4').replace(export_path(mid,state))
                jobs[mid].update(message=f'MP4 ready. Rendered in {time.monotonic()-started:.1f}s.',progress=1,error=False)
                storage.write_json(root(mid)/'export.json',{'render_token':state['render_token'],'duration':state['duration'],
                    'width':WIDTH,'height':HEIGHT,'fps':FPS,'panels':sum(len(b['panels']) for b in state['beats']),
                    'visual_style':VISUAL_STYLE,'motion_style':MOTION_STYLE,'transition_style':TRANSITION_STYLE})
    except Exception as exc:
        with lock:jobs[mid].update(message=f'Export stopped: {exc}',error=True)
    finally:
        with lock:
            jobs[mid]['running']=False
            jobs[mid].pop('progress_path',None)


def start(mid, chapter, token):
    with lock:
        if jobs.get(mid,{}).get('running'):raise FileExistsError('An export is already running.')
        if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):raise ValueError('FFmpeg and ffprobe are required.')
        state=timeline(mid,chapter)
        if state['render_token']!=token:raise FileExistsError('Timing changed. Reload and export the current version.')
        jobs[mid]={'running':True,'message':'Preparing panel images and audio…','progress':0,'duration':state['duration']}
        threading.Thread(target=run,args=(mid,chapter,state),daemon=True).start()
        return dict(jobs[mid])


def asset(mid,chapter,token):
    state=timeline(mid,chapter)
    if state['render_token']!=token:raise FileExistsError('This video is outdated. Render the current timing.')
    path=export_path(mid,state)
    if not path.is_file():raise FileNotFoundError('Render the MP4 first.')
    return path


def frame_asset(mid, chapter, panel_id, revision, style):
    current, _ = source(mid, chapter)  # Same Chapter 1 scope and selected crops as export.
    if style != digest(VISUAL_STYLE):raise FileExistsError('Video style changed. Reload the preview.')
    panel = next((p for b in current for p in b['panels'] if p['id']==panel_id and p['revision']==revision), None)
    if panel is None:raise FileNotFoundError('Current selected panel not found.')
    return ensure_frame(panel)
