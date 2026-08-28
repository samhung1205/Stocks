/* 財務規劃頁：輸入 → 呼叫後端試算 → 渲染結果。
   所有財務公式都在後端（app/services/finance/），這裡完全不算數字，
   只負責收集輸入、丟給 /api/finance/simulate、把回來的結果畫出來。 */

let U = null;         // 名單/問卷/預設值
let P = null;         // 目前情境
let R = null;         // 最近一次試算結果
let step = 1;
const charts = {};
const MAX_STEP = 7;

/* 角色與曝險配色：刻意不用紅綠——財務規劃沒有「漲跌」的語意 */
const ROLE_COLOR = {
  core_tw: "#4f8ef7", core_us: "#7aa9f7", growth: "#a78bfa",
  income: "#e8a33d", defense: "#5eb0b8", cash: "#8b93a3",
};
const ASSET_COLOR = { equity: "#4f8ef7", bond: "#5eb0b8", cash: "#8b93a3" };
const REGION_COLOR = { TW: "#4f8ef7", US: "#a78bfa", "-": "#8b93a3" };

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
  P.targets = [...document.querySelectorAll("#etf-list .fin-etf")].map(row => {
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
    b.classList.toggle("active", Number(b.dataset.go) === step));
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
  const byId = Object.fromEntries((P.targets || []).map(t => [t.stock_id, t]));
  const ids = [...new Set([...(P.targets || []).map(t => t.stock_id),
                           ...U.items.map(i => i.stock_id)])];
  $("etf-list").innerHTML = ids.map(sid => {
    const m = etfMeta(sid);
    const t = byId[sid] || { enabled: 0 };
    const on = !!t.enabled;
    return `
      <div class="fin-etf ${on ? "" : "off"}" data-sid="${esc(sid)}">
        <div class="top">
          <input type="checkbox" class="en" ${on ? "checked" : ""}>
          <span class="sid">${esc(sid)}</span>
          <span>${esc(m.name || "")}</span>
          <span class="fin-pill">${esc(m.role_label || "")}</span>
        </div>
        <div class="fields">
          <div><label>目標權重 %</label><input type="number" class="w" step="0.1"
            value="${t.weight ?? ""}"></div>
          <div><label>假設報酬 %</label><input type="number" class="er" step="0.1"
            placeholder="${m.default_return ?? ""}" value="${t.expected_return ?? ""}"></div>
          <div><label>目前市值</label><input type="number" class="av"
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

/* ── 結果骨架（只建一次，之後只更新內容，圖表實例才能保留） ── */
function renderSkeleton() {
  $("result").innerHTML = `
    <div class="card fin-section">
      <h2><span class="idx">1</span> 財務安全 Financial Health</h2>
      <div class="fin-sum" id="health-cards"></div>
      <div id="health-msg"></div>
    </div>

    <div class="card fin-section">
      <h2><span class="idx">2</span> 每月投資能力 Monthly Investment</h2>
      <div id="monthly-body"></div>
    </div>

    <div class="card fin-section">
      <h2><span class="idx">3</span> 建議配置 Suggested Portfolio</h2>
      <div class="fin-bars" id="role-bars"></div>
      <div id="role-reasons"></div>
    </div>

    <div class="card fin-section">
      <h2><span class="idx">4</span> 每月 ETF 投入金額 Monthly Allocation</h2>
      <div class="scroll-x"><table class="data" id="alloc-table">
        <thead><tr><th class="l">標的</th><th class="l">角色</th><th>比例</th>
          <th>初始投入</th><th>每月定額</th><th>假設報酬</th><th>歷史含息年化</th></tr></thead>
        <tbody></tbody></table></div>
      <p class="fin-note" id="alloc-note"></p>
    </div>

    <div class="card fin-section">
      <h2><span class="idx">5</span> 複利模擬 Compound Growth</h2>
      <div class="grid cols-2">
        <div id="summary-kv"></div>
        <div><div id="chart-growth" class="chart"></div></div>
      </div>
      <div id="milestones"></div>
    </div>

    <div class="card fin-section">
      <h2><span class="idx">6</span> 本金與投資收益 Contribution vs Investment Return</h2>
      <div id="chart-sources" class="chart"></div>
      <p class="fin-note" id="sources-note"></p>
    </div>

    <div class="card fin-section">
      <h2><span class="idx">7</span> 三種報酬情境 Scenario Analysis</h2>
      <div id="chart-scenarios" class="chart"></div>
      <div class="scroll-x"><table class="data" id="scenario-table">
        <thead><tr><th class="l">情境</th><th>年化報酬</th><th>期末資產（名目）</th>
          <th>期末購買力（實質）</th><th>投資增值</th><th>獲利占比</th></tr></thead>
        <tbody></tbody></table></div>
      <p class="fin-note">情境模擬並非未來績效預測。</p>
    </div>

    <div class="card fin-section">
      <h2><span class="idx">8</span> 風險分析 Risk Analysis</h2>
      <div class="grid cols-2">
        <div><div id="chart-asset" class="chart short"></div></div>
        <div><div id="chart-region" class="chart short"></div></div>
      </div>
      <div id="risk-warnings"></div>
    </div>

    <div class="card fin-section">
      <h2><span class="idx">9</span> 退休試算 Retirement Projection</h2>
      <div class="grid cols-2">
        <div id="retire-kv"></div>
        <div><div id="chart-withdraw" class="chart"></div></div>
      </div>
      <div class="fin-alert" id="sorr-note"></div>
    </div>

    <div class="card fin-section">
      <h2><span class="idx">10</span> 再平衡 Rebalancing</h2>
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
    const hist = m.history?.available
      ? `${m.history.cagr_total}% <span class="muted">(近${m.history.window_years}年)</span>`
      : '<span class="muted">—</span>';
    return `<tr>
      <td class="l">${t.stock_id === "CASH" ? "現金" :
        `<a href="stock.html?id=${esc(t.stock_id)}">${esc(t.stock_id)} ${esc(t.name)}</a>`}</td>
      <td class="l"><span class="fin-pill" style="border-color:${ROLE_COLOR[t.role]};color:${ROLE_COLOR[t.role]}">${esc(t.role_label)}</span></td>
      <td>${t.weight}%</td>
      <td>${fmt.twd(t.initial_amount)}</td>
      <td>${fmt.twd(t.monthly_amount)}</td>
      <td>${t.expected_return}%</td>
      <td>${hist}</td></tr>`;
  }).join("") || '<tr><td colspan="7" class="muted l">尚未選擇任何標的</td></tr>';
  const sumOk = Math.abs(A.weight_sum - 100) < 0.5;
  $("alloc-note").innerHTML =
    `權重合計 <b class="${sumOk ? "" : "warn"}">${A.weight_sum}%</b>` +
    (sumOk ? "" : "（總和不是 100%，金額分配會依實際比例計算）") +
    `｜組合加權預期報酬 <b>${A.expected_return}%</b>` +
    `｜加權內扣費用約 ${A.expected_fee}%` +
    `｜「假設報酬」是試算用的假設值，與左欄的歷史含息年化是兩回事。`;
  $("weight-sum").innerHTML = `權重合計 <b class="${sumOk ? "" : "warn"}">${A.weight_sum}%</b>`;

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
  axisLabel: { color: "#8b93a3", formatter: v => fmt.wan(v) },
});

