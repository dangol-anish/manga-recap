const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
class Element{
 constructor(tag='div'){this.tag=tag;this.children=[];this.events={};this.value='';this.disabled=false;this.hidden=true;this.paused=true;this.currentTime=0;this.dataset={};this.style={};this.readyState=1;this.duration=3;this.loads=0;}
 append(...nodes){this.children.push(...nodes);}replaceChildren(){this.children=[];}addEventListener(type,fn){this.events[type]=fn;}setAttribute(k,v){this[k]=v;}removeAttribute(k){delete this[k];}load(){this.loads++;}
 querySelectorAll(tag){return this.children.flatMap(c=>[...(c.tag===tag?[c]:[]),...c.querySelectorAll(tag)]);}
 async play(){this.paused=false;this.events.play?.();}pause(){this.paused=true;}
}
(async()=>{
 const elements=new Map(),calls=[];const get=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};
 const makePanel=(id,kind)=>({id,key:id,frame_url:'/frames/'+id,motion_kind:kind,phrase:'A phrase'});
 let server={manga:'Sample',chapter:'Chapter 1',source_token:'a'.repeat(64),render_token:'b'.repeat(64),fps:24,width:1280,height:720,duration:3,ready:false,busy:false,job:{status:'complete',render_token:'b'.repeat(64),progress:1,message:'MP4 ready.'},
 motion_style:{zoom_max:.065,zoom_per_second:.014,slide_max:.038,slide_per_second:.009},transition_style:{frames:4},
 beats:[{number:1,duration:2,narration:'An existing script.',switches:[.6],phrase_switches:[.6],panels:[makePanel('p0000-1','zoom_in'),makePanel('p0000-2','zoom_out')]},
 {number:2,duration:1,narration:'Another beat.',switches:[],phrase_switches:[],panels:[makePanel('p0001-1','slide_from_left')]}]};
 const clone=v=>JSON.parse(JSON.stringify(v));
 const context=vm.createContext({document:{querySelector:get,createElement:tag=>new Element(tag)},URL,URLSearchParams,location:{search:'',href:'http://127.0.0.1:8000/cinema?manga=sample&chapter=Chapter%201'},history:{replaceState:()=>{}},setTimeout:()=>{},fetch:async(url,options)=>{
 calls.push({url,options});let value;
 if(url.endsWith('/catalog'))value=[{id:'sample',name:'Sample',chapters:[{name:'Chapter 1',video_ready:true}]}];
 else if(url.endsWith('/timing')){const data=JSON.parse(options.body);server.beats[data.number-1].switches=data.switches;server.render_token='c'.repeat(64);server.ready=false;value=server;}
 else if(url.endsWith('/export')){server.busy=true;server.job={status:'running',render_token:server.render_token,progress:.2,message:'Rendering'};value=server.job;}
 else value=server;
 return {ok:true,json:async()=>clone(value)};
 }});
 vm.runInContext(fs.readFileSync('app/static/cinema.js','utf8'),context);await new Promise(r=>setImmediate(r));
 assert.equal(get('#chapter-select').disabled,true);get('#manga-select').value='sample';await get('#manga-select').events.change();assert.equal(get('#chapter-select').disabled,false,'Choosing a manga must enable its chapter dropdown');get('#chapter-select').value='Chapter 1';await get('#chapter-select').events.change();

 const player=get('#preview');assert.equal(calls.filter(c=>c.options?.method).length,0,'Opening Video must not export or synthesize');assert.equal(player.hidden,true);assert.equal(get('#build-preview').disabled,false);assert.equal(get('#progress').value,0,'A deleted video must not show completed render progress');assert(!get('#job').textContent.includes('MP4 ready'));
 await get('#build-preview').events.click();assert.equal(get('#progress').value,.2);assert.equal(get('#build-preview').disabled,true);
 server.ready=true;server.busy=false;server.job={status:'complete',render_token:server.render_token,progress:1,message:'MP4 ready.'};await vm.runInContext('refresh()',context);
 assert.equal(player.hidden,false);assert.match(player.src,/\/mp4\?token=/);assert.equal(get('#preview-empty').hidden,true);
 player.events.loadedmetadata();const loads=player.loads;await player.play();
 for(const [time,panel,beat] of [[.7,'p0000-2','1'],[2.2,'p0001-1','2'],[.1,'p0000-1','1'],[2.7,'p0001-1','2'],[1.2,'p0000-2','1']]){player.currentTime=time;player.events.seeked();assert.equal(player.currentTime,time,'Seeking must never be rewritten by panel UI');assert.equal(get('#beat').value,beat);assert.match(get('#panel-label').textContent,new RegExp(panel));}
 await vm.runInContext('refresh()',context);assert.equal(player.currentTime,1.2);assert.equal(player.loads,loads,'Polling must not reload the same MP4');assert.equal(player.paused,false,'Polling must not pause playback');
 get('#beat').value='1';get('#beat').events.change();assert.equal(player.currentTime,0);
 const input=get('#timings').querySelectorAll('input')[0];input.value='.9';input.events.input();assert.equal(player.hidden,true);assert.equal(player.src,undefined);assert.equal(get('#export').disabled,true);assert.equal(get('#save').disabled,false);assert.equal(get('#manga-select').disabled,true);
 await get('#save').events.click();const saved=JSON.parse(calls.find(c=>c.url.endsWith('/timing')).options.body);assert.deepEqual(saved.switches,[.9]);assert.equal(saved.number,1);assert.equal(get('#build-preview').disabled,false);assert.equal(get('#play').disabled,true,'Old MP4 cannot be played as current timing');
 await get('#build-preview').events.click();server.ready=true;server.busy=false;server.job={status:'complete',render_token:server.render_token,progress:1,message:'MP4 ready.'};await vm.runInContext('refresh()',context);assert.equal(player.hidden,false);assert.match(get('#download').href,/download=true/);
 server.beats[0].duration=1.28;server.duration=2.28;server.source_token='e'.repeat(64);server.render_token='f'.repeat(64);await vm.runInContext('refresh()',context);player.events.loadedmetadata();
 get('#beat').value='2';get('#beat').events.change();assert.equal(get('#beat').value,'2');assert.equal(player.currentTime,31/24);
 get('#beat').value='1';get('#beat').events.change();const edit=get('#timings').querySelectorAll('input')[0];edit.value='.7';edit.events.input();server.render_token='9'.repeat(64);await vm.runInContext('refresh()',context);assert.equal(get('#save').disabled,true);assert.match(get('#notice').textContent,/another tab/);
 const html=fs.readFileSync('app/static/cinema.html','utf8');assert.match(html,/<video id="preview" controls playsinline/);assert(!html.includes('<audio'));assert(!html.includes('panel-outgoing'));assert(!fs.readFileSync('app/static/cinema.js','utf8').includes('requestAnimationFrame'));
 assert(!calls.some(c=>c.url.includes('/api/speech/')&&c.options?.method));
 console.log('Native video preview: forward/backward seeking, no reload/rewind on polling, build/rebuild, stale media blocking and timing edits passed.');
})().catch(e=>{console.error(e);process.exitCode=1;});
