const $=s=>document.querySelector(s),player=$('#preview'),params=new URLSearchParams(location.search);
let groups=[],state=null,busy=false,loading=0,polling=false,activeRequest=null,selected=1,draft=[],dirty=false,segments=[],mediaUrl='',pendingSeek=0,conflict=false,reviewWindow=null;
function node(tag,text,cls){const el=document.createElement(tag);if(text!==undefined)el.textContent=text;if(cls)el.className=cls;return el;}
function mid(){return $('#manga-select').value;}function chapter(){return $('#chapter-select').value;}function base(){return `/api/cinema/${encodeURIComponent(mid())}/${encodeURIComponent(chapter())}`;}
async function api(url,body){const response=await fetch(url,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const value=await response.json();if(!response.ok)throw new Error(typeof value.detail==='string'?value.detail:'Video request failed. Please retry.');return value;}
function notice(text){$('#notice').textContent=text;}function current(){return state?.beats[selected-1];}function pause(){reviewWindow=null;player.pause();}
function controls(){const blocked=busy||!!state?.busy;for(const id of ['manga-select','chapter-select','refresh-catalog'])$('#'+id).disabled=blocked||dirty;$('#chapter-select').disabled=blocked||dirty||!mid();$('#export').disabled=blocked||dirty||conflict||!state;$('#build-preview').disabled=blocked||dirty||conflict||!state;$('#save').disabled=blocked||!dirty||conflict;$('#reset').disabled=blocked||!state;$('#beat').disabled=blocked;for(const input of $('#timings').querySelectorAll('input'))input.disabled=blocked;$('#play').disabled=busy||!state?.ready||dirty||conflict;$('#discard-timing').disabled=busy||!dirty||!!state?.busy;for(const button of $('#timings').querySelectorAll('button'))button.disabled=blocked||conflict||(button.dataset.playback==='true'&&(!state?.ready||dirty));}
function rebuild(){if(!state)return;segments=[];let elapsed=0;for(const beat of state.beats){beat.start=elapsed;const switches=beat.number===selected?draft:beat.switches;const bounds=[0,...switches,beat.duration];for(let i=0;i<beat.panels.length;i++){const start=Math.floor((elapsed+bounds[i])*state.fps+.5),end=Math.floor((elapsed+bounds[i+1])*state.fps+.5),panel=beat.panels[i];segments.push({panel,beat:beat.number,start,end});}elapsed+=beat.duration;}}
function showPanel(){if(!state||!segments.length||player.hidden)return;const t=Number.isFinite(player.currentTime)?player.currentTime:0,tick=Math.floor(t*state.fps+1e-6);let index=segments.findIndex(s=>tick<s.end);if(index<0)index=segments.length-1;const segment=segments[index];if(!dirty&&!reviewWindow&&segment.beat!==selected){selected=segment.beat;$('#beat').value=String(selected);draft=[...current().switches];renderTimings();}$('#panel-label').textContent=`${segment.panel.id} · Beat ${segment.beat}`;$('#clock').textContent=`${t.toFixed(1)} / ${state.duration.toFixed(1)} s`;}
function renderPlayer(){if(!state){player.hidden=true;return;}const ready=state.ready&&!dirty&&!conflict;player.hidden=!ready;$('#preview-empty').hidden=ready;$('#preview-status').textContent=dirty?'Save your timing changes, then click Create video again.':state.busy?'Creating your video. Progress is shown below.':'Click Create video. When it is ready, you can watch it here and save a copy.';
 if(!ready){pause();if(mediaUrl){pendingSeek=player.currentTime||pendingSeek;player.removeAttribute('src');player.load();mediaUrl='';}return;}
 const url=`${base()}/mp4?token=${state.render_token}`;if(mediaUrl!==url){pause();mediaUrl=url;player.src=url;player.load();}}
function valid(){const b=current();if(!b)return false;const bounds=[0,...draft,b.duration];return bounds.every(Number.isFinite)&&bounds.slice(1).every((n,i)=>n-bounds[i]>=1/state.fps-1e-8);}
function changed(){pause();dirty=true;$('#saved').textContent='Unsaved timing';$('#download').hidden=true;renderPlayer();rebuild();updateDurations();renderChecks();showPanel();controls();}
function updateDurations(){const b=current(),bounds=[0,...draft,b.duration];for(const el of $('#timings').querySelectorAll('small'))if(el.dataset.hold!==undefined){const i=Number(el.dataset.hold);const frames=Math.floor((b.start+bounds[i+1])*state.fps+.5)-Math.floor((b.start+bounds[i])*state.fps+.5);el.textContent=Number.isFinite(frames)?`Screen time: ${(frames/state.fps).toFixed(2)} s · ${frames} frames`:'Enter a valid switch time';}}
function editSwitch(index,value){
 const b=current();if(!b||busy||state.busy||conflict)return;
 const next=[...draft];next[index]=value;
 const bounds=[0,...next,b.duration];
 if(!bounds.every(Number.isFinite)||!bounds.slice(1).every((n,i)=>n-bounds[i]>=1/state.fps-1e-8)){
   notice('That switch would leave less than one frame for a panel. Move a neighboring switch first.');return;
 }
 draft=next;renderTimings();changed();notice('');
}
async function reviewCut(index,play=true){
 if(!state?.ready||dirty||conflict||busy)return;
 if(player.readyState<1){notice('The video is still loading. Wait for the player, then try this switch again.');return;}
 const b=current(),tick=Math.floor((b.start+(index?draft[index-1]:0))*state.fps+.5),at=tick/state.fps;
 pause();
 const start=play?Math.max(Math.floor(b.start*state.fps+.5)/state.fps,at-1.25):at;
 const end=Math.min(Math.floor((b.start+b.duration)*state.fps+.5)/state.fps-1/state.fps,at+1.25);
 pendingSeek=start;player.currentTime=start;
 $('#cut-review-status').textContent=play?`Watching around ${b.panels[index].id} at ${at.toFixed(2)} chapter seconds. Playback will stop automatically.`:`At ${b.panels[index].id}, ${at.toFixed(2)} chapter seconds.`;
 if(play){reviewWindow={end:Math.max(start,end),number:b.number};try{await player.play();}catch(e){pause();notice('Could not play this switch. Use the video player’s Play button. '+e.message);}}
 showPanel();
}
function renderChecks(){
 const holder=$('#timing-checks');holder.replaceChildren();const warnings=[];
 for(const segment of segments){const duration=(segment.end-segment.start)/state.fps;
   if(duration<.7||duration>15)warnings.push({...segment,duration});
 }
 $('#checks-count').textContent=warnings.length?`${warnings.length} to review`:'No pacing suggestions';
 holder.append(node('p',dirty?'Includes your unsaved timing draft. Save timing and create the video to watch those changes.':'Checks use the actual video frame boundaries: under 0.7 seconds or over 15 seconds per panel.','muted'));
 for(const item of warnings.slice(0,50)){
   const button=node('button',`${item.panel.id} · Beat ${item.beat} · ${item.duration.toFixed(2)} s — ${item.duration<.7?'quick flash':'long hold'}`,'secondary pacing-check');button.type='button';button.disabled=dirty||busy||!!state?.busy||conflict;
   button.addEventListener('click',()=>{chooseBeat(item.beat);$('#timing-heading').scrollIntoView?.({behavior:'smooth',block:'start'});notice(`Review ${item.panel.id}. This is a pacing suggestion; no timing has changed.`);});holder.append(button);
 }
 if(warnings.length>50)holder.append(node('p',`Showing the first 50 of ${warnings.length} suggestions. All panels are available in the beat selector.`,'muted'));
}
function renderTimings(){
 const b=current();$('#narration').textContent=b.narration||'Silent visual beat';$('#timings').replaceChildren();
 for(let i=0;i<b.panels.length;i++){
   const p=b.panels[i],row=node('div',undefined,'timing-row'),image=document.createElement('img');image.src=p.frame_url;image.alt=p.id;image.loading='lazy';
   image.addEventListener('error',()=>{image.alt=`${p.id}: panel image unavailable. Refresh the chapter to check its source.`;});
   const details=node('div');details.append(node('strong',p.id));if(p.phrase)details.append(node('small','“'+p.phrase+'”'));
   const hold=node('small');hold.dataset.hold=String(i);details.append(hold);
   const review=node('div',undefined,'cut-review-actions');
   for(const [label,play] of [['Jump to panel',false],['Watch switch',true]]){
     const button=node('button',label,'secondary');button.type='button';button.dataset.playback='true';button.addEventListener('click',()=>reviewCut(i,play));review.append(button);
   }
   details.append(review);row.append(image,details);
   if(i){
     const editing=node('div',undefined,'switch-editor'),label=node('label','Switch at (beat seconds)'),input=document.createElement('input');
     input.type='number';input.step='any';input.min=String(1/state.fps);input.max=String(b.duration-1/state.fps);input.value=String(Number(draft[i-1].toFixed(6)));input.setAttribute('aria-label',`Switch to ${p.id} at beat seconds`);
     input.addEventListener('input',()=>{draft[i-1]=input.value.trim()===''?NaN:Number(input.value);changed();});label.append(input);editing.append(label);
     const nudges=node('div',undefined,'cut-nudges');
     for(const [label,step] of [['−1 frame',-1],['+1 frame',1]]){const button=node('button',label,'secondary');button.type='button';button.setAttribute('aria-label',`${label} for ${p.id}`);button.addEventListener('click',()=>editSwitch(i-1,draft[i-1]+step/state.fps));nudges.append(button);}
     const mark=node('button','Use video position','secondary');mark.type='button';mark.dataset.playback='true';mark.addEventListener('click',()=>{if(player.readyState<1){notice('Wait for the video to load before using its position.');return;}editSwitch(i-1,player.currentTime-b.start);});
     editing.append(nudges,mark);row.append(editing);
   }else row.append(node('span','Starts at 0.00 s','timing-first'));
   $('#timings').append(row);
 }
 updateDurations();controls();
}

function chooseBeat(number,seek=true){pause();selected=number;draft=[...current().switches];dirty=false;$('#saved').textContent='';$('#cut-review-status').textContent='';$('#beat').value=String(selected);rebuild();if(seek){pendingSeek=Math.floor(current().start*state.fps+.5)/state.fps;if(state.ready&&mediaUrl&&player.readyState>=1)player.currentTime=pendingSeek;}renderTimings();showPanel();}
function renderJob(){$('#export').textContent=state?.ready?'Create video again':'Create video';const job=state.job?.render_token===state.render_token&&(state.ready||state.job.status!=='complete')?state.job:null;$('#job').textContent=dirty?'Save your timing changes before creating the video.':job?.message||(state.ready?'Your video is ready to watch and save.':'Click Create video to make your video.');const progress=Math.max(0,Math.min(1,job?.progress||0));$('#progress').value=progress;$('#render-progress').hidden=!job||dirty;$('#render-percent').textContent=Math.floor(progress*100)+'%';$('#render-label').textContent=job?.status==='failed'?'Video creation stopped':job?.status==='running'?'Creating video':'Video progress';const ready=state.ready&&!dirty&&!conflict;$('#download').hidden=!ready;if(ready){$('#download').href=`${base()}/mp4?token=${state.render_token}&download=true`;$('#download').download=chapter()+'.mp4';}renderPlayer();controls();}
function render(initial=false){if(!initial)rebuild();$('#chapter-content').hidden=false;$('#manga-name').textContent=state.manga;$('#chapter-title').textContent=state.chapter;$('#stats').textContent=`${state.beats.length} beats · ${state.beats.reduce((n,b)=>n+b.panels.length,0)} selected panels · ${(state.duration/60).toFixed(1)} minutes · Phrase timing`;
 if(initial){$('#beat').replaceChildren();for(const b of state.beats){const option=node('option',`Beat ${b.number} · ${b.panels.length} panels · ${b.duration.toFixed(2)} s`);option.value=String(b.number);$('#beat').append(option);}selected=Math.min(selected,state.beats.length);draft=[...current().switches];rebuild();chooseBeat(selected);if(state.timing_reset)notice('Sources changed. Timing has reset to the current saved phrase timestamps.');}renderChecks();renderJob();}
async function refresh(initial=false){if((polling&&!initial)||!chapter())return;const request={};activeRequest=request;polling=true;const stamp=loading;try{const next=await api(base());if(stamp!==loading)return;const changedSource=state&&state.source_token!==next.source_token;if(dirty&&state&&(changedSource||state.render_token!==next.render_token)){conflict=true;pause();notice('Sources or timing changed in another tab. Your unsaved draft has not been saved; refresh the page before editing again.');controls();return;}if(changedSource){pause();dirty=false;initial=true;notice('Audio or panels changed. The current phrase timing has been loaded.');}if(state&&state.render_token!==next.render_token&&!dirty)initial=true;if(!dirty)conflict=false;state=next;render(initial);}catch(error){if(stamp!==loading)return;notice(error.message);conflict=true;pause();$('#download').hidden=true;renderPlayer();controls();}finally{if(activeRequest===request){activeRequest=null;polling=false;}}}
async function loadChapter(){loading++;pause();state=null;dirty=false;conflict=false;selected=1;pendingSeek=0;mediaUrl='';player.removeAttribute('src');player.load();$('#chapter-content').hidden=true;notice('');if(!chapter()){controls();return;}busy=true;controls();try{await refresh(true);const url=new URL(location.href);url.searchParams.set('manga',mid());url.searchParams.set('chapter',chapter());history.replaceState(null,'',url);}finally{busy=false;controls();}}
function chapterOptions(preferred=''){const group=groups.find(g=>g.id===mid()),select=$('#chapter-select');select.replaceChildren();const placeholder=node('option','Choose a chapter with audio');placeholder.value='';select.append(placeholder);for(const c of group?.chapters||[]){const option=node('option',c.name+(c.video_ready?'':' · '+(c.video_blocked_reason||'Finish Stage 6')));option.value=c.name;option.disabled=!c.video_ready;select.append(option);}select.value=group?.chapters.some(c=>c.name===preferred&&c.video_ready)?preferred:'';$('#catalog-note').textContent=group&&!group.chapters.some(c=>c.video_ready)?'Generate all current audio in Stage 6 first.':'';}
async function catalog(){if(busy)return;busy=true;controls();try{groups=await api('/api/cinema/catalog');const preferred=mid()||params.get('manga')||'',previous=chapter()||params.get('chapter')||'';$('#manga-select').replaceChildren();const placeholder=node('option','Choose a manga');placeholder.value='';$('#manga-select').append(placeholder);for(const g of groups){const option=node('option',g.name);option.value=g.id;$('#manga-select').append(option);}$('#manga-select').value=groups.some(g=>g.id===preferred)?preferred:'';chapterOptions(previous);}catch(error){notice(error.message);}finally{busy=false;controls();}await loadChapter();}
$('#manga-select').addEventListener('change',()=>{if(dirty){notice('Save timing before changing manga.');return;}chapterOptions();loadChapter();});$('#chapter-select').addEventListener('change',loadChapter);$('#refresh-catalog').addEventListener('click',()=>{if(dirty){notice('Save timing before refreshing.');return;}catalog();});
$('#beat').addEventListener('change',()=>{if(dirty){$('#beat').value=String(selected);notice('Save timing before selecting another beat.');return;}chooseBeat(Number($('#beat').value));notice('');});
$('#play').addEventListener('click',async()=>{if(!state?.ready||dirty||conflict)return;pause();pendingSeek=Math.floor(current().start*state.fps+.5)/state.fps;player.currentTime=pendingSeek;try{await player.play();}catch(error){notice(error.message);}});$('#pause').addEventListener('click',pause);player.addEventListener('seeked',showPanel);player.addEventListener('timeupdate',()=>{if(reviewWindow&&player.currentTime>=reviewWindow.end){const end=reviewWindow.end;pause();player.currentTime=end;$('#cut-review-status').textContent='Switch review finished. No timing has changed.';}showPanel();});player.addEventListener('seeking',()=>{if(reviewWindow&&(player.currentTime<current().start||player.currentTime>reviewWindow.end+.25))reviewWindow=null;});player.addEventListener('ended',()=>{reviewWindow=null;});player.addEventListener('pause',()=>{if(player.paused)reviewWindow=null;});player.addEventListener('loadedmetadata',()=>{player.currentTime=Math.min(pendingSeek,Math.max(0,(Number.isFinite(player.duration)?player.duration:state?.duration||0)-1/(state?.fps||24)));showPanel();});player.addEventListener('error',()=>{if(mediaUrl)notice('The video could not load. Refresh the chapter and click Create video again.');});
$('#discard-timing').addEventListener('click',()=>{pause();if(conflict){dirty=false;refresh(true);return;}draft=[...current().switches];dirty=false;rebuild();renderTimings();renderChecks();renderJob();$('#saved').textContent='Unsaved changes discarded';});
$('#reset').addEventListener('click',()=>{draft=[...current().phrase_switches];renderTimings();changed();});
$('#save').addEventListener('click',async()=>{if(busy||conflict||!valid()){notice('Switch times must increase, stay inside the beat and leave one frame per panel.');return;}busy=true;pause();controls();try{state=await api(base()+'/timing',{source_token:state.source_token,render_token:state.render_token,number:selected,switches:draft});dirty=false;draft=[...current().switches];rebuild();renderTimings();renderChecks();renderJob();$('#saved').textContent='Timing saved — create video to see the changes';notice('');}catch(error){notice(error.message);}finally{busy=false;controls();}});
async function beginRender(){if(busy||dirty||!state)return;busy=true;pause();controls();try{await api(base()+'/export',{render_token:state.render_token});await refresh();notice('');}catch(error){notice(error.message);}finally{busy=false;controls();}}
$('#export').addEventListener('click',beginRender);$('#build-preview').addEventListener('click',beginRender);
async function poll(){if(!busy&&chapter())await refresh(!state);setTimeout(poll,state?.busy?2000:5000);}
catalog();setTimeout(poll,2000);

$('#open-video-root').addEventListener('click',async()=>{const button=$('#open-video-root');button.disabled=true;try{await api('/api/cinema/open-root-folder',{});}catch(error){notice(error.message);}finally{button.disabled=false;}});
