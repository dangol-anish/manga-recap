"""Explicit, sequential, resumable narration through the AI Studio web page."""
import copy
import json
import re
import threading
import time
import uuid
from fastapi import APIRouter
from pydantic import BaseModel, Field
from app import errors, storage, narration_stage5 as story
from app.chapter_names import chapter_sort_key
from app.studio_browser import Attention
from app.narration_extension import ExtensionBrowser as StudioBrowser
from app.studio_responses import reply_problem, full_repair_prompt

router = APIRouter(prefix='/api/browser-narration')
lock = threading.RLock()
active = None
worker = None
cancel = threading.Event()
resume_signal = threading.Event()
latest = None
checkpoint_failed = False

ACTIVITIES={'connecting':'Connecting to AI Studio','opening_tab':'Opening a fresh chat','configuring':'Configuring AI Studio','checking_schema':'Verifying saved JSON schema','inserting_prompt':'Adding the batch prompt','attaching_photos':'Attaching labeled pages','waiting_upload':'Waiting for uploads to finish','submitting':'Submitting the request','generating':'Waiting for Gemini to finish','reading_reply':'Reading the completed reply','validating':'Validating narration','correcting':'Preparing a correction','saved':'Batch saved'}


def finish_timing(job, end=None):
    started=job.get('run_started_at')
    if isinstance(started,(int,float)):
        job['elapsed_seconds']=job.get('elapsed_seconds',0)+max(0,(time.time() if end is None else end)-started)
    job['run_started_at']=None


def report_activity(step):
    if step not in ACTIVITIES:return
    with lock:
        if active is None or threading.current_thread() is not worker:return
        if active.get('activity',{}).get('step')==step:return
        active['activity']={'step':step,'label':ACTIVITIES[step],'started_at':time.time()}
        save(active)


def presentation(job, now=None):
    if not job:return job
    value=copy.deepcopy(job);now=time.time() if now is None else now
    started=value.get('run_started_at')
    value['timing_available']='elapsed_seconds' in value or isinstance(started,(int,float))
    value['elapsed_seconds']=max(0,value.get('elapsed_seconds',0))+(max(0,now-started) if isinstance(started,(int,float)) and value['status'] in {'running','stopping'} else 0)
    activity=value.get('activity') or {}
    value['activity_elapsed_seconds']=max(0,now-activity['started_at']) if isinstance(activity.get('started_at'),(int,float)) else None
    from app.narration_extension import connection_view
    value['browser_connection']=connection_view()
    return value


def directory(): return storage.DATA / 'browser-narration'


def matches(pattern,value): return isinstance(value,str) and re.fullmatch(pattern,value) is not None


def job_path(ident):
    if not matches('[a-f0-9]{32}', ident): raise ValueError('Invalid narration queue ID.')
    target = directory() / 'jobs' / (ident + '.json')
    if storage.DATA.is_symlink() or directory().is_symlink() or target.parent.is_symlink() or target.is_symlink(): raise ValueError('Unsafe narration queue path.')
    return target


def validate_job(job):
    if not isinstance(job, dict) or job.get('schema_version') != 1 or not isinstance(job.get('items'), list):
        raise ValueError('Saved browser narration queue is damaged. Its files are retained; check Backup & storage before continuing.')
    job_path(job.get('id', ''))
    if not matches('[a-f0-9]{32}', job.get('manga_id','')):
        raise ValueError('Saved narration queue has an invalid manga identity. Its files were retained.')
    if any(not matches('[a-f0-9]{64}',job.get(key,'')) for key in ('expected_revision','prompt_revision')):
        raise ValueError('Saved narration queue is missing its source versions. Its files were retained.')
    if job.get('phase') not in ('ready','sending','awaiting_reply','reply_saved','importing','correcting') or type(job.get('cursor')) is not int or not 0<=job['cursor']<=len(job['items']):
        raise ValueError('Saved narration queue has an invalid recovery checkpoint. Its files were retained.')
    if job.get('status') not in ('running','paused','stopping','complete','failed'):
        raise ValueError('Saved browser narration queue has an invalid status. Its files were retained.')
    for item in job['items']:
        if not isinstance(item, dict) or not isinstance(item.get('chapter'), str) or not matches(r'batch-\d+', item.get('batch_id', '')) or not matches('[a-f0-9]{64}', item.get('fingerprint', '')):
            raise ValueError('Saved browser narration queue contains an invalid batch. No work was changed.')
        if item.get('status') not in ('waiting','running','complete') or type(item.get('take',0)) is not int or item.get('take',0)<0:
            raise ValueError('Saved browser narration queue has an invalid batch checkpoint. No work was changed.')
    return job


