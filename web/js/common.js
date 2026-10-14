/* 共用：導覽列、API 封裝、格式化、圖表主題 */

const NAV_GROUPS = [
  ["市場研究", [["index.html", "儀表板"], ["screener.html", "篩選器"],
    ["watchlist.html", "觀察名單"]]],
  ["策略研究", [["strategy.html", "策略實驗室"], ["strategy.html?optimization=home", "參數穩健性"],
    ["strategy.html?walkforward=home", "樣本外驗證"], ["portfolio.html", "策略投資組合"]]],
  ["個人決策", [["journal.html", "交易日誌"], ["finance.html", "財務規劃"]]],
];

const NAV_CARET = `<svg class="nav-caret" viewBox="0 0 10 6" width="10" height="6" aria-hidden="true"><path d="M1 1l4 4 4-4" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>`;

function renderNav() {
  const here = location.pathname.split("/").pop() || "index.html";
  const stage = here === "strategy.html" ? (qs("optimization") ? "參數穩健性" : qs("walkforward") ? "樣本外驗證" : "策略實驗室") : "";
  const current = here === "account.html" ? "帳號與存取" : here === "stock.html" ? "個股研究" :
    NAV_GROUPS.flatMap(group => group[1]).find(([href, label]) => href.split("?")[0] === here && (!stage || label === stage))?.[1] || "儀表板";
  const groupName = here === "account.html" ? "個人設定" : here === "stock.html" ? "市場研究" : NAV_GROUPS.find(([, links]) => links.some(([, label]) => label === current))?.[0] || "市場研究";
  const nav = document.createElement("nav");
  nav.className = "topbar";
  nav.setAttribute("aria-label", "全站導覽");
  nav.innerHTML = `
    <a class="brand" href="index.html">台股分析<span>研究卷宗</span></a>
    <span class="nav-current" aria-current="page">${groupName}<span aria-hidden="true"> / </span><strong>${current}</strong></span>
    <div class="nav-groups">${NAV_GROUPS.map(([name, links], i) =>
      `<div class="nav-group"><button type="button" class="nav-trigger" aria-expanded="false" aria-controls="nav-menu-${i}">${name}${NAV_CARET}</button><div class="nav-items" id="nav-menu-${i}" hidden>${links.map(([href, label]) =>
        `<a class="navlink ${current === label ? "active" : ""}" ${current === label ? 'aria-current="page"' : ""} href="${href}">${label}</a>`).join("")}${name === "市場研究" && here === "stock.html" ? `<span class="navlink active" aria-current="page">個股研究</span>` : ""}</div></div>`).join("")}</div>
    <div class="searchbox">
      <label class="sr-only" for="global-search">搜尋股票代號或名稱</label><input id="global-search" placeholder="搜尋股票，如 2330" autocomplete="off" aria-controls="search-results">
      <div class="search-results" id="search-results" role="listbox"></div>
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
      try {
        const rows = await api(`/api/stocks/search?q=${encodeURIComponent(q)}`);
        box.innerHTML = rows.map(r =>
          `<a role="option" href="stock.html?id=${encodeURIComponent(r.stock_id)}">
             <span class="sid">${r.stock_id}</span><span>${r.name}</span>
             <span class="mkt">${r.market === "twse" ? "上市" : "上櫃"}·${r.industry || ""}</span>
           </a>`).join("") || `<div class="muted">查無結果</div>`;
      } catch { box.innerHTML = `<div class="muted">搜尋暫時無法使用，請稍後重試。</div>`; }
      box.style.display = "block";
    }, 250);
  });
  document.addEventListener("click", e => {
    if (!nav.querySelector(".searchbox").contains(e.target)) box.style.display = "none";
  });
  input.addEventListener("keydown", e => {
    if (e.key === "Escape") box.style.display = "none";
    if (e.key === "Enter" && /^\d{4,6}$/.test(input.value.trim()))
      location.href = `stock.html?id=${input.value.trim()}`;
  });
  /* 導覽選單：點擊或滑鼠停留開啟；滑鼠移開、點擊外部、Esc、焦點離開皆關閉。 */
  const groups = [...nav.querySelectorAll(".nav-group")];
  const mobile = matchMedia("(max-width: 760px)");
  let openGroup = null, openedByHover = false, hoverTimer;
  const setOpen = (group, byHover = false) => {
    clearTimeout(hoverTimer);
    groups.forEach(g => {
      g.querySelector(".nav-trigger").setAttribute("aria-expanded", String(g === group));
      g.querySelector(".nav-items").hidden = g !== group;
    });
    openGroup = group; openedByHover = !!group && byHover;
  };
  groups.forEach(group => {
    const trigger = group.querySelector(".nav-trigger");
    trigger.addEventListener("click", () => {
      // 滑鼠停留已先開啟時，點擊是「留著」而不是「關閉」
      if (openGroup === group && !openedByHover) setOpen(null); else setOpen(group);
    });
    group.addEventListener("pointerenter", e => {
      if (e.pointerType !== "mouse" || mobile.matches) return;
      clearTimeout(hoverTimer);
      if (openGroup !== group) hoverTimer = setTimeout(() => setOpen(group, true), openGroup ? 0 : 90);
    });
    group.addEventListener("pointerleave", e => {
      if (e.pointerType !== "mouse" || mobile.matches) return;
      clearTimeout(hoverTimer);
      if (openGroup === group) hoverTimer = setTimeout(() => setOpen(null), 220);
    });
    group.addEventListener("focusout", e => {
      if (openGroup === group && e.relatedTarget && !group.contains(e.relatedTarget)) setOpen(null);
    });
    group.addEventListener("keydown", e => {
      if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
      e.preventDefault();
      if (openGroup !== group) setOpen(group);
      const items = [...group.querySelectorAll("a.navlink")];
      const at = items.indexOf(document.activeElement), step = e.key === "ArrowDown" ? 1 : -1;
      items[at < 0 ? (step > 0 ? 0 : items.length - 1) : (at + step + items.length) % items.length]?.focus();
    });
  });
  document.addEventListener("pointerdown", e => {
    if (openGroup && !openGroup.contains(e.target)) setOpen(null);
  });
  document.addEventListener("keydown", e => {
    if (e.key !== "Escape" || !openGroup) return;
    const trigger = openGroup.querySelector(".nav-trigger");
    const hadFocus = openGroup.contains(document.activeElement);
    setOpen(null);
    if (hadFocus) trigger.focus();
  });
  mobile.addEventListener("change", () => setOpen(null));
}

async function api(path, opts = {}) {
  opts = {...opts, headers: {...(opts.headers || {})}};
  if (!['GET', 'HEAD', 'OPTIONS'].includes((opts.method || 'GET').toUpperCase())) {
    const csrf = document.cookie.split('; ').find(s => s.startsWith('stocks_csrf='))?.slice(12);
    if (csrf) opts.headers['X-CSRF-Token'] = csrf;
  }
  if (opts.body && typeof opts.body !== "string") {
    opts.body = JSON.stringify(opts.body);
    opts.headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
  }
  const resp = await fetch(path, opts);
  if (resp.status === 401) location.replace('/login.html');
  if (!resp.ok) {
    let msg = resp.statusText;
    try { msg = (await resp.json()).detail || msg; } catch {}
    throw new Error(msg);
  }
  return resp.json();
}

const escapeHtml = value => String(value ?? "").replace(/[&<>"']/g, c =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function toastStack() {
  let stack = document.querySelector(".toast-stack");
  if (!stack) {
    stack = document.createElement("div");
    stack.className = "toast-stack";
    document.body.appendChild(stack);
  }
  return stack;
}

function toast(msg, ms = 2600) {
  const el = document.createElement("div");
  el.className = "toast";
  el.setAttribute("role", "status");
  el.textContent = msg;
  toastStack().appendChild(el);
  setTimeout(() => el.remove(), ms);
}

/* 失敗訊息不自動消失：使用者要有時間讀原因，再自己關掉。 */
function toastError(msg) {
  const stack = toastStack();
  const text = String(msg);
  if ([...stack.querySelectorAll(".toast.error span")].some(el => el.textContent === text)) return;
  const errors = stack.querySelectorAll(".toast.error");
  if (errors.length >= 3) errors[0].remove();
  const el = document.createElement("div");
  el.className = "toast error";
  el.setAttribute("role", "alert");
  el.innerHTML = `<strong>未完成</strong><span></span><button type="button" class="ghost small">關閉</button>`;
  el.querySelector("span").textContent = text;
  el.querySelector("button").addEventListener("click", () => el.remove());
  stack.appendChild(el);
}

/* 最後防線：沒有自行處理錯誤的非同步操作，失敗時仍要讓使用者看得到。 */
window.addEventListener("unhandledrejection", e => {
  toastError(e.reason?.message || String(e.reason || "操作失敗，請稍後重試"));
});

/* Shared result identity strip: provenance stays visible before any metric. */
function researchMetadata(items) {
  return `<dl class="research-metadata">${items.map(([name, value]) =>
    `<div><dt>${escapeHtml(name ?? "—")}</dt><dd>${escapeHtml(value ?? "—")}</dd></div>`).join("")}</dl>`;
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

/* ECharts 共用主題：顏色只讀 CSS token，避免圖表另有一份品牌色票。 */
function cssVar(name, fallback = "") {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
}

const CHART_THEME = {
  backgroundColor: "transparent",
  textStyle: { color: cssVar("--muted", "#5b5e66") },
  grid: { left: 56, right: 20, top: 30, bottom: 28 },
  UP: cssVar("--up", "#a63732"), DOWN: cssVar("--down", "#247048"),
  ACCENT: cssVar("--accent", "#555d7a"), WARN: cssVar("--warn", "#835516"),
  MUTED: cssVar("--muted", "#5b5e66"), TEXT: cssVar("--text", "#2b2d31"),
  PANEL2: cssVar("--panel2", "#e9ecee"), BORDER: cssVar("--border", "#cdd2d4"),
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
    textStyle: { color: CHART_THEME.MUTED },
    tooltip: { trigger: "axis", backgroundColor: CHART_THEME.PANEL2,
               borderColor: CHART_THEME.BORDER,
               textStyle: { color: CHART_THEME.TEXT, fontSize: 12 } },
    grid: { left: 60, right: 24, top: 34, bottom: 30 },
    ...extra,
  };
}

function axisStyle() {
  return {
    axisLine: { lineStyle: { color: CHART_THEME.BORDER } },
    axisLabel: { color: CHART_THEME.MUTED },
    splitLine: { lineStyle: { color: CHART_THEME.BORDER, opacity: .6 } },
  };
}

function qs(name) {
  return new URLSearchParams(location.search).get(name);
}

renderNav();

window.accountReady = api('/api/auth/me').then(user => {
  document.querySelectorAll('[data-admin]').forEach(el => { el.hidden = user.role !== 'admin'; });
  const link = document.createElement('a'); link.href = '/account.html'; link.className = 'account-nav';
  link.innerHTML = '<svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><circle cx="12" cy="12" r="9.25"/><circle cx="12" cy="9" r="3"/><path d="M5.4 18.4a6.8 6.8 0 0 1 13.2 0"/></svg>';
  link.title = '帳號與存取'; link.setAttribute('aria-label', `帳號與存取：${user.display_name}`);
  if (location.pathname.endsWith('/account.html')) link.setAttribute('aria-current', 'page');
  document.querySelector('.topbar')?.append(link);
  return user;
});
window.addEventListener('pageshow', event => { if (event.persisted) location.reload(); });
