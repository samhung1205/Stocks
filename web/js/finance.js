/* 財務規劃頁：輸入 → 呼叫後端試算 → 渲染結果。
   所有財務公式都在後端（app/services/finance/），這裡完全不算數字，
   只負責收集輸入、丟給 /api/finance/simulate、把回來的結果畫出來。 */

let U = null;         // 名單/問卷/預設值
let P = null;         // 目前情境
let R = null;         // 最近一次試算結果
let step = 1;
let portfolioCatalog = [];
let sourceEvidence = null;
let relinkKey = null;
const charts = {};
const MAX_STEP = 7;

/* 角色與曝險配色：刻意不用紅綠——財務規劃沒有「漲跌」的語意 */
const RESEARCH_COLOR = {
  indigo: CHART_THEME.ACCENT, slate: "#70798a", teal: "#4f7474",
  ochre: "#8a6a32", graphite: "#5f626a", soft: "#8a8e97",
};
const ROLE_COLOR = {
  core_tw: RESEARCH_COLOR.indigo, core_us: RESEARCH_COLOR.slate,
  growth: "#6d7085", income: RESEARCH_COLOR.ochre,
  defense: RESEARCH_COLOR.teal, cash: RESEARCH_COLOR.soft,
};
const ASSET_COLOR = { equity: RESEARCH_COLOR.indigo, bond: RESEARCH_COLOR.teal, cash: RESEARCH_COLOR.soft };
const REGION_COLOR = { TW: RESEARCH_COLOR.indigo, US: RESEARCH_COLOR.slate, "-": RESEARCH_COLOR.soft };

const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>"]/g, c =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

/* ── 表單 ↔ profile 物件 ─────────────────────────────── */
/* id 命名：f-<欄位> → profile、f-a-<欄位> → assumptions、f-r-<欄位> → retirement */
function targetOf(id) {
  if (id.startsWith("f-a-")) return [P.assumptions, id.slice(4)];
  if (id.startsWith("f-r-")) return [P.retirement, id.slice(4)];
  return [P, id.slice(2)];
}

function readForm() {
  document.querySelectorAll("[id^='f-']").forEach(el => {
    const [obj, key] = targetOf(el.id);
    if (el.type === "checkbox") {
      obj[key] = key === "need_income" ? (el.checked ? 1 : 0) : el.checked;
    } else if (el.type === "number") {
      obj[key] = el.value === "" ? null : Number(el.value);
    } else {
      obj[key] = el.value || null;
    }
  });
  P.risk_answers = {};
  document.querySelectorAll("#risk-questions input:checked").forEach(el => {
    P.risk_answers[el.name.slice(2)] = el.value;
  });
  const etfs = [...document.querySelectorAll("#etf-list .fin-etf")].map(row => {
    const sid = row.dataset.sid;
    const num = sel => {
      const v = row.querySelector(sel).value;
      return v === "" ? null : Number(v);
    };
    return {
      stock_id: sid,
      enabled: row.querySelector(".en").checked ? 1 : 0,
      weight: num(".w"),
      expected_return: num(".er"),
      actual_value: num(".av"),
    };
  });
  const others = [...document.querySelectorAll("#other-source-list .fin-source-row")].map(row => {
    const existing = P.targets.find(t => t.stock_id === row.dataset.sid);
    const val = sel => { const v = row.querySelector(sel).value; return v === "" ? null : Number(v); };
    return {...existing, enabled: row.querySelector(".en").checked ? 1 : 0,
      weight: val(".w"), actual_value: val(".av"),
      expected_return: existing.source_type === "manual" ? val(".er") : existing.expected_return};
  });
  P.targets = [...etfs, ...others];
}

function fillForm() {
  document.querySelectorAll("[id^='f-']").forEach(el => {
    const [obj, key] = targetOf(el.id);
    const v = obj?.[key];
    if (el.type === "checkbox") el.checked = !!v;
    else el.value = v === null || v === undefined ? "" : v;
  });
  renderQuestions();
  renderEtfList();
  renderOtherSources();
}

/* ── 靜態區塊渲染 ────────────────────────────────────── */
function renderSteps() {
  const labels = ["個人資料", "財務狀況", "投資金額", "風險問卷",
                  "投資組合", "試算假設", "退休設定"];
  $("steps").innerHTML = labels.map((l, i) =>
    `<button type="button" data-go="${i + 1}"><span class="n">${i + 1}</span>${l}</button>`).join("");
  $("steps").querySelectorAll("button").forEach(b =>
    b.addEventListener("click", () => goStep(Number(b.dataset.go))));
}

function goStep(n) {
  step = Math.max(1, Math.min(MAX_STEP, n));
  document.querySelectorAll(".fin-step").forEach(el =>
    el.classList.toggle("active", Number(el.dataset.step) === step));
  $("steps").querySelectorAll("button").forEach(b =>
    { const active = Number(b.dataset.go) === step; b.classList.toggle("active", active); b.setAttribute("aria-current", active ? "step" : "false"); });
  $("btn-prev").disabled = step === 1;
  $("btn-next").disabled = step === MAX_STEP;
}

function renderQuestions() {
  $("risk-questions").innerHTML = U.questions.map(q => `
    <div class="fin-q">
      <div class="qt">${esc(q.text)}</div>
      <div class="fin-opts">${q.options.map(o => `
        <label><input type="radio" name="q-${q.key}" value="${o.value}"
          ${P.risk_answers?.[q.key] === o.value ? "checked" : ""}>${esc(o.label)}</label>`).join("")}
      </div>
    </div>`).join("");
  $("risk-questions").querySelectorAll("input").forEach(el =>
    el.addEventListener("change", recalc));
}

function etfMeta(sid) {
  return U.items.find(i => i.stock_id === sid) || { stock_id: sid, name: sid };
}

function histText(h) {
  if (!h || !h.available) return `<span class="muted">歷史：${esc(h?.reason || "無資料")}</span>`;
  const split = (h.split_events || []).length
    ? `<br><span class="muted">已還原分割：${h.split_events.map(e =>
        `${e.date}（1 股換 ${Math.round(1 / e.ratio)} 股）`).join("、")}</span>`
    : "";
  return `<span class="hist">歷史含息年化 ${h.cagr_total}%</span>` +
    `<span class="muted">（近 ${h.window_years} 年，${h.start}～${h.end}；` +
    `年化波動 ${h.volatility ?? "—"}%，期間最大回撤 ${h.max_drawdown}%）</span>` + split;
}

