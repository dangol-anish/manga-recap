(() => {
  const $ = id => document.getElementById(id), panel = $('memory-panel');
  let mid='', data=null, serial=0, busy=false, page=0;
  const node=(tag,text)=>{const e=document.createElement(tag);e.textContent=text;return e;};
  const status=text=>{$('memory-status').textContent=text;};
  async function request(body) {
    const r=await fetch(`/api/story/${encodeURIComponent(mid)}/memory`, body ? {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)} : {});
    let d;try{d=await r.json();}catch{throw Error('Could not read the story memory response. Reload memory; no automatic retry was sent.');}
    if(!r.ok)throw Error(typeof d.detail==='string'?d.detail:'Could not save story memory. Reload and try again; narration and media are unchanged.');
    return d;
  }
  function render() {
    $('memory-items').replaceChildren();if(!data)return;
    const filter=$('memory-filter').value;
    const rows=data.items.filter(r=>filter==='all'||filter==='pinned'&&r.pinned||filter==='verified'&&r.verified||filter==='claims'&&r.kind==='fact'&&!r.verified||filter==='open'&&r.kind==='thread'&&!r.resolved||filter==='resolved'&&r.kind==='thread'&&r.resolved);
    page=Math.min(page,Math.max(0,Math.ceil(rows.length/50)-1));
    $('memory-previous').disabled=busy||page===0;$('memory-next').disabled=busy||(page+1)*50>=rows.length;
    $('memory-page-count').textContent=rows.length?`${page*50+1}–${Math.min((page+1)*50,rows.length)} of ${rows.length}`:'0 items';
    for(const r of rows.slice(page*50,(page+1)*50)){
      const card=node('article','');card.className='memory-card';
      card.append(node('p',r.kind==='thread'?(r.resolved?'Resolved thread':'Unresolved thread'):(r.verified?'Verified by me':'AI claim · '+(r.ai_status==='confirmed'?'AI marked confident':'uncertain'))),node('h3',r.text),node('p',`${r.chapter} · ${r.batch_id}${r.panel_ids.length?' · '+r.panel_ids.join(', '):' · AI continuity question'}`));
      const fields={};for(const [key,label] of [['pinned','Pin in future prompts'],...(r.kind==='fact'?[['verified','Verified by me']]:[['resolved','Resolved']])]){
        const wrap=node('label',label),input=document.createElement('input');input.type='checkbox';input.checked=r[key];input.disabled=busy||!r.current;wrap.className='check-label';wrap.prepend(input);card.append(wrap);fields[key]=input;
      }
      if(!r.current)card.append(node('p','Source narration is outdated. Refresh its batch before reviewing this item.'));
      const save=node('button','Save review');save.type='button';save.className='secondary';save.disabled=busy||!r.current;
      save.onclick=async()=>{
        const version=serial;busy=true;for(const input of panel.querySelectorAll('button,input'))input.disabled=true;
        status('Saving this review…');
        try {const next=await request({revision:data.revision,id:r.id,pinned:fields.pinned.checked,verified:fields.verified?.checked||false,resolved:fields.resolved?.checked||false});
          if(version!==serial)return;data=next;status('Review saved. Copy a fresh batch prompt to include it. Existing narration and media are unchanged.');globalThis.dispatchEvent(new Event('story-memory-saved'));
        }catch(e){if(version===serial)status('Story memory: '+e.message+' Reload before retrying if the connection was interrupted.');}
        finally{busy=false;$('memory-reload').disabled=false;if(version===serial)render();else if(panel.open)load();}
      };
      card.append(save);$('memory-items').append(card);
    }
    if(!rows.length)$('memory-items').append(node('p','No matching facts or threads. Import narration to save AI memory first.'));
  }
  async function load(){if(!mid||busy)return;const version=++serial;status('Reading saved facts and threads…');try{const next=await request();if(version!==serial)return;data=next;render();status(`${data.items.length} saved facts and threads. All remain on disk; prompts include every active pin plus a bounded selection of other memory. Sources from future batches are excluded.`);}catch(e){if(version===serial)status('Story memory: '+e.message);}}
  $('memory-reload').onclick=load;$('memory-filter').onchange=()=>{page=0;render();};
  $('memory-previous').onclick=()=>{page--;render();};$('memory-next').onclick=()=>{page++;render();};
  panel.addEventListener('toggle',()=>{if(panel.open&&!data)load();});
  if(globalThis.location?.hash==='#memory-panel')panel.open=true;
  globalThis.StoryMemory={change(value){if(value===mid)return;mid=value;data=null;page=0;++serial;$('memory-items').replaceChildren();status('Choose a manga, then open this section.');if(panel.open)load();},imported(){if(panel.open&&!busy)load();}};
})();
