/* Shared market facts only; no private research content or browser persistence.
   Used by the dashboard (status + one recommended action) and maintenance.html (all tools). */
const DATASET_LABELS = {quotes:'每日行情', institutional:'三大法人（個股）', valuations:'本益比／殖利率', market_summary:'大盤摘要'};
const DATASET_STATES = {current:'已核實', publication_lag:'官方資料落後一交易日', missing:'待補齊', unverified:'尚未核實'};
const FRESHNESS_LABELS = {fresh:'資料已核實', partial:'部分資料待補齊', stale:'資料待更新', failed:'更新未完成', unknown:'資料狀態待確認', running:'自動更新中'};
const REPAIR_DAYS = 30;  // POST /api/admin/repair-light runs market_daily.backfill_history(30)

function retryDescription(retry) {
  if (retry?.state === 'running') return '正在自動更新，完成後會重新核對各資料集。';
  if (retry?.state === 'scheduled' && retry.next_check_at) {
    const time = new Date(retry.next_check_at).toLocaleTimeString('zh-TW', {hour:'2-digit', minute:'2-digit', hour12:false, timeZone:'Asia/Taipei'});
    return `下次自動檢查 ${time}（台北時間），通常不需手動操作。`;
  }
  return ({exhausted:'本交易日自動重試次數或時間已達上限；需要時請管理員手動修復。',
    outside_window:'本日自動檢查時段已結束；需要時請管理員手動重試。',
    disabled:'自動更新未啟用，需要管理員手動更新。',
    no_baseline:'尚未建立市場資料，請管理員完成首次資料準備。',
    idle:'每日盤後自動檢查，通常不需手動操作。'})[retry?.state] || '目前無法確認下一次更新，請查看明細或聯絡管理員。';
}

const shortDate = date => String(date || '').slice(5);

/* Trading sessions newer than the local data. Calendar evidence says 交易日; otherwise the
   server's weekday estimate is disclosed as an estimate, never presented as exact. */
function freshnessLag(s) {
  const dates = s?.pending_missing_dates || [];
  if (!dates.length) return null;
  const estimated = s.pending_missing_dates_source !== 'trading_calendar';
  return {count: dates.length, first: dates[0], last: dates[dates.length - 1], estimated,
    text: `${dates.length} 個${estimated ? '平日（依平日估計）' : '交易日'}`};
}

/* One recommended admin action for the current state; null when nothing should be pressed. */
function recommendedRepair(s) {
  if (!s || s.current_status === 'running' || s.freshness === 'fresh') return null;
  const target = s.expected_trading_day;
  const gaps = [...new Set([...(s.pending_missing_dates || []),
    ...Object.values(s.datasets || {}).flatMap(row => row.missing_dates || [])])].sort();
  const older = gaps.filter(date => target && date < target);
  if (older.length) {
    const windowStart = new Date(Date.now() - (REPAIR_DAYS - 1) * 864e5).toISOString().slice(0, 10);
    return {kind:'repair', endpoint:'/api/admin/repair-light',
      label: `修復近 ${REPAIR_DAYS} 日缺漏（${shortDate(older[0])} 起）`,
      note: older[0] < windowStart ? `${older[0]} 早於近 ${REPAIR_DAYS} 日範圍，修復後請到資料維護回填歷史。` : ''};
  }
  return {kind:'update', endpoint:'/api/admin/update-daily', label:'更新最新交易日', note:''};
}

/* Write only real changes so the polite live regions are not re-announced on every poll. */
function setText(node, text) { if (node && node.textContent !== text) node.textContent = text; }

