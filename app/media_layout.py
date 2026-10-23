"""Flatten readable media copies without touching the pipeline's original files."""
import re
from pathlib import Path
from app import storage
from app.audio_folders import checked,publish,safe_name,named_folder


def chapter_label(name):return safe_name(re.sub(r'\s+','',name))


def migrate(manga_folder,chapter,entry,kind):
    """Publish old readable copies flat, then retain the old tree as a backup."""
    old_name=entry.get('folder')
    if not old_name:return
    if old_name in ('.','..') or Path(old_name).name!=old_name or '\\' in old_name:raise ValueError('Unsafe old media folder.')
    old=manga_folder/old_name
    if old.is_symlink():raise ValueError('Unsafe old media folder.')
    extension='.mp3' if kind=='Audio' else '.mp4'
    prefix=chapter_label(chapter)
    if old.is_dir():
        from app.speech_stage6 import checksum
        files=list(old.glob('*'+extension))
        if kind=='Audio':files+=list((old/'Voice clips').glob('*.mp3'))
        for source in sorted(files):
            if source.is_symlink() or not source.is_file() or not source.resolve().is_relative_to(old.resolve()):raise ValueError('Unsafe saved media file.')
            beat=re.match(r'Beat (\d+)',source.stem)
            label=prefix+'-'+(f'Beat{int(beat[1]):03d}-Audio' if beat else 'Audio' if kind=='Audio' else 'Video')
            publish(source,manga_folder,label,checksum(source),entry.setdefault('flat',{}),extension)
        backup=checked(storage.OUTPUTS/'.media-folder-backups'/kind)
        parent=checked(backup/manga_folder.name)
        target=named_folder(parent,old_name)
        target.rmdir()
        old.rename(target)
        entry.setdefault('previous_folders',[]).append(str(target.relative_to(storage.OUTPUTS)))
    entry.pop('folder',None)
