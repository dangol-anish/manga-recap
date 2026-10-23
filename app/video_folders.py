"""Readable copies of finished chapter videos for Finder and Explorer."""
import re
import threading
from pathlib import Path
from app import storage
from app.audio_folders import checked,named_folder,publish

lock=threading.RLock()


def folder(parent,name):
    if not name or name in ('.','..') or Path(name).name!=name or '\\' in name:
        raise ValueError('Unsafe saved video folder.')
    return checked(parent/name)


def sync():
    from app import detection,speech_stage6 as speech
    with lock:
        if storage.OUTPUTS.is_symlink() or not storage.OUTPUTS.resolve().is_relative_to(storage.ROOT.resolve()):
            raise ValueError('Unsafe saved video folder.')
        directory=checked(storage.OUTPUTS/'Videos')
        index_path=storage.DATA/'video-folders.json'
        if index_path.is_symlink():raise ValueError('Unsafe saved video index.')
        index=storage.read_json(index_path,{'manga':{}})
        jobs=[storage.read_json(p,{}) for p in (storage.DATA/'downloader/jobs').glob('*.json') if not p.is_symlink()]
        for manga in detection.catalog()['manga']:
            title=next((j['title'] for j in jobs if j.get('folder')==manga['folder'] and j.get('title')),None)
            title=title or re.sub(r'-(?:weebcentral|mangapill)-[0-9a-f]{8}$','',manga['name'])
            for chapter in manga['chapters']:
                original=storage.OUTPUTS/manga['id']/chapter['name']/'narration/video-stage7'
                if original.is_symlink() or not original.is_dir() or not original.resolve().is_relative_to(storage.OUTPUTS.resolve()):continue
                manifest=original/'export.json'
                if manifest.is_symlink():continue
                saved=storage.read_json(manifest,{})
                token=saved.get('render_token','')
                if not isinstance(token,str) or not re.fullmatch('[0-9a-f]{64}',token):continue
                source=original/('chapter-'+token+'.mp4')
                if source.is_symlink() or not source.is_file() or source.stat().st_size!=saved.get('bytes'):continue
                digest=speech.checksum(source)
                entry=index['manga'].get(manga['id'])
                if entry is None:
                    target=named_folder(directory,title)
                    entry=index['manga'][manga['id']]={'folder':target.name,'chapters':{}}
                manga_folder=folder(directory,entry['folder'])
                from app.media_layout import migrate,chapter_label
                chapter_entry=entry['chapters'].setdefault(chapter['name'],{})
                migrate(manga_folder,chapter['name'],chapter_entry,'Videos')
                target=publish(source,manga_folder,chapter_label(chapter['name'])+'-Video',digest,chapter_entry.setdefault('flat',{}),extension='.mp4')
                chapter_entry['current']={'file':target.name,'sha256':digest,'render_token':token}
                storage.write_json(index_path,index)
        return directory