function renderEtfList() {
  const byId = Object.fromEntries((P.targets || []).filter(t => !t.source_type || t.source_type === "etf").map(t => [t.stock_id, t]));
  const ids = [...new Set([...(P.targets || []).filter(t => !t.source_type || t.source_type === "etf").map(t => t.stock_id),
                           ...U.items.map(i => i.stock_id)])];
  $("etf-list").innerHTML = ids.map(sid => {
    const m = etfMeta(sid);
    const t = byId[sid] || { enabled: 0 };
    const on = !!t.enabled;
    const fieldId = `etf-${String(sid).replace(/[^A-Za-z0-9_-]/g, "-")}`;
    return `
      <div class="fin-etf ${on ? "" : "off"}" data-sid="${esc(sid)}">
        <div class="top">
          <input type="checkbox" class="en" aria-label="${esc(sid)} 啟用" ${on ? "checked" : ""}>
          <span class="sid">${esc(sid)}</span>
          <span>${esc(m.name || "")}</span>
          <span class="fin-pill">${esc(m.role_label || "")}</span>
        </div>
        <div class="fields">
          <div><label for="${fieldId}-weight">目標權重 %</label><input id="${fieldId}-weight" type="number" class="w" step="0.1" aria-label="${esc(sid)} 目標權重 %"
            value="${t.weight ?? ""}"></div>
          <div><label for="${fieldId}-return">假設報酬 %</label><input id="${fieldId}-return" type="number" class="er" step="0.1" aria-label="${esc(sid)} 假設報酬 %"
            placeholder="${m.default_return ?? ""}" value="${t.expected_return ?? ""}"></div>
          <div><label for="${fieldId}-value">目前市值</label><input id="${fieldId}-value" type="number" class="av" aria-label="${esc(sid)} 目前市值"
            placeholder="日誌帶入" value="${t.actual_value ?? ""}"></div>
        </div>
        <div class="meta">${esc(m.asset_label || "")}·${esc(m.region_label || "")}
          ·內扣約 ${m.fee ?? "—"}%｜${esc(m.note || "")}<br>${histText(m.history)}</div>
      </div>`;
  }).join("");
  $("etf-list").querySelectorAll("input").forEach(el => {
    el.addEventListener("input", recalc);
    el.addEventListener("change", recalc);
  });
}

const percent = (v, fraction = false) => v == null || !Number.isFinite(Number(v)) ? "—" : `${(Number(v) * (fraction ? 100 : 1)).toFixed(2)}%`;
const metric = (label, value, isPct = false, fraction = false) => value == null ? "" :
  `<div><dt>${label}</dt><dd>${isPct ? percent(value, fraction) : esc(value)}</dd></div>`;

function renderEvidence(ev, where) {
  const research = ev?.research;
  if (!ev) { where.innerHTML = '<p class="fin-hint">選擇固定版本後可檢視研究證據。</p>'; return; }
  const archive = ev.archived ? '<p class="fin-archive">來源已封存；固定版本與既有研究仍可讀取。</p>' : "";
  if (!research?.available) {
    where.innerHTML = `<h3>研究證據</h3>${archive}<p class="fin-hint">未選擇已完成的研究紀錄；目前沒有研究數值可顯示。這是正常狀態。</p><p class="fin-hint">Historical / OOS ≠ forecast；歷史與樣本外證據不是未來報酬預測。</p>`;
    return;
  }
  const m = research.metrics || {}, cash = research.cash || {}, turn = research.turnover || {};
  const w = research.window || {};
  const warningRows = research.warnings || [];
  const warnings = warningRows.map(x => {
    const detail = x.reason || x.message || x.detail || Object.entries(x)
      .filter(([k]) => !["code", "level", "date"].includes(k))
      .map(([k, v]) => `${k}: ${typeof v === "object" ? JSON.stringify(v) : v}`).join("；");
    return `<li>${esc(x.code || x.level || "限制")}${x.date ? ` · ${esc(x.date)}` : ""}${detail ? `：${esc(detail)}` : ""}</li>`;
  }).join("");
  const warningCounts = Object.entries(warningRows.reduce((out, x) => {
    const key = x.code || x.level || "限制"; out[key] = (out[key] || 0) + 1; return out;
  }, {})).map(([code, count]) => `${esc(code)} × ${count}`).join(" · ");
  where.innerHTML = `<h3>研究證據</h3>${archive}
    <p class="fin-evidence-label">Historical / OOS ≠ forecast；歷史與樣本外證據不是未來報酬預測。</p>
    <p class="fin-hint">研究期間 ${esc(w.start || "—")} ～ ${esc(w.end || "—")} · Run ${esc(ev.research_run_id)}</p>
    <dl class="fin-evidence-metrics">${metric("CAGR", m.cagr, true)}${metric("Sharpe", m.sharpe == null ? null : Number(m.sharpe).toFixed(2))}${metric("Max Drawdown", m.max_drawdown, true)}${metric("Total Return", m.total_return, true)}${metric("Annualized volatility", m.annualized_volatility, true)}${metric("平均內部現金", cash.avg_cash_weight, true, true)}${metric("期末內部現金", cash.last_cash_weight, true, true)}${metric("Turnover", turn.turnover ?? m.turnover, true)}${metric("Cost drag", turn.cost_drag_pct, true)}</dl>
    <p class="fin-hint">組合內部現金是研究證據，不計入 Planner 個人現金。</p>
    ${warnings ? `<div class="fin-evidence-warnings"><strong>警示與限制 · ${warningRows.length}</strong><p class="fin-hint">${warningCounts}</p><details><summary>展開原始限制明細</summary><ul class="fin-hint">${warnings}</ul></details></div>` : ""}`;
}

function renderOtherSources() {
  $("other-source-list").innerHTML = (P?.targets || []).filter(t => t.source_type === "manual" || t.source_type === "strategy_portfolio").map(t => {
    const portfolio = t.source_type === "strategy_portfolio";
    const id = esc(t.stock_id);
    const fieldId = `source-${String(t.stock_id).replace(/[^A-Za-z0-9_-]/g, "-")}`;
    return `<div class="fin-etf fin-source-row ${t.enabled ? "" : "off"}" data-sid="${id}">
      <div class="top"><input class="en" type="checkbox" aria-label="${id} 啟用" ${t.enabled ? "checked" : ""}>
        <strong>${portfolio ? "Strategy Portfolio" : "Manual / 自訂"}</strong><span class="source-name">${portfolio ? esc(portfolioCatalog.find(p => p.id === t.strategy_portfolio_id)?.name || t.stock_id) : id}</span></div>
      ${portfolio ? `<p class="fin-hint">Pinned <span class="version-label">version ID ${esc(t.portfolio_version_id)}</span> · ${t.research_run_id ? `Run ${esc(t.research_run_id)}` : "未選 Research Run"} · assumption source: ${esc(t.assumption_source || "未設定")}</p>
        <p class="fin-newer" hidden>Newer version available · 目前仍固定在舊版本。</p>
        <button type="button" class="ghost small relink" data-key="${id}">Relink / Update Source</button>
        <div class="fin-evidence row-evidence"><p class="fin-hint">正在載入研究證據…</p></div>
        <p class="fin-hint">Equity / TW 是 Planner 的 reporting approximation，不代表研究組合的實際持倉或限制。</p>` : `<p class="fin-hint">自訂／未分類；不套用 ETF role 或台股核心資產分類。</p>`}
      <div class="fields"><div><label for="${fieldId}-weight">目標權重 %</label><input id="${fieldId}-weight" class="w" type="number" step="0.1" aria-label="${id} 目標權重 %" value="${t.weight ?? ""}"></div>
        <div><label for="${fieldId}-return">Planning return %</label><input id="${fieldId}-return" class="er" type="number" step="0.1" aria-label="${id} Planning return %" value="${t.expected_return ?? ""}" ${portfolio ? "readonly title=\"請透過 Relink 確認假設\"" : ""}></div>
        <div><label for="${fieldId}-value">目前市值（手動）</label><input id="${fieldId}-value" class="av" type="number" aria-label="${id} 目前市值（手動）" value="${t.actual_value ?? ""}" placeholder="可留空"></div></div>
      ${portfolio ? '<p class="fin-hint">若 Step 2 已含這筆市值，請避免重複輸入。</p>' : ""}
      <button type="button" class="ghost small remove-source" data-key="${id}">移除來源</button></div>`;
  }).join("");
  $("other-source-list").querySelectorAll("input").forEach(el => { el.addEventListener("input", recalc); el.addEventListener("change", recalc); });
  $("other-source-list").querySelectorAll(".remove-source").forEach(b => b.onclick = () => {
    readForm(); P.targets = P.targets.filter(t => t.stock_id !== b.dataset.key); renderOtherSources(); recalc();
  });
  $("other-source-list").querySelectorAll(".relink").forEach(b => b.onclick = () => beginRelink(b.dataset.key));
  $("other-source-list").querySelectorAll(".fin-source-row").forEach(row => {
    const t = P.targets.find(x => x.stock_id === row.dataset.sid);
    if (t?.source_type !== "strategy_portfolio") return;
    const p = portfolioCatalog.find(x => x.id === t.strategy_portfolio_id);
    if (p?.latest_version && p.latest_version.id !== t.portfolio_version_id) row.querySelector(".fin-newer").hidden = false;
    if (p?.latest_version?.id === t.portfolio_version_id) row.querySelector(".version-label").textContent = `v${p.latest_version.version_no} (ID ${t.portfolio_version_id})`;
    api(`/api/finance/portfolio-evidence?strategy_portfolio_id=${t.strategy_portfolio_id}&portfolio_version_id=${t.portfolio_version_id}${t.research_run_id ? `&research_run_id=${encodeURIComponent(t.research_run_id)}` : ""}`)
      .then(ev => { row.querySelector(".version-label").textContent = `v${ev.version_no} (ID ${ev.portfolio_version_id})`; renderEvidence(ev, row.querySelector(".row-evidence")); })
      .catch(err => { row.querySelector(".row-evidence").textContent = `研究證據無法載入：${err.message}`; });
  });
}

