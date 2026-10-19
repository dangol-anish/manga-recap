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

from PIL import Image, ImageOps
from app import audio, narration, review, storage

WIDTH, HEIGHT, FPS = 1280, 720, 24
lock = threading.RLock()
jobs = {}


def root(mid):
    return storage.OUTPUTS / mid / 'Chapter 1' / 'stage5'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


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
    result = []
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
            item['panels'].append({'id':pid, 'revision':revision, 'url':url, '_crop':str(crop)})
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
    render_token = digest([token,[b['switches'] for b in current],WIDTH,HEIGHT,FPS])
    return {'source_token':token,'render_token':render_token,'beats':current,
            'duration':sum(b['duration'] for b in current), 'timing_reset':bool(saved and not valid)}


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
        job = dict(jobs.get(mid,{'running':False,'message':'Ready to render Chapter 1.'}))
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
            for p in b['panels']:p.pop('_crop',None)
        return state


def run(mid, chapter, state):
    started = time.monotonic()
    try:
        root(mid).mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='render-',dir=root(mid)) as name:
            work = Path(name)
            lines=['ffconcat version 1.0']
            with wave.open(str(work/'narration.wav'),'wb') as track:
                track.setparams((1,2,24000,0,'NONE','not compressed'))
                index=0
                for beat in state['beats']:
                    data = pcm(beat['_audio']) if beat['_audio'] else b'\0\0'*round(beat['duration']*24000)
                    track.writeframes(data)
                    bounds=[0,*beat['switches'],beat['duration']]
                    for i,panel in enumerate(beat['panels']):
                        frame=work/f'frame-{index:04}.png'
                        with Image.open(panel['_crop']) as image:
                            fitted=ImageOps.contain(image.convert('RGB'),(WIDTH,HEIGHT),Image.Resampling.LANCZOS)
                            canvas=Image.new('RGB',(WIDTH,HEIGHT),'black')
                            canvas.paste(fitted,((WIDTH-fitted.width)//2,(HEIGHT-fitted.height)//2));canvas.save(frame)
                        lines.extend([f"file '{frame.name}'",f'duration {bounds[i+1]-bounds[i]:.9f}'])
                        index+=1
            lines.append(f"file 'frame-{index-1:04}.png'")
            (work/'panels.ffconcat').write_text('\n'.join(lines)+'\n')
            with lock:
                jobs[mid].update(message='Rendering MP4…',progress_path=str(work/'progress.txt'))
            with open(work/'ffmpeg.log','w') as log:
                result = subprocess.run(['ffmpeg','-nostdin','-hide_banner','-loglevel','error','-y',
                    '-f','concat','-safe','1','-i',str(work/'panels.ffconcat'),'-i',str(work/'narration.wav'),
                    '-map','0:v:0','-map','1:a:0','-vf',f'fps={FPS},tpad=stop_mode=clone:stop_duration=2','-r',str(FPS),'-fps_mode','cfr','-c:v','libx264',
                    '-preset','veryfast','-tune','stillimage','-crf','21','-threads','2','-filter_threads','1',
                    '-pix_fmt','yuv420p','-c:a','aac','-b:a','128k','-t',str(state['duration']),
                    '-movflags','+faststart','-progress',str(work/'progress.txt'),str(work/'chapter.mp4')],
                    stdout=subprocess.DEVNULL,stderr=log,timeout=3600)
            if result.returncode:raise ValueError('FFmpeg export failed: '+(work/'ffmpeg.log').read_text()[-600:])
            probe=subprocess.run(['ffprobe','-v','error','-show_entries','stream=codec_name,codec_type,duration:format=duration',
                                  '-of','json',str(work/'chapter.mp4')],capture_output=True,text=True,check=True,timeout=30)
            info=json.loads(probe.stdout)
            if {s['codec_type'] for s in info['streams']}!={'video','audio'} or abs(float(info['format']['duration'])-state['duration'])>.15 or any(abs(float(s['duration'])-state['duration'])>.15 for s in info['streams']):
                raise ValueError('Rendered video failed audio/video duration validation.')
            with lock:
                if timeline(mid,chapter)['render_token']!=state['render_token']:
                    raise FileExistsError('Timing or source changed during render. Render the current version again.')
                (work/'chapter.mp4').replace(export_path(mid,state))
                jobs[mid].update(message=f'MP4 ready. Rendered in {time.monotonic()-started:.1f}s.',progress=1,error=False)
                storage.write_json(root(mid)/'export.json',{'render_token':state['render_token'],'duration':state['duration'],
                    'width':WIDTH,'height':HEIGHT,'fps':FPS,'panels':sum(len(b['panels']) for b in state['beats'])})
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
