from contextlib import redirect_stdout
from io import StringIO
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from scripts import run_tests
from app import errors


class TestRunnerTests(unittest.TestCase):
    def invoke(self):
        with redirect_stdout(StringIO()) as stream:
            result=run_tests.main()
        return result,stream.getvalue()

    def test_missing_tools_fail_before_any_subprocess(self):
        with patch.object(run_tests.shutil,'which',return_value=None),patch.object(run_tests.subprocess,'run') as execute:
            code,message=self.invoke()
        self.assertEqual(code,1);execute.assert_not_called()
        self.assertIn('node, ffmpeg, ffprobe',message)

    def test_runs_python_and_every_ui_script_from_repo_root_without_shell(self):
        with patch.object(run_tests.shutil,'which',side_effect=lambda name:'/tools/'+name),patch.object(run_tests.subprocess,'run',return_value=SimpleNamespace(returncode=0)) as execute:
            code,message=self.invoke()
        self.assertEqual(code,0)
        self.assertEqual(execute.call_count,1+len(list((run_tests.ROOT/'tests').glob('test_*.cjs'))))
        self.assertEqual(execute.call_args_list[0].args[0][:3],[run_tests.sys.executable,'-m','unittest'])
        for call in execute.call_args_list:
            self.assertEqual(call.kwargs,{'cwd':run_tests.ROOT,'check':False})
        self.assertIn('All checks passed',message)

    def test_failure_is_reported_but_other_independent_checks_still_run(self):
        with patch.object(run_tests.shutil,'which',side_effect=lambda name:'/tools/'+name),patch.object(run_tests.subprocess,'run',side_effect=[SimpleNamespace(returncode=3)]+[SimpleNamespace(returncode=0)]*100) as execute:
            code,message=self.invoke()
        self.assertEqual(code,1);self.assertGreater(execute.call_count,1)
        self.assertIn('Python regressions (exit 3)',message)

    def test_workflow_exception_mapping_preserves_status_and_details(self):
        for exc,code in [(FileExistsError('Changed source'),409),(FileNotFoundError('Missing input'),400),(PermissionError('Access denied'),400),(ValueError('Bad setting'),400)]:
            response=errors.request_error(exc)
            self.assertEqual(response.status_code,code);self.assertEqual(response.detail,str(exc))
