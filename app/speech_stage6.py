"""Stage 6 speech from the rebuilt narration store; never reads legacy media."""
import math
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
import uuid

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from app import storage, narration_stage5 as story, panel_preparation as panels, audio, alignment
from app.runtime import open_system_folder

router=APIRouter(prefix='/api/speech')
VOICE=audio.DEFAULT_VOICE
PACING=dict(audio.DEFAULT_PACING)
PROFILE='stage6-word-boundaries-v1:'+audio.PAUSE_PROFILE
lock=threading.RLock()
active=None
worker=None
stop_event=threading.Event()


def root(mid,chapter):
    path=story.root(mid,chapter)/'audio-stage6'
    if path.is_symlink():raise ValueError('Unsafe audio folder.')
    return path


def checksum(path):
    from app.read_cache import file_hash
    return file_hash(path)


def source(mid,chapter):
    with story.lock,panels.lock:
        status=story.status(mid,chapter)
        if not status['batches'] or not all(b['imported'] for b in status['batches']):
            raise FileExistsError('Import all current Stage 5 batches before generating audio. Stale batches must be refreshed.')
        saved=story.load_state(mid)['chapters'][chapter]['batches'];beats=[];settings=pacing(mid,chapter);selected_voice=voice(mid,chapter)
        for batch in status['batches']:
            entry=saved[batch['id']]
            for item in entry['beats']:
                number=len(beats)+1
                token=story._digest([mid,chapter,number,batch['id'],batch['fingerprint'],batch['context_token'],item['panel_ids'],item['narration'],item.get('anchors',[]),selected_voice,settings,PROFILE])
                beats.append({**item,'number':number,'batch_id':batch['id'],'token':token,'pacing':dict(settings),'voice':selected_voice,'silent':not item['narration'].strip()})
        return {'manga':status['manga'],'chapter':chapter,'voice':selected_voice,'pacing':dict(settings),'beats':beats,'revision':story._digest([b['token'] for b in beats])}


def manifest(mid,chapter):
    path=root(mid,chapter)/'manifest.json'
    if path.is_symlink():raise ValueError('Unsafe audio manifest.')
    return storage.read_json(path,{'clips':{}})


def voice(mid,chapter):
    saved=manifest(mid,chapter)
    if saved.get('voice'):return saved['voice']
    # Older manifests store the actual voice per clip. Keep existing audio valid.
    existing=[c for c in saved.get('clips',{}).values() if c.get('voice')]
    if existing:return max(existing,key=lambda c:c.get('created_at',0))['voice']
    return VOICE


def pacing(mid,chapter):
    saved=manifest(mid,chapter)
    if saved.get('pacing'):return dict(saved['pacing'])
    # Pre-settings manifests already record their actual synthesis profile on
    # every clip. Preserve it when the default for new chapters changes.
    existing=[c for c in saved.get('clips',{}).values() if c.get('pacing')]
    if existing:return dict(max(existing,key=lambda c:c.get('created_at',0))['pacing'])
    return dict(PACING)


def clip_path(mid,chapter,clip):
    name=clip.get('file','')
    if not name or Path(name).name!=name or not name.endswith('.mp3'):return None
    directory=root(mid,chapter);path=directory/name
    if path.is_symlink() or not path.resolve().is_relative_to(directory.resolve()):return None
    return path if path.is_file() else None


def valid_clip(mid,chapter,beat,clip):
    path=clip_path(mid,chapter,clip)
    if path is None or clip.get('token')!=beat['token'] or clip.get('voice')!=beat['voice'] or clip.get('pacing')!=beat.get('pacing',PACING):return False
    duration=clip.get('duration',0)
    if not isinstance(duration,(int,float)) or not math.isfinite(duration) or duration<=0:return False
    words=clip.get('words');timed=clip.get('timing_anchors')
    if not isinstance(words,list) or not words or not isinstance(timed,list):return False
    if [a.get('panel_id') for a in timed]!=beat['panel_ids']:return False
    try:
        last=-1
        for anchor in timed:
            start=anchor['start'];end=anchor['end']
            if not math.isfinite(start) or not math.isfinite(end) or start<=last or not 0<=start<end<=duration+.001:return False
            last=start
        return checksum(path)==clip.get('sha256')
    except (ValueError,KeyError,TypeError,OSError):return False


