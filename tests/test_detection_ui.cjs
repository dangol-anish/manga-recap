const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
class Element{
 constructor(tag='div'){this.tag=tag;this.value='';this.dataset={};this.children=[];this.events={};this.disabled=false;}
 append(...children){this.children.push(...children);}
 replaceChildren(){this.children=[];}
 addEventListener(event,callback){this.events[event]=callback;}
 querySelectorAll(tag){return this.children.flatMap(c=>[...(c.tag===tag?[c]:[]),...c.querySelectorAll(tag)]);}
}
(async()=>{
 const elements=new Map(),calls=[];const get=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};
 const chapter={id:'chapter-1',name:'Chapter 1',folder:'manga/Sample/Chapter 1',images:1,pending:false};
 let jobs=[];
 const job={id:'job-1',status:'running',direction:'ltr',message:'Detecting',total_pages:1,completed_pages:0,completed_chapters:0,valid_pages:0,flagged_pages:0,panels_found:0,average_seconds_per_page:0,elapsed_seconds:0,chapters:[{...chapter,manga:'Sample',status:'running',pages:['0000.png'],results:[],elapsed_seconds:0}]};
 const context=vm.createContext({document:{querySelector:get,createElement:tag=>new Element(tag),createTextNode:text=>{const n=new Element('text');n.textContent=text;return n;}},localStorage:{getItem:()=>'',setItem:()=>{}},setTimeout:()=>{},fetch:async(url,options)=>{
  calls.push({url,body:options?.body});let value;
  if(url.endsWith('/catalog'))value={manga:[{id:'manga-1',name:'Sample',direction:'rtl',chapters:[chapter]}],detector:{ready:true,message:'Magi ready',revision:'abc123456789'}};
  else if(url.endsWith('/open-folder'))value={opened:true};
  else if(url.endsWith('/stop')){job.status='paused';jobs=[job];value=job;}
  else if(options?.method){job.status='running';jobs=[job];value=job;}
  else value=jobs;
  return {ok:true,json:async()=>value};
 }});
 vm.runInContext(fs.readFileSync('app/static/detection.js','utf8'),context);await new Promise(resolve=>setImmediate(resolve));
 assert.equal(calls.filter(c=>c.body).length,0,'Loading the page must not start detection');
 get('#manga-select').value='manga-1';get('#manga-select').events.change();const checkbox=get('#chapter-list').querySelectorAll('input')[0];checkbox.checked=true;checkbox.events.change();get('#reading-direction').value='ltr';
 assert.equal(get('#start-detection').disabled,false);await get('#start-detection').events.click();
 const start=calls.find(c=>c.url.endsWith('/jobs')&&c.body);assert.deepEqual(JSON.parse(start.body),{chapter_ids:['chapter-1'],direction:'ltr'});
 assert.equal(get('#stop-detection').hidden,false);assert.equal(get('#manga-select').disabled,true);
 await get('#stop-detection').events.click();assert.equal(get('#resume-detection').hidden,false);
 await get('#resume-detection').events.click();assert.ok(calls.some(c=>c.url.endsWith('/job-1/resume')));
 const folder=get('#chapter-results').querySelectorAll('button')[0];await folder.events.click();assert.equal(calls.at(-1).url,'/api/detection/jobs/job-1/chapters/0/open-folder');
 console.log('Stage 3 UI: explicit start, direction, busy controls, stop/resume, native folder button passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