function sourceError(message) {
  const box = $("source-error"); box.textContent = message; box.hidden = false; box.focus();
}
function clearSourceError() { $("source-error").hidden = true; }
function sourceUrl(portfolioId, versionId, runId) {
  const q = new URLSearchParams({strategy_portfolio_id: portfolioId, portfolio_version_id: versionId});
  if (runId) q.set("research_run_id", runId);
  return `/api/finance/portfolio-evidence?${q}`;
}
async function loadPortfolios() {
  portfolioCatalog = await api("/api/strategy-lab/portfolios?include_archived=true");
  renderPortfolioOptions();
  renderOtherSources();
}
function renderPortfolioOptions(term = "") {
  const select = $("source-portfolio"), current = select.value;
  const needle = term.trim().toLocaleLowerCase();
  let matches = portfolioCatalog.filter(p => !needle || `${p.id} ${p.name}`.toLocaleLowerCase().includes(needle));
  const currentRow = portfolioCatalog.find(p => String(p.id) === current);
  const visible = matches.slice(0, 80);
  if (currentRow && !visible.some(p => p.id === currentRow.id)) visible.unshift(currentRow);
  select.innerHTML = '<option value="">選擇 Portfolio</option>' + visible.map(p =>
    `<option value="${p.id}">${esc(p.name)} · #${p.id}${p.archived ? "（已封存）" : ""}</option>`).join("");
  if (current) select.value = current;
  $("source-portfolio-range").textContent = matches.length > visible.length
    ? `顯示前 ${visible.length} 筆，共 ${matches.length} 筆；請繼續輸入縮小範圍。`
    : `顯示 ${matches.length} / ${portfolioCatalog.length} 筆。`;
}
function sourceMode() {
  const kind = $("investment-source").value;
  $("manual-source-form").hidden = kind !== "manual";
  $("portfolio-source-form").hidden = kind !== "strategy_portfolio";
}
async function sourcePortfolioChanged(preferredVersion = null) {
  const id = Number($("source-portfolio").value);
  $("source-version").innerHTML = '<option value="">選擇固定版本</option>';
  $("source-run").innerHTML = '<option value="">不選 Research Run</option>';
  sourceEvidence = null; renderEvidence(null, $("source-evidence"));
  if (!id) return;
  try {
    const versions = await api(`/api/strategy-lab/portfolios/${id}/versions`);
    if (Number($("source-portfolio").value) !== id) return;
    $("source-version").innerHTML += versions.map(v => `<option value="${v.id}">v${v.version_no} · ${esc(v.note || "")}</option>`).join("");
    if (preferredVersion) $("source-version").value = String(preferredVersion);
    await sourceVersionChanged();
  } catch (err) { sourceError(`版本載入失敗：${err.message}`); }
}
async function sourceVersionChanged(preferredRun = null) {
  const id = Number($("source-portfolio").value), version = Number($("source-version").value);
  $("source-run").innerHTML = '<option value="">不選 Research Run</option>';
  sourceEvidence = null; renderEvidence(null, $("source-evidence"));
  $("source-pin").textContent = version ? `Pinned version ID ${version}；儲存後不會自動切換。` : "請明確選擇版本。";
  if (!id || !version) return;
  try {
    const runs = await api(`/api/strategy-lab/portfolios/${id}/research-runs?limit=200`);
    if (Number($("source-portfolio").value) !== id || Number($("source-version").value) !== version) return;
    $("source-run").innerHTML += runs.filter(r => r.portfolio_version_id === version && r.status === "completed")
      .map(r => `<option value="${esc(r.id)}">${esc(r.created_at || r.finished_at || "")}${r.id ? ` · ${esc(r.id.slice(0, 8))}` : ""}</option>`).join("");
    if (preferredRun) $("source-run").value = preferredRun;
    await sourceRunChanged();
  } catch (err) { sourceError(`Research Run 載入失敗：${err.message}`); }
}
async function sourceRunChanged() {
  const id = Number($("source-portfolio").value), version = Number($("source-version").value);
  const run = $("source-run").value;
  sourceEvidence = null;
  if (!id || !version) return;
  try {
    const ev = await api(sourceUrl(id, version, run));
    if (Number($("source-portfolio").value) !== id || Number($("source-version").value) !== version || $("source-run").value !== run) return;
    sourceEvidence = ev; renderEvidence(ev, $("source-evidence")); updateReferenceButton();
  } catch (err) { sourceError(`研究證據載入失敗：${err.message}`); }
}
function updateReferenceButton() {
  const informed = $("source-assumption").value === "research_informed";
  $("source-scenario-choice").hidden = $("source-assumption").value !== "scenario";
  const cagr = sourceEvidence?.research?.metrics?.cagr;
  $("source-use-reference").hidden = !informed || cagr == null;
  $("source-assumption-note").textContent = informed
    ? "請先閱讀上方 evidence，再自行輸入假設，或主動按 Use as reference。確認來源後仍需按頁面頂端的儲存。"
    : "規劃假設由你決定；選擇 Portfolio 或 Run 不會變更此數字。";
}
async function beginRelink(key) {
  readForm();
  const target = P.targets.find(t => t.stock_id === key);
  if (!target) return;
  relinkKey = key;
  $("investment-source").value = "strategy_portfolio"; sourceMode();
  $("source-form-title").textContent = "Relink / Update Source：重新選擇版本、研究與規劃假設。原假設不會沿用。";
  $("source-cancel").hidden = false;
  $("source-portfolio").value = target.strategy_portfolio_id;
  $("source-return").value = ""; $("source-assumption").value = "manual";
  clearSourceError();
  await sourcePortfolioChanged(target.portfolio_version_id);
  $("source-run").value = ""; await sourceRunChanged(); updateReferenceButton();
  $("portfolio-source-form").scrollIntoView({block: "nearest"}); $("source-version").focus();
}
function confirmPortfolioSource() {
  readForm();
  const id = Number($("source-portfolio").value), version = Number($("source-version").value);
  const run = $("source-run").value || null, assumption = $("source-assumption").value;
  const raw = $("source-return").value.trim();
  if (!id || !version) return sourceError("請選擇 Portfolio 與固定版本。 ");
  if (assumption === "research_informed" && (!run || !sourceEvidence?.research?.available || raw === ""))
    return sourceError("Research-informed 需要已完成的研究證據與明確填寫的 planning return。");
  const key = `portfolio:${id}`;
  if (!relinkKey && P.targets.some(t => t.stock_id === key)) return sourceError("此 Portfolio 已在清單中；請使用 Relink / Update Source。");
  if (relinkKey && relinkKey !== key && P.targets.some(t => t.stock_id === key)) return sourceError("目標 Portfolio 已在清單中，無法建立重複來源。");
  const old = P.targets.find(t => t.stock_id === relinkKey);
  const next = {stock_id: key, source_type: "strategy_portfolio", strategy_portfolio_id: id,
    portfolio_version_id: version, research_run_id: run, assumption_source: raw === "" ? null : assumption,
    expected_return: raw === "" ? null : Number(raw), enabled: old?.enabled ?? 1,
    weight: old?.weight ?? null, actual_value: old?.strategy_portfolio_id === id ? old.actual_value : null};
  if (relinkKey) P.targets = P.targets.filter(t => t.stock_id !== relinkKey);
  P.targets.push(next); relinkKey = null;
  $("source-return").value = ""; $("source-cancel").hidden = true; clearSourceError();
  $("investment-source").value = "etf"; sourceMode();
  renderOtherSources(); recalc();
  $("save-hint").textContent = "來源已更新；請按儲存確認 Planner 情境";
}

