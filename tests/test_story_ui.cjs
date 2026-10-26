const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
class Element{
 constructor(tag='div'){this.tag=tag;this.children=[];this.events={};this.dataset={};this.value='';this.disabled=false;this.hidden=true;this.checked=false;}
 append(...nodes){this.children.push(...nodes);}
 replaceChildren(){this.children=[];}
 addEventListener(type,fn){this.events[type]=fn;}
 querySelectorAll(tag){return this.children.flatMap(c=>[...(c.tag===tag?[c]:[]),...c.querySelectorAll(tag)]);}
 setAttribute(k,v){this[k]=v;}
 focus(){this.focused=true;}select(){this.selected=true;}
}
(async()=>{
 const elements=new Map(),calls=[],copied=[];const get=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};
 const b={id:'batch-001',pages:['0000.png','0001.png'],panel_ids:['p0000-1','p0001-1'],fingerprint:'a'.repeat(64),context_token:'b'.repeat(64),ready:true,previous_context:{coverage:{saved_batches:0,cast_included:0,facts_included:0}}};
 const state={manga:'Sample',chapter:'Chapter 1',pages:2,panels:2,imported_beats:0,batches:[b],revision:'c'.repeat(64)};let valid=false;
 const context=vm.createContext({addEventListener(){},document:{querySelector:get,createElement:t=>new Element(t)},URL,URLSearchParams,location:{search:'?manga=sample&chapter=Chapter%201',href:'http://127.0.0.1:8000/story?manga=sample&chapter=Chapter%201'},history:{replaceState:()=>{}},navigator:{clipboard:{writeText:async text=>copied.push(text)}},fetch:async(url,options)=>{
  calls.push({url,options});let value;let status=200;
  if(url.endsWith('/catalog'))value=[{id:'sample',name:'Sample',chapters:[{name:'Chapter 1',ready:true}]}];
  else if(url.includes('/prompt?'))return {ok:true,text:async()=> 'MASTER + EXACT IDS + SAVED CONTEXT + RECEIPT'};
  else if(url.endsWith('/export'))value={photos:2,folder:'outputs/sample/Chapter 1/narration/exports/batch-001'};
  else if(url.endsWith('/import')){if(valid){b.imported=true;b.beat_count=1;b.selected_count=1;state.imported_beats=1;value={ok:true,reply_mode:"full",imported_beats:1,selected_panels:1,skipped_panels:1};}else{status=422;value={ok:false,errors:[],issues:{'p9999-1':['Unknown ID']},can_correct:true,redo_message:'Redo beat 1 using valid IDs only.'};}}
  else value=state;
  return {ok:status===200,status,json:async()=>value};
 }});
 vm.runInContext(fs.readFileSync('app/static/story.js','utf8'),context);await new Promise(r=>setImmediate(r));
 assert.equal(calls.filter(c=>c.options?.method).length,0,'Opening narration is read-only');
 const buttons=get('#batches').querySelectorAll('button');assert.deepEqual(buttons.map(b=>b.textContent),['Export photos','Copy batch prompt']);
 await buttons[0].events.click();assert.equal(calls.at(-1).options.method,'POST');assert.equal(JSON.parse(calls.at(-1).options.body).fingerprint,b.fingerprint);
 await buttons[1].events.click();assert.equal(copied[0],'MASTER + EXACT IDS + SAVED CONTEXT + RECEIPT');assert.equal(get('#prompt-panel').hidden,false);assert.equal(get('#batch-prompt').value,copied[0]);
 get('#reply').value='{"beats":[]}';await get('#import-reply').events.click();assert.equal(get('#validation').hidden,false);assert.match(get('#issues').children[0].textContent,/p9999-1/);assert.equal(get('#corrections-only').disabled,false);assert.equal(get('#corrections-only').checked,false);
 await get('#copy-redo').events.click();assert.equal(get('#corrections-only').checked,true);assert.match(copied.at(-1),/Redo beat 1/);
 valid=true;await get('#import-reply').events.click();assert.match(get('#import-status').textContent,/Imported 1 beats/);assert.equal(get('#reply').value,'');assert.equal(get('#corrections-only').checked,false);assert.match(get('#import-status').textContent,/Full batch reply detected/);
 assert(!fs.readFileSync('app/static/story.js','utf8').includes('.zip'));assert(!fs.readFileSync('app/static/story.html','utf8').includes('Download batch prompt'));
 console.log('Stage 5 UI: explicit photo export, copyable prompt, validation/corrections and import status passed.');
})().catch(e=>{console.error(e);process.exitCode=1;});