function renderFreshness(s, options = {}) {
  const el = id => document.getElementById(id);
  const running = s.current_status === 'running';
  const state = running ? 'running' : s.freshness;
  const label = FRESHNESS_LABELS[state] || FRESHNESS_LABELS.unknown;
  const lag = freshnessLag(s);
  const indicator = el('freshness-indicator');
  if (indicator) {
    indicator.dataset.state = state;
    setText(indicator, label);
    indicator.setAttribute('aria-label', `${label}，查看更新明細`);
  }
  setText(el('freshness-summary'), label);
  const asofLag = el('asof-lag');
  if (asofLag) { asofLag.hidden = !lag; setText(asofLag, lag ? `之後 ${lag.text}未納入` : ''); }

  const missing = Object.entries(s.datasets || {}).filter(([,row]) => ['missing','unverified'].includes(row.state));
  const shown = s.latest_market_summary_date || s.latest_completed_quote_date;
  const reason = lag ? `畫面顯示 ${shown || '最後一次'} 收盤；${shortDate(lag.first)} 至 ${shortDate(lag.last)} 共 ${lag.text}尚未納入。` :
    missing.length ? `${missing.map(([key]) => DATASET_LABELS[key] || key).join('、')}待補齊。` : '各資料集完整性仍待核對。';
  const banner = el('stale-banner');
  if (banner) { banner.dataset.state = state; banner.hidden = s.freshness === 'fresh'; }
  setText(el('stale-title'), label);
  setText(el('stale-text'), reason);
  setText(el('stale-retry'), running ? '正在處理，完成後會自動重新核對。' : retryDescription(s.retry_state));
  setText(el('stale-detail'), retryDescription(s.retry_state));

  const rows = Object.entries(DATASET_LABELS).map(([key,name]) => {
    const row = s.datasets?.[key] || {state:'unverified'};
    const gaps = row.missing_dates || [];
    const scope = row.checked_from && row.checked_through ? `檢查範圍 ${row.checked_from} 至 ${row.checked_through}` : '檢查範圍內';
    const note = gaps.length ? `${scope}，缺 ${gaps.length} 日：${gaps.slice(0,5).join('、')}${gaps.length > 5 ? ' 等' : ''}` :
      row.state === 'publication_lag' ? '保留官方實際發布日期，不以今日日期代填。' :
      row.calendar_verified === false ? '交易日曆證據不足，尚不能判定完整。' : '已按市場別及檢查範圍核對。';
    return `<tr><th scope="row" class="l">${name}</th><td class="l">${escapeHtml(row.latest_date || '尚無資料')}</td><td class="l"><span class="dataset-state" data-state="${escapeHtml(row.state)}">${DATASET_STATES[row.state] || '尚未核實'}</span><br>${escapeHtml(note)}</td></tr>`;
  });
  if (lag) rows.push(`<tr><th scope="row" class="l">尚未納入的交易日</th><td class="l">${escapeHtml(lag.first)} 起</td><td class="l"><span class="dataset-state" data-state="missing">共 ${escapeHtml(lag.text)}</span><br>${escapeHtml(lag.first)} 至 ${escapeHtml(lag.last)}。上方各列只核對各自檢查範圍內的日期。</td></tr>`);
  const benchmark = s.research_benchmark || {};
  const benchmarkPending = !benchmark.latest_date || (benchmark.expected_through && benchmark.latest_date < benchmark.expected_through);
  rows.push(`<tr><th scope="row" class="l">研究基準 TAIEX_TR</th><td class="l">${escapeHtml(benchmark.latest_date || '尚無資料')}</td><td class="l"><span class="dataset-state" data-state="${benchmarkPending ? 'missing' : 'current'}">${benchmarkPending ? '待獨立回補' : '獨立維護'}</span><br>不隨每日行情更新${benchmark.expected_through ? `；本次可補至 ${escapeHtml(benchmark.expected_through)}` : ''}。</td></tr>`);
  const body = el('dataset-status-rows');
  const html = rows.join('');
  if (body && body.dataset.rendered !== html) { body.innerHTML = html; body.dataset.rendered = html; }

  const action = recommendedRepair(s);
  document.querySelectorAll('[data-repair-action]').forEach(button => {
    button.hidden = !action;
    if (!action) return;
    button.dataset.endpoint = action.endpoint;
    if (!button.disabled) setText(button, action.label);
  });
  document.querySelectorAll('[data-repair-note]').forEach(node => { node.hidden = !action?.note; setText(node, action?.note || ''); });
  for (const id of ['btn-stale-update', 'btn-repair-light']) { const button = el(id); if (button) button.disabled = running; }
  if (el('btn-benchmark')) el('btn-benchmark').disabled = running || !benchmarkPending;
  options.onRendered?.({state, lag, action});
}

function renderFreshnessError(error) {
  const el = id => document.getElementById(id);
  const indicator = el('freshness-indicator');
  if (indicator) {
    indicator.dataset.state = 'unknown';
    setText(indicator, '更新狀態無法確認');
    indicator.setAttribute('aria-label', '更新狀態無法確認，查看明細');
  }
  setText(el('freshness-summary'), '無法確認');
  if (el('stale-banner')) { el('stale-banner').hidden = false; el('stale-banner').dataset.state = 'unknown'; }
  setText(el('stale-title'), '更新狀態無法確認');
  setText(el('stale-text'), '請以畫面上的資料日期為準，稍後會自動重新查詢。');
  setText(el('stale-retry'), '');
  setText(el('stale-detail'), error.message);
  const body = el('dataset-status-rows');
  if (body) { body.innerHTML = '<tr><td colspan="3" class="l">暫時無法讀取各資料集狀態，請稍後重試。</td></tr>'; delete body.dataset.rendered; }
  document.querySelectorAll('[data-repair-action], [data-repair-note]').forEach(node => { node.hidden = true; });
}

/* Runs the recommended or named admin repair; the caller refreshes freshness afterwards. */
async function runRepairAction(button, endpoint, report) {
  const original = button.textContent;
  button.disabled = true; button.textContent = '處理中…';
  try {
    const result = await api(endpoint, {method:'POST', body:{}});
    const done = result.status === 'completed';
    report?.(endpoint.endsWith('repair-light')
      ? (done ? '近期修復已完成並重新核對，日期見資料明細。' : '近期修復已執行，仍有待補齊項目，請查看缺漏日期與來源狀態。')
      : (done ? '最新交易日已更新並重新核對。' : '更新已執行，仍有待補齊項目，請查看資料明細。'));
    return result;
  } catch (error) { toastError(`${original}未完成：${error.message}`); return null; }
  finally { button.disabled = false; button.textContent = original; }
}
