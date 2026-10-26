const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
class Element {
 constructor(tag='div'){this.tag=tag;this.children=[];this.events={};this.value='all';this.open=false;}
 append(...v){this.children.push(...v);}prepend(...v){this.children.unshift(...v);}replaceChildren(...v){this.children=v;}
 addEventListener(e,fn){this.events[e]=fn;}
 querySelectorAll(tags){return this.children.flatMap(c=>[...(tags.split(',').includes(c.tag)?[c]:[]),...c.querySelectorAll(tags)]);}
}
(async()=>{
 const nodes=new Map(),get=id=>{if(!nodes.has(id))nodes.set(id,new Element());return nodes.get(id);};
 const calls=[],events=[];
 let data={revision:'r',items:[{id:'f',kind:'fact',text:'A <claim>.',chapter:'Chapter 1',batch_id:'batch-001',panel_ids:['p0000-1'],ai_status:'confirmed',pinned:false,verified:false,resolved:false,current:true},{id:'t',kind:'thread',text:'Where is he?',chapter:'Chapter 1',batch_id:'batch-001',panel_ids:[],ai_status:'uncertain',pinned:false,verified:false,resolved:false,current:true}]}, fail=false;
 const context=vm.createContext({document:{getElementById:get,createElement:t=>new Element(t)},Event:class{constructor(type){this.type=type;}},dispatchEvent:e=>events.push(e.type),fetch:async(url,options)=>{
  calls.push({url,options});if(options?.body){const body=JSON.parse(options.body);data=JSON.parse(JSON.stringify(data));Object.assign(data.items.find(r=>r.id===body.id),body);data.revision='next';}
  return {ok:!fail,json:async()=>fail?{detail:'Memory changed. Reload.'}:data};
 }});
 vm.runInContext(fs.readFileSync('app/static/story_memory.js','utf8'),context);
 context.StoryMemory.change('a');assert.equal(calls.length,0,'Closed section must not fetch');
 get('memory-panel').open=true;get('memory-panel').events.toggle();await new Promise(r=>setImmediate(r));
 assert.equal(calls.length,1);assert.equal(get('memory-items').children.length,2);
 let card=get('memory-items').children[0];assert.equal(card.children[0].textContent,'AI claim · AI marked confident');assert.equal(card.children[1].textContent,'A <claim>.');
 const boxes=card.querySelectorAll('input');boxes[0].checked=true;
 assert.equal(calls.length,1,'Editing a checkbox never writes until Save');
 await card.querySelectorAll('button')[0].onclick();
 assert.equal(JSON.parse(calls[1].options.body).verified,false,'Pin does not verify');assert.deepEqual(events,['story-memory-saved']);
 get('memory-filter').value='open';get('memory-filter').onchange();assert.equal(get('memory-items').children.length,1);
 card=get('memory-items').children[0];card.querySelectorAll('input')[1].checked=true;await card.querySelectorAll('button')[0].onclick();assert.equal(data.items[1].resolved,true);
 fail=true;await get('memory-reload').onclick();assert.match(get('memory-status').textContent,/Memory changed/);
 context.StoryMemory.change('b');assert.equal(get('memory-items').children.length,0,'Other manga facts must disappear immediately');
 console.log('Story memory UI checks passed');
})().catch(e=>{console.error(e);process.exit(1);});
