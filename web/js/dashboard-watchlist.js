/* Page-memory preview of the authenticated owner's existing watchlist. */
(() => {
  const PAGE_SIZE = 6;
  const tiers = [['holding','持有中'], ['waiting','等買點'], ['research','研究中']];
  const tierLabels = Object.fromEntries(tiers);
  const el = id => document.getElementById(id);
  const tier = el('watch-tier'), industry = el('watch-industry');
  let rows = [], page = 1;
  const industryOf = row => row.industry?.trim() || '未分類';

  // Holdings carry the live risk, so they lead; alerts break ties within a stage.
  const tierRank = Object.fromEntries(tiers.map(([key], i) => [key, i]));
  const ordered = list => list.map((row, i) => [row, i]).sort(([a, i], [b, j]) =>
    (tierRank[a.tier] ?? 9) - (tierRank[b.tier] ?? 9) || (b.alerts?.length || 0) - (a.alerts?.length || 0) || i - j).map(([row]) => row);

  function renderTally() {
    const tally = el('watch-tally');
    tally.hidden = !rows.length;
    if (!rows.length) return;
    const flagged = rows.filter(row => row.alerts?.length);
    const counts = tiers.map(([key, label]) => [label, rows.filter(row => row.tier === key).length]).filter(([, n]) => n);
    tally.innerHTML = `${counts.map(([label, n]) => `${label} <strong>${n}</strong>`).join('<span aria-hidden="true"> · </span>')}` +
      `<span class="watch-tally-alerts" data-flagged="${flagged.length > 0}">${flagged.length ? `有警訊 <strong>${flagged.length}</strong> 檔` : '目前無財報警訊'}</span>`;
  }

  function render() {
    const filtered = ordered(rows.filter(row => (tier.value === 'all' || row.tier === tier.value) &&
      (industry.value === 'all' || industryOf(row) === industry.value)));
    const pages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
    page = Math.min(page, pages);
    const start = (page - 1) * PAGE_SIZE;
    const shown = filtered.slice(start, start + PAGE_SIZE);
    // Filters and paging only earn their space once the list is longer than one page.
    const paged = rows.length > PAGE_SIZE;
    renderTally();
    el('watch-filters').hidden = !paged;
    el('watch-empty').hidden = !!rows.length;
    el('watch-filter-empty').hidden = !rows.length || !!filtered.length;
    el('watch-preview-region').hidden = !shown.length;
    el('watch-table').hidden = !shown.length;
    el('watch-pagination').hidden = !paged;
    const table = el('watch-table');
    table.querySelectorAll('tbody').forEach(body => body.remove());
    table.insertAdjacentHTML('beforeend', shown.map(row => {
      const alerts = row.alerts || [];
      const url = `stock.html?id=${encodeURIComponent(row.stock_id)}`;
      const alert = alerts.length ? `<a class="watch-alert" href="${url}#stock-risk" aria-label="查看 ${escapeHtml(row.name)} 的 ${alerts.length} 項警訊">
            <span class="badge warn">警訊 ${alerts.length}</span><span class="watch-alert-text">${escapeHtml(alerts[0].message)}${alerts.length > 1 ? `（另 ${alerts.length - 1} 項）` : ''}</span></a>` : '';
      // The same alert renders in a column on wide screens and as a sub-row on phones; CSS
      // display:none removes the other copy from the accessibility tree and tab order.
      const alertRow = alert ? `<tr class="watch-alert-row"><td colspan="6" class="l">${alert}</td></tr>` : '';
      return `<tbody class="watch-item"><tr>
        <td class="l"><a href="${url}"><span class="watch-stock-id">${escapeHtml(row.stock_id)}</span><span class="watch-stock-name">${escapeHtml(row.name)}</span></a></td>
        <td class="l"><span class="badge tier" data-tier="${escapeHtml(row.tier)}">${escapeHtml(tierLabels[row.tier] || row.tier || '未分級')}</span></td>
        <td>${fmt.num(row.close)}<small class="watch-quote-date muted">${escapeHtml(row.qdate || '日期未知')}</small></td>
        <td class="watch-detail ${fmt.cls(row.change)}">${fmt.sign(row.change)}</td>
        <td class="watch-detail">${fmt.num(row.pe, 1)}</td>
        <td class="l watch-alert-cell">${alert || '<span class="muted">—</span>'}</td>
      </tr>${alertRow}</tbody>`;
    }).join(''));
    const scope = tier.value !== 'all' || industry.value !== 'all' ? `（全部 ${rows.length} 檔）` : '';
    el('watch-page-count').textContent = filtered.length
      ? `${start + 1}–${start + shown.length}／${filtered.length} 檔${scope} · ${page}／${pages} 頁`
      : `0 檔${scope}`;
    el('watch-prev').disabled = page <= 1;
    el('watch-next').disabled = page >= pages;
    el('watch-preview-region').scrollTop = 0;
  }

  function filterChanged() { page = 1; render(); }
  tier.addEventListener('change', filterChanged);
  industry.addEventListener('change', filterChanged);
  el('watch-reset').addEventListener('click', () => {
    tier.value = industry.value = 'all';
    filterChanged();
    tier.focus();
  });
  for (const [id, delta] of [['watch-prev', -1], ['watch-next', 1]]) {
    el(id).addEventListener('click', event => {
      page += delta;
      render();
      if (event.currentTarget.disabled) el('watch-page-count').focus();
    });
  }

  window.watchPreview = {
    setRows(nextRows) {
      if (!Array.isArray(nextRows)) throw new Error('觀察名單回應格式不正確');
      rows = nextRows;
      const selectedTier = tier.value, selectedIndustry = industry.value;
      tier.replaceChildren(new Option('全部階段', 'all'), ...tiers.map(([key,label]) =>
        new Option(`${label}（${rows.filter(row => row.tier === key).length}）`, key)));
      const industries = [...new Set(rows.map(industryOf))].sort((a,b) => a.localeCompare(b, 'zh-Hant'));
      industry.replaceChildren(new Option('全部產業', 'all'), ...industries.map(name =>
        new Option(`${name}（${rows.filter(row => industryOf(row) === name).length}）`, name)));
      tier.value = tiers.some(([key]) => key === selectedTier) ? selectedTier : 'all';
      industry.value = industries.includes(selectedIndustry) ? selectedIndustry : 'all';
      render();
    },
  };
})();
