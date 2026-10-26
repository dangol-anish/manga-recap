(() => {
  'use strict';
  const get = id => document.getElementById(id);
  const make = (tag, text, cls) => {
    const el = document.createElement(tag);
    if (text !== undefined) el.textContent = text;
    if (cls) el.className = cls;
    return el;
  };
  let manga = '', revision = '', profileBusy = false, profileSerial = 0, rows = [];
  let job = null, checkBusy = false, checkSerial = 0, sectionStamp = '', joinStamp = '', pairStamp = '', playing = false, pendingJump = null;
  const reports = new Map(), tail = get('join-tail'), head = get('join-head'), checker = get('quality-player');
  const running = () => !!job && ['running', 'stopping'].includes(job.status);
  async function api(url, body) {
    const response = await fetch(url, body === undefined ? {} : {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
    const value = await response.json();
    if (!response.ok) throw Error(typeof value.detail === 'string' ? value.detail : 'Audio check failed. Refresh the saved selection and try again.');
    return value;
  }
  function controls() {
    const locked = !manga || !revision || profileBusy || running();
    get('pronunciation-add').disabled = locked || rows.length >= 50;
    get('pronunciation-save').disabled = locked;
    get('pronunciation-reload').disabled = !manga || profileBusy || running();
    for (const row of rows) row.word.disabled = row.hint.disabled = row.remove.disabled = locked;
    get('quality-check').disabled = checkBusy || running() || !get('quality-section').value;
    get('quality-listen').disabled = !get('quality-join').value || playing;
    get('quality-stop').disabled = !playing;
  }
  function addRow(value = {word: '', say_as: ''}) {
    const line = make('div', undefined, 'pronunciation-row');
    const word = make('input'), hint = make('input'), remove = make('button', 'Remove', 'secondary');
    word.type = hint.type = 'text'; word.value = value.word; hint.value = value.say_as;
    word.maxLength = 80; hint.maxLength = 100;
    word.placeholder = 'Word from your script'; hint.placeholder = 'How to say it';
    word.setAttribute('aria-label', 'Word in the script'); hint.setAttribute('aria-label', 'Pronunciation hint');
    remove.type = 'button'; line.append(word, hint, remove); get('pronunciation-rows').append(line);
    const row = {line, word, hint, remove}; rows.push(row);
    remove.addEventListener('click', () => { rows = rows.filter(r => r !== row); line.remove(); controls(); });
    controls();
  }
  async function loadProfile() {
    const stamp = ++profileSerial, selected = manga;
    revision = ''; profileBusy = true; rows = []; get('pronunciation-rows').replaceChildren(); controls();
    if (!selected) { profileBusy = false; get('pronunciation-message').textContent = 'Choose a manga above. Existing recordings are unchanged.'; controls(); return; }
    get('pronunciation-message').textContent = 'Loading saved pronunciation hints…';
    try {
      const value = await api(`/api/grouped-audio/pronunciation/${encodeURIComponent(selected)}`);
      if (stamp !== profileSerial) return;
      revision = value.revision;
      for (const item of value.items) addRow(item);
      get('pronunciation-message').textContent = `${value.items.length} saved hints. Changes apply only to new Gemini selections; resumed jobs keep their original hints.`;
    } catch (error) { if (stamp === profileSerial) get('pronunciation-message').textContent = error.message; }
    finally { if (stamp === profileSerial) { profileBusy = false; controls(); } }
  }
  get('pronunciation-add').addEventListener('click', () => { if (!get('pronunciation-add').disabled) addRow(); });
  get('pronunciation-reload').addEventListener('click', loadProfile);
  get('pronunciation-save').addEventListener('click', async () => {
    if (get('pronunciation-save').disabled) return;
    const stamp = profileSerial, selected = manga;
    profileBusy = true; controls();
    try {
      const value = await api(`/api/grouped-audio/pronunciation/${encodeURIComponent(selected)}`, {revision, items: rows.map(r => ({word: r.word.value, say_as: r.hint.value}))});
      if (stamp !== profileSerial) return;
      revision = value.revision;
      get('pronunciation-message').textContent = `Saved ${value.items.length} hints for new Gemini audio. Existing script, recordings and video are unchanged.`;
    } catch (error) { if (stamp === profileSerial) get('pronunciation-message').textContent = error.message; }
    finally { if (stamp === profileSerial) { profileBusy = false; controls(); } }
  });
  function section() { return job?.sections.find(s => String(s.number) === get('quality-section').value); }
  function reportKey(s) { return `${job.id}:${s.number}:${s.audio_token}`; }
  function showReport() {
    const s = section(), root = get('quality-report'); root.replaceChildren();
    if (!s) { checker.pause(); checker.removeAttribute('src'); return; }
    const src = `/api/grouped-audio/jobs/${job.id}/audio?section=${s.number}&token=${encodeURIComponent(s.audio_token)}`;
    if (checker.getAttribute('src') !== src) { checker.pause(); pendingJump = null; checker.src = src; }
    const saved = reports.get(reportKey(s)) || (s.quality?.audio_token === s.audio_token ? s.quality : null);
    if (!saved) { get('quality-message').textContent = 'This saved section has not been checked yet. Check it locally without requesting new speech.'; return; }
    get('quality-message').textContent = saved.checked ? saved.warnings.length ? `${saved.warnings.length} things to listen for` : 'No signal warnings found. Listening is still needed.' : 'Automatic signal check unavailable; listen manually.';
    if (saved.checked) root.append(make('p', `${saved.words_per_minute} words/min at saved playback speed · longest quiet stretch ${saved.longest_quiet_seconds}s`, 'muted'));
    const list = make('ul');
    for (const warning of saved.warnings) {
      const row = make('li', warning.message);
      if (Number.isFinite(warning.start)) {
        const button = make('button', `Listen near ${Math.floor(warning.start / 60)}:${String(Math.floor(warning.start % 60)).padStart(2, '0')}`, 'secondary quality-jump');
        button.type = 'button'; button.addEventListener('click', async () => {
          pendingJump = Math.max(0, warning.start - 2);
          try { if (checker.readyState >= 1) checker.currentTime = pendingJump; await checker.play(); }
          catch (error) { get('quality-message').textContent = 'Use the section player to listen. ' + error.message; }
        }); row.append(button);
      }
      list.append(row);
    }
    root.append(list, make('p', saved.note, 'muted'));
  }
  get('quality-section').addEventListener('change', () => { checkSerial++; checkBusy = false; showReport(); controls(); });
  get('quality-check').addEventListener('click', async () => {
    const s = section(); if (!s || checkBusy || running()) return;
    const key = reportKey(s), stamp = ++checkSerial;
    checkBusy = true; controls(); get('quality-message').textContent = 'Checking the saved WAV locally. No speech request is being sent…';
    try {
      const report = await api(`/api/grouped-audio/jobs/${job.id}/quality/${s.number}?token=${encodeURIComponent(s.audio_token)}`);
      if (stamp !== checkSerial) return;
      reports.set(key, report); showReport();
    } catch (error) { if (stamp === checkSerial) get('quality-message').textContent = error.message; }
    finally { if (stamp === checkSerial) { checkBusy = false; controls(); } }
  });
  function stop() { playing = false; tail.pause(); head.pause(); controls(); }
  function pair() {
    const number = Number(get('quality-join').value);
    return [job?.sections.find(s => s.number === number), job?.sections.find(s => s.number === number + 1)];
  }
  function setJoin() {
    const [a, b] = pair();
    const stamp = JSON.stringify([job?.id, a?.number, a?.audio_token, b?.audio_token]);
    if (stamp === pairStamp) return;
    pairStamp = stamp; stop();
    for (const [player, s] of [[tail, a], [head, b]]) {
      const src = s ? `/api/grouped-audio/jobs/${job.id}/audio?section=${s.number}&token=${encodeURIComponent(s.audio_token)}` : '';
      if ((player.getAttribute('src') || '') !== src) { if (src) player.src = src; else player.removeAttribute('src'); }
    }
    get('join-ending').textContent = a?.ending || ''; get('join-opening').textContent = b?.opening || '';
  }
  get('quality-join').addEventListener('change', setJoin);
  checker.addEventListener('loadedmetadata', () => { if (pendingJump !== null) checker.currentTime = Math.min(pendingJump, Math.max(0, checker.duration - .05)); });
  checker.addEventListener('error', () => { get('quality-message').textContent = 'The saved section could not load. Refresh the selection and check its recording; no speech request was made.'; });
  document.addEventListener('play', event => {
    if (event.target.tagName !== 'AUDIO') return;
    for (const player of document.querySelectorAll('audio')) if (player !== event.target) player.pause();
    if (playing && event.target !== head && event.target !== tail) stop();
  }, true);
  tail.addEventListener('loadedmetadata', () => { if (playing) tail.currentTime = Math.max(0, tail.duration - 8); });
  tail.addEventListener('ended', async () => {
    if (!playing) return;
    try { head.currentTime = 0; await head.play(); } catch (error) { stop(); get('join-message').textContent = 'The next section could not start. Use its player below. ' + error.message; }
  });
  head.addEventListener('timeupdate', () => { if (playing && head.currentTime >= 8) stop(); });
  head.addEventListener('ended', stop);
  for (const player of [tail, head]) player.addEventListener('error', () => { stop(); get('join-message').textContent = 'This recording could not load. Refresh the saved selection and try again; no new speech was requested.'; });
  get('quality-stop').addEventListener('click', stop);
  get('quality-listen').addEventListener('click', async () => {
    const [a, b] = pair(); if (!a || !b || playing) return;
    for (const player of document.querySelectorAll('audio')) player.pause();
    playing = true; controls();
    get('join-message').textContent = 'Listening to the last 8 seconds, then the first 8 seconds. Browser loading can add a brief pause.';
    try {
      if (tail.readyState >= 1) tail.currentTime = Math.max(0, tail.duration - 8);
      await tail.play();
    } catch (error) { stop(); get('join-message').textContent = 'Playback could not start. Use the native players below. ' + error.message; }
  });
  globalThis.AudioQuality = {
    mangaChanged(value) { if (manga !== value) { manga = value; loadProfile(); } },
    update(value) {
      const previousJob = job?.id; job = value;
      get('audio-quality-review').hidden = !job?.sections.some(s => s.recorded);
      if (!job) { stop(); controls(); return; }
      if (previousJob !== job.id) { stop(); checkSerial++; checkBusy = false; reports.clear(); sectionStamp = joinStamp = ''; }
      get('quality-selection').textContent = `Saved selection: ${job.manga} · ${job.pronunciation_count || 0} pronunciation hints snapshotted for Gemini`;
      const recorded = job.sections.filter(s => s.recorded), stamp = JSON.stringify([job.id, recorded.map(s => [s.number, s.audio_token])]);
      if (stamp !== sectionStamp) {
        const previous = get('quality-section').value;
        get('quality-section').replaceChildren();
        for (const s of recorded) { const option = make('option', `Section ${s.number} · ${s.chapters.join(' → ')}`); option.value = String(s.number); get('quality-section').append(option); }
        get('quality-section').value = recorded.some(s => String(s.number) === previous) ? previous : recorded.length ? String(recorded[0].number) : '';
        sectionStamp = stamp;
      }
      if (!checkBusy) showReport();
      const joins = job.sections.filter((s, i) => s.timed && job.sections[i+1]?.timed && job.sections[i+1].number === s.number + 1);
      get('audio-join-review').hidden = !joins.length;
      const joinsKey = JSON.stringify([job.id, joins.map(s => [s.number, s.audio_token, job.sections.find(n => n.number === s.number + 1).audio_token])]);
      if (joinsKey !== joinStamp) {
        const previous = get('quality-join').value;
        get('quality-join').replaceChildren();
        for (const s of joins) { const option = make('option', `Section ${s.number} → ${s.number+1}`); option.value = String(s.number); get('quality-join').append(option); }
        get('quality-join').value = joins.some(s => String(s.number) === previous) ? previous : joins.length ? String(joins[0].number) : '';
        joinStamp = joinsKey; setJoin();
      }
      controls();
    }
  };
})();
