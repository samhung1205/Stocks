/* 共用：導覽列、API 封裝、格式化、圖表主題 */

const NAV = [
  ["index.html", "儀表板"],
  ["screener.html", "篩選器"],
  ["watchlist.html", "觀察名單"],
  ["journal.html", "交易日誌"],
  ["finance.html", "財務規劃"],
];

function renderNav() {
  const here = location.pathname.split("/").pop() || "index.html";
  const nav = document.createElement("nav");
  nav.className = "topbar";
  nav.innerHTML = `
    <span class="brand">台股分析</span>
    ${NAV.map(([href, label]) =>
      `<a class="navlink ${here === href ? "active" : ""}" href="${href}">${label}</a>`
    ).join("")}
    <div class="searchbox">
      <input id="global-search" placeholder="輸入代號或名稱（如 2330）" autocomplete="off">
      <div class="search-results" id="search-results"></div>
    </div>`;
  document.body.prepend(nav);

  const input = nav.querySelector("#global-search");
  const box = nav.querySelector("#search-results");
  let timer;
  input.addEventListener("input", () => {
    clearTimeout(timer);
    const q = input.value.trim();
    if (!q) { box.style.display = "none"; return; }
    timer = setTimeout(async () => {
      const rows = await api(`/api/stocks/search?q=${encodeURIComponent(q)}`);
      box.innerHTML = rows.map(r =>
        `<div onclick="location.href='stock.html?id=${r.stock_id}'">
           <span class="sid">${r.stock_id}</span><span>${r.name}</span>
           <span class="mkt">${r.market === "twse" ? "上市" : "上櫃"}·${r.industry || ""}</span>
         </div>`).join("") || `<div class="muted">查無結果</div>`;
      box.style.display = "block";
    }, 250);
  });
  document.addEventListener("click", e => {
    if (!nav.querySelector(".searchbox").contains(e.target)) box.style.display = "none";
  });
  input.addEventListener("keydown", e => {
    if (e.key === "Enter" && /^\d{4,6}$/.test(input.value.trim()))
      location.href = `stock.html?id=${input.value.trim()}`;
  });
}

async function api(path, opts = {}) {
  if (opts.body && typeof opts.body !== "string") {
    opts.body = JSON.stringify(opts.body);
    opts.headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
  }
  const resp = await fetch(path, opts);
  if (!resp.ok) {
    let msg = resp.statusText;
    try { msg = (await resp.json()).detail || msg; } catch {}
    throw new Error(msg);
  }
  return resp.json();
}

function toast(msg, ms = 2600) {
  const el = document.createElement("div");
  el.className = "toast";
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), ms);
}

/* 格式化 */
const fmt = {
  num: (x, d = 2) => x == null ? "—" : Number(x).toLocaleString("zh-TW", { maximumFractionDigits: d }),
  int: x => x == null ? "—" : Math.round(x).toLocaleString("zh-TW"),
  pct: (x, d = 1) => x == null ? "—" : `${Number(x).toFixed(d)}%`,
  signPct: x => x == null ? "—" : `${x > 0 ? "+" : ""}${Number(x).toFixed(2)}%`,
  sign: (x, d = 2) => x == null ? "—" : `${x > 0 ? "+" : ""}${fmt.num(x, d)}`,
  yi: x => x == null ? "—" : `${(x / 1e8).toLocaleString("zh-TW", { maximumFractionDigits: 1 })} 億`,
  zhang: x => x == null ? "—" : `${Math.round(x / 1000).toLocaleString("zh-TW")} 張`,
  cls: x => x == null ? "" : x > 0 ? "up" : x < 0 ? "down" : "",
  /* 台幣金額：NT$1,250,000 */
  twd: x => x == null ? "—" : `NT$${Math.round(x).toLocaleString("zh-TW")}`,
  /* 大額數字改用萬/億，避免一長串數字難讀（如 12500000 → 1,250萬） */
  wan: x => {
    if (x == null) return "—";
    const n = Math.abs(x);
    if (n < 10000) return `${Math.round(x).toLocaleString("zh-TW")} 元`;
    // 未滿百萬時保留一位小數，否則 1.6 萬會被四捨五入成 2 萬、區間看起來一樣大
    if (n < 1e8) return `${(x / 1e4).toLocaleString("zh-TW",
      { maximumFractionDigits: n < 1e6 ? 1 : 0 })} 萬`;
    return `${(x / 1e8).toLocaleString("zh-TW", { maximumFractionDigits: 2 })} 億`;
  },
};

/* ECharts 暗色主題共用 */
const CHART_THEME = {
  backgroundColor: "transparent",
  textStyle: { color: "#8b93a3" },
  grid: { left: 56, right: 20, top: 30, bottom: 28 },
  UP: "#f0524f", DOWN: "#3fb26f", ACCENT: "#4f8ef7", WARN: "#e8a33d",
};

function makeChart(id) {
  const el = document.getElementById(id);
  const chart = echarts.init(el, null, { renderer: "canvas" });
  window.addEventListener("resize", () => chart.resize());
  return chart;
}

function baseOption(extra = {}) {
  return {
    backgroundColor: "transparent",
    textStyle: { color: "#8b93a3" },
    tooltip: { trigger: "axis", backgroundColor: "#1e222b", borderColor: "#2a2f3a",
               textStyle: { color: "#e6e9ef", fontSize: 12 } },
    grid: { left: 60, right: 24, top: 34, bottom: 30 },
    ...extra,
  };
}

function axisStyle() {
  return {
    axisLine: { lineStyle: { color: "#2a2f3a" } },
    axisLabel: { color: "#8b93a3" },
    splitLine: { lineStyle: { color: "#20242d" } },
  };
}

function qs(name) {
  return new URLSearchParams(location.search).get(name);
}

renderNav();
