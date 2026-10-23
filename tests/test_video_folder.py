from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app
from app import storage,video_folders as folders


class VideoFolderTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.out=self.root/'outputs';self.data=self.root/'data'
        self.catalog={'manga':[{'id':'abc','name':'technical-weebcentral-12345678','folder':'manga/technical','chapters':[{'name':'Chapter 1'},{'name':'Chapter 2'}]}]}
        self.patches=[patch.object(storage,'ROOT',self.root),patch.object(storage,'OUTPUTS',self.out),patch.object(storage,'DATA',self.data),patch('app.detection.catalog',return_value=self.catalog)]
        for p in self.patches:p.start()
        storage.write_json(self.data/'downloader/jobs/job.json',{'folder':'manga/technical','title':'Readable Manga Title'})
    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.temp.cleanup()
    def video(self,chapter='Chapter 1',token='a'*64,content=b'finished mp4 fixture'):
        directory=self.out/'abc'/chapter/'narration/video-stage7';directory.mkdir(parents=True,exist_ok=True)
        source=directory/f'chapter-{token}.mp4';source.write_bytes(content)
        storage.write_json(directory/'export.json',{'render_token':token,'bytes':len(content)})
        return source
    def test_always_available_route_opens_clean_root_without_selection(self):
        with patch('app.runtime.open_system_folder') as opener:
            response=TestClient(app).post('/api/cinema/open-root-folder')
            self.assertEqual(response.status_code,200,response.text)
            opener.assert_called_once_with(self.out/'Videos')
            self.assertEqual(list((self.out/'Videos').iterdir()),[])
    def test_titles_chapters_and_finished_files_without_rerendering(self):
        source=self.video();self.video('Chapter 2')
        with patch('app.video_stage7.start') as render,patch('app.speech_stage6.source') as speech:
            root=folders.sync();chapter=root/'Readable Manga Title'
            self.assertEqual((chapter/'Chapter1-Video.mp4').read_bytes(),source.read_bytes())
            self.assertTrue((root/'Readable Manga Title/Chapter2-Video.mp4').is_file())
            folders.sync();self.assertEqual(len(list(chapter.glob('*.mp4'))),2)
            render.assert_not_called();speech.assert_not_called()
    def test_new_versions_and_user_changes_never_overwrite_originals(self):
        old=self.video();original=old.read_bytes();root=folders.sync();chapter=root/'Readable Manga Title'
        self.video(token='b'*64,content=b'new finished video')
        folders.sync();self.assertEqual((chapter/'Chapter1-Video.mp4').read_bytes(),original)
        self.assertEqual((chapter/'Chapter1-Video (2).mp4').read_bytes(),b'new finished video')
        (chapter/'Chapter1-Video (2).mp4').write_bytes(b'user edit')
        folders.sync();self.assertEqual((chapter/'Chapter1-Video (2).mp4').read_bytes(),b'user edit')
        self.assertEqual((chapter/'Chapter1-Video (3).mp4').read_bytes(),b'new finished video')
        self.assertEqual(old.read_bytes(),original)
    def test_unfinished_or_wrong_size_video_not_listed(self):
        source=self.video();source.write_bytes(b'incomplete')
        root=folders.sync();self.assertEqual(list(root.iterdir()),[])
        storage.write_json(source.parent/'export.json',{'render_token':'../../outside','bytes':10})
        self.assertEqual(list(folders.sync().iterdir()),[])
    def test_duplicate_titles_remain_separate(self):
        self.video();other=dict(self.catalog['manga'][0],id='def',chapters=[{'name':'Chapter 1'}]);self.catalog['manga'].append(other)
        directory=self.out/'def/Chapter 1/narration/video-stage7';directory.mkdir(parents=True)
        (directory/('chapter-'+'b'*64+'.mp4')).write_bytes(b'other')
        storage.write_json(directory/'export.json',{'render_token':'b'*64,'bytes':5})
        root=folders.sync();self.assertEqual(len(list(root.iterdir())),2)
    def test_symlink_root_rejected(self):
        self.out.mkdir();target=self.root/'other';target.mkdir();(self.out/'Videos').symlink_to(target,target_is_directory=True)
        with patch('app.runtime.open_system_folder') as opener:
            self.assertEqual(TestClient(app).post('/api/cinema/open-root-folder').status_code,400)
            opener.assert_not_called()
