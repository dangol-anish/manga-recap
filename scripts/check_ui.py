"""Render isolated copies of every app page and verify shared controls/layout.

Development only: no app startup, real APIs, user data or signed-in browser.
Run after installing requirements-ui.txt and the selected Playwright browsers.
"""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import threading
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / 'app/static'
os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH', str(ROOT / '.cache/ui-browsers'))

DYNAMIC = '''<section class="surface qa-dynamic" aria-label="Dynamic control examples">
<h2>Dynamic controls</h2><div class="workflow-pickers"><div><label for="qa-select">Chapter progress</label><select id="qa-select"><option value="one">A very long manga and chapter title that must never push its arrow outside the field</option><option value="two">Chapter 2</option></select></div><button id="qa-next" type="button">Refresh progress</button></div>
<div class="quality-picker"><div><label for="qa-disabled">Unavailable section</label><select id="qa-disabled" disabled><option>No matching audio yet</option></select></div></div>
<label for="qa-text">Pronunciation hint</label><input id="qa-text" type="text" placeholder="Character name">
<label class="check-label"><input id="qa-check" type="checkbox">Select this chapter</label>
<div class="short-review"><label>Inspect segment<select id="qa-review"><option>Segment 1 · Chapter 100 · p0000-1</option></select></label></div>
</section>'''


def fixture(name):
    path = STATIC / name
    if path.parent != STATIC or path.suffix != '.html' or not path.is_file():
        raise ValueError('Unknown UI fixture')
    text = path.read_text(encoding='utf-8')
    # The real markup and stylesheet order are tested; production scripts and
    # saved media never run or load in this isolated HTTP server.
    text = re.sub(r'<script\b[^>]*>.*?</script>', '', text, flags=re.S | re.I)
    text = text.replace('</main>', DYNAMIC + '</main>')
    return text.encode('utf-8')


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        path = unquote(urlsplit(self.path).path)
        try:
            if path.startswith('/fixture/'):
                content = fixture(path.removeprefix('/fixture/'))
                kind = 'text/html; charset=utf-8'
            elif path.startswith('/static/'):
                file = STATIC / path.removeprefix('/static/')
                if file.parent != STATIC or file.suffix != '.css':
                    raise ValueError('Only page styles are served')
                content = file.read_bytes()
                kind = 'text/css; charset=utf-8'
            else:
                raise ValueError('No app APIs or media are available in UI fixtures')
            self.send_response(200)
            self.send_header('Content-Type', kind)
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Security-Policy', "default-src 'self' data:; script-src 'none'; style-src 'self' 'unsafe-inline'; connect-src 'none'")
            self.end_headers()
            self.wfile.write(content)
        except (ValueError, OSError):
            self.send_error(404)


CHECK = '''async () => {
 const visible = e => e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden';
 const issues=[], selects=[...document.querySelectorAll('select:not([multiple])')].filter(visible);
 for(const e of selects){e.scrollIntoView({block:'center'});await new Promise(requestAnimationFrame);const s=getComputedStyle(e), r=e.getBoundingClientRect(),name=e.id||e.name||e.outerHTML.slice(0,60);
   if(Math.abs(r.height-44)>.1)issues.push(`${name}: height ${r.height}, expected 44`);
   for(const [key,expected] of Object.entries({fontSize:'13px',borderRadius:'8px',paddingRight:'44px',paddingLeft:'13px'}))
     if(s[key]!==expected)issues.push(`${name}: ${key}=${s[key]}, expected ${expected}`);
   if(!['calc(100% - 14px) 50%','right 14px 50%','right 14px center'].includes(s.backgroundPosition))issues.push(`${name}: arrow inset ${s.backgroundPosition}`);
   if(parseFloat(s.borderTopWidth)!==1)issues.push(`${name}: missing standard border`);
   if(r.width<=44||r.right>innerWidth+1||r.left< -1)issues.push(`${name}: field is clipped or outside viewport`);
   if(s.backgroundColor==='rgb(255, 255, 255)'||s.color==='rgb(0, 0, 0)')issues.push(`${name}: unexpected light theme`);
 }
 const text=document.querySelector('#qa-text'),checkbox=document.querySelector('#qa-check');text.scrollIntoView({block:'center'});await new Promise(requestAnimationFrame);
 if(text.getBoundingClientRect().height<44)issues.push('Text input is too short');
 if(checkbox.getBoundingClientRect().height!==16)issues.push('Checkbox inherited text-field dimensions');
 if(document.documentElement.scrollWidth>innerWidth+1)issues.push(`Horizontal page overflow: ${document.documentElement.scrollWidth} > ${innerWidth}`);
 for(const e of [...document.querySelectorAll('button')].filter(visible)){
   e.scrollIntoView({block:'center'});await new Promise(requestAnimationFrame);
   if(e.getBoundingClientRect().height<43.9)issues.push(`Small button: ${e.id||e.textContent.trim()}`);
 }
 return {issues,selects:selects.length};
}'''


