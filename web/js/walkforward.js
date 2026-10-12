/* Strategy Lab Stage F2: Walk Forward / OOS evidence UI.
   Independent folds are primary. Stitched OOS is deliberately isolated as secondary evidence. */
(function () {
  const root = document.getElementById("lab-root");
  const state = { contracts: null, plan: null, strategy: null, sourceRun: null, sourceOpt: null,
    job: null, result: null, folds: [], stitched: null, foldEquity: [], stitchedEquity: [], trainingCoverage: {} };
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? "").replace(/[&<>"']/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const clone = value => JSON.parse(JSON.stringify(value));
  const LABELS = {
    quality_growth_momentum: "品質 × 成長 × 動能", revenue_growth_acceleration: "營收成長加速",
    momentum_trend: "動能 + 趨勢", value_quality: "價值 × 品質", low_volatility_quality: "低波動 × 品質",
    plateau: "連續候選區域（plateau）", isolated_peak: "孤立尖峰", scattered: "高值區分散", flat: "目標無明顯區別",
    insufficient_data: "資料不足", completed: "已完成", running: "執行中", queued: "排隊中",
    failed: "失敗", cancelled: "已取消", evaluated: "已評估", no_candidate: "無穩定候選（no_candidate）",
    strict: "嚴格（Strict）", assumed: "假設（Assumed）", expanding: "擴展", rolling: "滾動",
    critical: "重大", caution: "注意", info: "資訊",
    training: "訓練中", frozen: "已凍結候選", test_opened: "測試期開始", oos_done: "OOS 完成",
    cash: "現金", prior_oos_terminal: "前一個 fold 的 OOS 終端狀態",
  };
  /* 嚴重度代碼對照；查不到的代碼照原樣顯示，方便回頭對照後端紀錄。 */
  const CODE_LABELS = {
    UNRESOLVED_ACTIONS_IN_WINDOW: "期間內有未解公司行動", UNRESOLVED_CORPORATE_ACTION: "未解公司行動",
    EMPTY_SIGNALS: "空訊號", ASSUMED_PUBLICATION_MODE: "推定公告日期", PARTIAL_REBALANCE_USED: "部分再平衡",
    NO_STABLE_CANDIDATE: "無穩定候選", PRIMARY_ON_SEARCH_EDGE: "主要候選位於邊界", PARTIAL_FOLD_TEST_WINDOW: "測試期未滿",
  };
  const codeLabel = code => CODE_LABELS[code] || code;
  const label = value => LABELS[value] || window.StrategyLabLabels?.[value] || String(value || "—").replaceAll("_", " ");
  const severityLabel = value => value ? label(value) : "—";
  const pathLabel = value => String(value || "—").split(".").map(label).join(" / ");
  const finite = value => value != null && Number.isFinite(Number(value));
  const num = (value, digits = 2, sign = false) => finite(value) ? `${sign && Number(value) > 0 ? "+" : ""}${Number(value).toFixed(digits)}` : "—";
  const pct = (value, digits = 2, sign = false) => finite(value) ? `${sign && Number(value) > 0 ? "+" : ""}${Number(value).toFixed(digits)}%` : "—";
  const shortHash = value => value ? `${String(value).slice(0, 8)}…` : "—";
  const dateTime = value => value ? new Date(value).toLocaleString("zh-TW", { dateStyle: "medium", timeStyle: "short" }) : "—";
  const setRoot = (html, busy = false) => { root.innerHTML = html; root.setAttribute("aria-busy", String(busy)); };
  const loading = (title, detail) => setRoot(`<div class="card state-card"><div class="state-spinner" aria-hidden="true"></div><h1>${esc(title)}</h1><p class="muted">${esc(detail)}</p></div>`, true);
  const errorState = (title, error) => setRoot(`<div class="card state-card state-error"><div class="state-icon" aria-hidden="true">!</div><h1>${esc(title)}</h1><p>${esc(error?.message || error)}</p><a class="btn" href="strategy.html?walkforward=home">返回樣本外驗證</a></div>`);

  function evidenceNotice() {
    return `<div class="wf-evidence-notice" role="note"><strong>獨立 fold 是主要的 OOS 證據</strong><span>每個 fold 都從現金開始、包含自己的進場成本；縫合（stitched）OOS 只作次要的路徑描述。最多 4 個 fold，所有跨 fold 摘要僅為敘述統計，不作顯著性或信賴區間宣稱。</span></div>`;
  }

  async function loadContract() {
    if (!state.contracts || !state.plan) [state.contracts, state.plan] = await Promise.all([
      api("/api/strategy-lab/walkforward/contracts"), api("/api/strategy-lab/walkforward/protocol"),
    ]);
  }

  async function home() {
    loading("載入樣本外驗證", "整理獨立 OOS 研究紀錄。");
    try {
      const jobs = await api("/api/strategy-lab/walkforward/jobs?limit=50");
      setRoot(`<header class="lab-page-head"><div><a class="back-link" href="strategy.html">← 策略實驗室</a><div class="eyebrow">策略研究 / 樣本外（OOS）</div><h1>樣本外驗證</h1><p class="muted">檢查樣本內研究結果到了真正的樣本外（OOS）期間是否仍然成立；不做績效排行。</p></div></header>${evidenceNotice()}
        <section class="card lab-section" aria-labelledby="wf-jobs-title"><div class="section-head"><div><h2 id="wf-jobs-title">研究紀錄</h2><p class="muted">依建立時間排列。請從策略回測結果或參數穩健性研究建立新的揭露試驗。</p></div></div>
        ${jobs.length ? jobTable(jobs) : `<div class="empty-state"><h3>尚無樣本外驗證研究</h3><p>先開啟一份策略回測結果或參數穩健性研究，再建立 OOS 試驗。</p></div>`}</section>`);
    } catch (error) { errorState("樣本外驗證無法載入", error); }
  }

  function jobTable(jobs) {
    return `<div class="scroll-x"><table class="data lab-table"><thead><tr><th class="l">策略模板</th><th class="l">狀態</th><th>Fold 進度</th><th>已評估</th><th>無候選</th><th>建立時間</th><th class="l">動作</th></tr></thead><tbody>${jobs.map(job => `<tr>
      <td class="l">${esc(label(job.template_key))}</td><td class="l"><span class="status-text status-${esc(job.status)}">${esc(label(job.status))}</span></td><td>${job.progress.completed} / ${job.progress.total}</td><td>${job.progress.evaluated}</td><td>${job.progress.no_candidate}</td><td>${dateTime(job.created_at)}</td><td class="l"><a href="strategy.html?walkforward=${encodeURIComponent(job.id)}">開啟研究</a></td></tr>`).join("")}</tbody></table></div>`;
  }

  async function createPage(strategyId, sourceRunId, sourceOptId) {
    loading("準備樣本外驗證", "讀取研究協定與來源研究設定。");
    try {
      await loadContract();
      if (strategyId) state.strategy = await api(`/api/strategy-lab/strategies/${strategyId}`);
      if (sourceRunId) state.sourceRun = await api(`/api/strategy-lab/runs/${sourceRunId}/result`);
      if (sourceOptId) state.sourceOpt = await api(`/api/strategy-lab/optimization/jobs/${sourceOptId}`);
      if (!state.strategy && state.sourceOpt?.provenance?.strategy_id) state.strategy = await api(`/api/strategy-lab/strategies/${state.sourceOpt.provenance.strategy_id}`).catch(() => null);
      if (!state.strategy && !state.sourceOpt) throw new Error("請從策略回測結果或參數穩健性研究進入樣本外驗證。");
      renderCreate();
    } catch (error) { errorState("無法建立樣本外驗證研究", error); }
  }

  function sourceDefinition() {
    const fromRun = state.sourceRun?.request || {};
    const fromOpt = state.sourceOpt?.request || {};
    const latest = state.strategy?.latest_version || {};
    return {
      templateKey: state.sourceOpt?.template_key || state.strategy?.template_key,
      params: fromOpt.params ?? fromRun.template_config ?? latest.params ?? {},
      backtest: fromOpt.backtest ?? fromRun.backtest_config ?? latest.backtest ?? {},
      benchmark: fromOpt.benchmark_id ?? fromRun.benchmark_id ?? "TAIEX_TR",
      publication: fromOpt.publication_mode ?? state.sourceRun?.publication_mode ?? "assumed",
      objective: state.sourceOpt?.objective || "sharpe",
      panelStart: fromOpt.panel_start,
      provenance: state.sourceOpt?.provenance,
    };
  }

  function renderCreate() {
    const p = state.plan.protocol, def = sourceDefinition();
    const sourceName = state.sourceOpt ? `參數穩健性研究 ${state.sourceOpt.id.slice(0, 8)}…` : "策略回測結果";
    setRoot(`<header class="lab-page-head compact research-header"><div><a class="back-link" href="${state.sourceOpt ? `strategy.html?optimization=${encodeURIComponent(state.sourceOpt.id)}${state.strategy ? `&id=${state.strategy.id}` : ""}` : `strategy.html?id=${state.strategy.id}${state.sourceRun ? `&run=${state.sourceRun.id}` : ""}`}">← ${esc(sourceName)}</a><div class="eyebrow">策略研究 / OOS 試驗卷宗</div><h1>建立樣本外驗證</h1><p class="muted">${esc(state.strategy?.name || label(def.templateKey))} · ${state.strategy ? `v${esc(state.strategy.latest_version?.version_no)}` : "模板定義"}</p></div></header>${evidenceNotice()}
      <form id="wf-create-form" class="card optimization-form wf-create-form">
        <div class="section-head"><div><h2>研究協定</h2><p class="muted">預設沿用已驗證的 F1 協定。建立後，fold 計畫、凍結規則與執行政策會固定寫入這次試驗的來源紀錄。</p></div></div>
        <dl class="protocol-strip"><div><dt>候選凍結規則</dt><dd>${esc(p.candidate_rule)}</dd></div><div><dt>執行政策</dt><dd>${esc(p.execution_policy)}</dd></div><div><dt>公告日期模式</dt><dd>${esc(label(def.publication))}</dd></div><div><dt>研究來源</dt><dd>${esc(sourceName)}</dd></div></dl>
        <div class="form-grid">
          <div><label for="wf-mode">視窗模式</label><select id="wf-mode"><option value="expanding" ${p.mode === "expanding" ? "selected" : ""}>擴展（Expanding）</option><option value="rolling">滾動（Rolling）</option></select></div>
          <div><label for="wf-initial">初始訓練長度（月）</label><input id="wf-initial" type="number" min="1" step="1" value="${p.initial_train_months}" required></div>
          <div id="wf-rolling-wrap" hidden><label for="wf-rolling">滾動訓練長度（月）</label><input id="wf-rolling" type="number" min="1" step="1" value="${p.rolling_train_months || p.initial_train_months}"></div>
          <div><label for="wf-test">測試長度（月）</label><input id="wf-test" type="number" min="1" step="1" value="${p.test_months}" required></div>
          <div><label for="wf-step">推進步長（月）</label><input id="wf-step" type="number" min="1" step="1" value="${p.step_months}" required></div>
          <div><label for="wf-final">最後一個 fold 的保留政策</label><select id="wf-final"><option value="${p.min_final_test_months}">測試期至少 ${p.min_final_test_months} 個月時保留</option></select></div>
          <div><label for="wf-start">研究起始日</label><input id="wf-start" type="date" value="${esc(state.plan.data_start)}" required></div>
          <div><label for="wf-end">研究結束日</label><input id="wf-end" type="date" value="${esc(state.plan.data_end)}" required></div>
        </div>
        <div class="freeze-explainer"><span class="freeze-node" aria-hidden="true"></span><div><strong>訓練 → 凍結 → 測試</strong><p>每個 fold 只凍結樣本內（IS）連續候選區域的中心點（medoid）；若沒有穩健候選，該 fold 在 OOS 期間持有現金，不以 IS 最高點補位。</p></div></div>
        <details class="more"><summary>進階設定</summary><div class="form-grid advanced-grid">
          <div><label for="wf-gap">間隔交易日</label><input id="wf-gap" type="number" value="${p.gap_sessions}" disabled><small>F1 OOS 協定固定為 0。</small></div>
          <div><label for="wf-reopt">重新最佳化規則</label><input id="wf-reopt" value="${esc(p.reoptimize)}" disabled></div>
          <div><label for="wf-execution">執行政策</label><input id="wf-execution" value="${esc(p.execution_policy)}" disabled></div>
          <div><label for="wf-freeze">候選凍結規則</label><input id="wf-freeze" value="${esc(p.candidate_rule)}" disabled></div>
          <div><label for="wf-objective">訓練排序目標</label><input id="wf-objective" value="${esc(def.objective)}" disabled></div>
          <div><label for="wf-publication">公告日期模式</label><select id="wf-publication"><option value="assumed" ${def.publication === "assumed" ? "selected" : ""}>假設（Assumed，期限推定）</option>${def.templateKey === "momentum_trend" ? `<option value="strict" ${def.publication === "strict" ? "selected" : ""}>嚴格（Strict，只用實際公告）</option>` : ""}</select></div>
        </div><p class="fin-note">若來源是參數穩健性研究，會沿用其已揭露的搜尋空間；本畫面不提供看完 OOS 後直接擴張搜尋範圍的動作。</p></details>
        <div class="builder-actions static"><a class="btn ghost" href="strategy.html?walkforward=home">取消</a><span class="muted">每次建立都是一筆新的揭露試驗。</span><button type="submit">建立並執行</button></div>
      </form>`);
    const mode = $("wf-mode");
    const syncMode = () => { $("wf-rolling-wrap").hidden = mode.value !== "rolling"; };
    mode.addEventListener("change", syncMode); syncMode();
    $("wf-create-form").addEventListener("submit", submitCreate);
  }

  async function submitCreate(event) {
    event.preventDefault();
    const button = event.submitter; button.disabled = true; button.textContent = "建立中…";
    try {
      const def = sourceDefinition();
      const backtest = clone(def.backtest || {}); backtest.missing = { ...(backtest.missing || {}), execution: "partial" };
      const protocol = {
        mode: $("wf-mode").value, initial_train_months: Number($("wf-initial").value),
        rolling_train_months: $("wf-mode").value === "rolling" ? Number($("wf-rolling").value) : null,
        test_months: Number($("wf-test").value), step_months: Number($("wf-step").value),
        min_final_test_months: Number($("wf-final").value), gap_sessions: 0, reoptimize: "each_fold",
        candidate_rule: state.plan.protocol.candidate_rule, execution_policy: "partial",
      };
      const body = { template_key: def.templateKey, params: def.params, backtest, objective: def.objective,
        data_start: $("wf-start").value, data_end: $("wf-end").value,
        publication_mode: $("wf-publication").value, benchmark_id: def.benchmark, protocol };
      if (def.panelStart) body.panel_start = def.panelStart;
      if (state.sourceOpt?.search?.space) body.search_space = state.sourceOpt.search.space;
      const strategyId = state.strategy?.id || def.provenance?.strategy_id;
      const versionId = state.sourceOpt?.provenance?.strategy_version_id || state.strategy?.latest_version?.id;
      if (strategyId && versionId) { body.strategy_id = strategyId; body.strategy_version_id = versionId; }
      const queued = await api("/api/strategy-lab/walkforward/jobs", { method: "POST", body });
      location.href = `strategy.html?walkforward=${queued.job_id}${strategyId ? `&id=${strategyId}` : ""}`;
    } catch (error) { toastError(`無法建立：${error.message}`); button.disabled = false; button.textContent = "建立並執行"; }
  }

  async function jobPage(jobId, strategyId) {
    loading("載入樣本外驗證結果", "讀取研究協定、各 fold 證據與資料品質。");
    try {
      state.job = await api(`/api/strategy-lab/walkforward/jobs/${jobId}`);
      if (strategyId) state.strategy = await api(`/api/strategy-lab/strategies/${strategyId}`).catch(() => null);
      else if (state.job.provenance?.strategy_id) state.strategy = await api(`/api/strategy-lab/strategies/${state.job.provenance.strategy_id}`).catch(() => null);
      if (["queued", "running"].includes(state.job.status)) return renderProgress();
      if (state.job.status !== "completed") return renderTerminal();
      [state.result, state.folds, state.stitched] = await Promise.all([
        api(`/api/strategy-lab/walkforward/jobs/${jobId}/result`),
        api(`/api/strategy-lab/walkforward/jobs/${jobId}/folds`),
        api(`/api/strategy-lab/walkforward/jobs/${jobId}/stitched`),
      ]);
      const evaluated = state.folds.filter(f => f.outcome === "evaluated");
      const equity = await Promise.all(evaluated.map(f => api(`/api/strategy-lab/walkforward/jobs/${jobId}/folds/${f.index}/equity`)));
      state.foldEquity = equity.map(x => ({ index: x.fold_index, points: x.points }));
      state.stitchedEquity = state.stitched.available ? (await api(`/api/strategy-lab/walkforward/jobs/${jobId}/stitched/equity`)).points : [];
      await loadTrainingCoverage();
      renderResult();
    } catch (error) { errorState("樣本外驗證結果無法載入", error); }
  }

  async function loadTrainingCoverage() {
    const targets = state.folds.filter(f => (f.severity?.training?.codes || []).some(x => x.code === "EMPTY_SIGNALS"));
    const rows = await Promise.all(targets.map(async f => {
      try {
        const data = await api(`/api/strategy-lab/optimization/jobs/${f.train_job_id}/results?limit=1`);
        return [f.index, data.rows?.[0]?.coverage || null];
      } catch { return [f.index, null]; }
    }));
    state.trainingCoverage = Object.fromEntries(rows);
  }

  function renderProgress() {
    const p = state.job.progress, complete = p.completed + p.failed;
    setRoot(`<header class="lab-page-head compact"><div><a class="back-link" href="strategy.html?walkforward=home">← 樣本外驗證</a><div class="eyebrow">策略研究 / 樣本外（OOS）</div><h1>${esc(state.strategy?.name || label(state.job.template_key))}</h1><p class="muted">${esc(label(state.job.status))} · fold ${complete} / ${p.total}</p></div></header>${evidenceNotice()}
      <section class="card run-progress optimization-progress"><div class="state-spinner" aria-hidden="true"></div><div><h2>${state.job.status === "queued" ? "等待建立 fold 計畫" : "執行 訓練 → 凍結 → 測試"}</h2><p class="muted">目前 fold ${p.current_fold == null ? "—" : p.current_fold + 1} · ${esc(p.current_stage ? label(p.current_stage) : "準備中")}。已完成 fold 不會重算。</p>
      <div class="progress-track determinate" role="progressbar" aria-label="樣本外驗證進度" aria-valuemin="0" aria-valuemax="${p.total}" aria-valuenow="${complete}"><div style="width:${Math.max(2, p.fraction * 100)}%"></div></div><div class="progress-actions"><button class="danger-quiet" id="wf-cancel" type="button">取消執行</button></div></div></section>`);
    $("wf-cancel").addEventListener("click", async () => { $("wf-cancel").disabled = true; await api(`/api/strategy-lab/walkforward/jobs/${state.job.id}/cancel`, { method: "POST" }); jobPage(state.job.id, state.strategy?.id); });
    setTimeout(() => jobPage(state.job.id, state.strategy?.id), 2000);
  }

  function renderTerminal() {
    setRoot(`<header class="lab-page-head compact"><div><a class="back-link" href="strategy.html?walkforward=home">← 樣本外驗證</a><h1>${esc(state.strategy?.name || label(state.job.template_key))}</h1></div></header><div class="card state-card state-error"><div class="state-icon" aria-hidden="true">!</div><h2>${esc(label(state.job.status))}</h2><p>${esc(state.job.error || "研究尚未完成。")}</p>${["failed", "cancelled"].includes(state.job.status) ? `<button id="wf-resume" type="button">續跑</button>` : ""}</div>`);
    if ($("wf-resume")) $("wf-resume").addEventListener("click", async () => { $("wf-resume").disabled = true; await api(`/api/strategy-lab/walkforward/jobs/${state.job.id}/resume`, { method: "POST" }); jobPage(state.job.id, state.strategy?.id); });
  }

  function renderResult() {
    const job = state.result.job;
    const first = state.folds[0]?.windows, last = state.folds.at(-1)?.windows;
    setRoot(`<header class="lab-page-head compact research-header"><div><a class="back-link" href="strategy.html?walkforward=home">← 樣本外驗證</a><div class="eyebrow">獨立 fold OOS / 主要證據 · ${esc(label(job.status))}</div><h1>${esc(state.strategy?.name || label(job.template_key))}</h1><p class="muted">${esc(label(job.template_key))} · 版本 ${esc(job.provenance.strategy_version_no || "—")}</p></div><div class="head-actions"><a class="btn ghost" href="strategy.html?walkforward=new${state.strategy ? `&id=${state.strategy.id}` : ""}">建立揭露後的新試驗</a></div>
      ${researchMetadata([["研究身分", state.strategy?.name || label(job.template_key)], ["版本", `v${job.provenance.strategy_version_no || "—"}`], ["證據型別", "獨立 fold OOS · 主要證據"], ["研究期間", `${first?.train?.[0] || "—"} — ${last?.test?.[1] || "—"}`], ["來源", `樣本外驗證 ${job.id} · ${job.provenance.candidate_rule}`], ["目前階段", "OOS 驗證"], ["下一步", "逐 fold 檢查無候選、資料品質與縫合曲線的限制"]])}</header>
      ${researchProtocol()}${oosSummary()}${foldTimeline()}${isOosSection()}${parameterStability()}${evidenceSection()}${warningSection()}`);
    if (state.foldEquity.length) StrategyCharts.independentOos("wf-independent-chart", state.foldEquity);
    bindEvidenceTabs();
  }

  function sectionHead(index, id, title, note) {
    return `<div class="section-head"><div><h2 id="${id}">${title}</h2>${note ? `<p class="muted">${note}</p>` : ""}</div></div>`;
  }

  function researchProtocol() {
    const j = state.result.job, p = j.protocol, folds = state.folds;
    const first = folds[0]?.windows, last = folds.at(-1)?.windows;
    return `<section class="card optimization-section" aria-labelledby="wf-protocol-title">${sectionHead("01", "wf-protocol-title", "研究協定", "研究定義與證據層級在結果前先揭露。")}
      <dl class="context-grid wf-context"><div><dt>策略 / 版本</dt><dd>${esc(state.strategy?.name || label(j.template_key))} · v${esc(j.provenance.strategy_version_no || "—")}</dd></div><div><dt>研究期間</dt><dd>${esc(first?.train?.[0])} — ${esc(last?.test?.[1])}</dd></div><div><dt>Fold 數</dt><dd>${j.progress.total}</dd></div>
      <div><dt>訓練 / 測試 / 步長</dt><dd>${esc(label(p.mode))} · ${p.initial_train_months} / ${p.test_months} / ${p.step_months} 個月</dd></div><div><dt>公告模式</dt><dd>${esc(label(j.provenance.publication_mode))}</dd></div><div><dt>凍結規則</dt><dd>${esc(j.provenance.candidate_rule)}</dd></div><div><dt>執行政策</dt><dd>${esc(j.provenance.execution_policy)}</dd></div><div><dt>主要證據</dt><dd>獨立 fold · 各自從現金開始</dd></div><div><dt>次要證據</dt><dd>縫合（stitched）OOS · 分開呈現</dd></div></dl>${evidenceNotice()}</section>`;
  }

  function statMedian(key) { return state.result.diagnostics?.fold_dispersion?.independent_fold_metrics?.[key]?.median; }
  function oosSummary() {
    const d = state.result.diagnostics, c = d.completeness;
    const degradation = d.degradation?.sharpe?.delta_oos_minus_is?.median;
    const items = [["已評估 fold", `${c.evaluated} / ${c.planned_folds}`, "有凍結的主要候選並實際進入 OOS"], ["無候選 fold", `${c.no_candidate} / ${c.planned_folds}`, "有效研究結果；該 fold 持現金"], ["OOS Sharpe", num(statMedian("sharpe")), "已評估 fold 的中位數"], ["OOS CAGR", pct(statMedian("cagr")), "已評估 fold 的中位數"], ["OOS 最大回撤", pct(statMedian("max_drawdown")), "已評估 fold 的中位數"], ["IS→OOS 衰減", num(degradation, 2, true), "Sharpe 差值中位數（OOS − IS）"]];
    return `<section class="card optimization-section" aria-labelledby="wf-summary-title">${sectionHead("02", "wf-summary-title", "OOS 摘要", "只含獨立 fold 證據；不混入縫合（stitched）指標。")}
      <div class="wf-summary-grid">${items.map(([name, value, note]) => `<div><span>${esc(name)}</span><strong>${esc(value)}</strong><small>${esc(note)}</small></div>`).join("")}</div><p class="fin-note">僅為敘述統計 · ${c.evaluated} 個已評估 fold；不作統計顯著性或信賴區間宣稱。</p></section>`;
  }

  function foldTimeline() {
    return `<section class="card optimization-section" aria-labelledby="wf-timeline-title">${sectionHead("03", "wf-timeline-title", "Fold 時間線", "每個 fold 都依序完成 訓練 → 凍結 → 測試；無候選（no_candidate）是有效結果，不是錯誤。")}
      <div class="wf-timeline">${state.folds.map(f => {
        const no = f.outcome === "no_candidate"; const flags = [
          `<span class="wf-badge ${no ? "cash" : "evaluated"}">${no ? "無候選 · 持現金" : "已評估"}</span>`,
          f.windows.truncated ? `<span class="wf-badge truncated">測試期未滿</span>` : "",
          f.plateau_label ? `<span class="wf-badge">${esc(label(f.plateau_label))}</span>` : "",
          f.candidate_flags?.primary_on_edge ? `<span class="wf-badge edge">主要候選在邊界</span>` : "",
          `<span class="wf-badge severity-${esc(f.severity?.max_severity || "info")}">OOS ${esc(severityLabel(f.severity?.max_severity))}</span>`,
        ].join("");
        return `<article class="wf-fold-card ${no ? "is-cash" : ""}"><div class="wf-fold-head"><div><span>FOLD ${f.index + 1}</span><strong>${esc(f.windows.test[0])} → ${esc(f.windows.test[1])}</strong></div><div class="wf-badges">${flags}</div></div>
          <div class="wf-track"><div class="wf-train"><span>訓練</span><strong>${esc(f.windows.train[0])}</strong><small>至 ${esc(f.windows.train[1])}</small></div><div class="wf-freeze"><span aria-hidden="true"></span><strong>凍結</strong><small>${no ? "無穩定候選" : "已鎖定中心點"}</small></div><div class="wf-test"><span>測試</span><strong>${no ? "持有現金" : `首次成交 ${esc(f.oos?.effective_first_fill_date || "—")}`}</strong><small>${no ? "未載入測試期資料" : "獨立從現金開始"}</small></div></div>
          ${no ? `<p class="wf-cash-note">本 fold 無穩健候選，OOS 持現金。沒有以 IS 最高點或前一個 fold 的參數補位。</p>` : ""}</article>`;
      }).join("")}</div></section>`;
  }

  const metricDefs = [["sharpe", "Sharpe", "num"], ["cagr", "CAGR", "pct"], ["max_drawdown", "最大回撤", "pct"], ["turnover", "週轉率", "pct"], ["cost_drag_pct", "成本拖累", "pct"]];
  function isOosSection() {
    const evaluated = state.folds.filter(f => f.outcome === "evaluated");
    return `<section class="card optimization-section" aria-labelledby="wf-compare-title">${sectionHead("04", "wf-compare-title", "IS → OOS", "成對比較凍結主要候選的訓練結果與獨立 OOS 結果；Δ 固定為 OOS − IS。")}
      <div class="wf-compare-list">${evaluated.map(f => `<article class="wf-compare-fold"><div class="wf-compare-title"><strong>Fold ${f.index + 1}</strong><span>${esc(f.windows.test.join(" — "))}</span></div><div class="wf-paired-metrics">${metricDefs.map(([key, title, kind]) => {
        const i = f.is_training?.metrics?.[key], o = f.oos_metrics?.[key], delta = finite(i) && finite(o) ? Number(o) - Number(i) : null;
        const format = kind === "pct" ? pct : num;
        return `<div class="wf-pair"><span>${esc(title)}</span><div><b>${format(i)}</b><i aria-hidden="true">→</i><b>${format(o)}</b></div><small>Δ ${format(delta, 2, true)}</small></div>`;
      }).join("")}</div></article>`).join("")}</div>
      ${evaluated.length ? `<div class="scroll-x"><table class="data wf-fallback-table"><caption>IS 與 OOS 數值對照表</caption><thead><tr><th class="l">Fold</th>${metricDefs.map(([, title]) => `<th>${esc(title)} IS</th><th>${esc(title)} OOS</th><th>Δ</th>`).join("")}</tr></thead><tbody>${evaluated.map(f => `<tr><td class="l">Fold ${f.index + 1}</td>${metricDefs.map(([key,, kind]) => { const i=f.is_training?.metrics?.[key],o=f.oos_metrics?.[key],d=finite(i)&&finite(o)?Number(o)-Number(i):null, format=kind==="pct"?pct:num; return `<td>${format(i)}</td><td>${format(o)}</td><td>${format(d,2,true)}</td>`; }).join("")}</tr>`).join("")}</tbody></table></div>` : `<div class="empty-inline">沒有已評估的 fold 可比較；無候選的 fold 保持現金。</div>`}
      <p class="fin-note">${evaluated.length} 個已評估 fold · 僅為敘述統計。Fold 數不足以支持顯著性、信賴區間或泛化宣稱。</p></section>`;
  }

  function parameterStability() {
    const evaluated = state.folds.filter(f => f.outcome === "evaluated");
    const jac = new Map((state.result.diagnostics.parameter_stability?.region_jaccard_consecutive || []).map(x => [x.folds[1], x.jaccard]));
    let previous = null;
    const rows = state.folds.map(f => {
      if (f.outcome !== "evaluated") return `<tr><td class="l">Fold ${f.index + 1}</td><td class="l muted">無凍結參數 · OOS 持現金</td><td>—</td><td>—</td><td>—</td><td>${esc(label(f.plateau_label))}</td><td>—<small class="row-note neutral">IS 最高點${f.candidate_flags?.is_peak_on_edge ? "在邊界" : "在範圍內"}</small></td></tr>`;
      const primaryChanged = previous ? previous.primary.point_key !== f.primary.point_key : null;
      const digestChanged = previous ? previous.candidate_digest !== f.candidate_digest : null;
      const out = `<tr><td class="l">Fold ${f.index + 1}</td><td class="l"><details class="param-disclosure"><summary>${Object.entries(f.primary.params).slice(0,2).map(([k,v]) => `${pathLabel(k)} ${v}`).join(" · ")}${Object.keys(f.primary.params).length > 2 ? " …" : ""}</summary><dl>${Object.entries(f.primary.params).map(([k,v]) => `<div><dt>${esc(pathLabel(k))}</dt><dd>${esc(v)}</dd></div>`).join("")}</dl></details></td><td>${primaryChanged == null ? "基準" : primaryChanged ? "已改變" : "未改變"}</td><td>${digestChanged == null ? "基準" : digestChanged ? "已改變" : "未改變"}<small class="row-note neutral">${esc(shortHash(f.candidate_digest))}</small></td><td>${finite(jac.get(f.index)) ? num(jac.get(f.index)) : "—"}</td><td>${esc(label(f.plateau_label))}</td><td>${f.candidate_flags.primary_on_edge ? `是${f.primary_edge_axes?.length ? `<small class="row-note">${f.primary_edge_axes.map(pathLabel).map(esc).join(" · ")}</small>` : ""}` : "否"}<small class="row-note neutral">IS 最高點${f.candidate_flags.is_peak_on_edge ? "在邊界" : "在範圍內"}</small></td></tr>`;
      previous = f; return out;
    }).join("");
    const edgeCount = evaluated.filter(f => f.candidate_flags.primary_on_edge).length;
    return `<section class="card optimization-section" aria-labelledby="wf-stability-title">${sectionHead("05", "wf-stability-title", "參數穩定性", "凍結的中心點（medoid）、候選區域與邊界位置按 fold 揭露。")}
      ${edgeCount ? `<div class="edge-alert" role="note"><span aria-hidden="true">!</span><div><strong>目前的預設搜尋空間可能受邊界限制</strong><p>${edgeCount} / ${evaluated.length} 個已評估 fold 的凍結主要候選位於搜尋邊界。此結果頁不提供直接擴大並重跑；若要擴張，必須建立新的揭露試驗。</p></div></div>` : ""}
      <div class="scroll-x wf-table-wrap"><table class="data wf-stability-table"><thead><tr><th class="l">Fold</th><th class="l">凍結的中心點參數</th><th>主要候選</th><th>候選集合指紋</th><th>區域重疊度（Jaccard）</th><th>曲面分類</th><th>位於搜尋邊界</th></tr></thead><tbody>${rows}</tbody></table></div></section>`;
  }

  function evidenceSection() {
    return `<section class="card optimization-section wf-evidence-section" aria-labelledby="wf-equity-title">${sectionHead("06", "wf-equity-title", "OOS 淨值證據", "獨立 fold 與縫合（stitched）OOS 語意不同，永不合併成同一項指標。")}
      <div class="tabs" role="tablist" aria-label="OOS 證據類型"><button role="tab" id="wf-tab-independent" aria-controls="wf-panel-independent" aria-selected="true" tabindex="0" data-wf-tab="independent" class="active">獨立 fold OOS｜主要證據</button><button role="tab" id="wf-tab-stitched" aria-controls="wf-panel-stitched" aria-selected="false" tabindex="-1" data-wf-tab="stitched">縫合 OOS（stitched）｜次要證據</button></div>
      <div id="wf-panel-independent" class="tab-panel active" role="tabpanel" aria-labelledby="wf-tab-independent"><div class="wf-panel-callout primary"><strong>主要證據 · 獨立 fold</strong><span>每個 fold 都從現金開始並各自支付進場成本。下圖共用「fold 內交易日」座標，只供比較，不是一條連續真實投資曲線。</span></div>
        ${state.foldEquity.length ? `<div id="wf-independent-chart" class="chart wf-equity-chart" role="img" aria-label="各獨立 fold 的 OOS 淨值，每個 fold 都從現金開始"></div>${independentFallback()}` : `<div class="empty-inline">沒有已評估 fold 的淨值；無候選的 fold 持有現金。</div>`}</div>
      <div id="wf-panel-stitched" class="tab-panel" role="tabpanel" aria-labelledby="wf-tab-stitched" hidden>${stitchedPanel()}</div></section>`;
  }

  function independentFallback() {
    return `<div class="scroll-x"><table class="data"><caption>獨立 fold OOS 淨值對照表</caption><thead><tr><th class="l">Fold</th><th class="l">測試期間</th><th>起始</th><th>結束</th><th>OOS 報酬</th><th class="l">狀態說明</th></tr></thead><tbody>${state.folds.map(f => `<tr><td class="l">Fold ${f.index + 1}</td><td class="l">${esc(f.windows.test.join(" — "))}${f.windows.truncated ? " · 未滿期" : ""}</td><td>${f.outcome === "evaluated" ? "100.00" : "現金"}</td><td>${f.outcome === "evaluated" ? num(100 * (1 + Number(f.oos_metrics?.net_return || 0) / 100)) : "現金"}</td><td>${pct(f.oos_metrics?.net_return)}</td><td class="l">${f.outcome === "evaluated" ? `從現金開始 · 首次成交 ${esc(f.oos?.effective_first_fill_date || "—")}` : "無穩定候選 · 持有現金"}</td></tr>`).join("")}</tbody></table></div>`;
  }

  function stitchedPanel() {
    if (!state.stitched?.available) return `<div class="wf-panel-callout secondary"><strong>次要證據</strong><span>縫合（stitched）OOS 尚不可用：${esc(state.stitched?.reason || "未完成所有 fold")}</span></div>`;
    const liquidation = (state.stitched.segments || []).filter(s => s.outcome === "no_candidate" && (s.fills > 0 || s.inherited_position_count > 0));
    return `<div class="wf-panel-callout secondary"><strong>次要證據 · 不是獨立 fold 證據</strong><span>只承接前一個 fold 的實際 OOS 終端狀態，不重播訓練期。這是一條連續的單一帳本（ledger）。</span></div>
      ${liquidation.map(s => `<div class="wf-liquidation" role="note"><strong>Fold ${s.fold + 1} 邊界 · 清算為現金</strong><span>無穩定候選；承接 ${s.inherited_position_count} 個部位，成交 ${s.fills} 筆。此後該 fold 持有現金。</span></div>`).join("")}
      <dl class="wf-secondary-metrics"><div><dt>淨報酬</dt><dd>${pct(state.stitched.metrics?.net_return)}</dd></div><div><dt>CAGR</dt><dd>${pct(state.stitched.metrics?.cagr)}</dd></div><div><dt>Sharpe</dt><dd>${num(state.stitched.metrics?.sharpe)}</dd></div><div><dt>最大回撤</dt><dd>${pct(state.stitched.metrics?.max_drawdown)}</dd></div></dl>
      <div id="wf-stitched-chart" class="chart wf-equity-chart" role="img" aria-label="次要證據：縫合 OOS 淨值"></div>
      <div class="scroll-x"><table class="data"><caption>縫合 OOS 各段對照表</caption><thead><tr><th class="l">Fold</th><th class="l">期間</th><th class="l">結果</th><th class="l">狀態來源</th><th>承接部位</th><th>成交筆數</th><th>區段報酬</th></tr></thead><tbody>${(state.stitched.segments || []).map(s => `<tr><td class="l">Fold ${s.fold + 1}</td><td class="l">${esc(s.window.join(" — "))}</td><td class="l">${esc(label(s.outcome))}</td><td class="l">${esc(label(s.state_source))}</td><td>${s.inherited_position_count}</td><td>${s.fills}</td><td>${pct(finite(s.net_return) ? s.net_return * 100 : null)}</td></tr>`).join("")}</tbody></table></div>`;
  }

  function bindEvidenceTabs() {
    const tabs = [...document.querySelectorAll("[data-wf-tab]")];
    const activate = button => {
      tabs.forEach(tab => { const active = tab === button; tab.classList.toggle("active", active); tab.setAttribute("aria-selected", String(active)); tab.tabIndex = active ? 0 : -1; const panel = $(`wf-panel-${tab.dataset.wfTab}`); panel.hidden = !active; panel.classList.toggle("active", active); });
      if (button.dataset.wfTab === "stitched" && state.stitchedEquity.length) StrategyCharts.stitchedOos("wf-stitched-chart", state.stitchedEquity);
    };
    tabs.forEach((tab, index) => { tab.addEventListener("click", () => activate(tab)); tab.addEventListener("keydown", event => { if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return; event.preventDefault(); const next = tabs[(index + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length]; activate(next); next.focus(); }); });
  }

  function codes(block) { return (block?.codes || []).map(x => x.code); }
  function warningSection() {
    const assumed = state.result.job.provenance.publication_mode === "assumed";
    const items = [
      [state.folds.some(f => (f.oos?.unresolved_action_count || 0) > 0), "critical", "未解公司行動", "OOS 計算跨越尚未解明的公司行動；相關 P&L 不應視為已修正。"],
      [state.folds.some(f => (f.oos?.partial_rebalance_count || 0) > 0), "caution", "部分再平衡", "部分標的缺成交價時凍結，其餘部位依部分成交政策執行。"],
      [assumed, "caution", "推定公告日期", "基本面資料可能依法定期限推定公告日；不宣稱完全排除公告時點的限制。"],
      [state.folds.some(f => f.candidate_flags?.primary_on_edge), "caution", "主要候選位於邊界", "凍結的中心點位於目前搜尋邊界；預設搜尋空間可能受邊界限制。"],
      [state.folds.some(f => f.outcome === "no_candidate"), "info", "無候選（no_candidate）", "有效研究結果；該獨立 OOS fold 從現金開始並持有現金。"],
      [state.folds.some(f => f.windows.truncated), "info", "最後 fold 未滿期", "最後一個測試期較短，但依預先揭露的最短期間政策保留。"],
      [state.folds.some(f => codes(f.severity?.training).includes("EMPTY_SIGNALS")), "caution", "訓練早期持現金 / 空訊號", "只發生在訓練期的資料覆蓋；不代表 OOS 資料外洩。"],
    ];
    return `<section class="card optimization-section" aria-labelledby="wf-warning-title">${sectionHead("07", "wf-warning-title", "資料與警示", "訓練期資料品質與 OOS fold 品質分開閱讀；訓練期出現「重大」不等於策略失效。")}
      <div class="training-quality-note"><strong>訓練期「重大」的意思</strong><p>目前實際驗證中的訓練期「重大」幾乎都來自未解公司行動，屬資料品質限制，不是策略失效評分。</p></div>
      <div class="quality-grid wf-quality-grid">${items.map(([active, severity, title, text]) => `<div class="quality-item ${active ? `is-active ${severity === "critical" ? "high" : "medium"}` : ""}"><span class="quality-symbol" aria-hidden="true">${active ? "!" : "·"}</span><div><strong>${esc(title)}</strong><p>${esc(text)}</p></div></div>`).join("")}</div>
      <div class="scroll-x wf-table-wrap"><table class="data wf-quality-table"><thead><tr><th class="l">Fold</th><th class="l">訓練期資料品質</th><th class="l">OOS fold 品質</th><th>OOS 未解公司行動</th><th>部分再平衡</th><th class="l">覆蓋備註</th></tr></thead><tbody>${state.folds.map(f => {
        const tc = codes(f.severity?.training), oc = codes(f.severity); const coverage = state.trainingCoverage[f.index];
        return `<tr><td class="l">Fold ${f.index + 1}</td><td class="l"><span class="severity-label ${esc(f.severity?.training?.max_severity || "info")}">${esc(severityLabel(f.severity?.training?.max_severity))}</span><small class="row-note neutral">${esc(tc.filter(x => ["UNRESOLVED_ACTIONS_IN_WINDOW","UNRESOLVED_CORPORATE_ACTION","EMPTY_SIGNALS","ASSUMED_PUBLICATION_MODE"].includes(x)).map(codeLabel).join(" · ") || "無相關訊號")}</small></td><td class="l"><span class="severity-label ${esc(f.severity?.max_severity || "info")}">${esc(severityLabel(f.severity?.max_severity))}</span><small class="row-note neutral">${esc(oc.filter(x => ["UNRESOLVED_ACTIONS_IN_WINDOW","PARTIAL_REBALANCE_USED","NO_STABLE_CANDIDATE","PRIMARY_ON_SEARCH_EDGE","PARTIAL_FOLD_TEST_WINDOW"].includes(x)).map(codeLabel).join(" · ") || "無相關訊號")}</small></td><td>${f.oos?.unresolved_action_count ?? 0}</td><td>${f.oos?.partial_rebalance_count ?? 0}</td><td class="l">${tc.includes("EMPTY_SIGNALS") ? `訓練早期持現金${coverage?.first_fill_date ? ` · 首次可用成交 ${esc(coverage.first_fill_date)}` : ""}${coverage?.signals_empty ? ` · 空訊號 ${coverage.signals_empty} 次` : ""}。OOS 資料外洩：無。` : f.outcome === "no_candidate" ? "無候選 · OOS 持有現金" : "—"}</td></tr>`;
      }).join("")}</tbody></table></div>
      <details class="more"><summary>研究來源與試驗揭露</summary><pre class="contract-view">${esc(JSON.stringify({ protocol_version: state.result.job.provenance.protocol_version, candidate_rule: state.result.job.provenance.candidate_rule, execution_policy: state.result.job.provenance.execution_policy, publication_mode: state.result.job.provenance.publication_mode, trial_disclosure: state.result.job.provenance.trial_disclosure }, null, 2))}</pre></details></section>`;
  }

  async function start(route, strategyId, sourceRunId, sourceOptId) {
    if (route === "home") return home();
    if (route === "new") return createPage(strategyId ? Number(strategyId) : null, sourceRunId, sourceOptId);
    return jobPage(route, strategyId ? Number(strategyId) : null);
  }
  window.WalkForwardUI = { start };
})();
