/* Strategy Lab：Home / Builder / Result。計算與持久化全部由後端負責。 */
(function () {
  const root = document.getElementById("lab-root");
  const state = { templates: [], strategy: null, step: 1, params: {}, backtest: {}, run: {} };
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? "").replace(/[&<>"']/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const clone = value => JSON.parse(JSON.stringify(value));

  const LABELS = {
    quality_growth_momentum: "品質 × 成長 × 動能",
    revenue_growth_acceleration: "營收成長加速",
    momentum_trend: "動能 + 趨勢",
    value_quality: "價值 × 品質",
    low_volatility_quality: "低波動 × 品質",
    strict: "嚴格（Strict）",
    assumed: "假設（Assumed）",
    TAIEX_TR: "加權報酬指數（TAIEX TR）",
    prices: "價格", month_revenue: "月營收", financials: "財報", fundamentals: "基本面",
    valuations: "估值", sector: "產業分類", liquidity: "流動性", availability: "公告可得性",
    daily: "每日", weekly: "每週", monthly: "每月",
  };
  /* 後端模板的「已知失效情境」是英文原文；這裡只做顯示用對照，查不到就顯示原文。 */
  const FAILURE_REGIMES = {
    "sharp factor rotations": "因子急速輪動",
    "early-cycle low-quality rallies": "景氣初升段的低品質股反彈",
    "crowded momentum reversals": "動能交易過度擁擠後的反轉",
    "sparse strict-mode financial coverage": "嚴格模式下財報覆蓋稀少",
    "base-effect distortions": "基期效應造成的失真",
    "cyclical peaks": "景氣循環高點",
    "one-off revenue recognition": "一次性營收認列",
    "delayed or restated revenue data": "營收資料延遲或重編",
    "range-bound whipsaws": "盤整區間的來回洗盤",
    "gap reversals": "跳空後反轉",
    "fast crash-and-rebound markets": "急跌急彈的行情",
    "unadjusted corporate-action discontinuities": "未還原公司行動造成的價格斷點",
    "value traps": "價值陷阱",
    "structural industry decline": "產業結構性衰退",
    "rate-driven multiple changes": "利率變動帶來的評價倍數改變",
    "thin valuation history": "估值歷史資料不足",
    "missing historical sector taxonomy": "缺少歷史產業分類",
    "sharp risk-on rallies": "風險偏好急升的行情",
    "volatility regime breaks": "波動結構轉變",
    "crowded defensive trades": "防禦型交易過度擁擠",
    "stale prices when liquidity filters are relaxed": "放寬流動性門檻後的陳舊價格",
  };
  const regime = text => FAILURE_REGIMES[text] || text;
  const modeLabel = value => value ? (LABELS[value] || value) : "—";
  const HYPOTHESES = {
    quality_growth_momentum: "獲利與資產品質穩健、成長可見，且價格動能確認的公司，較可能持續優於市場。",
    revenue_growth_acceleration: "月營收維持正成長且持續加速，可能在季報完全反映前揭露營運動能改善。",
    momentum_trend: "中期相對強勢且維持在長期均線之上的股票，較可能延續既有趨勢。",
    value_quality: "價格合理且獲利品質穩健的公司，較可能避開部分價值陷阱。",
    low_volatility_quality: "較低已實現波動與基本面品質並存，有機會形成較防禦性的股票組合。",
  };
  const KEY_LABELS = {
    universe: "股票池", ranking: "排名處理", selection: "選股", portfolio: "組合規則",
    rebalance: "再平衡", quality_weights: "品質權重", growth: "成長", momentum: "動能",
    factor_weights: "因子權重", revenue: "營收", trend: "趨勢", value: "價值",
    volatility: "波動", backtest_config: "回測設定", execution: "成交", missing: "缺值處理",
    sizing: "部位", costs: "成本", initial_cash: "初始資金", risk_free_rate: "無風險利率",
    liquidity_lookback: "流動性回看期", min_liquidity_observations: "最少流動性觀察數",
    min_average_daily_amount: "最低日均成交金額", security_types: "證券類型",
    winsor_lower: "Winsor 下界", winsor_upper: "Winsor 上界", missing_policy: "缺值政策",
    top_n: "持股數", weighting: "權重方法", cash_weight: "現金權重", frequency: "頻率",
    dates: "指定日期", lookback: "回看期", skip_recent: "略過最近交易日", ma_lookback: "均線期數",
    require_above_ma: "要求高於均線", acceleration_window: "加速視窗", require_positive_yoy: "YoY 必須為正",
    require_positive_acceleration: "加速度必須為正", max_valuation_age_days: "估值最長沿用日數",
    sector_neutral: "產業中性", minimum_sector_size: "最小產業樣本", sector_fallback: "產業資料替代方式",
    weights: "權重", acceleration: "加速度", quality: "品質", low_volatility: "低波動", backtest: "回測",
    commission_rate: "手續費率", commission_discount: "手續費折數", minimum_commission: "最低手續費",
    sell_tax_rate: "賣出證交稅", slippage_bps: "滑價（bps）", price: "成交價",
    delay_sessions: "延遲交易日", valuation: "估值缺值", max_stale_sessions: "最長陳舊日數",
    method: "部位方法", max_position_weight: "單一部位上限", lot_size: "交易單位",
  };

  function deepMerge(base, overrides) {
    const out = clone(base || {});
    Object.entries(overrides || {}).forEach(([key, value]) => {
      if (value && typeof value === "object" && !Array.isArray(value) &&
          out[key] && typeof out[key] === "object" && !Array.isArray(out[key])) {
        out[key] = deepMerge(out[key], value);
      } else out[key] = clone(value);
    });
    return out;
  }

  function template(key) { return state.templates.find(t => t.key === key); }
  function label(key) { return LABELS[key] || KEY_LABELS[key] || key.replaceAll("_", " "); }
  function dateTime(value) {
    if (!value) return "—";
    return new Date(value).toLocaleString("zh-TW", { dateStyle: "medium", timeStyle: "short" });
  }
  function metric(value, kind = "num", digits = 2) {
    if (value == null || !Number.isFinite(Number(value))) return "—";
    if (kind === "pct") return `${Number(value) > 0 ? "+" : ""}${Number(value).toFixed(digits)}%`;
    if (kind === "money") return `NT$${Math.round(Number(value)).toLocaleString("zh-TW")}`;
    if (kind === "days") return `${Number(value).toFixed(0)} 天`;
    return Number(value).toFixed(digits);
  }
  const statusLabel = value => ({ queued: "排隊中", running: "執行中", completed: "已完成", failed: "失敗", cancelled: "已取消" }[value] || value);
  function signClass(value) { return value > 0 ? "positive" : value < 0 ? "negative" : ""; }
  function getPath(obj, path) { return path.split(".").reduce((value, key) => value?.[key], obj); }
  function setPath(obj, path, value) {
    const keys = path.split(".");
    let current = obj;
    keys.slice(0, -1).forEach(key => current = current[key] ??= {});
    current[keys.at(-1)] = value;
  }

  function setRoot(html, busy = false) {
    root.innerHTML = html;
    root.setAttribute("aria-busy", String(busy));
  }
  function loading(title, detail) {
    setRoot(`<div class="card state-card"><div class="state-spinner" aria-hidden="true"></div>
      <h1>${esc(title)}</h1><p class="muted">${esc(detail)}</p></div>`, true);
  }
  function errorState(title, error, retry = "location.reload()") {
    setRoot(`<div class="card state-card state-error"><div class="state-icon" aria-hidden="true">!</div>
      <h1>${esc(title)}</h1><p>${esc(error?.message || error)}</p>
      <button type="button" onclick="${retry}">再試一次</button></div>`);
  }

  async function loadCatalog() {
    if (!state.templates.length) state.templates = await api("/api/strategy-lab/templates");
  }

  function templateCard(t) {
    const requirements = Object.keys(t.required_data || {}).filter(k => k !== "publication_modes");
    return `<article class="card template-card">
      <h2>${esc(label(t.key))}</h2>
      <p>${esc(HYPOTHESES[t.key] || t.hypothesis)}</p>
      <div class="template-meta"><span>需要資料</span>${requirements.map(k => esc(label(k))).join(" · ") || "價格"}</div>
      <details class="more"><summary>適用限制</summary>
        <ul>${t.known_failure_regimes.map(x => `<li>${esc(regime(x))}</li>`).join("")}</ul>
      </details>
      <a class="btn" href="strategy.html?new=${encodeURIComponent(t.key)}">使用此模板</a>
    </article>`;
  }

  async function home() {
    loading("載入策略實驗室", "整理策略與最近回測摘要。");
    try {
      await loadCatalog();
      const strategies = await api("/api/strategy-lab/strategies");
      const runLists = await Promise.all(strategies.map(s =>
        api(`/api/strategy-lab/strategies/${s.id}/runs?limit=5`).catch(() => [])));
      const rows = strategies.map((s, i) => ({ strategy: s, runs: runLists[i] }));
      let recent = rows.flatMap(x => x.runs.map(run => ({ ...run, strategy: x.strategy })))
        .sort((a, b) => String(b.created_at).localeCompare(String(a.created_at))).slice(0, 8);
      recent = await Promise.all(recent.map(async run => {
        try { return { ...run, ...(await api(`/api/strategy-lab/runs/${run.id}`)), strategy: run.strategy }; }
        catch { return run; }
      }));
      setRoot(`<header class="lab-page-head">
          <div><div class="eyebrow">策略研究 / 研究起點</div><h1>策略實驗室</h1>
            <p class="muted">從可解釋模板建立策略、保留版本，並用實際資料檢查歷史表現。</p></div>
          <div class="head-actions"><a class="btn ghost" href="strategy.html?walkforward=home">樣本外驗證</a>
            <a class="btn ghost" href="strategy.html?optimization=home">參數穩健性</a>
            <a class="btn" href="#templates">建立新策略</a></div>
        </header>
        <section class="card lab-section" aria-labelledby="my-strategies-title">
          <div class="section-head"><div><h2 id="my-strategies-title">我的策略</h2>
            <p class="muted">已儲存的策略與最新一次回測。</p></div></div>
          ${rows.length ? strategyTable(rows) : `<div class="empty-state">
            <h3>還沒有儲存的策略</h3><p>從下方五個研究模板選一個開始。預設參數是研究起點，不代表最佳組合。</p>
            <a class="btn" href="#templates">選擇模板</a></div>`}
        </section>
        <section class="card lab-section" aria-labelledby="recent-title">
          <div class="section-head"><div><h2 id="recent-title">最近回測摘要</h2>
            <p class="muted">只顯示最近執行紀錄，不進行策略排名。</p></div></div>
          ${recent.length ? recentTable(recent) : `<div class="empty-inline">尚無回測紀錄。建立策略後即可從「確認並執行」步驟執行。</div>`}
        </section>
        <section class="lab-section" id="templates" aria-labelledby="templates-title">
          <div class="section-head"><div>
            <h2 id="templates-title">研究模板</h2>
            <p class="muted">每個模板都可修改。不選出贏家，也不預設哪個策略較好。</p></div></div>
          <div class="template-grid">${state.templates.map(templateCard).join("")}</div>
        </section>`);
    } catch (error) { errorState("策略實驗室無法載入", error); }
  }

  function strategyTable(rows) {
    return `<div class="scroll-x"><table class="data lab-table"><thead><tr>
      <th class="l">策略</th><th class="l">模板</th><th>版本</th><th>最近回測</th>
      <th>CAGR</th><th>Sharpe</th><th>最大回撤</th><th class="l">動作</th></tr></thead><tbody>
      ${rows.map(({ strategy: s, runs }) => {
        const run = runs[0]; const m = run?.metrics || {};
        const resultUrl = run?.status === "completed" ? `strategy.html?id=${s.id}&run=${run.id}` : `strategy.html?id=${s.id}`;
        return `<tr><td class="l"><a href="${resultUrl}">${esc(s.name)}</a></td>
          <td class="l">${esc(label(s.template_key))}</td><td>v${s.latest_version?.version_no || 1}</td>
          <td>${run ? `<span class="status-text status-${esc(run.status)}">${esc(statusLabel(run.status))}</span><br><small>${dateTime(run.finished_at || run.created_at)}</small>` : "—"}</td>
          <td class="${signClass(m.cagr)}">${metric(m.cagr, "pct")}</td><td>${metric(m.sharpe)}</td>
          <td class="${signClass(m.max_drawdown)}">${metric(m.max_drawdown, "pct")}</td>
          <td class="l"><a href="strategy.html?id=${s.id}&edit=1">設定</a></td></tr>`;
      }).join("")}</tbody></table></div>`;
  }

  function recentTable(runs) {
    return `<div class="scroll-x"><table class="data lab-table"><thead><tr>
      <th class="l">策略</th><th>期間</th><th class="l">模式</th><th class="l">狀態</th><th>淨報酬</th><th>執行時間</th>
      </tr></thead><tbody>${runs.map(run => {
        const m = run.metrics || {}; const request = run.request || {};
        return `<tr><td class="l"><a href="strategy.html?id=${run.strategy.id}&run=${run.id}">${esc(run.strategy.name)}</a></td>
          <td>${esc(request.start || "—")} — ${esc(request.end || "—")}</td><td class="l">${esc(modeLabel(run.publication_mode))}</td>
          <td class="l"><span class="status-text status-${esc(run.status)}">${esc(statusLabel(run.status))}</span></td>
          <td class="${signClass(m.net_return)}">${metric(m.net_return, "pct")}</td><td>${dateTime(run.finished_at || run.created_at)}</td></tr>`;
      }).join("")}</tbody></table></div>`;
  }

  const BACKTEST_DEFAULT = {
    initial_cash: 1000000, risk_free_rate: 0,
    execution: { price: "open", delay_sessions: 1 },
    missing: { execution: "next_available", valuation: "carry_forward", max_stale_sessions: 5 },
    sizing: { method: "signal_weights", max_position_weight: 1, cash_weight: 0, lot_size: 1 },
    costs: { commission_rate: .001425, commission_discount: .6, minimum_commission: 1,
             sell_tax_rate: .003, slippage_bps: 0 },
  };

  function defaultDates() {
    const end = new Date(); end.setDate(end.getDate() - 2);
    const start = new Date(end); start.setFullYear(start.getFullYear() - 2);
    return { start: start.toISOString().slice(0, 10), end: end.toISOString().slice(0, 10) };
  }

  async function builder(strategyId, templateKey) {
    loading("開啟策略設定", "準備模板與版本設定。");
    try {
      await loadCatalog();
      state.strategy = strategyId ? await api(`/api/strategy-lab/strategies/${strategyId}`) : null;
      const key = state.strategy?.template_key || templateKey || state.templates[0].key;
      const meta = template(key);
      if (!meta) throw new Error("找不到指定模板");
      state.params = deepMerge(meta.default_config, state.strategy?.latest_version?.params || {});
      state.backtest = deepMerge(BACKTEST_DEFAULT, state.strategy?.latest_version?.backtest || {});
      state.run = { ...defaultDates(), publication_mode: "strict", benchmark_id: "TAIEX_TR",
        universe_mode: "liquid", explicit_ids: "2330, 2454, 2317, 2603, 2408" };
      state.step = 1;
      renderBuilder(meta);
    } catch (error) { errorState("策略設定無法載入", error); }
  }

  const stepLabels = ["策略模板", "研究範圍", "因子與參數", "組合規則",
    "再平衡", "交易成本", "資料與公告模式", "確認並執行"];

  function renderBuilder(meta) {
    setRoot(`<header class="lab-page-head compact research-header"><div><a class="back-link" href="strategy.html">← 策略實驗室</a>
        <div class="eyebrow">策略研究 / 定義卷宗</div><h1>${state.strategy ? "建立策略新版本" : "建立研究策略"}</h1>
        <p class="muted">${esc(label(meta.key))} · ${state.strategy ? `v${state.strategy.latest_version.version_no}` : "尚未建立"}</p></div>
        ${state.strategy ? `<a class="btn ghost" href="strategy.html?id=${state.strategy.id}">查看結果</a>` : ""}</header>
      <nav class="steps lab-steps" id="builder-steps" aria-label="策略設定步驟">
        ${stepLabels.map((name, i) => `<button type="button" data-step="${i + 1}"><span class="n">${i + 1}</span>${esc(name)}</button>`).join("")}
      </nav>
      <form id="builder-form" class="builder-form" novalidate>
        <section class="card step" data-step="1">${templateStep(meta)}</section>
        <section class="card step" data-step="2">${universeStep()}</section>
        <section class="card step" data-step="3">${factorStep(meta)}</section>
        <section class="card step" data-step="4">${portfolioStep()}</section>
        <section class="card step" data-step="5">${rebalanceStep()}</section>
        <section class="card step" data-step="6">${costStep()}</section>
        <section class="card step" data-step="7">${dataStep()}</section>
        <section class="card step" data-step="8"><div id="review-content"></div></section>
        <div class="builder-actions"><button class="ghost" id="builder-prev" type="button">上一步</button>
          <span class="muted" id="autosave-note">設定只會在儲存或執行時建立新版本。</span>
          <button id="builder-next" type="button">下一步</button></div>
      </form>`);
    bindBuilder(meta);
    goStep(1);
  }

  function templateStep(meta) {
    return `<div class="step-title"><span>步驟 1</span><h2>策略模板</h2></div>
      <div class="builder-narrow"><label for="strategy-name">策略名稱</label>
        <input id="strategy-name" required maxlength="100" value="${esc(state.strategy?.name || label(meta.key))}">
        <label for="strategy-description">研究備註（選填）</label>
        <textarea id="strategy-description" rows="3" placeholder="記錄策略假設、用途或修改方向">${esc(state.strategy?.description || "")}</textarea>
      </div>
      <div class="template-summary"><div><div class="template-overline">已選策略模板</div>
        <h3>${esc(label(meta.key))}</h3><p>${esc(HYPOTHESES[meta.key] || meta.hypothesis)}</p></div>
        <div><h3>已知失效情境</h3><ul>${meta.known_failure_regimes.map(x => `<li>${esc(regime(x))}</li>`).join("")}</ul></div></div>
      <div class="alert info"><strong>研究起點</strong><div class="d">模板預設值尚未經參數穩健性研究或樣本外（OOS）驗證。</div></div>`;
  }

  function numberField(path, text, options = {}) {
    const source = options.source === "backtest" ? state.backtest : state.params;
    const raw = getPath(source, path); const scale = options.scale || 1;
    return `<div><label for="p-${path}">${esc(text)}</label><input id="p-${path}" data-path="${path}"
      data-source="${options.source || "params"}" data-scale="${scale}" type="number"
      ${options.min != null ? `min="${options.min}"` : ""} ${options.max != null ? `max="${options.max}"` : ""}
      step="${options.step || "1"}" value="${raw == null ? "" : Number(raw) * scale}"></div>`;
  }
  function checkboxField(path, text, source = "params") {
    const obj = source === "backtest" ? state.backtest : state.params;
    return `<label class="check-row"><input type="checkbox" data-path="${path}" data-source="${source}"
      ${getPath(obj, path) ? "checked" : ""}><span>${esc(text)}</span></label>`;
  }
  function selectField(path, text, values, source = "params") {
    const obj = source === "backtest" ? state.backtest : state.params; const current = getPath(obj, path);
    return `<div><label for="p-${path}">${esc(text)}</label><select id="p-${path}" data-path="${path}" data-source="${source}">
      ${values.map(([value, name]) => `<option value="${esc(value)}" ${current === value ? "selected" : ""}>${esc(name)}</option>`).join("")}</select></div>`;
  }

  function universeStep() {
    return `<div class="step-title"><span>步驟 2</span><h2>研究範圍</h2></div>
      <p class="step-intro">先定義策略可投資範圍，再選擇本次回測要用流動性股票池或明確代號。</p>
      <div class="form-grid">${numberField("universe.min_average_daily_amount", "最低日均成交金額（NT$）", { min: 0, step: 1000000 })}
        ${numberField("universe.liquidity_lookback", "流動性回看期（交易日）", { min: 1 })}
        ${numberField("universe.min_liquidity_observations", "最少有效觀察數", { min: 1 })}</div>
      <fieldset class="choice-field"><legend>本次回測的股票範圍</legend>
        <label class="choice-row"><input type="radio" name="universe-mode" value="liquid" checked>
          <span><strong>流動性股票池</strong><small>依回測起日之前的成交金額篩選。</small></span></label>
        <label class="choice-row"><input type="radio" name="universe-mode" value="explicit">
          <span><strong>明確代號</strong><small>適合短時間驗證或固定研究範圍。</small></span></label>
      </fieldset>
      <div id="explicit-universe" hidden><label for="explicit-ids">股票代號（逗號或換行分隔）</label>
        <textarea id="explicit-ids" rows="3">${esc(state.run.explicit_ids)}</textarea></div>
      <div class="warning-line"><span class="severity-dot medium"></span><div><strong>存續偏誤（survivorship bias）</strong>
        <span>股票主檔仍是目前快照，無法完整還原歷史指數成分；結果頁會保留此限制。</span></div></div>
      <details class="more"><summary>進階範圍設定</summary>
        <p class="fin-note">第一版以日均成交金額替代市值門檻。歷史分類與短暫符合門檻的標的仍可能不完整。</p>
      </details>`;
  }

  const FACTOR_FIELDS = {
    quality_growth_momentum: [
      ["momentum.lookback", "動能回看期", {}], ["momentum.skip_recent", "略過最近交易日", {}],
      ["factor_weights.quality", "品質權重", { step: .05 }], ["factor_weights.growth", "成長權重", { step: .05 }],
      ["factor_weights.momentum", "動能權重", { step: .05 }]],
    revenue_growth_acceleration: [
      ["revenue.acceleration_window", "加速視窗（月）", {}], ["revenue.weights.yoy", "YoY 權重", { step: .05 }],
      ["revenue.weights.acceleration", "加速度權重", { step: .05 }]],
    momentum_trend: [
      ["momentum.lookback", "動能回看期", {}], ["momentum.skip_recent", "略過最近交易日", {}],
      ["trend.ma_lookback", "趨勢均線期數", {}], ["factor_weights.momentum", "動能權重", { step: .05 }],
      ["factor_weights.trend", "趨勢權重", { step: .05 }]],
    value_quality: [
      ["value.max_valuation_age_days", "估值最長沿用日數", {}], ["value.minimum_sector_size", "最小產業樣本", {}],
      ["factor_weights.value", "價值權重", { step: .05 }], ["factor_weights.quality", "品質權重", { step: .05 }]],
    low_volatility_quality: [
      ["volatility.lookback", "波動回看期", {}], ["factor_weights.low_volatility", "低波動權重", { step: .05 }],
      ["factor_weights.quality", "品質權重", { step: .05 }]],
  };

  function factorStep(meta) {
    const fields = FACTOR_FIELDS[meta.key] || [];
    const flags = meta.key === "revenue_growth_acceleration"
      ? checkboxField("revenue.require_positive_yoy", "YoY 必須為正") + checkboxField("revenue.require_positive_acceleration", "加速度必須為正")
      : meta.key === "momentum_trend" ? checkboxField("trend.require_above_ma", "收盤必須高於趨勢均線")
      : meta.key === "value_quality" ? checkboxField("value.sector_neutral", "啟用產業中性排名") : "";
    return `<div class="step-title"><span>步驟 3</span><h2>因子與參數</h2></div>
      <p class="step-intro">只呈現目前模板會用到的主要參數；排名與缺值策略收在進階設定。</p>
      <div class="form-grid">${numberField("selection.top_n", "持股數（前 N 名）", { min: 1 })}
        ${fields.map(([path, text, opts]) => numberField(path, text, opts)).join("")}</div>
      <div class="check-grid">${flags}</div>
      <details class="more"><summary>進階排名設定</summary>
        <div class="form-grid">${numberField("ranking.winsor_lower", "Winsor 下界", { min: 0, max: 1, step: .01 })}
          ${numberField("ranking.winsor_upper", "Winsor 上界", { min: 0, max: 1, step: .01 })}
          ${selectField("ranking.missing_policy", "缺值政策", [["exclude", "排除"], ["median", "中位數"], ["worst", "最差排名"]])}</div>
      </details>`;
  }

  function portfolioStep() {
    return `<div class="step-title"><span>步驟 4</span><h2>組合規則</h2></div>
      <div class="form-grid">${selectField("portfolio.weighting", "配置方式", [["equal", "等權"], ["score", "依分數加權"]])}
        ${numberField("portfolio.cash_weight", "策略保留現金（%）", { min: 0, max: 99, step: 1, scale: 100 })}
        ${numberField("sizing.max_position_weight", "單一部位上限（%）", { source: "backtest", min: 1, max: 100, scale: 100 })}
        ${numberField("sizing.lot_size", "交易單位（股）", { source: "backtest", min: 1 })}</div>
      <details class="more"><summary>進階部位設定</summary>
        ${selectField("sizing.method", "部位方法", [["signal_weights", "使用策略目標權重"], ["equal", "引擎等權"], ["score_weighted", "引擎分數加權"]], "backtest")}
      </details>`;
  }

  function rebalanceStep() {
    return `<div class="step-title"><span>步驟 5</span><h2>再平衡</h2></div>
      <div class="form-grid">${selectField("rebalance.frequency", "再平衡頻率", [["daily", "每日"], ["weekly", "每週"], ["monthly", "每月"]])}</div>
      <div class="alert info"><strong>T+1 成交</strong><div class="d">訊號在 T 日觀察，最早於下一個交易日成交；不允許當日偷看收盤後成交。</div></div>`;
  }

  function costStep() {
    return `<div class="step-title"><span>步驟 6</span><h2>交易成本</h2></div>
      <p class="step-intro">淨報酬（Net）會扣除以下成本；毛報酬（Gross）同步保留，方便辨識成本拖累。</p>
      <div class="form-grid">${numberField("costs.commission_rate", "券商手續費率（%）", { source: "backtest", scale: 100, step: .0001, min: 0 })}
        ${numberField("costs.commission_discount", "手續費折數", { source: "backtest", step: .05, min: 0 })}
        ${numberField("costs.minimum_commission", "最低手續費（NT$）", { source: "backtest", min: 0 })}
        ${numberField("costs.sell_tax_rate", "賣出證交稅（%）", { source: "backtest", scale: 100, step: .01, min: 0 })}
        ${numberField("costs.slippage_bps", "滑價（bps）", { source: "backtest", step: 1, min: 0 })}</div>
      <details class="more"><summary>進階成交設定</summary><div class="form-grid">
        ${selectField("execution.price", "成交價格", [["open", "下一交易日開盤"], ["close", "下一交易日收盤"]], "backtest")}
        ${numberField("execution.delay_sessions", "延遲交易日", { source: "backtest", min: 1 })}
        ${selectField("missing.execution", "缺少成交價", [["next_available", "等下一個可用價格"], ["cancel", "取消訂單"]], "backtest")}
        ${numberField("missing.max_stale_sessions", "估值最長陳舊交易日", { source: "backtest", min: 0 })}</div></details>`;
  }

  function dataStep() {
    return `<div class="step-title"><span>步驟 7</span><h2>資料與公告模式</h2></div>
      <div class="form-grid"><div><label for="run-start">回測開始</label><input id="run-start" type="date" value="${state.run.start}"></div>
        <div><label for="run-end">回測結束</label><input id="run-end" type="date" value="${state.run.end}"></div>
        <div><label for="benchmark-id">比較基準</label><select id="benchmark-id"><option value="TAIEX_TR">加權報酬指數（TAIEX TR）</option></select></div>
        ${numberField("initial_cash", "初始資金（NT$）", { source: "backtest", min: 1, step: 100000 })}
      </div>
      <fieldset class="choice-field"><legend>公告模式</legend>
        <label class="choice-row"><input type="radio" name="publication-mode" value="strict" checked>
          <span><strong>嚴格（Strict）</strong><small>只接受實際或已觀察到的公告日期；基本面覆蓋可能很少。</small></span></label>
        <label class="choice-row"><input type="radio" name="publication-mode" value="assumed">
          <span><strong>假設（Assumed）</strong><small>允許法定申報期限假設；結果會持續顯示警示。</small></span></label>
      </fieldset>
      <div class="coverage-callout"><strong>資料覆蓋</strong><p>五年回填仍需以結果中的實際區間、股票數與資料筆數判讀。缺資料不會被包裝成正式多年績效。</p></div>
      <details class="more"><summary>進階研究假設</summary>
        <div class="form-grid">${numberField("risk_free_rate", "無風險利率（%）", { source: "backtest", scale: 100, step: .1 })}</div>
        <p class="fin-note">免費資料沒有保存歷次修訂版本（非 revision-vintage 資料庫）；目前公告值可能包含後來的重編。</p>
      </details>`;
  }

  function bindBuilder(meta) {
    $("builder-steps").querySelectorAll("button").forEach(button => button.addEventListener("click", () => {
      const next = Number(button.dataset.step);
      if (!state.strategy && next > 1) return;
      syncBuilderState(); goStep(next);
    }));
    $("builder-prev").addEventListener("click", () => { syncBuilderState(); goStep(state.step - 1); });
    $("builder-next").addEventListener("click", async () => {
      syncBuilderState();
      if (state.step === 1 && !state.strategy) return createFromTemplate(meta);
      if (state.step === 8) return saveAndRun();
      goStep(state.step + 1);
    });
    $("builder-form").querySelectorAll("[data-path]").forEach(el => {
      el.addEventListener(el.type === "checkbox" ? "change" : "input", () => syncParam(el));
    });
    document.querySelectorAll("input[name='universe-mode']").forEach(el => el.addEventListener("change", () => {
      state.run.universe_mode = el.value; $("explicit-universe").hidden = el.value !== "explicit";
    }));
    document.querySelectorAll("input[name='publication-mode']").forEach(el => el.addEventListener("change", () => state.run.publication_mode = el.value));
  }

  function syncParam(el) {
    const obj = el.dataset.source === "backtest" ? state.backtest : state.params;
    let value = el.type === "checkbox" ? el.checked : el.type === "number" ? (el.value === "" ? null : Number(el.value)) : el.value;
    if (el.dataset.scale && value != null) value /= Number(el.dataset.scale);
    setPath(obj, el.dataset.path, value);
  }

  function syncBuilderState() {
    document.querySelectorAll("[data-path]").forEach(syncParam);
    state.run.start = $("run-start")?.value || state.run.start;
    state.run.end = $("run-end")?.value || state.run.end;
    state.run.benchmark_id = $("benchmark-id")?.value || state.run.benchmark_id;
    state.run.explicit_ids = $("explicit-ids")?.value || state.run.explicit_ids;
    state.run.publication_mode = document.querySelector("input[name='publication-mode']:checked")?.value || state.run.publication_mode;
    state.run.universe_mode = document.querySelector("input[name='universe-mode']:checked")?.value || state.run.universe_mode;
  }

  async function createFromTemplate(meta) {
    const name = $("strategy-name").value.trim();
    if (!name) { $("strategy-name").focus(); toast("請先輸入策略名稱"); return; }
    const button = $("builder-next"); button.disabled = true; button.textContent = "建立中…";
    try {
      state.strategy = await api("/api/strategy-lab/strategies", { method: "POST", body: {
        name, description: $("strategy-description").value.trim() || null, template_key: meta.key,
      }});
      history.replaceState({}, "", `strategy.html?id=${state.strategy.id}&edit=1`);
      document.querySelector(".lab-page-head h1").textContent = "建立策略新版本";
      document.querySelector(".lab-page-head p").textContent =
        `${label(meta.key)} · v${state.strategy.latest_version.version_no}`;
      $("builder-steps").querySelectorAll("button").forEach(b => b.disabled = false);
      toast("策略已建立，接著設定研究範圍"); goStep(2);
    } catch (error) { toastError(`建立失敗：${error.message}`); }
    finally { button.disabled = false; }
  }

  function goStep(next) {
    state.step = Math.max(1, Math.min(8, next));
    document.querySelectorAll(".step").forEach(el => el.classList.toggle("active", Number(el.dataset.step) === state.step));
    $("builder-steps").querySelectorAll("button").forEach(button => {
      const active = Number(button.dataset.step) === state.step;
      button.classList.toggle("active", active); button.setAttribute("aria-current", active ? "step" : "false");
      button.disabled = !state.strategy && Number(button.dataset.step) > 1;
    });
    $("builder-prev").disabled = state.step === 1;
    $("builder-next").textContent = state.step === 1 && !state.strategy ? "建立並繼續" : state.step === 8 ? "執行回測" : "下一步";
    if (state.step === 8) renderReview();
    document.querySelector(`.step[data-step='${state.step}']`)?.focus({ preventScroll: true });
  }

  function renderReview() {
    syncBuilderState();
    const explicit = state.run.explicit_ids.split(/[\s,]+/).filter(Boolean);
    $("review-content").innerHTML = `<div class="step-title"><span>步驟 8</span><h2>確認並執行</h2></div>
      <div class="review-grid"><div><h3>策略</h3><dl>
        <dt>名稱</dt><dd>${esc($("strategy-name").value)}</dd><dt>模板</dt><dd>${esc(label(state.strategy.template_key))}</dd>
        <dt>持股數</dt><dd>前 ${esc(getPath(state.params, "selection.top_n"))} 名</dd><dt>再平衡</dt><dd>${esc(modeLabel(getPath(state.params, "rebalance.frequency")))}</dd></dl></div>
        <div><h3>回測</h3><dl><dt>期間</dt><dd>${esc(state.run.start)} — ${esc(state.run.end)}</dd>
        <dt>股票範圍</dt><dd>${state.run.universe_mode === "explicit" ? `${explicit.length} 個明確代號` : "流動性股票池"}</dd>
        <dt>比較基準</dt><dd>${esc(label(state.run.benchmark_id))}</dd><dt>公告模式</dt><dd>${esc(modeLabel(state.run.publication_mode))}</dd></dl></div></div>
      <div class="warning-line"><span class="severity-dot medium"></span><div><strong>結果是歷史回測，不是預測</strong>
        <span>執行後請先檢查資料覆蓋、存續偏誤與公告模式警示，再解讀績效。</span></div></div>`;
  }

  async function saveAndRun() {
    syncBuilderState();
    if (!state.run.start || !state.run.end || state.run.start >= state.run.end) { toast("請確認回測起訖日期"); return; }
    const explicit = state.run.explicit_ids.split(/[\s,]+/).map(x => x.trim()).filter(Boolean);
    if (state.run.universe_mode === "explicit" && !explicit.length) { toast("請至少輸入一個股票代號"); return; }
    const button = $("builder-next"); button.disabled = true; button.textContent = "儲存設定…";
    try {
      state.strategy = await api(`/api/strategy-lab/strategies/${state.strategy.id}`, { method: "PATCH", body: {
        name: $("strategy-name").value.trim(), description: $("strategy-description").value.trim() || null,
        params: state.params, backtest: state.backtest,
      }});
      const body = { start: state.run.start, end: state.run.end, publication_mode: state.run.publication_mode,
        benchmark_id: state.run.benchmark_id };
      if (state.run.universe_mode === "explicit") body.stock_ids = explicit;
      else body.universe = { as_of: state.run.start,
        min_avg_amount: getPath(state.params, "universe.min_average_daily_amount"),
        min_observations: getPath(state.params, "universe.min_liquidity_observations") };
      button.textContent = "排入回測…";
      const queued = await api(`/api/strategy-lab/strategies/${state.strategy.id}/runs`, { method: "POST", body });
      location.href = `strategy.html?id=${state.strategy.id}&run=${queued.run_id}`;
    } catch (error) { toastError(`無法執行：${error.message}`); button.disabled = false; button.textContent = "執行回測"; }
  }

  async function resultPage(strategyId, runId) {
    loading("載入策略結果", "確認版本、資料覆蓋與回測狀態。");
    try {
      await loadCatalog();
      const strategy = await api(`/api/strategy-lab/strategies/${strategyId}`);
      let selected = runId;
      if (!selected) {
        const runs = await api(`/api/strategy-lab/strategies/${strategyId}/runs?limit=50`);
        selected = runs.find(r => r.status === "completed")?.id;
        if (!selected) return noResult(strategy, runs[0]);
      }
      const run = await api(`/api/strategy-lab/runs/${selected}`);
      if (run.status !== "completed") return runState(strategy, run);
      const [result, equity] = await Promise.all([
        api(`/api/strategy-lab/runs/${selected}/result`), api(`/api/strategy-lab/runs/${selected}/equity`),
      ]);
      renderResult(strategy, result, equity);
    } catch (error) { errorState("策略結果無法載入", error, `location.href='strategy.html?id=${strategyId}'`); }
  }

  function noResult(strategy, latest) {
    setRoot(`<header class="lab-page-head compact"><div><a class="back-link" href="strategy.html">← 策略實驗室</a>
      <h1>${esc(strategy.name)}</h1><p class="muted">${esc(label(strategy.template_key))} · v${strategy.latest_version.version_no}</p></div></header>
      <div class="card state-card"><div class="state-icon neutral" aria-hidden="true">—</div><h2>還沒有可顯示的結果</h2>
      <p>${latest?.status === "failed" ? `最近一次執行失敗：${esc(latest.error || "未知錯誤")}` : "完成設定並執行回測後，這裡會顯示績效與資料品質。"}</p>
      <a class="btn" href="strategy.html?id=${strategy.id}&edit=1">設定並執行</a></div>`);
  }

  function runState(strategy, run) {
    if (run.status === "failed") {
      setRoot(`<header class="lab-page-head compact"><div><a class="back-link" href="strategy.html">← 策略實驗室</a><h1>${esc(strategy.name)}</h1></div></header>
        <div class="card state-card state-error"><div class="state-icon" aria-hidden="true">!</div><h2>回測失敗</h2>
        <p>${esc((run.error || "未知錯誤").split("\n")[0])}</p><a class="btn" href="strategy.html?id=${strategy.id}&edit=1">返回設定</a></div>`);
      return;
    }
    setRoot(`<header class="lab-page-head compact"><div><a class="back-link" href="strategy.html">← 策略實驗室</a>
      <h1>${esc(strategy.name)}</h1><p class="muted">回測執行中 · 公告模式：${esc(modeLabel(run.publication_mode))}</p></div></header>
      <div class="card run-progress"><div class="state-spinner" aria-hidden="true"></div><div><h2>${run.status === "queued" ? "等待執行" : "計算歷史結果"}</h2>
      <p class="muted">目前步驟：${run.status === "queued" ? "建立資料快照" : "建立時點資料（point-in-time）並計算交易"}</p>
      <div class="progress-track"><div></div></div><p class="fin-note">頁面會自動更新。長時間運算在服務重啟後會標記為失敗。</p></div></div>`, true);
    setTimeout(() => resultPage(strategy.id, run.id), 1200);
  }

  function renderResult(strategy, result, equity) {
    const m = result.metrics || {}, request = result.request || {}, coverage = result.coverage || {};
    const coveragePct = coverage.stocks_requested ? coverage.stocks_with_quotes / coverage.stocks_requested * 100 : 0;
    const warningCount = (result.warnings || []).length + (coverage.notes || []).length + 1;
    setRoot(`<header class="result-header card research-header"><div><a class="back-link" href="strategy.html">← 策略實驗室</a>
        <div class="eyebrow">歷史證據 / 回測</div><h1>${esc(strategy.name)}</h1>
        <p>${esc(label(strategy.template_key))} · 版本 ${esc(request.strategy_version_no || "—")}</p></div>
      <dl class="result-meta"><div><dt>研究期間</dt><dd>${esc(request.start)} — ${esc(request.end)}</dd></div>
        <div><dt>比較基準</dt><dd>${esc(label(request.benchmark_id || result.metadata?.benchmark_id))}</dd></div>
        <div><dt>公告模式</dt><dd><span class="pill ${result.publication_mode === "assumed" ? "hold" : "on"}">${esc(modeLabel(result.publication_mode))}</span></dd></div>
        <div><dt>完成時間</dt><dd>${dateTime(result.finished_at)}</dd></div></dl>
      <div class="head-actions"><a class="btn" href="strategy.html?walkforward=new&id=${strategy.id}&source_run=${result.id}">樣本外驗證（OOS）</a>
        <a class="btn ghost" href="strategy.html?optimization=new&id=${strategy.id}&source_run=${result.id}">參數穩健性</a>
        <a class="btn ghost" href="strategy.html?id=${strategy.id}&edit=1">設定</a></div>
      ${researchMetadata([["研究身分", strategy.name], ["版本", `v${request.strategy_version_no || "—"}`], ["證據型別", "歷史回測"], ["研究期間", `${request.start || "—"} — ${request.end || "—"}`], ["來源", `回測 #${result.id || "—"} · ${modeLabel(result.publication_mode)}`], ["目前階段", "回測"], ["下一步", "檢查資料品質，再做參數穩健性研究與樣本外驗證"]])}</header>
      <div class="research-disclosure" role="note"><strong>歷史回測 ≠ 未來預測</strong><span>先閱讀資料覆蓋、公告模式與警示，再解讀績效。這些數值尚不是獨立 OOS 證據。</span></div>
      <section class="kpi-grid" aria-label="主要績效指標">
        ${kpi("CAGR", m.cagr, "pct")} ${kpi("Sharpe", m.sharpe)} ${kpi("最大回撤", m.max_drawdown, "pct")}
        ${kpi("Sortino", m.sortino)} ${kpi("淨報酬", m.net_return, "pct")} ${kpi("週轉率", m.turnover, "pct")}
      </section>
      <section class="card chart-card"><div class="section-head"><div><h2>淨值曲線</h2>
        <p class="muted">策略淨報酬／毛報酬與比較基準 · 起始值標準化為 100</p></div>
        <div class="segmented" role="group" aria-label="淨值尺度"><button class="active" data-scale="linear" aria-pressed="true">線性</button>
          <button data-scale="log" aria-pressed="false">對數</button></div></div><div id="equity-chart" class="chart tall" role="img" aria-label="策略與基準淨值曲線"></div></section>
      <section class="card chart-card"><div class="section-head"><div><h2>回撤</h2>
        <p class="muted">策略與比較基準，相對各自歷史高點</p></div></div>
        <div id="drawdown-chart" class="chart" role="img" aria-label="策略與基準回撤曲線"></div></section>
      <section class="card result-detail"><div class="tabs" role="tablist" aria-label="策略結果內容">
        ${[["performance", "績效"], ["parameters", "參數"], ["trades", "交易"], ["warnings", `資料與警示 (${warningCount})`]]
          .map(([id, text], i) => `<button role="tab" id="tab-${id}" aria-controls="panel-${id}" aria-selected="${i === 0}" tabindex="${i === 0 ? 0 : -1}" data-tab="${id}" class="${i === 0 ? "active" : ""}">${text}</button>`).join("")}</div>
        <div id="panel-performance" class="tab-panel active" role="tabpanel" aria-labelledby="tab-performance">${performancePanel(m, result)}</div>
        <div id="panel-parameters" class="tab-panel" role="tabpanel" aria-labelledby="tab-parameters" hidden>${parametersPanel(request)}</div>
        <div id="panel-trades" class="tab-panel" role="tabpanel" aria-labelledby="tab-trades" hidden><div id="trades-content" class="empty-inline">載入交易明細中…</div></div>
        <div id="panel-warnings" class="tab-panel" role="tabpanel" aria-labelledby="tab-warnings" hidden>${warningsPanel(result, coveragePct)}</div>
      </section>`);
    StrategyCharts.equity("equity-chart", equity, "linear");
    StrategyCharts.drawdown("drawdown-chart", equity);
    document.querySelectorAll("[data-scale]").forEach(button => button.addEventListener("click", () => {
      document.querySelectorAll("[data-scale]").forEach(b => { b.classList.toggle("active", b === button); b.setAttribute("aria-pressed", String(b === button)); });
      StrategyCharts.equity("equity-chart", equity, button.dataset.scale);
    }));
    bindTabs(result.id);
  }

  function kpi(name, value, kind = "num") {
    return `<div class="card kpi"><div class="k">${esc(name)}</div><div class="v ${signClass(value)}">${metric(value, kind)}</div></div>`;
  }

  function performancePanel(m, result) {
    const drag = m.gross_return != null && m.net_return != null ? m.gross_return - m.net_return : null;
    const rows = [
      ["毛報酬", metric(m.gross_return, "pct"), "未扣手續費、稅與滑價"],
      ["淨報酬", metric(m.net_return, "pct"), "已扣除回測成本"],
      ["成本拖累", drag == null ? "—" : `${drag.toFixed(2)} 個百分點`, "毛報酬 − 淨報酬"],
      ["年化波動率", metric(m.annualized_volatility, "pct"), "日對數報酬年化"],
      ["Calmar", metric(m.calmar), "CAGR ÷ |最大回撤|"],
      ["Alpha", metric(m.alpha, "pct"), "相對比較基準，年化"], ["Beta", metric(m.beta), "相對比較基準"],
      ["勝率", metric(m.win_rate, "pct"), "已平倉交易"],
      ["賺賠比（Payoff）", metric(m.payoff_ratio), "平均獲利 ÷ 平均虧損絕對值"],
      ["獲利因子（Profit Factor）", metric(m.profit_factor), "總獲利 ÷ 總虧損絕對值"],
      ["期望值", metric(m.expectancy, "money"), "每筆交易期望淨損益"],
      ["交易筆數", fmt.int(m.number_of_trades), "已平倉交易"],
      ["平均持有期間", metric(m.average_holding_period, "days"), "加權平均"],
      ["回復時間", metric(m.recovery_time, "days"), "已完成回撤的最長恢復時間"],
    ];
    return `<div class="gross-net"><div><span>毛報酬</span><strong>${metric(m.gross_return, "pct")}</strong></div>
      <div class="arrow" aria-hidden="true">→</div><div><span>淨報酬</span><strong>${metric(m.net_return, "pct")}</strong></div>
      <p>成本拖累 ${drag == null ? "—" : `${drag.toFixed(2)} 個百分點`}；兩者來自同一組交易。</p></div>
      <div class="scroll-x"><table class="data metric-table"><thead><tr><th class="l">指標</th><th>數值</th><th class="l">定義</th></tr></thead>
      <tbody>${rows.map(([name, value, note]) => `<tr><td class="l">${esc(name)}</td><td>${esc(value)}</td><td class="l muted">${esc(note)}</td></tr>`).join("")}</tbody></table></div>
      <p class="fin-note">成交模型：${esc(result.metadata?.execution_model || "—")} · 無風險利率：${metric((result.metadata?.config?.risk_free_rate || 0) * 100, "pct")}</p>`;
  }

  function flatten(value, prefix = "") {
    return Object.entries(value || {}).flatMap(([key, item]) => {
      const path = prefix ? `${prefix}.${key}` : key;
      return item && typeof item === "object" && !Array.isArray(item) ? flatten(item, path) : [[path, item]];
    });
  }
  function parametersPanel(request) {
    const rows = [...flatten(request.template_config), ...flatten(request.backtest_config, "backtest")];
    return `<div class="scroll-x"><table class="data"><thead><tr><th class="l">參數</th><th class="l">數值</th></tr></thead><tbody>
      ${rows.map(([path, value]) => `<tr><td class="l">${esc(path.split(".").map(label).join(" / "))}</td>
        <td class="l mono">${esc(Array.isArray(value) ? value.join(", ") || "—" : typeof value === "boolean" ? (value ? "是" : "否") : value)}</td></tr>`).join("")}</tbody></table></div>`;
  }

  function warningsPanel(result, coveragePct) {
    const c = result.coverage || {}, notes = c.notes || [], warnings = result.warnings || [];
    const assumed = result.publication_mode === "assumed";
    const visible = Object.values(c.publications_visible || {}).reduce((sum, sources) => sum + Object.values(sources).reduce((a, b) => a + b, 0), 0);
    return `<div class="warning-summary"><div><span class="severity-dot ${assumed ? "medium" : "low"}"></span>
        <div><strong>${assumed ? "公告模式：假設（Assumed）" : "公告模式：嚴格（Strict）"}</strong>
        <p>${assumed ? "部分基本面資料依名目申報期限推定，不能宣稱完全排除前視偏誤。" : "只使用實際或已觀察到的公告；缺少基本面時會排除，不以假設值補齊。"}</p></div></div>
      <div><span class="severity-dot medium"></span><div><strong>存續偏誤限制</strong>
        <p>沒有完整歷史指數成分；現有股票主檔仍可能帶入存續偏誤。</p></div></div>
      ${notes.map(note => `<div><span class="severity-dot medium"></span><div><strong>覆蓋備註</strong><p>${esc(note)}</p></div></div>`).join("")}</div>
      <h3>資料覆蓋</h3><div class="coverage-grid">
        ${coverageItem("期間", `${c.first_date || "—"} — ${c.last_date || "—"}`)}
        ${coverageItem("交易日", fmt.int(c.trading_sessions))}${coverageItem("有行情股票", `${fmt.int(c.stocks_with_quotes)} / ${fmt.int(c.stocks_requested)} (${coveragePct.toFixed(0)}%)`)}
        ${coverageItem("單檔行情日數", `${fmt.int(c.quote_days_min)} / ${fmt.int(c.quote_days_median)} / ${fmt.int(c.quote_days_max)}（最少 / 中位數 / 最多）`)}
        ${coverageItem("比較基準", `${fmt.int(c.benchmark_days)} 天`)}${coverageItem("可見公告", fmt.int(visible))}
        ${coverageItem("估值", `${fmt.int(c.valuation_observations)} 筆 / ${fmt.int(c.valuation_days)} 天`)}
        ${coverageItem("公司行動", fmt.int(c.corporate_actions))}</div>
      <details class="more warnings-detail"><summary>引擎警示（${warnings.length}）</summary>
        ${warnings.length ? `<ul>${warnings.map(w => `<li>${esc(w)}</li>`).join("")}</ul>` : `<p class="muted">沒有引擎警示。</p>`}
      </details>
      <p class="fin-note">公告模式：${esc(modeLabel(c.publication_mode || result.publication_mode))} · 比較基準：${esc(c.benchmark_id ? label(c.benchmark_id) : "—")}。資料覆蓋是本次回測實際讀到的資料，不代表資料庫已完成五年全量回填。</p>`;
  }
  function coverageItem(name, value) { return `<div><span>${esc(name)}</span><strong>${esc(value)}</strong></div>`; }

  function bindTabs(runId) {
    const tabs = [...document.querySelectorAll("[role='tab']")];
    function activate(button) {
      tabs.forEach(tab => {
        const active = tab === button; tab.classList.toggle("active", active);
        tab.setAttribute("aria-selected", String(active)); tab.tabIndex = active ? 0 : -1;
        const panel = $(`panel-${tab.dataset.tab}`); panel.hidden = !active; panel.classList.toggle("active", active);
      });
      if (button.dataset.tab === "trades" && !button.dataset.loaded) { button.dataset.loaded = "1"; loadTrades(runId, "closed"); }
    }
    tabs.forEach((tab, index) => {
      tab.addEventListener("click", () => activate(tab));
      tab.addEventListener("keydown", event => {
        if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
        event.preventDefault(); const delta = event.key === "ArrowRight" ? 1 : -1;
        const next = tabs[(index + delta + tabs.length) % tabs.length]; activate(next); next.focus();
      });
    });
  }

  async function loadTrades(runId, kind) {
    const target = $("trades-content");
    target.innerHTML = `<div class="empty-inline">載入 ${kind === "closed" ? "已平倉交易" : "成交明細"}…</div>`;
    try {
      const data = await api(`/api/strategy-lab/runs/${runId}/trades?kind=${kind}&limit=500`);
      target.innerHTML = `<div class="section-head"><div><h3>${kind === "closed" ? "已平倉交易" : "成交明細"}</h3>
        <p class="muted">共 ${fmt.int(data.total)} 筆</p></div><div class="segmented" role="group" aria-label="交易類型">
        <button data-kind="closed" class="${kind === "closed" ? "active" : ""}">已平倉</button><button data-kind="fill" class="${kind === "fill" ? "active" : ""}">成交</button></div></div>
        ${data.rows.length ? tradeTable(data.rows, kind) : `<div class="empty-inline">這次回測沒有${kind === "closed" ? "已平倉交易" : "成交紀錄"}。</div>`}`;
      target.querySelectorAll("[data-kind]").forEach(button => button.addEventListener("click", () => loadTrades(runId, button.dataset.kind)));
    } catch (error) { target.innerHTML = `<div class="alert"><strong>交易明細無法載入</strong><div class="d">${esc(error.message)}</div></div>`; }
  }

  function tradeTable(rows, kind) {
    if (kind === "fill") return `<div class="scroll-x"><table class="data"><thead><tr><th class="l">日期</th><th class="l">股票</th><th class="l">方向</th><th>股數</th><th>成交價</th><th>手續費</th><th>稅</th><th class="l">原因</th></tr></thead><tbody>
      ${rows.map(r => `<tr><td class="l">${esc(r.date)}</td><td class="l">${esc(r.stock_id)}</td><td class="l"><span class="side-mark ${esc(r.side)}">${r.side === "buy" ? "買進" : r.side === "sell" ? "賣出" : esc(r.side)}</span></td>
        <td>${fmt.num(r.shares)}</td><td>${fmt.num(r.fill_price)}</td><td>${fmt.num(r.commission)}</td><td>${fmt.num(r.tax)}</td><td class="l">${esc(r.reason)}</td></tr>`).join("")}</tbody></table></div>`;
    return `<div class="scroll-x"><table class="data"><thead><tr><th class="l">股票</th><th class="l">進場</th><th class="l">出場</th><th>股數</th><th>毛損益</th><th>淨損益</th><th>持有天數</th><th class="l">出場原因</th></tr></thead><tbody>
      ${rows.map(r => `<tr><td class="l">${esc(r.stock_id)}</td><td class="l">${esc(r.entry_date)}</td><td class="l">${esc(r.exit_date)}</td>
        <td>${fmt.num(r.shares)}</td><td class="${signClass(r.gross_pnl)}">${fmt.sign(r.gross_pnl)}</td><td class="${signClass(r.net_pnl)}">${fmt.sign(r.net_pnl)}</td>
        <td>${metric(r.holding_days, "days")}</td><td class="l">${esc(r.exit_reason)}</td></tr>`).join("")}</tbody></table></div>`;
  }

  /* 其他兩個研究模組（參數穩健性、樣本外驗證）共用同一份參數名稱對照。 */
  window.StrategyLabLabels = { ...KEY_LABELS, ...LABELS };

  async function start() {
    document.title = `${qs("walkforward") ? "樣本外驗證" : qs("optimization") ? "參數穩健性" : "策略實驗室"} — 台股分析`;
    const strategyId = qs("id"); const runId = qs("run"); const newKey = qs("new");
    if (qs("walkforward")) return window.WalkForwardUI.start(qs("walkforward"), strategyId, qs("source_run"), qs("source_opt"));
    if (qs("optimization")) return window.OptimizationUI.start(qs("optimization"), strategyId, qs("source_run"));
    if (newKey || qs("edit") === "1") return builder(strategyId ? Number(strategyId) : null, newKey);
    if (strategyId) return resultPage(Number(strategyId), runId);
    return home();
  }
  start();
})();
