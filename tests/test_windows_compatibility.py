"""Portable fixtures plus explicit Windows API branches; no user media."""
import ctypes
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from contextlib import ExitStack

from app import runtime, storage, video, read_cache
from app.chapter_names import chapter_folder_name


class WindowsCompatibilityTests(unittest.TestCase):
    def test_fresh_checkout_starts_all_current_stages_without_local_assets(self):
        from fastapi.testclient import TestClient
        from app import main
        with tempfile.TemporaryDirectory(dir=storage.ROOT) as temp, ExitStack() as stack:
            root=Path(temp)
            for module in (storage,main):
                stack.enter_context(patch.object(module,'DATA',root/'data'))
                stack.enter_context(patch.object(module,'OUTPUTS',root/'outputs'))
            stack.enter_context(patch.object(storage,'ROOT',root))
            with TestClient(main.app) as client:
                for path in ('/','/renumbering','/detection','/panels','/story','/speech','/cinema','/stitch'):
                    response=client.get(path)
                    self.assertEqual(response.status_code,200,(path,response.text))
                for path in ('/api/downloader/jobs','/api/renumbering/chapters','/api/preparation/catalog','/api/story/catalog','/api/speech/catalog','/api/cinema/catalog'):
                    response=client.get(path)
                    self.assertEqual(response.status_code,200,(path,response.text))
                    self.assertEqual(response.json(),[])
                self.assertEqual(client.get('/api/detection/catalog').json()['manga'],[])
            self.assertFalse((root/'manga').exists())
            self.assertFalse((root/'outputs').exists())

    def test_windows_execution_state_is_released_even_after_an_error(self):
        setter = Mock(return_value=1)
        dll = SimpleNamespace(kernel32=SimpleNamespace(SetThreadExecutionState=setter))
        with patch.object(runtime.sys, 'platform', 'win32'), patch.object(ctypes, 'windll', dll, create=True):
            with self.assertRaisesRegex(ValueError, 'fixture'):
                with runtime.keep_awake(): raise ValueError('fixture')
        self.assertEqual([c.args for c in setter.call_args_list], [(0x80000001,), (0x80000000,)])

    def test_failed_windows_keep_awake_is_reported(self):
        dll = SimpleNamespace(kernel32=SimpleNamespace(SetThreadExecutionState=Mock(return_value=0)))
        with patch.object(runtime.sys, 'platform', 'win32'), patch.object(ctypes, 'windll', dll, create=True):
            with self.assertWarnsRegex(RuntimeWarning, 'prevent idle sleep'):
                with runtime.keep_awake(): pass

    def test_windows_opens_explorer_without_a_shell_or_path_interpolation(self):
        folder = Path('Manga café & notes')
        with patch.object(runtime.sys, 'platform', 'win32'), patch.object(runtime.os, 'startfile', create=True) as opener:
            runtime.open_system_folder(folder)
            opener.assert_called_once_with(str(folder))

    def test_utf8_json_works_independently_of_the_system_locale(self):
        with tempfile.TemporaryDirectory(dir=storage.ROOT) as temp:
            path = Path(temp) / '日本語.json'
            path.write_bytes('{"name":"日本語 — Rosé"}'.encode('utf-8'))
            self.assertEqual(storage.read_json(path)['name'], '日本語 — Rosé')
            storage.write_json(path, {'name': '日本語 — Rosé'})
            self.assertEqual(json.loads(path.read_bytes()), {'name': '日本語 — Rosé'})

    def test_transient_windows_sharing_conflict_retries_but_preserves_atomicity(self):
        with tempfile.TemporaryDirectory(dir=storage.ROOT) as temp:
            path=Path(temp)/'state.json';storage.write_json(path,{'old':True})
            replace=storage.os.replace;calls=[]
            def conflict(source,target):
                calls.append(True)
                if len(calls)<3:
                    self.assertEqual(storage.read_json(path),{'old':True})
                    error=PermissionError('file in use');error.winerror=32;raise error
                replace(source,target)
            with patch.object(storage.os,'replace',side_effect=conflict),patch.object(storage.time,'sleep'):
                storage.write_json(path,{'new':True})
            self.assertEqual(storage.read_json(path),{'new':True});self.assertEqual(len(calls),3)
            error=PermissionError('still in use');error.winerror=32
            with patch.object(storage.os,'replace',side_effect=error),patch.object(storage.time,'sleep'):
                with self.assertRaises(PermissionError):storage.write_json(path,{'lost':True})
            self.assertEqual(storage.read_json(path),{'new':True})
            self.assertEqual(list(path.parent.glob('*.tmp')),[])

    def test_windows_hash_checks_mid_read_changes_without_trusting_cross_request_cache(self):
        with tempfile.TemporaryDirectory(dir=storage.ROOT) as temp:
            path = Path(temp) / 'image'; path.write_bytes(b'one')
            def change(file):
                file.write_bytes(b'changed during read'); return 'ignored'
            with patch.object(read_cache.sys, 'platform', 'win32'), patch.object(read_cache, '_hash_file', side_effect=change):
                with self.assertRaises(FileExistsError): read_cache.file_hash(path)

    def test_chapter_fallbacks_are_legal_windows_names(self):
        for name in ('CON','aux','LPT1','NUL.extra','COM3'):
            with self.subTest(name=name): self.assertTrue(chapter_folder_name(name).startswith('_'))
        self.assertFalse(chapter_folder_name('a'*79+' . ').endswith(('.', ' ')))
        self.assertEqual(chapter_folder_name('Chapter 02.50'), 'Chapter 2.5')

    def test_server_lock_is_exclusive_across_processes_and_released(self):
        with tempfile.TemporaryDirectory(dir=storage.ROOT) as temp:
            path=Path(temp)/'lock'
            program = 'from app.runtime import acquire_server_lock; import sys; acquire_server_lock(sys.argv[1]).close()'
            handle=runtime.acquire_server_lock(path)
            try:
                result=subprocess.run([sys.executable,'-c',program,str(path)],cwd=storage.ROOT,capture_output=True,timeout=15)
                self.assertNotEqual(result.returncode,0)
            finally:handle.close()
            subprocess.run([sys.executable,'-c',program,str(path)],cwd=storage.ROOT,capture_output=True,timeout=15,check=True)

    def test_full_chapter_render_passes_a_small_command_with_a_large_graph_file(self):
        with tempfile.TemporaryDirectory(dir=storage.ROOT) as temp:
            work=Path(temp);crop=work/'crop.png'
            from PIL import Image
            Image.new('RGB',(16,16)).save(crop)
            panels=[{'id':f'p{i:04d}-1','_crop':str(crop)} for i in range(116)]
            state={'duration':116,'beats':[{'duration':116,'switches':list(range(1,116)),'panels':panels,'_audio':None}]}
            commands=[]
            def execute(command, **kwargs):
                commands.append(command)
                if command[0]=='ffprobe':
                    return SimpleNamespace(stdout=json.dumps({'streams':[{'codec_type':'video','duration':'116'},{'codec_type':'audio','duration':'116'}],'format':{'duration':'116'}}))
                self.assertLess(len(subprocess.list2cmdline(command)),8000)
                self.assertNotIn('-filter_complex',command)
                graph=Path(command[command.index('-/filter_complex')+1])
                self.assertGreater(graph.stat().st_size,32767)
                return SimpleNamespace(returncode=0)
            with patch.object(video,'ensure_frame',return_value=crop),patch.object(video.subprocess,'run',side_effect=execute):
                video.render_to(state,work)
            self.assertEqual(len(commands),2)

    def test_windows_symlink_privilege_skip_does_not_hide_other_errors(self):
        from tests.support import create_symlink
        path=Mock();error=OSError('requires privilege');error.winerror=1314
        path.symlink_to.side_effect=error
        with self.assertRaises(unittest.SkipTest):create_symlink(path,'target')
        path.symlink_to.side_effect=OSError('disk failed')
        with self.assertRaisesRegex(OSError,'disk failed'):create_symlink(path,'target')
