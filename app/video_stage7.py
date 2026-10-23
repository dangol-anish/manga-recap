"""Rebuilt video timeline: Stage 4 crops + Stage 6 clips; no synthesis."""
import json
from pathlib import Path
import shutil
import tempfile
import threading
import time
from urllib.parse import quote,urlencode
import uuid
import wave

from fastapi import APIRouter,HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel,Field
from app import storage,video,speech_stage6 as speech,narration_stage5 as story,panel_preparation as panels

router=APIRouter(prefix='/api/cinema')
lock=threading.RLock()
active=None
worker=None
cancel=threading.Event()


def root(mid,chapter):
    path=story.root(mid,chapter)/'video-stage7'
    if path.is_symlink():raise ValueError('Unsafe video folder.')
    return path


def source(mid,chapter):
    spoken=speech.status(mid,chapter)
    if spoken['ready']!=spoken['spoken']:raise FileExistsError('Generate all current Stage 6 audio before making a video.')
    info,files,_=story.resolve(mid,chapter);ids=panels.page_ids(files);available={}
    for page in files:
        record=panels.safe_record(panels.record_path(info,ids[page.name]))
        directory=panels.record_path(info,record['page_id']).parent/record['revision']
        for panel in record['panels']:available[panel['id']]=(directory/'crops'/(panel['id']+'.png'),record['revision'])
    video_directory=root(mid,chapter);audio_directory=speech.root(mid,chapter);audio_clips=speech.manifest(mid,chapter)['clips']
    beats=[];ordinal=0
    for beat in spoken['beats']:
        clip=None if beat['silent'] else audio_clips[beat['token']]
        duration=max(2.0,len(beat['panel_ids'])/video.FPS) if beat['silent'] else beat['duration']
        values=[duration*i/len(beat['panel_ids']) for i in range(1,len(beat['panel_ids']))] if beat['silent'] else [a['start'] for a in beat['timing_anchors'][1:]]
        video.validate_switches(values,len(beat['panel_ids']),duration)
        item={'number':beat['number'],'narration':beat['narration'],'silent':beat['silent'],'token':beat['token'],
              'pacing':dict(spoken['pacing']),'audio_file':clip['file'] if clip else None,'duration':duration,'phrase_switches':values,'panels':[],
              '_audio':str(audio_directory/clip['file']) if clip else None,
              'audio_url':None if clip is None else f'/api/speech/{quote(mid)}/{quote(chapter)}/beats/{beat["number"]}/audio?'+urlencode({'token':beat['token'],'file':clip['file']})}
        for pid in beat['panel_ids']:
            if pid not in available:raise FileExistsError(f'{pid}: current prepared crop is missing.')
            crop,revision=available[pid]
            if crop.is_symlink() or not crop.resolve().is_relative_to(panels.root(info).resolve()) or not crop.is_file():raise ValueError('Unsafe or missing crop: '+pid)
            key=video.digest([pid,revision,speech.checksum(crop),video.VISUAL_STYLE])
            url=f'/api/cinema/{quote(mid)}/{quote(chapter)}/frame?'+urlencode({'panel_id':pid,'key':key})
            phrase=next((a['phrase'] for a in beat.get('anchors',[]) if a['panel_id']==pid),'')
            item['panels'].append({'id':pid,'revision':revision,'key':key,'frame_url':url,'phrase':phrase,
                                  'motion_kind':video.MOTION_STYLE['cycle'][ordinal%4],'_crop':str(crop),'_frame':str(video_directory/'frames'/(key+'.png'))})
            ordinal+=1
        beats.append(item)
    preview_url=(f'/api/speech/{quote(mid)}/{quote(chapter)}/full/audio?'+urlencode({'token':spoken['full']['token']})) if spoken['full']['ready'] and not any(b['silent'] for b in beats) else None
    token=video.digest([spoken['revision'],[(b['token'],b['audio_file'],b['duration'],b['phrase_switches'],[(p['id'],p['key']) for p in b['panels']]) for b in beats]])
    return beats,token,spoken['manga'],preview_url