def save(job):
    global latest, checkpoint_failed
    job['updated_at']=time.time()
    latest = copy.deepcopy(job)
    try: storage.write_json(job_path(job['id']), job)
    except Exception:
        checkpoint_failed=True
        raise
    checkpoint_failed=False


def current():
    with lock:
        if checkpoint_failed and latest is not None: return copy.deepcopy(latest)
        if active is not None: return copy.deepcopy(active)
        pointer = directory() / 'current.json'
        if pointer.is_symlink(): raise ValueError('Unsafe narration queue pointer.')
        ident = storage.read_json(pointer)
        return validate_job(storage.read_json(job_path(ident))) if ident else None


def recover():
    global latest
    job = current()
    if job and job['status'] in {'running','stopping'}:
        finish_timing(job,job.get('updated_at',job.get('run_started_at',time.time())))
        job.update(status='paused', message='App restarted. Completed imports are safe. Resume to recover the saved reply; unfinished submissions are never resent automatically.')
        save(job)


def prompt_revision(mid):
    from app import story_memory
    return story._digest([(storage.ROOT/'prompts/narration_master.md').read_text(encoding='utf-8'), story_memory.load(mid)])


def check_sources(job):
    if prompt_revision(job['manga_id']) != job['prompt_revision']:
        raise FileExistsError('The narration prompt or reviewed story memory changed. Stop this queue and start a new selection; completed imports are retained.')
    if story._digest(story.load_state(job['manga_id'])) != job['expected_revision']:
        raise FileExistsError('Saved narration changed outside this queue, or an import completed before its queue checkpoint. Stop and start a new selection to reuse current imports; no requests will be resent.')


def attempt_dir(job, item):
    # Folder names are generated from validated identities, never AI content.
    target = directory() / 'replies' / job['id'] / str(job['items'].index(item)) / str(item.get('take',0))
    if any(p.is_symlink() for p in (directory(), directory()/'replies', target.parent.parent, target.parent, target)):
        raise ValueError('Unsafe narration reply folder.')
    target.mkdir(parents=True, exist_ok=True)
    return target


def checkpoint(job, **changes):
    with lock:
        if changes.get('status') in {'paused','complete','failed'}:finish_timing(job)
        job.update(changes)
        save(job)


def pause(job, exc):
    with lock:
        _pause(job,exc)


def _pause(job, exc):
    global active, latest, checkpoint_failed
    code = getattr(exc, 'code', 'error')
    finish_timing(job)
    if isinstance(exc,InterruptedError):
        job.update(status='paused',attention='stopped',retry_safe=False,error=None,
                   message='Queue stopped locally. Saved replies and accepted batches are retained. A submitted AI Studio request may still finish. Resume recovers saved work; use the separate retry checkbox only after inspecting an uncertain submission.')
        for item in job['items']:
            if item.get('status')=='running':item['status']='waiting'
        try:save(job)
        finally:active=None
        return
    job.update(status='paused', attention=code, retry_safe=getattr(exc, 'retry_safe', False))
    resource = job['items'][job.get('cursor', 0)] if job['items'] and job.get('cursor', 0) < len(job['items']) else None
    if resource and resource.get('status')=='running': resource['status']='waiting'
    errors.failure(job, exc, 'story', 'Create narration in AI Studio',
                   f"{resource['chapter']} / {resource['batch_id']}" if resource else job['id'])
    job['message'] = str(exc) + ' Completed imports and saved replies are retained. ' + job['error']['message']
    latest=copy.deepcopy(job)
    checkpoint_failed=True
    try: save(job)
    finally: active = None