def job_path(ident):
    if len(ident)!=32 or any(c not in '0123456789abcdef' for c in ident):raise ValueError('Invalid audio job.')
    return storage.DATA/'speech/jobs'/(ident+'.json')


def jobs():
    return sorted((storage.read_json(p) for p in (storage.DATA/'speech/jobs').glob('*.json') if not p.is_symlink()),key=lambda j:j['created_at'],reverse=True)


def save_job(job):storage.write_json(job_path(job['id']),job)


def recover():
    for job in jobs():
        if job['status'] in ('running','stopping'):
            job.update(status='paused',message='Interrupted. Completed clips are saved; resume when ready.')
            save_job(job)


def status(mid,chapter):
    current=source(mid,chapter)
    with lock:
        clips=manifest(mid,chapter)['clips']
        for beat in current['beats']:
            clip=clips.get(beat['token'],{});ready=not beat['silent'] and valid_clip(mid,chapter,beat,clip)
            beat.update(ready=ready,duration=clip['duration'] if ready else None,file=clip['file'] if ready else None,
                        timing_anchors=clip.get('timing_anchors',[]) if ready else [])
        job=next((j for j in jobs() if j['manga_id']==mid and j['chapter']==chapter),None)
        current.update(ready=sum(b['ready'] for b in current['beats']),spoken=sum(not b['silent'] for b in current['beats']),
                       duration=sum(b['duration'] or 0 for b in current['beats']),job=job,busy=active is not None)
    current['full']=full_status(mid,chapter,current)
    return current


def chapter_token(current):
    return story._digest(['pcm-concat-v1',current['revision'],[(b['token'],b['file']) for b in current['beats'] if not b['silent']]])


def full_status(mid,chapter,current):
    available=current['spoken']>0 and current['ready']==current['spoken']
    token=chapter_token(current) if available else None
    saved=storage.read_json(root(mid,chapter)/'chapter.json',{})
    path=clip_path(mid,chapter,saved)
    ready=bool(available and saved.get('token')==token and path and checksum(path)==saved.get('sha256'))
    return {'ready':ready,'available':available,'token':token,'file':saved.get('file') if ready else None,'duration':saved.get('duration') if ready else None}