def timeline(mid,chapter):
    beats,token,manga,preview=source(mid,chapter)
    saved=storage.read_json(root(mid,chapter)/'timing.json',{})
    valid=saved.get('source_token')==token
    for beat in beats:
        beat['switches']=saved.get('switches',{}).get(str(beat['number']),beat['phrase_switches']) if valid else beat['phrase_switches']
        video.validate_switches(beat['switches'],len(beat['panels']),beat['duration'])
    result={'manga':manga,'chapter':chapter,'source_token':token,'render_token':video.render_token(token,beats),'beats':beats,
            'preview_audio':preview or f'/api/cinema/{quote(mid)}/{quote(chapter)}/preview-audio?'+urlencode({'source_token':token}),'duration':sum(b['duration'] for b in beats),'timing_reset':bool(saved and not valid),'pacing':dict(beats[0]['pacing']),
            'motion_style':dict(video.MOTION_STYLE),'transition_style':dict(video.TRANSITION_STYLE),'fps':video.FPS,'width':video.WIDTH,'height':video.HEIGHT}
    # Validate actual rounded frame counts, including global beat boundaries.
    video.motion_plan(result)
    return result


def job_path(ident):
    if len(ident)!=32 or any(c not in '0123456789abcdef' for c in ident):raise ValueError('Invalid video job.')
    return storage.DATA/'cinema/jobs'/(ident+'.json')


def jobs():
    return sorted([storage.read_json(p) for p in (storage.DATA/'cinema/jobs').glob('*.json') if not p.is_symlink()],key=lambda j:j['created_at'],reverse=True)


def recover():
    for job in jobs():
        if job['status']=='running':
            job.update(status='interrupted',message='Video creation stopped. Your saved videos are safe. Click Create video to try again.')
            storage.write_json(job_path(job['id']),job)


def export_path(mid,chapter,token):return root(mid,chapter)/('chapter-'+token+'.mp4')


def status(mid,chapter):
    current=timeline(mid,chapter)
    with lock:
        saved=storage.read_json(root(mid,chapter)/'export.json',{})
        path=export_path(mid,chapter,current['render_token'])
        current['ready']=bool(saved.get('render_token')==current['render_token'] and path.is_file() and not path.is_symlink() and path.stat().st_size==saved.get('bytes'))
        job=next((dict(j) for j in jobs() if j['manga_id']==mid and j['chapter']==chapter),None)
        if job and active and active['id']==job['id'] and active.get('progress_path'):
            path=Path(active['progress_path'])
            if path.is_file():
                for line in path.read_text(encoding='utf-8').splitlines():
                    if line.startswith('out_time_us='):
                        try:job['progress']=min(.99,int(line.split('=')[1])/1e6/current['duration'])
                        except ValueError:pass
            job['elapsed_seconds']=time.time()-job['created_at']
        if job:job.pop('progress_path',None)
        current.update(job=job,busy=active is not None)
    for beat in current['beats']:
        beat.pop('_audio',None)
        for panel in beat['panels']:
            panel.pop('_crop',None);panel.pop('_frame',None)
    return current


def save(mid,chapter,body):
    from app.tasks import lock as task_lock,ensure_idle
    with task_lock,lock:
        ensure_idle();current=timeline(mid,chapter)
        if current['source_token']!=body.source_token or current['render_token']!=body.render_token:raise FileExistsError('Sources or timing changed in another tab. Refresh before saving.')
        beat=next((b for b in current['beats'] if b['number']==body.number),None)
        if not beat:raise ValueError('Beat not found.')
        values=video.validate_switches(body.switches,len(beat['panels']),beat['duration'])
        beat['switches']=values;video.motion_plan(current)
        value={'source_token':current['source_token'],'switches':{str(b['number']):b['switches'] for b in current['beats']}}
        path=root(mid,chapter)/'timing.json';old=storage.read_json(path)
        if old:storage.write_json(root(mid,chapter)/'timing-history'/(uuid.uuid4().hex+'.json'),old)
        storage.write_json(path,value)
    return status(mid,chapter)


