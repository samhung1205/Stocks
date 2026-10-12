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
    plateau: "Plateau", isolated_peak: "Isolated peak", scattered: "Scattered", flat: "Flat",
    insufficient_data: "Insufficient data", completed: "已完成", running: "執行中", queued: "排隊中",
    failed: "失敗", cancelled: "已取消", evaluated: "Evaluated", no_candidate: "No stable candidate",
  };
  const label = value => LABELS[value] || String(value || "—").replaceAll("_", " ");
  const pathLabel = value => String(value || "—").split(".").map(label).join(" / ");
  const finite = value => value != null && Number.isFinite(Number(value));
  const num = (value, digits = 2, sign = false) => finite(value) ? `${sign && Number(value) > 0 ? "+" : ""}${Number(value).toFixed(digits)}` : "—";
  const pct = (value, digits = 2, sign = false) => finite(value) ? `${sign && Number(value) > 0 ? "+" : ""}${Number(value).toFixed(digits)}%` : "—";
  const shortHash = value => value ? `${String(value).slice(0, 8)}…` : "—";
  const dateTime = value => value ? new Date(value).toLocaleString("zh-TW", { dateStyle: "medium", timeStyle: "short" }) : "—";
  const setRoot = (html, busy = false) => { root.innerHTML = html; root.setAttribute("aria-busy", String(busy)); };
  const loading = (title, detail) => setRoot(`<div class="card state-card"><div class="state-spinner" aria-hidden="true"></div><h1>${esc(title)}</h1><p class="muted">${esc(detail)}</p></div>`, true);
  const errorState = (title, error) => setRoot(`<div class="card state-card state-error"><div class="state-icon" aria-hidden="true">!</div><h1>${esc(title)}</h1><p>${esc(error?.message || error)}</p><a class="btn" href="strategy.html?walkforward=home">返回 Walk Forward</a></div>`);

  function evidenceNotice() {
    return `<div class="wf-evidence-notice" role="note"><strong>Independent folds 是主要 OOS 證據</strong><span>每個 fold 都從現金開始、包含自己的進場成本；stitched OOS 只作次要路徑描述。最多 4 folds，所有跨 fold 摘要僅為 descriptive，不作顯著性或信賴區間宣稱。</span></div>`;
  }

  async function loadContract() {
    if (!state.contracts || !state.plan) [state.contracts, state.plan] = await Promise.all([
      api("/api/strategy-lab/walkforward/contracts"), api("/api/strategy-lab/walkforward/protocol"),
    ]);
  }

  async function home() {
    loading("載入 Walk Forward", "整理獨立 OOS 研究紀錄。");
    try {
      const jobs = await api("/api/strategy-lab/walkforward/jobs?limit=50");
      setRoot(`<header class="lab-page-head"><div><a class="back-link" href="strategy.html">← 策略實驗室</a><div class="eyebrow">OUT-OF-SAMPLE RESEARCH</div><h1>Walk Forward / OOS</h1><p class="muted">檢查樣本內研究結果到了真正 OOS 後是否仍然成立；不做績效排行。</p></div></header>${evidenceNotice()}
        <section class="card lab-section" aria-labelledby="wf-jobs-title"><div class="section-head"><div><h2 id="wf-jobs-title">研究紀錄</h2><p class="muted">依建立時間排列。請從 Strategy Result 或 Optimization Research 建立新的 disclosed trial。</p></div></div>
        ${jobs.length ? jobTable(jobs) : `<div class="empty-state"><h3>尚無 Walk Forward 研究</h3><p>先開啟一個 Strategy Result 或 Optimization Research，再建立 OOS trial。</p></div>`}</section>`);
    } catch (error) { errorState("Walk Forward 無法載入", error); }
  }

  function jobTable(jobs) {
    return `<div class="scroll-x"><table class="data lab-table"><thead><tr><th class="l">Strategy template</th><th class="l">狀態</th><th>Folds</th><th>Evaluated</th><th>No candidate</th><th>建立時間</th><th class="l">動作</th></tr></thead><tbody>${jobs.map(job => `<tr>
      <td class="l">${esc(label(job.template_key))}</td><td class="l"><span class="status-text status-${esc(job.status)}">${esc(label(job.status))}</span></td><td>${job.progress.completed} / ${job.progress.total}</td><td>${job.progress.evaluated}</td><td>${job.progress.no_candidate}</td><td>${dateTime(job.created_at)}</td><td class="l"><a href="strategy.html?walkforward=${encodeURIComponent(job.id)}">開啟研究</a></td></tr>`).join("")}</tbody></table></div>`;
  }

  async function createPage(strategyId, sourceRunId, sourceOptId) {
    loading("準備 Walk Forward", "讀取現有 protocol 與來源研究設定。");
    try {
      await loadContract();
      if (strategyId) state.strategy = await api(`/api/strategy-lab/strategies/${strategyId}`);
      if (sourceRunId) state.sourceRun = await api(`/api/strategy-lab/runs/${sourceRunId}/result`);
      if (sourceOptId) state.sourceOpt = await api(`/api/strategy-lab/optimization/jobs/${sourceOptId}`);
      if (!state.strategy && state.sourceOpt?.provenance?.strategy_id) state.strategy = await api(`/api/strategy-lab/strategies/${state.sourceOpt.provenance.strategy_id}`).catch(() => null);
      if (!state.strategy && !state.sourceOpt) throw new Error("請從 Strategy Result 或 Optimization Research 進入 Walk Forward。");
      renderCreate();
    } catch (error) { errorState("無法建立 Walk Forward 研究", error); }
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
    const sourceName = state.sourceOpt ? `Optimization research ${state.sourceOpt.id.slice(0, 8)}…` : "Strategy Result";
    setRoot(`<header class="lab-page-head compact research-header"><div><a class="back-link" href="${state.sourceOpt ? `strategy.html?optimization=${encodeURIComponent(state.sourceOpt.id)}${state.strategy ? `&id=${state.strategy.id}` : ""}` : `strategy.html?id=${state.strategy.id}${state.sourceRun ? `&run=${state.sourceRun.id}` : ""}`}">← ${esc(sourceName)}</a><div class="eyebrow">策略研究 / OOS 試驗卷宗</div><h1>建立 Walk Forward / OOS</h1><p class="muted">${esc(state.strategy?.name || label(def.templateKey))} · ${state.strategy ? `v${esc(state.strategy.latest_version?.version_no)}` : "模板定義"}</p></div></header>${evidenceNotice()}
      <form id="wf-create-form" class="card optimization-form wf-create-form">
        <div class="section-head"><div><h2>研究協定</h2><p class="muted">預設沿用 F1 validated protocol。建立後，fold 計畫、freeze rule 與 execution policy 會固定在 trial provenance。</p></div></div>
        <dl class="protocol-strip"><div><dt>候選凍結規則</dt><dd>${esc(p.candidate_rule)}</dd></div><div><dt>執行政策</dt><dd>${esc(p.execution_policy)}</dd></div><div><dt>公告日期模式</dt><dd>${esc(def.publication)}</dd></div><div><dt>研究來源</dt><dd>${esc(sourceName)}</dd></div></dl>
        <div class="form-grid">
          <div><label for="wf-mode">視窗模式</label><select id="wf-mode"><option value="expanding" ${p.mode === "expanding" ? "selected" : ""}>Expanding（擴展）</option><option value="rolling">Rolling（滾動）</option></select></div>
          <div><label for="wf-initial">初始訓練長度（月）</label><input id="wf-initial" type="number" min="1" step="1" value="${p.initial_train_months}" required></div>
          <div id="wf-rolling-wrap" hidden><label for="wf-rolling">滾動訓練長度（月）</label><input id="wf-rolling" type="number" min="1" step="1" value="${p.rolling_train_months || p.initial_train_months}"></div>
          <div><label for="wf-test">測試長度（月）</label><input id="wf-test" type="number" min="1" step="1" value="${p.test_months}" required></div>
          <div><label for="wf-step">推進步長（月）</label><input id="wf-step" type="number" min="1" step="1" value="${p.step_months}" required></div>
          <div><label for="wf-final">最後一折保留政策</label><select id="wf-final"><option value="${p.min_final_test_months}">測試期至少 ${p.min_final_test_months} 個月時保留</option></select></div>
          <div><label for="wf-start">研究起始日</label><input id="wf-start" type="date" value="${esc(state.plan.data_start)}" required></div>
          <div><label for="wf-end">研究結束日</label><input id="wf-end" type="date" value="${esc(state.plan.data_end)}" required></div>
        </div>
        <div class="freeze-explainer"><span class="freeze-node" aria-hidden="true"></span><div><strong>Train → Freeze → Test</strong><p>每 fold 只凍結 IS plateau 的 medoid；若沒有穩健候選，該 fold 會在 OOS 持現金，不以 IS peak 補位。</p></div></div>
        <details class="more"><summary>進階設定</summary><div class="form-grid advanced-grid">
          <div><label for="wf-gap">間隔交易日</label><input id="wf-gap" type="number" value="${p.gap_sessions}" disabled><small>F1 OOS contract 固定為 0。</small></div>
          <div><label for="wf-reopt">重新最佳化規則</label><input id="wf-reopt" value="${esc(p.reoptimize)}" disabled></div>
          <div><label for="wf-execution">執行政策</label><input id="wf-execution" value="${esc(p.execution_policy)}" disabled></div>
          <div><label for="wf-freeze">候選凍結規則</label><input id="wf-freeze" value="${esc(p.candidate_rule)}" disabled></div>
          <div><label for="wf-objective">訓練排序目標</label><input id="wf-objective" value="${esc(def.objective)}" disabled></div>
          <div><label for="wf-publication">公告日期模式</label><select id="wf-publication"><option value="assumed" ${def.publication === "assumed" ? "selected" : ""}>Assumed（期限推定）</option>${def.templateKey === "momentum_trend" ? `<option value="strict" ${def.publication === "strict" ? "selected" : ""}>Strict（只用實際公告）</option>` : ""}</select></div>
        </div><p class="fin-note">若來源是 Optimization Research，會沿用其 disclosed search space；本畫面不提供看完 OOS 後直接擴張搜尋範圍的動作。</p></details>
        <div class="builder-actions static"><a class="btn ghost" href="strategy.html?walkforward=home">取消</a><span class="muted">每次建立都是新的 disclosed research trial。</span><button type="submit">建立並執行</button></div>
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
    } catch (error) { toast(`無法建立：${error.message}`, 7000); button.disabled = false; button.textContent = "建立並執行"; }
  }

  async function jobPage(jobId, strategyId) {
    loading("載入 Walk Forward 結果", "讀取 protocol、fold evidence 與資料品質。");
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
    } catch (error) { errorState("Walk Forward 結果無法載入", error); }
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
    setRoot(`<header class="lab-page-head compact"><div><a class="back-link" href="strategy.html?walkforward=home">← Walk Forward</a><div class="eyebrow">OUT-OF-SAMPLE RESEARCH</div><h1>${esc(state.strategy?.name || label(state.job.template_key))}</h1><p class="muted">${esc(label(state.job.status))} · fold ${complete} / ${p.total}</p></div></header>${evidenceNotice()}
      <section class="card run-progress optimization-progress"><div class="state-spinner" aria-hidden="true"></div><div><h2>${state.job.status === "queued" ? "等待建立 fold 計畫" : "執行 Train → Freeze → Test"}</h2><p class="muted">目前 fold ${p.current_fold == null ? "—" : p.current_fold + 1} · ${esc(p.current_stage || "準備中")}。已完成 fold 不會重算。</p>
      <div class="progress-track determinate" role="progressbar" aria-label="Walk Forward progress" aria-valuemin="0" aria-valuemax="${p.total}" aria-valuenow="${complete}"><div style="width:${Math.max(2, p.fraction * 100)}%"></div></div><div class="progress-actions"><button class="danger-quiet" id="wf-cancel" type="button">取消執行</button></div></div></section>`);
    $("wf-cancel").addEventListener("click", async () => { $("wf-cancel").disabled = true; await api(`/api/strategy-lab/walkforward/jobs/${state.job.id}/cancel`, { method: "POST" }); jobPage(state.job.id, state.strategy?.id); });
    setTimeout(() => jobPage(state.job.id, state.strategy?.id), 2000);
  }

  function renderTerminal() {
    setRoot(`<header class="lab-page-head compact"><div><a class="back-link" href="strategy.html?walkforward=home">← Walk Forward</a><h1>${esc(state.strategy?.name || label(state.job.template_key))}</h1></div></header><div class="card state-card state-error"><div class="state-icon" aria-hidden="true">!</div><h2>${esc(label(state.job.status))}</h2><p>${esc(state.job.error || "研究尚未完成。")}</p>${["failed", "cancelled"].includes(state.job.status) ? `<button id="wf-resume" type="button">Resume</button>` : ""}</div>`);
    if ($("wf-resume")) $("wf-resume").addEventListener("click", async () => { $("wf-resume").disabled = true; await api(`/api/strategy-lab/walkforward/jobs/${state.job.id}/resume`, { method: "POST" }); jobPage(state.job.id, state.strategy?.id); });
  }

  function renderResult() {
    const job = state.result.job;
    const first = state.folds[0]?.windows, last = state.folds.at(-1)?.windows;
    setRoot(`<header class="lab-page-head compact research-header"><div><a class="back-link" href="strategy.html?walkforward=home">← Walk Forward</a><div class="eyebrow">Independent OOS / 主要證據 · ${esc(label(job.status))}</div><h1>${esc(state.strategy?.name || label(job.template_key))}</h1><p class="muted">${esc(label(job.template_key))} · 版本 ${esc(job.provenance.strategy_version_no || "—")}</p></div><div class="head-actions"><a class="btn ghost" href="strategy.html?walkforward=new${state.strategy ? `&id=${state.strategy.id}` : ""}">建立揭露後的新試驗</a></div>
      ${researchMetadata([["研究身分", state.strategy?.name || label(job.template_key)], ["版本", `v${job.provenance.strategy_version_no || "—"}`], ["證據型別", "Independent-fold OOS · primary"], ["期間 / as-of", `${first?.train?.[0] || "—"} — ${last?.test?.[1] || "—"}`], ["來源", `Walk Forward ${job.id} · ${job.provenance.candidate_rule}`], ["目前階段", "OOS 驗證"], ["下一步", "逐 fold 檢查 no_candidate、品質與 stitched 限制"]])}</header>
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
      <dl class="context-grid wf-context"><div><dt>Strategy / version</dt><dd>${esc(state.strategy?.name || label(j.template_key))} · v${esc(j.provenance.strategy_version_no || "—")}</dd></div><div><dt>Research window</dt><dd>${esc(first?.train?.[0])} — ${esc(last?.test?.[1])}</dd></div><div><dt>Number of folds</dt><dd>${j.progress.total}</dd></div>
      <div><dt>Train / test / step</dt><dd>${p.mode} · ${p.initial_train_months}M / ${p.test_months}M / ${p.step_months}M</dd></div><div><dt>Publication mode</dt><dd>${esc(j.provenance.publication_mode)}</dd></div><div><dt>Freeze rule</dt><dd>${esc(j.provenance.candidate_rule)}</dd></div><div><dt>Execution policy</dt><dd>${esc(j.provenance.execution_policy)}</dd></div><div><dt>Primary evidence</dt><dd>Independent folds · cash start</dd></div><div><dt>Secondary evidence</dt><dd>Stitched OOS · separate</dd></div></dl>${evidenceNotice()}</section>`;
  }

  function statMedian(key) { return state.result.diagnostics?.fold_dispersion?.independent_fold_metrics?.[key]?.median; }
  function oosSummary() {
    const d = state.result.diagnostics, c = d.completeness;
    const degradation = d.degradation?.sharpe?.delta_oos_minus_is?.median;
    const items = [["Evaluated folds", `${c.evaluated} / ${c.planned_folds}`, "有 frozen primary 並實際進入 OOS"], ["No-candidate folds", `${c.no_candidate} / ${c.planned_folds}`, "有效研究結果；該 fold 持現金"], ["OOS Sharpe", num(statMedian("sharpe")), "evaluated folds median"], ["OOS CAGR", pct(statMedian("cagr")), "evaluated folds median"], ["OOS Max Drawdown", pct(statMedian("max_drawdown")), "evaluated folds median"], ["IS→OOS degradation", num(degradation, 2, true), "median Δ Sharpe（OOS − IS）"]];
    return `<section class="card optimization-section" aria-labelledby="wf-summary-title">${sectionHead("02", "wf-summary-title", "OOS 摘要", "只含 independent-fold evidence；不混入 stitched 指標。")}
      <div class="wf-summary-grid">${items.map(([name, value, note]) => `<div><span>${esc(name)}</span><strong>${esc(value)}</strong><small>${esc(note)}</small></div>`).join("")}</div><p class="fin-note">Descriptive only · ${c.evaluated} evaluated fold(s)；未做 statistical significance 或 confidence claim。</p></section>`;
  }

  function foldTimeline() {
    return `<section class="card optimization-section" aria-labelledby="wf-timeline-title">${sectionHead("03", "wf-timeline-title", "Fold 時間線", "每個 fold 都依序完成 Train → Freeze → Test；no_candidate 是有效結果，不是錯誤。")}
      <div class="wf-timeline">${state.folds.map(f => {
        const no = f.outcome === "no_candidate"; const flags = [
          `<span class="wf-badge ${no ? "cash" : "evaluated"}">${no ? "NO CANDIDATE · CASH" : "EVALUATED"}</span>`,
          f.windows.truncated ? `<span class="wf-badge truncated">TRUNCATED</span>` : "",
          f.plateau_label ? `<span class="wf-badge">${esc(label(f.plateau_label))}</span>` : "",
          f.candidate_flags?.primary_on_edge ? `<span class="wf-badge edge">PRIMARY ON EDGE</span>` : "",
          `<span class="wf-badge severity-${esc(f.severity?.max_severity || "info")}">OOS ${esc(f.severity?.max_severity || "—")}</span>`,
        ].join("");
        return `<article class="wf-fold-card ${no ? "is-cash" : ""}"><div class="wf-fold-head"><div><span>FOLD ${f.index + 1}</span><strong>${esc(f.windows.test[0])} → ${esc(f.windows.test[1])}</strong></div><div class="wf-badges">${flags}</div></div>
          <div class="wf-track"><div class="wf-train"><span>TRAIN</span><strong>${esc(f.windows.train[0])}</strong><small>to ${esc(f.windows.train[1])}</small></div><div class="wf-freeze"><span aria-hidden="true"></span><strong>FREEZE</strong><small>${no ? "no stable candidate" : "medoid locked"}</small></div><div class="wf-test"><span>TEST</span><strong>${no ? "Hold cash" : `First fill ${esc(f.oos?.effective_first_fill_date || "—")}`}</strong><small>${no ? "test data not loaded" : "independent cash start"}</small></div></div>
          ${no ? `<p class="wf-cash-note">本 fold 無穩健候選，OOS 持現金。沒有以 IS peak 或前一 fold 參數補位。</p>` : ""}</article>`;
      }).join("")}</div></section>`;
  }

  const metricDefs = [["sharpe", "Sharpe", "num"], ["cagr", "CAGR", "pct"], ["max_drawdown", "MaxDD", "pct"], ["turnover", "Turnover", "pct"], ["cost_drag_pct", "Cost drag", "pct"]];
  function isOosSection() {
    const evaluated = state.folds.filter(f => f.outcome === "evaluated");
    return `<section class="card optimization-section" aria-labelledby="wf-compare-title">${sectionHead("04", "wf-compare-title", "IS → OOS", "成對比較 frozen primary 的訓練結果與獨立 OOS；Δ 固定為 OOS − IS。")}
      <div class="wf-compare-list">${evaluated.map(f => `<article class="wf-compare-fold"><div class="wf-compare-title"><strong>Fold ${f.index + 1}</strong><span>${esc(f.windows.test.join(" — "))}</span></div><div class="wf-paired-metrics">${metricDefs.map(([key, title, kind]) => {
        const i = f.is_training?.metrics?.[key], o = f.oos_metrics?.[key], delta = finite(i) && finite(o) ? Number(o) - Number(i) : null;
        const format = kind === "pct" ? pct : num;
        return `<div class="wf-pair"><span>${esc(title)}</span><div><b>${format(i)}</b><i aria-hidden="true">→</i><b>${format(o)}</b></div><small>Δ ${format(delta, 2, true)}</small></div>`;
      }).join("")}</div></article>`).join("")}</div>
      ${evaluated.length ? `<div class="scroll-x"><table class="data wf-fallback-table"><caption>IS 與 OOS 數值 fallback</caption><thead><tr><th class="l">Fold</th>${metricDefs.map(([, title]) => `<th>${esc(title)} IS</th><th>${esc(title)} OOS</th><th>Δ</th>`).join("")}</tr></thead><tbody>${evaluated.map(f => `<tr><td class="l">Fold ${f.index + 1}</td>${metricDefs.map(([key,, kind]) => { const i=f.is_training?.metrics?.[key],o=f.oos_metrics?.[key],d=finite(i)&&finite(o)?Number(o)-Number(i):null, format=kind==="pct"?pct:num; return `<td>${format(i)}</td><td>${format(o)}</td><td>${format(d,2,true)}</td>`; }).join("")}</tr>`).join("")}</tbody></table></div>` : `<div class="empty-inline">沒有 evaluated fold 可比較；no-candidate folds 保持現金。</div>`}
      <p class="fin-note">${evaluated.length} evaluated fold(s) · descriptive only。折數不足以支持顯著性、信賴區間或泛化宣稱。</p></section>`;
  }

  function parameterStability() {
    const evaluated = state.folds.filter(f => f.outcome === "evaluated");
    const jac = new Map((state.result.diagnostics.parameter_stability?.region_jaccard_consecutive || []).map(x => [x.folds[1], x.jaccard]));
    let previous = null;
    const rows = state.folds.map(f => {
      if (f.outcome !== "evaluated") return `<tr><td class="l">Fold ${f.index + 1}</td><td class="l muted">No frozen parameters · OOS cash</td><td>—</td><td>—</td><td>—</td><td>${esc(label(f.plateau_label))}</td><td>Primary —<small class="row-note neutral">IS peak ${f.candidate_flags?.is_peak_on_edge ? "on edge" : "inside"}</small></td></tr>`;
      const primaryChanged = previous ? previous.primary.point_key !== f.primary.point_key : null;
      const digestChanged = previous ? previous.candidate_digest !== f.candidate_digest : null;
      const out = `<tr><td class="l">Fold ${f.index + 1}</td><td class="l"><details class="param-disclosure"><summary>${Object.entries(f.primary.params).slice(0,2).map(([k,v]) => `${pathLabel(k)} ${v}`).join(" · ")}${Object.keys(f.primary.params).length > 2 ? " …" : ""}</summary><dl>${Object.entries(f.primary.params).map(([k,v]) => `<div><dt>${esc(pathLabel(k))}</dt><dd>${esc(v)}</dd></div>`).join("")}</dl></details></td><td>${primaryChanged == null ? "Baseline" : primaryChanged ? "Changed" : "Unchanged"}</td><td>${digestChanged == null ? "Baseline" : digestChanged ? "Changed" : "Unchanged"}<small class="row-note neutral">${esc(shortHash(f.candidate_digest))}</small></td><td>${finite(jac.get(f.index)) ? num(jac.get(f.index)) : "—"}</td><td>${esc(label(f.plateau_label))}</td><td>${f.candidate_flags.primary_on_edge ? `Yes${f.primary_edge_axes?.length ? `<small class="row-note">${f.primary_edge_axes.map(pathLabel).map(esc).join(" · ")}</small>` : ""}` : "No"}<small class="row-note neutral">IS peak ${f.candidate_flags.is_peak_on_edge ? "on edge" : "inside"}</small></td></tr>`;
      previous = f; return out;
    }).join("");
    const edgeCount = evaluated.filter(f => f.candidate_flags.primary_on_edge).length;
    return `<section class="card optimization-section" aria-labelledby="wf-stability-title">${sectionHead("05", "wf-stability-title", "參數穩定性", "Frozen medoid、candidate region 與邊界位置按 fold 揭露。")}
      ${edgeCount ? `<div class="edge-alert" role="note"><span aria-hidden="true">!</span><div><strong>目前 default search space 可能受邊界限制</strong><p>${edgeCount} / ${evaluated.length} evaluated folds 的 frozen primary 位於搜尋邊界。此結果頁不提供直接擴大並重跑；若要擴張，必須建立新的 disclosed research trial。</p></div></div>` : ""}
      <div class="scroll-x wf-table-wrap"><table class="data wf-stability-table"><thead><tr><th class="l">Fold</th><th class="l">Frozen medoid parameters</th><th>Primary</th><th>Candidate digest</th><th>Region Jaccard</th><th>Plateau label</th><th>Search edge</th></tr></thead><tbody>${rows}</tbody></table></div></section>`;
  }

  function evidenceSection() {
    return `<section class="card optimization-section wf-evidence-section" aria-labelledby="wf-equity-title">${sectionHead("06", "wf-equity-title", "OOS 淨值證據", "Independent folds 與 stitched OOS 使用不同語意，永不合併成同一 KPI。")}
      <div class="tabs" role="tablist" aria-label="OOS evidence type"><button role="tab" id="wf-tab-independent" aria-controls="wf-panel-independent" aria-selected="true" tabindex="0" data-wf-tab="independent" class="active">Independent OOS · Primary</button><button role="tab" id="wf-tab-stitched" aria-controls="wf-panel-stitched" aria-selected="false" tabindex="-1" data-wf-tab="stitched">Stitched OOS · Secondary</button></div>
      <div id="wf-panel-independent" class="tab-panel active" role="tabpanel" aria-labelledby="wf-tab-independent"><div class="wf-panel-callout primary"><strong>Primary evidence · independent folds</strong><span>每個 fold 都從現金開始並各自支付進場成本。下圖共用「fold 內交易日」座標，只供比較，不是一條連續真實投資曲線。</span></div>
        ${state.foldEquity.length ? `<div id="wf-independent-chart" class="chart wf-equity-chart" role="img" aria-label="Independent fold OOS equity, each fold starts from cash"></div>${independentFallback()}` : `<div class="empty-inline">沒有 evaluated fold equity；no-candidate folds 持現金。</div>`}</div>
      <div id="wf-panel-stitched" class="tab-panel" role="tabpanel" aria-labelledby="wf-tab-stitched" hidden>${stitchedPanel()}</div></section>`;
  }

  function independentFallback() {
    return `<div class="scroll-x"><table class="data"><caption>Independent OOS equity 數值 fallback</caption><thead><tr><th class="l">Fold</th><th class="l">Test window</th><th>Start</th><th>End</th><th>OOS return</th><th class="l">State semantics</th></tr></thead><tbody>${state.folds.map(f => `<tr><td class="l">Fold ${f.index + 1}</td><td class="l">${esc(f.windows.test.join(" — "))}${f.windows.truncated ? " · truncated" : ""}</td><td>${f.outcome === "evaluated" ? "100.00" : "Cash"}</td><td>${f.outcome === "evaluated" ? num(100 * (1 + Number(f.oos_metrics?.net_return || 0) / 100)) : "Cash"}</td><td>${pct(f.oos_metrics?.net_return)}</td><td class="l">${f.outcome === "evaluated" ? `Cash start · first fill ${esc(f.oos?.effective_first_fill_date || "—")}` : "No stable candidate · hold cash"}</td></tr>`).join("")}</tbody></table></div>`;
  }

  function stitchedPanel() {
    if (!state.stitched?.available) return `<div class="wf-panel-callout secondary"><strong>Secondary evidence</strong><span>Stitched OOS 尚不可用：${esc(state.stitched?.reason || "未完成所有 folds")}</span></div>`;
    const liquidation = (state.stitched.segments || []).filter(s => s.outcome === "no_candidate" && (s.fills > 0 || s.inherited_position_count > 0));
    return `<div class="wf-panel-callout secondary"><strong>Secondary evidence · Not independent-fold evidence</strong><span>prior OOS terminal state only · no training replay。這是一條承接前一 fold 實際 OOS 終端狀態的單一 ledger。</span></div>
      ${liquidation.map(s => `<div class="wf-liquidation" role="note"><strong>Fold ${s.fold + 1} boundary · liquidation to cash</strong><span>No stable candidate；${s.inherited_position_count} 個 inherited positions，${s.fills} fills。此後該 fold 持現金。</span></div>`).join("")}
      <dl class="wf-secondary-metrics"><div><dt>Net return</dt><dd>${pct(state.stitched.metrics?.net_return)}</dd></div><div><dt>CAGR</dt><dd>${pct(state.stitched.metrics?.cagr)}</dd></div><div><dt>Sharpe</dt><dd>${num(state.stitched.metrics?.sharpe)}</dd></div><div><dt>MaxDD</dt><dd>${pct(state.stitched.metrics?.max_drawdown)}</dd></div></dl>
      <div id="wf-stitched-chart" class="chart wf-equity-chart" role="img" aria-label="Secondary stitched OOS equity"></div>
      <div class="scroll-x"><table class="data"><caption>Stitched OOS segment fallback</caption><thead><tr><th class="l">Fold</th><th class="l">Window</th><th class="l">Outcome</th><th class="l">State source</th><th>Inherited positions</th><th>Fills</th><th>Segment return</th></tr></thead><tbody>${(state.stitched.segments || []).map(s => `<tr><td class="l">Fold ${s.fold + 1}</td><td class="l">${esc(s.window.join(" — "))}</td><td class="l">${esc(label(s.outcome))}</td><td class="l">${esc(s.state_source)}</td><td>${s.inherited_position_count}</td><td>${s.fills}</td><td>${pct(finite(s.net_return) ? s.net_return * 100 : null)}</td></tr>`).join("")}</tbody></table></div>`;
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
      [state.folds.some(f => (f.oos?.partial_rebalance_count || 0) > 0), "caution", "部分再平衡", "部分標的缺成交價時凍結，其餘部位依 partial policy 執行。"],
      [assumed, "caution", "推定公告日期", "基本面資料可能依法定期限推定公告日；不宣稱完全排除 publication timing 限制。"],
      [state.folds.some(f => f.candidate_flags?.primary_on_edge), "caution", "主要候選位於邊界", "Frozen medoid 位於目前搜尋邊界；default search space 可能受邊界限制。"],
      [state.folds.some(f => f.outcome === "no_candidate"), "info", "no_candidate", "有效研究結果；該 independent OOS fold 從現金開始並持有現金。"],
      [state.folds.some(f => f.windows.truncated), "info", "最後 fold 未滿期", "最後 test window 較短，但依預先揭露的 minimum policy 保留。"],
      [state.folds.some(f => codes(f.severity?.training).includes("EMPTY_SIGNALS")), "caution", "訓練早期持現金 / 空訊號", "只發生在 training coverage；不代表 OOS leakage。"],
    ];
    return `<section class="card optimization-section" aria-labelledby="wf-warning-title">${sectionHead("07", "wf-warning-title", "資料與警示", "Training data quality 與 OOS fold quality 分開閱讀；training critical 不等於策略失效。")}
      <div class="training-quality-note"><strong>Training critical 的語意</strong><p>目前 real validation 的 training critical 幾乎普遍由 unresolved corporate actions 造成，屬資料品質限制，不是策略失效評分。</p></div>
      <div class="quality-grid wf-quality-grid">${items.map(([active, severity, title, text]) => `<div class="quality-item ${active ? `is-active ${severity === "critical" ? "high" : "medium"}` : ""}"><span class="quality-symbol" aria-hidden="true">${active ? "!" : "·"}</span><div><strong>${esc(title)}</strong><p>${esc(text)}</p></div></div>`).join("")}</div>
      <div class="scroll-x wf-table-wrap"><table class="data wf-quality-table"><thead><tr><th class="l">Fold</th><th class="l">Training data quality</th><th class="l">OOS fold quality</th><th>Unresolved OOS</th><th>Partial rebalance</th><th class="l">Coverage note</th></tr></thead><tbody>${state.folds.map(f => {
        const tc = codes(f.severity?.training), oc = codes(f.severity); const coverage = state.trainingCoverage[f.index];
        return `<tr><td class="l">Fold ${f.index + 1}</td><td class="l"><span class="severity-label ${esc(f.severity?.training?.max_severity || "info")}">${esc(f.severity?.training?.max_severity || "—")}</span><small class="row-note neutral">${esc(tc.filter(x => ["UNRESOLVED_ACTIONS_IN_WINDOW","UNRESOLVED_CORPORATE_ACTION","EMPTY_SIGNALS","ASSUMED_PUBLICATION_MODE"].includes(x)).join(" · ") || "No selected signal")}</small></td><td class="l"><span class="severity-label ${esc(f.severity?.max_severity || "info")}">${esc(f.severity?.max_severity || "—")}</span><small class="row-note neutral">${esc(oc.filter(x => ["UNRESOLVED_ACTIONS_IN_WINDOW","PARTIAL_REBALANCE_USED","NO_STABLE_CANDIDATE","PRIMARY_ON_SEARCH_EDGE","PARTIAL_FOLD_TEST_WINDOW"].includes(x)).join(" · ") || "No selected signal")}</small></td><td>${f.oos?.unresolved_action_count ?? 0}</td><td>${f.oos?.partial_rebalance_count ?? 0}</td><td class="l">${tc.includes("EMPTY_SIGNALS") ? `Training early cash${coverage?.first_fill_date ? ` · first usable fill ${esc(coverage.first_fill_date)}` : ""}${coverage?.signals_empty ? ` · ${coverage.signals_empty} empty signals` : ""}. OOS leakage: no.` : f.outcome === "no_candidate" ? "No candidate · OOS held cash" : "—"}</td></tr>`;
      }).join("")}</tbody></table></div>
      <details class="more"><summary>Research provenance & trial disclosure</summary><pre class="contract-view">${esc(JSON.stringify({ protocol_version: state.result.job.provenance.protocol_version, candidate_rule: state.result.job.provenance.candidate_rule, execution_policy: state.result.job.provenance.execution_policy, publication_mode: state.result.job.provenance.publication_mode, trial_disclosure: state.result.job.provenance.trial_disclosure }, null, 2))}</pre></details></section>`;
  }

  async function start(route, strategyId, sourceRunId, sourceOptId) {
    if (route === "home") return home();
    if (route === "new") return createPage(strategyId ? Number(strategyId) : null, sourceRunId, sourceOptId);
    return jobPage(route, strategyId ? Number(strategyId) : null);
  }
  window.WalkForwardUI = { start };
})();