def url_checkpoint(job, url):
    from urllib.parse import urlparse
    parsed = urlparse(url)
    # Never persist account identifiers, URL queries, authentication redirects.
    if parsed.scheme == 'https' and parsed.hostname == 'aistudio.google.com' and parsed.path.startswith('/prompts/'):
        clean = 'https://aistudio.google.com' + parsed.path
        if clean != job.get('chat_url'): checkpoint(job, chat_url=clean)


def process_item(job, item, browser):
    from app import narration
    with story.lock, story.panels.lock:
        check_sources(job)
        batch = story.find_batch(job['manga_id'], item['chapter'], item['batch_id'], item['fingerprint'])
        state = story.status(job['manga_id'], item['chapter'])
        status = next(b for b in state['batches'] if b['id'] == item['batch_id'])
        if status['imported']:
            item['status'] = 'complete'; save(job); return
        if not status['ready']: raise FileExistsError(status['blocked_reason'])
        if job.get('phase') in {'sending','awaiting_reply','reply_saved','importing','correcting'}:
            if job.get('context_token') != status['context_token']:
                raise FileExistsError('Earlier story context changed. Start a new selection with current prompts; saved replies were retained.')
        else:
            job.update(context_token=status['context_token'], correction=0, reply_mode='full')
    folder = attempt_dir(job, item)
    response = folder / f"reply-{job.get('correction',0)}.txt"
    if job.get('phase') == 'correcting':
        raise Attention('A correction was interrupted before its browser submission could be confirmed. Check AI Studio, then explicitly retry this unfinished request.', 'recovery')
    if job.get('phase') not in {'sending','awaiting_reply','reply_saved','importing'}:
        with story.lock, story.panels.lock:
            check_sources(job)
            prompt = story.prompt_text(job['manga_id'], item['chapter'], item['batch_id'])
            photos = story.export_photos(job['manga_id'], item['chapter'], item['batch_id'], item['fingerprint'])
            photo_paths = [photos/(r['page_id']+'.jpg') for r in batch['page_records']]
            storage.write_json(folder/'input.json', {'prompt':prompt,'photos':[p.relative_to(storage.ROOT).as_posix() for p in photo_paths], 'context_token':job['context_token']})
        report_activity('opening_tab')
        browser.prepare(prompt, photo_paths, cancel)
        before = browser.visible_replies()
        with story.lock, story.panels.lock:
            check_sources(job)
            story.find_batch(job['manga_id'],item['chapter'],item['batch_id'],item['fingerprint'])
        checkpoint(job, phase='sending', before=before, chat_url='', message=f"Sending {item['chapter']} / {item['batch_id']} to AI Studio…")
        if cancel.is_set(): raise InterruptedError('Stopped before submitting to AI Studio.')
        report_activity('submitting')
        url_checkpoint(job, browser.send())
        checkpoint(job, phase='awaiting_reply', message=f"Waiting for AI Studio's JSON reply for {item['chapter']} / {item['batch_id']}…")
    elif job.get('phase') in {'sending','awaiting_reply'}:
        browser.recover_page(job.get('chat_url',''))
    while True:
        if cancel.is_set(): raise InterruptedError('Stopped. Resume to continue from the saved reply.')
        if job['phase'] not in {'reply_saved','importing'}:
            report_activity('generating')
            text = browser.receive(job.get('before', []), cancel, lambda url: url_checkpoint(job,url))
            # Atomic raw reply checkpoint precedes every validation/import.
            if getattr(browser,'last_capture',None):
                storage.write_json(folder/f"capture-{job.get('correction',0)}.json",browser.last_capture)
            storage.write_json(response, {'text':text})
            checkpoint(job, phase='reply_saved', message=f"Checking {item['chapter']} / {item['batch_id']}…")
        raw = storage.read_json(response)
        if not raw or not isinstance(raw.get('text'),str):
            raise ValueError('Saved AI Studio reply is missing or damaged. Check this batch before retrying; narration was not reset.')
        mode=job.get('reply_mode','partial' if job.get('correction',0)>0 else 'full')
        problem=reply_problem(raw['text'])
        if problem and problem['kind'] in {'refusal','provider_error'}:
            checkpoint(job,phase='reply_saved',response_problem=problem)
            raise Attention(problem['message']+' Completed chapters remain saved. Retry this batch explicitly only when its cause is resolved.', 'response')
        with story.lock, story.panels.lock:
            check_sources(job)
            story.find_batch(job['manga_id'],item['chapter'],item['batch_id'],item['fingerprint'])
            report_activity('validating')
            checkpoint(job, phase='importing')
            if problem:
                result={'ok':False,'can_correct':False,'errors':[problem['message']]}
            else:
                result = narration.import_reply(job['manga_id'], item['chapter'], item['batch_id'], item['fingerprint'], raw['text'],
                    corrections_only=mode=='partial', context_token=job['context_token'], require_receipt=True, backend=story)
            if result['ok']:
                item['status'] = 'complete'
                job.update(expected_revision=story._digest(story.load_state(job['manga_id'])), phase='ready', before=[], chat_url='', correction=0,reply_mode='full',response_problem=None,
                           message=f"Saved {item['chapter']} / {item['batch_id']}. Story accuracy still needs human review.")
                save(job)
                report_activity('saved')
                return
        storage.write_json(folder/f"validation-{job.get('correction',0)}.json",result)
        checkpoint(job,response_problem=problem or {'kind':'validation','message':'; '.join(result.get('errors',[]))[:1200] or 'Narration panel/anchor checks failed.'})
        if job.get('correction',0) >= 2:
            checkpoint(job, phase='reply_saved')
            raise Attention('Gemini still did not return valid narration after two correction attempts. Use Retry this batch in a fresh chat, or the manual Narration correction controls. Rejected replies and earlier imports are safe; later batches have not started.', 'validation')
        if not result.get('can_correct') and not problem:
            checkpoint(job, phase='reply_saved')
            raise Attention('The reply has a source/receipt mismatch or cannot be safely patched. Review its saved validation errors. Use Retry this batch in a fresh chat to request narration from the current sources; the app will not relabel or import this draft.', 'validation')
        correction = job.get('correction',0)+1
        storage.write_json(folder/f'correction-{correction}.json', result)
        repair_mode='full' if problem else 'partial'
        if problem:
            with story.lock,story.panels.lock:
                check_sources(job)
                repair=full_repair_prompt(story.prompt_text(job['manga_id'],item['chapter'],item['batch_id']),problem['message'])
        else:repair=result['redo_message']
        checkpoint(job, phase='correcting', correction=correction, reply_mode=repair_mode, message=f"Correcting {item['chapter']} / {item['batch_id']} (attempt {correction}/2)…")
        report_activity('correcting')
        browser.prepare(repair, [], cancel, fresh=False, reply_mode=repair_mode)
        before = browser.visible_replies()
        with story.lock, story.panels.lock:
            check_sources(job)
            story.find_batch(job['manga_id'],item['chapter'],item['batch_id'],item['fingerprint'])
        checkpoint(job, phase='sending', before=before)
        if cancel.is_set(): raise InterruptedError('Stopped before submitting a correction to AI Studio.')
        url_checkpoint(job, browser.send())
        checkpoint(job, phase='awaiting_reply', message=f"Waiting for AI Studio's corrected JSON for {item['chapter']} / {item['batch_id']}…")
        response = folder/f'reply-{correction}.txt'