def run(mid,chapter,snapshot,job):
    global active
    try:
        directory=root(mid,chapter);directory.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=directory,prefix='.render-') as name:
            work=Path(name)
            with lock:
                active['progress_path']=str(work/'progress.txt')
                job.update(message='Creating your video from the saved panels and audio…');storage.write_json(job_path(job['id']),job)
            video.render_to(snapshot,work,cancel_event=cancel)
            if cancel.is_set():raise InterruptedError('Render interrupted.')
            if timeline(mid,chapter)['render_token']!=snapshot['render_token']:raise FileExistsError('The audio or panels changed while creating the video. Click Create video again.')
            target=export_path(mid,chapter,snapshot['render_token'])
            if target.is_symlink():raise ValueError('Unsafe export file.')
            # Keep an existing valid same-version export safe if publishing fails.
            backup=None
            with lock:
                if target.exists():
                    backup=directory/('.previous-'+uuid.uuid4().hex+'.mp4');shutil.copyfile(target,backup)
                try:
                    (work/'chapter.mp4').replace(target)
                    storage.write_json(directory/'export.json',{'render_token':snapshot['render_token'],'duration':snapshot['duration'],
                        'width':video.WIDTH,'height':video.HEIGHT,'fps':video.FPS,'bytes':target.stat().st_size,
                        'visual_style':video.VISUAL_STYLE,'motion_style':video.MOTION_STYLE,'transition_style':video.TRANSITION_STYLE})
                except Exception:
                    if backup:backup.replace(target)
                    else:target.unlink(missing_ok=True)
                    raise
                finally:
                    if backup:backup.unlink(missing_ok=True)
        job.update(status='complete',progress=1,message='Your video is ready to watch and save.')
    except InterruptedError:job.update(status='interrupted',message='Video creation stopped. Your saved videos are safe.')
    except Exception as exc:job.update(status='failed',message=f'Could not create the video: {exc}. Your saved videos are safe.')
    finally:
        with lock:
            job.update(elapsed_seconds=time.time()-job['created_at']);job.pop('progress_path',None)
            try:storage.write_json(job_path(job['id']),job)
            finally:active=None


def start(mid,chapter,token):
    global active,worker
    from app.tasks import lock as task_lock,ensure_idle
    with task_lock,lock:
        ensure_idle()
        if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):raise ValueError('Video creation needs the free FFmpeg tools. Install FFmpeg and ffprobe, then try again.')
        current=timeline(mid,chapter)
        if current['render_token']!=token:raise FileExistsError('The audio, panels or timing changed. Refresh the page and click Create video again.')
        job={'id':uuid.uuid4().hex,'manga_id':mid,'chapter':chapter,'render_token':token,'status':'running','created_at':time.time(),'elapsed_seconds':0,'progress':0,'message':'Preparing video…'}
        storage.write_json(job_path(job['id']),job);cancel.clear();active=dict(job)
        worker=threading.Thread(target=run,args=(mid,chapter,current,job),daemon=True)
        try:worker.start()
        except Exception:
            active=None;job.update(status='failed',message='Could not start creating the video. Please try again.');storage.write_json(job_path(job['id']),job);raise
        return job


def shutdown():
    cancel.set()
    if worker and worker.is_alive():worker.join(70)


class Timing(BaseModel):
    source_token:str=Field(min_length=64,max_length=64)
    render_token:str=Field(min_length=64,max_length=64)
    number:int=Field(ge=1)
    switches:list

class Export(BaseModel):
    render_token:str=Field(min_length=64,max_length=64)

