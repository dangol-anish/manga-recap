'use strict';
const BASE='http://127.0.0.1:8000/api/narration-extension';
let looping=false;
let lastRenderWake=0;
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function api(path,body,token){
  const response=await fetch(BASE+path,{method:'POST',headers:{'Content-Type':'application/json','X-Narration-Extension':chrome.runtime.id,...(token?{Authorization:'Bearer '+token}:{})},body:JSON.stringify(body||{}),signal:AbortSignal.timeout(12000)});
  const value=await response.json();
  if(!response.ok)throw Error(typeof value.detail==='string'?value.detail:'Local browser connection failed. Check the app and pair again.');
  return value;
}
async function chosenTab(){
  const {tabId}=await chrome.storage.local.get('tabId');
  if(!Number.isInteger(tabId))throw Error('Open AI Studio in your normal browser, then connect that tab from the extension popup.');
  const tab=await chrome.tabs.get(tabId);
  if(!tab.url?.startsWith('https://aistudio.google.com/'))throw Error('The selected tab is not ready in AI Studio. Finish signing in yourself, then Resume in the app.');
  return tab;
}
async function renderingWindow(tab){
  // One paired tab in its own window can stay active while Stage 5 is viewed.
  const {narrationWindowId}=await chrome.storage.local.get('narrationWindowId');
  if(narrationWindowId!==tab.windowId){
    const window=await chrome.windows.create({tabId:tab.id,type:'normal',focused:true,state:'maximized'});
    lastRenderWake=Date.now();
    await chrome.storage.local.set({narrationWindowId:window.id});
    tab=await chrome.tabs.get(tab.id);
  }
  const window=await chrome.windows.get(tab.windowId);
  if(window.state!=='maximized')await chrome.windows.update(window.id,{state:'maximized'});
  if(!tab.active)await chrome.tabs.update(tab.id,{active:true});
  return tab;
}
async function ready(tabId,previousDocument,expiresAt=Date.now()+25000){
  for(let i=0;i<60&&Date.now()<expiresAt;i++){
    try{const value=await chrome.tabs.sendMessage(tabId,{type:'narration-command',action:'ping'});if(value?.ready&&value.documentId!==previousDocument)return value;}catch(_){}
    await delay(500);
  }
  throw Error('The AI Studio tab did not become ready. Finish signing in yourself, then Resume.');
}
async function execute(command){
  if(!['hello','prepare','snapshot','send','recover'].includes(command.action))throw Error('Unknown narration action.');
  let tab=await chosenTab();
  const token=(await chrome.storage.local.get('token')).token;
  const progress=step=>api('/progress',{id:command.id,step},token);
  if(Date.now()>command.expiresAt)throw Error('This browser action expired. Inspect the tab before resuming.');
  if(command.action==='hello'){const value=await ready(tab.id,undefined,command.expiresAt);return {url:tab.url,protocol:value.protocol,window_visibility:true};}
  tab=await renderingWindow(tab);
  if(command.action==='recover'||(command.action==='prepare'&&command.payload.fresh)){
    const url=command.action==='recover'?command.payload.url:'https://aistudio.google.com/prompts/new_chat';
    const parsed=new URL(url);
    if(parsed.origin!=='https://aistudio.google.com'||!parsed.pathname.startsWith('/prompts/')||parsed.search||parsed.hash)throw Error('Invalid AI Studio conversation address.');
    let previousDocument;
    await progress('opening_tab');
    try{previousDocument=(await chrome.tabs.sendMessage(tab.id,{type:'narration-command',action:'ping'}))?.documentId;}catch(_){}
    await chrome.tabs.update(tab.id,{url});
    // Wait for the new document, so an old content script cannot prepare a new batch.
    let loaded=false;
    for(let i=0;i<70&&Date.now()<command.expiresAt;i++){await delay(300);const current=await chrome.tabs.get(tab.id);if(current.status==='complete'&&current.url===url){loaded=true;break;}}
    if(!loaded)throw Error('AI Studio navigation did not reach the requested chat. Nothing was submitted. Check the selected tab and resume.');
    await ready(tab.id,previousDocument,command.expiresAt);
    if(command.action==='recover')return {url};
  }else await ready(tab.id,undefined,command.expiresAt);
  if(Date.now()>command.expiresAt)throw Error('This browser action expired. Inspect the tab before resuming.');
  if(command.action==='send'){await progress('submitting');await api('/permit',{id:command.id},token);}
  const message={type:'narration-command',action:command.action,payload:command.payload,expiresAt:command.expiresAt,commandId:command.id};
  let value=await chrome.tabs.sendMessage(tab.id,message);
  const unfocusedPending=value?.focused===false&&!value?.finished&&Date.now()-lastRenderWake>=15000;
  if(command.action==='snapshot'&&(value?.visible===false||unfocusedPending)){
    // Some Windows browsers also defer rendering when a window is occluded.
    // Wake only this paired window; never replay send/prepare or reload the chat.
    await chrome.windows.update(tab.windowId,{focused:true});
    lastRenderWake=Date.now();
    await delay(500);
    if(Date.now()>command.expiresAt)throw Error('The reply read expired while waking AI Studio. Inspect its tab before resuming.');
    value=await chrome.tabs.sendMessage(tab.id,message);
    if(value?.visible===false)return {...value,generating:true,finished:false};
  }
  return value;
}
async function loop(){
  if(looping)return;looping=true;
  try{
    while(true){
      const {token}=await chrome.storage.local.get('token');if(!token)break;
      try{
        const {command}=await api('/poll',{},token);
        await chrome.storage.local.set({connectionNote:'Connected to the local app.'});
        if(command){
          let value;
          const heartbeat=setInterval(()=>api('/heartbeat',{},token).catch(()=>{}),5000);
          try{value=await execute(command);}catch(error){value={error:error.message,code:'extension',retry_safe:command.action==='prepare'};}finally{clearInterval(heartbeat);}
          // Retrying this acknowledgement is safe; executing the command again is not.
          let saved=false;
          for(let i=0;i<3&&!saved;i++){try{await api('/result',{id:command.id,value},token);saved=true;}catch(_){await delay(500);}}
          if(!saved)throw Error('The app did not acknowledge the last browser action. Check queue status before resuming.');
        }
      }catch(error){await chrome.storage.local.set({connectionNote:error.message});break;}
      await delay(2000);
    }
  }finally{looping=false;}
}
chrome.runtime.onMessage.addListener((message,sender,reply)=>{
  if(sender.id!==chrome.runtime.id)return;
  if(message.type==='progress'&&sender.tab&&sender.url?.startsWith('https://aistudio.google.com/')){
    chrome.storage.local.get(['token','tabId']).then(async value=>{
      if(value.tabId!==sender.tab.id)throw Error('This tab is not paired.');
      return api('/progress',{id:message.id,step:message.step},value.token);
    }).then(reply,error=>reply({error:error.message}));return true;
  }
  if(!sender.url?.startsWith(chrome.runtime.getURL('')))return;
  if(message.type==='pair'){
    (async()=>{
      const [tab]=await chrome.tabs.query({active:true,currentWindow:true});
      if(!tab?.url?.startsWith('https://aistudio.google.com/'))throw Error('Open AI Studio and finish signing in first. Then reopen this popup on that tab.');
      const value=await api('/pair',{code:message.code});
      await chrome.storage.local.set({token:value.token,tabId:tab.id,narrationWindowId:null,connectionNote:'Connected to this AI Studio tab.'});
      loop();return {ok:true};
    })().then(reply,error=>reply({error:error.message}));return true;
  }
  if(message.type==='wake'){loop();reply({ok:true});}
});
chrome.alarms.create('narration-connection',{periodInMinutes:.5});
chrome.alarms.onAlarm.addListener(alarm=>{if(alarm.name==='narration-connection')loop();});
chrome.runtime.onStartup.addListener(()=>loop());
loop();
