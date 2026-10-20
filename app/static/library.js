const library = document.querySelector('#library');
const notice = document.querySelector('#notice');
let previous = '';
function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}
async function request(url, body) {
  const response = await fetch(url, body === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const result = await response.json();
  if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Request failed. Check the folder and try again.');
  return result;
}
async function action(url) {
  try {await request(url, {}); notice.textContent=''; await refresh();}
  catch(error) {notice.textContent=error.message;}
}
function render(data) {
  document.querySelector('#model-status').textContent=data.validation_message;
  const expanded = new Set([...library.querySelectorAll('details[open]')].map(n=>n.dataset.key));
  library.replaceChildren();
  if (!data.manga.length) {
    const empty=element('div',undefined,'empty');
    empty.append(element('h2','Your Library is empty'),element('p','Add a folder above to get started.'));
    library.append(empty);
  }
  for(const manga of data.manga) {
    const card=element('article',undefined,'manga');
    const head=element('div',undefined,'manga-head');
    const title=element('div');
    title.append(element('h2',manga.name),element('div',manga.folder,'meta'));
    head.append(title,element('span',manga.direction==='rtl'?'← Right to left':'Left to right →','badge'));
    const memory=element('a','Story memory','review-link');memory.href=`/memory/${manga.id}`;memory.style.cssText='display:inline-block;margin:14px 24px';card.append(head,memory);
    if(manga.error) card.append(element('p',manga.error,'chapter'));
    for(const chapter of manga.chapters) {
      const row=element('section',undefined,'chapter');
      const top=element('div',undefined,'chapter-top');
      const label=element('div');
      label.append(element('div',chapter.name,'chapter-title'),element('div',`${chapter.done} / ${chapter.total} valid pages · ${chapter.flagged||0} flagged · ${chapter.panels} panels · ${chapter.seconds.toFixed(1)}s saved page time`,'stats'));
      top.append(label);
      const button=element('button',chapter.status==='running'?'Stop':chapter.status==='complete'?'Complete':chapter.done?'Resume chapter':chapter.allowed?'Process chapter':'Detector unavailable','secondary');
      button.disabled=!data.detector_ready||!chapter.allowed||chapter.status==='complete'||(data.busy&&chapter.status!=='running');
      button.addEventListener('click',()=>action(chapter.status==='running'?'/api/stop':`/api/manga/${manga.id}/chapters/${encodeURIComponent(chapter.name)}/start`));
      top.append(button); row.append(top);
      if(chapter.review_ready) { const link=element('a','Review panels','review-link');link.href=`/review/${manga.id}/${encodeURIComponent(chapter.name)}`;row.append(link); }
      if(chapter.review_ready) { const link=element('a','Export & import narration','review-link');link.href=`/narration/${manga.id}/${encodeURIComponent(chapter.name)}`;link.style.marginLeft='16px';row.append(link); }
      if(!chapter.review_ready&&(chapter.done===chapter.total||chapter.status==='flagged')){const prepare=element('button','Prepare panels','secondary');prepare.onclick=()=>action(`/api/panels/${manga.id}/${encodeURIComponent(chapter.name)}/prepare`);row.append(prepare);}
      if(chapter.review_ready){for(const [route,title] of [['audio','Audio'],['video','Video']]){const link=element('a',title,'review-link');link.href=`/${route}/${manga.id}/${encodeURIComponent(chapter.name)}`;link.style.marginLeft='16px';row.append(link);}}
      const progress=element('progress');progress.max=chapter.total;progress.value=chapter.done;progress.setAttribute('aria-label',`${chapter.name} pages complete`);row.append(progress);
      row.append(element('div',`${chapter.status}${chapter.job.current_page?' · '+chapter.job.current_page:''}${chapter.job.message?' · '+chapter.job.message:''}`,'message'));
      if(chapter.job.keep_awake) row.append(element('div','Mac kept awake while this chapter runs','message'));
      if(chapter.job.model_load_seconds) row.append(element('div',`Model load: ${chapter.job.model_load_seconds.toFixed(1)}s · ${chapter.job.device} · Last run: ${(chapter.job.elapsed_seconds||0).toFixed(1)}s`,'message'));
      const details=element('details');details.dataset.key=manga.id+'/'+chapter.name;details.open=expanded.has(details.dataset.key);
      details.append(element('summary','Page timings & panel counts'));
      const fillPages=()=>{if(!details.open||details.querySelector('table'))return;
      const table=element('table'), heading=element('tr');
      for(const name of ['Page','Panels','Time / page','Status']) heading.append(element('th',name));
      const thead=element('thead');thead.append(heading);table.append(thead);
      const tbody=element('tbody');
      for(const page of chapter.pages){const tr=element('tr');for(const value of [page.page,page.panels??'—',page.seconds===null?'—':page.seconds.toFixed(2)+'s',page.done?'Saved':page.flagged?'Flagged':'Pending'])tr.append(element('td',String(value)));tbody.append(tr);}
      table.append(tbody);details.append(table);};details.ontoggle=fillPages;fillPages();row.append(details);card.append(row);
    }
    library.append(card);
  }
}
async function refresh(){try{const data=await request('/api/library');const serialized=JSON.stringify(data);if(serialized!==previous){render(data);previous=serialized;}return data;}catch(error){notice.textContent=error.message;}}
document.querySelector('#add-form').addEventListener('submit',async event=>{
  event.preventDefault();const button=event.target.querySelector('button');button.disabled=true;
  try{await request('/api/manga',{folder:document.querySelector('#folder').value,direction:document.querySelector('#direction').value});notice.textContent='Manga added. Process a chapter, prepare its panels, then create narration.';event.target.reset();await refresh();}catch(error){notice.textContent=error.message;}finally{button.disabled=false;}
});
(async function poll(){const data=await refresh();setTimeout(poll,data?.busy?1500:10000);})();