def error(exc):return HTTPException(409 if isinstance(exc,FileExistsError) else 400,str(exc))


@router.post('/open-root-folder')
def root_folder_api():
    try:
        from app.video_folders import sync
        from app.runtime import open_system_folder
        directory=sync()
        open_system_folder(directory)
        return {'opened':True}
    except (ValueError,OSError) as exc:raise error(exc)


@router.get('/catalog')
def catalog_api():
    groups=speech.catalog_api()
    for group in groups:
        for chapter in group['chapters']:
            chapter['video_ready']=False
            if chapter['audio_ready']:
                try:
                    s=speech.status(group['id'],chapter['name']);chapter['video_ready']=s['ready']==s['spoken']
                except (ValueError,OSError):pass
    return groups


@router.get('/{mid}/{chapter}')
def status_api(mid:str,chapter:str):
    try:return status(mid,chapter)
    except (ValueError,OSError) as exc:raise error(exc)


@router.post('/{mid}/{chapter}/timing')
def timing_api(mid:str,chapter:str,body:Timing):
    try:return save(mid,chapter,body)
    except (ValueError,OSError) as exc:raise error(exc)


@router.post('/{mid}/{chapter}/export')
def export_api(mid:str,chapter:str,body:Export):
    try:return start(mid,chapter,body.render_token)
    except (ValueError,OSError) as exc:raise error(exc)


@router.get('/{mid}/{chapter}/frame')
def frame_api(mid:str,chapter:str,panel_id:str,key:str):
    try:
        beats,_,_,_=source(mid,chapter)
        panel=next((p for b in beats for p in b['panels'] if p['id']==panel_id and p['key']==key),None)
        if not panel:raise FileExistsError('Panel or composition changed. Refresh the preview.')
        cache=Path(panel['_frame'])
        if cache.is_symlink() or cache.parent.is_symlink():raise ValueError('Unsafe frame cache.')
        path=video.ensure_frame(panel)
        return FileResponse(path,media_type='image/png',headers={'Cache-Control':'private, max-age=86400'})
    except (ValueError,OSError) as exc:raise error(exc)


@router.get('/{mid}/{chapter}/mp4')
def mp4_api(mid:str,chapter:str,token:str,download:bool=False):
    try:
        current=status(mid,chapter)
        if current['render_token']!=token or not current['ready']:raise FileExistsError('This video is unavailable or needs updating. Click Create video again.')
        return FileResponse(export_path(mid,chapter,token),media_type='video/mp4',filename=chapter+'.mp4' if download else None,headers={'Cache-Control':'no-store'})
    except (ValueError,OSError) as exc:raise error(exc)


@router.get('/{mid}/{chapter}/preview-audio')
def preview_audio_api(mid:str,chapter:str,source_token:str):
    try:
        current=timeline(mid,chapter)
        if current['source_token']!=source_token:raise FileExistsError('Preview audio changed. Refresh the chapter.')
        directory=root(mid,chapter)/'preview-audio';directory.mkdir(parents=True,exist_ok=True)
        target=directory/(source_token+'.wav')
        if target.is_symlink() or directory.is_symlink():raise ValueError('Unsafe preview audio path.')
        if not target.is_file():
            with tempfile.TemporaryDirectory(dir=directory,prefix='.preview-') as name:
                temporary=Path(name)/'chapter.wav'
                with wave.open(str(temporary),'wb') as track:
                    track.setparams((1,2,24000,0,'NONE','not compressed'))
                    for beat in current['beats']:
                        track.writeframes(video.pcm(beat['_audio']) if beat['_audio'] else b'\0\0'*round(beat['duration']*24000))
                if timeline(mid,chapter)['source_token']!=source_token:raise FileExistsError('Audio changed while preparing preview. Refresh.')
                temporary.replace(target)
        return FileResponse(target,media_type='audio/wav',headers={'Cache-Control':'no-store'})
    except (ValueError,OSError) as exc:raise error(exc)
