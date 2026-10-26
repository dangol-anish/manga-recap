(() => {
  'use strict';
  const host = document.querySelector('[data-chapter-progress]');
  if (!host) return;
  const make = (tag, text, cls) => {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = text;
    if (cls) element.className = cls;
    return element;
  };
  const summary = make('summary', 'Chapter progress · see what to do next');
  const body = make('div', undefined, 'workflow-body');
  const pickers = make('div', undefined, 'workflow-pickers');
  const manga = make('select'), chapter = make('select');
  manga.id = 'workflow-manga'; chapter.id = 'workflow-chapter';
  for (const [label, input] of [['Manga', manga], ['Chapter', chapter]]) {
    const wrapper = make('div'), caption = make('label', label);
    caption.htmlFor = input.id; wrapper.append(caption, input); pickers.append(wrapper);
  }
  const refresh = make('button', 'Refresh progress', 'secondary');
  refresh.type = 'button'; pickers.append(refresh);
  const message = make('p', 'Choose a chapter to check its saved work.', 'workflow-message');
  message.setAttribute('role', 'status'); message.setAttribute('aria-live', 'polite');
  const results = make('div');
  body.append(pickers, message, results); host.append(summary, body);
  let groups = [], loaded = false, serial = 0;
  const params = new URLSearchParams(location.search);
  const placeholder = (select, text) => {
    const option = make('option', text); option.value = ''; select.replaceChildren(option);
  };
  async function get(url) {
    const response = await fetch(url);
    const value = await response.json();
    if (!response.ok) throw Error(typeof value.detail === 'string' ? value.detail : 'Could not check chapter progress. Refresh to try again.');
    return value;
  }
  function controls(busy) {
    manga.disabled = refresh.disabled = busy;
    chapter.disabled = busy || !manga.value;
  }
  function chapters(preferred = '') {
    placeholder(chapter, 'Choose a chapter');
    for (const item of groups.find(g => g.id === manga.value)?.chapters || []) {
      const option = make('option', item.name); option.value = item.id; chapter.append(option);
      if (item.id === preferred || item.name === preferred) chapter.value = item.id;
    }
    chapter.disabled = !manga.value;
  }
  function show(value) {
    results.replaceChildren();
    const next = make('div', undefined, 'workflow-next');
    const explanation = make('div');
    explanation.append(make('strong', value.complete ? 'Video ready' : `Next: Stage ${value.next.stage} · ${value.next.name}`),
      make('p', value.next.detail));
    const link = make('a', value.complete ? 'Watch or save video →' : `Open Stage ${value.next.stage} →`, 'workflow-link');
    link.href = value.next.url; next.append(explanation, link);
    const list = make('ol', undefined, 'workflow-steps');
    for (const step of value.steps) {
      const row = make('li'); row.dataset.state = step.state;
      const name = make('a', `${step.stage}. ${step.name}`); name.href = step.url;
      row.append(name, make('span', ({ready: 'Ready', next: 'Needs action', waiting: 'Waiting', optional: 'Optional'})[step.state], 'workflow-state'), make('p', step.detail));
      list.append(row);
    }
    results.append(next, list); message.textContent = value.note;
  }
  async function check() {
    const stamp = ++serial; results.replaceChildren();
    if (!chapter.value) { message.textContent = 'Choose a chapter to check its saved work.'; return; }
    controls(true); message.textContent = 'Checking saved images, narration, audio and video…';
    try {
      const value = await get(`/api/workflow/${encodeURIComponent(manga.value)}/${encodeURIComponent(chapter.value)}`);
      if (stamp === serial) show(value);
    } catch (error) { if (stamp === serial) message.textContent = error.message; }
    finally { if (stamp === serial) controls(false); }
  }
  async function load() {
    results.replaceChildren(); controls(true); message.textContent = 'Loading chapters…';
    try {
      const oldManga = manga.value || params.get('manga'), oldChapter = chapter.value || params.get('chapter');
      groups = await get('/api/workflow/catalog');
      placeholder(manga, 'Choose a manga');
      for (const group of groups) {
        const option = make('option', group.name); option.value = group.id; manga.append(option);
      }
      manga.value = groups.some(g => g.id === oldManga) ? oldManga : groups.length === 1 ? groups[0].id : '';
      chapters(oldChapter); loaded = true;
      message.textContent = groups.length ? 'Choose a chapter to check its saved work.' : 'No chapter images yet. Start with Stage 1: Downloader.';
    } catch (error) { message.textContent = error.message; }
    finally { controls(false); }
    if (chapter.value) await check();
  }
  placeholder(manga, 'Choose a manga'); placeholder(chapter, 'Choose a chapter'); chapter.disabled = true;
  host.addEventListener('toggle', () => { if (host.open && !loaded) load(); });
  manga.addEventListener('change', () => { chapters(); results.replaceChildren(); message.textContent = 'Choose a chapter to check its saved work.'; });
  chapter.addEventListener('change', check); refresh.addEventListener('click', load);
})();