function drawCharts(r) {
  const pr = r.projection;
  const xs = pr.series.map(s => s.age ? `${s.age}歲` : `第${s.year}年`);

  chart("chart-growth").setOption({
    ...baseOption({ legend: { data: ["累積投入本金", "投資組合總資產"], textStyle: { color: "#8b93a3" } } }),
    xAxis: axisX(xs), yAxis: axisY(),
    tooltip: { trigger: "axis", backgroundColor: "#1e222b", borderColor: "#2a2f3a",
               textStyle: { color: "#e6e9ef", fontSize: 12 },
               valueFormatter: v => fmt.twd(v) },
    series: [
      { name: "累積投入本金", type: "line", showSymbol: false,
        data: pr.series.map(s => s.total_contributed),
        lineStyle: { color: "#8b93a3", width: 2, type: "dashed" } },
      { name: "投資組合總資產", type: "line", showSymbol: false,
        data: pr.series.map(s => s.value),
        lineStyle: { color: "#4f8ef7", width: 2 },
        areaStyle: { color: "rgba(79,142,247,.14)" } },
    ],
  }, true);

  chart("chart-sources").setOption({
    ...baseOption({ legend: { data: ["初始本金", "累積每月投入", "投資收益"], textStyle: { color: "#8b93a3" } } }),
    xAxis: axisX(xs), yAxis: axisY(),
    tooltip: { trigger: "axis", backgroundColor: "#1e222b", borderColor: "#2a2f3a",
               textStyle: { color: "#e6e9ef", fontSize: 12 },
               valueFormatter: v => fmt.twd(v) },
    series: [
      { name: "初始本金", type: "line", stack: "s", showSymbol: false,
        data: pr.series.map(s => s.initial),
        lineStyle: { width: 0 }, areaStyle: { color: "#5eb0b8" } },
      { name: "累積每月投入", type: "line", stack: "s", showSymbol: false,
        data: pr.series.map(s => s.contributed),
        lineStyle: { width: 0 }, areaStyle: { color: "#4f8ef7" } },
      { name: "投資收益", type: "line", stack: "s", showSymbol: false,
        data: pr.series.map(s => Math.max(s.returns, 0)),
        lineStyle: { width: 0 }, areaStyle: { color: "#a78bfa" } },
    ],
  }, true);

  const SC = r.scenarios.scenarios;
  const scMeta = { conservative: ["保守", "#8b93a3"], base: ["基準", "#4f8ef7"],
                   optimistic: ["樂觀", "#a78bfa"] };
  chart("chart-scenarios").setOption({
    ...baseOption({ legend: { data: Object.keys(SC).map(k => scMeta[k][0]), textStyle: { color: "#8b93a3" } } }),
    xAxis: axisX(xs), yAxis: axisY(),
    tooltip: { trigger: "axis", backgroundColor: "#1e222b", borderColor: "#2a2f3a",
               textStyle: { color: "#e6e9ef", fontSize: 12 },
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
             textStyle: { color: "#8b93a3", fontSize: 13, fontWeight: "normal" } },
    tooltip: { trigger: "item", backgroundColor: "#1e222b", borderColor: "#2a2f3a",
               textStyle: { color: "#e6e9ef", fontSize: 12 },
               formatter: p => `${p.name}：${p.value}%` },
    series: [{
      type: "pie", radius: ["42%", "68%"], center: ["50%", "56%"],
      label: { color: "#e6e9ef", formatter: "{b} {c}%" },
      labelLine: { lineStyle: { color: "#2a2f3a" } },
      data: Object.entries(ex.asset).map(([k, v]) => ({
        name: { equity: "股票", bond: "債券", cash: "現金" }[k] || k, value: v,
        itemStyle: { color: ASSET_COLOR[k] || "#8b93a3" },
      })),
    }],
  }, true);

  const regions = Object.entries(ex.region);
  chart("chart-region").setOption({
    ...baseOption({ grid: { left: 60, right: 30, top: 36, bottom: 30 } }),
    title: { text: "國家曝險", left: "center", top: 4,
             textStyle: { color: "#8b93a3", fontSize: 13, fontWeight: "normal" } },
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" },
               backgroundColor: "#1e222b", borderColor: "#2a2f3a",
               textStyle: { color: "#e6e9ef", fontSize: 12 },
               valueFormatter: v => `${v}%` },
    xAxis: { type: "value", max: 100, ...axisStyle() },
    yAxis: { type: "category", ...axisStyle(),
             data: regions.map(([k]) => ({ TW: "台灣", US: "美國", "-": "現金" }[k] || k)) },
    series: [{
      type: "bar", barWidth: 18,
      data: regions.map(([k, v]) => ({ value: v, itemStyle: { color: REGION_COLOR[k] || "#8b93a3" } })),
      label: { show: true, position: "right", color: "#8b93a3", formatter: "{c}%" },
    }],
  }, true);

  const RT = r.retirement;
  chart("chart-withdraw").setOption({
    ...baseOption({ legend: { data: ["正常情境", "退休初期負報酬"], textStyle: { color: "#8b93a3" } } }),
    xAxis: axisX(RT.normal.series.map(s => `${s.age}歲`)), yAxis: axisY(),
    tooltip: { trigger: "axis", backgroundColor: "#1e222b", borderColor: "#2a2f3a",
               textStyle: { color: "#e6e9ef", fontSize: 12 },
               valueFormatter: v => fmt.twd(v) },
    series: [
      { name: "正常情境", type: "line", showSymbol: false,
        data: RT.normal.series.map(s => s.balance),
        lineStyle: { color: "#4f8ef7", width: 2 },
        areaStyle: { color: "rgba(79,142,247,.12)" } },
      { name: "退休初期負報酬", type: "line", showSymbol: false,
        data: RT.sorr.series.map(s => s.balance),
        lineStyle: { color: "#e8a33d", width: 2, type: "dashed" } },
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
    } catch (err) { toast("試算失敗：" + err.message); }
  }, 250);
}

