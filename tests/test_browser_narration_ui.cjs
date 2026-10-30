const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
class Element {
 constructor(tag='div'){this.tag=tag;this.children=[];this.events={};this.value='';this.hidden=false;this.checked=false;this.disabled=false;this.open=false;}
 append(...nodes){this.children.push(...nodes);}replaceChildren(){this.children=[];}addEventListener(name,fn){this.events[name]=fn;}
}
(async()=>{
 const elements=new Map(),calls=[],timers=[],notifications=[];
 const get=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};
 let saved=null,connected=true,pairFailure=false,copied='';
 class Notice{static permission='default';static async requestPermission(){this.permission='granted';return 'granted';}constructor(title,options){notifications.push({title,options});}}
 const context=vm.createContext({AbortController,document:{getElementById:get,createElement:tag=>new Element(tag),addEventListener(){}},window:{},navigator:{clipboard:{writeText:async text=>copied=text}},Notification:Notice,
 setInterval:()=>1,setTimeout:fn=>{timers.push(fn);return timers.length;},clearTimeout(){},fetch:async(url,options)=>{
  calls.push({url,options});let result=saved;
  if(url==='/api/story/catalog')result=[{id:'sample',name:'Sample',chapters:[{name:'Chapter 1',ready:true},{name:'Chapter 2',ready:false}]}];
  if(url==='/api/narration-extension/status')result={connected,paired:connected};
  if(url==='/api/narration-extension/code')return {ok:!pairFailure,json:async()=>pairFailure?{detail:'Synthetic pairing failure'}:{code:'synthetic-code'}};
  if(options?.method){
   if(url==='/api/browser-narration')saved={id:'f'.repeat(32),status:'running',items:[{chapter:'Chapter 1',batch_id:'batch-001',status:'running'}],message:'Sending…',cursor:0};
   if(url.endsWith('/resume'))saved={...saved,status:'running'};
   if(url.endsWith('/clear')){saved=null;return {ok:true,json:async()=>({cleared:true,message:'Queue cleared. Saved narration retained.'})};}
   if(url.endsWith('/stop'))saved={...saved,status:'stopping'};
   result=saved;
  }
  return {ok:true,json:async()=>result};
 }});
 vm.runInContext(fs.readFileSync('app/static/browser_narration.js','utf8'),context);
 await new Promise(r=>setImmediate(r));assert.equal(calls.length,1);assert(!calls[0].options?.method,'Opening narration never submits a request');
 get('browser-narration-panel').open=true;await get('browser-narration-panel').events.toggle();
 get('browser-manga').value='sample';get('browser-manga').events.change();
 const labels=get('browser-chapters').children;assert.equal(labels.length,2);assert.equal(labels[1].children[0].disabled,true);
 labels[0].children[0].checked=true;labels[0].children[0].events.change();assert.equal(get('browser-start').disabled,false);
 connected=false;
 await get('browser-start').events.click();
 assert.equal(calls.filter(c=>c.options?.method).length,0,'Disconnected Start must explain the blocker without submitting a queue');
 assert.match(get('browser-start-note').textContent,/Connect AI Studio first/);
 await get('browser-connection-refresh').events.click();assert.match(get('browser-setup-feedback').textContent,/not connected/);
 await get('browser-catalog-refresh').events.click();
 assert.equal(get('browser-chapters').children[0].children[0].checked,true,'Refreshing chapters retains the same selected chapters');
 connected=true;
 await get('browser-start').events.click();assert.equal(calls.filter(c=>c.options?.method).length,1);
 assert.deepEqual(JSON.parse(calls.find(c=>c.options?.method).options.body),{manga_id:'sample',chapters:['Chapter 1']});
 await get('browser-alerts').events.click();
 saved={...saved,status:'paused',phase:'sending',attention:'timeout',retry_safe:false,message:'Check unfinished request.'};
 await timers.at(-1)();assert.equal(get('browser-resume').hidden,false);assert.equal(get('browser-retry-label').hidden,false);
 assert.equal(notifications.length,1);await get('browser-job-refresh').events.click();assert.equal(notifications.length,1,'Read-only refresh does not repeat alerts');
 assert.equal(calls.filter(c=>c.options?.method).length,1,'Pausing never automatically retries writes');
 get('browser-retry').checked=true;await get('browser-resume').events.click();
 assert.equal(JSON.parse(calls.findLast(c=>c.options?.method).options.body).retry_unfinished,true);
 saved={...saved,status:'paused',phase:'reply_saved',attention:'validation',response_problem:{message:'Synthetic invalid narration'}};
 await get('browser-job-refresh').events.click();
 assert.equal(get('browser-retry-batch').hidden,false);assert.equal(get('browser-resume').hidden,true);
 assert.match(get('browser-response-problem').textContent,/Synthetic invalid narration/);
 await get('browser-retry-batch').events.click();
 assert(calls.findLast(c=>c.options?.method).url.endsWith('/retry-batch'));
 connected=false;await get('browser-stop').events.click();
 assert(calls.findLast(c=>c.options?.method).url.endsWith('/stop'),'Stop works without a connected extension');
 await get('browser-pair-code').events.click();assert.equal(get('browser-pair-value').value,'synthetic-code');assert.match(get('browser-setup-feedback').textContent,/ready below/);
 await get('browser-pair-copy').events.click();assert.equal(copied,'synthetic-code');assert.match(get('browser-setup-feedback').textContent,/copied/);
 pairFailure=true;await get('browser-pair-code').events.click();assert.equal(get('browser-setup-feedback').textContent,'Synthetic pairing failure');assert.equal(get('browser-pair-code').disabled,false);
 saved={...saved,status:'failed'};await get('browser-job-refresh').events.click();
 assert.equal(get('browser-clear').hidden,false);await get('browser-clear').events.click();
 assert.equal(get('browser-job').hidden,true);assert.equal(get('browser-start').disabled,false);
 assert.equal(get('browser-chapters').children[0].children[0].checked,true,'Clearing retains selected chapters');
 assert(calls.findLast(c=>c.options?.method).url.endsWith('/clear'));
 console.log('Browser narration UI: explicit selection/start, disabled unprepared chapters, pause alerts and explicit retry passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