def run(job):
    global active
    from app.runtime import keep_awake
    try:
        with keep_awake(), StudioBrowser() as browser:
            while not cancel.is_set():
                try:
                    for index,item in enumerate(job['items']):
                        if cancel.is_set(): raise InterruptedError('Stopped before the next narration batch.')
                        if item['status'] == 'complete': continue
                        job['cursor'] = index
                        item['status'] = 'running'
                        save(job)
                        process_item(job,item,browser)
                    checkpoint(job, status='complete', attention='', message='Selected narration is saved. Press Refresh in Chapter selection to load its beats, then review story & panels before creating audio.')
                    break
                except InterruptedError as exc:
                    pause(job, exc); break
                except Exception as exc:
                    if type(exc).__module__.startswith('playwright.'):
                        wrapped=Attention('A browser control did not finish. Inspect the narration browser for a sign-in, terms dialog or changed control. If the browser closed, press Stop, then Resume to reopen it. An uncertain submission will not be resent automatically.',
                                          'browser',job.get('phase') in {'ready','correcting'})
                        wrapped.__cause__=exc
                        exc=wrapped
                    pause(job,exc)
                    if not isinstance(exc,Attention): break
                    # Keep the visible browser available for sign-in or inspection.
                    # No processing/retries while paused; heavy-task ownership released.
                    while not cancel.is_set() and not resume_signal.wait(.5): pass
                    resume_signal.clear()
                    if cancel.is_set(): break
    except Exception as exc:
        try: pause(job,exc)
        except Exception as checkpoint_error: errors.warning(checkpoint_error,'story','Save narration queue checkpoint',job['id'])
    finally:
        with lock:
            if job['status'] in {'running','stopping'}:
                finish_timing(job)
                job.update(status='paused',message='Stopped. Saved replies and imports are retained; explicitly resume when ready.')
            errors.save_final(save,job,'story')
            active = None


