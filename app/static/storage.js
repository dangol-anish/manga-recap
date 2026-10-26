(() => {
  'use strict';
  const get = id => document.getElementById(id);
  const make = (tag, text, cls) => {
    const el = document.createElement(tag);
    if (text !== undefined) el.textContent = text;
    if (cls) el.className = cls;
    return el;
  };
  let busy = false, job = null, restorePlan = null, cleanupPlan = null, lastFinished = '';
  const size = bytes => {
    if (bytes < 1024) return `${bytes} B`;
    const units = ['KB', 'MB', 'GB', 'TB']; let value = bytes / 1024, unit = 0;
    while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit++; }
    return `${value.toFixed(1)} ${units[unit]}`;
  };
  const running = () => job?.status === 'running';
  async function api(path, body) {
    const response = await fetch('/api/storage' + path, body === undefined ? {} : {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)
    });
    const value = await response.json();
    if (!response.ok) throw Error(typeof value.detail === 'string' ? value.detail : 'Storage request failed. Refresh and check the affected files before retrying.');
    return value;
  }
  function controls() {
    const locked = busy || running();
    for (const id of ['create-backup', 'preview-cleanup', 'refresh-storage', 'refresh-backups', 'restore-archive', 'replace-files']) get(id).disabled = locked;
    get('preview-restore').disabled = locked || !get('restore-archive').value;
    get('apply-restore').disabled = locked || !restorePlan || !get('confirm-restore').checked || (!restorePlan.replace && restorePlan.files.some(r => r.action === 'replace'));
    get('apply-cleanup').disabled = locked || !cleanupPlan?.files.length || !get('confirm-cleanup').checked;
    get('stop-storage').disabled = busy;
    get('recover-storage').disabled = locked;
    get('confirm-cleanup').disabled = get('confirm-restore').disabled = locked;
  }
  function clearPlans() {
    restorePlan = cleanupPlan = null;
    get('restore-plan').hidden = get('cleanup-plan').hidden = true;
    get('confirm-restore').checked = get('confirm-cleanup').checked = false;
  }
  function showJob() {
    get('storage-job').hidden = !job;
    if (job) {
      get('job-heading').textContent = ({backup:'Creating backup', restore:'Restoring backup', cleanup:'Cleaning rebuildable caches'})[job.kind] || 'Storage task';
      get('storage-job-status').textContent = job.status;
      get('storage-job-message').textContent = job.message;
      get('storage-progress').max = job.total || 1;
      get('storage-progress').value = job.completed || 0;
      get('storage-job-count').textContent = `${job.completed || 0} / ${job.total || 0} file steps${job.freed_bytes ? ' · ' + size(job.freed_bytes) + ' of cache files removed' : ''}${job.result ? ' · ' + job.result : ''}`;
      get('stop-storage').hidden = !running();
      get('recover-storage').hidden = !job.recovery_required;
    }
    controls();
  }
  async function sizes() {
    const value = await api('/usage');
    get('disk-space').textContent = `${size(value.free_bytes)} free on this drive · ${size(value.used_bytes)} in local work, caches and backups`;
    get('storage-usage').replaceChildren();
    for (const row of value.categories) {
      const card = make('div', undefined, 'storage-size');
      card.append(make('strong', size(row.bytes)), make('span', row.name), make('small', `${row.files} files`));
      get('storage-usage').append(card);
    }
    get('manga-usage').replaceChildren();
    for (const row of value.manga) { const p = make('p'); p.append(make('span', row.name), make('span', size(row.bytes))); get('manga-usage').append(p); }
  }
  async function listBackups() {
    const selected = get('restore-archive').value, rows = await api('/backups');
    const empty = make('option', rows.length ? 'Choose a backup' : 'No backups yet — create one above'); empty.value = '';
    get('restore-archive').replaceChildren(empty);
    for (const row of rows) { const option = make('option', `${row.name} · ${size(row.bytes)}`); option.value = row.name; get('restore-archive').append(option); }
    get('restore-archive').value = rows.some(r => r.name === selected) ? selected : '';
    controls();
  }
  function fileList(id, rows) {
    const root = get(id); root.replaceChildren();
    const add = items => { for (const row of items) root.append(make('p', `${row.action ? ({same:'Unchanged', replace:'Replace with safety copy', add:'Restore missing'})[row.action] + ' · ' : ''}${row.path} · ${size(row.size)}`)); };
    add(rows.slice(0, 100));
    if (rows.length > 100) { const button = make('button', `Show remaining ${rows.length - 100} files`, 'secondary'); button.type = 'button'; button.addEventListener('click', () => { button.remove(); add(rows.slice(100)); }); root.append(button); }
  }
  async function action(fn) {
    if (busy) return;
    busy = true; controls(); get('storage-notice').textContent = '';
    try { await fn(); } catch (error) { get('storage-notice').textContent = error.message; }
    finally { busy = false; controls(); }
  }
  get('open-backups').addEventListener('click', () => action(() => api('/open-backups', {})));
  get('refresh-storage').addEventListener('click', () => action(sizes));
  get('refresh-backups').addEventListener('click', () => action(async () => { clearPlans(); await listBackups(); }));
  get('create-backup').addEventListener('click', () => action(async () => { clearPlans(); job = await api('/backup', {}); showJob(); }));
  get('preview-restore').addEventListener('click', () => action(async () => {
    restorePlan = null; get('restore-plan').hidden = true; get('confirm-restore').checked = false;
    get('storage-notice').textContent = 'Checking backup and comparing existing files. No files are being restored yet…';
    restorePlan = await api('/restore/preview', {archive: get('restore-archive').value, replace: get('replace-files').checked});
    const counts = key => restorePlan.files.filter(r => r.action === key).length;
    get('restore-summary').textContent = `${counts('add')} missing files to restore · ${counts('same')} identical files kept · ${counts('replace')} differing files${restorePlan.replace ? ' to replace, with current versions saved separately.' : '. Restore is blocked if any files differ; nothing will be overwritten by default.'}`;
    fileList('restore-files', restorePlan.files); get('restore-plan').hidden = false; get('storage-notice').textContent = 'Restore check complete. Review the file list before applying.';
  }));
  for (const id of ['restore-archive', 'replace-files']) get(id).addEventListener('change', () => { restorePlan = null; get('restore-plan').hidden = true; get('confirm-restore').checked = false; controls(); });
  get('confirm-restore').addEventListener('change', controls);
  get('apply-restore').addEventListener('click', () => action(async () => {
    if (!restorePlan || !get('confirm-restore').checked) return;
    job = await api('/restore', {id: restorePlan.id, token: restorePlan.token, confirm: true}); clearPlans(); showJob();
  }));
  get('preview-cleanup').addEventListener('click', () => action(async () => {
    cleanupPlan = null; get('cleanup-plan').hidden = true; get('confirm-cleanup').checked = false;
    get('storage-notice').textContent = 'Checking rebuildable caches. Nothing is being deleted…';
    cleanupPlan = await api('/cleanup/preview', {});
    get('cleanup-summary').textContent = `${cleanupPlan.files.length} rebuildable cache files · ${size(cleanupPlan.files.reduce((n, r) => n + r.size, 0))} can be cleared. Everything else is kept.`;
    fileList('cleanup-files', cleanupPlan.files); get('cleanup-plan').hidden = false; get('storage-notice').textContent = 'Cleanup preview ready. No files have been deleted.';
  }));
  get('confirm-cleanup').addEventListener('change', controls);
  get('apply-cleanup').addEventListener('click', () => action(async () => {
    if (!cleanupPlan || !get('confirm-cleanup').checked) return;
    job = await api('/cleanup', {id: cleanupPlan.id, token: cleanupPlan.token, confirm: true}); clearPlans(); showJob();
  }));
  get('stop-storage').addEventListener('click', () => action(async () => { job = await api('/stop', {}); showJob(); }));
  get('recover-storage').addEventListener('click', () => action(async () => { const result = await api('/recover', {}); get('storage-notice').textContent = result.message; job = await api('/status'); showJob(); }));
  async function poll() {
    if (!busy) {
      try {
        job = await api('/status'); showJob();
        if (job && job.status !== 'running' && lastFinished !== job.id) {
          lastFinished = job.id; clearPlans(); await listBackups(); await sizes();
        }
      } catch (error) { get('storage-notice').textContent = error.message; }
    }
    setTimeout(poll, running() ? 1500 : 5000);
  }
  action(async () => { await sizes(); await listBackups(); job = await api('/status'); showJob(); lastFinished = job?.status !== 'running' ? job?.id : ''; });
  setTimeout(poll, 1500);
})();
