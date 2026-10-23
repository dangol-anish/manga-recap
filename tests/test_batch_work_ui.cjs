const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
class Element{
 constructor(tag){this.tag=tag;this.children=[];this.events={};this.value='';this.dataset={};}
 append(...nodes){this.children.push(...nodes);}replaceChildren(){this.children=[];}setAttribute(k,v){this[k]=v;}addEventListener(k,v){this.events[k]=v;}
 querySelectorAll(tag){return this.children.flatMap(c=>[...(c.tag===tag?[c]:[]),...c.querySelectorAll(tag)]);}
}
const flush=()=>new Promise(r=>setImmediate(r));
(async()=>{for(const kind of ['audio','video']){
 const panel=new Element('section');panel.dataset.batchKind=kind;let saved=null;const calls=[],timers=[];
 const context=vm.createContext({document:{querySelector:()=>panel,createElement:t=>new Element(t)},setTimeout:fn=>timers.push(fn),fetch:async(url,options)=>{
  calls.push({url,options});let result;
  if(url.endsWith('/catalog'))result=[{id:'m',name:'Manga',chapters:[{name:'Chapter 1',audio_ready:true,video_ready:true},{name:'Chapter 2',audio_ready:true,video_ready:true},{name:'Chapter 3',audio_ready:false,video_ready:false}]}];
  else if(url.endsWith('/open-folder'))result={opened:true};
  else if(options?.method){if(url.endsWith('/stop'))saved.status='paused';else if(url.endsWith('/resume'))saved.status='running';else{const b=JSON.parse(options.body);saved={id:'job',kind,manga_id:b.manga_id,status:'running',items:b.chapters.map(chapter=>({chapter,status:'waiting',progress:0,message:'Waiting'}))};}result=saved;}
  else result=saved;return {ok:true,json:async()=>JSON.parse(JSON.stringify(result))};
 }});
 vm.runInContext(fs.readFileSync('app/static/batch_work.js','utf8'),context);await flush();
 const select=panel.querySelectorAll('select')[0];const tools=panel.children.find(c=>c.className==='batch-tools'),actions=panel.children.find(c=>c.className==='batch-actions');assert.equal(tools.hidden,true);assert.equal(actions.hidden,true);select.value='m';select.events.change();assert.equal(tools.hidden,false);assert.equal(actions.hidden,false);
 const boxes=panel.querySelectorAll('input');assert.equal(boxes.length,3);assert.equal(boxes[2].disabled,true);
 assert(!calls.some(c=>c.options?.method),'Opening the page must never start work');
 let buttons=panel.querySelectorAll('button');const all=buttons.find(b=>b.textContent==='Select all ready chapters');const create=buttons.find(b=>b.textContent.startsWith('Create selected'));
 assert.equal(create.disabled,true);all.events.click();assert.equal(boxes[0].checked,true);assert.equal(boxes[1].checked,true);assert.equal(create.disabled,false);
 await create.events.click();assert.deepEqual(JSON.parse(calls.find(c=>c.options?.method).options.body),{manga_id:'m',chapters:['Chapter 1','Chapter 2']});assert.equal(create.disabled,true);
 assert.equal(panel.querySelectorAll('progress').length,2);
 let stop=panel.querySelectorAll('button').find(b=>b.textContent==='Stop selected chapters');await stop.events.click();assert.equal(create.disabled,false);
 const resume=panel.querySelectorAll('button').find(b=>b.textContent==='Resume unfinished chapters');await resume.events.click();assert.equal(create.disabled,true);
 assert(calls.some(c=>c.url.endsWith('/job/resume')));
 saved.status='complete';for(const item of saved.items){item.status='complete';item.message='Ready';item.progress=1;}await timers.shift()();
 const folders=panel.querySelectorAll('button').filter(b=>b['aria-label']?.startsWith('Open audio folder for '));
 assert.equal(folders.length,kind==='audio'?2:0);
 if(kind==='audio'){await folders[1].events.click();assert(calls.some(c=>c.url==='/api/speech/m/Chapter%202/open-folder'));assert.equal(folders[1].disabled,false);}

 }
 console.log('Audio/video batch UI: explicit multi-selection, eligibility, per-chapter progress, stop/resume and no automatic generation passed.');
})().catch(e=>{console.error(e);process.exitCode=1;});
