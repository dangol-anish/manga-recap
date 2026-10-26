const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
class Element{
 constructor(tag='div',text=''){this.tag=tag;this.textContent=text;this.children=[];this.events={};this.value='';this.disabled=false;this.hidden=false;this.dataset={};this.attrs={};this.loads=0;}
 append(...nodes){this.children.push(...nodes);}prepend(...nodes){this.children.unshift(...nodes);}replaceChildren(...nodes){this.children=[...nodes];}addEventListener(name,fn){this.events[name]=fn;}
 setAttribute(k,v){this.attrs[k]=v;}getAttribute(k){return k==='src'?this._src:this.attrs[k];}scrollIntoView(){}select(){}showModal(){this.open=true;}close(){this.open=false;}
 get firstChild(){return this.children[0];}set src(value){this._src=value;this.loads++;}get src(){return this._src;}
 querySelectorAll(selector){const match=node=>selector.startsWith('.')?node.className===selector.slice(1):selector.startsWith('textarea[')?node.tag==='textarea'&&String(node.dataset.index)===selector.match(/"(\d+)"/)[1]:node.tag===selector;return this.children.flatMap(c=>[...(match(c)?[c]:[]),...c.querySelectorAll(selector)]);}
 querySelector(s){return this.querySelectorAll(s)[0]||null;}
}
(async()=>{
 const nodes=new Map(),calls=[],get=id=>{if(!nodes.has(id))nodes.set(id,new Element());return nodes.get(id);};
 const row={chapter:'Chapter 1',source_beat:1,panel_id:'p0000-1',narration:'An event happens.'};
 let project={id:'p',name:'Test project',revision:'r',manga:'Sample',chapters:['Chapter 1'],busy:false,stale:false,job:null,shorts:[]};
 const idea={id:'s',title:'A surprising moment',why_it_works:'A clear payoff',word_count:80,estimated_seconds:33,segments:[row],ready:false};
 const catalog=[{id:'m',name:'Sample',chapters:[{name:'Chapter 1',shorts_ready:true},{name:'Chapter 2',shorts_ready:false,reason:'Import narration.'}]}];
 const context=vm.createContext({document:{getElementById:get,createElement:t=>new Element(t),querySelectorAll:()=>[]},localStorage:{getItem:()=>null,setItem:()=>{}},navigator:{clipboard:{writeText:async()=>{}}},setInterval:()=>{},fetch:async(url,options)=>{calls.push({url,options});let value;
 if(url.endsWith('/catalog'))value=catalog;else if(url.endsWith('/projects')&&!options?.method)value=[];
 else if(url.endsWith('/projects'))value=project;
 else if(url.endsWith('/import')){project.shorts=[idea];project.revision='r2';value={ok:true,project};}
 else if(url.endsWith('/create')){project.busy=true;project.job={status:'running',selected:['s'],completed:[],message:'Starting',progress:0};value=project;}
 else if(url.endsWith('/stop')){project.busy=false;project.job.status='paused';value=project;}
 else if(url.endsWith('/edit')){project.revision='r3';value={ok:true,project};}
 else value=project;
 return {ok:true,json:async()=>JSON.parse(JSON.stringify(value))};}});
 vm.runInContext(fs.readFileSync('app/static/shorts.js','utf8'),context);await new Promise(r=>setImmediate(r));
 assert(!calls.some(c=>c.options?.method),'Opening Shorts must not generate, import or create a package');assert.equal(get('make-package').disabled,true);
 get('manga').value='m';get('manga').events.change();let choices=get('chapters').querySelectorAll('input');assert.equal(choices.length,2);assert.equal(choices[1].disabled,true);choices[0].checked=true;choices[0].events.change();assert.equal(get('make-package').disabled,false);
 await get('make-package').events.click();assert.equal(get('project').hidden,false);assert.equal(get('source-download').href,'/api/shorts/projects/p/package');assert(!calls.some(c=>c.url.endsWith('/create')));
 get('reply').value='{}';await get('import').events.click();const card=get('ideas').children[0];const box=card.querySelector('input');box.checked=true;box.events.change();assert.equal(get('create-selected').disabled,false);
 card.querySelector('textarea').events.input();assert.equal(get('create-selected').disabled,true,'Unsaved script edits must block creation');await card.querySelector('button').events.click();assert.equal(get('create-selected').disabled,false);
 await get('create-selected').events.click();assert.equal(get('job').hidden,false);assert.equal(get('create-selected').disabled,true);
 project.shorts[0].ready=true;project.shorts[0].video_url='/actual.mp4?v=1';project.job.completed=['s'];await vm.runInContext('poll()',context);let player=get('ideas').children[0].querySelector('video');assert(player);const loads=player.loads;await vm.runInContext('poll()',context);assert.equal(player.loads,loads,'Polling must not reload the current MP4');
 await get('stop').events.click();assert.equal(get('stop').hidden,true);
 const html=fs.readFileSync('app/static/shorts.html','utf8');assert(html.indexOf('controls.css')>html.indexOf('shorts.css'));assert.match(html,/href="\/shorts" aria-current="page"/);
 console.log('Shorts UI: explicit package/import/create, unavailable chapters, dirty script protection, progress, stop and stable native MP4 passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