/* ── 結果骨架（只建一次，之後只更新內容，圖表實例才能保留） ── */
function renderSkeleton() {
  $("result").innerHTML = `
    <div class="card fin-section">
      <h2><span class="idx">1</span> 財務安全</h2>
      <div class="fin-sum" id="health-cards"></div>
      <div id="health-msg"></div>
    </div>

    <div class="card fin-section">
      <h2><span class="idx">2</span> 每月投資能力</h2>
      <div id="monthly-body"></div>
    </div>

    <div class="card fin-section">
      <h2><span class="idx">3</span> 建議配置</h2>
      <div class="fin-bars" id="role-bars"></div>
      <div id="role-reasons"></div>
    </div>

    <div class="card fin-section">
      <h2><span class="idx">4</span> 每月 ETF 投入金額</h2>
      <div class="scroll-x"><table class="data" id="alloc-table">
        <thead><tr><th class="l">標的</th><th class="l">角色</th><th>比例</th>
          <th>初始投入</th><th>每月定額</th><th>假設報酬</th><th>歷史含息年化</th></tr></thead>
        <tbody></tbody></table></div>
      <p class="fin-note" id="alloc-note"></p>
    </div>

    <div class="card fin-section">
      <h2><span class="idx">5</span> 複利模擬</h2>
      <div class="grid cols-2">
        <div id="summary-kv"></div>
        <div><div id="chart-growth" class="chart"></div></div>
      </div>
      <div id="milestones"></div>
    </div>

    <div class="card fin-section">
      <h2><span class="idx">6</span> 本金與投資收益</h2>
      <div id="chart-sources" class="chart"></div>
      <p class="fin-note" id="sources-note"></p>
    </div>

    <div class="card fin-section">
      <h2><span class="idx">7</span> 三種報酬情境</h2>
      <div id="chart-scenarios" class="chart"></div>
      <div class="scroll-x"><table class="data" id="scenario-table">
        <thead><tr><th class="l">情境</th><th>年化報酬</th><th>期末資產（名目）</th>
          <th>期末購買力（實質）</th><th>投資增值</th><th>獲利占比</th></tr></thead>
        <tbody></tbody></table></div>
      <p class="fin-note">情境模擬並非未來績效預測。</p>
    </div>

    <div class="card fin-section">
      <h2><span class="idx">8</span> 風險分析</h2>
      <div class="grid cols-2">
        <div><div id="chart-asset" class="chart short"></div></div>
        <div><div id="chart-region" class="chart short"></div></div>
      </div>
      <div id="risk-warnings"></div>
    </div>

    <div class="card fin-section">
      <h2><span class="idx">9</span> 退休試算</h2>
      <div class="grid cols-2">
        <div id="retire-kv"></div>
        <div><div id="chart-withdraw" class="chart"></div></div>
      </div>
      <div class="fin-alert" id="sorr-note"></div>
    </div>

    <div class="card fin-section">
      <h2><span class="idx">10</span> 再平衡</h2>
      <p class="fin-note" id="rebalance-msg"></p>
      <div class="scroll-x"><table class="data" id="rebalance-table">
        <thead><tr><th class="l">標的</th><th>目前市值</th><th>目前比例</th>
          <th>目標比例</th><th>偏離</th><th>本期建議投入</th><th>投入後比例</th></tr></thead>
        <tbody></tbody></table></div>
    </div>

    <div class="card fin-section">
      <details class="fin-more">
        <summary>年度詳細表格（逐年資產變化）</summary>
        <div class="scroll-x"><table class="data" id="yearly-table">
          <thead><tr><th>年齡</th><th>年度</th><th>第 N 年</th><th>初始本金</th>
            <th>累積投入</th><th>投資收益</th><th>總資產</th><th>實質購買力</th></tr></thead>
          <tbody></tbody></table></div>
      </details>
    </div>`;
}

/* ── 結果更新 ────────────────────────────────────────── */
function kv(k, v, sub) {
  return `<div class="fin-kv"><span class="k">${k}</span>
    <span class="v">${v}${sub ? `<small>${sub}</small>` : ""}</span></div>`;
}

