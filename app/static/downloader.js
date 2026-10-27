const $ = selector => document.querySelector(selector);
let catalog = null, selected = new Set(), jobs = [], busy = false, polling = false;
let currentId = localStorage.getItem('downloader-job') || '';
let folderBusy = false;
let historySignature = '', resultsSignature = '';
function node(tag, text, className) {const n=document.createElement(tag); if(text!==undefined)n.textContent=text;if(className)n.className=className;return n;}
async function api(path, body) {
  const response = await fetch('/api/downloader'+path, body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const result = await response.json();
  if(!response.ok) throw new Error(typeof result.detail==='string'?result.detail:'Request failed. Please check your selection.');
  return result;
}
function notice(text){$('#notice').textContent=text;}
function running(){return jobs.some(j=>['running','stopping'].includes(j.status));}
function updateSelection(){
  $('#selection-count').textContent=`${selected.size} / ${catalog.chapters.length} selected`;
  $('#download-button').disabled=!selected.size||busy||running();
  for(const input of $('#chapter-list').querySelectorAll('input')) input.checked=selected.has(input.value);
}
function showCatalog(){
  $('#catalog-panel').hidden=false;$('#source-name').textContent=catalog.source;$('#manga-title').textContent=catalog.title;
  $('#range-start').value=1;$('#range-end').value=catalog.chapters.length;
  $('#range-start').max=$('#range-end').max=catalog.chapters.length;
  $('#chapter-list').replaceChildren();
  const fragment=document.createDocumentFragment();
  catalog.chapters.forEach((chapter,index)=>{
    const label=node('label',undefined,'chapter-row'),input=document.createElement('input');input.type='checkbox';input.value=chapter.id;
    input.addEventListener('change',()=>{input.checked?selected.add(chapter.id):selected.delete(chapter.id);updateSelection();});
    label.append(input,node('span',String(index+1).padStart(2,'0'),'position'),node('span',chapter.name));fragment.append(label);
  });$('#chapter-list').append(fragment);updateSelection();
}
$('#source-form').addEventListener('submit',async event=>{
  event.preventDefault();if(busy)return;busy=true;$('#load-button').disabled=true;$('#load-button').textContent='Loading…';notice('Loading manga details and chapters…');
  try{catalog=await api('/catalog',{url:$('#source-url').value.trim()});selected=new Set();showCatalog();notice(`${catalog.chapters.length} chapters found. Select the chapters you want to download.`);}
  catch(error){notice(error.message);}
  finally{busy=false;$('#load-button').disabled=false;$('#load-button').textContent='Load chapters →';if(catalog)updateSelection();}
});
$('#select-all').addEventListener('click',()=>{selected=new Set(catalog.chapters.map(c=>c.id));updateSelection();});
$('#clear-selection').addEventListener('click',()=>{selected.clear();updateSelection();});
$('#apply-range').addEventListener('click',()=>{
  const start=Number($('#range-start').value),end=Number($('#range-end').value);
  if(!Number.isInteger(start)||!Number.isInteger(end)||start<1||end<start||end>catalog.chapters.length){notice(`Choose list positions between 1 and ${catalog.chapters.length}, with the last position at or after the first.`);return;}
  selected=new Set(catalog.chapters.slice(start-1,end).map(c=>c.id));updateSelection();notice(`Selected list positions ${start}–${end}.`);
});
function choose(id,scroll=false){currentId=id;localStorage.setItem('downloader-job',id);resultsSignature='';render();if(scroll)$('#progress-panel').scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth',block:'start'});}
async function act(path,body={}){
  if(busy)return;busy=true;$('#download-button').disabled=$('#resume-button').disabled=$('#stop-button').disabled=true;
  try{const job=await api(path,body);currentId=job.id;localStorage.setItem('downloader-job',job.id);notice('');await refresh();}
  catch(error){notice(error.message);}
  finally{busy=false;render();}
}
$('#download-button').addEventListener('click',()=>act('/jobs',{catalog_id:catalog.id,chapter_ids:[...selected]}));
$('#resume-button').addEventListener('click',()=>act(`/jobs/${currentId}/resume`));
$('#stop-button').addEventListener('click',()=>act(`/jobs/${currentId}/stop`));
$('#open-folder-button').addEventListener('click',async()=>{
  if(!currentId||folderBusy)return;folderBusy=true;$('#open-folder-button').disabled=true;
  try{await api(`/jobs/${currentId}/open-folder`,{});$('#folder-message').hidden=true;}
  catch(error){$('#folder-message').textContent=error.message;$('#folder-message').hidden=false;}
  finally{folderBusy=false;$('#open-folder-button').disabled=false;}
});
function duration(seconds){const s=Math.floor(seconds);return `${Math.floor(s/60)}m ${String(s%60).padStart(2,'0')}s`;}
function render(){
  const job=jobs.find(j=>j.id===currentId);
  $('#history-panel').hidden=!jobs.length;
  const signature=JSON.stringify(jobs.map(j=>[j.id,j.status,j.verified_pages,j.total_pages]));
  if(signature!==historySignature){
    historySignature=signature;$('#history-list').replaceChildren();
    for(const j of jobs){const button=node('button',undefined,'history-row');button.type='button';button.append(node('strong',j.title),node('span',`${j.verified_pages}/${j.total_pages} pages · ${j.status}`));button.addEventListener('click',()=>choose(j.id,true));$('#history-list').append(button);}
  }
  if(catalog)updateSelection();
  $('#progress-panel').hidden=!job;if(!job)return;
  $('#progress-title').textContent=job.title;$('#job-status').textContent=job.status;
  const planning=job.discovered_chapters<job.chapters.length;
  const percent=job.total_pages?Math.floor(job.verified_pages/job.total_pages*100):0;
  $('#progress-percent').textContent=planning?'':`${job.status==='complete'?100:percent}%`;
  const progress=$('#download-progress');
  if(planning&&['running','stopping'].includes(job.status))progress.removeAttribute('value');
  else progress.value=job.status==='complete'?100:percent;
  $('#progress-label').textContent=planning?`Page lists loaded: ${job.discovered_chapters} / ${job.chapters.length} chapters`:`${job.verified_pages} / ${job.total_pages} pages verified${job.failed_pages?` · ${job.failed_pages} failed`:''}`;
  $('#chapter-progress').textContent=`${job.completed_chapters} / ${job.chapters.length} chapters complete`;
  $('#elapsed').textContent=duration(job.elapsed_seconds);$('#job-message').textContent=job.message+(job.resume_blocked?' These folders were renumbered; download resume is disabled to protect your edits.':'');$('#destination').textContent=job.chapters.length===1?job.chapters[0].folder:job.folder;$('#open-folder-button').disabled=folderBusy;
  const waiting=Math.max(0,Math.ceil((Number(job.source_wait?.until)||0)-Date.now()/1000));
  const cooldown=$('#source-cooldown');cooldown.hidden=!waiting;
  if(waiting)cooldown.textContent=`${job.source_wait.host} · ${duration(waiting)} remaining. ${job.status==='running'?'The download will continue automatically; you can stop it at any time.':'Resume this download after the wait.'} Your saved images are safe.`;
  $('#stop-button').hidden=!['running','stopping'].includes(job.status);$('#stop-button').disabled=busy||job.status==='stopping';
  $('#resume-button').hidden=job.resume_blocked||!['paused','failed'].includes(job.status);$('#resume-button').disabled=busy||running()||waiting>0;
  const chapterSignature=JSON.stringify([job.id,job.chapters]);
  if(chapterSignature!==resultsSignature){
    resultsSignature=chapterSignature;$('#chapter-results').replaceChildren();
    for(const chapter of job.chapters){
      const row=node('div',undefined,'result-row');row.append(node('span',chapter.name),node('span',`${chapter.verified_pages}/${chapter.total_pages} pages · ${chapter.status}`));
      if(chapter.error||chapter.errors.length){const details=node('details',undefined,'result-errors');details.append(node('summary',chapter.error?'Source error':`${chapter.errors.length} failed page(s)`));if(chapter.error)details.append(node('p',chapter.error));for(const e of chapter.errors)details.append(node('p',`Page ${e.page}: ${e.reason}`));row.append(details);}
      $('#chapter-results').append(row);
    }
  }
}
async function refresh(){if(polling)return;polling=true;try{jobs=await api('/jobs');const active=jobs.find(j=>['running','stopping'].includes(j.status));if(active&&active.id!==currentId)currentId=active.id;if(!jobs.some(j=>j.id===currentId))currentId=jobs[0]?.id||'';render();}catch(error){notice(`Connection interrupted. Saved pages are safe. ${error.message}`);}finally{polling=false;}}
async function poll(){await refresh();setTimeout(poll,running()?1000:4000);}poll();
