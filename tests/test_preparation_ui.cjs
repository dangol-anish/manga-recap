const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
class Element{
 constructor(tag='div'){this.tag=tag;this.children=[];this.events={};this.dataset={};this.value='';this.disabled=false;}
 append(...nodes){this.children.push(...nodes);}
 replaceChildren(){this.children=[];}
 addEventListener(event,fn){this.events[event]=fn;}
 querySelectorAll(tag){return this.children.flatMap(c=>[...(c.tag===tag?[c]:[]),...c.querySelectorAll(tag)]);}
}
(async()=>{
 const elements=new Map(),calls=[],review=[];const get=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};
 const chapter={id:'chapter-a',name:'Chapter 1',folder:'manga/Sample/Chapter 1',images:2,ready:true,prepared_pages:0};let jobs=[];
 const job={id:'job-a',status:'running',message:'Preparing',total_pages:2,completed_pages:0,prepared_pages:0,flagged_pages:0,panels:0,elapsed_seconds:0,chapters:[{id:'chapter-a',name:'Chapter 1',manga:'Sample',total_pages:2,results:[]}]};
 const context=vm.createContext({URLSearchParams,location:{search:""},window:{PanelReview:{setBusy:()=>{},loadChapter:async id=>review.push(id),refresh:async()=>{}}},document:{querySelector:get,createElement:tag=>new Element(tag)},localStorage:{getItem:()=>'',setItem:()=>{}},setTimeout:()=>{},fetch:async(url,options)=>{
  calls.push({url,body:options?.body});let value;
  if(url.endsWith('/catalog'))value=[{id:'manga-a',name:'Sample',chapters:[chapter]}];
  else if(url.endsWith('/open-folder'))value={opened:true};
  else if(url.endsWith('/stop')){job.status='paused';job.prepared_pages=job.completed_pages=1;job.panels=2;job.chapters[0].results=[{page:'0000.png',status:'prepared',panels:2,flagged:false}];jobs=[job];value=job;}
  else if(options?.method){jobs=[job];value=job;}
  else value=jobs;
  return {ok:true,json:async()=>value};
 }});
 vm.runInContext(fs.readFileSync('app/static/panel_preparation.js','utf8'),context);await new Promise(resolve=>setImmediate(resolve));
 assert.equal(calls.filter(c=>c.body).length,0,'Opening Stage 4 must not prepare chapters');
 get('#manga-select').value='manga-a';get('#manga-select').events.change();const checkbox=get('#chapter-list').querySelectorAll('input')[0];checkbox.checked=true;checkbox.events.change();assert.equal(get('#prepare-chapters').disabled,false);
 await get('#prepare-chapters').events.click();const start=calls.find(c=>c.url.endsWith('/jobs')&&c.body);assert.deepEqual(JSON.parse(start.body),{chapter_ids:['chapter-a']});assert.equal(get('#stop-preparation').hidden,false);
 await get('#stop-preparation').events.click();assert.equal(get('#resume-preparation').hidden,false);
 await get('#job-chapters').querySelectorAll('button')[0].events.click();assert.deepEqual(review,[]);assert.notEqual(get('#review-section').hidden,false);
 assert.equal(calls.at(-1).url,'/api/preparation/chapters/chapter-a/open-folder');
 console.log('Preparation UI: explicit chapter start, progress/stop, hidden review section and native folder opening passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
