/* Shared market facts only; no private research content or browser persistence. */
const DATASET_LABELS = {quotes:'每日行情', institutional:'三大法人（個股）', valuations:'本益比／殖利率', market_summary:'大盤摘要'};
const DATASET_STATES = {current:'已核實', publication_lag:'官方資料落後一交易日', missing:'待補齊', unverified:'尚未核實'};

function retryDescription(retry) {
  if (retry?.state === 'running') return '正在自動更新，完成後會重新核對各資料集。';
  if (retry?.state === 'scheduled' && retry.next_check_at) {
    const time = new Date(retry.next_check_at).toLocaleTimeString('zh-TW', {hour:'2-digit', minute:'2-digit', hour12:false, timeZone:'Asia/Taipei'});
    return `下次自動檢查 ${time}（台北時間），通常不需手動操作。`;
  }
  return ({exhausted:'本交易日自動重試次數或時間已達上限；需要時請管理員手動修復。',
    outside_window:'本日自動檢查時段已結束；需要時請管理員手動重試。',
    disabled:'自動更新未啟用；資料日期與完整性見下表。',
    no_baseline:'尚未建立市場資料，請管理員完成首次資料準備。',
    idle:'每日盤後自動檢查，通常不需手動操作。'})[retry?.state] || '目前無法確認下一次更新，請查看明細或聯絡管理員。';
}

function renderFreshness(s) {
  const el = id => document.getElementById(id);
  const running = s.current_status === 'running';
  const state = running ? 'running' : s.freshness;
  const labels = {fresh:'資料已核實', partial:'部分資料待補齊', stale:'資料待更新', failed:'更新未完成', unknown:'資料狀態待確認', running:'自動更新中'};
  const indicator = el('freshness-indicator');
  indicator.dataset.state = state;
  indicator.textContent = labels[state] || labels.unknown;
  indicator.setAttribute('aria-label', `${indicator.textContent}，查看更新明細`);
  el('freshness-summary').textContent = indicator.textContent;
  const missing = Object.entries(s.datasets || {}).filter(([,row]) => ['missing','unverified'].includes(row.state));
  const reason = missing.length ? missing.map(([key]) => DATASET_LABELS[key] || key).join('、') :
    s.pending_missing_dates?.length ? `待核對 ${s.pending_missing_dates.length} 個交易日` : '各資料集完整性仍待核對';
  const banner = el('stale-banner');
  banner.dataset.state = state;
  banner.hidden = s.freshness === 'fresh';
  el('stale-title').textContent = labels[state] || labels.unknown;
  el('stale-text').textContent = `${reason}。${s.retry_state?.state === 'exhausted' ? '本日重試已達上限。' : running ? '正在處理。' : ''}`;
  el('stale-detail').textContent = retryDescription(s.retry_state);
  const rows = Object.entries(DATASET_LABELS).map(([key,label]) => {
    const row = s.datasets?.[key] || {state:'unverified'};
    const gaps = row.missing_dates || [];
    const note = gaps.length ? `缺漏／筆數不足：${gaps.slice(0,5).join('、')}${gaps.length > 5 ? ` 等 ${gaps.length} 日` : ''}` :
      row.state === 'publication_lag' ? '保留官方實際發布日期，不以今日日期代填。' :
      row.calendar_verified === false ? '交易日曆證據不足，尚不能判定完整。' : '已按市場別及檢查範圍核對。';
    return `<tr><th scope="row" class="l">${label}</th><td class="l">${escapeHtml(row.latest_date || '尚無資料')}</td><td class="l"><span class="dataset-state" data-state="${escapeHtml(row.state)}">${DATASET_STATES[row.state] || '尚未核實'}</span><br>${escapeHtml(note)}</td></tr>`;
  });
  const benchmark = s.research_benchmark || {};
  const benchmarkPending = !benchmark.latest_date || (benchmark.expected_through && benchmark.latest_date < benchmark.expected_through);
  rows.push(`<tr><th scope="row" class="l">研究基準 TAIEX_TR</th><td class="l">${escapeHtml(benchmark.latest_date || '尚無資料')}</td><td class="l"><span class="dataset-state" data-state="${benchmarkPending ? 'missing' : 'current'}">${benchmarkPending ? '待獨立回補' : '獨立維護'}</span><br>不隨每日行情更新${benchmark.expected_through ? `；本次可補至 ${escapeHtml(benchmark.expected_through)}` : ''}。</td></tr>`);
  el('dataset-status-rows').innerHTML = rows.join('');
  el('btn-stale-update').disabled = running;
  el('btn-stale-update').textContent = running ? '更新中…' : '更新今日資料';
  el('btn-repair-light').disabled = running;
  el('btn-benchmark').disabled = running || !benchmarkPending;
}

function renderFreshnessError(error) {
  const el = id => document.getElementById(id);
  el('freshness-indicator').dataset.state = 'unknown';
  el('freshness-indicator').textContent = '更新狀態無法確認';
  el('freshness-indicator').setAttribute('aria-label', '更新狀態無法確認，查看明細');
  el('freshness-summary').textContent = '無法確認';
  el('stale-banner').hidden = false;
  el('stale-banner').dataset.state = 'unknown';
  el('stale-title').textContent = '更新狀態無法確認';
  el('stale-text').textContent = '請查看資料日期，明細稍後會重新查詢。';
  el('stale-detail').textContent = error.message;
  el('dataset-status-rows').innerHTML = '<tr><td colspan="3" class="l">暫時無法讀取各資料集狀態，請稍後重試。</td></tr>';
}