/* 未自訂權重時，把規則算出來的權重回填到輸入框，讓使用者看得到起點 */
function syncWeightInputs() {
  if (P.assumptions.custom_weights) return;
  const w = Object.fromEntries(R.allocation.targets.map(t => [t.stock_id, t.weight]));
  document.querySelectorAll("#etf-list .fin-etf").forEach(row => {
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
  goStep(1);

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

$("btn-new").addEventListener("click", async () => {
  const name = prompt("新情境名稱", "情境 " + (new Date()).toLocaleDateString("zh-TW"));
  if (!name) return;
  readForm();
  P = await api("/api/finance/profiles/new?name=" + encodeURIComponent(name));
  fillForm();
  recalc();
  $("save-hint").textContent = "尚未儲存，按「儲存」建立";
});

$("btn-save").addEventListener("click", async () => {
  readForm();
  if (!P.name) P.name = prompt("情境名稱", "基準情境") || "基準情境";
  try {
    P = await api("/api/finance/profiles", { method: "POST", body: P });
    await loadProfileList(P.id);
    $("save-hint").textContent = "已儲存 " + new Date().toLocaleTimeString("zh-TW");
    toast("已儲存情境「" + P.name + "」");
  } catch (err) { toast("儲存失敗：" + err.message); }
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
  } catch (err) { toast("失敗：" + err.message); }
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
  if (P.targets.some(t => t.stock_id === sid)) { toast("已在名單中"); return; }
  U = await api("/api/finance/universe?extra=" +
    encodeURIComponent([...P.targets.map(t => t.stock_id), sid].join(",")));
  readForm();
  P.targets.push({ stock_id: sid, enabled: 1, weight: null,
                   expected_return: null, actual_value: null });
  renderEtfList();
  $("add-etf").value = "";
  recalc();
});

boot();
