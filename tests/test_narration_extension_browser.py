"""Actual unpacked extension, isolated Chromium profile, synthetic site and loopback server."""
import base64
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class ExtensionBrowserTests(unittest.TestCase):
    def test_unpacked_extension_uploads_configures_and_submits_once(self):
        try: from playwright.sync_api import sync_playwright
        except ImportError: self.skipTest('Optional development Playwright is not installed; no download attempted.')
        root=Path(__file__).resolve().parents[1]
        os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH',str(root/'.cache/ui-browsers'))
        state={'command':None,'results':{},'polls':0,'permits':0,'requests':[]}
        mutex=threading.Lock()
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                body=json.loads(self.rfile.read(int(self.headers.get('Content-Length','0'))))
                state['requests'].append({'path':self.path,'authorized':self.headers.get('Authorization')=='Bearer synthetic-token'})
                pairing=self.path.endswith('/pair') and body.get('code')=='synthetic-code'
                if not pairing and self.headers.get('Authorization')!='Bearer synthetic-token':
                    self.send_response(403);self.end_headers();return
                with mutex:
                    if pairing:
                        value={'token':'synthetic-token'}
                    elif self.path.endswith('/poll'):
                        state['polls']+=1;command=state['command'];state['command']=None
                        value={'command':command}
                    elif self.path.endswith('/permit'):
                        state['permits']+=1;value={'permitted':True}
                    elif self.path.endswith('/result'):
                        state['results'][body['id']]=body['value'];value={'saved':True}
                    else:value={}
                payload=json.dumps(value).encode()
                self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as temporary, sync_playwright() as runtime:
                if not Path(runtime.chromium.executable_path).exists():self.skipTest('Development Chromium is not installed; no download attempted.')
                extension=Path(temporary)/'extension';shutil.copytree(root/'browser-extension',extension)
                for name in ('manifest.json','background.js'):
                    path=extension/name;path.write_text(path.read_text().replace('127.0.0.1:8000',f'127.0.0.1:{server.server_port}'))
                context=runtime.chromium.launch_persistent_context(str(Path(temporary)/'profile'),channel='chromium',headless=True,
                    args=[f'--disable-extensions-except={extension}',f'--load-extension={extension}'])
                try:
                    html='''<!doctype html><main>
<button>Gemini 3.8 Flash</button><div role="combobox" aria-label="Thinking Level">Medium</div>
<button role="switch" aria-label="Structured outputs" aria-checked="false" id="structured">Structured outputs</button>
<button aria-label="Edit JSON schema" id="edit">Edit schema</button><div id="editor" hidden><button role="tab">Code Editor</button><textarea aria-label="Enter a JSON schema"></textarea><button aria-label="Save the current schema" id="save">Save</button></div>
<textarea aria-label="Enter a prompt"></textarea><input type="file" multiple><button id="run">Run</button><section id="responses"></section>
</main><script>
window.sends=0;
document.querySelector('#structured').onclick=()=>{document.querySelector('#structured').setAttribute('aria-checked','true');document.querySelector('#editor').hidden=false;};
document.querySelector('#edit').onclick=()=>document.querySelector('#editor').hidden=false;
// Reproduce the actual AI Studio save normalization observed on 2026-10-30.
function normalizeSchema(s){if(s.properties){Object.values(s.properties).forEach(normalizeSchema);s.propertyOrdering=Object.keys(s.properties);}if(s.items)normalizeSchema(s.items);return s;}
document.querySelector('#save').onclick=()=>{const editor=document.querySelector('[aria-label="Enter a JSON schema"]');editor.value=JSON.stringify(normalizeSchema(JSON.parse(editor.value)));if(window.dropSchema)document.querySelector('[aria-label=\"Enter a JSON schema\"]').value=JSON.stringify({type:'object',properties:{response:{type:'string'}}});document.querySelector('#editor').hidden=true;};
document.querySelector('#run').onclick=()=>{window.sends++;document.querySelector('#responses').innerHTML='<article data-message-author-role="assistant"><pre>{"beats":[]}</pre><button>Good response</button><button>Bad response</button></article>';document.querySelector('#run').disabled=true;history.replaceState(null,'','/prompts/fixture');};
</script>'''
                    def route(route):
                        url=route.request.url
                        if url=='https://aistudio.google.com/prompts/fail-navigation':route.fulfill(status=302,headers={'Location':'https://aistudio.google.com/blocked'})
                        elif url.startswith('https://aistudio.google.com/'):route.fulfill(body=html,content_type='text/html')
                        elif url.startswith(f'http://127.0.0.1:{server.server_port}/') or url.startswith('chrome-extension://'):route.continue_()
                        else:route.abort()
                    context.route('**/*',route)
                    page=context.new_page();page.goto('https://aistudio.google.com/prompts/new_chat')
                    worker=context.service_workers[0] if context.service_workers else context.wait_for_event('serviceworker')
                    # Exercise the real popup's pairing handler, with only fixture data.
                    extension_id=worker.url.split('/')[2]
                    popup=context.new_page();popup.goto(f'chrome-extension://{extension_id}/popup.html')
                    popup.get_by_label('One-time pairing code').fill('synthetic-code')
                    page.bring_to_front()
                    popup.evaluate("document.querySelector('#connect').click()")
                    deadline=time.monotonic()+10
                    while time.monotonic()<deadline and 'Connected. Return' not in popup.locator('#status').inner_text():popup.wait_for_timeout(100)
                    self.assertIn('Connected. Return',popup.locator('#status').inner_text())
                    count=0
                    def command(action,payload=None,expired=False,ttl=240):
                        nonlocal count
                        count+=1;ident=f'{count:032x}'
                        if action=='prepare':payload={**payload,'schema':json.loads((root/'browser-extension/narration-schema.json').read_text())}
                        with mutex:state['command']={'id':ident,'action':action,'payload':payload or {},'expiresAt':int((time.time()+(-1 if expired else ttl))*1000)}
                        deadline=time.monotonic()+35
                        while time.monotonic()<deadline:
                            with mutex:value=state['results'].get(ident)
                            if value is not None:return value
                            popup.wait_for_timeout(100)
                        note=worker.evaluate("async()=> (await chrome.storage.local.get('connectionNote')).connectionNote")
                        self.fail(f'No extension result for {action}; state={state}; connection={note}')
                    self.assertIn('url',command('hello'))
                    photos=[{'name':name,'data':base64.b64encode(b'local synthetic image bytes').decode()} for name in ('p0000.jpg','p0001.jpg')]
                    result=command('prepare',{'fresh':True,'prompt':'Synthetic narration prompt','photos':photos})
                    self.assertNotIn('error',result,result)
                    self.assertEqual(page.get_by_label('Enter a prompt').input_value(),'Synthetic narration prompt')
                    self.assertEqual(page.locator('input[type=file]').evaluate('(e)=>[...e.files].map(f=>f.name)'),['p0000.jpg','p0001.jpg'])
                    self.assertEqual(page.get_by_role('switch',name='Structured outputs').get_attribute('aria-checked'),'true')
                    schema=json.loads(page.get_by_label('Enter a JSON schema').input_value())
                    self.assertEqual(schema['propertyOrdering'],list(schema['properties']));self.assertIn('propertyOrdering',schema['properties']['beats']['items']);self.assertIn('beats',schema['properties']);self.assertNotIn('response',schema['properties'])
                    self.assertEqual(page.evaluate('window.sends'),0)
                    self.assertNotIn('error',command('send'))
                    result=command('snapshot');self.assertTrue(result['finished']);self.assertEqual(result['replies'][0]['text'],'{"beats":[]}')
                    self.assertEqual(page.evaluate('window.sends'),1);self.assertEqual(state['permits'],1)
                    # Real AI Studio can collapse code and omit the Run control.
                    page.locator('#responses').evaluate('''e=>{e.innerHTML='<ms-chat-turn><ms-code-block style="display:contents"><button aria-label="Expand code snippet">Expand</button><pre hidden></pre></ms-code-block><button aria-label="Good response">thumb_up</button></ms-chat-turn>';e.querySelector('[aria-label="Expand code snippet"]').onclick=()=>{e.querySelector('pre').hidden=false;e.querySelector('pre').textContent='{"beats":[]}';e.querySelector('[aria-label="Expand code snippet"]').remove();};}''')
                    page.locator('#run').evaluate("e=>e.hidden=true")
                    collapsed=command('snapshot');self.assertTrue(collapsed['finished']);self.assertEqual(collapsed['replies'][-1]['text'],'{"beats":[]}')
                    page.locator('#run').evaluate("e=>e.hidden=false")
                    page.locator('#responses').evaluate('''e=>e.innerHTML='<article data-message-author-role="assistant"><pre>{"beats":[]}</pre><button>Good response</button></article>' ''')
                    # Prompt examples must never become model output.
                    page.locator('main').evaluate("e=>e.insertAdjacentHTML('afterbegin','<article data-message-author-role=\"user\"><pre>{\"beats\":[\"PROMPT EXAMPLE\"]}</pre></article>')")
                    self.assertEqual(len(command('snapshot')['replies']),1)
                    for reply in ('', 'ok', 'I cannot assist with this request.', '{broken', '{"response":""}', '{"response":"ok"}', '[]', 'null', '{"wrong":true}'):
                        page.locator('[data-message-author-role=assistant] pre').evaluate('(e,t)=>e.textContent=t',reply)
                        snapshot=command('snapshot');self.assertTrue(snapshot['finished']);self.assertEqual(snapshot['replies'][-1]['text'],reply)
                    page.locator('#run').evaluate("e=>{e.textContent='Stop';e.disabled=false;}")
                    snapshot=command('snapshot');self.assertTrue(snapshot['generating']);self.assertFalse(snapshot['finished'])
                    page.locator('#run').evaluate("e=>{e.textContent='Run';e.disabled=true;}")

                    self.assertIn('error',command('send',expired=True));self.assertEqual(page.evaluate('window.sends'),1)
                    # Fresh batches configure JSON again, without submitting automatically.
                    self.assertNotIn('error',command('prepare',{'fresh':True,'prompt':'Next batch','photos':[]}))
                    self.assertEqual(page.evaluate('window.sends'),0)
                    self.assertEqual(page.get_by_role('switch',name='Structured outputs').get_attribute('aria-checked'),'true')
                    page.locator('main').evaluate("e=>{const p=document.createElement('p');p.setAttribute('role','alert');p.textContent='You have reached your daily limit.';e.append(p);}")
                    result=command('send');self.assertEqual(result['code'],'limit');self.assertEqual(page.evaluate('window.sends'),0)
                    page.locator('[role=alert]').evaluate('(e)=>e.remove()')
                    page.evaluate('window.dropSchema=true')
                    failed=command('prepare',{'fresh':False,'prompt':'Must not submit','photos':[]})
                    self.assertEqual(failed.get('code'),'schema',failed);self.assertEqual(page.evaluate('window.sends'),0)
                    self.assertIn('error',command('recover',{'url':'https://evil.invalid/prompts/x'}))
                    failed=command('recover',{'url':'https://aistudio.google.com/prompts/fail-navigation'},ttl=3)
                    self.assertIn('error',failed);self.assertIn('navigation',failed['error'])
                    self.assertNotIn('error',command('recover',{'url':'https://aistudio.google.com/prompts/restored'}))
                    self.assertEqual(page.url,'https://aistudio.google.com/prompts/restored')
                    page.close();self.assertIn('error',command('hello'))

                finally:context.close()
        finally:server.shutdown();server.server_close()


if __name__=='__main__':unittest.main()
