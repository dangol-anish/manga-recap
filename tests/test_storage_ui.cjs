const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
class Element {
 constructor(tag='div'){this.tag=tag;this.children=[];this.events={};this.value='';this.checked=false;this.hidden=true;this.disabled=false;}
 append(...items){this.children.push(...items);for(const c of items)c.parent=this;}
 replaceChildren(...items){this.children=[];this.append(...items);}
 addEventListener(name,fn){this.events[name]=fn;}
 remove(){this.parent.children=this.parent.children.filter(c=>c!==this);}
}
const tick=()=>new Promise(r=>setImmediate(r));
(async()=>{
 const nodes=new Map(),get=id=>{if(!nodes.has(id))nodes.set(id,new Element());return nodes.get(id);};
 const calls=[],timers=[];let job=null,fail=false;
 const context=vm.createContext({document:{getElementById:get,createElement:t=>new Element(t)},setTimeout:fn=>timers.push(fn),fetch:async(url,options)=>{
  calls.push({url,options});if(fail)throw Error('Disk could not be read. No changes were made.');
  const body=options?.body?JSON.parse(options.body):{};let value;
  if(url.endsWith('/usage'))value={free_bytes:1024**3,used_bytes:1000,categories:[{name:'Original manga',bytes:200,files:2}],manga:[{name:'<Sample>',bytes:200}]};
  else if(url.endsWith('/backups'))value=[{name:'snapshot.zip',bytes:1000}];
  else if(url.endsWith('/restore/preview'))value={id:'restore-id',token:'restore-token',replace:body.replace,files:[{path:'manga/Sample/0000.png',size:10,action:'replace'}]};
  else if(url.endsWith('/cleanup/preview'))value={id:'cleanup-id',token:'cleanup-token',files:[{path:'.cache/pip/cache',size:100}]};
  else if(url.endsWith('/restore'))job=value={id:'job1',kind:'restore',status:'running',completed:0,total:1,message:'Restoring'};
  else if(url.endsWith('/cleanup'))job=value={id:'job2',kind:'cleanup',status:'running',completed:0,total:1,message:'Cleaning'};
  else value=job;
  return {ok:true,json:async()=>value};
 }});
 vm.runInContext(fs.readFileSync('app/static/storage.js','utf8'),context);await tick();await tick();
 assert(calls.every(c=>!c.options?.method),'Opening storage is read-only');
 assert.equal(get('apply-restore').disabled,true);assert.equal(get('apply-cleanup').disabled,true);assert.equal(get('replace-files').checked,false);
 get('restore-archive').value='snapshot.zip';get('restore-archive').events.change();await get('preview-restore').events.click();
 assert.equal(JSON.parse(calls.at(-1).options.body).replace,false);assert.equal(get('restore-plan').hidden,false);
 get('confirm-restore').checked=true;get('confirm-restore').events.change();assert.equal(get('apply-restore').disabled,true,'Conflicting restore blocked by default');
 get('replace-files').checked=true;get('replace-files').events.change();assert.equal(get('restore-plan').hidden,true);
 await get('preview-restore').events.click();assert.equal(get('confirm-restore').checked,false,'Every new plan needs fresh confirmation');
 get('confirm-restore').checked=true;get('confirm-restore').events.change();assert.equal(get('apply-restore').disabled,false);
 await get('apply-restore').events.click();assert.equal(JSON.parse(calls.at(-1).options.body).confirm,true);assert.equal(get('storage-job').hidden,false);
 job.status='complete';await timers.at(-1)();
 await get('preview-cleanup').events.click();assert.equal(get('apply-cleanup').disabled,true);assert.match(get('cleanup-files').children[0].textContent,/\.cache\/pip\/cache/);
 get('confirm-cleanup').checked=true;get('confirm-cleanup').events.change();await get('apply-cleanup').events.click();
 assert(calls.some(c=>c.url.endsWith('/cleanup')&&JSON.parse(c.options.body).confirm===true));
 job.status='complete';await timers.at(-1)();fail=true;await get('refresh-storage').events.click();
 assert.match(get('storage-notice').textContent,/Disk could not be read/);
 const html=fs.readFileSync('app/static/storage.html','utf8');assert.match(html,/API keys, installed packages, models, caches and runtime logs are excluded/);assert.match(html,/cannot be undone/);assert.match(html,/Off by default/);
 assert(html.indexOf('/static/storage.css')<html.indexOf('/static/controls.css'));
 console.log('Storage UI: read-only opening, explicit previews, conflicts, confirmations, progress and errors passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
