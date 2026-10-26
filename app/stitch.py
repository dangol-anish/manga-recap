"""Join selected finished chapter videos without changing their audio or pictures."""
from app import errors
import copy
import hashlib
import json
import math
import os
import re
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from functools import lru_cache
from fastapi import APIRouter,HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel,Field
from app import storage,video_folders
from app.audio_folders import publish
from app.chapter_names import chapter_sort_key
from app.runtime import open_system_folder
from app.speech_stage6 import checksum

router=APIRouter(prefix='/api/stitch')
lock=threading.RLock()
active=None
worker=None
cancel=threading.Event()


def jobs_root():
    directory=storage.DATA/'stitch/jobs'
    if directory.is_symlink() or directory.parent.is_symlink():raise ValueError('Unsafe video task folder.')
    return directory


def job_path(ident):
    if not isinstance(ident,str) or not re.fullmatch('[0-9a-f]{32}',ident):raise ValueError('Video task not found.')
    return jobs_root()/(ident+'.json')


def load(ident):
    path=job_path(ident)
    if path.is_symlink():raise ValueError('Unsafe video task.')
    job=storage.read_json(path)
    if not job:raise ValueError('Video task not found.')
    return job


def save(job):storage.write_json(job_path(job['id']),job)


def source_path(relative):
    path=storage.ROOT/relative
    base=storage.OUTPUTS/'Videos'
    if not path.resolve().is_relative_to(base.resolve()) or not base.resolve().is_relative_to(storage.ROOT.resolve()):raise ValueError('Choose a video from the saved Videos folder.')
    if any(p.is_symlink() for p in (path,*path.parents) if p.is_relative_to(storage.ROOT)):raise ValueError('Video shortcuts are not supported.')
    if not path.is_file() or path.suffix.lower()!='.mp4':raise ValueError('The selected video is missing.')
    return path


@lru_cache(maxsize=128)
def probe(path,digest):
    result=subprocess.run(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(path)],capture_output=True,text=True,timeout=30, encoding='utf-8', errors='replace')
    errors.checked_process(result,'Read chapter video',path)
    data=json.loads(result.stdout);streams=data.get('streams',[])
    v=next((s for s in streams if s['codec_type']=='video'),None);a=next((s for s in streams if s['codec_type']=='audio'),None)
    duration=float(data.get('format',{}).get('duration',0))
    if not v or not a or not math.isfinite(duration) or duration<=0:raise ValueError('Choose finished videos with both picture and audio.')
    signature=[v.get(k) for k in ('codec_name','profile','width','height','pix_fmt','time_base','r_frame_rate','extradata_size')]+[a.get(k) for k in ('codec_name','profile','sample_rate','channels','channel_layout','time_base')]
    return {'duration':duration,'signature':signature}


def catalog():
    root=video_folders.sync();index=storage.read_json(storage.DATA/'video-folders.json',{'manga':{}});groups=[]
    for mid,entry in index['manga'].items():
        folder=video_folders.folder(root,entry['folder']);videos=[]
        for path in sorted(folder.glob('*-Video*.mp4'),key=lambda p:chapter_sort_key(p.name)):
            if path.is_symlink():continue
            relative=path.relative_to(storage.ROOT).as_posix();source_path(relative);digest=checksum(path)
            try:metadata=probe(str(path),digest)
            except (ValueError,OSError,subprocess.SubprocessError) as exc:
                errors.warning(exc,'stitch','Read finished chapter video',path);continue
            ident=hashlib.sha256((mid+'/'+path.name+'/'+digest).encode()).hexdigest()[:32]
            videos.append({'id':ident,'filename':path.name,'relative':relative,'sha256':digest,**metadata})
        if videos:groups.append({'id':mid,'name':folder.name,'folder':folder.relative_to(storage.ROOT).as_posix(),'videos':videos})
    return groups


