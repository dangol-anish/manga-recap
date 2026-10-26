(() => {
 const node=(tag,text,cls)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e;};
 let serial=0;
 function invalidate(card){card.dataset.reviewToken=String(++serial);const box=card.querySelector('.short-review');if(box)box.remove();}
 async function open(pid,sid,revision,card){
   invalidate(card);const token=card.dataset.reviewToken;
   const box=node('section',undefined,'short-review'),message=node('p','Reading saved script, source references and any matching audio…','muted');box.setAttribute('aria-label','Short story and pacing review');box.append(message);card.append(box);
   try{
     const response=await fetch(`/api/shorts/projects/${encodeURIComponent(pid)}/shorts/${encodeURIComponent(sid)}/review`);
     let data;try{data=await response.json();}catch{throw Error('Could not read the review response. Open this review again; no script or media was changed.');}
     if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:'Could not review this saved short. Refresh the project and try again.');
     if(card.isConnected===false||card.dataset.reviewToken!==token)return;
     if(data.revision!==revision)throw Error('This project changed in another tab. Refresh it before reviewing; your current script was not changed.');
     box.replaceChildren();box.append(node('h3','Review the story before creating it'));
     box.append(node('p',`${data.word_count} words · ${data.duration.toFixed(1)}s ${data.all_measured?'from saved audio':'estimated'} · ${data.measured_segments}/${data.segments.length} segments have matching audio`,'review-metrics'));
     if(data.estimated_range)box.append(node('p',`Typical reading estimate: ${data.estimated_range[0].toFixed(0)}–${data.estimated_range[1].toFixed(0)} seconds at this project’s ${data.speed}× speed. Pauses and delivery can change the result.`,'muted'));
     const ends=node('div',undefined,'short-review-ends');
     for(const [label,text] of [['Opening',data.opening],['Ending',data.ending]]){const column=node('div');column.append(node('h4',label),node('p',text));ends.append(column);}box.append(ends);
     box.append(node('p','Does the opening give a clear reason to stay, and does the ending deliver a supported payoff? These are your review questions, not an automatic viral score.','muted'));
     const label=node('label','Inspect a segment'),select=node('select');label.append(select);
     for(const row of data.segments){const option=node('option',`Segment ${row.number} · ${row.chapter} · ${row.panel_id} · ${row.duration.toFixed(1)}s${row.measured?'':' estimated'}`);option.value=String(row.number-1);select.append(option);}select.value='0';box.append(label);
     const detail=node('div',undefined,'short-review-detail');box.append(detail);
     function show(index){
       select.value=String(index);const row=data.segments[index];detail.replaceChildren();
       const figure=node('div',undefined,'short-review-panel'),img=node('img');img.src=row.panel_url;img.alt=row.chapter+' / '+row.panel_id;img.loading='lazy';figure.append(img);
       img.addEventListener('error',()=>{figure.append(node('p','This panel could not load. Refresh the project to check its source.','muted'));});
       const text=node('div');text.append(node('h4','Short script'),node('p',row.narration),node('h4','Original source beat'),node('p',row.source_narration),node('p','This original beat may cover several panels; compare the short against the image too.','muted'));
       if(row.source_unsure)text.append(node('p','Original uncertainty: '+row.source_unsure,'review-caution'));
       const compare=node('div',undefined,'short-review-compare');compare.append(figure,text);detail.append(compare);
       detail.append(node('p',`${row.start.toFixed(2)}–${row.end.toFixed(2)}s in the short · ${row.measured?'saved audio timing':'estimated timing'}${data.all_measured?'':' (earlier estimated segments may shift this position)'}`,'muted'));
       const player=card.querySelector('video');
       if(data.all_measured&&player&&!player.hidden){const watch=node('button','Jump to this panel in saved video','secondary');watch.type='button';const feedback=node('p','','muted');watch.addEventListener('click',()=>{if(card.dataset.reviewToken!==token||player.hidden)return;if(player.readyState<1){feedback.textContent='Wait for the saved video to load, then try again.';return;}try{player.pause();player.currentTime=Math.floor(row.start*30+.5)/30;player.scrollIntoView?.({behavior:'smooth',block:'center'});feedback.textContent='Video paused at this panel. Use its Play button to listen.';}catch{feedback.textContent='Could not seek in this saved video. Reload the project and try again.';}});detail.append(watch,feedback);}
       detail.append(node('h4','Caption chunks'));const captions=node('div',undefined,'short-review-captions');
       for(const cue of row.captions){const chip=node('span',cue.text);chip.title=`${cue.start.toFixed(2)}–${cue.end.toFixed(2)}s${row.measured?'':' estimated'}`;captions.append(chip);}detail.append(captions);
       detail.append(node('p','Uses the same chunk grouping as the video. This is text inspection, not a rendered caption-layout preview.','muted'));
     }
     select.addEventListener('change',()=>show(Number(select.value)));show(0);
     if(data.notices.length){const list=node('div',undefined,'short-review-notices');list.append(node('h4','Worth checking — no automatic changes'));
       for(const notice of data.notices){if(notice.segment){const button=node('button',`Segment ${notice.segment}: ${notice.text}`,'secondary');button.type='button';button.addEventListener('click',()=>show(notice.segment-1));list.append(button);}else list.append(node('p',notice.text));}box.append(list);
     }else box.append(node('p','No pacing suggestions from these simple checks. This does not verify the story.','muted'));
     box.append(node('p',data.guidance,'muted'));
   }catch(error){if(card.dataset.reviewToken===token){message.textContent='Shorts review: '+error.message;box.replaceChildren(message);}}
 }
 globalThis.ShortsReview={open,invalidate};
})();