def launch(job):
    global active, worker
    cancel.clear(); resume_signal.clear()
    job['run_started_at']=time.time()
    job['activity']={'step':'connecting','label':ACTIVITIES['connecting'],'started_at':time.time()}
    job.update(status='running', attention='', retry_safe=False, error=None)
    save(job); active=job
    try:
        worker=threading.Thread(target=run,args=(job,),daemon=True)
        worker._narration_job=job
        worker.start()
    except Exception:
        active=None;job['status']='paused';save(job);raise
    return copy.deepcopy(job)


def start(mid, chapters):
    from app.tasks import lock as task_lock, ensure_idle
    with task_lock, lock, story.lock, story.panels.lock:
        ensure_idle()
        if worker and worker.is_alive(): raise FileExistsError('Stop the previous narration queue before starting another selection.')
        if not chapters or len(set(chapters))!=len(chapters): raise ValueError('Select one or more distinct chapters.')
        items=[]
        for chapter in sorted(chapters,key=chapter_sort_key):
            for batch in story.batches(mid,chapter):
                items.append({'chapter':chapter,'batch_id':batch['id'],'fingerprint':batch['fingerprint'],'status':'waiting'})
        job={'schema_version':1,'id':uuid.uuid4().hex,'manga_id':mid,'items':items,'created_at':time.time(),
             'expected_revision':story._digest(story.load_state(mid)),'prompt_revision':prompt_revision(mid),'phase':'ready','cursor':0,
             'model':'gemini-3.8-flash','thinking':'medium','connection':'extension','message':'Connecting to your selected AI Studio tab through the browser extension.'}
        job['status']='paused';save(job)
        storage.write_json(directory()/'current.json',job['id'])
        return launch(job)


def resume(ident, retry=False):
    global active
    from app.tasks import lock as task_lock, ensure_idle
    with task_lock, lock:
        ensure_idle()
        job = current()
        if not job or job['id']!=ident: raise ValueError('Narration queue not found.')
        if job['status']=='complete': return job
        if job['status'] not in {'paused','failed'}:
            raise FileExistsError('This queue is already running or still stopping. Refresh its status and wait until it is paused before resuming.')
        if worker and worker.is_alive() and cancel.is_set():
            raise FileExistsError('The previous worker is still stopping. Wait for it to finish, then Resume; no new request was sent.')
        if job.get('attention') in {'validation','response'} and job.get('phase')=='reply_saved' and not retry:
            raise ValueError('The completed reply was rejected. Use Retry this batch in a fresh chat, or import a manual correction. Accepted batches remain saved.')
        check_sources(job)
        original=getattr(worker,'_narration_job',None) if worker and worker.is_alive() else None
        if worker and worker.is_alive() and (original is None or original['id']!=job['id']):
            raise FileExistsError('A different narration browser is still open. Stop it before resuming this restored queue.')
        # A known rejection may be retried on explicit Resume. An ambiguous
        # submission requires the separate explicit retry checkbox.
        if retry or job.get('retry_safe'):
            job.update(phase='ready',before=[],chat_url='',correction=0,reply_mode='full',response_problem=None)
            if job.get('cursor',0)<len(job['items']):
                item=job['items'][job.get('cursor',0)]
                item['take']=item.get('take',0)+1
        job.update(status='running', attention='', error=None, message='Resuming selected narration…')
        job['run_started_at']=time.time()
        job['activity']={'step':'connecting','label':ACTIVITIES['connecting'],'started_at':time.time()}
        save(job)
        if worker and worker.is_alive():
            # Worker owns the original mutable job object.
            active = job
            if original is not None: original.clear();original.update(copy.deepcopy(job));active=original
            resume_signal.set()
            return copy.deepcopy(job)
        return launch(job)


