const SID = qs("id");
const stockPath = `/api/stocks/${encodeURIComponent(SID || "")}`;
let stockReady = false;
const from = qs("from");
const back = document.getElementById("stock-back");
if (from === "screener") {
  let last = "";
  try { last = sessionStorage.getItem("screener:last") || ""; } catch {}
  back.href = last.startsWith("screener.html?") ? last : "screener.html";
  back.textContent = "← 返回全市場篩選器";
}
if (from === "watchlist") { back.href = "watchlist.html"; back.textContent = "← 返回觀察名單"; }
document.getElementById("link-plan").href = `journal.html?plan=${encodeURIComponent(SID || "")}`;

const charts = {};
function chartOnce(id) {
  if (!window.echarts) {
    document.getElementById(id).textContent = "圖表工具未載入，請閱讀上方摘要與完整數值。";
    return { setOption() {}, resize() {} };
  }
  if (!charts[id]) charts[id] = makeChart(id);
  return charts[id];
}

/* 六個面向使用 roving tabindex；自動啟用並保留各自的載入／重試狀態。 */
const loaded = {}, pending = {};
const tabs = [...document.querySelectorAll('#tabs [role="tab"]')];
function selectTab(button, focus = false) {
  if (!stockReady) return;
  for (const b of tabs) {
    const selected = b === button;
    b.classList.toggle("active", selected);
    b.setAttribute("aria-selected", String(selected));
    b.tabIndex = selected ? 0 : -1;
    document.getElementById(b.getAttribute("aria-controls")).hidden = !selected;
  }
  if (focus) { button.focus(); button.scrollIntoView({ block: "nearest", inline: "nearest" }); }
  loadTab(button.dataset.tab);
  Object.values(charts).forEach(c => c.resize());
}
document.getElementById("tabs").addEventListener("click", e => {
  const button = e.target.closest('[role="tab"]');
  if (button) selectTab(button);
});
document.getElementById("tabs").addEventListener("keydown", e => {
  const index = tabs.indexOf(e.target);
  if (index < 0 || !["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) return;
  e.preventDefault();
  const next = e.key === "Home" ? 0 : e.key === "End" ? tabs.length - 1 :
    (index + (e.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
  selectTab(tabs[next], true);
});

async function loadTab(tab) {
  if (!stockReady || loaded[tab] || pending[tab]) return;
  pending[tab] = true;
  const panel = document.getElementById(`panel-${tab}`);
  const status = document.getElementById(`status-${tab}`);
  panel.setAttribute("aria-busy", "true");
  status.textContent = "正在讀取此面向的資料…";
  try {
    await ({kline: drawKline, revenue: drawRevenue, profit: drawProfit,
      health: drawHealth, valuation: drawValuation, chips: drawChips}[tab])();
    loaded[tab] = true;
    status.textContent = "";
  } catch (err) {
    status.innerHTML = `此面向載入失敗：${escapeHtml(err.message)} <button type="button" class="ghost small">重試此面向</button>`;
    status.querySelector("button").addEventListener("click", () => loadTab(tab));
  } finally {
    pending[tab] = false;
    panel.setAttribute("aria-busy", "false");
    if (!panel.hidden) Object.values(charts).forEach(c => c.resize());
  }
}

/* 圖表與表格讀取相同 rows；缺值保留「—」，原始單位寫在欄名。 */
function dataTable(caption, rows, columns) {
  if (!rows.length) return `<p class="muted">${escapeHtml(caption)}：目前沒有資料。</p>`;
  return `<div class="scroll-x stock-data-scroll" role="region" tabindex="0" aria-label="${escapeHtml(caption)}，可左右捲動"><table class="data"><caption>${escapeHtml(caption)}（${rows.length} 筆；— 表示缺值）</caption><thead><tr>${columns.map(([, label]) => `<th scope="col">${escapeHtml(label)}</th>`).join("")}</tr></thead><tbody>${rows.map(r => `<tr>${columns.map(([key,, format = fmt.num], i) => i === 0 ? `<th scope="row">${escapeHtml(r[key])}</th>` : `<td>${escapeHtml(format(r[key]))}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
}
function setEvidence(tab, summary, tables) {
  document.getElementById(`summary-${tab}`).textContent = summary;
  if (tables != null) document.getElementById(`values-${tab}`).innerHTML = tables;
}
function period(rows, key) {
  return rows.length ? `${rows[0][key]} 至 ${rows.at(-1)[key]}` : "尚無期間資料";
}
function emptyCharts(ids, message) {
  for (const id of ids) document.getElementById(id).textContent = message;
}

/* ── 頭部 ── */
async function loadHead() {
  const ov = await api(`${stockPath}`);
  const { info, quote, valuation, watchlist } = ov;
  document.title = `${SID} ${info.name} — 台股分析`;
  document.getElementById("title").textContent = `${SID} ${info.name}`;
  document.getElementById("mkt-badge").textContent = info.market === "twse" ? "上市" : "上櫃";
  document.getElementById("ind-badge").textContent = info.industry || "—";
  if (quote) {
    document.getElementById("price").textContent = fmt.num(quote.close);
    const chg = document.getElementById("chg");
    const pct = quote.change != null && quote.close ? quote.change / (quote.close - quote.change) * 100 : null;
    chg.textContent = `${fmt.sign(quote.change)} (${fmt.signPct(pct)})`;
    chg.className = fmt.cls(quote.change);
    document.getElementById("qdate").textContent = quote.date;
  }
  else document.getElementById("qdate").textContent = "尚無收盤行情；空白不代表價格為零。";
  if (valuation) {
    document.getElementById("val-line").textContent =
      `PE ${fmt.num(valuation.pe, 1)}｜PB ${fmt.num(valuation.pb, 2)}｜殖利率 ${fmt.pct(valuation.dividend_yield)}`;
  }
  if (watchlist) document.getElementById("watch-tier").value = watchTier = watchlist.tier;
}

let watchTier = "";
document.getElementById("watch-tier").addEventListener("change", async e => {
  const v = e.target.value;
  if (!stockReady || !v) return;
  try {
    if (v === "__remove__") {
      await api(`/api/watchlist/${SID}`, { method: "DELETE" });
      watchTier = "";
      toast("已移出觀察名單");
    } else {
      await api(`/api/watchlist/${SID}`, { method: "POST", body: { tier: v } });
      watchTier = v;
      toast("已加入觀察名單");
    }
  } catch (err) { toastError("觀察名單未更新：" + err.message); }
  e.target.value = watchTier;
});

document.getElementById("btn-refresh").addEventListener("click", async e => {
  if (!stockReady) return;
  e.target.disabled = true; e.target.textContent = "抓取中…";
  try {
    await api(`${stockPath}/deep`, {method: 'POST', body: {}});
    toast("資料已更新，重新載入頁面");
    location.reload();
  } catch (err) { toastError("資料未更新：" + err.message); e.target.disabled = false; e.target.textContent = "重新抓取資料"; }
});

document.getElementById("btn-live").addEventListener("click", async e => {
  if (!stockReady) return;
  e.target.disabled = true; e.target.textContent = "查詢中…";
  const el = document.getElementById("live-quote");
  try {
    const rt = await api(`/api/realtime?ids=${SID}`);
    const q = rt.quotes[0];
    if (!q || q.last == null) {
      el.textContent = "目前查無即時報價（非交易時段或代號異常）";
    } else {
      const chg = q.yesterday ? (q.last / q.yesterday - 1) * 100 : null;
      el.innerHTML = `即時 <b class="${fmt.cls(chg)}">${fmt.num(q.last)} (${fmt.signPct(chg)})</b>
        ｜來源 ${rt.provider}（延遲約 ${rt.delay_seconds} 秒）${q.time ? "｜" + q.time : ""}`;
    }
  } catch (err) { el.textContent = "查詢失敗：" + err.message; }
  e.target.disabled = false; e.target.textContent = "查看即時價";
});

/* ── K線 ── */
function ma(closes, n) {
  return closes.map((_, i) =>
    i + 1 < n ? null : +(closes.slice(i + 1 - n, i + 1).reduce((a, b) => a + b, 0) / n).toFixed(2));
}

async function drawKline() {
  let rows = await api(`${stockPath}/kline?days=500`);
  if (rows.length < 30) {  // 深度抓取可能剛在背景開始 → 稍候再試一次
    await new Promise(r => setTimeout(r, 4000));
    rows = await api(`${stockPath}/kline?days=500`);
  }
  const maValues = { MA20: ma(rows.map(r => r.close), 20), MA60: ma(rows.map(r => r.close), 60),
    MA120: ma(rows.map(r => r.close), 120), MA240: ma(rows.map(r => r.close), 240) };
  const raw = rows.map((r, i) => ({ ...r, ...Object.fromEntries(Object.entries(maValues).map(([key, values]) => [key, values[i]])) }));
  const last = rows.at(-1);
  const incomplete = rows.filter(r => [r.open, r.high, r.low].some(v => v == null)).length;
  setEvidence("kline", last ? `${period(rows, "date")}，共 ${rows.length} 筆收盤紀錄。最新收盤 ${fmt.num(last.close)} 元，成交量 ${fmt.zhang(last.volume)}。${incomplete ? `${incomplete} 筆缺少開／高／低，該筆不繪製 K 棒；收盤與均線仍保留。` : ""}均線為目前載入區間的收盤簡單平均，資料不足不計算。` : "尚無量價資料，缺值不代表價格為零。",
    dataTable("收盤量價與均線", raw, [["date", "日期"], ["open", "開盤（元）"], ["high", "最高（元）"], ["low", "最低（元）"], ["close", "收盤（元）"], ["volume", "成交量（股）", fmt.int], ...[20,60,120,240].map(n => [`MA${n}`, `${n} 日均線（元）`])]));
  if (!rows.length) {
    emptyCharts(["chart-kline"], "尚無量價資料，可稍後重新載入；管理員可重新抓取資料。");
    document.getElementById("tech-line").textContent = "技術摘要：尚無收盤資料。";
    return;
  }
  const dates = rows.map(r => r.date);
  const closes = rows.map(r => r.close);
  const k = rows.map(r => [r.open, r.high, r.low].some(v => v == null) ? ["-", "-", "-", "-"] : [r.open, r.close, r.low, r.high]);
  const vols = rows.map((r, i) => ({
    value: r.volume == null ? null : r.volume / 1000,
    itemStyle: { color: r.open == null ? CHART_THEME.MUTED : r.close >= r.open ? CHART_THEME.UP : CHART_THEME.DOWN, opacity: .6 },
  }));
  const mas = { MA20: ma(closes, 20), MA60: ma(closes, 60), MA120: ma(closes, 120), MA240: ma(closes, 240) };
  const maColors = { MA20: "#835516", MA60: CHART_THEME.ACCENT, MA120: "#705c80", MA240: CHART_THEME.MUTED };
  chartOnce("chart-kline").setOption(baseOption({
    tooltip: { trigger: "axis", axisPointer: { type: "cross" },
      backgroundColor: CHART_THEME.PANEL2, borderColor: CHART_THEME.BORDER, textStyle: { color: CHART_THEME.TEXT, fontSize: 12 } },
    legend: { textStyle: { color: CHART_THEME.MUTED }, top: 0 },
    axisPointer: { link: [{ xAxisIndex: "all" }] },
    grid: [{ left: 64, right: 20, top: 34, height: "58%" },
           { left: 64, right: 20, top: "76%", height: "16%" }],
    xAxis: [
      { type: "category", data: dates, ...axisStyle() },
      { type: "category", gridIndex: 1, data: dates, axisLabel: { show: false }, ...axisStyle() },
    ],
    yAxis: [
      { type: "value", scale: true, ...axisStyle() },
      { type: "value", gridIndex: 1, ...axisStyle(), axisLabel: { color: CHART_THEME.MUTED, formatter: v => fmt.int(v) } },
    ],
    dataZoom: [
      { type: "inside", xAxisIndex: [0, 1], start: 55, end: 100,
        zoomOnMouseWheel: "ctrl", moveOnMouseWheel: false },  // 滾輪留給頁面捲動
      { type: "slider", xAxisIndex: [0, 1], bottom: 0, height: 16,
        borderColor: CHART_THEME.BORDER, backgroundColor: CHART_THEME.PANEL2 },
    ],
    series: [
      { name: SID, type: "candlestick", data: k,
        itemStyle: { color: CHART_THEME.UP, color0: CHART_THEME.DOWN,
                     borderColor: CHART_THEME.UP, borderColor0: CHART_THEME.DOWN } },
      ...Object.entries(mas).map(([name, data]) => ({
        name, type: "line", data, showSymbol: false,
        lineStyle: { width: 1.2, color: maColors[name] }, itemStyle: { color: maColors[name] } })),
      { name: "成交量(張)", type: "bar", xAxisIndex: 1, yAxisIndex: 1, data: vols },
    ],
  }));
  const techLine = document.getElementById("tech-line");
  try {
    const t = await api(`${stockPath}/technical`);
    const position = (value, above, below) => value == null ? "資料不足，位置未知" : value ? above : below;
    techLine.textContent =
      `技術摘要（${rows.at(-1).date}）：近 ${Math.min(rows.length, 260)} 筆觀測最高 ${fmt.num(t.high_52w)}、最低 ${fmt.num(t.low_52w)} 元（僅含有高低價的紀錄）；距觀測高點 ${fmt.pct(t.off_high_pct)}；` +
      `量比 ${fmt.num(t.vol_ratio)}；60 日線：${position(t.above_ma60, "站上", "跌破")}；年線：${position(t.above_ma240, "站上", "之下")}。`;
  } catch (err) {
    techLine.textContent = `技術摘要暫時無法載入：${err.message}。量價摘要與數值表仍可查閱。`;
    const retry = document.createElement("button");
    retry.type = "button"; retry.className = "ghost small"; retry.textContent = "重試技術摘要";
    retry.addEventListener("click", () => { loaded.kline = false; loadTab("kline"); });
    techLine.append(" ", retry);
  }
}

/* ── 月營收 ── */
async function drawRevenue() {
  const rows = await api(`${stockPath}/revenue`);
  const last = rows.at(-1);
  document.getElementById("revenue-scale").disabled = !rows.length;
  setEvidence("revenue", last ? `${period(rows, "ym")}，共 ${rows.length} 個月。最新 ${last.ym} 月營收 ${fmt.yi(last.revenue)}，年增率 ${fmt.pct(last.yoy)}，累計年增率 ${fmt.pct(last.acc_yoy)}。` : "尚無月營收資料。下方原始數值不會把缺值當作零。");
  document.getElementById("revenue-values").innerHTML = dataTable("月營收原始數值", rows, [["ym", "月份"], ["revenue", "月營收（元）", fmt.int], ["yoy", "年增率（%）"], ["acc_yoy", "累計年增率（%）"]]);
  if (!rows.length) {
    document.getElementById("chart-revenue").innerHTML = '<p class="muted">尚無月營收資料。</p>';
    return;
  }
  const yoy = rows.flatMap(r => [r.yoy, r.acc_yoy]).filter(v => v != null && Number.isFinite(Number(v))).map(Number);
  const ordered = [...yoy].sort((a, b) => a - b);
  const edge = Math.max(Math.abs(ordered[Math.floor(ordered.length * .08)] || 0), Math.abs(ordered[Math.ceil(ordered.length * .92) - 1] || 0), 20);
  const hasExtreme = yoy.some(v => Math.abs(v) > edge);
  const scale = document.getElementById("revenue-scale");
  const note = document.getElementById("revenue-scale-note");
  if (!hasExtreme) { scale.value = "full"; scale.disabled = true; }
  const draw = () => {
    const focused = scale.value === "focus" && hasExtreme;
    note.textContent = focused ? `年增率右軸聚焦 ±${fmt.num(edge, 0)}%；${yoy.filter(v => Math.abs(v) > edge).length} 個極值超出目前畫幅。可切換「顯示完整極值」或展開全部月份原始數值。` : "年增率右軸顯示所有極值；下方可展開全部月份原始數值。";
    chartOnce("chart-revenue").setOption(baseOption({
    legend: { textStyle: { color: CHART_THEME.MUTED, fontSize: 12 }, top: 0, left: 8, itemWidth: 13 },
    grid: { left: 58, right: 48, top: 65, bottom: 36 },
    xAxis: { type: "category", data: rows.map(r => r.ym), ...axisStyle() },
    yAxis: [
      { type: "value", name: "營收(億)", ...axisStyle(), axisLabel: { color: CHART_THEME.MUTED, formatter: v => fmt.int(v / 1e8) } },
      { type: "value", name: "%", position: "right", min: focused ? -edge : null, max: focused ? edge : null, splitLine: { show: false }, ...axisStyle() },
    ],
    series: [
      { name: "月營收", type: "bar", data: rows.map(r => r.revenue),
        itemStyle: { color: CHART_THEME.ACCENT, opacity: .65 } },
      { name: "YoY%", type: "line", yAxisIndex: 1, showSymbol: false,
        data: rows.map(r => r.yoy), lineStyle: { color: "#705c80", width: 2 }, itemStyle: { color: "#705c80" } },
      { name: "累計YoY%", type: "line", yAxisIndex: 1, showSymbol: false,
        data: rows.map(r => r.acc_yoy), lineStyle: { color: CHART_THEME.ACCENT, width: 1.5, type: "dashed" }, itemStyle: { color: CHART_THEME.ACCENT } },
    ],
    }), true);
  };
  scale.onchange = draw;
  draw();
}

/* ── 獲利能力 ── */
async function drawProfit() {
  const rows = await api(`${stockPath}/financials`);
  const last = rows.at(-1);
  setEvidence("profit", last ? `${period(rows, "quarter")}，共 ${rows.length} 季。最新 ${last.quarter} 毛利率 ${fmt.pct(last.gross_margin)}、營益率 ${fmt.pct(last.op_margin)}、淨利率 ${fmt.pct(last.net_margin)}、近四季 ROE ${fmt.pct(last.roe_ttm)}、單季 EPS ${fmt.num(last.eps)} 元。` : "尚無獲利資料，不能以空白推論獲利為零。",
    dataTable("季度獲利能力", rows, [["quarter", "季度"], ["gross_margin", "毛利率（%）"], ["op_margin", "營益率（%）"], ["net_margin", "淨利率（%）"], ["roe_ttm", "近四季 ROE（%）"], ["eps", "單季 EPS（元）"]]));
  if (!rows.length) {
    emptyCharts(["chart-margins", "chart-eps"], "尚無財報資料。");
    return;
  }
  const q = rows.map(r => r.quarter);
  chartOnce("chart-margins").setOption(baseOption({
    legend: { textStyle: { color: CHART_THEME.MUTED }, top: 0 },
    xAxis: { type: "category", data: q, ...axisStyle() },
    yAxis: { type: "value", name: "%", ...axisStyle() },
    series: [
      { name: "毛利率", type: "line", data: rows.map(r => r.gross_margin), showSymbol: false, lineStyle: { width: 2 } },
      { name: "營益率", type: "line", data: rows.map(r => r.op_margin), showSymbol: false, lineStyle: { width: 2 } },
      { name: "淨利率", type: "line", data: rows.map(r => r.net_margin), showSymbol: false, lineStyle: { width: 2 } },
      { name: "ROE(近四季)", type: "line", data: rows.map(r => r.roe_ttm), showSymbol: false,
        lineStyle: { width: 1.5, type: "dashed", color: CHART_THEME.WARN } },
    ],
  }));
  chartOnce("chart-eps").setOption(baseOption({
    legend: { textStyle: { color: CHART_THEME.MUTED }, top: 0 },
    xAxis: { type: "category", data: q, ...axisStyle() },
    yAxis: { type: "value", name: "EPS", ...axisStyle() },
    series: [{ name: "單季EPS", type: "bar", data: rows.map(r => r.eps),
               itemStyle: { color: "rgba(240,82,79,.6)" } }],
  }));
}

/* ── 財務品質 ── */
async function drawHealth() {
  const rows = await api(`${stockPath}/financials`);
  const last = rows.at(-1);
  setEvidence("health", last ? `${period(rows, "quarter")}。最新 ${last.quarter} 營業現金流 ${fmt.yi(last.op_cashflow)}、稅後淨利 ${fmt.yi(last.net_income)}、自由現金流 ${fmt.yi(last.fcf)}；存貨 ${fmt.yi(last.inventory)}、應收帳款 ${fmt.yi(last.receivables)}、負債比 ${fmt.pct(last.debt_ratio)}。` : "尚無財務品質資料，缺值不代表現金流為零。",
    dataTable("季度財務品質", rows, [["quarter", "季度"], ["op_cashflow", "營業現金流（元）", fmt.int], ["net_income", "稅後淨利（元）", fmt.int], ["fcf", "自由現金流（元）", fmt.int], ["inventory", "存貨（元）", fmt.int], ["receivables", "應收帳款（元）", fmt.int], ["debt_ratio", "負債比（%）"]]));
  if (!rows.length) {
    emptyCharts(["chart-cashflow", "chart-working"], "尚無財報資料。");
    return;
  }
  const q = rows.map(r => r.quarter);
  chartOnce("chart-cashflow").setOption(baseOption({
    legend: { textStyle: { color: CHART_THEME.MUTED }, top: 0 },
    xAxis: { type: "category", data: q, ...axisStyle() },
    yAxis: { type: "value", name: "億", ...axisStyle(), axisLabel: { color: CHART_THEME.MUTED, formatter: v => fmt.int(v / 1e8) } },
    series: [
      { name: "營業現金流", type: "bar", data: rows.map(r => r.op_cashflow),
        itemStyle: { color: "rgba(63,178,111,.65)" } },
      { name: "稅後淨利", type: "bar", data: rows.map(r => r.net_income),
        itemStyle: { color: CHART_THEME.ACCENT, opacity: .65 } },
      { name: "自由現金流", type: "line", data: rows.map(r => r.fcf), showSymbol: false,
        lineStyle: { color: CHART_THEME.WARN, width: 1.6 } },
    ],
  }));
  chartOnce("chart-working").setOption(baseOption({
    legend: { textStyle: { color: CHART_THEME.MUTED }, top: 0 },
    xAxis: { type: "category", data: q, ...axisStyle() },
    yAxis: [{ type: "value", name: "億", ...axisStyle(), axisLabel: { color: CHART_THEME.MUTED, formatter: v => fmt.int(v / 1e8) } },
            { type: "value", name: "負債比%", position: "right", splitLine: { show: false }, max: 100, ...axisStyle() }],
    series: [
      { name: "存貨", type: "line", data: rows.map(r => r.inventory), showSymbol: false },
      { name: "應收帳款", type: "line", data: rows.map(r => r.receivables), showSymbol: false },
      { name: "負債比%", type: "line", yAxisIndex: 1, data: rows.map(r => r.debt_ratio),
        showSymbol: false, lineStyle: { type: "dashed" } },
    ],
  }));
}

/* ── 估值 ── */
async function drawValuation() {
  const v = await api(`${stockPath}/valuation`);
  const hist = v.history || [];
  const current = v.current;
  setEvidence("valuation", current ? `${current.date} 本益比 ${fmt.num(current.pe, 1)} 倍、股價淨值比 ${fmt.num(current.pb, 2)} 倍、殖利率 ${fmt.pct(current.dividend_yield)}。圖表紀錄 ${period(hist, "date")}，共 ${hist.length} 筆；百分位依現有歷史樣本計算，不保證涵蓋五年。` : "尚無估值紀錄，不能據此判斷便宜或昂貴。",
    dataTable("估值歷史（圖表期間）", hist, [["date", "日期"], ["pe", "本益比（倍）"], ["pb", "股價淨值比（倍）"], ["dividend_yield", "殖利率（%）"]]));
  if (hist.length > 5) {
    chartOnce("chart-pe").setOption(baseOption({
      legend: { textStyle: { color: CHART_THEME.MUTED }, top: 0 },
      xAxis: { type: "category", data: hist.map(r => r.date), ...axisStyle() },
      yAxis: [{ type: "value", name: "PE", scale: true, ...axisStyle() },
              { type: "value", name: "殖利率%", position: "right", splitLine: { show: false }, ...axisStyle() }],
      series: [
        { name: "本益比", type: "line", data: hist.map(r => r.pe), showSymbol: false, lineStyle: { width: 2 } },
        { name: "殖利率%", type: "line", yAxisIndex: 1, data: hist.map(r => r.dividend_yield),
          showSymbol: false, lineStyle: { width: 1.5, type: "dashed", color: CHART_THEME.DOWN } },
      ],
    }));
  } else {
    document.getElementById("chart-pe").innerHTML =
      '<p class="muted">估值歷史隨每日更新累積（回填歷史不含估值），持續使用數週後這裡會有自身區間可比較。</p>';
  }
  const cur = v.current || {};
  document.getElementById("val-summary").innerHTML = `
    <table class="data">
      <tr><th class="l">指標</th><th>目前</th><th>自身歷史百分位</th></tr>
      <tr><td class="l">本益比 PE</td><td>${fmt.num(cur.pe, 1)}</td><td>${v.pe_percentile != null ? fmt.pct(v.pe_percentile, 0) + "（越低越便宜）" : "—"}</td></tr>
      <tr><td class="l">股價淨值比 PB</td><td>${fmt.num(cur.pb, 2)}</td><td>${v.pb_percentile != null ? fmt.pct(v.pb_percentile, 0) : "—"}</td></tr>
      <tr><td class="l">殖利率</td><td>${fmt.pct(cur.dividend_yield)}</td><td class="muted">近四季EPS ${fmt.num(v.eps_ttm)} 元</td></tr>
    </table>
    <p class="muted">框架提醒：估值應比較自身歷史、同業、成長率與景氣位置。此處只提供已累積的自身歷史樣本；比較前先確認期間與筆數，同業可開個股頁對照。</p>`;
}

/* ── 籌碼 ── */
async function drawChips() {
  const c = await api(`${stockPath}/chips`);
  const inst = c.institutional || [];
  const mg = c.margin || [];
  let cum = 0;
  let cumulativeKnown = true;
  const cumLine = inst.map(r => {
    if (r.total_net == null) cumulativeKnown = false;
    if (!cumulativeKnown) return null;
    return (cum += r.total_net) / 1000;
  });
  const lastInst = inst.at(-1), lastMargin = mg.at(-1);
  const net = n => {
    const slice = inst.slice(-n);
    return slice.length && slice.every(r => r.total_net != null) ? fmt.zhang(slice.reduce((sum, r) => sum + r.total_net, 0)) : "資料不足";
  };
  const totals = inst.length ? `最近 ${Math.min(inst.length, 5)} 筆合計 ${net(5)}；最近 ${Math.min(inst.length, 20)} 筆合計 ${net(20)}。` : "";
  setEvidence("chips", `${lastInst ? `法人紀錄 ${period(inst, "date")}；最新 ${lastInst.date} 外資 ${fmt.zhang(lastInst.foreign_net)}、投信 ${fmt.zhang(lastInst.trust_net)}、自營商 ${fmt.zhang(lastInst.dealer_net)}、合計 ${fmt.zhang(lastInst.total_net)}。` : "尚無法人資料。"} ${lastMargin ? `最新 ${lastMargin.date} 融資餘額 ${fmt.num(lastMargin.margin_balance)} 張、融券餘額 ${fmt.num(lastMargin.short_balance)} 張。` : "尚無融資券資料。"}${totals} 買賣超看持續性，不只看單日；累計由載入首筆起算，遇缺值後不再累計。`,
    dataTable("法人買賣超", inst.map((r, i) => ({...r, cumulative: cumLine[i] == null ? null : cumLine[i] * 1000})), [["date", "日期"], ["foreign_net", "外資（股）", fmt.int], ["trust_net", "投信（股）", fmt.int], ["dealer_net", "自營商（股）", fmt.int], ["total_net", "合計（股）", fmt.int], ["cumulative", "區間累計（股）", fmt.int]]) +
    dataTable("融資券餘額", mg, [["date", "日期"], ["margin_balance", "融資餘額（張）"], ["short_balance", "融券餘額（張）"]]));
  if (inst.length) {
    chartOnce("chart-insti").setOption(baseOption({
      legend: { textStyle: { color: CHART_THEME.MUTED }, top: 0 },
      xAxis: { type: "category", data: inst.map(r => r.date), ...axisStyle() },
      yAxis: [{ type: "value", name: "張", ...axisStyle(), axisLabel: { color: CHART_THEME.MUTED, formatter: v => fmt.int(v) } },
              { type: "value", name: "累計(張)", position: "right", splitLine: { show: false }, ...axisStyle() }],
      series: [
        { name: "外資", type: "bar", stack: "n", data: inst.map(r => r.foreign_net == null ? null : r.foreign_net / 1000) },
        { name: "投信", type: "bar", stack: "n", data: inst.map(r => r.trust_net == null ? null : r.trust_net / 1000) },
        { name: "自營", type: "bar", stack: "n", data: inst.map(r => r.dealer_net == null ? null : r.dealer_net / 1000) },
        { name: "累計買賣超", type: "line", yAxisIndex: 1, data: cumLine, showSymbol: false,
          lineStyle: { color: CHART_THEME.WARN, width: 1.6 } },
      ],
    }));
  } else emptyCharts(["chart-insti"], "尚無法人資料。");
  if (mg.length) {
    chartOnce("chart-margin-bal").setOption(baseOption({
      legend: { textStyle: { color: CHART_THEME.MUTED }, top: 0 },
      xAxis: { type: "category", data: mg.map(r => r.date), ...axisStyle() },
      yAxis: { type: "value", name: "張", ...axisStyle() },
      series: [
        { name: "融資餘額", type: "line", data: mg.map(r => r.margin_balance), showSymbol: false },
        { name: "融券餘額", type: "line", data: mg.map(r => r.short_balance), showSymbol: false },
      ],
    }));
  }
  else emptyCharts(["chart-margin-bal"], "尚無融資券資料。");
}

/* ── 系統判讀摘要 ── */
async function loadNarrative() {
  const s = await api(`${stockPath}/summary`);
  const badge = document.getElementById("tilt-badge");
  badge.textContent = `${s.tilt}（量化 ${s.quant_score}/${s.quant_max}）`;
  badge.className = "badge " + (s.tilt === "偏多" ? "up" : s.tilt === "偏空" ? "down" : "");
  document.getElementById("verdict-evidence").textContent = `${s.tilt}｜量化 ${s.quant_score}/${s.quant_max}`;
  const rows = s.lines.map(l => `
    <p style="margin:6px 0"><b>${l.label}</b>
      <span class="muted">（${l.effective != null ? l.effective : "—"}/${l.max}分）</span>：
      ${l.detail}</p>`).join("");
  const missing = s.missing.length
    ? `<p class="warn" style="margin:10px 0 0">尚需你自行判斷評分：${s.missing.join("、")}（這些是主觀判斷，系統無法自動生成）</p>`
    : "";
  document.getElementById("narrative").innerHTML = rows + missing;
}

/* ── 評分表 ── */
async function loadScore() {
  const card = await api(`${stockPath}/score`);
  const el = document.getElementById("scorecard");
  el.innerHTML = card.items.map(it => `
    <div class="score-row">
      <span>${it.label}<span class="muted">/${it.max}</span></span>
      <div class="score-bar"><div style="width:${(it.effective || 0) / it.max * 100}%"></div></div>
      <span>${it.effective != null ? it.effective : "—"}</span>
      <input type="number" min="0" max="${it.max}" step="0.5" data-key="${it.key}"
             aria-label="${it.label}人工評分，滿分 ${it.max}"
             value="${it.manual != null ? it.manual : ""}"
             placeholder="${it.manual_only ? "人工" : "自動" + (it.auto ?? "")}">
      ${it.reasons.length ? `<div class="reasons">${it.reasons.join("；")}</div>` : ""}
    </div>`).join("");
  document.getElementById("score-total").textContent =
    card.complete ? `總分 ${card.total} / 100` : `（${card.total} 分，質化項未評）`;
  document.getElementById("score-verdict").textContent = card.verdict || "產業/競爭力/催化劑需人工評分";
  document.getElementById("verdict-score").textContent =
    card.complete ? `${card.total} / 100` : `${card.total} 分｜質化項未評`;
  if (card.note) document.getElementById("score-note").value = card.note;
}

document.getElementById("btn-save-score").addEventListener("click", async () => {
  if (!stockReady) return;
  const payload = { note: document.getElementById("score-note").value };
  document.querySelectorAll("#scorecard input").forEach(inp => {
    payload[inp.dataset.key] = inp.value === "" ? null : parseFloat(inp.value);
  });
  try {
    await api(`${stockPath}/score`, { method: "POST", body: payload });
    toast("評分已儲存");
    loadSection("scorecard", "verdict-score", loadScore);
  } catch (err) { toastError("評分未儲存：" + err.message); }
});

/* ── 警訊與查核 ── */
async function loadAlerts() {
  const a = await api(`${stockPath}/alerts`);
  document.getElementById("alerts").innerHTML = a.alerts.length
    ? a.alerts.map(x => `<p class="warn" style="margin:6px 0">⚠ ${x.message}</p>`).join("")
    : '<p class="muted" style="margin:6px 0">未偵測到量化警訊</p>';
  const risk = document.getElementById("verdict-risk");
  risk.textContent = a.alerts.length ? `${a.alerts.length} 項量化警訊` : "未偵測到量化警訊";
  risk.classList.toggle("caution", a.alerts.length > 0);
  document.getElementById("checklist").innerHTML = a.checklist.map(c =>
    `<p style="margin:6px 0"><a href="${escapeHtml(c.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(c.item)} <span aria-hidden="true">↗</span><span class="sr-only">（另開視窗）</span></a></p>`).join("");
}

/* 各區塊獨立載入；失敗時就地說明並提供重試，不留下永遠的「載入中」。 */
function loadSection(bodyId, verdictId, fn) {
  return fn().catch(err => {
    const el = document.getElementById(bodyId);
    el.innerHTML = `<p class="inline-error" role="alert">無法載入：${escapeHtml(err.message)}
      <button type="button" class="ghost small">重試</button></p>`;
    el.querySelector("button").addEventListener("click", () => {
      el.textContent = "載入中…";
      loadSection(bodyId, verdictId, fn);
    });
    document.getElementById(verdictId).textContent = "無法載入";
  });
}

function setStockReady(ready) {
  stockReady = ready;
  document.getElementById("stock-content").hidden = !ready;
  document.getElementById("watch-tier").disabled = !ready;
  document.getElementById("btn-refresh").disabled = !ready;
}
async function startStock() {
  setStockReady(false);
  const state = document.getElementById("stock-load-state");
  state.hidden = false;
  document.getElementById("stock-recovery").hidden = true;
  document.getElementById("stock-load-title").textContent = "正在讀取個股資料";
  document.getElementById("stock-load-message").textContent = "確認股票後，會載入圖表與研究證據。";
  const retry = document.getElementById("stock-retry");
  retry.disabled = true;
  try {
    if (!SID) throw new Error("網址缺少股票代號，請搜尋股票或返回市場概況。");
    await loadHead();
    setStockReady(true);
    state.hidden = true;
    loadTab("kline");
    loadSection("narrative", "verdict-evidence", loadNarrative);
    loadSection("scorecard", "verdict-score", loadScore);
    loadSection("alerts", "verdict-risk", loadAlerts);
  } catch (err) {
    document.title = "個股資料未載入 — 台股分析";
    document.getElementById("title").textContent = SID ? `個股 ${SID}` : "個股研究";
    document.getElementById("stock-load-title").textContent = "個股資料未載入";
    document.getElementById("stock-load-message").textContent = `${err.message} 此頁尚無可供判讀的資料。請確認代號，或重新載入。`;
    document.getElementById("stock-recovery").hidden = false;
    retry.hidden = !SID;
  } finally { retry.disabled = false; }
}
document.getElementById("stock-retry").addEventListener("click", startStock);
document.getElementById("stock-search").addEventListener("click", () => {
  const search = document.getElementById("global-search");
  search?.focus(); search?.select();
});
startStock();