function updateResult(r) {
  const h = r.health;
  $("health-cards").innerHTML = [
    ["每月可支配所得", fmt.twd(h.disposable), `收入 ${fmt.wan(h.income_total)} − 必要支出 ${fmt.wan(h.essential_expense)}`],
    ["緊急預備金", h.emergency_months == null ? "—" : `${h.emergency_months} 個月`, `可動用現金 ${fmt.wan(h.liquid_cash)}`],
    ["淨資產", fmt.wan(h.net_worth), `資產 ${fmt.wan(h.total_assets)}／負債 ${fmt.wan(h.total_debt)}`],
    ["負債比", h.debt_ratio == null ? "—" : fmt.pct(h.debt_ratio), `儲蓄率 ${h.savings_rate == null ? "—" : fmt.pct(h.savings_rate)}`],
  ].map(([k, v, s]) => `<div class="b"><div class="k">${k}</div><div class="v">${v}</div>
    <div class="s">${s}</div></div>`).join("");
  $("health-msg").innerHTML =
    `<div class="fin-alert ${h.emergency_level === "short" ? "" : "info"}">${esc(h.emergency_message)}</div>`;

  const sg = h.suggestion;
  $("monthly-body").innerHTML = `
    <div class="fin-sum" style="margin-bottom:12px">
      <div class="b"><div class="k">目前採用的每月投資金額</div>
        <div class="v">${fmt.twd(r.monthly_invest)}</div>
        <div class="s">${r.monthly_invest_source === "user" ? "你自行設定" : "採用系統建議中位數"}</div></div>
      <div class="b"><div class="k">系統建議區間</div>
        <div class="v">${fmt.wan(sg.low)} ～ ${fmt.wan(sg.high)}</div>
        <div class="s">依可支配所得、緊備金、年齡與目標推算</div></div>
    </div>
    <p class="fin-note">${esc(sg.summary)}</p>
    <ul class="fin-note" style="padding-left:18px">
      ${sg.reasons.map(x => `<li>${esc(x)}</li>`).join("")}</ul>`;
  $("invest-suggest").innerHTML = sg.high > 0
    ? `建議區間 <b>${fmt.twd(sg.low)} ～ ${fmt.twd(sg.high)}</b>（中位數 ${fmt.twd(sg.mid)}）`
    : esc(sg.summary);

  const rw = r.roles.weights;
  $("role-bars").innerHTML = Object.entries(rw).filter(([, v]) => v > 0)
    .map(([k, v]) => `
      <div class="fin-barrow">
        <span class="muted">${esc(U.roles[k].label)}</span>
        <div class="track"><div style="width:${v}%;background:${ROLE_COLOR[k]}"></div></div>
        <span>${v}%</span>
      </div>`).join("");
  $("role-reasons").innerHTML = `
    <p class="fin-note"><span class="fin-pill on">${esc(r.risk.label)}</span>
      <span class="fin-pill">距退休 ${r.roles.years_to_retire} 年</span>
      ${r.risk.complete ? "" : '<span class="fin-pill hold">風險問卷尚未做完</span>'}</p>
    <ul class="fin-note" style="padding-left:18px">
      ${r.roles.reasons.map(x => `<li>${esc(x)}</li>`).join("")}</ul>`;

  const A = r.allocation;
  $("alloc-table").querySelector("tbody").innerHTML = A.targets.map(t => {
    const m = etfMeta(t.stock_id);
    const source = P.targets.find(x => x.stock_id === t.stock_id);
    const hist = source?.source_type === "strategy_portfolio" ? '<span class="muted">見左側研究證據</span>' : m.history?.available
      ? `${m.history.cagr_total}% <span class="muted">(近${m.history.window_years}年)</span>`
      : '<span class="muted">—</span>';
    return `<tr>
      <td class="l">${t.stock_id === "CASH" ? "現金" :
        source?.source_type === "strategy_portfolio" ? esc(t.name) : source?.source_type === "manual" ? esc(t.stock_id) : `<a href="stock.html?id=${esc(t.stock_id)}">${esc(t.stock_id)} ${esc(t.name)}</a>`}</td>
      <td class="l"><span class="fin-pill">${esc(t.role_label)}</span></td>
      <td>${t.weight}%</td>
      <td>${fmt.twd(t.initial_amount)}</td>
      <td>${fmt.twd(t.monthly_amount)}</td>
      <td>${t.expected_return == null ? "未設定" : `${t.expected_return}%`}</td>
      <td>${hist}</td></tr>`;
  }).join("") || '<tr><td colspan="7" class="muted l">尚未選擇任何標的</td></tr>';
  const sumOk = Math.abs(A.weight_sum - 100) < 0.5;
  $("alloc-note").innerHTML =
    `權重合計 <b class="${sumOk ? "" : "warn"}">${A.weight_sum}%</b>` +
    (sumOk ? "" : "（總和不是 100%，金額分配會依實際比例計算）") +
    `｜組合加權規劃假設 <b>${A.expected_return == null ? "未設定" : `${A.expected_return}%`}</b>` +
    `｜加權內扣費用約 ${A.expected_fee}%` +
    `｜「假設報酬」是試算用的假設值，與左欄的歷史含息年化是兩回事。`;
  $("weight-sum").innerHTML = `權重合計 <b class="${sumOk ? "" : "warn"}">${A.weight_sum}%</b>`;
  const fallback = !!P.assumptions.use_portfolio_return && A.expected_return == null;
  $("base-assumption-status").textContent = fallback
    ? `Currently using Planner Base Assumption: ${P.assumptions.return_base}%. This is not the Strategy Portfolio historical return.／目前使用 Planner 基準假設，並非組合研究的歷史報酬。`
    : P.assumptions.use_portfolio_return ? `目前使用已設定標的的加權規劃假設：${A.expected_return}%。` : `目前使用你在此填寫的 Planner 三種情境假設。`;

  const pr = r.projection, as = r.assumptions;
  $("summary-kv").innerHTML =
    kv("初始本金", fmt.twd(pr.initial), fmt.wan(pr.initial)) +
    kv("每月投入", fmt.twd(pr.monthly), fmt.wan(pr.monthly)) +
    kv("投資時間", `${as.horizon_years} 年`, `基準年化報酬 ${pr.annual_return}%` +
       (pr.fee > 0 ? `，扣費用後 ${pr.net_return}%` : "")) +
    kv("累積每月投入", fmt.twd(pr.monthly_contributed), fmt.wan(pr.monthly_contributed)) +
    kv("總投入本金", fmt.twd(pr.total_contributed), fmt.wan(pr.total_contributed)) +
    kv("投資增值", fmt.twd(pr.investment_gain), fmt.wan(pr.investment_gain)) +
    `<div class="fin-kv total"><span class="k">預估期末資產（名目）</span>
      <span class="v">${fmt.twd(pr.final_value)}<small>${fmt.wan(pr.final_value)}</small></span></div>` +
    kv("今日購買力（實質）", fmt.twd(pr.final_real_value),
       `已扣除 ${as.inflation}% 通膨，${fmt.wan(pr.final_real_value)}`) +
    kv("投資獲利占最終資產", fmt.pct(pr.gain_ratio),
       pr.reinvest ? "股息再投入" : `股息不再投入，另累積現金 ${fmt.wan(pr.cash_pot)}`);

  $("milestones").innerHTML = `<p class="fin-note">里程碑（基準情境）：</p>
    <div class="flex">${r.milestones.map(m => `
      <span class="fin-pill ${m.reached ? "on" : ""}">${fmt.wan(m.target)}${m.is_custom ? "（目標）" : ""}：
        ${m.reached ? `第 ${m.year} 年${m.age ? `／${m.age} 歲` : ""}` : "期間內未達成"}</span>`).join("")}</div>`;

  const gain = pr.final_value - pr.total_contributed;
  $("sources-note").innerHTML =
    `期末資產 ${fmt.twd(pr.final_value)} 之中，<b>${fmt.twd(pr.total_contributed)}</b>
     （${fmt.pct(100 - pr.gain_ratio)}）是自己投入的本金，
     <b>${fmt.twd(gain)}</b>（${fmt.pct(pr.gain_ratio)}）來自投資增值。
     前期本金貢獻較大，後期複利貢獻才會快速拉開。`;

  const SC = r.scenarios.scenarios;
  const scLabel = { conservative: "保守 Conservative", base: "基準 Base", optimistic: "樂觀 Optimistic" };
  $("scenario-table").querySelector("tbody").innerHTML =
    Object.entries(SC).map(([k, s]) => `<tr>
      <td class="l">${scLabel[k]}</td><td>${s.annual_return}%</td>
      <td>${fmt.twd(s.final_value)}<span class="muted"> ${fmt.wan(s.final_value)}</span></td>
      <td>${fmt.twd(s.final_real_value)}</td>
      <td>${fmt.twd(s.investment_gain)}</td><td>${fmt.pct(s.gain_ratio)}</td></tr>`).join("");

  $("risk-warnings").innerHTML = A.warnings.length
    ? A.warnings.map(w => `<div class="fin-alert ${w.level === "info" ? "info" : ""}">
        ${esc(w.message)}<div class="d">${esc(w.detail || "")}</div></div>`).join("")
    : '<p class="fin-note">目前配置沒有偵測到重疊、單一市場過度集中或資產類別過度集中的情形。</p>';

  const RT = r.retirement;
  $("retire-kv").innerHTML =
    kv("退休後每月支出", fmt.twd(RT.gap.expense_monthly), "以今日購買力輸入") +
    kv("退休金＋其他收入", fmt.twd(RT.gap.income_monthly), "每月") +
    kv("每月缺口", fmt.twd(RT.gap.monthly_gap),
       `退休當年名目金額約 ${fmt.twd(RT.gap.monthly_gap_at_retire)}`) +
    kv("每年缺口", fmt.twd(RT.gap.annual_gap_today),
       `退休當年約 ${fmt.twd(RT.gap.annual_gap_at_retire)}`) +
    kv(`所需退休資產（提款率 ${RT.required_at_rate.rate}%）`,
       RT.required_at_rate.required ? fmt.twd(RT.required_at_rate.required) : "—",
       RT.required_corpus.map(c => `${c.rate}%：${fmt.wan(c.required)}`).join("｜")) +
    kv("退休時預估資產", fmt.twd(RT.balance_at_retire), `${RT.years_to_retire} 年後`) +
    `<div class="fin-kv total"><span class="k">${RT.sufficient ? "預估可覆蓋退休需求" : "預估仍有缺口"}</span>
      <span class="v">${RT.surplus == null ? "—" : fmt.twd(Math.abs(RT.surplus))}
      <small>${RT.sufficient ? "高於所需資產" : "低於所需資產"}</small></span></div>` +
    kv("每年提款", fmt.twd(RT.annual_withdraw), `每月約 ${fmt.twd(RT.monthly_withdraw)}`) +
    kv("正常情境可支撐", RT.normal.lasts_through
        ? `撐過 ${RT.normal.years_supported} 年（至 ${RT.normal.series.at(-1).age} 歲）`
        : `${RT.normal.years_supported} 年（${RT.normal.depleted_age} 歲耗盡）`,
       `期末餘額 ${fmt.wan(RT.normal.final_balance)}`) +
    kv("壓力情境可支撐", RT.sorr.lasts_through
        ? `撐過 ${RT.sorr.years_supported} 年`
        : `${RT.sorr.years_supported} 年（${RT.sorr.depleted_age} 歲耗盡）`,
       `退休前 3 年年報酬 ${P.retirement.sorr_shock ?? -10}%`);
  $("sorr-note").innerHTML = esc(RT.sorr_note) +
    (RT.sorr_cost_years > 0
      ? `<div class="d">以目前設定，壓力情境比正常情境少支撐約 ${RT.sorr_cost_years} 年。</div>`
      : "");

  const RB = r.rebalance;
  $("rebalance-msg").textContent = RB.message;
  $("rebalance-table").querySelector("tbody").innerHTML = RB.has_holdings
    ? RB.rows.map(x => `<tr>
        <td class="l">${x.stock_id === "CASH" ? "現金" : esc(x.stock_id)} ${esc(x.name || "")}</td>
        <td>${fmt.twd(x.value)}</td><td>${x.current_weight}%</td>
        <td>${x.target_weight}%</td>
        <td class="${Math.abs(x.drift) >= 5 ? "warn" : "muted"}">${x.drift > 0 ? "+" : ""}${x.drift}pp</td>
        <td>${fmt.twd(x.suggested_contribution)}</td><td>${x.after_weight}%</td></tr>`).join("")
    : '<tr><td colspan="7" class="muted l">尚無持倉資料</td></tr>';

  $("yearly-table").querySelector("tbody").innerHTML = pr.series.map(s => `<tr>
    <td>${s.age ?? "—"}</td><td>${s.calendar_year ?? "—"}</td><td>${s.year}</td>
    <td>${fmt.wan(s.initial)}</td><td>${fmt.wan(s.contributed)}</td>
    <td>${fmt.wan(s.returns)}</td><td>${fmt.twd(s.value)}</td>
    <td>${fmt.twd(s.real_value)}</td></tr>`).join("");

  drawCharts(r);
}