def build_full(mid,chapter,revision):
    from mutagen.mp3 import MP3
    current=status(mid,chapter)
    if current['revision']!=revision:raise FileExistsError('Narration changed. Refresh before assembling full audio.')
    if not current['full']['available']:raise ValueError('Generate all spoken beats before assembling full audio.')
    if current['full']['ready']:return current['full']
    executable=shutil.which('ffmpeg')
    if not executable:raise ValueError('FFmpeg is required to assemble full audio.')
    directory=root(mid,chapter);token=current['full']['token'];cues=[];sample_count=0
    with tempfile.TemporaryDirectory(dir=directory,prefix='.chapter-') as temporary:
        work=Path(temporary);pcm=work/'chapter.pcm';encoded=work/'chapter.mp3'
        with pcm.open('wb') as output:
            for beat in current['beats']:
                if beat['silent']:continue
                path=clip_path(mid,chapter,{'file':beat['file']});before=output.tell()
                decoded=subprocess.run([executable,'-nostdin','-v','error','-i',str(path),'-f','s16le','-ac','1','-ar','24000','-'],stdout=output,stderr=subprocess.PIPE,timeout=60)
                if decoded.returncode:raise ValueError('Could not read beat '+str(beat['number'])+': '+decoded.stderr.decode(errors='replace')[-300:])
                count=(output.tell()-before)//2
                if count<=0:raise ValueError('Beat '+str(beat['number'])+' decoded to empty audio.')
                cues.append({'beat':beat['number'],'token':beat['token'],'file':beat['file'],'start':sample_count/24000,'end':(sample_count+count)/24000})
                sample_count+=count
        result=subprocess.run([executable,'-nostdin','-v','error','-f','s16le','-ac','1','-ar','24000','-i',str(pcm),'-threads','1','-codec:a','libmp3lame','-b:a','96k',str(encoded)],capture_output=True,timeout=120)
        if result.returncode:raise ValueError('Could not assemble chapter audio: '+result.stderr.decode(errors='replace')[-300:])
        duration=MP3(encoded).info.length
        if abs(duration-sample_count/24000)>.2:raise ValueError('Full audio duration did not match the saved beats.')
        latest=status(mid,chapter)
        if not latest['full']['available'] or latest['full']['token']!=token:raise FileExistsError('Beat audio changed during assembly. Retry the current version.')
        target=directory/('chapter-'+token+'-'+uuid.uuid4().hex+'.mp3')
        metadata={'token':token,'file':target.name,'duration':sample_count/24000,'sha256':checksum(encoded),'beats':cues,'source_revision':revision,'created_at':time.time()}
        with lock:
            encoded.replace(target)
            try:storage.write_json(directory/'chapter.json',metadata)
            except Exception:
                target.unlink(missing_ok=True);raise
    return {'ready':True,'available':True,'token':token,'file':target.name,'duration':sample_count/24000}


def generate_one(mid,chapter,beat,source_revision):
    from mutagen.mp3 import MP3
    directory=root(mid,chapter);directory.mkdir(parents=True,exist_ok=True)
    name=beat['token']+'-'+uuid.uuid4().hex
    temporary=directory/('.'+name+'.mp3');target=directory/(name+'.mp3')
    published=False
    try:
        settings=dict(beat['pacing'])
        spoken=alignment.speech(beat['narration'],temporary,settings,beat['voice'])
        errors,anchors=alignment.validate_map(beat,{'beat':beat['number'],'anchors':beat.get('anchors',[])})
        if errors:raise ValueError('; '.join(errors))
        timed=alignment.anchor_times(beat,anchors,spoken['words'],spoken['duration'])
        duration=MP3(temporary).info.length
        if not math.isfinite(duration) or duration<=0:raise ValueError('Speech returned empty audio.')
        if abs(duration-spoken['duration'])>.2:raise ValueError('Audio duration and speech timestamps do not match.')
        clip={'token':beat['token'],'file':target.name,'voice':beat['voice'],'pacing':settings,'profile':PROFILE,
              'duration':spoken['duration'],'mp3_duration':duration,'words':spoken['words'],'timing_anchors':timed,
              'removed_silence':spoken['removed_silence'],'sentence_pauses':spoken['sentence_pauses'],'sha256':checksum(temporary),
              'batch_id':beat['batch_id'],'source_revision':source_revision,'created_at':time.time()}
        if source(mid,chapter)['revision']!=source_revision:raise FileExistsError('Narration changed. Previous clips are retained; generate the current version.')
        with lock:
            temporary.replace(target);published=True
            saved=manifest(mid,chapter)
            old=saved['clips'].get(beat['token'])
            if old:saved.setdefault('history',{}).setdefault(beat['token'],[]).append(old)
            saved['clips'][beat['token']]=clip
            saved['pacing']=settings
            saved['voice']=beat['voice']
            storage.write_json(directory/'manifest.json',saved)
        print(f'Stage 6 {chapter} beat {beat["number"]}: {spoken["duration"]:.2f}s audio; {len(spoken["words"])} word timestamps',flush=True)
    except Exception:
        if published:target.unlink(missing_ok=True)
        raise
    finally:temporary.unlink(missing_ok=True)


