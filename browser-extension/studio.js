/* Visible DOM controls only. No page internals, network interception, login or keys. */
(() => {
  'use strict';
  if(globalThis.__mangaNarrationExtension===4)return;
  globalThis.__mangaNarrationExtension=4;
  const wait=ms=>new Promise(resolve=>setTimeout(resolve,ms));
  const visible=e=>!!(e.getClientRects().length)&&getComputedStyle(e).visibility!=='hidden';
  const label=e=>{
    const ids=e.getAttribute('aria-labelledby');
    return (e.getAttribute('aria-label')||(ids?ids.split(/\s+/).map(id=>document.getElementById(id)?.textContent||'').join(' '):'')||e.innerText||e.textContent||e.title||'').trim();
  };
  const documentId=crypto.randomUUID();
  const elements=(selector,name)=>[...document.querySelectorAll(selector)].filter(e=>visible(e)&&(typeof name==='string'?label(e)===name:name.test(label(e))));
  function fail(message,code='settings',retry_safe=true){const error=Error(message);Object.assign(error,{code,retry_safe});throw error;}
  function one(selector,name){const found=elements(selector,name);if(found.length!==1)fail(`Could not identify ${name} in AI Studio. Check Run settings and the selected tab, then Resume.`);return found[0];}
  let actionDeadline=Infinity;
  async function until(fn,timeout=600000){const end=Math.min(Date.now()+timeout,actionDeadline);while(Date.now()<end){check();const result=fn();if(result)return result;await wait(200);}fail('AI Studio controls did not become ready within the 10-minute preparation window. Nothing was submitted. Check the tab before resuming.');}
  function check(){
    // Read provider notices, never quoted story dialogue or the user's prompt.
    const text=platformText();
    if(/captcha|verify (?:that )?you are human|unusual traffic|suspicious activity|access restricted/i.test(text))fail('AI Studio needs a human access check. Complete it yourself before resuming.','access');
    if(/quota.{0,80}(?:exceeded|exhausted)|(?:daily|usage|rate) limit (?:reached|exceeded)|too many requests|you(?: have|.ve) reached.{0,60}limit/i.test(text))fail('AI Studio reports a usage limit. Progress is saved. Wait until access is available, then Resume.','limit');
    if(/failed to generate|an internal error occurred|something went wrong|content.{0,25}blocked/i.test(text))fail('AI Studio did not finish normally. Inspect its last request; nothing will be resent automatically.','generation',false);
  }
  function fill(element,value){
    element.focus();
    const proto=element.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:HTMLInputElement.prototype;
    if(element.matches('textarea,input')){
      element.select();
      // Native editing also updates editor widgets which ignore a value setter.
      if(!document.execCommand('insertText',false,value))Object.getOwnPropertyDescriptor(proto,'value').set.call(element,value);
    }
    else if(element.isContentEditable)element.textContent=value;
    else fail('The prompt editor changed. Nothing was submitted.');
    element.dispatchEvent(new InputEvent('input',{bubbles:true,inputType:'insertText',data:value}));element.dispatchEvent(new Event('change',{bubbles:true}));element.blur();
  }
  const buttons='button,[role="button"]';
  const run=()=>elements(buttons,/^Run(?:\s|$)/);
  const enabled=e=>!e.disabled&&e.getAttribute('aria-disabled')!=='true';
  function platformText(){return [...document.querySelectorAll('[role="alert"],[role="alertdialog"],mat-snack-bar-container,ms-error-message,.error-message')].filter(visible).map(e=>e.innerText).join('\n').slice(0,5000);}
  function modelReplies(){
    const roots=[];
    for(const good of elements(buttons,/^Good response$/)){
      let root=good.closest('ms-chat-turn,[data-message-author-role="model"],[data-message-author-role="assistant"],[data-role="model"]');
      if(!root){
        let parent=good.parentElement;
        for(let i=0;parent&&i<9;i++,parent=parent.parentElement){
          if(parent===document.body||parent.tagName==='MAIN')break;
          const goodCount=[...parent.querySelectorAll(buttons)].filter(e=>/^Good response$/.test(label(e))).length;
          if(goodCount===1&&parent.querySelector('pre,ms-code-block,ms-text-chunk,.markdown,[class*="markdown"]')){root=parent;break;}
        }
      }
      if(root&&!roots.includes(root))roots.push(root);
    }
    // Completed empty/refused turns may have no feedback buttons.
    for(const root of document.querySelectorAll('ms-chat-turn,[data-message-author-role="model"],[data-message-author-role="assistant"]')){
      if(!visible(root)||roots.includes(root))continue;
      const role=root.getAttribute('data-message-author-role');
      const modelHeader=[...root.querySelectorAll('h3,[role="heading"]')].some(e=>/^Model(?:\s|$)/.test(label(e)));
      if((role==='assistant'||role==='model'||modelHeader)&&!roots.some(e=>e.contains(root)||root.contains(e)))roots.push(root);
    }
    roots.sort((a,b)=>a.compareDocumentPosition(b)&Node.DOCUMENT_POSITION_FOLLOWING?-1:1);
    return roots.map((root,index)=>{
      const blocks=[...root.querySelectorAll('ms-code-block,pre')];
      const top=blocks.filter(e=>!blocks.some(other=>other!==e&&other.contains(e)));
      let text;
      if(top.length){text=top.map(block=>{
        const code=block.querySelector('code')||block.querySelector('pre');if(code)return code.textContent;
        const copy=block.cloneNode(true);copy.querySelectorAll('button,[role="button"],[class*="toolbar"]').forEach(e=>e.remove());
        return copy.textContent.replace(/^\s*JSON\s*(?=[{\[])/i,'');
      }).join('\n');}
      else{
        const copy=root.cloneNode(true);copy.querySelectorAll('button,[role="button"],h1,h2,h3,[role="heading"],ms-thought-chunk,[class*="thought"],[class*="footer"],[class*="timestamp"]').forEach(e=>e.remove());
        text=copy.textContent;
      }
      const feedback=[...root.querySelectorAll(buttons)].some(e=>/^Good response$/.test(label(e)));
      return {id:root.id||`assistant-${index}`,text:(text||'').trim(),completed:feedback};
    });
  }
  function snapshot(){
    check();
    const replies=modelReplies();
    const generating=!!elements(buttons,/^(?:Stop|Cancel)(?:\s|$)/i).length||
      !!elements('[aria-busy="true"],[role="progressbar"],mat-progress-spinner',/.*/).length;
    // Completion belongs to the latest model turn, never a stale page-wide notice.
    return {protocol:4,url:location.href,visible:document.visibilityState==='visible',focused:document.hasFocus(),status_text:platformText(),replies,generating,
      finished:!generating&&replies.at(-1)?.completed===true};
  }
  async function readSnapshot(){
    // Only expand code in the latest model turn with feedback controls.
    // Never expand/capture a formatting example in the user's prompt.
    const good=elements(buttons,'Good response').at(-1);
    const root=good?.closest('ms-chat-turn,[data-message-author-role="model"],[data-message-author-role="assistant"]');
    if(root){
      const expand=[...root.querySelectorAll(buttons)].filter(e=>visible(e)&&label(e)==='Expand code snippet');
      if(expand.length===1){expand[0].click();await wait(250);}
    }
    return snapshot();
  }
  function modelSurface(){
    // The current model is a non-button card in newer Run settings layouts.
    for(const heading of elements('h1,h2,h3,h4,[role="heading"],span,div,p',/^Run settings$/i)){
      for(let root=heading.parentElement;root&&root!==document.body;root=root.parentElement){
        if(root.querySelector('[aria-label="Enter a prompt"]'))break;
        const switches=[...root.querySelectorAll('[role="switch"]')];
        if(switches.some(e=>visible(e)&&/^Structured outputs$/i.test(label(e))))return root;
      }
    }
    // Retain the earlier model-button layout, without matching prompt text.
    return elements(buttons,/^Gemini\s+3\.8\s+Flash(?:\s|$)/i)[0]||null;
  }
  function expectedModel(surface){
    const nodes=[surface,...surface.querySelectorAll('h1,h2,h3,h4,span,div,p,button,[role="button"]')];
    return nodes.some(e=>visible(e)&&!e.closest('[role="listbox"],[role="menu"],[role="dialog"]')&&
      /^(?:Gemini\s+3\.8\s+Flash|gemini-3\.8-flash)$/i.test(label(e).replace(/\s+/g,' ').trim()))||
      (surface.matches(buttons)&&/^Gemini\s+3\.8\s+Flash(?:\s|$)/i.test(label(surface)));
  }
  async function configure(outputSchema,report){
    await report('configuring');
    check();
    const model=await until(()=>modelSurface());
    if(!expectedModel(model))fail('Run settings shows a different model. Select Gemini 3.8 Flash, then Resume. Nothing was submitted.');
    const thinking=one('[role="combobox"]',/^Thinking level$/i);
    if(!/medium/i.test(thinking.innerText)){
      thinking.click();(await until(()=>elements('[role="option"]','Medium')[0])).click();
      await until(()=>/medium/i.test(thinking.innerText));
    }
    for(const name of ['Code execution','Function calling','Grounding with Google Search','Grounding with Google Maps','Browse the url context','URL context']){
      const e=elements('[role="switch"]',name)[0];if(e?.getAttribute('aria-checked')==='true')e.click();
    }
    const structured=one('[role="switch"]','Structured outputs');
    if(structured.getAttribute('aria-checked')!=='true')structured.click();
    await until(()=>elements(buttons,'Save the current schema').length||elements(buttons,'Edit JSON schema').length);
    if(!elements(buttons,'Save the current schema').length)one(buttons,'Edit JSON schema').click();
    (await until(()=>elements('[role="tab"]','Code Editor')[0])).click();
    const schema=await until(()=>elements('textarea,input,[contenteditable="true"]','Enter a JSON schema')[0]);
    if(!outputSchema?.properties?.beats||outputSchema.properties.response)fail('The narration output schema is missing or outdated. Restart the app before resuming.');
    fill(schema,JSON.stringify(outputSchema));await wait(300);one(buttons,'Save the current schema').click();
    await until(()=>structured.getAttribute('aria-checked')==='true'&&!elements(buttons,'Save the current schema').length);
    // Read the persisted editor back. Never assume a programmatic fill saved.
    await report('checking_schema');
    one(buttons,'Edit JSON schema').click();
    const saved=await until(()=>elements('textarea,input,[contenteditable="true"]','Enter a JSON schema')[0]);
    let persisted;try{persisted=JSON.parse(saved.value??saved.textContent);}catch(_){fail('AI Studio did not save a readable narration schema. No request was submitted.','schema');}
    // AI Studio adds propertyOrdering on every object when saving. Accept
    // only that known addition, containing each actual property exactly once.
    // Never ignore removed properties, required fields or other constraints.
    function comparable(actual,expected){
      if(Array.isArray(actual))return actual.map((value,index)=>comparable(value,expected?.[index]));
      if(!actual||typeof actual!=='object')return actual;
      const keys=Object.keys(actual).sort();
      const order=actual.propertyOrdering,properties=actual.properties;
      const addedOrder=!Object.hasOwn(expected||{},'propertyOrdering')&&properties&&typeof properties==='object'&&!Array.isArray(properties)&&Array.isArray(order)&&order.every(key=>typeof key==='string')&&JSON.stringify([...order].sort())===JSON.stringify(Object.keys(properties).sort());
      return Object.fromEntries(keys.filter(key=>!(key==='propertyOrdering'&&addedOrder)).map(key=>[key,comparable(actual[key],expected?.[key])]));
    }
    const matches=JSON.stringify(comparable(persisted,outputSchema))===JSON.stringify(comparable(outputSchema,outputSchema));
    if(!matches)fail('AI Studio saved a different narration schema (beyond its normal property ordering). No request was submitted. Check the schema editor before resuming.','schema');
    one(buttons,'Save the current schema').click();
    await until(()=>!elements(buttons,'Save the current schema').length);
  }
  async function prepare(payload,expiresAt,report){
    check();
    const prompt=await until(()=>elements('textarea,input,[contenteditable="true"]','Enter a prompt')[0]);
    await configure(payload.schema,report);
    if(Date.now()>expiresAt)fail('Preparing this batch expired. Nothing was submitted.');
    await report('inserting_prompt');fill(prompt,payload.prompt);
    if(payload.photos.length){
      await report('attaching_photos');
      const inputs=[...document.querySelectorAll('input[type="file"]')];
      if(inputs.length!==1)fail('The photo upload control changed. No request was submitted.','upload');
      const transfer=new DataTransfer();
      for(const file of payload.photos){
        const binary=atob(file.data),bytes=Uint8Array.from(binary,c=>c.charCodeAt(0));
        transfer.items.add(new File([bytes],file.name,{type:'image/jpeg'}));
      }
      inputs[0].files=transfer.files;inputs[0].dispatchEvent(new Event('change',{bubbles:true}));
      if(inputs[0].files.length!==payload.photos.length)fail('The browser did not attach every photo. Check attachments before resuming.','upload');
      // Some uploads enable Run briefly before their progress controls appear.
      // Require a stable ready state rather than a fixed upload delay alone.
      let stable=0;
      await report('waiting_upload');
      await until(()=>{
        check();const runs=run();
        const ready=runs.length===1&&enabled(runs[0])&&!elements('[role="progressbar"],mat-progress-spinner',/.*/).length;
        stable=ready?stable+1:0;return stable>=10;
      });
    }
    return {url:location.href};
  }
  let busy=false;
  chrome.runtime.onMessage.addListener((message,sender,reply)=>{
    if(sender.id!==chrome.runtime.id||message.type!=='narration-command')return;
    if(message.action==='ping'){reply({ready:true,documentId,protocol:4});return;}
    if(busy){reply({error:'An earlier action is still running in this AI Studio tab. Inspect it before resuming.',code:'recovery'});return;}
    busy=true;
    actionDeadline=message.expiresAt;
    (async()=>{
      if(Date.now()>message.expiresAt)fail('This browser action expired. Nothing was submitted.','recovery',false);
      if(message.action==='snapshot')return readSnapshot();
      if(message.action==='prepare')return prepare(message.payload,message.expiresAt,async step=>{
        const value=await chrome.runtime.sendMessage({type:'progress',id:message.commandId,step});
        if(!value||value.error)fail(value?.error||'The app did not confirm browser progress. Check the connection before resuming.','extension',false);
      });
      if(message.action==='send'){
        check();const button=one(buttons,/^Run(?:\s|$)/);
        if(!enabled(button))fail('Run is unavailable. Check uploads and settings; nothing was submitted.');
        button.click();return {url:location.href};
      }
      fail('Unknown narration action.','extension',false);
    })().then(reply,error=>reply({error:error.message,code:error.code||'browser',retry_safe:error.retry_safe===true})).finally(()=>busy=false);
    return true;
  });
})();
