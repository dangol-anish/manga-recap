"""Offline checks; never fetch provider documentation or inspect user assets."""
from pathlib import Path
import re
import unittest
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]


class DocumentationTests(unittest.TestCase):
    def test_relative_markdown_links_point_to_existing_repo_files(self):
        for page in [ROOT/'README.md', *sorted((ROOT/'docs').glob('*.md'))]:
            for raw in re.findall(r'(?<!!)\[[^\]]+\]\(([^)]+)\)',page.read_text(encoding='utf-8')):
                target=raw.strip('<>')
                parsed=urlsplit(target)
                if parsed.scheme or target.startswith(('#','//')):continue
                path=(page.parent/unquote(parsed.path)).resolve()
                with self.subTest(page=page.name,link=target):
                    self.assertTrue(path.is_relative_to(ROOT),'Documentation link escapes the repository')
                    self.assertTrue(path.exists(),'Missing documentation target')

    def test_current_guides_use_current_workflow_labels_and_branch(self):
        readme=(ROOT/'README.md').read_text()
        shorts=(ROOT/'docs/SHORTS.md').read_text()
        workflow=(ROOT/'docs/WORKFLOW.md').read_text()
        for text in [readme,shorts,workflow]:
            self.assertNotIn('**Build video preview**',text)
            self.assertNotIn('**Export MP4**',text)
            self.assertNotIn('on `codex/shorts-workflow`',text)
        self.assertIn('scripts.run_tests',readme)
        self.assertIn('Existing jobs, projects and recordings retain',readme)
