const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
class Element{
 constructor(tag='div'){this.tag=tag;this.children=[];this.events={};this.dataset={};this.value='';this.disabled=false;}
 append(...nodes){this.children.push(...nodes);}
 replaceChildren(){this.children=[];}
 addEventListener(event,fn){this.events[event]=fn;}
 setAttribute(name,value){this[name]=value;}
 getBoundingClientRect(){return {width:100,height:150,left:0,top:0};}
 setPointerCapture(){}
 showModal(){this.open=true;}
 close(){this.open=false;}
 querySelectorAll(tag){return this.children.flatMap(c=>[...(c.tag===tag?[c]:[]),...c.querySelectorAll(tag)]);}
 getContext(){return new Proxy({measureText:text=>({width:text.length*8})},{get:(target,key)=>target[key]||(()=>{})});}
}
(async()=>{
 const elements=new Map(),requests=[];const get=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};
 let record={page:'0000.png',page_id:'p0000',width:100,height:150,revision:'r1',flagged:false,flag_reasons:[],panels:[{id:'p0000-1',box:[0,0,40,60]},{id:'p0000-2',box:[60,0,100,60]}]};
 const saves=[];
 class SourceImage{set src(value){this.url=value;setImmediate(()=>this.onload());}}
 const context=vm.createContext({window:{},Image:SourceImage,confirm:()=>true,document:{querySelector:get,createElement:tag=>new Element(tag)},fetch:async(url,options)=>{
  requests.push(url);let value;
  if(options?.method==='POST'){const body=JSON.parse(options.body);saves.push(body);record={...record,revision:'r'+(saves.length+1),panels:body.boxes.map((box,i)=>({id:`p0000-${i+1}`,box})),flagged:false};value=record;}
  else value=url.endsWith('/p0000')?record:{report:{chapter:'Chapter 1',pages:1,panels:record.panels.length,flagged:[],reading_direction:'rtl'},pages:[record]};
  return {ok:true,json:async()=>value};
 }});
 vm.runInContext(fs.readFileSync('app/static/panel_review.js','utf8'),context);
 await context.window.PanelReview.loadChapter('chapter-a');assert.equal(get('#contact-sheet').querySelectorAll('button').length,1);
 await get('#contact-sheet').querySelectorAll('button')[0].events.click();await new Promise(resolve=>setImmediate(resolve));
 assert.equal(get('#editor').open,true);assert.equal(get('#save-fixes').disabled,false);
 get('#panel-list').children[0].children[2].events.click();
 await get('#save-fixes').events.click();assert.deepEqual(saves[0].boxes,[[60,0,100,60],[0,0,40,60]],'Reorder must be sent in reading order');
 get('#draw-box').events.click();const down={clientX:10,clientY:80,pointerId:1,preventDefault(){}};
 get('#page-canvas').events.pointerdown(down);get('#page-canvas').events.pointermove({clientX:90,clientY:145});get('#page-canvas').events.pointerup({clientX:90,clientY:145});
 assert.equal(get('#panel-list').children.length,3);await get('#save-fixes').events.click();assert.equal(saves[1].boxes.length,3);assert.deepEqual(saves[1].boxes[2],[10,80,90,145]);
 get('#panel-list').children[2].children[0].events.click();get('#delete-box').events.click();assert.equal(get('#panel-list').children.length,2);
 get('#panel-list').children[0].children[0].events.click();get('#page-canvas').events.pointerdown({clientX:60,clientY:0,pointerId:2,preventDefault(){}});get('#page-canvas').events.pointermove({clientX:55,clientY:5});get('#page-canvas').events.pointerup({clientX:55,clientY:5});
 await get('#save-fixes').events.click();assert.equal(saves[2].boxes.length,2);assert.deepEqual(saves[2].boxes[0],[55,5,100,60]);
 context.window.PanelReview.setBusy(true);assert.equal(get('#save-fixes').disabled,true);context.window.PanelReview.setBusy(false);assert.equal(get('#save-fixes').disabled,false);
 assert.ok(requests.filter(u=>u.includes('/pages/')).every(u=>u.includes('/p0000')),'Save only the edited page');
 console.log('Panel editor: load, reorder, draw, delete, drag-resize, revision saves and busy protection passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