/* ── 圖表 ────────────────────────────────────────────── */
function chart(id) {
  if (!charts[id]) charts[id] = makeChart(id);
  return charts[id];
}
const axisX = data => ({ type: "category", data, ...axisStyle() });
const axisY = () => ({
  type: "value", ...axisStyle(),
  axisLabel: { color: CHART_THEME.MUTED, formatter: v => fmt.wan(v) },
});

function drawCharts(r) {
  const pr = r.projection;
  const xs = pr.series.map(s => s.age ? `${s.age}歲` : `第${s.year}年`);

  chart("chart-growth").setOption({
    ...baseOption({ legend: { data: ["累積投入本金", "投資組合總資產"], textStyle: { color: CHART_THEME.MUTED } } }),
    xAxis: axisX(xs), yAxis: axisY(),
    tooltip: { trigger: "axis", backgroundColor: CHART_THEME.PANEL2, borderColor: CHART_THEME.BORDER,
               textStyle: { color: CHART_THEME.TEXT, fontSize: 12 },
               valueFormatter: v => fmt.twd(v) },
    series: [
      { name: "累積投入本金", type: "line", showSymbol: false,
        data: pr.series.map(s => s.total_contributed),
        lineStyle: { color: RESEARCH_COLOR.soft, width: 2, type: "dashed" } },
      { name: "投資組合總資產", type: "line", showSymbol: false,
        data: pr.series.map(s => s.value),
        lineStyle: { color: RESEARCH_COLOR.indigo, width: 2 },
        areaStyle: { color: "rgba(85,93,122,.14)" } },
    ],
  }, true);

  chart("chart-sources").setOption({
    ...baseOption({ legend: { data: ["初始本金", "累積每月投入", "投資收益"], textStyle: { color: CHART_THEME.MUTED } } }),
    xAxis: axisX(xs), yAxis: axisY(),
    tooltip: { trigger: "axis", backgroundColor: CHART_THEME.PANEL2, borderColor: CHART_THEME.BORDER,
               textStyle: { color: CHART_THEME.TEXT, fontSize: 12 },
               valueFormatter: v => fmt.twd(v) },
    series: [
      { name: "初始本金", type: "line", stack: "s", showSymbol: false,
        data: pr.series.map(s => s.initial),
        lineStyle: { width: 0 }, areaStyle: { color: RESEARCH_COLOR.teal } },
      { name: "累積每月投入", type: "line", stack: "s", showSymbol: false,
        data: pr.series.map(s => s.contributed),
        lineStyle: { width: 0 }, areaStyle: { color: RESEARCH_COLOR.indigo } },
      { name: "投資收益", type: "line", stack: "s", showSymbol: false,
        data: pr.series.map(s => Math.max(s.returns, 0)),
        lineStyle: { width: 0 }, areaStyle: { color: RESEARCH_COLOR.slate } },
    ],
  }, true);

  const SC = r.scenarios.scenarios;
  const scMeta = { conservative: ["保守", RESEARCH_COLOR.soft], base: ["基準", RESEARCH_COLOR.indigo],
                   optimistic: ["樂觀", RESEARCH_COLOR.teal] };
  chart("chart-scenarios").setOption({
    ...baseOption({ legend: { data: Object.keys(SC).map(k => scMeta[k][0]), textStyle: { color: CHART_THEME.MUTED } } }),
    xAxis: axisX(xs), yAxis: axisY(),
    tooltip: { trigger: "axis", backgroundColor: CHART_THEME.PANEL2, borderColor: CHART_THEME.BORDER,
               textStyle: { color: CHART_THEME.TEXT, fontSize: 12 },
               valueFormatter: v => fmt.twd(v) },
    series: Object.entries(SC).map(([k, s]) => ({
      name: scMeta[k][0], type: "line", showSymbol: false,
      data: s.series.map(x => x.value),
      lineStyle: { color: scMeta[k][1], width: 2 },
    })),
  }, true);

  const ex = r.allocation.exposure;
  chart("chart-asset").setOption({
    backgroundColor: "transparent",
    title: { text: "資產類別", left: "center", top: 4,
             textStyle: { color: CHART_THEME.MUTED, fontSize: 13, fontWeight: "normal" } },
    tooltip: { trigger: "item", backgroundColor: CHART_THEME.PANEL2, borderColor: CHART_THEME.BORDER,
               textStyle: { color: CHART_THEME.TEXT, fontSize: 12 },
               formatter: p => `${p.name}：${p.value}%` },
    series: [{
      type: "pie", radius: ["42%", "68%"], center: ["50%", "56%"],
      label: { color: CHART_THEME.TEXT, formatter: "{b} {c}%" },
      labelLine: { lineStyle: { color: CHART_THEME.BORDER } },
      data: Object.entries(ex.asset).map(([k, v]) => ({
        name: { equity: "股票", bond: "債券", cash: "現金" }[k] || k, value: v,
        itemStyle: { color: ASSET_COLOR[k] || RESEARCH_COLOR.soft },
      })),
    }],
  }, true);

  const regions = Object.entries(ex.region);
  chart("chart-region").setOption({
    ...baseOption({ grid: { left: 60, right: 30, top: 36, bottom: 30 } }),
    title: { text: "國家曝險", left: "center", top: 4,
             textStyle: { color: CHART_THEME.MUTED, fontSize: 13, fontWeight: "normal" } },
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" },
               backgroundColor: CHART_THEME.PANEL2, borderColor: CHART_THEME.BORDER,
               textStyle: { color: CHART_THEME.TEXT, fontSize: 12 },
               valueFormatter: v => `${v}%` },
    xAxis: { type: "value", max: 100, ...axisStyle() },
    yAxis: { type: "category", ...axisStyle(),
             data: regions.map(([k]) => ({ TW: "台灣", US: "美國", "-": "現金" }[k] || k)) },
    series: [{
      type: "bar", barWidth: 18,
      data: regions.map(([k, v]) => ({ value: v, itemStyle: { color: REGION_COLOR[k] || RESEARCH_COLOR.soft } })),
      label: { show: true, position: "right", color: CHART_THEME.MUTED, formatter: "{c}%" },
    }],
  }, true);

  const RT = r.retirement;
  chart("chart-withdraw").setOption({
    ...baseOption({ legend: { data: ["正常情境", "退休初期負報酬"], textStyle: { color: CHART_THEME.MUTED } } }),
    xAxis: axisX(RT.normal.series.map(s => `${s.age}歲`)), yAxis: axisY(),
    tooltip: { trigger: "axis", backgroundColor: CHART_THEME.PANEL2, borderColor: CHART_THEME.BORDER,
               textStyle: { color: CHART_THEME.TEXT, fontSize: 12 },
               valueFormatter: v => fmt.twd(v) },
    series: [
      { name: "正常情境", type: "line", showSymbol: false,
        data: RT.normal.series.map(s => s.balance),
        lineStyle: { color: RESEARCH_COLOR.indigo, width: 2 },
        areaStyle: { color: "rgba(85,93,122,.12)" } },
      { name: "退休初期負報酬", type: "line", showSymbol: false,
        data: RT.sorr.series.map(s => s.balance),
        lineStyle: { color: RESEARCH_COLOR.ochre, width: 2, type: "dashed" } },
    ],
  }, true);
}