def stop(ident):
    with lock:
        job=current()
        if not job or job['id']!=ident:raise ValueError('Narration queue not found.')
        if worker and worker.is_alive():
            cancel.set(); resume_signal.set()
            original=active or getattr(worker,'_narration_job',None)
            if original is not None:
                original['status']='stopping';save(original)
            return current()
        if job.get('status') in {'running','stopping'}:
            finish_timing(job)
            job.update(status='paused',attention='stopped',retry_safe=False,message='Queue stopped. Saved replies and accepted narration are retained. Resume explicitly to recover unfinished work.')
            for item in job['items']:
                if item.get('status')=='running':item['status']='waiting'
            save(job)
        return job



def clear_queue(ident):
    """Dismiss only the current inactive queue; retain every saved file."""
    global active, latest, checkpoint_failed
    with lock:
        job=current()
        if not job or job['id']!=ident:raise ValueError('Narration queue not found. Refresh its status before clearing.')
        if job['status'] not in {'paused','failed','complete'}:
            raise FileExistsError('Stop the running queue and wait until it is paused before clearing it. Saved narration is unchanged.')
        previous=worker
        if previous and previous.is_alive():
            cancel.set();resume_signal.set()
    # A paused worker may still own the browser context. Let it close locally
    # before detaching its pointer; never join while holding its final-save lock.
    if previous and previous.is_alive():previous.join(3)
    with lock:
        if worker and worker.is_alive():raise FileExistsError('The narration worker is still stopping. Refresh status and clear again once it has stopped. No files were deleted.')
        job=current()
        if not job or job['id']!=ident:raise FileExistsError('The current queue changed. Refresh status before clearing it.')
        if job['status'] not in {'paused','failed','complete'}:raise FileExistsError('Stop this queue before clearing it.')
        pointer=directory()/'current.json'
        if pointer.is_symlink():raise ValueError('Unsafe narration queue pointer.')
        storage.write_json(pointer,None)
        active=latest=None
        checkpoint_failed=False
        return {'cleared':True,'message':'Queue cleared from this page. Saved narration, story memory and queue/reply files are retained. Choose chapters and press Start to reuse accepted batches.'}


def shutdown():
    cancel.set();resume_signal.set()
    if worker and worker.is_alive():worker.join(10)


class Selection(BaseModel):
    manga_id:str=Field(min_length=1,max_length=128)
    chapters:list[str]=Field(min_length=1,max_length=500)


class Resume(BaseModel):
    retry_unfinished:bool=False


@router.get('')
def status_api():
    try:return presentation(current())
    except (ValueError,OSError) as exc:raise errors.request_error(exc)


@router.post('')
def start_api(body:Selection):
    try:return start(body.manga_id,body.chapters)
    except (ValueError,OSError) as exc:raise errors.request_error(exc)


@router.post('/{ident}/resume')
def resume_api(ident:str,body:Resume):
    try:return resume(ident,body.retry_unfinished)
    except (ValueError,OSError) as exc:raise errors.request_error(exc)


@router.post('/{ident}/stop')
def stop_api(ident:str):
    try:return stop(ident)
    except (ValueError,OSError) as exc:raise errors.request_error(exc)


@router.post('/{ident}/retry-batch')
def retry_batch_api(ident:str):
    try:
        with lock:
            job=current()
            if not job or job['id']!=ident or job['status']!='paused' or job.get('phase')!='reply_saved' or job.get('attention') not in {'validation','response'}:
                raise ValueError('Only a completed, rejected reply can use this retry. Inspect an uncertain submission before using its separate retry checkbox.')
        return resume(ident,True)
    except (ValueError,OSError) as exc:raise errors.request_error(exc)


@router.post('/{ident}/clear')
def clear_api(ident:str):
    try:return clear_queue(ident)
    except (ValueError,OSError) as exc:raise errors.request_error(exc)
