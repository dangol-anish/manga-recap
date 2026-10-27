// Exercise the real client script with a minimal DOM, without opening a browser.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
class Element {
  constructor(){this.disabled=false;this.hidden=false;this.children=[];this.events={};}
  addEventListener(name,callback){this.events[name]=callback;}
  append(...children){this.children.push(...children);}
  replaceChildren(){this.children=[];}
  querySelectorAll(){return [];}
  removeAttribute(name){delete this[name];}
  scrollIntoView(){this.scrolled=true;}
}
(async()=>{
  const elements=new Map(),calls=[];
  const get=selector=>{if(!elements.has(selector))elements.set(selector,new Element());return elements.get(selector);};
  get('#open-folder-button').disabled=true;
  const job={id:'a'.repeat(32),title:'Sample',folder:'manga/Sample',status:'complete',discovered_chapters:1,total_pages:2,verified_pages:2,failed_pages:0,completed_chapters:1,elapsed_seconds:1,message:'Done',chapters:[{name:'Chapter 1',folder:'manga/Sample/Chapter 1',status:'complete',verified_pages:2,total_pages:2,errors:[]}]};
  let fail=false,conflict=false;
  const context=vm.createContext({
    document:{querySelector:get,createElement:()=>new Element(),createDocumentFragment:()=>new Element()},
    localStorage:{getItem:()=>job.id,setItem:()=>{}},setTimeout:()=>{},
    matchMedia:()=>({matches:true}),
    fetch:async(url,options)=>{calls.push(url);if(conflict&&options?.method==='POST'&&url.endsWith('/jobs'))return {ok:false,headers:{get:()=>job.id},json:async()=>({detail:'These chapters already belong to an existing download. Use Resume.'})};if(!fail&&url.endsWith('/clear'))job.cleared_at=Date.now()/1000;if(!fail&&url.endsWith('/restore'))job.cleared_at=null;return {ok:!fail,json:async()=>url.endsWith('/jobs')?[job]:fail?{detail:'Finder could not open.'}:{opened:true}};}
  });
  vm.runInContext(fs.readFileSync('app/static/downloader.js','utf8'),context);
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(get('#open-folder-button').disabled,false,'Loaded job must enable the folder button');
  assert.equal(get('#destination').textContent,'manga/Sample/Chapter 1');
  await get('#open-folder-button').events.click();
  assert.equal(calls.at(-1),`/api/downloader/jobs/${job.id}/open-folder`);
  assert.equal(get('#open-folder-button').disabled,false,'Button must re-enable after success');
  fail=true;await get('#open-folder-button').events.click();
  assert.equal(get('#folder-message').hidden,false);
  assert.equal(get('#folder-message').textContent,'Finder could not open.');
  assert.equal(get('#open-folder-button').disabled,false,'Failure must allow retry');
  job.status='running';job.source_wait={host:'weebcentral.com',until:Date.now()/1000+120};
  vm.runInContext('render()',context);
  assert.equal(get('#source-cooldown').hidden,false);
  assert.match(get('#source-cooldown').textContent,/continue automatically/);
  assert.equal(get('#stop-button').disabled,false,'A cooldown must remain stoppable');
  job.status='paused';vm.runInContext('render()',context);
  assert.equal(get('#resume-button').disabled,true,'Do not offer an early retry');
  assert.match(get('#source-cooldown').textContent,/Resume this download after/);
  job.source_wait.until=Date.now()/1000-1;vm.runInContext('render()',context);
  assert.equal(get('#source-cooldown').hidden,true);
  assert.equal(get('#resume-button').disabled,false);
  fail=false;
  await get('#clear-job-button').events.click();
  assert.equal(calls.filter(url=>url.endsWith('/clear')).length,1);
  assert.equal(get('#progress-panel').hidden,true,'Cleared records must disappear from the current view');
  assert.equal(get('#show-cleared-label').hidden,false,'Cleared history must be recoverable');
  get('#show-cleared').checked=true;get('#show-cleared').events.change();
  assert.equal(get('#progress-panel').hidden,false);
  assert.equal(get('#clear-job-button').textContent,'Restore to list');
  await get('#clear-job-button').events.click();
  assert.equal(job.cleared_at,null);
  job.status='running';vm.runInContext('render()',context);
  assert.equal(get('#clear-job-button').disabled,true,'Stop must be required before clearing an active record');
  job.status='paused';job.cleared_at=Date.now()/1000;conflict=true;
  vm.runInContext("showCleared=false;selectionCleared=true;currentId='';render()",context);
  await vm.runInContext("act('/jobs',{catalog_id:'sample',chapter_ids:['chapter']})",context);
  assert.equal(get('#show-cleared').checked,true);
  assert.equal(get('#progress-panel').hidden,false,'Conflicts must reveal the original download');
  assert.equal(get('#progress-panel').scrolled,true,'Bring the existing Resume control into view');
  assert.equal(get('#resume-button').disabled,false);
  assert.match(get('#notice').textContent,/Use Resume/);
  console.log('Folder button: enabled after load; click endpoint; retry and inline errors passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