/* ── 試算（debounce，spec §31 要求所有結果即時更新） ──── */
let timer;
function recalc() {
  clearTimeout(timer);
  timer = setTimeout(async () => {
    readForm();
    try {
      R = await api("/api/finance/simulate", { method: "POST", body: P });
      updateResult(R);
      syncWeightInputs();
    } catch (err) { toastError("試算失敗：" + err.message); }
  }, 250);
}

/* 未自訂權重時，把規則算出來的權重回填到輸入框，讓使用者看得到起點 */
function syncWeightInputs() {
  if (P.assumptions.custom_weights) return;
  const w = Object.fromEntries(R.allocation.targets.map(t => [t.stock_id, t.weight]));
  document.querySelectorAll("#etf-list .fin-etf, #other-source-list .fin-source-row").forEach(row => {
    const el = row.querySelector(".w");
    if (document.activeElement !== el) el.value = w[row.dataset.sid] ?? "";
  });
}

/* ── 情境管理 ────────────────────────────────────────── */
async function loadProfileList(selectId) {
  const rows = await api("/api/finance/profiles");
  $("profile-sel").innerHTML = rows.length
    ? rows.map(p => `<option value="${p.id}">${esc(p.name)}</option>`).join("")
    : '<option value="">（尚未建立情境）</option>';
  if (selectId) $("profile-sel").value = selectId;
  return rows;
}

async function openProfile(id) {
  P = id ? await api(`/api/finance/profiles/${id}`)
         : await api("/api/finance/profiles/new");
  fillForm();
  relinkKey = null;
  $("source-return").value = "";
  $("source-cancel").hidden = true;
  recalc();
}

