const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../browser-extension/background.js'),'utf8');
function fixture({hidden=false,alwaysHidden=false,pending=false}={}){
  let now=0;
  const state={tabId:7,token:'synthetic',narrationWindowId:null};
  const tab={id:7,windowId:1,active:false,url:'https://aistudio.google.com/prompts/fixture'};
  const events=[];let minimized=false;
  const chrome={storage:{local:{get:async()=>({...state}),set:async value=>Object.assign(state,value)}},
    runtime:{id:'fixture'},tabs:{get:async id=>{assert.equal(id,7);return {...tab};},
      update:async(id,props)=>{assert.equal(id,7);events.push(['tab',props]);Object.assign(tab,props);return {...tab};},
      sendMessage:async(id,message)=>{assert.equal(id,7);events.push(['message',message.action]);
        if(message.action==='ping')return {ready:true,documentId:'fixture',protocol:4};
        return {protocol:4,visible:!hidden,focused:!pending,finished:!pending,generating:pending,replies:[]};}},
    windows:{create:async props=>{events.push(['create',props]);assert.equal(props.tabId,7);tab.windowId=2;tab.active=true;return {id:2};},
      get:async id=>({id,state:minimized?'minimized':'normal'}),
      update:async(id,props)=>{assert.equal(id,2);events.push(['window',props]);if(props.focused&&!alwaysHidden)hidden=false;if(props.state==='normal')minimized=false;}}};
  const context={chrome,Date:{now:()=>now},URL,AbortSignal:{timeout:()=>({})},setTimeout:fn=>{now+=500;fn();},
    fetch:async()=>({ok:true,json:async()=>({permitted:true})})};
  vm.createContext(context);vm.runInContext(source.slice(0,source.indexOf('async function loop(){')),context);
  return {state,tab,events,execute:action=>context.execute({action,payload:{},expiresAt:now+30000,id:'fixture'}),
    minimize:()=>{minimized=true;tab.active=false;},tick:ms=>{now+=ms;}};
}
(async()=>{
  const normal=fixture();
  const hello=await normal.execute('hello');assert.equal(hello.window_visibility,true);
  assert.equal(normal.events.filter(e=>e[0]==='create').length,0,'Connecting alone must not move tabs');
  await normal.execute('snapshot');await normal.execute('snapshot');
  assert.equal(normal.events.filter(e=>e[0]==='create').length,1,'Reuse the same window');
  assert.equal(normal.events.filter(e=>e[0]==='window'&&e[1].focused).length,0,'Visible reads must not steal focus');
  normal.minimize();await normal.execute('snapshot');
  assert(normal.events.some(e=>e[0]==='window'&&e[1].state==='maximized'));
  assert.equal(normal.events.find(e=>e[0]==='create')[1].state,'maximized');
  assert.equal(normal.events.find(e=>e[0]==='create')[1].width,undefined);
  assert(normal.events.some(e=>e[0]==='tab'&&e[1].active));
  await normal.execute('send');assert.equal(normal.events.filter(e=>e[0]==='message'&&e[1]==='send').length,1);
  const hidden=fixture({hidden:true});const reply=await hidden.execute('snapshot');
  assert.equal(reply.visible,true);
  assert.equal(hidden.events.filter(e=>e[0]==='message'&&e[1]==='snapshot').length,2);
  assert.equal(hidden.events.filter(e=>e[0]==='message'&&e[1]==='send').length,0);
  const blocked=fixture({hidden:true,alwaysHidden:true});const unread=await blocked.execute('snapshot');
  assert.equal(unread.finished,false);assert.equal(unread.generating,true);
  assert.equal(blocked.events.filter(e=>e[0]==='message'&&e[1]==='snapshot').length,2,'Wake retries must be bounded');
  const pending=fixture({pending:true});await pending.execute('snapshot');
  assert.equal(pending.events.filter(e=>e[0]==='window'&&e[1].focused).length,0);
  pending.tick(15000);await pending.execute('snapshot');await pending.execute('snapshot');
  assert.equal(pending.events.filter(e=>e[0]==='window'&&e[1].focused).length,1,'Pending unfocused replies wake at most once per 15 seconds');
  assert.equal(normal.events.filter(e=>e[0]==='tab'&&e[1].url).length,0,'Reads must not navigate');
  console.log('Paired window reuse, activation, hidden-render wake and no-resend checks passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
