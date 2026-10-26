const assert = require('node:assert/strict'), fs = require('node:fs'), vm = require('node:vm');
class Element {
  constructor(tag='div') { this.tag=tag; this.children=[]; this.events={}; this.value=''; this.hidden=true; }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this.children=items; }
  addEventListener(name, callback) { this.events[name]=callback; }
  get options() { return this.children; }
  querySelectorAll(tag) { return this.children.flatMap(c=>[...(c.tag===tag?[c]:[]), ...c.querySelectorAll(tag)]); }
}
(async () => {
  const nodes=new Map(), get=id=>{if(!nodes.has(id))nodes.set(id,new Element());return nodes.get(id);};
  const calls=[]; let fail=false, pending=null;
  const value={batch_id:'batch-001',token:'token',selected_panels:3,skipped_panels:2,beats:[
    {number:1,narration:'An event. Another event.',unsure:'Identity <unknown>.',silent:false,panels:[
      {id:'p0000-1',page:'0000.png',narration:'An event. '},{id:'p0000-2',page:'0000.png',narration:'Another event.'}]},
    {number:2,narration:'',silent:true,unsure:'',panels:[{id:'p0001-1',page:'0001.png',narration:''}]}]};
  const context=vm.createContext({document:{getElementById:get,createElement:tag=>new Element(tag)},fetch:async(url,options)=>{
    calls.push({url,options}); if(pending)await pending;
    return {ok:!fail,json:async()=>fail?{detail:'Saved narration changed. Refresh this chapter.'}:value};
  }});
  vm.runInContext(fs.readFileSync('app/static/story_preview.js','utf8'),context);
  const update=()=>context.StoryPreview.update({batches:[{id:'batch-001',imported:true,beat_count:2},{id:'batch-002',imported:false,stale:true}]},'m','Chapter 1');
  update(); assert.equal(calls.length,0,'Selecting chapter must not fetch preview or start generation');
  assert.equal(get('preview-batch').options.length,2,'Only current imported batches are available');
  await get('preview-open').events.click(); assert.equal(get('preview-content').hidden,false);
  assert.equal(get('preview-story').querySelectorAll('img').length,2);
  assert.equal(get('preview-story').querySelectorAll('p')[1].textContent,'Identity <unknown>.','Uncertainty uses safe text');
  assert.equal(get('preview-previous').disabled,true);
  assert.match(get('preview-story').querySelectorAll('img')[0].src,/token=token/);
  get('preview-next').events.click();assert.equal(get('preview-next').disabled,true);
  assert.match(get('preview-story').querySelectorAll('p')[0].textContent,/Silent visual/);
  get('preview-story').querySelectorAll('img')[0].events.error();
  assert.equal(get('preview-story').querySelectorAll('p')[1].hidden,false,'Image failure is visible');
  get('preview-previous').events.click();assert.equal(get('preview-story').querySelectorAll('img').length,2);
  fail=true;await get('preview-open').events.click();assert.equal(get('preview-content').hidden,true);
  assert.match(get('preview-message').textContent,/Saved narration changed/);
  fail=false;let release;pending=new Promise(r=>release=r);
  const load=get('preview-open').events.click();context.StoryPreview.reset();release();await load;
  assert.equal(get('preview-content').hidden,true,'Late response cannot restore another chapter');
  pending=null;update();await get('preview-open').events.click();update();
  assert.equal(get('preview-content').hidden,true,'Reimport invalidates visible preview');
  assert(calls.every(c=>!c.options?.method),'Review never sends writes or speech requests');
  console.log('Story preview UI: navigation, uncertainty, failures, stale responses and read-only behavior passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
