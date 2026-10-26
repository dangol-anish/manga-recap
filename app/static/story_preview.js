(() => {
  'use strict';
  const get = id => document.getElementById(id);
  const make = (tag, text, cls) => {
    const el = document.createElement(tag);
    if (text !== undefined) el.textContent = text;
    if (cls) el.className = cls;
    return el;
  };
  let manga = '', chapter = '', source = null, selected = 0, generation = 0, busy = false;
  function controls() {
    get('preview-open').disabled = busy || !get('preview-batch').value;
    get('preview-batch').disabled = busy || get('preview-batch').options.length < 2;
    get('preview-previous').disabled = !source || selected === 0;
    get('preview-next').disabled = !source || selected === source.beats.length - 1;
  }
  function clear() {
    generation++; source = null; busy = false;
    get('preview-content').hidden = true;
    get('preview-story').replaceChildren();
  }
  function reset() {
    clear(); manga = chapter = '';
    get('preview-message').textContent = 'Choose a prepared chapter to check its imported narration.';
    get('preview-batch').replaceChildren(); controls();
  }
  function update(state, mid, name) {
    const old = manga === mid && chapter === name ? get('preview-batch').value : '';
    clear(); manga = mid; chapter = name;
    const select = get('preview-batch'), empty = make('option', 'Choose an imported batch');
    empty.value = ''; select.replaceChildren(empty);
    for (const batch of state.batches.filter(b => b.imported)) {
      const option = make('option', `Batch ${Number(batch.id.slice(6))} · ${batch.beat_count} beats`);
      option.value = batch.id; select.append(option);
    }
    select.value = state.batches.some(b => b.imported && b.id === old) ? old : state.batches.find(b => b.imported)?.id || '';
    get('preview-message').textContent = select.value ? 'Choose Show story & panels to review this saved batch. Unimported or outdated batches are excluded.' : 'Import a current narration batch to check it here.';
    controls();
  }
  function render() {
    const beat = source.beats[selected], container = get('preview-story');
    container.replaceChildren(); get('preview-beat').value = String(selected);
    get('preview-summary').textContent = `${selected + 1} of ${source.beats.length} beats · ${source.selected_panels} selected panels · ${source.skipped_panels} skipped panels in this batch`;
    container.append(make('h3', `Beat ${beat.number}`), make('p', beat.silent ? 'Silent visual beat — no narration.' : beat.narration, 'preview-narration'));
    if (beat.unsure) {
      const warning = make('div', undefined, 'preview-uncertainty');
      warning.append(make('strong', 'AI uncertainty — check against the images'), make('p', beat.unsure));
      container.append(warning);
    }
    const grid = make('div', undefined, 'preview-panels');
    for (const [index, panel] of beat.panels.entries()) {
      const figure = make('figure'), image = make('img');
      image.alt = `${panel.id}, ${panel.page}, panel ${index + 1} in this beat`;
      image.loading = 'lazy'; image.decoding = 'async';
      image.src = `/api/story/${encodeURIComponent(manga)}/${encodeURIComponent(chapter)}/${source.batch_id}/preview/${encodeURIComponent(panel.id)}?token=${source.token}`;
      const imageBox = make('div', undefined, 'preview-image'); imageBox.append(image);
      const caption = make('figcaption');
      caption.append(make('strong', `${index + 1}. ${panel.id}`), make('span', panel.page, 'preview-page'),
        make('p', beat.silent ? 'Shown without narration.' : panel.narration, 'preview-span'));
      const unavailable = make('p', 'Panel image could not load. Refresh this preview; if it still fails, check Stage 4 prepared panels.', 'preview-image-error');
      unavailable.hidden = true; image.addEventListener('error', () => { unavailable.hidden = false; });
      figure.append(imageBox, unavailable, caption); grid.append(figure);
    }
    container.append(grid); get('preview-content').hidden = false; controls();
  }
  async function load() {
    if (busy || !get('preview-batch').value) return;
    const bid = get('preview-batch').value;
    clear(); const stamp = generation; busy = true; controls();
    get('preview-message').textContent = 'Checking the saved script and current panels…';
    try {
      const response = await fetch(`/api/story/${encodeURIComponent(manga)}/${encodeURIComponent(chapter)}/${encodeURIComponent(bid)}/preview`);
      const value = await response.json();
      if (!response.ok) throw Error(typeof value.detail === 'string' ? value.detail : 'Could not load this story preview. Refresh the chapter and try again.');
      if (stamp !== generation) return;
      if (!value.beats?.length) throw Error('This imported batch has no story beats to preview.');
      source = value; selected = 0;
      get('preview-beat').replaceChildren();
      for (const [index, beat] of source.beats.entries()) {
        const option = make('option', `Beat ${beat.number} · ${beat.panels.length} panels${beat.unsure ? ' · Check uncertainty' : ''}`);
        option.value = String(index); get('preview-beat').append(option);
      }
      get('preview-message').textContent = 'Follow the panels from left to right below; their text shows what will be narrated while each panel is on screen.';
      render();
    } catch (error) {
      if (stamp === generation) { source = null; get('preview-content').hidden = true; get('preview-message').textContent = error.message; }
    } finally { if (stamp === generation) { busy = false; controls(); } }
  }
  get('preview-open').addEventListener('click', load);
  get('preview-batch').addEventListener('change', () => { clear(); get('preview-message').textContent = 'Choose Show story & panels to load this batch.'; controls(); });
  get('preview-beat').addEventListener('change', () => { if (source) { selected = Number(get('preview-beat').value); render(); } });
  get('preview-previous').addEventListener('click', () => { if (source && selected > 0) { selected--; render(); } });
  get('preview-next').addEventListener('click', () => { if (source && selected < source.beats.length - 1) { selected++; render(); } });
  globalThis.StoryPreview = { update, reset };
})();
