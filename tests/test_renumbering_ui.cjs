const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
class Element{
 constructor(tag='div'){this.tag=tag;this.value='';this.disabled=false;this.hidden=false;this.children=[];this.events={};}
 addEventListener(event,fn){this.events[event]=fn;}
 append(...nodes){this.children.push(...nodes);}
 replaceChildren(){this.children=[];}
 querySelectorAll(tag){return this.children.flatMap(child=>[...(child.tag===tag?[child]:[]),...child.querySelectorAll(tag)]);}
 get options(){return this.children;}
}
(async()=>{
 const elements=new Map(),calls=[];const get=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};
 const chapter={id:'chapter-a',folder:'manga/Sample/Chapter 1',name:'Chapter 1',manga:'Sample',images:2,pending:false};
 const plan={id:'plan-a',status:'preview',chapters:[{...chapter,changes:2,mapping:[{from:'2.jpg',to:'0000.jpg'},{from:'9.png',to:'0001.png'}]}]};
 const context=vm.createContext({URLSearchParams,location:{search:""},document:{querySelector:get,createElement:tag=>new Element(tag),createTextNode:text=>{const n=new Element('text');n.textContent=text;return n;}},fetch:async(url,options)=>{calls.push({url,body:options?.body});return {ok:true,json:async()=>url.endsWith('/chapters')?[chapter]:url.endsWith('/open')?{opened:true}:{...plan,status:'complete',results:[{folder:chapter.folder,status:'complete',images:2,renamed:2}]}};}});
 vm.runInContext(fs.readFileSync('app/static/renumbering.js','utf8'),context);
 await new Promise(resolve=>setImmediate(resolve));
 assert.equal(get('#apply-numbering').disabled,true,'No selection must disable renumbering');
 assert.equal(calls.length,1,'Loading must never rename automatically');
 const checkbox=get('#folder-list').querySelectorAll('input')[0];checkbox.checked=true;checkbox.events.change();
 assert.equal(get('#apply-numbering').disabled,false);
 await get('#apply-numbering').events.click();
 assert.equal(calls.at(-1).url,'/api/renumbering/renumber');
 assert.equal(JSON.parse(calls.at(-1).body).chapter_ids[0],'chapter-a');
 assert.equal(calls.some(c=>c.url.endsWith('/preview')),false,'No preview request or extra click');
 assert.equal(get('#rename-results').hidden,false);
 assert.equal(get('#apply-numbering').disabled,true,'Success clears selection to prevent repeat clicks');
 const folder=get('#result-list').querySelectorAll('button')[0];
 assert.ok(folder,'Successful chapter must have a folder button');assert.equal(folder.disabled,false);
 await folder.events.click();assert.equal(calls.at(-1).url,'/api/renumbering/plans/plan-a/folders/0/open');
 assert.equal(folder.disabled,false,'Folder button re-enables after opening');
 console.log('Renumbering UI: single-click renumber, no preview, per-chapter folder button passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
