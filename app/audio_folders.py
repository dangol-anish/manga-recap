"""Readable MP3 copies for Finder/Explorer; internal audio is never moved."""
import re
import shutil
import tempfile
import threading
from pathlib import Path
from app import storage

lock=threading.RLock()


def safe_name(value):
    value=re.sub(r'[<>:"/\\|?*\x00-\x1f]', '-', str(value)).strip().rstrip('. ')
    if not value: value='Untitled'
    if value.split('.')[0].upper() in {'CON','PRN','AUX','NUL',*(f'COM{i}' for i in range(1,10)),*(f'LPT{i}' for i in range(1,10))}:value='_'+value
    return value[:140].rstrip('. ')


def checked(directory):
    if directory.is_symlink() or not directory.resolve().is_relative_to(storage.OUTPUTS.resolve()):
        raise ValueError('Unsafe audio folder.')
    directory.mkdir(parents=True,exist_ok=True)
    return directory


def named_folder(parent, label):
    name=safe_name(label);candidate=parent/name;number=2
    existing={p.name.casefold() for p in parent.iterdir()}
    while candidate.name.casefold() in existing:
        candidate=parent/f'{name} ({number})';number+=1
    return checked(candidate)


def publish(source, directory, label, digest, entry, extension=".mp3"):
    if extension not in (".mp3", ".mp4"):raise ValueError("Unsupported saved media type.")
    from app.speech_stage6 import checksum
    key=label+':'+digest
    old=entry.get('files',{}).get(key)
    if old:
        target=directory/old
        if target.parent==directory and not target.is_symlink() and target.is_file() and checksum(target)==digest:return target
    target=directory/(label+extension);number=2
    existing={p.name.casefold() for p in directory.iterdir()}
    while target.name.casefold() in existing:
        # Reuse an identical copy after an interrupted index update.
        if target.is_file() and not target.is_symlink() and checksum(target)==digest:break
        target=directory/f'{label} ({number}){extension}';number+=1
    else:
        with tempfile.NamedTemporaryFile(dir=directory,prefix='.copy-',suffix='.tmp',delete=False) as file:temporary=Path(file.name)
        try:
            shutil.copyfile(source,temporary)
            if checksum(temporary)!=digest:raise ValueError('The file changed while copying. Please open the folder again.')
            temporary.replace(target)
        finally:temporary.unlink(missing_ok=True)
    entry.setdefault('files',{})[key]=target.name
    return target


def sync(mid=None, chapter=None):
    from app import detection, speech_stage6 as speech
    with lock:
        if storage.OUTPUTS.is_symlink() or not storage.OUTPUTS.resolve().is_relative_to(storage.ROOT.resolve()):raise ValueError('Unsafe saved-files folder.')
        directory=checked(storage.OUTPUTS/'Audio')
        index_path=storage.DATA/'audio-folders.json'
        if index_path.is_symlink():raise ValueError('Unsafe audio folder index.')
        index=storage.read_json(index_path,{'manga':{}})
        jobs=[]
        for path in (storage.DATA/'downloader/jobs').glob('*.json'):
            if not path.is_symlink():jobs.append(storage.read_json(path,{}))
        selected=None
        for manga in detection.catalog()['manga']:
            if mid and manga['id']!=mid:continue
            title=next((j['title'] for j in jobs if j.get('folder')==manga['folder'] and j.get('title')),None)
            title=title or re.sub(r'-(?:weebcentral|mangapill)-[0-9a-f]{8}$','',manga['name'])
            for item in manga['chapters']:
                name=item['name']
                if chapter and name!=chapter:continue
                from app.grouped_audio import chapter_state
                overlay=chapter_state(manga['id'],name)
                original=storage.ROOT/overlay['folder'] if overlay else storage.OUTPUTS/manga['id']/name/'narration/audio-stage6'
                if original.is_symlink() or not original.resolve().is_relative_to(storage.OUTPUTS.resolve()) or not original.is_dir():continue
                full_path=original/'chapter.json'
                full=storage.read_json(full_path,{}) if not full_path.is_symlink() else {}
                files=[]
                def add(metadata,label,beats=False):
                    filename=metadata.get('file','')
                    if Path(filename).name!=filename or not filename.endswith('.mp3'):return
                    path=original/filename
                    if path.is_symlink() or not path.is_file():return
                    digest=speech.checksum(path)
                    if metadata.get('sha256') and digest!=metadata['sha256']:return
                    files.append((path,label,digest,beats))
                add(full,'Full chapter')
                # Chapter metadata keeps the exact saved beat order even after
                # later narration changes make that audio unavailable in the app.
                for beat in full.get('beats',[]):add(beat,f"Beat {beat['beat']:03d}",True)
                try:
                    current=speech.status(manga['id'],name)
                    for beat in current['beats']:
                        if beat['ready']:add(beat,f"Beat {beat['number']:03d}",True)
                except (ValueError,OSError):pass
                if not files:continue
                entry=index['manga'].get(manga['id'])
                if entry is None:
                    folder=named_folder(directory,title)
                    entry=index['manga'][manga['id']]={'folder':folder.name,'chapters':{}}
                if Path(entry['folder']).name!=entry['folder']:raise ValueError('Unsafe manga folder.')
                manga_folder=checked(directory/entry['folder'])
                from app.media_layout import migrate,chapter_label
                chapter_entry=entry['chapters'].setdefault(name,{})
                migrate(manga_folder,name,chapter_entry,'Audio')
                for source,label,digest,is_beat in files:
                    prefix=chapter_label(name)
                    suffix=label.replace(' ','')+'-Audio' if is_beat else 'Audio'
                    publish(source,manga_folder,prefix+'-'+suffix,digest,chapter_entry.setdefault('flat',{}))
                    storage.write_json(index_path,index)
                if mid and chapter:selected=manga_folder
        if mid and chapter and selected is None:raise ValueError('Create audio for this chapter first.')
        return selected or directory