def run(job):
    global active
    start=time.monotonic();base=job.get('elapsed_seconds',0)
    try:
        current=source(job['manga_id'],job['chapter'])
        if current['revision']!=job['source_revision']:raise FileExistsError('Narration changed. Generate missing audio for the current version instead.')
        for number in job['numbers']:
            if number in job['completed_numbers']:continue
            if stop_event.is_set():break
            beat=current['beats'][number-1]
            with lock:
                job.update(message=f'Generating beat {number}…',current_beat=number);save_job(job)
            clip=manifest(job['manga_id'],job['chapter'])['clips'].get(beat['token'],{})
            if job['regenerate'] or not valid_clip(job['manga_id'],job['chapter'],beat,clip):
                generate_one(job['manga_id'],job['chapter'],beat,current['revision'])
            with lock:
                job['completed_numbers'].append(number)
                job.update(elapsed_seconds=base+time.monotonic()-start);save_job(job)
        complete=len(job['completed_numbers'])==len(job['numbers'])
        if complete and not stop_event.is_set():
            current=status(job['manga_id'],job['chapter'])
            if current['full']['available']:
                with lock:
                    job.update(message='Assembling full chapter audio…');save_job(job)
                build_full(job['manga_id'],job['chapter'],current['revision'])
        job.update(status='complete' if complete else 'paused',message='Audio generation complete.' if complete else 'Stopped after the current beat. Completed clips are saved.')
    except Exception as exc:job.update(status='failed',message=f'{type(exc).__name__}: {exc}. Completed clips are saved; resume to retry.')
    finally:
        with lock:
            job.update(elapsed_seconds=base+time.monotonic()-start,current_beat=None)
            try:save_job(job)
            finally:active=None


def start(mid,chapter,revision,number=None,resume=None):
    global active,worker
    from app.tasks import lock as task_lock,ensure_idle
    with task_lock,lock:
        ensure_idle()
        current=status(mid,chapter)
        if current['revision']!=revision:raise FileExistsError('Narration changed. Refresh before generating audio.')
        if not shutil.which('ffmpeg'):raise ValueError('Install free FFmpeg and restart the app before generating audio.')
        if resume:
            job=storage.read_json(job_path(resume))
            if not job or job['manga_id']!=mid or job['chapter']!=chapter:raise ValueError('Audio job not found for this chapter.')
            if job['status'] not in ('paused','failed'):raise ValueError('This job cannot be resumed.')
            if job['source_revision']!=revision:raise FileExistsError('Narration changed since this job. Generate missing audio instead.')
            clips=manifest(mid,chapter)['clips']
            job['completed_numbers']=[n for n in job['completed_numbers'] if valid_clip(mid,chapter,current['beats'][n-1],clips.get(current['beats'][n-1]['token'],{}))]
        else:
            if number is not None and not 1<=number<=len(current['beats']):raise ValueError('Beat not found.')
            chosen=[b['number'] for b in current['beats'] if not b['silent'] and (b['number']==number if number is not None else not b['ready'])]
            if not chosen:raise ValueError('No missing spoken beats. Silent beats do not need audio; regenerate a spoken beat individually if needed.')
            job={'id':uuid.uuid4().hex,'manga_id':mid,'chapter':chapter,'source_revision':revision,'numbers':chosen,'completed_numbers':[],
                 'regenerate':number is not None,'created_at':time.time(),'elapsed_seconds':0}
        job.update(status='running',message='Starting Andrew speech…');save_job(job)
        stop_event.clear();active=job
        worker=threading.Thread(target=run,args=(job,),daemon=True)
        try:worker.start()
        except Exception:
            active=None;job.update(status='failed',message='Could not start audio worker. Retry.');save_job(job);raise
        return dict(job)


def stop(ident):
    with lock:
        if active is None or active['id']!=ident:raise ValueError('That audio job is not running.')
        stop_event.set();active.update(status='stopping',message='Stopping after the current beat…');save_job(active)
        return dict(active)


