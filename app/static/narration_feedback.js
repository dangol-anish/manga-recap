/* Stage 5 only. Feedback never starts, retries or cancels app work. */
(() => {
  'use strict';
  let serial=0,manual=null;
  const region=document.getElementById('narration-toasts');
  function show(message,kind='info',existing){
    if(!region)return null;
    let card=existing;
    if(!card){card=document.createElement('div');card.className='narration-toast';card.dataset.toastId=String(++serial);
      const text=document.createElement('p'),close=document.createElement('button');close.type='button';close.textContent='Dismiss';close.dataset.feedbackIgnore='true';close.addEventListener('click',()=>card.remove());card.append(text,close);region.append(card);
      while(region.children.length>3)region.children[0].remove();
    }
    card.dataset.kind=kind;card.firstElementChild.textContent=message;
    card.setAttribute('role',kind==='error'?'alert':'status');
    clearTimeout(card.dismissTimer);
    clearTimeout(card.waitTimer);
    if(kind==='loading')card.waitTimer=setTimeout(()=>{card.firstElementChild.textContent=message+' Still waiting after 12 seconds. Check the section or queue status before repeating this action.';},12000);
    if(kind!=='error'&&kind!=='loading')card.dismissTimer=setTimeout(()=>card.remove(),7000);
    return card;
  }
  window.NarrationFeedback={show,begin:message=>show(message,'loading'),finish:(card,message,kind='success')=>show(message,kind,card)};
  document.addEventListener('click',event=>{
    const button=event.target.closest?.('button');
    if(!button||button.disabled||button.dataset.feedbackIgnore||button.id.startsWith('browser-')||region?.contains(button))return;
    const name=(button.getAttribute('aria-label')||button.textContent).trim();
    manual={card:show(name+' requested.','info'),name,at:Date.now()};
  },true);
  const send=window.fetch.bind(window);
  window.fetch=async(url,options)=>{
    const polling=(!options?.method||options.method==='GET')&&/^\/api\/(?:browser-narration(?:$|\?)|narration-extension\/status)/.test(String(url));
    const action=!polling&&manual&&Date.now()-manual.at<1500?manual:null;
    if(action)show(action.name+'…','loading',action.card);
    const controller=action&&!options?.signal?new AbortController():null;
    const timeout=controller?setTimeout(()=>controller.abort(),20000):null;
    try{
      const response=await send(url,controller?{...options,signal:controller.signal}:options);
      if(action&&String(url).startsWith('/api/')){
        let message=response.ok?action.name+' completed.':action.name+' failed. Check the message in this section.';
        let kind=response.ok?'success':'error';
        if(response.clone){try{const value=await response.clone().json();if(value.ok===false){message='Narration needs correction. See the validation messages; saved narration is unchanged.';kind='error';}else if(!response.ok&&typeof value.detail==='string')message=value.detail;}catch(_){}}
        show(message,kind,action.card);
      }
      return response;
    }catch(error){if(controller?.signal.aborted)error=Error('The app did not respond within 20 seconds. Refresh this section before repeating the action; it may already have been accepted. Nothing was resent automatically.');if(action)show(error.message,'error',action.card);throw error;}finally{if(timeout)clearTimeout(timeout);}
  };
  if(typeof MutationObserver!=='undefined')for(const id of ['notice','copy-status','prompt-copy-status','import-status','memory-status','preview-message']){
    const source=document.getElementById(id);if(!source)continue;
    new MutationObserver(()=>{const text=source.textContent.trim();if(text&&manual&&Date.now()-manual.at<120000)show(text,/not imported|failed|unavailable|could not|paste .*first|manually/i.test(text)?'error':/checking|loading|saving/i.test(text)?'loading':'info',manual.card);}).observe(source,{childList:true,subtree:true,characterData:true});
  }
})();
