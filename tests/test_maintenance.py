import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid
import zipfile
from fastapi.testclient import TestClient
from app import maintenance as m, storage
from app.main import app


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='.test-storage-',dir=Path(__file__).resolve().parents[1]);self.root=Path(self.temp.name)
        self.patches=[patch.object(storage,'ROOT',self.root),patch.object(storage,'DATA',self.root/'data'),patch.object(storage,'OUTPUTS',self.root/'outputs'),patch.object(m,'ensure_idle')]
        for p in self.patches:p.start()
        m.active=m.worker=None;m.recovery_required=False;m.cancel.clear()
        self.put('manga/Sample/Chapter 1/0000.png',b'original image')
        self.put('manga/Sample/Chapter 1/.renumbering.json',b'{"status":"complete"}')
        self.put('data/manga/sample/story-stage5.json',b'{"narration":"Original story"}')
        self.put('outputs/sample/Chapter 1/narration/audio-stage6/clip.mp3',b'audio')
        self.put('outputs/Videos/Sample/Chapter1-Video.mp4',b'video')
        self.put('.env',b'GEMINI_API_KEY=private-key')
        self.put('data/server.lock',b'lock')
        self.put('vendor/model.bin',b'weights')
        self.put('.cache/pip/download.bin',b'installer')
        self.client=TestClient(app)
    def tearDown(self):
        m.cancel.set()
        if m.worker and m.worker.is_alive():m.worker.join(10)
        m.active=m.worker=None;m.recovery_required=False;m.cancel.clear()
        for p in reversed(self.patches):p.stop()
        self.temp.cleanup()
    def put(self,name,value):
        path=self.root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(value);return path
    def job(self,kind):
        return {'id':uuid.uuid4().hex,'kind':kind,'status':'running','completed':0,'total':0,'created_at':1}
    def backup(self):
        job=self.job('backup');m.save(job);m.run(job)
        self.assertEqual(job['status'],'complete',job.get('message'));return job['result']
    def execute(self,kind,plan):
        job=self.job(kind);m.save(job);m.run(job,plan);return job
    def protected(self):
        return {str(p.relative_to(self.root)):p.read_bytes() for prefix in ('manga','outputs') for p in (self.root/prefix).rglob('*') if p.is_file()}

    def test_backup_contains_all_work_with_hidden_markers_and_no_keys_models_or_runtime(self):
        frame='outputs/sample/Chapter 1/narration/video-stage7/frames/'+('a'*64)+'.png'
        self.put(frame,b'cache frame')
        before=self.protected();name=self.backup();rows=m.inspect(name)
        names={r['path'] for r in rows}
        self.assertIn('manga/Sample/Chapter 1/.renumbering.json',names)
        self.assertIn('data/manga/sample/story-stage5.json',names)
        self.assertIn('outputs/Videos/Sample/Chapter1-Video.mp4',names)
        self.assertNotIn(frame,names)
        self.assertFalse(any('.env' in p or 'server.lock' in p or p.startswith(('vendor/','.cache/','data/maintenance/')) for p in names))
        with zipfile.ZipFile(m.archive_path(name)) as archive:
            for row in rows:self.assertEqual(hashlib.sha256(archive.read(row['path'])).hexdigest(),row['sha256'])
        self.assertEqual(before,self.protected())

    def test_default_restore_adds_missing_and_keeps_identical_and_extra_files(self):
        name=self.backup();image=self.root/'manga/Sample/Chapter 1/0000.png';image.unlink()
        extra=self.put('manga/Sample/Chapter 1/new.png',b'user extra')
        plan=m.restore_plan(name,False);job=self.execute('restore',plan)
        self.assertEqual(job['status'],'complete',job.get('message'));self.assertEqual(image.read_bytes(),b'original image');self.assertEqual(extra.read_bytes(),b'user extra')

    def test_conflicting_restore_blocked_by_default(self):
        name=self.backup();image=self.put('manga/Sample/Chapter 1/0000.png',b'new manual version')
        plan=m.restore_plan(name,False)
        response=self.client.post('/api/storage/restore',json={'id':plan['id'],'token':plan['token'],'confirm':True})
        self.assertEqual(response.status_code,409,response.text);self.assertEqual(image.read_bytes(),b'new manual version');self.assertIsNone(m.active)

    def test_explicit_replacement_keeps_original_in_verified_safety_copy(self):
        name=self.backup();image=self.put('manga/Sample/Chapter 1/0000.png',b'new manual version')
        plan=m.restore_plan(name,True);job=self.execute('restore',plan)
        self.assertEqual(job['status'],'complete',job.get('message'));self.assertEqual(image.read_bytes(),b'original image')
        self.assertEqual((self.root/'backups/restore-safety'/job['id']/'manga/Sample/Chapter 1/0000.png').read_bytes(),b'new manual version')

    def test_failed_restore_rolls_back_already_published_files(self):
        name=self.backup();path=self.put('data/manga/sample/story-stage5.json',b'{"manual":"keep"}')
        plan=m.restore_plan(name,True);progress=m.progress
        def fail(job,message,completed=None):
            if message.startswith('Restoring '):raise OSError('Injected write failure')
            progress(job,message,completed)
        with patch.object(m,'progress',side_effect=fail):job=self.execute('restore',plan)
        self.assertEqual(job['status'],'failed');self.assertEqual(path.read_bytes(),b'{"manual":"keep"}');self.assertFalse(m.recovery_required)

    def test_changed_workspace_and_changed_archive_invalidate_preview(self):
        name=self.backup();plan=m.restore_plan(name,True)
        self.put('manga/Sample/Chapter 1/0000.png',b'changed after preview')
        job=self.execute('restore',plan);self.assertEqual(job['status'],'failed')
        self.assertEqual((self.root/'manga/Sample/Chapter 1/0000.png').read_bytes(),b'changed after preview')
        plan=m.restore_plan(name,True)
        with m.archive_path(name).open('ab') as stream:stream.write(b'changed')
        self.assertEqual(self.execute('restore',plan)['status'],'failed')

    def test_backup_source_change_never_publishes_a_finished_archive(self):
        progress=m.progress
        def change(job,message,completed=None):
            progress(job,message,completed)
            self.put('data/new.json',b'new work')
        job=self.job('backup')
        with patch.object(m,'progress',side_effect=change):m.run(job)
        self.assertEqual(job['status'],'failed');self.assertEqual(m.backups(),[])

    def test_cleanup_targets_only_rebuildable_cache_and_never_work_or_backups(self):
        self.backup();frame='outputs/sample/Chapter 1/narration/video-stage7/frames/'+('a'*64)+'.png'
        self.put(frame,b'derived frame');self.put('outputs/sample/Chapter 1/panels/crops/p0000-1.png',b'crop')
        before=self.protected();narration=(self.root/'data/manga/sample/story-stage5.json').read_bytes()
        plan=m.cleanup_plan();self.assertEqual({r['path'] for r in plan['files']},{'.cache/pip/download.bin',frame})
        job=self.execute('cleanup',plan);self.assertEqual(job['status'],'complete',job.get('message'))
        after=self.protected();self.assertEqual({k:v for k,v in before.items() if k!=frame},after)
        self.assertEqual((self.root/'data/manga/sample/story-stage5.json').read_bytes(),narration);self.assertEqual(len(m.backups()),1)

    def test_cleanup_changed_cache_or_forged_plan_cannot_delete_original(self):
        plan=m.cleanup_plan();self.put('.cache/pip/download.bin',b'new cache')
        job=self.execute('cleanup',plan);self.assertEqual(job['status'],'failed');self.assertTrue((self.root/'.cache/pip/download.bin').exists())
        original='manga/Sample/Chapter 1/0000.png';path=self.root/original
        malicious={'files':[{'path':original,'size':path.stat().st_size,'sha256':m.checksum(path)}]}
        self.assertEqual(self.execute('cleanup',malicious)['status'],'failed');self.assertTrue(path.exists())

    def test_confirmation_and_plan_integrity_are_required(self):
        plan=m.cleanup_plan()
        response=self.client.post('/api/storage/cleanup',json={'id':plan['id'],'token':plan['token']})
        self.assertEqual(response.status_code,400)
        plan['files']=[];storage.write_json(m.plan_path(plan['id']),plan)
        with self.assertRaises(FileExistsError):m.load_plan(plan['id'],plan['token'],'cleanup')

    def test_archive_paths_unknown_entries_and_duplicate_case_are_rejected(self):
        for paths in (['../escape'],['.env'],['data/A.json','data/a.json'],['data/server.lock'],['data//alias.json']):
            name=uuid.uuid4().hex+'.zip';path=self.root/'backups'/name;path.parent.mkdir(exist_ok=True)
            rows=[{'path':p,'size':1,'sha256':hashlib.sha256(b'x').hexdigest()} for p in paths]
            with zipfile.ZipFile(path,'w') as archive:
                for p in paths:archive.writestr(p,b'x')
                archive.writestr('backup-manifest.json',json.dumps({'format':'manga-recap-backup-v1','files':rows}))
            with self.assertRaises(ValueError):m.inspect(name)

    def test_corrupt_payload_is_verified_before_any_workspace_change(self):
        name=self.backup();path=m.archive_path(name)
        with zipfile.ZipFile(path) as archive:contents={n:archive.read(n) for n in archive.namelist()}
        key='manga/Sample/Chapter 1/0000.png';contents[key]=b'bad data bytes' # same declared length
        rows=json.loads(contents['backup-manifest.json']);row=next(r for r in rows['files'] if r['path']==key);row['size']=len(contents[key])
        contents['backup-manifest.json']=json.dumps(rows).encode()
        with zipfile.ZipFile(path,'w') as archive:
            for n,data in contents.items():archive.writestr(n,data)
        before=self.protected();job=self.execute('restore',m.restore_plan(name,False))
        self.assertEqual(job['status'],'failed');self.assertEqual(before,self.protected())

    def test_interrupted_restore_recovery_rolls_back_owned_files_only(self):
        path=self.root/'manga/Sample/Chapter 1/0000.png';old=path.read_bytes();job=self.job('restore')
        self.put('backups/restore-safety/'+job['id']+'/manga/Sample/Chapter 1/0000.png',old)
        path.write_bytes(b'restored');s=path.stat()
        job['journal']=[{'path':path.relative_to(self.root).as_posix(),'before':hashlib.sha256(old).hexdigest(),'sha256':m.checksum(path),'identity':[s.st_dev,s.st_ino]}]
        m.save(job);m.recover();self.assertEqual(path.read_bytes(),old);self.assertEqual(storage.read_json(m.job_path(job['id']))['status'],'paused')

    def test_outside_edit_is_kept_and_blocks_further_processing_until_recovered(self):
        path=self.root/'manga/Sample/Chapter 1/0000.png';job=self.job('restore')
        job['journal']=[{'path':path.relative_to(self.root).as_posix(),'before':'a'*64,'sha256':'b'*64,'identity':[0,0]}]
        m.save(job);m.recover();self.assertTrue(m.recovery_required);self.assertEqual(path.read_bytes(),b'original image')
        from app.tasks import ensure_idle
        with self.assertRaises(FileExistsError):ensure_idle()

    def test_no_space_and_stop_leave_originals_untouched(self):
        before=self.protected()
        with patch.object(m.shutil,'disk_usage',return_value=type('Disk',(),{'free':0})()):
            job=self.job('backup');m.run(job);self.assertEqual(job['status'],'failed')
        m.cancel.set();job=self.job('backup');m.run(job);self.assertEqual(job['status'],'paused');self.assertEqual(before,self.protected())

    def test_storage_usage_does_not_follow_model_cache_links(self):
        target=self.root/'vendor/model.bin';link=self.root/'vendor/link.bin'
        try:link.symlink_to(target)
        except OSError:self.skipTest('Symlinks not available')
        value=m.usage();self.assertEqual(next(c['bytes'] for c in value['categories'] if c['path']=='vendor'),len(b'weights'))

    def test_cleanup_and_backup_reject_symlink_inputs(self):
        path=self.root/'.cache/pip/link';path.parent.mkdir(parents=True,exist_ok=True)
        try:path.symlink_to(self.root/'.env')
        except OSError:self.skipTest('Symlinks not available')
        with self.assertRaises(ValueError):m.cleanup_plan()

    def test_other_heavy_tasks_are_blocked_by_storage(self):
        from app.tasks import ensure_idle
        m.active={'id':'a'*32}
        with self.assertRaises(FileExistsError):ensure_idle()

    def test_other_app_writes_are_blocked_while_storage_owns_workspace(self):
        m.active={'id':'a'*32}
        before=self.protected()
        response=self.client.post('/api/manga',json={'name':'Must not be created','folder':'manga/Sample','direction':'rtl'})
        self.assertEqual(response.status_code,409,response.text)
        self.assertIn('Backup, restore or cleanup',response.json()['detail'])
        self.assertEqual(before,self.protected())

    def test_identical_outside_file_created_during_restore_is_never_removed(self):
        name=self.backup();image=self.root/'manga/Sample/Chapter 1/0000.png';image.unlink()
        plan=m.restore_plan(name,False)
        def outside_file(source,target):
            target.write_bytes(source.read_bytes())
            raise FileExistsError('An outside application created this destination')
        with patch.object(m.os,'link',side_effect=outside_file):job=self.execute('restore',plan)
        self.assertEqual(job['status'],'failed')
        self.assertTrue(m.recovery_required)
        self.assertEqual(image.read_bytes(),b'original image')
