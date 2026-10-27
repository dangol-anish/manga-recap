"""Shared control coverage; browser geometry is checked by scripts.check_ui."""
from html.parser import HTMLParser
from pathlib import Path
import unittest
from scripts import check_ui

ROOT=Path(__file__).resolve().parents[1]


class Page(HTMLParser):
    def __init__(self,text):
        super().__init__();self.theme=None;self.styles=[];self.feed(text)
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag=='html':self.theme=attrs.get('data-theme')
        if tag=='link' and attrs.get('rel')=='stylesheet':self.styles.append(attrs['href'].split('?')[0])


class SharedControlTests(unittest.TestCase):
    def test_every_page_uses_dark_theme_and_shared_styles_last(self):
        pages=list((ROOT/'app/static').glob('*.html'));self.assertGreaterEqual(len(pages),17)
        for path in pages:
            with self.subTest(page=path.name):
                page=Page(path.read_text(encoding='utf-8'))
                self.assertEqual(page.theme,'dark')
                self.assertEqual(page.styles[-2:],['/static/layout.css','/static/controls.css'])
                self.assertEqual(page.styles.count('/static/controls.css'),1)

    def test_browser_fixtures_cannot_start_application_scripts(self):
        for path in (ROOT/'app/static').glob('*.html'):
            text=check_ui.fixture(path.name).decode()
            self.assertNotIn('<script',text.lower())
            self.assertIn('qa-select',text)
        for name in ['../storage.py','../main.py','bad.html','speech.html/../../main.py']:
            with self.assertRaises(ValueError):check_ui.fixture(name)

    def test_control_contract_includes_full_geometry_and_accessibility(self):
        css=(ROOT/'app/static/controls.css').read_text()
        for rule in ['--mr-control-height: 44px','--mr-control-radius: 8px','--mr-control-font-size: 13px','padding-inline-end: 44px','right 14px center','forced-colors: active','forced-color-adjust: auto',':focus-visible','select option']:
            self.assertIn(rule,css)
        self.assertNotIn('!important',css)