def run(browsers, output, screenshots=False):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print('Install development browser tools: python -m pip install -r requirements-ui.txt')
        return 1
    output.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    records = []
    try:
        with sync_playwright() as runtime:
            for engine in browsers:
                print(f'Checking {engine}: all pages at desktop, tablet and phone widths',flush=True)
                browser = getattr(runtime, engine).launch()
                try:
                    page = None
                    for path in sorted(STATIC.glob('*.html')):
                        for width in [1280, 768, 390]:
                            if page is not None:page.close()
                            page = browser.new_page(viewport={'width':width,'height':900},color_scheme='dark')
                            page.goto(f'http://127.0.0.1:{server.server_port}/fixture/{path.name}', wait_until='networkidle')
                            # Reveal relevant ready-state fields, without running application JS.
                            page.evaluate('''() => {
                              for(const e of document.querySelectorAll('[hidden]'))if(e.querySelector('select'))e.hidden=false;
                              for(const e of document.querySelectorAll('details'))e.open=true;
                            }''')
                            result = page.evaluate(CHECK)
                            loaded=page.evaluate('''() => [...document.querySelectorAll('link[rel=stylesheet]')].filter(e=>!e.sheet).map(e=>e.href)''')
                            result['issues'].extend('Stylesheet did not load: '+url for url in loaded)
                            records.append({'browser':engine,'page':path.name,'width':width,**result})
                            if result['issues'] or screenshots and width == 1280:
                                page.screenshot(path=str(output/f'{engine}-{path.stem}-{width}.png'), full_page=True)
                            if screenshots and path.name=='speech.html' and width==1280:
                                page.locator('.group-grid').screenshot(path=str(output/f'{engine}-speech-controls.png'))
                            if result['issues']:
                                print(engine, path.name, width, '\n  ' + '\n  '.join(result['issues']),flush=True)
                    # Standard select/keyboard semantics must remain native.
                    field = page.locator('#qa-select')
                    field.select_option('two')
                    assert field.input_value() == 'two'
                    field.focus()
                    page.keyboard.press('Alt+Tab' if engine=='webkit' else 'Tab')
                    focused=page.evaluate('document.activeElement.id')
                    assert focused=='qa-next', f'{engine}: keyboard focus moved to {focused!r}'
                    page.emulate_media(forced_colors='active')
                    style = field.evaluate('(e)=>({appearance:getComputedStyle(e).appearance,image:getComputedStyle(e).backgroundImage})')
                    assert style['image'] == 'none' and style['appearance'] == 'auto', style
                    page.close()
                finally:
                    browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
        (output/'results.json').write_text(json.dumps(records,indent=2),encoding='utf-8')
    failures = sum(bool(row['issues']) for row in records)
    print(f'Browser layout checks: {len(records)} page/viewport cases; {failures} failed. Report: {output}')
    return 1 if failures else 0


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--browsers',nargs='+',choices=['chromium','firefox','webkit'],default=['chromium'])
    parser.add_argument('--output',type=Path,default=ROOT/'reports/ui')
    parser.add_argument('--screenshots',action='store_true')
    args=parser.parse_args()
    raise SystemExit(run(args.browsers,args.output,args.screenshots))