def shutdown():
    stop_event.set()
    if worker and worker.is_alive():worker.join(timeout=130)


class Request(BaseModel):
    revision:str=Field(min_length=64,max_length=64)


def error(exc):return HTTPException(409 if isinstance(exc,FileExistsError) else 400,str(exc))


@router.post('/open-root-folder')
def root_folder_api():
    try:
        from app.audio_folders import sync
        directory=sync()
        open_system_folder(directory)
        return {'opened':True}
    except (ValueError,OSError) as exc:raise error(exc)


@router.get('/catalog')
def catalog_api():
    groups=story.catalog_api()
    for group in groups:
        for chapter in group['chapters']:
            chapter['audio_ready']=False
            if chapter['ready']:
                try:
                    s=story.status(group['id'],chapter['name'])
                    chapter['audio_ready']=bool(s['batches'] and all(b['imported'] for b in s['batches']))
                except (ValueError,OSError):pass
    return groups


@router.get('/{mid}/{chapter}')
def status_api(mid:str,chapter:str):
    try:return status(mid,chapter)
    except (ValueError,OSError) as exc:raise error(exc)


@router.post('/{mid}/{chapter}/generate')
def generate_api(mid:str,chapter:str,body:Request):
    try:return start(mid,chapter,body.revision)
    except (ValueError,OSError) as exc:raise error(exc)


@router.post('/{mid}/{chapter}/beats/{number}/regenerate')
def regenerate_api(mid:str,chapter:str,number:int,body:Request):
    try:return start(mid,chapter,body.revision,number)
    except (ValueError,OSError) as exc:raise error(exc)


@router.post('/{mid}/{chapter}/jobs/{ident}/resume')
def resume_api(mid:str,chapter:str,ident:str,body:Request):
    try:return start(mid,chapter,body.revision,resume=ident)
    except (ValueError,OSError) as exc:raise error(exc)


@router.post('/jobs/{ident}/stop')
def stop_api(ident:str):
    try:return stop(ident)
    except (ValueError,OSError) as exc:raise error(exc)


@router.get('/{mid}/{chapter}/beats/{number}/audio')
def audio_api(mid:str,chapter:str,number:int,token:str,file:str):
    try:
        current=status(mid,chapter)
        beat=next((b for b in current['beats'] if b['number']==number),None)
        if not beat or beat['token']!=token or beat['file']!=file or not beat['ready']:raise FileExistsError('Audio changed or is unavailable. Refresh this chapter.')
        path=clip_path(mid,chapter,manifest(mid,chapter)['clips'][token])
        return FileResponse(path,media_type='audio/mpeg',headers={'Cache-Control':'no-store'})
    except (ValueError,OSError) as exc:raise error(exc)


@router.post('/{mid}/{chapter}/open-folder')
def folder_api(mid:str,chapter:str):
    try:
        from app.audio_folders import sync
        path=sync(mid,chapter)
        open_system_folder(path);return {'opened':True}
    except (ValueError,OSError) as exc:raise error(exc)


@router.post('/{mid}/{chapter}/full')
def full_api(mid:str,chapter:str,body:Request):
    from app.tasks import lock as task_lock,ensure_idle
    try:
        with task_lock:
            ensure_idle()
            return build_full(mid,chapter,body.revision)
    except (ValueError,OSError) as exc:raise error(exc)


@router.get('/{mid}/{chapter}/full/audio')
def full_audio_api(mid:str,chapter:str,token:str):
    try:
        current=status(mid,chapter);full=current['full']
        if not full['ready'] or full['token']!=token:raise FileExistsError('Full audio is outdated. Assemble it again from the current beats.')
        return FileResponse(clip_path(mid,chapter,full),media_type='audio/mpeg',headers={'Cache-Control':'no-store'})
    except (ValueError,OSError) as exc:raise error(exc)
