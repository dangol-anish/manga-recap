(() => {
  'use strict';
  const get = id => document.getElementById(id);
  const make = (tag, text) => { const e=document.createElement(tag); if(text!==undefined)e.textContent=text; return e; };
  let viewEpoch=0,attentionToast=null;
  let groups=[], job=null, busy=false, timer=null, lastNotice='', sound=null, connected=false, setupBusy=false, stopBusy=false, refreshing=false, receivedAt=Date.now(), lastStatusAt=Date.now();
  const begin=text=>window.NarrationFeedback?.begin(text);
  const finish=(card,text,kind='success')=>window.NarrationFeedback?.finish(card,text,kind);
  const selected=new Set();
  async function request(url, body) {
    const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),20000);
    try{
    const response=await fetch(url,body===undefined?{signal:controller.signal}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal:controller.signal});
    let value;
    try { value=await response.json(); } catch (_) { throw Error('Could not read the narration queue response. Refresh queue status before retrying; a request may already have been accepted.'); }
    if(!response.ok)throw Error(value.detail||'Narration queue request failed. Refresh its status before retrying.');
    return value;
    }catch(error){if(controller.signal.aborted)throw Error('The app did not respond within 20 seconds. Check queue status before retrying; an action may already have reached the app. Nothing will be resent automatically.');throw error;}
    finally{clearTimeout(timeout);}
  }
  function controls() {
    const running=job&&['running','stopping'].includes(job.status);
    get('browser-start').disabled=busy||setupBusy||running||!selected.size;
    get('browser-start-note').textContent=running?'The selected queue is running.':!selected.size?'Choose prepared chapters to start.':!connected?'Connect AI Studio first. Get a pairing code above, connect the extension on your AI Studio tab, then return here. Start will check the connection again.':'Ready. Start will check your browser connection before submitting the selected chapters.';
    get('browser-manga').disabled=busy||setupBusy||running;
    get('browser-catalog-refresh').disabled=busy||setupBusy||running;
    get('browser-resume').hidden=!job||!['paused','failed'].includes(job.status);
    get('browser-resume').disabled=busy||setupBusy;
    const rejected=job&&job.status==='paused'&&job.phase==='reply_saved'&&['validation','response'].includes(job.attention);
    get('browser-retry-batch').hidden=!rejected;get('browser-retry-batch').disabled=busy||setupBusy;
    if(rejected)get('browser-resume').hidden=true;
    const clearable=job&&['paused','failed','complete'].includes(job.status);
    get('browser-clear').hidden=!clearable;get('browser-clear-note').hidden=!clearable;get('browser-clear').disabled=busy||setupBusy||stopBusy;
    get('browser-stop').hidden=!job||job.status==='complete';
    get('browser-stop').disabled=stopBusy||job?.status==='stopping';
    get('browser-stop').textContent=job?.status==='stopping'?'Stopping…':'Stop queue';
    for(const id of ['browser-start','browser-resume','browser-retry-batch','browser-stop','browser-catalog-refresh','browser-extension-folder','browser-pair-code','browser-connection-refresh','browser-disconnect','browser-pair-copy']){
      const e=get(id);e.title=e.disabled?(running?'Wait for the current queue or use Stop queue.':busy||setupBusy||stopBusy?'This action is already being processed.':'Choose prepared chapters first.'):'';
    }
    for(const id of ['browser-extension-folder','browser-pair-code','browser-connection-refresh','browser-disconnect','browser-pair-copy'])get(id).disabled=setupBusy;
  }
  function chapters() {
    selected.clear(); get('browser-chapters').replaceChildren();
    const group=groups.find(g=>g.id===get('browser-manga').value);
    for(const chapter of group?.chapters||[]) {
      const label=make('label'),box=make('input');box.type='checkbox';box.value=chapter.name;box.disabled=!chapter.ready;
      box.addEventListener('change',()=>{box.checked?selected.add(chapter.name):selected.delete(chapter.name);selectionNote();controls();});
      label.append(box,make('span',chapter.name+(chapter.ready?'':' · Finish Stage 4')));get('browser-chapters').append(label);
    }
    selectionNote(); controls();
  }
  function selectionNote(){get('browser-selection-note').textContent=selected.size?`${selected.size} chapters selected. Current imports will be reused; unfinished batches run in natural chapter order.`:'Select prepared chapters. Unprepared chapters stay unavailable.';}
  async function catalog(){
    const previous=get('browser-manga').value;
    const previousSelection=new Set(selected);
    groups=await request('/api/story/catalog');get('browser-manga').replaceChildren();
    const empty=make('option','Choose a manga');empty.value='';get('browser-manga').append(empty);
    for(const group of groups){const option=make('option',group.name);option.value=group.id;get('browser-manga').append(option);}
    if(groups.some(g=>g.id===previous))get('browser-manga').value=previous;
    chapters();
    if(get('browser-manga').value===previous){
      for(const label of get('browser-chapters').children){const box=label.children[0];if(!box.disabled&&previousSelection.has(box.value)){box.checked=true;selected.add(box.value);}}
      selectionNote();controls();
    }
  }
  async function connectionStatus(){
    const value=await request('/api/narration-extension/status');connected=value.connected;
    get('browser-connection-status').textContent=connected?'Browser extension connected. Ready to start or resume.':value.paired?'Extension paired but not responding. Open its popup in your browser, then check again.':'Connect the extension to your AI Studio tab using a pairing code.';
    controls();
    return connected?'Connection checked: browser extension is connected.':'Connection checked: this app session is not connected. Get a new pairing code and connect it from the extension popup on your AI Studio tab.';
  }
  async function setupAction(fn){
    const card=begin('Updating narration controls…');
    if(setupBusy){finish(card,'An action is already in progress.','info');return;}setupBusy=true;controls();get('browser-error').textContent='';get('browser-setup-feedback').textContent='Working…';
    try{const message=await fn();get('browser-setup-feedback').textContent=typeof message==='string'?message:'Done.';finish(card,get('browser-setup-feedback').textContent);}
    catch(error){get('browser-setup-feedback').textContent=error.message;get('browser-error').textContent=error.message;finish(card,error.message,'error');}
    finally{setupBusy=false;controls();}
  }
  get('browser-extension-folder').addEventListener('click',()=>setupAction(async()=>{await request('/api/narration-extension/open-folder',{});return 'Extension folder opened in Finder or File Explorer.';}));
  get('browser-connection-refresh').addEventListener('click',()=>setupAction(connectionStatus));
  get('browser-disconnect').addEventListener('click',()=>setupAction(async()=>{await request('/api/narration-extension/disconnect',{});await connectionStatus();return 'Extension disconnected. Saved narration and queue progress are retained.';}));
  get('browser-pair-code').addEventListener('click',()=>setupAction(async()=>{
    const value=await request('/api/narration-extension/code',{});get('browser-pair-value').value=value.code;get('browser-pair-box').hidden=false;
    return 'New pairing code is ready below. Copy it into the extension popup on your AI Studio tab. It expires in five minutes.';
  }));
  get('browser-pair-copy').addEventListener('click',()=>setupAction(async()=>{
    try{await navigator.clipboard.writeText(get('browser-pair-value').value);}catch(_){get('browser-pair-value').focus();get('browser-pair-value').select();throw Error('Select and copy the pairing code above.');}
    return 'Pairing code copied. Paste it into the extension popup on your AI Studio tab.';
  }));
  function alertUser(value) {
    if(['running','stopping'].includes(value.status)){attentionToast?.remove();attentionToast=null;lastNotice='';return;}
    const key=`${value.id}/${value.status}/${value.attention||''}/${value.cursor}`;
    if(!['paused','failed','complete'].includes(value.status)||key===lastNotice)return;
    lastNotice=key;
    const title=value.status==='complete'?'Narration queue finished':'Narration queue needs attention';
    const detail=value.error?.reason||value.message||'';
    attentionToast?.remove();attentionToast=window.NarrationFeedback?.show(title+(detail?': '+detail:''),value.status==='complete'?'success':'error');
    try{if(typeof Notification!=='undefined'&&Notification.permission==='granted')new Notification(title,{body:detail,tag:'manga-narration',requireInteraction:true});}catch(_){get('browser-error').textContent='Desktop notification could not be displayed. Queue status and on-page alerts remain available.';}
    try{if(get('browser-chime').checked&&sound){const tone=sound.createOscillator(),gain=sound.createGain();tone.connect(gain);gain.connect(sound.destination);gain.gain.value=.08;tone.frequency.value=660;tone.start();tone.stop(sound.currentTime+.3);}}catch(_){get('browser-error').textContent='Sound could not play. On-page queue alerts remain available.';}
  }
  function render(value, notify=false){
    if(value&&job&&value.id===job.id&&typeof value.updated_at==='number'&&typeof job.updated_at==='number'&&value.updated_at<job.updated_at)return;
    receivedAt=Date.now();lastStatusAt=receivedAt;
    job=value;get('browser-job').hidden=!job;
    if(!job){controls();return;}
    const complete=job.items.filter(i=>i.status==='complete').length;
    get('browser-job-status').textContent=`${complete}/${job.items.length} batches saved · ${job.status}\n${job.error?.reason||job.message||''}`;
    get('browser-response-problem').textContent=job.response_problem?`Last reply rejected: ${job.response_problem.message} Accepted narration is unchanged. You can retry only this batch in a fresh chat or use the manual import controls.`:'';
    get('browser-job-progress').value=complete/Math.max(1,job.items.length);
    get('browser-job-items').replaceChildren();
    for(const item of job.items)get('browser-job-items').append(make('li',`${item.chapter} · Batch ${Number(item.batch_id.slice(6))} · ${item.status}`));
    get('browser-retry-label').hidden=!['paused','failed'].includes(job.status)||job.retry_safe||!['sending','awaiting_reply','correcting'].includes(job.phase);
    controls();clock();if(notify)alertUser(job);
  }
  const duration=seconds=>{const n=Math.max(0,Math.floor(seconds||0));return `${Math.floor(n/60)}m ${n%60}s`;};
  function clock(){
    if(!job)return;
    const running=['running','stopping'].includes(job.status),extra=running?(Date.now()-receivedAt)/1000:0;
    const item=job.items[job.cursor],activity=job.activity||{},fallback={ready:'Preparing batch',sending:'Submitting request',awaiting_reply:'Waiting for Gemini',reply_saved:'Reply saved',importing:'Validating narration',correcting:'Preparing correction'};
    const age=typeof job.activity_elapsed_seconds==='number'?job.activity_elapsed_seconds+extra:null;
    get('browser-activity').textContent=`${item?`${/^\d/.test(item.chapter)?'Chapter ':''}${item.chapter} · Batch ${Number(item.batch_id.slice(6))} · `:''}${running?(activity.label||fallback[job.phase]||'Working'):job.status==='complete'?'All selected batches saved':'Paused — waiting for your action'}`;
    get('browser-elapsed').textContent=`${job.timing_available===false?'Recorded active time unavailable for this older queue':`Recorded active time: ${duration((job.elapsed_seconds||0)+extra)}`}${age!==null&&running?` · This step: ${duration(age)}`:''}${job.correction?` · Correction ${job.correction}/2`:''}`;
    let warning='';
    if(running&&Date.now()-lastStatusAt>12000)warning='App status is not updating. Check the app connection. Do not repeat Start or Retry until you can read the saved queue status.';
    else if(running&&job.browser_connection?.paired&&!job.browser_connection.connected)warning='The browser extension heartbeat is missing. Check the paired tab and extension; do not resubmit an uncertain request.';
    else if(running&&age!==null){const step=activity.step;const limit=step==='generating'?180:step==='waiting_upload'?120:step==='opening_tab'||step==='submitting'?30:60;
      if(age>=limit)warning=step==='generating'?`Gemini has been generating for ${duration(age)}. This is longer than usual, but does not prove it is stuck. Check its tab; the queue keeps a separate generation timeout.`:`${activity.label||'This step'} has taken ${duration(age)}. Check the browser tab. Use Stop if needed; saved batches are retained.`;
    }
    get('browser-stall-warning').textContent=warning;get('browser-stall-warning').hidden=!warning;
  }
  setInterval(clock,1000);
  setInterval(()=>{if(busy&&!refreshing)refresh(true);},2500);
  async function refresh(notify=false){
    if(refreshing)return false;refreshing=true;
    if(timer)clearTimeout(timer);timer=null;
    let ok=false;
    try{const epoch=viewEpoch,value=await request('/api/browser-narration');if(epoch===viewEpoch){render(value,notify);ok=true;}}catch(error){get('browser-error').textContent=error.message;clock();}finally{refreshing=false;}
    if(job&&['running','stopping'].includes(job.status))timer=setTimeout(()=>refresh(true),2500);
    return ok;
  }
  async function action(fn,label='Queue request'){
    if(busy)return;busy=true;controls();get('browser-error').textContent='';
    const card=begin(label+'…');
    try{const value=await fn();render(value,true);finish(card,`${label}: ${value.status}.`,['paused','failed'].includes(value.status)?'error':'success');get('browser-retry').checked=false;await refresh();}
    catch(error){get('browser-error').textContent=error.message;get('browser-start-note').textContent=error.message;finish(card,error.message,'error');await refresh();}
    finally{busy=false;controls();}
  }
  get('browser-manga').addEventListener('change',chapters);
  get('browser-catalog-refresh').addEventListener('click',()=>setupAction(async()=>{await catalog();return 'Chapters refreshed. Your available selections were retained.';}));
  async function requireConnection(){await connectionStatus();if(!connected)throw Error('AI Studio is not connected to this app session. Get a pairing code above, connect the extension on your AI Studio tab, then click Start or Resume again. Your chapter selections are retained.');}
  get('browser-start').addEventListener('click',()=>action(async()=>{await requireConnection();return request('/api/browser-narration',{manga_id:get('browser-manga').value,chapters:[...selected]});},'Start narration queue'));
  get('browser-resume').addEventListener('click',()=>action(async()=>{await requireConnection();return request(`/api/browser-narration/${job.id}/resume`,{retry_unfinished:get('browser-retry').checked});},'Resume narration queue'));
  get('browser-retry-batch').addEventListener('click',()=>action(async()=>{await requireConnection();return request(`/api/browser-narration/${job.id}/retry-batch`,{});},'Retry batch in a fresh chat'));
  get('browser-stop').addEventListener('click',async()=>{
    if(stopBusy||!job)return;stopBusy=true;controls();const card=begin('Stopping narration queue…');
    try{const value=await request(`/api/browser-narration/${job.id}/stop`,{});render(value);finish(card,'Stop requested. Saved narration is retained; a request already sent to Google may still finish.');await refresh();}
    catch(error){get('browser-error').textContent=error.message;finish(card,error.message,'error');}
    finally{stopBusy=false;controls();}
  });
  get('browser-clear').addEventListener('click',async()=>{
    if(busy||setupBusy||stopBusy||!job)return;
    busy=true;controls();const card=begin('Clearing queue from this page…');
    try{const value=await request(`/api/browser-narration/${job.id}/clear`,{});viewEpoch++;render(null);lastNotice='';get('browser-error').textContent='';get('browser-retry').checked=false;finish(card,value.message);await refresh();}
    catch(error){get('browser-error').textContent=error.message;finish(card,error.message,'error');await refresh();}
    finally{busy=false;controls();}
  });
  get('browser-job-refresh').addEventListener('click',()=>setupAction(async()=>{if(!await refresh())throw Error(get('browser-error').textContent);return 'Queue status refreshed.';}));
  get('browser-alerts').addEventListener('click',async()=>{
    const card=begin('Enabling desktop alerts…');
    try{if(typeof Notification==='undefined')throw Error('Desktop alerts are unavailable in this browser. Keep the Narration page open to see queue status.');
      const permission=await Notification.requestPermission();get('browser-error').textContent=permission==='granted'?'Desktop alerts enabled. Keep this page open for alerts.':'Desktop alerts were not enabled. Queue messages remain visible here.';finish(card,get('browser-error').textContent,permission==='granted'?'success':'info');
    }catch(error){get('browser-error').textContent=error.message;finish(card,error.message,'error');}
  });
  get('browser-chime').addEventListener('change',async()=>{
    const enabled=get('browser-chime').checked,card=begin(enabled?'Enabling sound alerts…':'Disabling sound alerts…');
    try{if(enabled){const Audio=window.AudioContext||window.webkitAudioContext;if(!Audio)throw Error('Sound alerts are unavailable in this browser.');sound=sound||new Audio();await sound.resume();}finish(card,enabled?'Sound alerts enabled. Keep this page open.':'Sound alerts disabled.');}
    catch(error){get('browser-chime').checked=false;get('browser-error').textContent=error.message;finish(card,error.message,'error');}
  });
  let loaded=false;
  document.addEventListener('visibilitychange',()=>{if(!document.hidden&&get('browser-narration-panel').open)connectionStatus().catch(error=>{get('browser-setup-feedback').textContent=error.message;});});
  get('browser-narration-panel').addEventListener('toggle',async()=>{if(get('browser-narration-panel').open&&!loaded){try{await Promise.all([catalog(),connectionStatus()]);loaded=true;}catch(error){get('browser-error').textContent=error.message;}}});
  refresh();
})();
