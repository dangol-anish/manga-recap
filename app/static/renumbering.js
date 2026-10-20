const $=selector=>document.querySelector(selector);
let chapters=[],selected=new Set(),busy=false;
function node(tag,text,className){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(className)n.className=className;return n;}
async function api(path,body){const r=await fetch('/api/renumbering'+path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const value=await r.json();if(!r.ok)throw new Error(typeof value.detail==='string'?value.detail:'Request failed. Refresh and try again.');return value;}
function message(text){$('#renumber-notice').textContent=text;}
function visible(){return chapters.filter(c=>!$('#manga-filter').value||c.manga===$('#manga-filter').value);}
function invalidate(){$('#rename-results').hidden=true;}
function controls(){
 $('#selected-count').textContent=`${selected.size} selected`;
 for(const id of ['refresh-folders','manga-filter','select-folders','clear-folders'])$('#'+id).disabled=busy;
 for(const input of $('#folder-list').querySelectorAll('input')){input.disabled=busy;input.checked=selected.has(input.value);}
 $('#apply-numbering').disabled=busy||!selected.size;
}
function render(){
 $('#folder-list').replaceChildren();$('#folder-empty').hidden=!!chapters.length;
 for(const c of visible()){
  const label=node('label',undefined,'chapter-row'),input=document.createElement('input');input.type='checkbox';input.value=c.id;
  input.addEventListener('change',()=>{input.checked?selected.add(c.id):selected.delete(c.id);invalidate();controls();});
  const name=node('span',undefined,'folder-label');name.append(node('span',c.name),node('small',c.folder));
  label.append(input,name,node('span',c.pending?'Interrupted · continue':`${c.images} images`,'folder-count'));$('#folder-list').append(label);
 }controls();
}
async function refresh(){if(busy)return;busy=true;invalidate();controls();message('Loading chapter folders…');try{
 chapters=await api('/chapters');selected=new Set([...selected].filter(id=>chapters.some(c=>c.id===id)));
 const filter=$('#manga-filter'),previous=filter.value;filter.replaceChildren();const all=node('option','All manga');all.value='';filter.append(all);
 for(const manga of [...new Set(chapters.map(c=>c.manga))]){const option=node('option',manga);option.value=manga;filter.append(option);}
 filter.value=[...filter.options].some(o=>o.value===previous)?previous:'';render();message('');
 }catch(error){message(error.message);}finally{busy=false;controls();}}
$('#refresh-folders').addEventListener('click',refresh);
$('#manga-filter').addEventListener('change',render);
$('#select-folders').addEventListener('click',()=>{for(const c of visible())selected.add(c.id);invalidate();controls();});
$('#clear-folders').addEventListener('click',()=>{selected.clear();invalidate();controls();});
function folderButton(planId,index){
 const button=node('button',undefined,'secondary folder-button');button.type='button';
 button.innerHTML='<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 7V5a1 1 0 0 1 1-1h5l2 3h9a1 1 0 0 1 1 1v11a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V7Z"/></svg>';
 button.append(document.createTextNode('Open folder'));button.title='Open this chapter in Finder or File Explorer';
 button.addEventListener('click',async()=>{if(button.disabled)return;button.disabled=true;
 try{await api(`/plans/${planId}/folders/${index}/open`,{});message('');}
 catch(error){message(error.message);}finally{button.disabled=false;}});return button;
}
$('#apply-numbering').addEventListener('click',async()=>{if(busy||!selected.size)return;busy=true;controls();$('#apply-numbering').textContent='Renumbering…';message('Renumbering selected folders. Please wait…');try{
 const result=await api('/renumber',{chapter_ids:[...selected]});$('#result-list').replaceChildren();$('#rename-results').hidden=false;
 result.results.forEach((chapter,index)=>{
  const row=node('div',undefined,'renumber-result'),text=node('div',undefined,'renumber-result-text');
  text.append(node('strong',chapter.folder.split('/').at(-1)),node('small',chapter.folder),node('span',chapter.status==='complete'?`${chapter.images} images · ${chapter.renamed} renamed`:'Needs attention'));
  row.append(text);if(chapter.status==='complete')row.append(folderButton(result.id,index));
  if(chapter.error)row.append(node('p',chapter.error,'result-detail'));$('#result-list').append(row);
 });
 message(result.status==='complete'?'Done. Each chapter is numbered from 0000 with no gaps.':'Some folders need attention. Resolve the reported issue, then select them and press Renumber again to continue. Image contents are preserved.');
 if(result.status==='complete')selected.clear();
 }catch(error){message(error.message);}finally{busy=false;$('#apply-numbering').textContent='Renumber selected chapters';controls();}});
refresh();
