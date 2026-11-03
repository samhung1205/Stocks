/* Admin-only maintenance page: shared market-data writes go through the existing
   /api/admin endpoints, which the server also restricts to administrators. */
(async () => {
  const el = id => document.getElementById(id);
  const user = await window.accountReady;
  if (user.role !== 'admin') { el('maintenance-denied').hidden = false; document.querySelector('.desk-asof').remove(); return; }

  async function loadFreshness() {
    try {
      const s = await api('/api/admin/data-freshness');
      renderFreshness(s);
      setText(el('dashboard-asof'), s.latest_market_summary_date ? `${s.latest_market_summary_date} 收盤` : '尚無市場資料');
      return s;
    } catch (error) { renderFreshnessError(error); return null; }
  }

  async function loadStatus() {
    try {
      const s = await api('/api/admin/status');
      setText(el('db-status'), `股票 ${fmt.int(s.stocks)} 檔｜行情 ${fmt.int(s.quote_days)} 天（至 ${s.latest_date || '—'}）｜月營收 ${fmt.int(s.revenue_months)} 個月｜深度資料 ${fmt.int(s.deep_stocks)} 檔`);
    } catch (error) { setText(el('db-status'), `資料庫概況無法讀取：${error.message}`); }
  }

  const report = text => setText(el('repair-message'), text);
  for (const [id, endpoint] of [['btn-stale-update', '/api/admin/update-daily'], ['btn-repair-light', '/api/admin/repair-light']]) {
    el(id).addEventListener('click', async event => {
      await runRepairAction(event.currentTarget, endpoint, report);
      loadStatus(); loadFreshness();
    });
  }
  el('btn-benchmark').addEventListener('click', async event => {
    const button = event.currentTarget;
    button.disabled = true;
    try {
      const job = await api('/api/admin/backfill-benchmark', {method:'POST', body:{}});
      report(`研究基準工作 ${job.id.slice(0,8)} 已建立，範圍 ${job.config.start} 至 ${job.config.end}；進度見下方「Strategy Lab 5 年歷史回補」。`);
      loadStatus(); loadFreshness();
    } catch (error) { toastError(`補齊研究基準未完成：${error.message}`); }
    finally { button.disabled = false; }
  });

  function bindAdmin(id, path, body, done) {
    el(id).addEventListener('click', async event => {
      const button = event.currentTarget;
      button.disabled = true;
      try {
        const result = await api(path, {method:'POST', body: body || {}});
        toast(result.note || done(result), 4000);
        loadStatus(); loadFreshness();
      } catch (error) { toastError(`${button.textContent}未完成：${error.message}`); }
      finally { button.disabled = false; }
    });
  }
  bindAdmin('btn-sync', '/api/admin/sync-stocks', null,
    r => typeof r.synced === 'number' ? `股票清單已同步（${fmt.int(r.synced)} 檔）` : '股票清單已同步');
  bindAdmin('btn-backfill', '/api/admin/backfill', {days: 180}, () => '歷史回填已在背景執行');
  bindAdmin('btn-revenue', '/api/admin/fetch-revenue', {months: 13}, r => `近 ${r.months ?? 13} 個月營收已在背景抓取`);

  // Poll faster only while an update runs or data is behind; slow down once verified.
  async function poll() {
    const s = document.hidden ? null : await loadFreshness();
    const delay = !s || s.current_status === 'running' ? 20000 : s.freshness === 'fresh' ? 300000 : 60000;
    setTimeout(poll, delay);
  }
  loadStatus(); poll();
})();
