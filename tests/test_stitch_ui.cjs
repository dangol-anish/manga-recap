const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
class Element{
 constructor(tag='div'){this.tag=tag;this.children=[];this.events={};this.value='';this.disabled=false;this.hidden=false;this.paused=true;this.loads=0;}
 append(...n){this.children.push(...n);}replaceChildren(){this.children=[];}addEventListener(t,fn){this.events[t]=fn;}removeAttribute(k){delete this[k];}pause(){this.paused=true;}
 querySelectorAll(t){return this.children.flatMap(c=>[...(c.tag===t?[c]:[]),...c.querySelectorAll(t)]);}
 set src(v){this._src=v;this.loads++;}get src(){return this._src;}
}
(async()=>{
 const elements=new Map(),calls=[],get=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};
 const videos=[1,2,10].map(n=>({id:String(n),filename:`Chapter${n}-Video.mp4`,duration:30}));let job=null;
 const ctx=vm.createContext({document:{querySelector:get,createElement:t=>new Element(t)},setTimeout:()=>{},fetch:async(url,options)=>{calls.push({url,options});let value;
 if(url.endsWith('/catalog'))value=[{id:'m',name:'Manga',videos}];else if(url.endsWith('/status'))value={job};else if(url.endsWith('/start')){const input=JSON.parse(options.body);job={id:'new',status:'running',progress:0,items:videos.filter(v=>input.video_ids.includes(v.id)),message:'Combining'};value=job;}else if(url.endsWith('/stop')){job.status='paused';value=job;}else if(url.endsWith('/retry')){job.status='running';value=job;}else value={opened:true};return {ok:true,json:async()=>JSON.parse(JSON.stringify(value))};}});
 vm.runInContext(fs.readFileSync('app/static/stitch.js','utf8'),ctx);await new Promise(r=>setImmediate(r));
 assert(!calls.some(c=>c.options?.method),'Opening Stitch must not create a video');assert.equal(get('#combine').disabled,true);
 get('#manga').value='m';get('#manga').events.change();const boxes=get('#video-list').querySelectorAll('input');assert.equal(boxes.length,3);
 boxes[0].checked=true;boxes[0].events.change();assert.equal(get('#combine').disabled,true);boxes[1].checked=true;boxes[1].events.change();assert.equal(get('#combine').disabled,false);
 await get('#combine').events.click();assert.deepEqual(JSON.parse(calls.find(c=>c.url.endsWith('/start')).options.body),{manga_id:'m',video_ids:['1','2']});assert.equal(get('#manga').disabled,true);assert.equal(get('#job').hidden,false);
 await get('#stop').events.click();assert.equal(get('#retry').hidden,false);await get('#retry').events.click();assert.equal(get('#retry').hidden,true);
 job.status='complete';job.progress=1;job.output='saved.mp4';await vm.runInContext('poll()',ctx);assert.equal(get('#result').hidden,false);const player=get('#combined-video');const loads=player.loads;player.paused=false;await vm.runInContext('poll()',ctx);assert.equal(player.loads,loads);assert.equal(player.paused,false,'Status polling must never pause/reload the same combined MP4');
 assert.match(get('#download').href,/download=true/);await get('#open-result').events.click();assert(calls.some(c=>c.url==='/api/stitch/jobs/new/open-folder'));
 const html=fs.readFileSync('app/static/stitch.html','utf8');assert.match(html,/<video id="combined-video" controls playsinline/);assert.match(html,/Stage 8:/);assert(html.indexOf('controls.css')>html.indexOf('stitch.css'));
 console.log('Stitch UI: selection, no automatic creation, progress, stop/retry, folder action and stable native video playback passed.');
})().catch(e=>{console.error(e);process.exitCode=1;});
