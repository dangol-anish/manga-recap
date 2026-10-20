import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from app import storage, renumbering as r, downloader
from app.main import app


class RenumberingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / 'outputs')
        self.root = Path(self.temp.name)
        self.patches = [patch.object(storage,'ROOT',self.root), patch.object(storage,'DATA',self.root/'data')]
        for p in self.patches:p.start()
        self.chapter = self.root/'manga'/'Sample'/'Chapter 1'
        self.chapter.mkdir(parents=True)
        downloader.active=None

    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.temp.cleanup()

    def seed(self, names, folder=None):
        folder=folder or self.chapter
        folder.mkdir(parents=True,exist_ok=True)
        for name in names:(folder/name).write_bytes(('content:'+name).encode())

    def preview(self):
        return r.preview([next(c['id'] for c in r.folders() if c['folder']=='manga/Sample/Chapter 1')])

    def contents(self):
        return {p.name:p.read_bytes() for p in r.images(self.chapter)}

    def test_natural_order_gaps_extensions_and_contents(self):
        self.seed(['page_10.JPG','page_2.png','page_1.webp'])
        plan=self.preview()
        self.assertEqual(plan['chapters'][0]['mapping'],[{'from':'page_1.webp','to':'0000.webp'},{'from':'page_2.png','to':'0001.png'},{'from':'page_10.JPG','to':'0002.JPG'}])
        result=r.apply(plan['id'])
        self.assertEqual(result['status'],'complete')
        self.assertEqual(self.contents(),{'0000.webp':b'content:page_1.webp','0001.png':b'content:page_2.png','0002.JPG':b'content:page_10.JPG'})

    def test_existing_target_names_are_staged_without_overwrite(self):
        self.seed(['0001.jpg','0003.jpg'])
        plan=self.preview();r.apply(plan['id'])
        self.assertEqual(self.contents(),{'0000.jpg':b'content:0001.jpg','0001.jpg':b'content:0003.jpg'})

    def test_preview_does_not_rename_images(self):
        self.seed(['001.jpg','004.jpg']);before=self.contents()
        self.preview()
        self.assertEqual(self.contents(),before)
        self.assertFalse((self.chapter/'.renumbering.json').exists())

    def test_only_selected_chapters_and_no_nonimage_changes(self):
        self.seed(['2.jpg','8.jpg','notes.json'])
        second=self.root/'manga/Sample/Chapter 2';self.seed(['10.jpg'],second)
        r.apply(self.preview()['id'])
        self.assertEqual((self.chapter/'notes.json').read_bytes(),b'content:notes.json')
        self.assertTrue((second/'10.jpg').exists())

    def test_each_selected_chapter_starts_at_zero(self):
        self.seed(['8.jpg']);second=self.root/'manga/Sample/Chapter 2';self.seed(['15.png'],second)
        plan=r.preview([c['id'] for c in r.folders()]);r.apply(plan['id'])
        self.assertTrue((self.chapter/'0000.jpg').exists());self.assertTrue((second/'0000.png').exists())

    def test_stale_preview_refuses_before_any_move(self):
        self.seed(['2.jpg']);plan=self.preview();self.seed(['3.jpg'])
        result=r.apply(plan['id'])
        self.assertEqual(result['status'],'needs_attention')
        self.assertEqual(set(self.contents()),{'2.jpg','3.jpg'})
        self.assertFalse((self.chapter/'.renumbering.json').exists())

    def test_repeat_apply_is_idempotent(self):
        self.seed(['2.jpg','9.png']);plan=self.preview();r.apply(plan['id']);before=self.contents()
        self.assertEqual(r.apply(plan['id'])['status'],'complete')
        self.assertEqual(self.contents(),before)
        self.assertEqual(self.preview()['chapters'][0]['changes'],0)

    def test_interruption_is_continued_from_journal(self):
        self.seed(['2.jpg','9.jpg']);plan=self.preview();original=r.move_exclusive;calls=0
        def interrupt(source,target,checksum):
            nonlocal calls
            calls+=1
            if calls==2:raise OSError('Simulated shutdown')
            return original(source,target,checksum)
        with patch.object(r,'move_exclusive',side_effect=interrupt):
            result=r.apply(plan['id'])
        self.assertEqual(result['status'],'needs_attention')
        self.assertTrue(r.folders()[0]['pending'])
        retry=self.preview();self.assertEqual(retry['chapters'][0]['images'],2)
        self.assertEqual(r.apply(retry['id'])['status'],'complete')
        self.assertEqual(self.contents(),{'0000.jpg':b'content:2.jpg','0001.jpg':b'content:9.jpg'})

    def test_interrupted_link_unlink_recovers_same_inode(self):
        self.seed(['2.jpg']);plan=self.preview();unlink=Path.unlink;failed=False
        def interrupt(path,*args,**kwargs):
            nonlocal failed
            if path.name=='2.jpg' and not failed:failed=True;raise OSError('Simulated interruption after link')
            return unlink(path,*args,**kwargs)
        with patch.object(Path,'unlink',interrupt):r.apply(plan['id'])
        self.assertEqual(r.apply(self.preview()['id'])['status'],'complete')
        self.assertEqual(self.contents(),{'0000.jpg':b'content:2.jpg'})

    def test_concurrent_target_is_never_overwritten(self):
        self.seed(['2.jpg','9.jpg']);plan=self.preview();move=r.move_exclusive
        def conflict(source,target,checksum):
            if target.name=='0001.jpg':target.write_bytes(b'new external file')
            return move(source,target,checksum)
        with patch.object(r,'move_exclusive',side_effect=conflict):result=r.apply(plan['id'])
        self.assertEqual(result['status'],'needs_attention')
        self.assertEqual((self.chapter/'0001.jpg').read_bytes(),b'new external file')
        self.assertEqual((self.chapter/'0000.jpg').read_bytes(),b'content:2.jpg')
        self.assertIn(b'content:9.jpg',[p.read_bytes() for p in self.chapter.glob('.renumber-*/*')])

    def test_paths_and_symbolic_links_rejected(self):
        for relative in ('../other','manga/../data','/tmp/folder','manga\\..\\data'):
            with self.assertRaises(ValueError):r.folder_path(relative)
        link=self.root/'manga/link';link.symlink_to(self.chapter,target_is_directory=True)
        with self.assertRaises(ValueError):r.folder_path('manga/link')
        self.seed(['1.jpg']);(self.chapter/'2.jpg').symlink_to(self.chapter/'1.jpg')
        self.assertEqual(len(r.images(self.chapter)),1)

    def test_renumbering_blocked_during_download(self):
        self.seed(['2.jpg']);plan=self.preview()
        downloader.active={'id':'a'*32}
        try:
            with self.assertRaises(FileExistsError):r.apply(plan['id'])
            self.assertTrue((self.chapter/'2.jpg').exists())
        finally:downloader.active=None

    def test_download_resume_cannot_restore_cleaned_pages(self):
        self.seed(['2.jpg']);r.apply(self.preview()['id'])
        job={'id':'a'*32,'chapters':[{'folder':'manga/Sample/Chapter 1'}]}
        with self.assertRaisesRegex(ValueError,'protect your edits'):downloader.start(job)

    def test_direct_renumber_and_native_result_folder(self):
        self.seed(['2.jpg','9.png']);client=TestClient(app)
        ident=r.folders()[0]['id']
        result=client.post('/api/renumbering/renumber',json={'chapter_ids':[ident]})
        self.assertEqual(result.status_code,200);self.assertEqual(result.json()['status'],'complete')
        self.assertEqual(set(self.contents()),{'0000.jpg','0001.png'})
        endpoint='/api/renumbering/plans/'+result.json()['id']+'/folders/0/open'
        with patch('app.runtime.open_system_folder') as open_folder:
            self.assertEqual(client.post(endpoint).status_code,200)
            open_folder.assert_called_once_with(self.chapter)
        with patch('app.runtime.open_system_folder') as open_folder:
            self.assertEqual(client.post(endpoint.replace('/0/','/99/')).status_code,400)
            open_folder.assert_not_called()

    def test_direct_renumber_refuses_unknown_folder(self):
        result=TestClient(app).post('/api/renumbering/renumber',json={'chapter_ids':['unknown']})
        self.assertEqual(result.status_code,400)

    def test_api_selection_and_validation(self):
        self.seed(['2.jpg']);client=TestClient(app)
        listing=client.get('/api/renumbering/chapters');self.assertEqual(listing.status_code,200)
        preview=client.post('/api/renumbering/preview',json={'chapter_ids':[listing.json()[0]['id']]})
        self.assertEqual(preview.status_code,200)
        result=client.post('/api/renumbering/apply',json={'plan_id':preview.json()['id']})
        self.assertEqual(result.status_code,200);self.assertEqual(result.json()['status'],'complete')
        self.assertEqual(client.post('/api/renumbering/preview',json={'chapter_ids':['unknown']}).status_code,400)
        self.assertEqual(client.post('/api/renumbering/apply',json={'plan_id':'../bad'}).status_code,400)
        self.assertIn('renumbering.js',client.get('/renumbering').text)


if __name__=='__main__':unittest.main()
