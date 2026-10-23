const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
class Element{
 constructor(tag='div'){this.tag=tag;this.children=[];this.events={};this.value='';this.disabled=false;this.hidden=true;this.paused=true;this.dataset={};this.classList={add:()=>{},remove:()=>{}};}
 append(...nodes){this.children.push(...nodes);}replaceChildren(){this.children=[];}addEventListener(event,fn){this.events[event]=fn;}
 setAttribute(k,v){this[k]=v;}removeAttribute(k){delete this[k];}remove(){this.removed=true;}
 async play(){this.paused=false;this.events.play?.();}pause(){if(!this.paused){this.paused=true;this.events.pause?.();}}
}
(async()=>{
 const elements=new Map(),calls=[];const get=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};
 const beats=[1,2].map(n=>({number:n,panel_ids:[`p0000-${n}`],narration:`Narration ${n}.`,batch_id:'batch-001',token:String(n).repeat(64),silent:false,ready:false,file:null,duration:null}));
 const state={manga:'Sample',chapter:'Chapter 1',revision:'a'.repeat(64),beats,ready:0,spoken:2,pacing:{speed:1.1,sentence_pause:.18},duration:0,busy:false,job:null,full:{ready:false,available:false}};
 const context=vm.createContext({document:{querySelector:get,createElement:tag=>new Element(tag)},URL,URLSearchParams,location:{search:'',href:'http://127.0.0.1:8000/speech?manga=sample&chapter=Chapter%201'},history:{replaceState:()=>{}},setTimeout:()=>{},fetch:async(url,options)=>{
 calls.push({url,options});let value;
 if(url.endsWith('/catalog'))value=[{id:'sample',name:'Sample',chapters:[{name:'Chapter 1',audio_ready:true}]}];
 else if(url.endsWith('/generate')){state.busy=true;state.job={id:'job-a',numbers:[1,2],completed_numbers:[],status:'running',elapsed_seconds:0,message:'Generating',source_revision:state.revision};value=state.job;}
 else if(url.endsWith('/stop')){state.busy=false;state.job.status='paused';value=state.job;}
 else if(url.endsWith('/resume')||url.endsWith('/regenerate'))value={id:'job-a',status:'running'};
 else if(url.endsWith('/full')){state.full={ready:true,available:true,token:'full-a',duration:4,file:'chapter.mp3'};value=state.full;}else if(url.endsWith('/open-folder'))value={opened:true};else value=state;
 return {ok:true,json:async()=>value};
 }});
 vm.runInContext(fs.readFileSync('app/static/speech.js','utf8'),context);await new Promise(r=>setImmediate(r));
 await get('#open-audio-root').events.click();assert(calls.some(c=>c.url==='/api/speech/open-root-folder'));assert.equal(get('#open-audio-root').disabled,false);calls.length=0;
 assert.equal(get('#chapter-select').disabled,true);get('#manga-select').value='sample';await get('#manga-select').events.change();assert.equal(get('#chapter-select').disabled,false,'Choosing a manga must enable its chapter dropdown');get('#chapter-select').value='Chapter 1';await get('#chapter-select').events.change();

 assert.equal(calls.filter(c=>c.options?.method).length,0,'Opening Audio must not call TTS');assert.equal(get('#beats').children.length,2);assert.equal(get('#generate').disabled,false);
 await get('#generate').events.click();assert.equal(JSON.parse(calls.find(c=>c.url.endsWith('/generate')).options.body).revision,state.revision);assert.equal(get('#generate').disabled,true);assert.equal(get('#stop-generation').hidden,false);
 await get('#stop-generation').events.click();assert.equal(get('#resume').hidden,false);await get('#resume').events.click();assert(calls.some(c=>c.url.endsWith('/jobs/job-a/resume')));
 state.job.status='complete';state.busy=false;state.ready=2;state.duration=4;state.full={ready:false,available:true};for(const b of beats){b.ready=true;b.duration=2;b.file=`clip-${b.number}.mp3`;}
 await vm.runInContext('loadStatus()',context);assert.equal(get('#listen').disabled,false);
 const cards=get('#beats').children,audio1=cards[0].children.at(-1),audio2=cards[1].children.at(-1);assert.equal(audio1.preload,'none');
 get('#listen').events.click();await new Promise(r=>setImmediate(r));assert.equal(audio1.paused,false);audio1.paused=true;audio1.events.ended();await new Promise(r=>setImmediate(r));assert.equal(audio2.paused,false);
 await vm.runInContext('loadStatus()',context);assert.equal(get('#beats').children[1].children.at(-1),audio2,'Polling must retain the playing audio element');assert.equal(audio2.paused,false);
 get('#stop-playback').events.click();assert.equal(audio2.paused,true);
 await get('#build-full').events.click();const full=get('#full-audio');assert.equal(full.hidden,false);assert.match(full.src,/full\/audio\?token=full-a/);
 await audio1.play();await full.play();assert.equal(audio1.paused,true);assert.equal(full.paused,false);
 await vm.runInContext('loadStatus()',context);assert.equal(full.paused,false,'Polling must not restart full audio');
 get('#stop-playback').events.click();assert.equal(full.paused,true);
 await cards[1].children[0].children[1].events.click();assert(calls.some(c=>c.url.endsWith('/beats/2/regenerate')));
 await get('#open-folder').events.click();assert(calls.at(-1).url.endsWith('/open-folder'));
 console.log('Stage 6 UI: no automatic TTS, generation/stop/resume, sequential playback, stable players and per-beat regeneration passed.');
})().catch(e=>{console.error(e);process.exitCode=1;});
