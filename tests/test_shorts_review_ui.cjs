const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
class Element{
 constructor(tag='div'){this.tag=tag;this.children=[];this.events={};this.dataset={};this.hidden=false;this.readyState=1;this.currentTime=0;this.isConnected=true;}
 append(...nodes){for(const n of nodes)n.parent=this;this.children.push(...nodes);}replaceChildren(...nodes){this.children=[];this.append(...nodes);}setAttribute(k,v){this[k]=v;}
 addEventListener(e,fn){this.events[e]=fn;}querySelectorAll(s){return this.children.flatMap(c=>[...(s.startsWith('.')?c.className===s.slice(1):c.tag===s)?[c]:[],...c.querySelectorAll(s)]);}
 querySelector(s){return this.querySelectorAll(s)[0]||null;}remove(){this.parent.children=this.parent.children.filter(c=>c!==this);this.isConnected=false;}pause(){this.paused=true;}scrollIntoView(){}
}
(async()=>{
 const card=new Element(),video=new Element('video');card.append(video);
 let calls=[],fail=false,pending=null;
 const row={number:1,chapter:'Chapter 1',panel_id:'p0000-1',narration:'A <short> sentence.',source_narration:'A full original source beat.',source_unsure:'Identity unclear.',duration:4,start:2,end:6,measured:true,panel_url:'/panel',captions:[{start:2,end:4,text:'A short sentence.'}]};
 const data={revision:'r',word_count:80,duration:34,all_measured:true,measured_segments:1,segments:[row],opening:row.narration,ending:row.narration,notices:[{segment:1,text:'Check uncertainty.'}],guidance:'No automatic verification.'};
 const context=vm.createContext({document:{createElement:t=>new Element(t)},fetch:async(url,options)=>{calls.push({url,options});if(pending)await pending;return {ok:!fail,json:async()=>fail?{detail:'Sources changed. Refresh.'}:data};}});
 vm.runInContext(fs.readFileSync('app/static/shorts_review.js','utf8'),context);
 assert.equal(calls.length,0);await context.ShortsReview.open('p','s','r',card);
 assert.equal(calls.length,1);assert.equal(calls[0].options,undefined,'Review is read only');
 assert(card.querySelectorAll('p').some(n=>n.textContent==='A <short> sentence.'),'Source text is not inserted as HTML');
 assert(card.querySelectorAll('p').some(n=>n.textContent==='A full original source beat.'));
 let jump=card.querySelectorAll('button').find(b=>b.textContent==='Jump to this panel in saved video');jump.events.click();assert.equal(video.currentTime,2);assert.equal(video.paused,true);
 assert(card.querySelectorAll('span').some(n=>n.textContent==='A short sentence.'));
 context.ShortsReview.invalidate(card);assert.equal(card.querySelector('.short-review'),null,'Editing removes old review immediately');
 let release;pending=new Promise(r=>release=r);const inFlight=context.ShortsReview.open('p','s','r',card);context.ShortsReview.invalidate(card);release();await inFlight;assert.equal(card.querySelector('.short-review'),null,'Late result cannot restore a review for an edited script');pending=null;
 await context.ShortsReview.open('p','s','different',card);assert(card.querySelectorAll('p').some(n=>n.textContent.includes('another tab')));
 fail=true;await context.ShortsReview.open('p','s','r',card);assert(card.querySelectorAll('p').some(n=>n.textContent.includes('Sources changed')));
 console.log('Shorts review UI: source comparison, caption chunks, measured seeking, stale responses and read-only actions passed.');
})().catch(e=>{console.error(e);process.exit(1);});
