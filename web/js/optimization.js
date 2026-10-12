/* Strategy Lab Stage E2: Optimization as parameter-robustness research, never a winner picker. */
(function () {
  const root = document.getElementById("lab-root");
  const state = { catalog: null, job: null, robustness: null, results: null, surface: null,
    strategy: null, sourceResult: null, sort: { key: "exploration_rank", direction: "asc" } };
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? "").replace(/[&<>"']/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const LABELS = {
    quality_growth_momentum: "品質 × 成長 × 動能", revenue_growth_acceleration: "營收成長加速",
    momentum_trend: "動能 + 趨勢", value_quality: "價值 × 品質", low_volatility_quality: "低波動 × 品質",
    plateau: "連續候選區域（plateau）", isolated_peak: "孤立尖峰（isolated peak）",
    scattered: "高值區分散（scattered）", flat: "目標無明顯區別（flat）", insufficient_data: "資料不足",
    median: "中位數", mean: "平均", min: "最小值", max: "最大值",
    sharpe: "Sharpe", sortino: "Sortino", calmar: "Calmar", cagr: "CAGR", net_return: "淨報酬",
    total_return: "總報酬", max_drawdown: "最大回撤", annualized_volatility: "年化波動率",
    turnover: "週轉率", cost_drag: "成本拖累", strict: "嚴格（Strict）", assumed: "假設（Assumed）",
  };
  const label = key => LABELS[key] || window.StrategyLabLabels?.[key] || String(key || "—").replaceAll("_", " ");
  const pathLabel = path => String(path || "全部").split(".").map(part => label(part)).join(" / ");
  const num = (value, digits = 2) => value == null || !Number.isFinite(Number(value)) ? "—" : Number(value).toFixed(digits);
  const pct = value => value == null || !Number.isFinite(Number(value)) ? "—" : `${Number(value).toFixed(2)}%`;
  const dateTime = value => value ? new Date(value).toLocaleString("zh-TW", { dateStyle: "medium", timeStyle: "short" }) : "—";
  const statusLabel = value => ({ queued: "排隊中", running: "執行中", completed: "已完成", failed: "失敗", cancelled: "已取消" }[value] || value);
  const setRoot = (html, busy = false) => { root.innerHTML = html; root.setAttribute("aria-busy", String(busy)); };
  const loading = (title, detail) => setRoot(`<div class="card state-card"><div class="state-spinner" aria-hidden="true"></div><h1>${esc(title)}</h1><p class="muted">${esc(detail)}</p></div>`, true);
  const errorState = (title, error) => setRoot(`<div class="card state-card state-error"><div class="state-icon" aria-hidden="true">!</div><h1>${esc(title)}</h1><p>${esc(error?.message || error)}</p><a class="btn" href="strategy.html?optimization=home">返回參數穩健性</a></div>`);

  async function loadCatalog() {
    state.catalog ||= await api("/api/strategy-lab/optimization/search-spaces");
    return state.catalog;
  }

  function notice() {
    return `<div class="research-notice" role="note"><strong>僅限樣本內參數穩健性研究</strong><span>這不是預測、推薦或最適參數結論；候選區域仍需日後獨立驗證。</span></div>`;
  }

  async function home() {
    loading("載入參數穩健性研究", "整理參數穩健性研究紀錄。");
    try {
      const [catalog, jobs] = await Promise.all([loadCatalog(), api("/api/strategy-lab/optimization/jobs?limit=50")]);
      setRoot(`<header class="lab-page-head"><div><a class="back-link" href="strategy.html">← 策略實驗室</a><div class="eyebrow">策略研究 / 樣本內探索</div>
        <h1>參數穩健性</h1><p class="muted">比較參數區域、敏感度與資料品質，不進行單一參數點選拔。</p></div>
        <a class="btn" href="strategy.html?optimization=new">建立新研究</a></header>${notice()}
        <section class="card lab-section"><div class="section-head"><div><h2>研究紀錄</h2><p class="muted">依建立時間顯示，不以績效排名。</p></div></div>
        ${jobs.length ? jobTable(jobs) : `<div class="empty-state"><h3>尚無參數穩健性研究</h3><p>可從策略結果帶入固定版本，或以模板基準建立。</p></div>`}</section>
        <section class="card lab-section"><h2>以模板基準開始</h2><div class="template-grid compact-grid">${Object.keys(catalog.templates).map(key => `<article class="template-card"><h3>${esc(label(key))}</h3><p class="muted">預設 ${catalog.templates[key].default_grid_size} 個參數點</p><a class="btn ghost" href="strategy.html?optimization=new&template=${encodeURIComponent(key)}">建立研究</a></article>`).join("")}</div></section>`);
    } catch (error) { errorState("參數穩健性研究無法載入", error); }
  }

  function jobTable(jobs) {
    return `<div class="scroll-x"><table class="data lab-table"><thead><tr><th class="l">策略模板</th><th class="l">狀態</th><th>探索目標</th><th>進度</th><th>建立時間</th><th class="l">動作</th></tr></thead><tbody>${jobs.map(job => `<tr>
      <td class="l">${esc(label(job.template_key))}</td><td class="l"><span class="status-text status-${esc(job.status)}">${esc(statusLabel(job.status))}</span></td>
      <td>${esc(label(job.objective))}</td><td>${job.progress.completed + job.progress.failed} / ${job.progress.total}</td><td>${dateTime(job.created_at)}</td>
      <td class="l"><a href="strategy.html?optimization=${encodeURIComponent(job.id)}">開啟研究</a></td></tr>`).join("")}</tbody></table></div>`;
  }

  async function createPage(strategyId, sourceRun) {
    loading("準備參數穩健性研究", "載入參數範圍與研究設定。");
    try {
      const catalog = await loadCatalog();
      if (strategyId) state.strategy = await api(`/api/strategy-lab/strategies/${strategyId}`);
      if (sourceRun) state.sourceResult = await api(`/api/strategy-lab/runs/${sourceRun}/result`);
      const requested = qs("template");
      const templateKey = state.strategy?.template_key || requested || Object.keys(catalog.templates)[0];
      renderCreate(templateKey);
    } catch (error) { errorState("無法建立參數穩健性研究", error); }
  }

  function renderCreate(templateKey) {
    const catalog = state.catalog, template = catalog.templates[templateKey], window = catalog.research_window;
    const source = state.sourceResult?.request;
    setRoot(`<header class="lab-page-head compact research-header"><div><a class="back-link" href="strategy.html?optimization=home">← 參數穩健性</a>
      <div class="eyebrow">策略研究 / 穩健性卷宗</div><h1>建立參數穩健性研究</h1><p class="muted">${state.strategy ? `${esc(state.strategy.name)} · v${esc(source?.strategy_version_no || state.strategy.latest_version?.version_no)}` : "模板基準"}</p></div></header>${notice()}
      <form id="optimization-form" class="card optimization-form">
        <div class="section-head"><div><h2>研究設定</h2><p class="muted">設定排序目標與運算預算；目標只用於決定探索順序。</p></div></div>
        <div class="form-grid">
          <div><label for="opt-template">策略模板</label><select id="opt-template" ${state.strategy ? "disabled" : ""}>${Object.keys(catalog.templates).map(key => `<option value="${esc(key)}" ${key === templateKey ? "selected" : ""}>${esc(label(key))}</option>`).join("")}</select></div>
          <div><label for="opt-objective">探索排序目標</label><select id="opt-objective">${Object.entries(catalog.objectives).map(([key, meta]) => `<option value="${esc(key)}">${esc(label(key))} · ${meta.higher_is_better ? "數值較高優先" : "數值較低優先"}</option>`).join("")}</select></div>
          <div><label for="opt-start">研究起始日</label><input id="opt-start" type="date" value="${esc(window.start)}" required></div>
          <div><label for="opt-end">研究結束日</label><input id="opt-end" type="date" value="${esc(window.end)}" required></div>
          <div><label for="opt-max-runs">執行點數上限</label><input id="opt-max-runs" type="number" min="1" max="${template.hard_max_runs}" value="${template.default_max_runs}" required></div>
          <div><label for="opt-policy">預算政策</label><select id="opt-policy"><option value="sample">固定隨機種子取樣</option><option value="reject">超出預算即拒絕</option></select></div>
          <div><label for="opt-seed">隨機種子（seed）</label><input id="opt-seed" type="number" step="1" value="0" required></div>
          <div><label for="opt-publication">公告日期模式</label><select id="opt-publication"><option value="assumed">假設（Assumed，期限推定）</option>${templateKey === "momentum_trend" ? `<option value="strict">嚴格（Strict，只用實際公告）</option>` : ""}</select></div>
        </div>
        <fieldset class="choice-field"><legend>搜尋空間</legend><label class="choice-row"><input type="radio" name="space-mode" value="default" checked><span><strong>預設研究網格</strong><small>${template.default_grid_size} 個結構化參數點</small></span></label>
          <label class="choice-row"><input type="radio" name="space-mode" value="custom"><span><strong>自訂網格</strong><small>只搜尋系統允許的參數軸與權重群組。</small></span></label></fieldset>
        <details class="more" id="advanced-space"><summary>進階搜尋空間設定</summary><p class="fin-note">修改參數軸的值列表或權重群組的步長。超出允許範圍會被拒絕。</p>
          <label for="opt-space">搜尋空間 JSON</label><textarea id="opt-space" class="code-input" rows="14">${esc(JSON.stringify(template.default_space, null, 2))}</textarea>
          <details class="more nested"><summary>可搜尋參數與範圍</summary><pre class="contract-view">${esc(JSON.stringify(template.allowed, null, 2))}</pre></details></details>
        <div class="builder-actions static"><a class="btn ghost" href="strategy.html?optimization=home">取消</a><span class="muted">建立後可取消或續跑；已完成的參數點不重算。</span><button type="submit">建立並執行</button></div>
      </form>`);
    $("opt-template").addEventListener("change", event => renderCreate(event.target.value));
    document.querySelectorAll("[name='space-mode']").forEach(input => input.addEventListener("change", () => {
      if (input.checked && input.value === "custom") $("advanced-space").open = true;
    }));
    $("optimization-form").addEventListener("submit", submitCreate);
  }

  async function submitCreate(event) {
    event.preventDefault(); const button = event.submitter; button.disabled = true; button.textContent = "建立中…";
    try {
      const key = $("opt-template").value;
      const body = { template_key: key, objective: $("opt-objective").value,
        research_start: $("opt-start").value, research_end: $("opt-end").value,
        max_runs: Number($("opt-max-runs").value), budget_policy: $("opt-policy").value,
        seed: Number($("opt-seed").value), publication_mode: $("opt-publication").value };
      const source = state.sourceResult?.request;
      if (source) { body.params = source.template_config; body.backtest = source.backtest_config; body.benchmark_id = source.benchmark_id; }
      if (document.querySelector("[name='space-mode']:checked").value === "custom") body.search_space = JSON.parse($("opt-space").value);
      const queued = await api("/api/strategy-lab/optimization/jobs", { method: "POST", body });
      const strategy = state.strategy ? `&id=${state.strategy.id}` : "";
      location.href = `strategy.html?optimization=${queued.job_id}${strategy}`;
    } catch (error) { toastError(`無法建立：${error.message}`); button.disabled = false; button.textContent = "建立並執行"; }
  }

  async function jobPage(jobId, strategyId) {
    loading("載入參數穩健性結果", "讀取進度、參數表面與資料品質。");
    try {
      state.job = await api(`/api/strategy-lab/optimization/jobs/${jobId}`);
      if (strategyId) state.strategy = await api(`/api/strategy-lab/strategies/${strategyId}`).catch(() => null);
      if (["queued", "running"].includes(state.job.status)) return renderProgress();
      if (state.job.progress.completed) {
        [state.robustness, state.results] = await Promise.all([
          api(`/api/strategy-lab/optimization/jobs/${jobId}/robustness`),
          api(`/api/strategy-lab/optimization/jobs/${jobId}/results?limit=200`),
        ]);
        const axes = surfaceAxes();
        state.surface = await fetchSurface(axes[0], axes[1], "median");
      }
      renderJob();
    } catch (error) { errorState("參數穩健性結果無法載入", error); }
  }

  function renderProgress() {
    const job = state.job, p = job.progress, complete = p.completed + p.failed;
    setRoot(`<header class="lab-page-head compact"><div><a class="back-link" href="strategy.html?optimization=home">← 參數穩健性</a><div class="eyebrow">策略研究 / 參數穩健性</div><h1>${esc(label(job.template_key))}</h1><p class="muted">${esc(statusLabel(job.status))} · ${complete} / ${p.total} 個參數點</p></div></header>${notice()}
      <section class="card run-progress optimization-progress"><div class="state-spinner" aria-hidden="true"></div><div><h2>${job.status === "queued" ? "等待建立研究資料" : "計算參數網格"}</h2>
      <p class="muted">已完成 ${p.completed}，失敗 ${p.failed}，快取 ${p.cached}。進行中的參數點會在算完後才回應取消。</p>
      <div class="progress-track determinate" role="progressbar" aria-label="參數穩健性研究進度" aria-valuemin="0" aria-valuemax="${p.total}" aria-valuenow="${complete}"><div style="width:${Math.max(2, p.fraction * 100)}%"></div></div>
      <div class="progress-actions"><button class="danger-quiet" id="cancel-job" type="button">取消執行</button></div></div></section>`);
    $("cancel-job").addEventListener("click", async () => { $("cancel-job").disabled = true; await api(`/api/strategy-lab/optimization/jobs/${job.id}/cancel`, { method: "POST" }); jobPage(job.id, state.strategy?.id); });
    setTimeout(() => jobPage(job.id, state.strategy?.id), 1800);
  }

  function surfaceAxes() {
    const topology = state.robustness?.topology || {};
    return [...Object.keys(topology.axes || {}), ...Object.entries(topology.groups || {}).flatMap(([group, cfg]) => cfg.keys.map(key => `${group}.${key}`))];
  }
  const fetchSurface = (x, y, agg) => api(`/api/strategy-lab/optimization/jobs/${state.job.id}/surface?x=${encodeURIComponent(x)}${y ? `&y=${encodeURIComponent(y)}` : ""}&agg=${encodeURIComponent(agg)}`);

  function renderJob() {
    const job = state.job;
    if (!job.progress.completed) {
      setRoot(`<header class="lab-page-head compact"><div><a class="back-link" href="strategy.html?optimization=home">← 參數穩健性</a><h1>${esc(label(job.template_key))}</h1></div></header>
        <div class="card state-card state-error"><div class="state-icon" aria-hidden="true">!</div><h2>${esc(statusLabel(job.status))}</h2><p>${esc(job.error || "沒有可顯示的參數點。")}</p>${resumeButton()}</div>`);
      bindResume();
      return;
    }
    const robust = state.robustness, analysis = robust.analysis, plateau = analysis.plateau, coverage = robust.search_coverage;
    const edge = plateau.edge_of_search_range || [];
    setRoot(`<header class="lab-page-head compact research-header"><div><a class="back-link" href="strategy.html?optimization=home">← 參數穩健性</a><div class="eyebrow">樣本內參數穩健性 / ${esc(statusLabel(job.status))}</div>
      <h1>${esc(state.strategy?.name || label(job.template_key))}</h1><p class="muted">${state.strategy ? `策略 v${esc(state.strategy.latest_version?.version_no)}` : "模板研究基準"}</p></div>
      <div class="head-actions">${job.status === "completed" ? `<a class="btn" href="strategy.html?walkforward=new&source_opt=${encodeURIComponent(job.id)}${state.strategy ? `&id=${state.strategy.id}` : ""}">樣本外驗證（OOS）</a>` : ""}${job.status === "cancelled" || job.status === "failed" ? resumeButton() : ""}<a class="btn ghost" href="strategy.html?optimization=new&template=${encodeURIComponent(job.template_key)}">新研究</a></div>
      ${researchMetadata([["研究身分", state.strategy?.name || label(job.template_key)], ["版本", state.strategy ? `v${state.strategy.latest_version?.version_no || "—"}` : "模板基準"], ["證據型別", "樣本內（IS）探索 · 不選最佳參數"], ["研究期間", `${robust.research_window[0]} — ${robust.research_window[1]}`], ["來源", `參數穩健性研究 ${job.id}`], ["目前階段", "穩健區域探索"], ["下一步", "檢查邊界與資料品質，再進入 OOS"]])}</header>
      ${researchContext(robust)}${robustnessSummary(analysis, edge)}${qualitySignals(robust, state.results.rows, edge)}
      ${surfaceSection()}${sensitivitySection(analysis.axis_sensitivity)}${resultsSection()}`);
    StrategyCharts.heatmap("optimization-heatmap", state.surface, pathLabel);
    bindSurfaceControls(); bindSorting(); bindResume();
  }

  function researchContext(robust) {
    const c = robust.search_coverage, job = state.job;
    return `<section class="card optimization-section" aria-labelledby="research-context-title"><div class="section-head"><div><h2 id="research-context-title">研究脈絡</h2></div></div>
      <dl class="context-grid"><div><dt>策略 / 版本</dt><dd>${esc(state.strategy?.name || label(job.template_key))} · ${state.strategy ? `v${esc(state.strategy.latest_version?.version_no)}` : "模板基準"}</dd></div>
      <div><dt>研究期間</dt><dd>${esc(robust.research_window[0])} — ${esc(robust.research_window[1])}</dd></div><div><dt>探索目標</dt><dd>${esc(label(robust.objective.name))} · ${robust.objective.higher_is_better ? "數值較高優先" : "數值較低優先"}</dd></div>
      <div><dt>已評估參數點</dt><dd>${c.runs_completed} / ${c.grid_size} · 網格 ${(c.grid_fraction_evaluated * 100).toFixed(1)}%</dd></div><div><dt>公告模式</dt><dd>${esc(label(robust.publication_mode))}</dd></div>
      <div><dt>預算 / 隨機種子</dt><dd>上限 ${job.search.max_runs} 次 · seed ${job.search.seed}${job.search.sampled ? " · 已取樣" : ""}</dd></div></dl>${notice()}</section>`;
  }

  function robustnessSummary(analysis, edge) {
    const p = analysis.plateau, dispersion = analysis.top_set_dispersion?.metrics?.[state.robustness.objective.metric] || {};
    return `<section class="card optimization-section" aria-labelledby="robustness-title"><div class="section-head"><div><h2 id="robustness-title">穩健性摘要</h2><p class="muted">分類是對目前網格相鄰關係的描述，不是穩定最適參數認證。</p></div></div>
      <div class="robustness-layout"><div class="robustness-label"><span class="robustness-mark" aria-hidden="true"></span><div><span>曲面分類</span><strong>${esc(label(p.label))}</strong></div></div>
      <dl class="robustness-stats"><div><dt>區域大小</dt><dd>${p.region_size} / ${p.upper_set_size}</dd></div><div><dt>目標值分散</dt><dd>${num(analysis.spread, 4)}</dd></div><div><dt>高值集合離散度</dt><dd>IQR ${num(dispersion.iqr, 4)} · σ ${num(dispersion.std, 4)}</dd></div><div><dt>峰值鄰點落差</dt><dd>${p.peak_drop == null ? "—" : pct(p.peak_drop * 100)}</dd></div></dl></div>
      ${edge.length ? `<div class="edge-alert" role="alert"><span aria-hidden="true">!</span><div><strong>候選區域位於目前搜尋邊界，可能需要擴大搜尋範圍</strong><p>邊界參數軸：${edge.map(pathLabel).map(esc).join("、")}。即使分類為連續候選區域（plateau），也不表示已找到穩定最適參數。</p></div></div>` : ""}
      <details class="more"><summary>分類規則與原因</summary><p class="muted">${esc(p.reasons.join(", "))}</p><pre class="contract-view">${esc(JSON.stringify(analysis.rule, null, 2))}</pre></details></section>`;
  }

  function qualitySignals(robust, rows, edge) {
    const unresolved = rows.filter(row => Number(row.unresolved_action_count) > 0).length;
    const empty = rows.filter(row => Number(row.coverage?.signals_empty) > 0);
    const highWarnings = rows.filter(row => Number(row.warnings_count) >= 50);
    const coverage = robust.search_coverage, panel = robust.job_coverage || {};
    const coverageAnomaly = coverage.grid_fraction_evaluated < 1 || coverage.runs_failed > 0 || (panel.notes || []).length || panel.stocks_with_quotes < panel.stocks_requested;
    const firstFills = empty.map(row => row.coverage?.first_fill_date).filter(Boolean).sort();
    const emptyCounts = empty.map(row => row.coverage?.signals_empty).filter(Number.isFinite);
    const items = [
      [edge.length > 0, "medium", "搜尋範圍邊界", edge.length ? `${edge.map(pathLabel).join("、")} 觸及邊界。` : "未觸及可檢查的搜尋邊界。"],
      [empty.length > 0, "medium", "空訊號", empty.length ? `${empty.length} / ${rows.length} 個參數點含空訊號。${firstFills.length ? ` 首次有效成交 ${firstFills[0]}${firstFills.at(-1) !== firstFills[0] ? ` — ${firstFills.at(-1)}` : ""}；空訊號數 ${Math.min(...emptyCounts)}–${Math.max(...emptyCounts)}。` : ""}` : "未偵測到空訊號。"],
      [unresolved > 0, "high", "未解公司行動", `${unresolved} / ${rows.length} 個參數點含未解公司行動；不應視為已修正績效。`],
      [highWarnings.length > 0, "high", "大量警示", `${highWarnings.length} 個參數點有 50 則以上警示；範圍 ${Math.min(...rows.map(r => r.warnings_count || 0))}–${Math.max(...rows.map(r => r.warnings_count || 0))}。`],
      [coverageAnomaly, "medium", "資料涵蓋異常", coverageAnomaly ? `網格 ${(coverage.grid_fraction_evaluated * 100).toFixed(1)}%，失敗 ${coverage.runs_failed}，資料備註 ${(panel.notes || []).length} 則。` : `網格 ${(coverage.grid_fraction_evaluated * 100).toFixed(1)}%，${panel.stocks_with_quotes}/${panel.stocks_requested} 檔股票有行情。`],
    ];
    return `<section class="card optimization-section quality-section" aria-labelledby="quality-title"><div class="section-head"><div><h2 id="quality-title">資料品質訊號</h2><p class="muted">這些訊號會改變結果可比性，不只是技術記錄。</p></div></div><div class="quality-grid">${items.map(([active, severity, title, text]) => `<div class="quality-item ${active ? `is-active ${severity}` : ""}"><span class="quality-symbol" aria-hidden="true">${active ? "!" : "·"}</span><div><strong>${esc(title)}</strong><p>${esc(text)}</p></div></div>`).join("")}</div></section>`;
  }

  function surfaceSection() {
    const axes = surfaceAxes(), surface = state.surface;
    return `<section class="card optimization-section" aria-labelledby="surface-title"><div class="section-head"><div><h2 id="surface-title">參數曲面</h2><p class="muted">色彩只是目標值大小的視覺輔助；格內數字為聚合值。</p></div>
      <div class="surface-controls"><div><label for="surface-x">X 軸</label><select id="surface-x">${axes.map(path => `<option value="${esc(path)}" ${surface.x.path === path ? "selected" : ""}>${esc(pathLabel(path))}</option>`).join("")}</select></div>
      <div><label for="surface-y">Y 軸</label><select id="surface-y">${axes.map(path => `<option value="${esc(path)}" ${surface.y?.path === path ? "selected" : ""}>${esc(pathLabel(path))}</option>`).join("")}</select></div>
      <div><label for="surface-agg">聚合方式</label><select id="surface-agg">${["median", "mean", "min", "max"].map(agg => `<option value="${agg}" ${surface.agg === agg ? "selected" : ""}>${label(agg)}</option>`).join("")}</select></div></div></div>
      <div class="aggregation-note"><strong>聚合其他參數</strong><span>${surface.aggregated_over.length ? surface.aggregated_over.map(pathLabel).map(esc).join(" · ") : "無；每格是完整參數點"}</span></div>
      <div id="optimization-heatmap" class="chart optimization-heatmap" role="img" aria-label="參數曲面熱圖"></div><p class="fin-note">游標停留時顯示樣本數 n、最小值與最大值。完整參數點可於下方結果表用鍵盤排序閱讀。</p></section>`;
  }

  function sensitivitySection(sensitivity) {
    const entries = Object.entries(sensitivity.by_parameter || {}).filter(([, value]) => value.marginal_mean && Object.keys(value.marginal_mean).length > 1)
      .sort((a, b) => (b[1].marginal_range_normalized || 0) - (a[1].marginal_range_normalized || 0));
    const max = Math.max(...entries.map(([, value]) => value.marginal_range_normalized || 0), 0.0001);
    return `<section class="card optimization-section" aria-labelledby="sensitivity-title"><div class="section-head"><div><h2 id="sensitivity-title">敏感度</h2><p class="muted">各參數軸的邊際變化；長度是相對的，並非顯著性檢定。</p></div></div>
      <div class="sensitivity-list">${entries.map(([path, value]) => `<div class="sensitivity-row"><div><strong>${esc(pathLabel(path))}</strong><span>邊際範圍 ÷ 目標值分散 ${pct((value.marginal_range_normalized || 0) * 100)}</span></div><div class="sensitivity-track" aria-hidden="true"><span style="width:${(value.marginal_range_normalized || 0) / max * 100}%"></span></div><details><summary>邊際平均</summary><span class="mono">${Object.entries(value.marginal_mean).map(([k, v]) => `${esc(k)}: ${num(v, 4)}`).join(" · ")}</span></details></div>`).join("")}</div></section>`;
  }

  const COLUMN_DEFS = [
    ["parameters", "參數"], ["exploration_rank", "探索順序"], ["objective_value", "目標值"], ["cagr", "CAGR"], ["sharpe", "Sharpe"], ["sortino", "Sortino"],
    ["max_drawdown", "最大回撤"], ["calmar", "Calmar"], ["net_return", "淨報酬"], ["turnover", "週轉率"], ["cost_drag_pct", "成本拖累"],
    ["unresolved_action_count", "未解公司行動"], ["warnings_count", "警示"],
  ];
  function valueOf(row, key) { return key in row ? row[key] : row.summary?.[key]; }
  function resultsSection() {
    return `<section class="card optimization-section" aria-labelledby="results-title"><div class="section-head"><div><h2 id="results-title">完整結果</h2><p class="muted">全部參數點可排序；探索順序只是依目標值檢視的次序，不是推薦。</p></div></div><div id="results-table">${renderResultsTable()}</div></section>`;
  }
  function renderResultsTable() {
    const { key, direction } = state.sort;
    const rows = [...state.results.rows].sort((a, b) => {
      if (key === "parameters") return JSON.stringify(a.params).localeCompare(JSON.stringify(b.params)) * (direction === "asc" ? 1 : -1);
      const av = valueOf(a, key), bv = valueOf(b, key); if (av == null) return 1; if (bv == null) return -1;
      return (Number(av) - Number(bv)) * (direction === "asc" ? 1 : -1);
    });
    return `<div class="scroll-x results-scroll"><table class="data optimization-table"><thead><tr>${COLUMN_DEFS.map(([field, text]) => `<th class="${field === "parameters" ? "l sticky-col" : ""}" aria-sort="${key === field ? (direction === "asc" ? "ascending" : "descending") : "none"}"><button type="button" data-sort="${field}">${esc(text)}${key === field ? `<span aria-hidden="true"> ${direction === "asc" ? "↑" : "↓"}</span>` : ""}</button></th>`).join("")}</tr></thead><tbody>${rows.map(row => `<tr>
      <td class="l sticky-col"><details class="param-disclosure"><summary>${Object.entries(row.params).slice(0, 2).map(([k, v]) => `${pathLabel(k)} ${v}`).join(" · ")}${Object.keys(row.params).length > 2 ? " …" : ""}</summary><dl>${Object.entries(row.params).map(([k, v]) => `<div><dt>${esc(pathLabel(k))}</dt><dd>${esc(v)}</dd></div>`).join("")}</dl></details></td>
      <td>${row.exploration_rank ?? "—"}</td><td>${num(row.objective_value, 4)}</td><td>${pct(row.summary.cagr)}</td><td>${num(row.summary.sharpe)}</td><td>${num(row.summary.sortino)}</td><td>${pct(row.summary.max_drawdown)}</td><td>${num(row.summary.calmar)}</td><td>${pct(row.summary.net_return)}</td><td>${pct(row.summary.turnover)}</td><td>${pct(row.summary.cost_drag_pct)}</td>
      <td><span class="count-flag ${row.unresolved_action_count ? "active" : ""}">${row.unresolved_action_count ?? "—"}</span></td><td><span class="count-flag ${row.warnings_count >= 50 ? "active" : ""}">${row.warnings_count ?? "—"}</span>${row.coverage?.signals_empty ? `<small class="row-note">空訊號 ${row.coverage.signals_empty} · 首次成交 ${esc(row.coverage.first_fill_date || "—")}</small>` : ""}</td></tr>`).join("")}</tbody></table></div>`;
  }

  function bindSorting() {
    document.querySelectorAll("[data-sort]").forEach(button => button.addEventListener("click", () => {
      const key = button.dataset.sort; state.sort.direction = state.sort.key === key && state.sort.direction === "asc" ? "desc" : "asc"; state.sort.key = key;
      $("results-table").innerHTML = renderResultsTable(); bindSorting();
    }));
  }
  function bindSurfaceControls() {
    ["surface-x", "surface-y", "surface-agg"].forEach(id => $(id).addEventListener("change", async () => {
      let x = $("surface-x").value, y = $("surface-y").value;
      if (x === y) { const fallback = surfaceAxes().find(path => path !== x); if (!fallback) return toast("熱圖需要兩個不同的參數軸"); y = fallback; }
      try { state.surface = await fetchSurface(x, y, $("surface-agg").value); renderJob(); } catch (error) { toastError(`無法更新熱圖：${error.message}`); }
    }));
  }
  function resumeButton() { return `<button class="btn ghost resume-job" type="button">續跑</button>`; }
  function bindResume() { document.querySelectorAll(".resume-job").forEach(button => button.addEventListener("click", async () => { button.disabled = true; await api(`/api/strategy-lab/optimization/jobs/${state.job.id}/resume?retry_failed=true`, { method: "POST" }); jobPage(state.job.id, state.strategy?.id); })); }

  async function start(route, strategyId, sourceRun) {
    if (route === "home") return home();
    if (route === "new") return createPage(strategyId ? Number(strategyId) : null, sourceRun);
    return jobPage(route, strategyId ? Number(strategyId) : null);
  }
  window.OptimizationUI = { start };
})();
