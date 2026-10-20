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
}
(async()=>{
  const elements=new Map(),calls=[];
  const get=selector=>{if(!elements.has(selector))elements.set(selector,new Element());return elements.get(selector);};
  get('#open-folder-button').disabled=true;
  const job={id:'a'.repeat(32),title:'Sample',folder:'manga/Sample',status:'complete',discovered_chapters:1,total_pages:2,verified_pages:2,failed_pages:0,completed_chapters:1,elapsed_seconds:1,message:'Done',chapters:[{name:'Chapter 1',folder:'manga/Sample/Chapter 1',status:'complete',verified_pages:2,total_pages:2,errors:[]}]};
  let fail=false;
  const context=vm.createContext({
    document:{querySelector:get,createElement:()=>new Element(),createDocumentFragment:()=>new Element()},
    localStorage:{getItem:()=>job.id,setItem:()=>{}},setTimeout:()=>{},
    fetch:async(url)=>{calls.push(url);return {ok:!fail,json:async()=>url.endsWith('/jobs')?[job]:fail?{detail:'Finder could not open.'}:{opened:true}};}
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
  console.log('Folder button: enabled after load; click endpoint; retry and inline errors passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
