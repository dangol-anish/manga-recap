/* Shared transport errors. Does not retry actions or start any work. */
(()=>{'use strict';
const send=window.fetch.bind(window),seen=new Set();
const names={downloader:'Stage 1: Downloader',renumbering:'Stage 2: Renumbering',detection:'Stage 3: Panel Detection',preparation:'Stage 4: Panel Review',story:'Stage 5: Narration',speech:'Stage 6: Audio','grouped-audio':'Stage 6: Gemini Audio',cinema:'Stage 7: Video',stitch:'Stage 8: Stitch',shorts:'Shorts'};
function stage(url){const path=String(url).split('?')[0].split('/').filter(Boolean);const key=path[path.indexOf('api')+1];return names[key]||'This app';}
function banner(text){let box=document.getElementById('app-error-alert');if(!box){box=document.createElement('aside');box.id='app-error-alert';box.className='app-error-alert';box.setAttribute('role','alert');const close=document.createElement('button');close.type='button';close.className='secondary';close.textContent='Dismiss';close.addEventListener('click',()=>box.remove());const message=document.createElement('p');message.id='app-error-alert-message';box.append(message,close);(document.querySelector('main')||document.body).prepend(box);}document.getElementById('app-error-alert-message').textContent=text;}
function safe(text){return String(text).replace(/(?:AQ\.[A-Za-z0-9_-]{20,}|AIza[A-Za-z0-9_-]{25,})/g,'[hidden key]').replace(/(https?:\/\/[^\s?#]+)\?[^\s]+/g,'$1?[hidden query]').slice(0,6500);}
window.fetch=async function(url,options={}){
 const method=(options.method||'GET').toUpperCase(),limit=method==='GET'?60000:180000,controller=new AbortController();let timedOut=false;
 const timer=setTimeout(()=>{timedOut=true;controller.abort();},limit);const external=options.signal;
 const abort=()=>controller.abort();if(external){if(external.aborted)abort();else external.addEventListener('abort',abort,{once:true});}
 let response;
 try{response=await send(url,{...options,signal:controller.signal});}
 catch(error){const reason=timedOut?`No response after ${limit/1000} seconds.`:external?.aborted?'This request was cancelled.':'The browser could not reach the local app.';throw Error(`${stage(url)}: ${reason} Check that the app terminal is still running at 127.0.0.1:8000, then refresh. ${method==='GET'?'Saved work is not reset.':'The action may already have reached the app. Check its progress before submitting it again; do not repeatedly click Create or Import.'}`);}
 finally{clearTimeout(timer);external?.removeEventListener('abort',abort);}
 const original=response.json.bind(response);
 response.json=async()=>{let value;
  let bodyTimer;try{value=await Promise.race([original(),new Promise((_,reject)=>{bodyTimer=setTimeout(()=>{controller.abort();reject(Error('Response body timed out'));},limit);})]);}catch(error){const cause=error.name==='SyntaxError'?'The response was not valid JSON.':error.message==='Response body timed out'?'Reading the response timed out.':'The connection ended while reading the response.';const message=`${stage(url)}: the app returned an unreadable response (HTTP ${response.status}). ${cause} Check the app terminal and restart if it has stopped. ${method==='GET'?'Refresh this stage.':'Check saved progress before repeating the action; it may already have been applied.'} Your files have not been reset.`;if(response.ok)throw Error(message);return {detail:message};}finally{clearTimeout(bodyTimer);}
  if(!response.ok&&(!value||typeof value!=='object'))value={detail:`${stage(url)}: request rejected (HTTP ${response.status}). Refresh this stage and check the app terminal.`};
  if(!response.ok){if(value.error?.message)value.detail=safe(value.error.message);else if(Array.isArray(value.detail))value.detail=value.detail.map(e=>(e.loc||[]).join('.')+': '+e.msg).join('; ');else if(typeof value.detail!=='string')value.detail=`${stage(url)}: request rejected (HTTP ${response.status}). Check your selection and the app terminal. Refresh before submitting again.`;}
  return value;
 };
 const ref=response.headers?.get('X-App-Warning');if(ref&&!seen.has(ref)){seen.add(ref);try{const r=await send('/api/errors/'+encodeURIComponent(ref));if(r.ok){const item=await r.json();banner(safe(item.message));}}catch{} }
 return response;
};
document.addEventListener('error',async event=>{const media=event.target;if(!media||!['AUDIO','VIDEO','IMG'].includes(media.tagName))return;if(media.error?.code===1)return;const src=media.currentSrc||media.src;if(!src||seen.has('media:'+src))return;seen.add('media:'+src);let explanation='';try{const target=new URL(src,location.href);if(target.origin===location.origin){const response=await window.fetch(target.href,{headers:{Range:'bytes=0-1023'}});if(!response.ok)explanation=(await response.json()).detail;}}catch(e){explanation=e.message;}banner(explanation||`${media.tagName==='IMG'?'Page image':'Audio/video'} could not load${media.alt?' ('+media.alt+')':''}. ${media.error?.code===3?'The browser could not decode this file.':media.error?.code===4?'The browser cannot play this file or format.':'Check the app connection and saved file.'} Refresh the stage to check whether its source changed. Do not regenerate all chapters or delete existing media.`);},true);
window.addEventListener('unhandledrejection' ,event=>{banner('An interface action did not finish: '+safe(event.reason?.message||event.reason)+'. Check saved progress, then refresh this page. Do not clear your manga or media.');});
window.addEventListener('error',event=>{if(event.message)banner('Interface error: '+safe(event.message)+(event.filename?' · '+event.filename.split('/').pop()+':'+event.lineno:'')+'. Refresh this page; if it repeats, report this message. Saved files have not been reset.');});
})();
