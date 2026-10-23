from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app
from app import storage,speech_stage6 as speech

class AudioRootFolderTests(unittest.TestCase):
    def test_open_root_without_a_selected_manga_or_chapter(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            with patch.object(storage,'ROOT',root),patch.object(storage,'OUTPUTS',root/'outputs'),patch.object(speech,'open_system_folder') as opener,patch('app.detection.catalog',return_value={'manga':[]}):
                response=TestClient(app).post('/api/speech/open-root-folder')
                self.assertEqual(response.status_code,200,response.text)
                opener.assert_called_once_with(root/'outputs/Audio')
                self.assertTrue((root/'outputs').is_dir())
    def test_outside_root_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            with patch.object(storage,'ROOT',root/'repo'),patch.object(storage,'OUTPUTS',root/'elsewhere'),patch.object(speech,'open_system_folder') as opener,patch('app.detection.catalog',return_value={'manga':[]}):
                self.assertEqual(TestClient(app).post('/api/speech/open-root-folder').status_code,400)
                opener.assert_not_called()

    def test_readable_names_copies_versions_and_originals_stay_safe(self):
        from app import audio_folders as folders
        import hashlib
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);out=root/'outputs';data=root/'data';original=out/'abc'/'Chapter 1'/'narration/audio-stage6'
            original.mkdir(parents=True);(data/'downloader/jobs').mkdir(parents=True)
            storage.write_json(data/'downloader/jobs/job.json',{'folder':'manga/technical-folder','title':'A Proper Manga Name'})
            clip=original/'random-hash.mp3';clip.write_bytes(b'original saved audio')
            storage.write_json(original/'chapter.json',{'file':clip.name,'sha256':hashlib.sha256(clip.read_bytes()).hexdigest(),'beats':[{'beat':1,'file':clip.name},{'beat':2,'file':clip.name}]})
            catalog={'manga':[{'id':'abc','name':'technical-folder','folder':'manga/technical-folder','chapters':[{'name':'Chapter 1'}]}]}
            with patch.object(storage,'ROOT',root),patch.object(storage,'OUTPUTS',out),patch.object(storage,'DATA',data),patch('app.detection.catalog',return_value=catalog),patch.object(speech,'status',side_effect=ValueError('Old narration')):
                library=folders.sync();chapter=library/'A Proper Manga Name'
                self.assertEqual((chapter/'Chapter1-Audio.mp3').read_bytes(),clip.read_bytes())
                self.assertTrue((chapter/'Chapter1-Beat001-Audio.mp3').is_file());self.assertTrue((chapter/'Chapter1-Beat002-Audio.mp3').is_file())
                self.assertEqual(folders.sync('abc','Chapter 1'),chapter)
                self.assertEqual(len(list(chapter.glob('*.mp3'))),3)
                # User edits to a friendly copy never affect the internal audio.
                (chapter/'Chapter1-Audio.mp3').write_bytes(b'user edit')
                folders.sync();self.assertEqual(clip.read_bytes(),b'original saved audio')
                self.assertEqual((chapter/'Chapter1-Audio.mp3').read_bytes(),b'user edit')
                self.assertEqual((chapter/'Chapter1-Audio (2).mp3').read_bytes(),clip.read_bytes())

    def test_portable_names_and_symlink_rejected(self):
        from app import audio_folders as folders
        self.assertEqual(folders.safe_name('A: Manga?'),'A- Manga-')
        self.assertEqual(folders.safe_name('CON'),'_CON')
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);out=root/'outputs';out.mkdir();other=root/'other';other.mkdir();(out/'Audio').symlink_to(other,target_is_directory=True)
            with patch.object(storage,'ROOT',root),patch.object(storage,'OUTPUTS',out):
                with self.assertRaises(ValueError):folders.sync()
