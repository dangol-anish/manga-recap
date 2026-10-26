"""One heavy task at a time, across the local library."""
import threading
lock=threading.RLock()


def ensure_idle():
    from app import grouped_audio
    if grouped_audio.active is not None:
        raise FileExistsError("Selected narration audio is being created. Finish or stop it first.")
    from app import shorts
    if shorts.active is not None:
        raise FileExistsError("Shorts are being created. Finish or stop them before starting another task.")
    from app import stitch
    if stitch.active is not None:
        raise FileExistsError('Videos are being combined. Wait or stop Stitch before starting another task.')
    from app import batch_work
    if batch_work.active is not None and threading.current_thread() is not batch_work.worker:
        raise FileExistsError('Selected chapters are being created. Wait for them to finish or stop the selection first.')
    from app import video_stage7
    if video_stage7.active is not None:
        raise FileExistsError("Stage 7 video export is running. Wait for it to finish before starting another task.")
    from app import speech_stage6
    if speech_stage6.active is not None:
        raise FileExistsError("Stage 6 audio is running. Finish or stop it before starting another task.")
    from app import panel_preparation
    if panel_preparation.active is not None:
        raise FileExistsError('Panel preparation is running. Finish or stop it before starting another task.')
    from app import detection
    if detection.busy():
        raise FileExistsError('Stage 3 panel detection is running or finishing its current page. Wait or stop it first.')
    from app import downloader
    if downloader.active is not None:
        raise FileExistsError('A download is running. Stop it or wait for it to finish.')
    from app import main,audio,video,alignment
    if main.process is not None and main.process.poll() is None:
        raise FileExistsError('Panel detection is running. Finish or stop it before starting another task.')
    for group in (audio.jobs,video.jobs,alignment.jobs,alignment.chapter_jobs):
        if any(job.get('running') for job in list(group.values())):
            raise FileExistsError('Another audio, alignment or video task is running. Wait for it to finish.')
