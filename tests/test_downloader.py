from tests.support import create_symlink
import asyncio
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, AsyncMock

from PIL import Image
from fastapi.testclient import TestClient
from app import downloader as d, download_sources as s, storage
from app.main import app


def png(color='white'):
    b=BytesIO();Image.new('RGB',(40,60),color).save(b,'PNG');return b.getvalue()


class SourcesTests(unittest.TestCase):
    def test_only_approved_series_urls(self):
        for url in ('http://mangapill.com/manga/a','https://evil-mangapill.com/manga/a','https://weebcentral.com/chapters/a','https://127.0.0.1/manga/a','https://mangapill.com:8080/manga/a'):
            with self.assertRaises(ValueError):s.source_url(url)
        self.assertEqual(s.source_url('https://www.mangapill.com/manga/1/title')[1],'mangapill.com')

    def test_chapter_names_numeric_order_dedup_and_other_hosts(self):
        html='''<a href="/chapters/b">Chapter 10</a><a href="/chapters/a">Chapter 2.5</a><a href="/chapters/a">duplicate</a><a href="https://evil.com/chapters/c">Chapter 1</a>'''
        self.assertEqual([c['name'] for c in s.chapter_list(html,'https://mangapill.com/manga/1')],['Chapter 2.5','Chapter 10'])

    def test_weebcentral_slug_normalized_and_duplicate_heading(self):
        url, _ = s.source_url('https://weebcentral.com/series/01JNKHGT37KPS3KBVA5CG1X7V0/Title')
        self.assertEqual(url,'https://weebcentral.com/series/01JNKHGT37KPS3KBVA5CG1X7V0')
        self.assertEqual(s.parse('<h1>Title</h1><h1>Title</h1>').title,'Title')

    def test_nested_weeb_labels(self):
        html='<a href="/chapters/abc"><span>Chapter 12.5</span><span>Last Read yesterday</span></a>'
        self.assertEqual(s.chapter_list(html,'https://weebcentral.com/series/abc')[0]['name'],'Chapter 12.5')

    def test_private_and_redirect_addresses_blocked(self):
        with patch.object(s.socket,'getaddrinfo',return_value=[(2,1,6,'',('127.0.0.1',443))]):
            with self.assertRaises(ValueError):s.public_url('https://cdn.example/page.png')
        for url in ('file:///etc/passwd','https://user:pass@cdn.example/image','http://127.0.0.1/image'):
            with self.assertRaises(ValueError):s.public_url(url)

    def test_preserves_signed_queries_and_page_order(self):
        html=b'<img src="ad.jpg"><img class="js-page" data-src="https://cdn.example/2.png?token=abc"><img class="js-page" src="https://cdn.example/1.png?token=xyz">'
        with patch.object(s,'fetch',AsyncMock(return_value=html)):
            urls=asyncio.run(s.image_urls(None,{'url':'https://mangapill.com/chapters/a'},'mangapill.com'))
        self.assertEqual(urls,['https://cdn.example/2.png?token=abc','https://cdn.example/1.png?token=xyz'])


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='.test-',dir=Path(__file__).resolve().parents[1])
        self.root=Path(self.temp.name)
        self.patches=[patch.object(storage,'ROOT',self.root),patch.object(storage,'DATA',self.root/'data')]
        for p in self.patches:p.start()
        self.catalog={'id':'a'*32,'title':'Example','host':'mangapill.com','source':'MangaPill','url':'https://mangapill.com/manga/1/example','chapters':[{'id':'c1','name':'Chapter 1','url':'https://mangapill.com/chapters/c1'}]}
        storage.write_json(d.record_path('catalogs',self.catalog['id']),self.catalog)
        d.stop_event.clear();d.active=None
        self.job=d.create(self.catalog['id'],['c1'])
        self.urls=['https://cdn.example/a.png','https://cdn.example/b.png']

    def tearDown(self):
        d.stop_event.clear();d.active=None
        for p in reversed(self.patches):p.stop()
        self.temp.cleanup()

    def transfer(self,payload=None,urls=None):
        fetch=AsyncMock(return_value=payload or png())
        with patch.object(s,'image_urls',AsyncMock(return_value=urls or self.urls)),patch.object(s,'fetch',fetch):
            asyncio.run(d.transfer(self.job))
        return fetch

    def test_download_images_correct_numbering_hashes_and_progress(self):
        self.transfer()
        self.assertEqual(self.job['chapters'][0]['status'],'complete')
        chapter=self.job['chapters'][0]
        self.assertEqual([p['file'] for p in chapter['pages']],['001.png','002.png'])
        self.assertTrue(all(d.verified(d.destination(chapter['folder']+'/'+p['file']),p) for p in chapter['pages']))
        self.assertEqual(d.summary(self.job)['verified_pages'],2)
        self.assertEqual(list(self.root.rglob('*.part')),[])
        self.assertFalse((self.root/'data/library.json').exists())

    def test_resume_skips_verified_and_redownloads_corrupt_page(self):
        self.transfer();self.assertEqual(self.transfer().await_count,0)
        c=self.job['chapters'][0];d.destination(c['folder']+'/001.png').write_bytes(b'corrupt')
        self.assertEqual(self.transfer().await_count,1)
        self.assertEqual(d.summary(self.job)['verified_pages'],2)

    def test_download_flushes_a_writable_handle_before_publication(self):
        handles={};flushed=[];original_open=Path.open;original_sync=d.os.fsync
        def track(path,*args,**kwargs):
            stream=original_open(path,*args,**kwargs)
            if path.suffix=='.part':handles[stream.fileno()]=stream
            return stream
        def windows_flush(fd):
            stream=handles.get(fd)
            if stream is not None and not stream.closed:
                if not stream.writable():raise OSError('Windows cannot flush a read-only handle')
                self.assertGreater(stream.tell(),0);flushed.append(True)
            return original_sync(fd)
        with patch.object(Path,'open',track),patch.object(d.os,'fsync',side_effect=windows_flush):self.transfer()
        self.assertEqual(d.summary(self.job)['verified_pages'],2)
        self.assertEqual(len(flushed),2)

    def test_reject_html_failure_retry_and_resume(self):
        original_sleep=asyncio.sleep
        async def fast_sleep(_): await original_sleep(0)
        with patch.object(d.asyncio,'sleep',fast_sleep):
            self.transfer(b'<html>blocked</html>')
        self.assertEqual(d.summary(self.job)['verified_pages'],0)
        self.assertEqual(d.summary(self.job)['failed_pages'],2)
        self.assertEqual(list(self.root.rglob('*.png')),[])
        self.transfer();self.assertEqual(d.summary(self.job)['verified_pages'],2)

    def test_partial_failure_retries_only_failed_image(self):
        async def page_fetch(client,url,*args):
            return png() if url.endswith('a.png') else b'not an image'
        async def fast_sleep(_):pass
        with patch.object(s,'image_urls',AsyncMock(return_value=self.urls)), patch.object(s,'fetch',page_fetch), patch.object(d.asyncio,'sleep',fast_sleep):
            asyncio.run(d.transfer(self.job))
        self.assertEqual(d.summary(self.job)['verified_pages'],1)
        self.assertEqual(d.summary(self.job)['failed_pages'],1)
        self.assertEqual(self.transfer().await_count,1)
        self.assertEqual(d.summary(self.job)['verified_pages'],2)

    def test_stop_during_download_preserves_completed_page(self):
        async def page_fetch(*args):
            d.stop_event.set()
            return png()
        with patch.object(s,'image_urls',AsyncMock(return_value=self.urls)), patch.object(s,'fetch',page_fetch):
            asyncio.run(d.transfer(self.job))
        self.assertEqual(d.summary(self.job)['verified_pages'],1)
        d.stop_event.clear()
        self.assertEqual(self.transfer().await_count,1)

    def test_stop_and_restart_recovery(self):
        d.stop_event.set();self.transfer();self.assertEqual(d.summary(self.job)['verified_pages'],0)
        self.job['status']='running';d.save(self.job);d.recover()
        self.assertEqual(d.load('jobs',self.job['id'])['status'],'paused')
        d.stop_event.clear();self.transfer();self.assertEqual(d.summary(self.job)['verified_pages'],2)

    def test_source_reorder_keeps_existing_images(self):
        self.transfer();original=self.job['chapters'][0]['pages'][0]['sha256']
        self.transfer(urls=self.urls[::-1]);chapter=self.job['chapters'][0]
        self.assertEqual(chapter['status'],'failed');self.assertIn('order changed',chapter['error'])
        self.assertEqual(chapter['pages'][0]['sha256'],original)

    def test_crash_after_publish_before_complete_is_adopted(self):
        self.transfer()
        for page in self.job['chapters'][0]['pages']:page['status']='downloading'
        self.assertEqual(self.transfer().await_count,0)
        self.assertEqual(d.summary(self.job)['verified_pages'],2)

    def test_path_escape_and_symlink_blocked(self):
        with self.assertRaises(ValueError):d.destination('manga/../../outside')
        (self.root/'manga').mkdir(exist_ok=True)
        create_symlink(self.root/'manga/link', self.root/'data', target_is_directory=True)
        with self.assertRaises(ValueError):d.destination('manga/link/page')

    def test_no_overwrite_untracked_file(self):
        folder=d.destination(self.job['chapters'][0]['folder']);folder.mkdir(parents=True)
        target=folder/'001.png';target.write_bytes(b'existing')
        async def fast_sleep(_):pass
        with patch.object(d.asyncio,'sleep',fast_sleep):self.transfer()
        self.assertEqual(target.read_bytes(),b'existing')
        self.assertEqual(d.summary(self.job)['failed_pages'],1)

    def test_api_selection_and_saved_jobs(self):
        client=TestClient(app)
        response=client.get('/api/downloader/jobs');self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()[0]['id'],self.job['id'])
        response=client.post('/api/downloader/jobs',json={'catalog_id':self.catalog['id'],'chapter_ids':['unknown']})
        self.assertEqual(response.status_code,400)
        self.assertEqual(client.post('/api/downloader/jobs/bad/resume').status_code,400)
        self.assertEqual(client.get('/').status_code,200)
        self.assertIn('download-progress',client.get('/').text)
        self.assertIn('Manga library',client.get('/library').text)

    def test_folder_ownership_prevents_parallel_jobs(self):
        folder=d.destination(self.job['chapters'][0]['folder']);folder.mkdir(parents=True)
        storage.write_json(folder/'.download-owner.json',{'job_id':'b'*32})
        with self.assertRaises(ValueError):d.start(self.job)
        self.assertIsNone(d.active)

    def test_stop_saves_active_object_and_resumes_after_failure(self):
        import time
        self.job['status']='running';d.save(self.job)
        d.active={'id':self.job['id'],'started':time.monotonic(),'job':self.job}
        response=TestClient(app).post('/api/downloader/jobs/'+self.job['id']+'/stop')
        self.assertEqual(response.status_code,200)
        self.assertTrue(d.stop_event.is_set())
        self.assertEqual(self.job['status'],'stopping')

    def test_saved_paths_survive_a_different_clone_location(self):
        import shutil
        self.transfer()
        moved = self.root / 'another clone with spaces'
        shutil.copytree(self.root / 'manga', moved / 'manga')
        shutil.copytree(self.root / 'data', moved / 'data')
        with patch.object(storage, 'ROOT', moved), patch.object(storage, 'DATA', moved / 'data'):
            self.job = d.load('jobs', self.job['id'])
            self.assertFalse(Path(self.job['folder']).is_absolute())
            self.assertEqual(self.transfer().await_count, 0)
            self.assertEqual(d.summary(self.job)['verified_pages'], 2)

    def test_server_lock_excludes_a_second_instance_and_releases(self):
        from app.runtime import acquire_server_lock
        path = self.root / 'server.lock'
        first = acquire_server_lock(path)
        try:
            with self.assertRaises(RuntimeError): acquire_server_lock(path)
        finally: first.close()
        acquire_server_lock(path).close()

    def test_windows_server_lock_branch(self):
        import sys
        from types import SimpleNamespace
        from unittest.mock import Mock
        from app import runtime
        windows = SimpleNamespace(LK_NBLCK=1, locking=Mock())
        with patch.object(runtime.os, 'name', 'nt'), patch.dict(sys.modules, {'msvcrt': windows}):
            stream = runtime.acquire_server_lock(self.root / 'windows.lock')
            try:
                self.assertEqual(stream.tell(), 0)
                windows.locking.assert_called_once_with(stream.fileno(), 1, 1)
            finally: stream.close()

    def test_open_single_chapter_in_finder(self):
        folder = d.destination(self.job['chapters'][0]['folder'])
        folder.mkdir(parents=True)
        with patch.object(d.sys, 'platform', 'darwin'), patch.object(d.subprocess, 'run') as launch:
            response = TestClient(app).post('/api/downloader/jobs/'+self.job['id']+'/open-folder')
        self.assertEqual(response.status_code, 200)
        launch.assert_called_once_with(['open', str(folder)], check=True, timeout=10)

    def test_open_multiple_chapters_in_explorer(self):
        self.job['chapters'].append({**self.job['chapters'][0], 'id': 'c2'})
        d.save(self.job)
        folder = d.destination(self.job['folder']); folder.mkdir(parents=True)
        with patch.object(d.sys, 'platform', 'win32'), patch.object(d.os, 'startfile', create=True) as launch:
            response = TestClient(app).post('/api/downloader/jobs/'+self.job['id']+'/open-folder')
        self.assertEqual(response.status_code, 200)
        launch.assert_called_once_with(str(folder))

    def test_missing_folder_does_not_launch_finder(self):
        with patch.object(d.subprocess, 'run') as launch:
            response = TestClient(app).post('/api/downloader/jobs/'+self.job['id']+'/open-folder')
        self.assertEqual(response.status_code, 400)
        launch.assert_not_called()

    def test_clean_chapter_names_and_duplicate_release_rejection(self):
        from app.chapter_names import chapter_folder_name
        for name,expected in [('Chapter 001: title','Chapter 1'),('Episode 2','Chapter 2'),('Ch. 0002.50','Chapter 2.5'),('Chapter 0','Chapter 0')]:
            self.assertEqual(chapter_folder_name(name),expected)
        self.assertEqual(self.job['chapters'][0]['folder'].split('/')[-1],'Chapter 1')
        self.catalog['chapters'].append({'id':'c2','name':'Chapter 1: other release','url':'https://mangapill.com/chapters/c2'})
        storage.write_json(d.record_path('catalogs',self.catalog['id']),self.catalog)
        with self.assertRaisesRegex(ValueError,'same chapter number'):d.create(self.catalog['id'],['c1','c2'])

    def test_migration_preserves_images_and_updates_journal_references(self):
        from scripts.normalize_chapter_folders import migrate
        old = self.job['folder']+'/Chapter 1-12345678'
        self.job['chapters'][0]['folder']=old;d.save(self.job)
        folder=d.destination(old);folder.mkdir(parents=True)
        (folder/'0000.png').write_bytes(b'original image')
        storage.write_json(folder/'.download-owner.json',{'job_id':self.job['id']})
        storage.write_json(storage.DATA/'renumbering/transactions/example.json',{'folder':old})
        moves=migrate()
        self.assertEqual(moves,[(old,self.job['folder']+'/Chapter 1')])
        new=d.destination(self.job['folder']+'/Chapter 1')
        self.assertEqual((new/'0000.png').read_bytes(),b'original image')
        self.assertEqual(d.load('jobs',self.job['id'])['chapters'][0]['folder'],self.job['folder']+'/Chapter 1')
        self.assertEqual(storage.read_json(storage.DATA/'renumbering/transactions/example.json')['folder'],self.job['folder']+'/Chapter 1')

    def test_migration_refuses_existing_destination(self):
        from scripts.normalize_chapter_folders import migrate
        old=self.job['folder']+'/Chapter 1-12345678'
        self.job['chapters'][0]['folder']=old;d.save(self.job)
        source=d.destination(old);source.mkdir(parents=True)
        storage.write_json(source/'.download-owner.json',{'job_id':self.job['id']})
        d.destination(self.job['folder']+'/Chapter 1').mkdir()
        with self.assertRaises(ValueError):migrate()
        self.assertTrue(source.exists())

    def test_one_task_at_a_time(self):
        from app.tasks import ensure_idle
        d.active={'id':self.job['id'],'started':0}
        with self.assertRaises(FileExistsError):ensure_idle()


if __name__=='__main__':unittest.main()
