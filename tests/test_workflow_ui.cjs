const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.events = {}; this.dataset = {}; this.value = ''; }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this.children = items; }
  addEventListener(name, callback) { this.events[name] = callback; }
  setAttribute(name, value) { this[name] = value; }
}
const tick = () => new Promise(resolve => setImmediate(resolve));
(async () => {
  const host = new Element('details'), calls = [];
  let fail = false;
  const context = vm.createContext({ URLSearchParams, location: { search: '?manga=m&chapter=Chapter%201' },
    document: { querySelector: () => host, createElement: tag => new Element(tag) },
    fetch: async (url, options) => {
      calls.push({url, options});
      if (fail) throw Error('Connection lost. Keep the app running and refresh.');
      return { ok: true, json: async () => url.endsWith('/catalog') ?
        [{ id: 'm', name: '<unsafe>', chapters: [{ id: 'c', name: 'Chapter 1' }] }] :
        { complete: false, next: { stage: 5, name: 'Narration', detail: 'Import batch-002 next.', url: '/story?manga=m&chapter=Chapter%201' },
          note: 'Checking never starts processing.', steps: [{ stage: 4, name: 'Prepared panels', state: 'ready', detail: 'Current.', url: '/panels' },
            { stage: 5, name: 'Narration', state: 'next', detail: 'Import batch-002.', url: '/story' }] } };
    } });
  vm.runInContext(fs.readFileSync('app/static/workflow.js', 'utf8'), context);
  assert.equal(calls.length, 0, 'Closed progress must not scan files');
  host.open = true; host.events.toggle(); await tick(); await tick();
  assert.equal(calls.length, 2);
  assert.equal(calls[1].url, '/api/workflow/m/c');
  const body = host.children[1], pickers = body.children[0], message = body.children[1], results = body.children[2];
  assert.match(results.children[0].children[0].children[0].textContent, /Next: Stage 5/);
  assert.match(results.children[0].children[1].href, /chapter=Chapter%201/);
  assert.equal(pickers.children[0].children[1].children[1].textContent, '<unsafe>');
  host.events.toggle(); await tick(); assert.equal(calls.length, 2, 'No automatic polling');
  fail = true; await pickers.children[2].events.click();
  assert.match(message.textContent, /Connection lost/);
  assert(calls.every(call => !call.options?.method), 'Readiness never writes or starts jobs');
  for (const name of ['downloader', 'renumbering', 'detection', 'panel_preparation', 'story', 'speech', 'cinema', 'stitch', 'shorts']) {
    const html = fs.readFileSync(`app/static/${name}.html`, 'utf8');
    assert(html.includes('data-chapter-progress'), name);
    assert(html.indexOf('/static/workflow.css') < html.indexOf('/static/controls.css'), name);
  }
  console.log('Chapter progress UI tests passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