def validate(items):
    for item in items:
        path=source_path(item['relative'])
        if checksum(path)!=item['sha256']:raise FileExistsError(item['filename']+' changed. Refresh the video list before combining.')
        if probe(str(path),item['sha256'])['signature']!=items[0]['signature']:raise ValueError('These videos have different video or audio settings. Create them with the same settings on the Video page before combining.')


def recover():
    for path,job in errors.recovery_records(jobs_root(),'stitch'):
        if job['status'] in ('running','stopping'):
            job.update(status='paused',message='Stopped when the app closed. Your chapter videos are safe. Try again when ready.');save(job)


def result_path(job):
    path=source_path(job['output'])
    if checksum(path)!=job['output_sha256']:raise FileExistsError('The combined video was moved or changed. Combine the chapters again.')
    return path


def status():
    with lock:
        paths=sorted((p for p in jobs_root().glob('*.json') if not p.is_symlink()),key=lambda p:p.stat().st_mtime,reverse=True)
        job=copy.deepcopy(load(active['id'])) if active else storage.read_json(paths[0]) if paths else None
        if job and job['status']=='complete':
            try:result_path(job)
            except (ValueError,OSError) as exc:job.update(status='unavailable',message=str(exc))
        return {'busy':active is not None,'job':job}


def run(job):
    global active
    process=None
    try:
        validate(job['items']);folder=video_folders.folder(storage.OUTPUTS/'Videos',job['manga_folder'])
        with tempfile.TemporaryDirectory(dir=folder,prefix='.stitch-') as name:
            work=Path(name);lines=[]
            for i,item in enumerate(job['items']):
                source=source_path(item['relative']);target=work/f'part{i:04d}.mp4'
                try:os.link(source,target)
                except OSError:shutil.copyfile(source,target)
                lines.append("file '"+target.name+"'")
            (work/'chapters.txt').write_text('\n'.join(lines)+'\n', encoding='utf-8')
            output=work/'combined.mp4';progress=work/'progress.txt';total=sum(i['duration'] for i in job['items'])
            with (work/'error.log').open('w') as log:
                command=['ffmpeg','-nostdin','-hide_banner','-loglevel','error','-f','concat','-safe','1','-i',str(work/'chapters.txt'),'-map','0:v:0','-map','0:a:0','-c','copy','-movflags','+faststart','-avoid_negative_ts','make_zero','-progress',str(progress),'-nostats','-y',str(output)]
                if cancel.is_set():raise InterruptedError()
                process=subprocess.Popen(command,stdout=subprocess.DEVNULL,stderr=log)
                while process.poll() is None:
                    if cancel.wait(.2):
                        process.terminate()
                        try:process.wait(5)
                        except subprocess.TimeoutExpired:process.kill();process.wait()
                        raise InterruptedError()
                    if progress.is_file():
                        for line in progress.read_text(encoding='utf-8').splitlines():
                            if line.startswith('out_time_us='):
                                try:job['progress']=min(.98,int(line.split('=')[1])/1e6/total)
                                except ValueError:pass
                        with lock:save(job)
                if process.returncode:raise ValueError('Could not combine the videos: '+(work/'error.log').read_text(encoding='utf-8')[-400:])
            if cancel.is_set():raise InterruptedError()
            metadata=probe(str(output),checksum(output))
            if abs(metadata['duration']-total)>max(.5,len(job['items'])*.2):raise ValueError('The combined video length did not match the selected chapters.')
            validate(job['items'])
            if cancel.is_set():raise InterruptedError()
            target=publish(output,folder,'Combined video',checksum(output),{},extension='.mp4')
            job.update(status='complete',progress=1,message='Your combined video is ready.',output=target.relative_to(storage.ROOT).as_posix(),output_sha256=checksum(target),duration=metadata['duration'])
    except InterruptedError:job.update(status='paused',message='Stopped. Your chapter videos are unchanged. Try again when ready.')
    except Exception as exc:
        job['status']='failed'; errors.failure(job, exc, 'stitch', 'Combine selected videos', job.get('manga_folder'))
    finally:
        if process and process.poll() is None:
            process.terminate()
            try:process.wait(5)
            except subprocess.TimeoutExpired:process.kill();process.wait()
        with lock:
            try:errors.save_final(save,job,'stitch')
            finally:active=None


