"""Explicit local backups, conflict-reviewed restore and tightly limited cache cleanup."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import threading
import time
import unicodedata
import uuid
import zipfile

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app import errors, storage
from app.tasks import lock as task_lock, ensure_idle

router = APIRouter(prefix='/api/storage')
active = None
worker = None
cancel = threading.Event()
recovery_required = False
CHUNK = 1024 * 1024
ROOTS = ('manga', 'data', 'outputs')
FRAME_CACHE = re.compile(r'outputs/[^/]+/[^/]+/narration/video-stage7/frames/[0-9a-f]{64}\.png')


def safe(relative):
    if not isinstance(relative, str) or not relative or '\\' in relative or ':' in relative or '\x00' in relative:
        raise ValueError('Unsafe storage path.')
    parts = PurePosixPath(relative).parts
    if not parts or PurePosixPath(relative).is_absolute() or PurePosixPath(relative).as_posix()!=relative or any(p in ('.', '..') for p in relative.split('/')):
        raise ValueError('Unsafe storage path.')
    current = storage.ROOT
    if current.is_symlink(): raise ValueError('The project root cannot be a symbolic link for storage operations.')
    for part in parts:
        current /= part
        if current.is_symlink(): raise ValueError('Symbolic links are not supported in backup, restore or cleanup: '+relative)
    if not current.resolve().is_relative_to(storage.ROOT.resolve()): raise ValueError('Path is outside this project.')
    return current


def excluded(name):
    parts = PurePosixPath(name).parts
    return (parts[0] not in ROOTS or bool(FRAME_CACHE.fullmatch(name)) or (parts[0] == 'data' and len(parts) > 1 and parts[1] in {'maintenance','errors'})
            or any(p == '.env' or p.startswith('.env.') for p in parts)
            or parts[-1].endswith(('.lock', '.stop')))


def files_under(relative, reject_links=True):
    if not reject_links and (storage.ROOT/relative).is_symlink(): return []
    root = safe(relative)
    if not root.exists(): return []
    found = []
    for directory, dirs, names in os.walk(root, followlinks=False):
        for name in list(dirs):
            if (Path(directory)/name).is_symlink() and not reject_links:
                dirs.remove(name);continue
            safe((Path(directory)/name).relative_to(storage.ROOT).as_posix())
        for name in names:
            if (Path(directory)/name).is_symlink() and not reject_links:continue
            path = safe((Path(directory)/name).relative_to(storage.ROOT).as_posix())
            if not path.is_file(): raise ValueError('Unsupported special file: '+str(path))
            found.append(path)
    return sorted(found)


def checksum(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        while block := stream.read(CHUNK):
            digest.update(block)
    return digest.hexdigest()


def stamp(path):
    s = path.stat()
    return [s.st_size, s.st_mtime_ns, s.st_ctime_ns]


def inventory():
    return {p.relative_to(storage.ROOT).as_posix(): stamp(p)
            for root in ROOTS for p in files_under(root)
            if not excluded(p.relative_to(storage.ROOT).as_posix())}


def ident(value):
    if not re.fullmatch('[0-9a-f]{32}', value): raise ValueError('Invalid storage task.')
    return value


def job_path(value): return safe('data/maintenance/jobs/'+ident(value)+'.json')

def save(job):
    job['updated_at'] = time.time()
    storage.write_json(job_path(job['id']), job)


def check_stop():
    if cancel.is_set(): raise InterruptedError('Stopped safely. Completed backups and your existing files are kept.')


def space(required):
    free = shutil.disk_usage(storage.ROOT).free
    if free < required + 64*1024*1024:
        raise OSError(28, f'Not enough free disk space. This operation needs approximately {required/1024**3:.2f} GB plus 64 MB working space; {free/1024**3:.2f} GB is free.')


def copy_checked(source, target, digest, *, expected=None):
    safe(target.relative_to(storage.ROOT).as_posix())
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name('.storage-'+uuid.uuid4().hex+'.tmp')
    try:
        with source.open('rb') as src, temporary.open('xb') as dst:
            while block := src.read(CHUNK): dst.write(block)
            dst.flush(); os.fsync(dst.fileno())
        if checksum(temporary) != digest: raise ValueError('A file changed or could not be verified: '+str(source))
        safe(target.relative_to(storage.ROOT).as_posix())
        current=checksum(target) if target.is_file() else None
        if current!=expected:raise FileExistsError('Destination changed before publication: '+str(target))
        if expected is None:
            # Atomic no-overwrite publication on the supported APFS/NTFS drives.
            os.link(temporary,target)
            temporary.unlink()
        else:
            os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def progress(job, message, completed=None):
    job['message'] = message
    if completed is not None: job['completed'] = completed
    save(job)


def backup(job):
    initial = inventory()
    if len(initial)>100000 or sum(len(json.dumps({'path':name,'size':s[0],'sha256':'0'*64}))+2 for name,s in initial.items())+512>32*1024*1024:
        raise ValueError('This workspace exceeds the backup tool’s 100,000-file / 32 MB manifest limit. No backup was published. Keep a manual copy of manga, data and outputs together instead.')
    portable=[unicodedata.normalize('NFC',name).casefold() for name in initial]
    if len(portable)!=len(set(portable)):raise ValueError('Some workspace filenames collide across platforms. No backup was published; preserve a manual copy before resolving names.')
    job['total'] = 3*len(initial)
    space(sum(s[0] for s in initial.values()) + sum(1024+len(name.encode('utf-8'))*4 for name in initial))
    directory = safe('backups'); directory.mkdir(exist_ok=True)
    name = time.strftime('MangaRecap-%Y%m%d-%H%M%S-')+job['id'][:8]+'.zip'
    temporary = directory/('.'+name+'.partial')
    target = directory/name
    entries = []
    try:
        with zipfile.ZipFile(temporary, 'x', compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
            for number, (relative, original) in enumerate(initial.items(), 1):
                check_stop(); path = safe(relative)
                if stamp(path) != original: raise FileExistsError('Files changed during backup. Try again when all editing has stopped: '+relative)
                digest = hashlib.sha256(); size = 0
                with path.open('rb') as src, archive.open(relative, 'w', force_zip64=True) as dst:
                    while block := src.read(CHUNK):
                        check_stop(); dst.write(block); digest.update(block); size += len(block)
                if stamp(path) != original or size != original[0]: raise FileExistsError('File changed during backup: '+relative)
                entries.append({'path':relative, 'size':size, 'sha256':digest.hexdigest()})
                progress(job, 'Backing up '+relative, number)
            if inventory() != initial: raise FileExistsError('Workspace files changed during backup. No finished backup was published; try again.')
            for number,row in enumerate(entries,1):
                check_stop()
                if checksum(safe(row['path'])) != row['sha256']: raise FileExistsError('File changed during backup: '+row['path'])
                progress(job,'Checking source consistency '+row['path'],len(entries)+number)
            archive.writestr('backup-manifest.json', json.dumps({'format':'manga-recap-backup-v1', 'created_at':time.time(), 'files':entries}))
        # Verify every archived byte before publishing the backup.
        with zipfile.ZipFile(temporary) as archive:
            for number,row in enumerate(entries,1):
                check_stop(); digest=hashlib.sha256()
                with archive.open(row['path']) as src:
                    while block := src.read(CHUNK): digest.update(block)
                if digest.hexdigest()!=row['sha256']: raise ValueError('Backup verification failed: '+row['path'])
                progress(job,'Verifying saved backup '+row['path'],2*len(entries)+number)
        temporary.rename(target)
        job.update(result=name, message='Backup verified and saved. Copy it to another drive for protection against disk failure.')
    finally:
        temporary.unlink(missing_ok=True)


def archive_path(name):
    if not isinstance(name,str) or Path(name).name != name or not name.endswith('.zip') or '/' in name:
        raise ValueError('Choose a backup ZIP from this project’s backups folder.')
    path=safe('backups/'+name)
    if not path.is_file(): raise FileNotFoundError('Backup file is missing. Open the backups folder and check its location.')
    return path


def inspect(name):
    path=archive_path(name)
    with zipfile.ZipFile(path) as archive:
        try:info=archive.getinfo('backup-manifest.json')
        except KeyError as exc:raise ValueError('This ZIP has no Manga Recap backup manifest. Choose a backup created by this app.') from exc
        if info.file_size > 32*1024*1024: raise ValueError('Backup manifest is too large.')
        manifest=json.loads(archive.read(info))
        if manifest.get('format')!='manga-recap-backup-v1' or not isinstance(manifest.get('files'),list):
            raise ValueError('This is not a Manga Recap backup made by this app.')
        rows=manifest['files']
        if len(rows)>100000: raise ValueError('Backup contains too many files for this restore tool.')
        names=[i.filename for i in archive.infolist()]
        paths=[]; seen=set()
        for row in rows:
            if not isinstance(row,dict) or set(row)!={'path','size','sha256'} or not isinstance(row['sha256'],str):
                raise ValueError('Backup contains an invalid file manifest record.')
            name=row['path']; safe(name)
            key=unicodedata.normalize('NFC',name).casefold()
            if excluded(name) or key in seen: raise ValueError('Unsafe or duplicate backup entry: '+name)
            if os.name=='nt' and any(re.search(r'[<>"|?*\x00-\x1f]',p) or p.endswith((' ','.')) or p.split('.')[0].upper() in {'CON','PRN','AUX','NUL',*[f'COM{i}' for i in range(1,10)],*[f'LPT{i}' for i in range(1,10)]} for p in PurePosixPath(name).parts):
                raise ValueError('Backup contains a filename Windows cannot restore: '+name)
            seen.add(key); paths.append(name)
            item=archive.getinfo(name)
            if stat.S_ISLNK(item.external_attr>>16) or item.is_dir() or item.file_size!=row['size'] or not isinstance(row['size'],int) or row['size']<0 or not re.fullmatch('[0-9a-f]{64}',row['sha256']):
                raise ValueError('Invalid backup file record: '+name)
        if len(names)!=len(set(names)) or set(names)!=set(paths)|{'backup-manifest.json'}: raise ValueError('Backup has unexpected or duplicate entries.')
    return rows


def plan_path(value): return safe('data/maintenance/plans/'+ident(value)+'.json')


def restore_plan(name, replace):
    rows=inspect(name); archive=archive_path(name); rows=[dict(r) for r in rows]
    for row in rows:
        path=safe(row['path'])
        if path.exists() and not path.is_file(): raise FileExistsError('A folder occupies a restored file location: '+row['path'])
        old=checksum(path) if path.exists() else None
        row.update(before=old, action='same' if old==row['sha256'] else 'replace' if old else 'add')
    plan={'id':uuid.uuid4().hex,'kind':'restore','archive':name,'archive_hash':checksum(archive),
          'replace':replace,'files':rows,'created_at':time.time()}
    plan['token']=hashlib.sha256(json.dumps(plan,sort_keys=True).encode()).hexdigest()
    storage.write_json(plan_path(plan['id']),plan)
    return plan


def rollback(job):
    """Undo only files still equal to our restored bytes; preserve outside edits."""
    problems=[]
    for row in reversed(job.get('journal',[])):
        try:
            path=safe(row['path']); current=checksum(path) if path.is_file() else None
            if current==row['before']: continue
            identity=[path.stat().st_dev,path.stat().st_ino] if path.is_file() else None
            if current!=row['sha256'] or identity!=row.get('identity'):
                raise FileExistsError('File changed outside restore; left unchanged: '+row['path'])
            if row['before']:
                original=safe('backups/restore-safety/'+job['id']+'/'+row['path'])
                copy_checked(original,path,row['before'],expected=row['sha256'])
            else: path.unlink()
        except (ValueError,OSError) as exc: problems.append(str(exc))
    if problems: raise FileExistsError('Restore rollback needs attention. Safety copies are kept in backups/restore-safety/'+job['id']+'. '+ '; '.join(problems))


def restore(job, plan):
    if checksum(archive_path(plan['archive']))!=plan['archive_hash']: raise FileExistsError('Backup changed after the preview. Preview it again.')
    rows=inspect(plan['archive'])
    if rows != [{k:r[k] for k in ('path','size','sha256')} for r in plan['files']]: raise ValueError('Backup manifest changed.')
    conflicts=[r for r in plan['files'] if r['action']=='replace']
    if conflicts and not plan['replace']: raise FileExistsError('Existing files differ. Nothing was restored. Choose replacement explicitly or use a fresh clone.')
    for row in plan['files']:
        path=safe(row['path']); current=checksum(path) if path.is_file() else None
        if current!=row['before']: raise FileExistsError('Workspace changed after restore preview: '+row['path']+'. Preview again.')
    space(sum(r['size'] for r in rows)+sum(safe(r['path']).stat().st_size for r in conflicts))
    stage=safe('backups/.restore-'+job['id']); stage.mkdir(parents=True)
    job['total']=2*len(rows); job['journal']=[]
    try:
        with zipfile.ZipFile(archive_path(plan['archive'])) as archive:
            for number,row in enumerate(rows,1):
                check_stop(); target=stage/row['path']; target.parent.mkdir(parents=True,exist_ok=True)
                digest=hashlib.sha256(); size=0
                with archive.open(row['path']) as src, target.open('xb') as dst:
                    while block:=src.read(CHUNK):
                        check_stop(); size+=len(block)
                        if size>row['size']: raise ValueError('Backup entry exceeds its declared size.')
                        dst.write(block);digest.update(block)
                    dst.flush();os.fsync(dst.fileno())
                if size!=row['size'] or digest.hexdigest()!=row['sha256']: raise ValueError('Backup verification failed: '+row['path'])
                progress(job,'Verifying backup '+row['path'],number)
        # Verification finishes before any existing workspace file is changed.
        for number,row in enumerate(plan['files'],1):
            check_stop()
            if row['action']=='same':
                progress(job,'Keeping identical file '+row['path'],len(rows)+number);continue
            target=safe(row['path']); current=checksum(target) if target.is_file() else None
            if current!=row['before']: raise FileExistsError('File changed during restore: '+row['path'])
            if row['before']:
                copy_checked(target,safe('backups/restore-safety/'+job['id']+'/'+row['path']),row['before'])
            prepared=stage/row['path']; stamp=prepared.stat()
            job['journal'].append({**row,'identity':[stamp.st_dev,stamp.st_ino]})
            save(job)  # Intent and staged-file identity are durable before publication.
            safe(row['path']);target.parent.mkdir(parents=True,exist_ok=True)
            current=checksum(target) if target.is_file() else None
            if current!=row['before']:raise FileExistsError('Destination changed before restore publication: '+row['path'])
            if row['before'] is None:os.link(prepared,target)
            else:os.replace(prepared,target)
            progress(job,'Restoring '+row['path'],len(rows)+number)
        job['message']='Restore finished. Existing replacements, if any, are kept in backups/restore-safety/'+job['id']+'.'
    except BaseException:
        rollback(job)
        raise
    finally:
        if stage.exists(): shutil.rmtree(stage)


def cleanup_candidates():
    result=[]
    for path in files_under('.cache/pip'):
        result.append({'path':path.relative_to(storage.ROOT).as_posix(),'category':'Installer download cache','size':path.stat().st_size,'sha256':checksum(path)})
    for path in files_under('outputs'):
        relative=path.relative_to(storage.ROOT).as_posix()
        if FRAME_CACHE.fullmatch(relative):
            result.append({'path':relative,'category':'Rebuildable video frame','size':path.stat().st_size,'sha256':checksum(path)})
    return sorted(result,key=lambda r:r['path'])


def cleanup_plan():
    plan={'id':uuid.uuid4().hex,'kind':'cleanup','files':cleanup_candidates(),'created_at':time.time()}
    plan['token']=hashlib.sha256(json.dumps(plan,sort_keys=True).encode()).hexdigest()
    storage.write_json(plan_path(plan['id']),plan)
    return plan


def cleanup(job,plan):
    job['total']=len(plan['files'])
    progress(job,'Rechecking the exact cache list before deletion.',0)
    current={r['path']:r for r in cleanup_candidates()}
    for row in plan['files']:
        if current.get(row['path'])!=row: raise FileExistsError('A cache file changed after the preview. Preview cleanup again; nothing was removed.')
    freed=0
    for number,row in enumerate(plan['files'],1):
        check_stop(); path=safe(row['path'])
        if checksum(path)!=row['sha256']: raise FileExistsError('A cache file changed during cleanup; it was kept: '+row['path'])
        path.unlink();freed+=row['size'];job['freed_bytes']=freed
        progress(job,'Removed rebuildable cache '+row['path'],number)
    job['message']='Cleanup finished. Original manga, narration, panels, audio, videos, revisions and backups are unchanged.'


def run(job,plan=None):
    global active,recovery_required
    try:
        if job['kind']=='backup': backup(job)
        elif job['kind']=='restore': restore(job,plan)
        else: cleanup(job,plan)
        job['status']='complete';save(job)
    except BaseException as exc:
        if job['kind']=='restore' and job.get('journal'):
            try: rollback(job)
            except (ValueError,OSError) as recovery:
                exc=recovery;job['recovery_required']=True;recovery_required=True
        job['status']='paused' if isinstance(exc,InterruptedError) else 'failed'
        errors.failure(job,exc,'storage',job['kind'].capitalize()+' local files')
        if isinstance(exc,InterruptedError):
            job['message']='Stopped safely. Completed backups and your original work are kept. Start a new backup or preview restore/cleanup again; this task does not resume automatically.'
        errors.save_final(save,job,'storage')
    finally:
        active=None


def start(kind,plan=None):
    global active,worker
    with task_lock:
        ensure_idle()
        if kind=='restore' and not plan['replace'] and any(r['action']=='replace' for r in plan['files']):
            raise FileExistsError('Some existing files differ. Nothing changed. Review conflicts and explicitly choose replacement, or restore into a fresh clone.')
        job={'id':uuid.uuid4().hex,'kind':kind,'status':'running','completed':0,'total':0,'message':'Starting '+kind+'…','created_at':time.time()}
        save(job);cancel.clear();active=job
        try:
            worker=threading.Thread(target=run,args=(job,plan),daemon=True);worker.start()
        except BaseException:
            active=None;job.update(status='failed',message='The storage worker could not start. No files were changed.');save(job);raise
        return view(job)


def view(job): return {k:v for k,v in job.items() if k!='journal'} if job else None


def latest():
    if active:return view(dict(active))
    jobs=errors.job_list(storage.DATA/'maintenance/jobs','storage')
    unresolved=[j for j in jobs if j.get('recovery_required')]
    if unresolved:return view(max(unresolved,key=lambda j:j.get('created_at',0)))
    return view(max(jobs,key=lambda j:j.get('created_at',0))) if jobs else None


def recover():
    global recovery_required
    recovery_required=False
    for _,job in errors.recovery_records(storage.DATA/'maintenance/jobs','storage'):
        if job.get('status')=='running' or job.get('recovery_required'):
            try:
                if job['kind']=='restore': rollback(job)
                job.update(status='paused',recovery_required=False,message='Interrupted storage task stopped safely. Preview and start again; no task resumes automatically.')
            except (ValueError,OSError,KeyError,TypeError) as exc:
                job.update(status='failed',recovery_required=True);recovery_required=True
                errors.failure(job,exc,'storage','Recover interrupted restore')
            save(job)


def shutdown():
    cancel.set()
    if worker and worker.is_alive():worker.join(30)


def usage():
    categories=[]; manga=[]
    for path,label in (('manga','Original manga'),('data','Narration & saved progress'),('outputs','Generated panels, audio & videos'),('vendor','Downloaded models'),('.cache','Installer & setup caches'),('backups','Backups & restore safety copies')):
        files=files_under(path,reject_links=False)
        categories.append({'name':label,'path':path,'bytes':sum(p.stat().st_size for p in files),'files':len(files)})
        if path=='manga':
            totals={}
            for p in files:
                parts=p.relative_to(storage.ROOT/'manga').parts;key=parts[0]
                totals[key]=totals.get(key,0)+p.stat().st_size
            manga=[{'name':k,'bytes':v} for k,v in sorted(totals.items())]
    disk=shutil.disk_usage(storage.ROOT)
    return {'categories':categories,'manga':manga,'free_bytes':disk.free,'used_bytes':sum(c['bytes'] for c in categories),
            'note':'Sizes count file bytes, including readable copies and previous revisions; symbolic links are skipped. Backups on this drive do not protect against drive failure.'}


def backups():
    root=safe('backups')
    if not root.exists():return []
    return [{'name':p.name,'bytes':p.stat().st_size,'modified':p.stat().st_mtime} for p in sorted(root.glob('*.zip'),key=lambda p:p.stat().st_mtime,reverse=True) if not p.is_symlink() and p.is_file()]


def load_plan(value,token,kind):
    plan=storage.read_json(plan_path(value))
    if not plan or plan['token']!=token or plan['kind']!=kind:raise FileExistsError('Storage preview is missing or changed. Preview again.')
    content={k:v for k,v in plan.items() if k!='token'}
    if hashlib.sha256(json.dumps(content,sort_keys=True).encode()).hexdigest()!=token:
        raise FileExistsError('Storage preview contents changed. Preview again before applying.')
    return plan


class RestorePreview(BaseModel):
    archive:str
    replace:bool=False


class Apply(BaseModel):
    id:str
    token:str
    confirm:bool=False


def endpoint(fn):
    from functools import wraps
    @wraps(fn)
    def wrapped(*args,**kwargs):
        try:return fn(*args,**kwargs)
        except (ValueError,OSError,KeyError,zipfile.BadZipFile) as exc:
            detail=errors.describe(exc,'storage','Manage local backup, restore or cleanup')
            raise HTTPException(detail['status'],detail['message']) from exc
    return wrapped


@router.get('/usage')
@endpoint
def usage_api():return usage()


@router.get('/backups')
@endpoint
def backups_api():return backups()


@router.get('/status')
@endpoint
def status_api():return latest()


@router.post('/backup')
@endpoint
def backup_api():return start('backup')


@router.post('/restore/preview')
@endpoint
def restore_preview_api(body:RestorePreview):
    with task_lock:
        ensure_idle();return restore_plan(body.archive,body.replace)


@router.post('/restore')
@endpoint
def restore_api(body:Apply):
    if not body.confirm:raise ValueError('Review the restore file list and explicitly confirm before restoring.')
    return start('restore',load_plan(body.id,body.token,'restore'))


@router.post('/cleanup/preview')
@endpoint
def cleanup_preview_api():
    with task_lock:
        ensure_idle();return cleanup_plan()


@router.post('/cleanup')
@endpoint
def cleanup_api(body:Apply):
    if not body.confirm:raise ValueError('Review the exact cache file list and explicitly confirm deletion first.')
    return start('cleanup',load_plan(body.id,body.token,'cleanup'))


@router.post('/stop')
@endpoint
def stop_api():
    if active:cancel.set()
    return latest()


@router.post('/recover')
@endpoint
def recover_api():
    with task_lock:
        if active:raise FileExistsError('Stop the current storage task before checking restore recovery.')
        recover()
        return {'recovery_required':recovery_required,'message':'Restore recovery still needs attention. Read the affected task and keep its safety copies.' if recovery_required else 'Recovery checked. Processing is available again.'}


@router.post('/open-backups')
@endpoint
def folder_api():
    from app.runtime import open_system_folder
    root=safe('backups');root.mkdir(exist_ok=True);open_system_folder(root)
    return {'opened':True}
