const parts=location.pathname.split('/').filter(Boolean),mid=decodeURIComponent(parts[1]),chapter=decodeURIComponent(parts[2]);
const base=`/api/video/${encodeURIComponent(mid)}/${encodeURIComponent(chapter)}`,$=s=>document.querySelector(s),player=$('#audio');
$('#back').href=`/audio/${encodeURIComponent(mid)}/${encodeURIComponent(chapter)}`;
let state=null,selected=Number(new URLSearchParams(location.search).get('beat'))||1,draft=[],dirty=false,continuous=false,silentTime=0,silentStarted=0,silentRunning=false,imageId='',timer=null;
async function request(url,body){const r=await fetch(url,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{});const d=await r.json();if(!r.ok)throw Error(d.errors?.join('\n')||d.detail||'Request failed');return d;}
function current(){return state.beats[selected-1];}
function time(){return current().silent?silentTime:player.currentTime;}
function markDirty(){dirty=true;$('#saved').textContent='Unsaved timing changes';updateExport();}
function stop(){continuous=false;player.pause();silentRunning=false;}
function showPanel(){
 if(!state)return;
 const b=current(),t=time();let index=Math.min(draft.filter(x=>x<=t).length,b.panels.length-1);
 const p=b.panels[index],img=$('#panel-image');
 if(imageId!==p.id){imageId=p.id;img.src=p.frame_url||p.url;img.alt=`Panel ${p.id}`;}
 const bounds=[0,...draft,b.duration],seconds=bounds[index+1]-bounds[index];
 const config=state.motion_style,kind=p.motion_kind||'zoom_in';
 if(config){
  const prefix=kind.startsWith('zoom')?'zoom':'slide';
  const amount=Math.min(config[prefix+'_max'],seconds*config[prefix+'_per_second']);
  const progress=Math.min(1,Math.max(0,(t-bounds[index])/Math.max(1/24,seconds-1/24)));
  let zoom=1+amount,offset=0;
  if(kind==='zoom_in')zoom=1+amount*progress;
  else if(kind==='zoom_out')zoom=1+amount*(1-progress);
  else offset=(kind==='slide_from_left'?1:-1)*(2*progress-1)*amount/2;
  img.style.transform=`translate3d(${offset*100}%,0,0) scale(${zoom})`;
 }else img.style.transform='none';
 // Hold the outgoing panel's final camera position during the short dissolve.
 const outgoing=$('#panel-outgoing');
 const previous=index?b.panels[index-1]:state.beats[selected-2]?.panels.at(-1);
 const elapsed=state.beats.slice(0,selected-1).reduce((sum,beat)=>sum+beat.duration,0);
 const start=Math.round((elapsed+bounds[index])*24),end=Math.round((elapsed+bounds[index+1])*24);
 let previousStart=start;
 if(index)previousStart=Math.round((elapsed+bounds[index-1])*24);
 else if(previous){const pb=state.beats[selected-2];previousStart=Math.round((elapsed-pb.duration+(pb.switches.at(-1)||0))*24);}
 const fadeFrames=Math.min(state.transition_style?.frames||0,end-start-1,Math.floor((start-previousStart)/2));
 const fadeTime=(t-bounds[index])*24;
 const fading=previous&&fadeFrames>0&&fadeTime>=0&&fadeTime<fadeFrames;
 outgoing.hidden=!fading;
 if(fading){
  const previousUrl=previous.frame_url||previous.url;
  if(outgoing.dataset.panel!==previousUrl){outgoing.dataset.panel=previousUrl;outgoing.src=previousUrl;}
  const pb=state.beats[selected-2];
  const duration=index?bounds[index]-bounds[index-1]:pb.duration-(pb.switches.at(-1)||0),pk=previous.motion_kind||'zoom_in';
  const prefix=pk.startsWith('zoom')?'zoom':'slide';
  const amount=Math.min(config[prefix+'_max'],duration*config[prefix+'_per_second']);
  const zoom=pk==='zoom_out'?1:1+amount;
  const offset=pk.startsWith('slide')?(pk==='slide_from_left'?1:-1)*amount/2:0;
  outgoing.style.transform=`translate3d(${offset*100}%,0,0) scale(${zoom})`;
  outgoing.style.opacity=1-(fadeTime+1)/(fadeFrames+1);
 }
 const titles={zoom_in:'Slow zoom in',zoom_out:'Slow zoom out',slide_from_left:'Slide from left',slide_from_right:'Slide from right'};
 const label=`${p.id} · Beat ${b.number} · ${titles[kind]||''}`;
 if($('#panel-label').textContent!==label)$('#panel-label').textContent=label;
 const clock=`${t.toFixed(1)} / ${b.duration.toFixed(1)} s`;
 if($('#clock').textContent!==clock)$('#clock').textContent=clock;
}
function edit(){stop();const b=current();draft=[...b.switches];dirty=false;silentTime=0;imageId='';$('#saved').textContent='';$('#narration').textContent=b.narration||'Silent beat';player.hidden=b.silent;$('#silent-seek').hidden=!b.silent;$('#silent-seek').max=b.duration;$('#silent-seek').value=0;if(!b.silent){player.src=`/api/audio/${encodeURIComponent(mid)}/${encodeURIComponent(chapter)}/beats/${b.number}/audio?v=${encodeURIComponent(b.revision)}`;player.load();}else{player.removeAttribute('src');player.load();}renderTimings();showPanel();updateExport();if(window.loadAlignment)window.loadAlignment(selected);}
function renderTimings(){const b=current(),holder=$('#timings');holder.replaceChildren();for(let i=0;i<b.panels.length;i++){const p=b.panels[i],row=document.createElement('div');row.className='timing';const img=document.createElement('img');img.src=p.url;img.alt=p.id;row.append(img);const text=document.createElement('div'),title=document.createElement('strong');title.textContent=p.id;text.append(title);const duration=document.createElement('small');duration.dataset.duration=i;text.append(duration);row.append(text);if(i){const label=document.createElement('label');label.textContent='Switch to this panel at (s)';const input=document.createElement('input');input.type='number';input.step='.01';input.min=(1/24).toFixed(3);input.max=(b.duration-1/24).toFixed(3);input.value=Number(draft[i-1].toFixed(3));input.setAttribute('aria-label',`Switch to ${p.id} at seconds`);input.oninput=()=>{draft[i-1]=Number(input.value);markDirty();durations();showPanel();};label.append(input);row.append(label);}else{const first=document.createElement('span');first.textContent='Starts at 0.00 s';row.append(first);}holder.append(row);}durations();}
function durations(){const bounds=[0,...draft,current().duration];document.querySelectorAll('[data-duration]').forEach(e=>{const i=Number(e.dataset.duration);e.textContent=`Screen time: ${(bounds[i+1]-bounds[i]).toFixed(2)} s`;});}
function valid(){const bounds=[0,...draft,current().duration];return bounds.every(Number.isFinite)&&bounds.slice(1).every((v,i)=>v-bounds[i]>=1/24-1e-8);}
function advance(){if(continuous&&selected<state.beats.length&&!dirty){selected++;$('#beat').value=selected;edit();continuous=true;start();}else stop();}
async function start(){if(!valid()){$('#notice').textContent='Switch times must increase and leave at least one frame per panel.';return;}$('#result').pause();const b=current();if(b.silent){if(silentTime>=b.duration)silentTime=0;silentStarted=performance.now()-silentTime*1000;silentRunning=true;}else{try{await player.play();}catch(e){$('#notice').textContent=e.message;}}}
function frame(){if(state){if(silentRunning){silentTime=Math.min(current().duration,(performance.now()-silentStarted)/1000);$('#silent-seek').value=silentTime;if(silentTime>=current().duration){silentRunning=false;advance();}}showPanel();}requestAnimationFrame(frame);}
function updateExport(){if(!state)return;$('#render').disabled=dirty||state.job.running;$('#save').disabled=!dirty;const ready=state.ready&&!dirty;$('#download').hidden=!ready;$('#result').hidden=!ready;const url=`${base}/mp4?token=${state.render_token}`;$('#download').href=url+'&download=true';if(ready&&$('#result').getAttribute('src')!==url){$('#result').src=url;}}
function job(){const j=state.job;$('#export-pacing').textContent=state.pacing?.speed?`Andrew · ${state.pacing.speed}× narration · approximately ${Math.round(state.pacing.sentence_pause*1000)} ms after periods`:'';$('#job').textContent=dirty?'Save timing changes before rendering.':j.message;$('#progress').value=j.progress||0;updateExport();}
async function refresh(initial=false){try{const next=await request(base);if(state&&next.source_token!==state.source_token){stop();$('#notice').textContent='Audio or panel sources changed. Timing has been reloaded.';initial=true;}state=next;$('#stats').textContent=`${state.beats.length} beats · ${state.beats.reduce((s,b)=>s+b.panels.length,0)} selected panels · ${(state.duration/60).toFixed(1)} minutes · ${state.timing_mode==='phrase-aligned'?'Phrase aligned':state.timing_mode||'Saved timing'}${state.pacing?.speed?' · '+state.pacing.speed+'× · '+Math.round(state.pacing.sentence_pause*1000)+' ms period pause':''}`;if(initial){selected=Math.max(1,Math.min(selected,state.beats.length));$('#beat').replaceChildren();for(const b of state.beats){const o=document.createElement('option');o.value=b.number;o.textContent=`Beat ${b.number} · ${b.panels.length} panels · ${b.duration.toFixed(2)} s`;$('#beat').append(o);}$('#beat').value=selected;edit();if(state.timing_reset)$('#notice').textContent='Sources changed: timing reset to equal durations. Save any new adjustments.';}job();}catch(e){$('#notice').textContent=e.message;$('#render').disabled=true;}}
$('#beat').onchange=()=>{if(dirty){$('#notice').textContent='Save this beat’s timing before switching beats.';$('#beat').value=selected;return;}selected=Number($('#beat').value);edit();$('#notice').textContent='';};
$('#play').onclick=()=>{continuous=!dirty;start();};$('#stop').onclick=stop;player.onended=advance;player.onplay=()=>{$('#result').pause();};$('#result').onplay=stop;player.ontimeupdate=showPanel;$('#silent-seek').oninput=()=>{silentRunning=false;silentTime=Number($('#silent-seek').value);showPanel();};
$('#equal').onclick=()=>{draft=current().panels.slice(1).map((_,i)=>current().duration*(i+1)/current().panels.length);renderTimings();markDirty();showPanel();};
$('#save').onclick=async()=>{try{if(!valid())throw Error('Switch times must increase and leave at least one frame per panel.');state=await request(base+'/timing',{source_token:state.source_token,number:selected,switches:draft});dirty=false;$('#saved').textContent='Timing saved';$('#notice').textContent='';job();}catch(e){$('#notice').textContent=e.message;}};
$('#render').onclick=async()=>{try{stop();await request(base+'/export',{render_token:state.render_token});await refresh();clearInterval(timer);timer=setInterval(()=>refresh(),2000);}catch(e){$('#notice').textContent=e.message;}};
refresh(true).then(()=>{timer=setInterval(()=>{if(state?.job.running)refresh();},2000);});requestAnimationFrame(frame);

document.querySelector('#trial-link').onclick=()=>{if(dirty){document.querySelector('#notice').textContent='Save the numeric timing edit first.';return false;}selected=6;document.querySelector('#beat').value=6;edit();};
