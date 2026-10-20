"""One heavy task at a time, across the local library."""
import threading
lock=threading.RLock()


def ensure_idle():
    from app import downloader
    if downloader.active is not None:
        raise FileExistsError('A download is running. Stop it or wait for it to finish.')
    from app import main,audio,video,alignment
    if main.process is not None and main.process.poll() is None:
        raise FileExistsError('Panel detection is running. Finish or stop it before starting another task.')
    for group in (audio.jobs,video.jobs,alignment.jobs,alignment.chapter_jobs):
        if any(job.get('running') for job in list(group.values())):
            raise FileExistsError('Another audio, alignment or video task is running. Wait for it to finish.')
