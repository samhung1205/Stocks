/* Stage A+ maintenance controls; no strategy builder or backtest UI. */
(async () => {
  const user = await window.accountReady;
  if (user.role !== 'admin') return;
  const el = id => document.getElementById(id);
  const root = '/api/admin/backfill-for-backtest';
  let busy = false;
  const iso = d => `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
  const end = new Date(); end.setDate(end.getDate() - 1);
  const start = new Date(end); start.setFullYear(start.getFullYear() - 5);
  el('history-start').value = iso(start); el('history-end').value = iso(end);
  const labels = {queued:'已排入執行', pending:'待執行', running:'執行中', paused:'已暫停', interrupted:'已中斷，可續跑',
    failed:'失敗，可重試', completed:'工作完成', completed_with_gaps:'工作完成，部分來源無資料'};
  async function refresh(preferred) {
    const selected = preferred || el('history-jobs').value;
    const state = await api(root);
    el('history-jobs').replaceChildren(...state.jobs.map(job => {
      const option = document.createElement('option');
      option.value = job.id; option.textContent = `${job.id.slice(0,8)} · ${labels[job.status] || job.status}`;
      return option;
    }));
    if (state.jobs.some(j => j.id === selected)) el('history-jobs').value = selected;
    const id = el('history-jobs').value;
    if (!id) { el('history-progress').textContent = '尚無回補工作'; return; }
    const job = await api(`${root}/${id}`);
    const progress = job.progress.map(p => `${p.status}: ${p.units} 單位 / ${p.rows_written || 0} 筆`).join('\n');
    const diagnostics = (job.diagnostics || []).map(e => `${e.task_key} [${e.status}]: ${e.error}`).join('\n');
    el('history-progress').textContent = `工作 ${id}\n${labels[job.status] || job.status} · ${job.config.start} ～ ${job.config.end}\n門檻 NT$${job.config.min_avg_amount.toLocaleString()}；至少 ${job.config.min_observations} 個觀察日\n${progress}\n${diagnostics}\n${job.empty_units.length ? '無資料單位（不代表覆蓋完整）：\n' + job.empty_units.map(e => e.task_key).join('\n') : ''}`;
  }
  async function act(fn) {
    if (busy) return;
    busy = true;
    try { await fn(); } catch (err) { toastError(err.message); }
    finally { busy = false; }
  }
  el('history-create').onclick = () => act(async () => {
    const job = await api(root, {method:'POST', body:{start:el('history-start').value, end:el('history-end').value,
      min_avg_amount:Number(el('history-threshold').value)}});
    await refresh(job.id);
  });
  el('history-resume').onclick = () => act(async () => {
    const id = el('history-jobs').value; if (!id) return;
    await api(`${root}/${id}/resume`, {method:'POST', body:{max_units:25}});
    toast('已排入續跑；服務需保持開啟。'); await refresh(id);
  });
  el('history-pause').onclick = () => act(async () => {
    const id = el('history-jobs').value; if (!id) return;
    await api(`${root}/${id}/pause`, {method:'POST', body:{}}); await refresh(id);
  });
  el('history-refresh').onclick = () => act(() => refresh());
  el('history-jobs').onchange = () => act(() => refresh());
  api(root).then(s => {el('history-threshold').value = s.default_min_avg_amount;}).catch(() => {});
  act(() => refresh());
  setInterval(() => { if (!document.hidden && el('history-progress').closest('details').open) act(() => refresh()); }, 5000);
})();
