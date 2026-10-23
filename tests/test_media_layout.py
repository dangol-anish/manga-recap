from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from app import storage
from app.media_layout import migrate

class MediaLayoutTests(unittest.TestCase):
    def test_existing_audio_and_clips_flattened_without_losing_old_tree(self):
        with tempfile.TemporaryDirectory() as name:
            out=Path(name)/'outputs';manga=out/'Audio/Manga';old=manga/'Chapter 1';(old/'Voice clips').mkdir(parents=True)
            (old/'Full chapter.mp3').write_bytes(b'full');(old/'Full chapter (2).mp3').write_bytes(b'edited version');(old/'Voice clips/Beat 001.mp3').write_bytes(b'clip')
            entry={'folder':'Chapter 1'}
            with patch.object(storage,'OUTPUTS',out):migrate(manga,'Chapter 1',entry,'Audio')
            self.assertEqual((manga/'Chapter1-Audio.mp3').read_bytes(),b'edited version')
            self.assertEqual((manga/'Chapter1-Audio (2).mp3').read_bytes(),b'full')
            self.assertEqual((manga/'Chapter1-Beat001-Audio.mp3').read_bytes(),b'clip')
            self.assertFalse(old.exists());self.assertNotIn('folder',entry)
            self.assertTrue((out/entry['previous_folders'][0]/'Full chapter.mp3').is_file())
    def test_existing_video_name_collision_preserves_both(self):
        with tempfile.TemporaryDirectory() as name:
            out=Path(name)/'outputs';manga=out/'Videos/Manga';old=manga/'Chapter 2';old.mkdir(parents=True)
            (old/'Finished video.mp4').write_bytes(b'old video');(manga/'Chapter2-Video.mp4').write_bytes(b'user file')
            entry={'folder':'Chapter 2'}
            with patch.object(storage,'OUTPUTS',out):migrate(manga,'Chapter 2',entry,'Videos')
            self.assertEqual((manga/'Chapter2-Video.mp4').read_bytes(),b'user file')
            self.assertEqual((manga/'Chapter2-Video (2).mp4').read_bytes(),b'old video')