def launch(job):
    global active,worker
    cancel.clear();job.update(status='running',progress=0,message='Combining your selected chapter videos…');save(job);active=job
    worker=threading.Thread(target=run,args=(job,),daemon=True)
    try:worker.start()
    except Exception:
        active=None;job.update(status='failed',message='Could not start. Please try again.');save(job);raise
    return copy.deepcopy(job)


def start(mid,ids):
    from app.tasks import lock as task_lock,ensure_idle
    with task_lock,lock:
        ensure_idle()
        if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):raise ValueError('Install the free FFmpeg tools before combining videos.')
        if len(ids)!=len(set(ids)):raise ValueError('Select each video once.')
        group=next((g for g in catalog() if g['id']==mid),None)
        if not group:raise ValueError('Choose a manga with saved chapter videos.')
        items=[item for item in group['videos'] if item['id'] in ids]
        if len(items)!=len(ids) or len(items)<2:raise FileExistsError('Some selected videos changed or are missing. Refresh and select at least two videos.')
        validate(items)
        return launch({'id':uuid.uuid4().hex,'created_at':time.time(),'manga_id':mid,'manga_folder':Path(group['folder']).name,'items':items})


def stop(ident):
    with lock:
        if not active or active['id']!=ident:raise ValueError('This video is not being combined.')
        cancel.set();active.update(status='stopping',message='Stopping safely…');save(active);return copy.deepcopy(active)


def retry(ident):
    from app.tasks import lock as task_lock,ensure_idle
    with task_lock,lock:
        ensure_idle();job=load(ident)
        if job['status'] not in ('paused','failed'):raise ValueError('This task cannot be restarted.')
        validate(job['items']);return launch(job)


def shutdown():
    cancel.set()
    if worker and worker.is_alive():worker.join(20)


class Selection(BaseModel):
    manga_id:str=Field(min_length=1,max_length=128)
    video_ids:list[str]=Field(min_length=2,max_length=500)


def error(exc):return HTTPException(409 if isinstance(exc,FileExistsError) else 400,str(exc))


@router.get('/catalog')
def catalog_api():
    try:return catalog()
    except (ValueError,OSError,subprocess.SubprocessError) as exc:raise error(exc)


@router.get('/status')
def status_api():
    try:return status()
    except (ValueError,OSError) as exc:raise error(exc)


@router.post('/start')
def start_api(body:Selection):
    try:return start(body.manga_id,body.video_ids)
    except (ValueError,OSError,subprocess.SubprocessError) as exc:raise error(exc)


@router.post('/open-folder')
def folder_api():
    try:open_system_folder(video_folders.sync());return {'opened':True}
    except (ValueError,OSError) as exc:raise error(exc)


@router.post('/jobs/{ident}/{action}')
def action_api(ident:str,action:str):
    try:
        if action=='stop':return stop(ident)
        if action=='retry':return retry(ident)
        if action=='open-folder':open_system_folder(result_path(load(ident)).parent);return {'opened':True}
        raise ValueError('Unknown action.')
    except (ValueError,OSError) as exc:raise error(exc)


@router.get('/jobs/{ident}/video')
def video_api(ident:str,download:bool=False):
    try:
        job=load(ident)
        if job['status']!='complete':raise ValueError('Wait for the combined video to finish.')
        path=result_path(job)
        return FileResponse(path,media_type='video/mp4',filename=path.name if download else None,headers={'Cache-Control':'no-store'})
    except (ValueError,OSError) as exc:raise error(exc)
