const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
class Element {
 constructor(tag='div'){this.tag=tag;this.tagName=tag.toUpperCase();this.children=[];this.events={};this.attrs={};this.value='';this.disabled=false;this.paused=true;this.readyState=1;this.currentTime=0;this.duration=30;this.loads=0;}
 append(...items){this.children.push(...items);for(const child of items)child.parent=this;}
 replaceChildren(...items){this.children=[];this.append(...items);}
 addEventListener(name,fn){this.events[name]=fn;}
 setAttribute(name,value){this.attrs[name]=value;}
 getAttribute(name){return this.attrs[name]??null;}
 removeAttribute(name){delete this.attrs[name];}
 remove(){this.parent.children=this.parent.children.filter(c=>c!==this);}
 get src(){return this.attrs.src;}set src(value){this.attrs.src=value;this.loads++;}
 pause(){this.paused=true;}
 async play(){this.paused=false;documentEvents.play?.({target:this});}
 get options(){return this.children;}
}
const documentEvents={},tick=()=>new Promise(r=>setImmediate(r));
(async()=>{
 const nodes=new Map(),get=id=>{if(!nodes.has(id))nodes.set(id,new Element(['join-tail','join-head','quality-player'].includes(id)?'audio':'div'));return nodes.get(id);};
 const calls=[];let fail=false;
 const context=vm.createContext({document:{getElementById:get,createElement:t=>new Element(t),addEventListener:(e,fn)=>documentEvents[e]=fn,querySelectorAll:t=>[...nodes.values()].filter(n=>n.tag===t)},fetch:async(url,options)=>{
  calls.push({url,options});if(fail)throw Error('Saved recording changed. Reload it.');
  let value;
  if(url.includes('/pronunciation/'))value=options?.method?{revision:'b'.repeat(64),items:JSON.parse(options.body).items}:{revision:'a'.repeat(64),items:[]};
  else value={checked:true,words_per_minute:180,longest_quiet_seconds:4,warnings:[{code:'long_quiet',message:'A quiet stretch needs checking.',start:10}],note:'This cannot verify every word.'};
  return {ok:true,json:async()=>value};
 }});
 vm.runInContext(fs.readFileSync('app/static/audio_quality.js','utf8'),context);
 assert.equal(calls.length,0,'Module must not generate or even check audio on opening');
 context.AudioQuality.mangaChanged('m');await tick();assert.equal(calls.length,1);assert.equal(get('pronunciation-save').disabled,false);
 get('pronunciation-add').events.click();const row=get('pronunciation-rows').children[0];row.children[0].value='Aster';row.children[1].value='AS-ter';
 await get('pronunciation-save').events.click();assert.deepEqual(JSON.parse(calls.at(-1).options.body).items,[{word:'Aster',say_as:'AS-ter'}]);assert.match(get('pronunciation-message').textContent,/unchanged/);
 const section=n=>({number:n,recorded:true,timed:true,audio_token:'token'+n,chapters:['Chapter '+n],opening:'Opening '+n,ending:'Ending '+n});
 const job={id:'job',manga:'Sample',status:'complete',sections:[section(1),section(2)]};
 context.AudioQuality.update(job);assert.equal(get('audio-quality-review').hidden,false);assert.equal(get('audio-join-review').hidden,false);
 const tail=get('join-tail'),head=get('join-head');const loads=tail.loads+head.loads;
 context.AudioQuality.update(job);assert.equal(tail.loads+head.loads,loads,'Status polling must not reload join players');
 await get('quality-check').events.click();assert(calls.at(-1).url.endsWith('/quality/1?token=token1'));assert.match(get('quality-message').textContent,/listen for/);
 const list=get('quality-report').children[1];await list.children[0].children[0].events.click();assert.equal(get('quality-player').currentTime,8);
 await get('quality-listen').events.click();assert.equal(tail.currentTime,22);assert.equal(tail.paused,false);
 const withThird={...job,sections:[...job.sections,section(3)]};context.AudioQuality.update(withThird);assert.equal(tail.paused,false,'New completed sections must not interrupt the current join');
 await tail.events.ended();assert.equal(head.currentTime,0);assert.equal(head.paused,false);head.currentTime=8.1;head.events.timeupdate();assert.equal(head.paused,true);
 fail=true;await get('quality-check').events.click();assert.match(get('quality-message').textContent,/Saved recording changed/);
 const writes=calls.filter(c=>c.options?.method);assert.equal(writes.length,1);assert(writes[0].url.includes('/pronunciation/'),'Only explicit hint saving may write');
 const html=fs.readFileSync('app/static/speech.html','utf8');assert.match(html,/Your written narration stays unchanged/);assert.match(html,/Brian does not use these hints/);assert.match(html,/or independently verify every spoken word/);
 console.log('Audio quality UI: explicit hints, local checks, warning seeks, join review and stable playback passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