async function boot() {
  U = await api("/api/finance/universe");
  $("disclaimer").textContent = U.disclaimer;
  $("f-goal").innerHTML = Object.entries(U.goals)
    .map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("");
  $("f-risk_type_manual").innerHTML = '<option value="">（用問卷結果）</option>' +
    U.risk_types.map(t => `<option value="${t.key}">${esc(t.label)}｜${esc(t.desc)}</option>`).join("");
  renderSteps();
  renderSkeleton();
  renderEvidence(null, $("source-evidence"));
  goStep(1);
  await loadPortfolios();

  const rows = await loadProfileList();
  await openProfile(rows.length ? rows[0].id : null);

  document.querySelectorAll("[id^='f-']").forEach(el => {
    el.addEventListener("input", recalc);
    el.addEventListener("change", recalc);
  });
}

/* ── 事件 ────────────────────────────────────────────── */
$("btn-prev").addEventListener("click", () => goStep(step - 1));
$("btn-next").addEventListener("click", () => goStep(step + 1));

$("profile-sel").addEventListener("change", e => {
  if (e.target.value) openProfile(Number(e.target.value));
});

$("btn-new").addEventListener("click", () => {
  $("new-profile-name").value = "情境 " + (new Date()).toLocaleDateString("zh-TW");
  $("new-profile-error").hidden = true;
  $("new-profile-dialog").showModal();
  $("new-profile-name").focus();
});
$("new-profile-cancel").addEventListener("click", () => $("new-profile-dialog").close());
$("new-profile-form").addEventListener("submit", async e => {
  e.preventDefault();
  const name = $("new-profile-name").value.trim();
  if (!name) { $("new-profile-error").textContent = "請輸入情境名稱。"; $("new-profile-error").hidden = false; return; }
  readForm();
  try {
    P = await api("/api/finance/profiles/new?name=" + encodeURIComponent(name));
    fillForm(); recalc();
    $("new-profile-dialog").close();
    $("save-hint").textContent = "尚未儲存，按「儲存」建立";
  } catch (err) { $("new-profile-error").textContent = err.message; $("new-profile-error").hidden = false; }
});

$("btn-save").addEventListener("click", async () => {
  readForm();
  const invalid = P.targets.find(t => t.source_type === "manual" && t.enabled && t.expected_return == null);
  if (invalid) {
    const error = $("planner-form-error"); error.hidden = false;
    error.textContent = `自訂來源「${invalid.stock_id}」需要明確的 planning return。`;
    goStep(5); error.focus(); return;
  }
  $("planner-form-error").hidden = true;
  if (!P.name) P.name = prompt("情境名稱", "基準情境") || "基準情境";
  try {
    P = await api("/api/finance/profiles", { method: "POST", body: P });
    await loadProfileList(P.id);
    $("save-hint").textContent = "已儲存 " + new Date().toLocaleTimeString("zh-TW");
    toast("已儲存情境「" + P.name + "」");
  } catch (err) { toastError("儲存失敗：" + err.message); }
});

$("btn-del").addEventListener("click", async () => {
  if (!P?.id) { toast("這個情境還沒儲存過"); return; }
  if (!confirm(`確定刪除情境「${P.name}」？`)) return;
  await api(`/api/finance/profiles/${P.id}`, { method: "DELETE" });
  const rows = await loadProfileList();
  await openProfile(rows.length ? rows[0].id : null);
  toast("已刪除");
});

$("btn-refresh-etf").addEventListener("click", async e => {
  e.target.disabled = true;
  try {
    const r = await api("/api/finance/refresh-etf", { method: "POST", body: {} });
    toast(r.note);
  } catch (err) { toastError("ETF 歷史資料未補齊：" + err.message); }
  setTimeout(() => { e.target.disabled = false; }, 3000);
});

$("btn-use-suggest").addEventListener("click", () => {
  if (!R) return;
  $("f-monthly_invest").value = R.health.suggestion.mid;
  recalc();
});

$("btn-reset-weights").addEventListener("click", () => {
  $("f-a-custom_weights").checked = false;
  document.querySelectorAll("#etf-list .w").forEach(el => { el.value = ""; });
  recalc();
});

$("btn-add-etf").addEventListener("click", async () => {
  const sid = $("add-etf").value.trim().toUpperCase();
  if (!sid) return;
  readForm();
  if (P.targets.some(t => t.stock_id === sid)) { toast("已在名單中"); return; }
  U = await api("/api/finance/universe?extra=" +
    encodeURIComponent([...P.targets.map(t => t.stock_id), sid].join(",")));
  P.targets.push({ stock_id: sid, enabled: 1, weight: null,
                   expected_return: null, actual_value: null });
  renderEtfList();
  $("add-etf").value = "";
  recalc();
});

$("investment-source").addEventListener("change", sourceMode);
$("add-manual").addEventListener("click", () => {
  readForm();
  const name = $("manual-name").value.trim(), raw = $("manual-return").value.trim();
  const error = $("manual-source-error");
  if (!name || raw === "" || !Number.isFinite(Number(raw))) { error.textContent = "請輸入自訂名稱與明確的 planning return。"; error.hidden = false; error.focus(); return; }
  if (name.startsWith("portfolio:") || P.targets.some(t => t.stock_id === name) || U.items.some(x => x.stock_id === name)) {
    error.textContent = "名稱已被使用或屬於保留代號。"; error.hidden = false; error.focus(); return;
  }
  error.hidden = true;
  P.targets.push({stock_id: name, source_type: "manual", assumption_source: "manual",
    expected_return: Number(raw), actual_value: null, enabled: 1, weight: null});
  $("manual-name").value = ""; $("manual-return").value = "";
  renderOtherSources(); recalc();
});
$("source-portfolio").addEventListener("change", () => { clearSourceError(); $("source-return").value = ""; sourcePortfolioChanged(); });
$("source-portfolio-search").addEventListener("input", e => renderPortfolioOptions(e.target.value));
$("source-version").addEventListener("change", () => { clearSourceError(); $("source-return").value = ""; sourceVersionChanged(); });
$("source-run").addEventListener("change", () => { clearSourceError(); $("source-return").value = ""; sourceRunChanged(); });
$("source-assumption").addEventListener("change", updateReferenceButton);
$("source-use-scenario").addEventListener("click", () => {
  readForm();
  const key = `return_${$("source-scenario").value}`;
  $("source-return").value = P.assumptions[key];
  $("source-assumption-note").textContent = `你已主動採用 Planner ${$("source-scenario").selectedOptions[0].textContent}情境假設 ${P.assumptions[key]}%。請再確認來源並儲存。`;
});
$("source-use-reference").addEventListener("click", () => {
  const cagr = sourceEvidence?.research?.metrics?.cagr;
  if (cagr == null || $("source-assumption").value !== "research_informed") return;
  $("source-return").value = Number(cagr).toFixed(2);
  $("source-assumption-note").textContent = "你已主動採用歷史 CAGR 作為規劃假設；這是你的選擇，不是系統預測。請再確認來源，然後儲存情境。";
});
$("source-confirm").addEventListener("click", confirmPortfolioSource);
$("source-cancel").addEventListener("click", () => { relinkKey = null; $("source-cancel").hidden = true; $("source-return").value = ""; clearSourceError(); $("investment-source").value = "etf"; sourceMode(); });

boot();
