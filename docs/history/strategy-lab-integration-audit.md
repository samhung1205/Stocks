# Strategy Lab / Backtesting Engine / Portfolio Lab — 整合稽核報告

> Stage A 交付物（spec §0、§80）。本文件只做分析與資訊架構規劃，**未修改任何程式碼**。
> 稽核日期：2026-09-19｜稽核對象：commit `c13a88f` 之工作樹（另有未追蹤的 `PRODUCT.md`、`.impeccable/`）

---

## 摘要：三個必須先解決的結構性問題

在談 UI 與引擎設計之前，稽核發現三件事會直接決定這個功能能不能成立。它們都不是「寫程式」問題，是資料與 schema 問題：

| # | 問題 | 現況 | 影響 |
|---|---|---|---|
| 1 | **全市場歷史深度只有 7 個月** | `daily_quotes` 有 1,223 個日期，但其中只有 **121 天**是全市場（>1000 檔），範圍 `2026-02-03 ~ 2026-09-18`。其餘 1,102 天是深度層逐檔回補的 20~30 檔 | 橫斷面選股回測（Top N、因子排序）目前**最多只能跑 7 個月**。五套 Strategy Template 全部需要多年資料 |
| 2 | **沒有任何 publication date** | `month_revenue` 只有 `ym`、`financials` 只有 `quarter`。兩張表都沒有「這筆資料何時公布」 | 直接違反 spec §8/§9 的 No Look-Ahead。現在寫任何用到營收/財報的策略，都會用到當下還不存在的資訊 |
| 3 | **`valuations` 全市場只有 5 天** | 9,841 列 / 5 個日期（`2026-07-31`~`2026-09-18`）。因為 `update_daily()` 用的 `valuations_latest()` 是「最新快照」端點，不是逐日回補 | Value × Quality 策略（spec §33 Strategy 4）的 P/E、P/B、殖利率**沒有歷史可回測** |

好消息是三個都有解，而且不需要付費資料源 — 詳見 §5「資料假設與缺口」。

---

# 1. 現有頁面架構與 Routing

## 1.1 Routing 模型

沒有前端 router。`app/main.py:34` 把 `web/` 整個目錄用 `StaticFiles(html=True)` 掛在 `/`，每個頁面是一個獨立 HTML 檔，靠 `<a href="xxx.html">` 與 `?id=` query string 導航。

| URL | 檔案 | 角色 | 行數 |
|---|---|---|---|
| `/` `/index.html` | `web/index.html` | 儀表板：大盤、法人、市場寬度、觀察名單速覽、資料維護 | 187 |
| `/screener.html` | `web/screener.html` | 全市場篩選器 | 168 |
| `/watchlist.html` | `web/watchlist.html` | 觀察名單三級 + 準即時報價 | 106 |
| `/journal.html` | `web/journal.html` | 交易日誌：八句話計畫、持倉、績效 | 256 |
| `/finance.html` | `web/finance.html` + `web/js/finance.js` | 財務規劃 / ETF 長期投資 | 213 + 672 |
| `/stock.html?id=2330` | `web/stock.html` | 個股頁（六個 tab + 右欄評分） | 499 |

**對 Strategy Lab 的意義**：新增頁面等於新增一個 `.html` 檔加一行 NAV 設定，成本極低、不需要 build step。但也代表**沒有 code splitting、沒有共用元件機制** — 見 §12 技術債。

## 1.2 後端 Routing

`app/api/routes.py`（313 行）是唯一的 REST 層，`APIRouter(prefix="/api")`。分六組：

```
/api/market/dashboard            儀表板
/api/stocks/{id}/...             個股（overview/kline/revenue/financials/chips/valuation/technical/alerts/score/summary）
/api/screener/presets|run        篩選器
/api/watchlist                   觀察名單 CRUD
/api/journal/...                 交易日誌
/api/finance/...                 財務規劃（universe/simulate/profiles/refresh-etf）
/api/admin/...                   資料維護（sync-stocks/update-daily/backfill/fetch-revenue/status）
```

路由層刻意保持薄：只做參數解析 + 呼叫 service，邏輯全在 `app/services/`。這個慣例必須延續。

---

# 2. 現有功能盤點

## 2.1 股票分析

| 功能 | 實作位置 | 可被 Strategy Lab 重用的程度 |
|---|---|---|
| 個股資料組裝（K線/營收/財報/籌碼/估值/技術） | `app/services/stock_view.py` | **高** — `financial_series()` 已算好 ROE(TTM)、負債比、FCF、三率；這些正是 Quality 因子的原料。但它是**單檔查詢**，回測需要橫斷面版本 |
| 100 分評分表 | `app/services/scoring.py` | **中** — `auto_scores()` 的計分邏輯（營收 YoY→分數、OCF/淨利→分數、估值百分位→分數）在概念上就是因子標準化。但它用的是「主觀刻度」而非 cross-sectional rank，不能直接當因子 |
| 警訊引擎 | `app/services/alerts.py` | **中** — 可以當作策略的排除條件（filter），但目前也是單檔 |
| 篩選器 | `app/services/screener.py` | **高（關鍵）** — 見下方專節 |

### `screener.py` 是 Backtest Engine 最接近的前身

`screener.run()` 已經做到了單一時點的橫斷面掃描：

- 一次 SQL 撈全市場最新一日的收盤 + 估值（`screener.py:44-52`）
- 批次撈月營收最近 3 個月（`screener.py:55-62`）
- 批次撈法人 N 日累計（`screener.py:65-78`）
- **一次 SQL 撈 370 天全市場歷史，在 Python 裡用 group-by-stock 迴圈算 MA60/MA240/52週高/量比**（`screener.py:81-105`）

最後一段尤其重要：它已經示範了「一次撈全市場長窗歷史 → 在記憶體內逐檔計算指標」的模式，而這正是回測引擎每個 rebalance date 要做的事。差別只有兩點：

1. 篩選器只算**最後一天**；回測要算**每一天**（或每個再平衡日）
2. 篩選器用 `params.get(...)` 硬編條件；回測需要可組合的 Factor / Rule 物件

**結論**：Backtest Engine 應該視為 `screener.py` 的「時間軸泛化版」，不是另起爐灶。`PRESETS`（`screener.py:22-39`）的寫法也直接對應 spec §33 的 Strategy Template 常數表。

## 2.2 ETF 功能

`app/services/finance/etf_stats.py`（193 行）— 這是全專案**唯一已經存在的績效計算程式碼**，必須詳讀：

- `compute(prices, divs)` 已經算出：**含息年化（CAGR）、價格年化、年化波動（log return × √252）、最大回撤、總報酬**
- `split_factors(closes)` 用「單日 ratio 超出 [0.55, 1.8] 即判定為公司行動」的啟發式偵測分割，並回推還原係數
- 股利再投入模型：`shares *= 1 + dividend / close`
- 刻意拆成 `stats()`（碰 DB）與 `compute()`（純函式）兩層，方便離線單測

**對 Backtest Engine 的意義**：

- ✅ CAGR / 波動 / MDD 的公式與慣例（252 交易日、log return、樣本變異數 n-1）**直接沿用**，不要在 backtest 裡寫第二套
- ⚠️ `split_factors()` 的啟發式**不該進回測引擎** — 它是「沒有公司行動資料時的補救」。回測需要真正的公司行動表（見 §5.3），否則 ±10% 漲停板附近的正常行情會被誤判
- ✅ `refresh()` 示範了「不要為了抓兩個 dataset 而呼叫 `deep_fetch.ensure_deep()`」的正確做法 — Strategy Lab 補資料時同樣要走專用路徑

## 2.3 個人財務試算

`app/services/finance/` 九個模組，**是整個專案的架構範本**，Strategy Lab 應該照抄這個分層：

```
純計算引擎（不 import app.db、吃 dict 吐 dict、可離線單測）
  universe.py     人工維護的常數表 + metadata
  health.py       財務健檢
  risk.py         五題問卷
  allocation.py   角色權重 → 標的權重
  compound.py     複利 / 三情境 / 通膨 / 里程碑
  retirement.py   退休缺口 / 提款模擬 / SoRR
  rebalance.py    現金流式再平衡
碰 DB 的兩層
  etf_stats.py    從 daily_quotes + dividends 算歷史
  service.py      profile CRUD + 結果組裝
```

`compound.py` 的 docstring 寫法值得完全照搬到 backtest 模組：**把公式直接寫在 docstring 裡，並註明「封閉解與逐月迴圈必須一致，smoke test 會交叉驗證」**。這正是 spec §72 要求的 sanity check 精神。

### 三個已確立的設計約束（Strategy Lab 必須遵守同樣邏輯）

1. **試算無狀態**：`POST /api/finance/simulate` 吃完整 payload、吐完整結果。→ 但 **Backtest 不能照抄這條**，因為回測是長時間運算，需要持久化 + cache（spec §73、§74）。差異要明確。
2. **歷史值與假設值必須分開呈現**：`etf_stats` 是歷史、使用者設定的是假設，UI 不混用。→ 對應 spec §66/§67「Historical vs Projected」。
3. **人工維護的常數要標明是人工維護的**：`universe.py` 的 metadata_note。→ Strategy Template 的預設參數同理。

## 2.4 現有 Dashboard

`web/index.html` 目前有四張 KPI card（加權指數、成交金額、三大法人、市場寬度）+ 兩張圖（指數走勢、觀察名單表）+ 一張「資料維護」卡。

**評估**：這張頁面已經接近 spec §42 的上限。「資料維護」那一區是操作面板不是資訊面板，佔了整個頁面底部。若要加 Research Snapshot（spec §42：Saved Strategies / Last Backtest / Portfolio / OOS Status），建議：

- 新增**一列**四格 snapshot，放在 KPI card 下方、走勢圖上方
- **不要**在 Dashboard 放任何 equity curve 或回測圖表
- 「資料維護」卡可考慮改成 `<details>` 收合（因為 Strategy Lab 會再加「補齊回測歷史」按鈕，否則這區會繼續膨脹）

## 2.5 現有 Navbar

`web/js/common.js:3-9` 的 `NAV` 常數 — 五個項目 + 右側全域搜尋框。**只有 Top Nav，沒有 Sidebar**。

```js
const NAV = [
  ["index.html", "儀表板"], ["screener.html", "篩選器"],
  ["watchlist.html", "觀察名單"], ["journal.html", "交易日誌"],
  ["finance.html", "財務規劃"],
];
```

spec §43 明確要求：**現有是 Top Nav 就沿用 Top Nav，不要為了新功能同時生出 Top Nav + Sidebar + Tabs 三層導覽**。加兩個項目後是 7 個 + 搜尋框，`@media (max-width:720px)` 已有換行處理（`app.css:44-51`），桌面版 1280px 寬度容納 7 個 navlink 綽綽有餘。

---

# 3. Design System 稽核

## 3.1 Design Token（`web/css/app.css:1-12`）

**已經有一層真正的 token，而且只有一層**，全部在 `:root`：

```css
--bg:#0f1115  --panel:#171a21  --panel2:#1e222b  --border:#2a2f3a
--text:#e6e9ef  --muted:#8b93a3
--up:#f0524f   /* 台股慣例：紅漲 */   --down:#3fb26f  /* 綠跌 */
--accent:#4f8ef7  --warn:#e8a33d
```

**缺什麼**：沒有 spacing token、沒有 radius token、沒有 font-size token。這些目前是散落的字面值（`gap:14px`、`border-radius:12px`、`font-size:22px`）。

**建議**：spec §78 說「如果現有 Design 本身不一致，先建立最小 Design Token Layer，但不要藉機整站 Redesign」。實際觀察下來字面值其實相當一致（radius 8/10/12、gap 8/10/14、font 11/12/13/14/16/22），所以**只需補一組 token 並在新程式碼使用，既有規則不動**：

```css
--sp-1:4px --sp-2:8px --sp-3:10px --sp-4:14px --sp-5:16px --sp-6:20px
--r-sm:8px --r-md:10px --r-lg:12px
--fs-xs:11px --fs-sm:12px --fs-base:14px --fs-lg:16px --fs-xl:22px
```

## 3.2 Typography

- Font stack：`"PingFang TC", "Noto Sans TC", "Microsoft JhengHei", sans-serif`（`app.css:14-16`），base 14px
- Scale：`h1` 22px / `h2` 16px / body 14px / muted 12px / meta 11px
- **沒有等寬字體** — 這是 Strategy Lab 的一個實際缺口。Metrics table、Parameter Heatmap、Trade Log 的數字欄位需要 tabular figures 才能對齊。建議新增 `--font-mono` 並在 `table.data td` 加 `font-variant-numeric: tabular-nums`（單一 CSS 規則，不算 redesign）

## 3.3 Color Token 與 spec §47 的衝突

`--up` 是**紅色**、`--down` 是**綠色**（台股慣例）。但 Strategy Lab 顯示的是**策略報酬**，不是台股漲跌。

這裡有真實的語意衝突：
- 台股使用者看到紅色 `+15%` 會直覺理解為「漲」→ 正確
- 但 Drawdown chart 的紅色區域、Correlation matrix 的紅色格子在國際量化慣例裡是「壞」

**財務規劃頁已經處理過同一個問題**，`app.css:189` 的註解寫得很清楚：

> 刻意不用紅綠表示好壞：財務規劃沒有「漲跌」，用 accent/warn/灰階即可

**建議**：Strategy Lab 沿用財務規劃頁的決定。
- 報酬數字：延用 `--up`/`--down`（因為它確實是漲跌）
- Drawdown / Correlation / Heatmap：用 `--accent` ↔ `--warn` 的雙色階，不用紅綠
- 並依 spec §47 同時附 `+` / `−` 符號，不只靠顏色（Accessibility）

## 3.4 可重用元件清單

CSS class 即元件（無 JS 元件系統）：

| Class | 用途 | Strategy Lab 可否直接用 |
|---|---|---|
| `.card` | 面板容器 | ✅ 直接用 |
| `.grid .cols-2/3/4` | 響應式網格（<900px 塌成單欄） | ✅ KPI Row 用 `.cols-3` × 2 列或 `.cols-4`（注意 spec §44 只要 6 個 KPI） |
| `.card .k` / `.card .v` | KPI 標籤 + 數值 | ✅ **直接對應 spec §44 的 KPI Row** |
| `table.data` | 資料表（**預設右對齊**、`:first-child` 與 `.l` 左對齊、hover、`.sortable` 排序） | ✅ **完全符合 spec §47**，含排序狀態 `.active` |
| `.scroll-x` | 寬表水平捲動 | ✅ Trade Log / Walk Forward 表格必用 |
| `.tabs` + `.tabs button.active` | 底線式 tab | ✅ **直接對應 spec §45 的結果頁 Tabs** |
| `.badge` / `.badge.warn` | 標籤 | ⚠️ 可用但 spec §3 警告「避免所有東西都加 Badge」。只用在 OOS Status 與 Robustness Warning |
| `.fin-alert` / `.fin-alert.info` | 左邊框警示框（warn / accent 兩色） | ✅ **直接對應 spec §62 Robustness Warning、§50 Error State** |
| `.fin-kv` | 左標籤右數值列 | ✅ Performance Metrics Table 的輕量版 |
| `.fin-sum .b` | 自動排列的小型統計方塊（`minmax(150px,1fr)`） | ✅ 比 `.card` 更適合密集 KPI |
| `.fin-steps` + `.fin-step` | **Step Workflow（藥丸式步驟列 + display:none 切換）** | ✅ **直接對應 spec §6 的 8 步 Strategy Builder** |
| `details.fin-more` | 收合進階內容 | ✅ **直接對應 spec §6 的 Accordion / Advanced Mode** |
| `.score-row` / `.score-bar` | 帶進度條的評分列 | ✅ **直接對應 spec §32 Historical Robustness 的 100 分拆解** |
| `.toast` | 浮動提示 | ✅ |
| `.fin-pill` / `.fin-pill.on` / `.fin-pill.hold` | 狀態藥丸 | ✅ OOS Status 用 |

**重點結論**：spec §6（Step Workflow）、§32（分數拆解）、§45（Tabs）、§47（表格右對齊）、§62（Warning）**在現有 CSS 裡全部已經有對應元件**。Strategy Lab 幾乎不需要新的視覺語彙，只需要把 `.fin-*` 前綴的那幾個從「財務規劃專用」提升為共用（或複製成 `.lab-*`）。

建議做法：把 `.fin-steps`/`.fin-step`/`.fin-alert`/`.fin-kv`/`.fin-sum`/`.fin-pill`/`details.fin-more` 這七組**改名為無前綴的共用元件**（`.steps`/`.step`/`.alert`/`.kv`/`.statgrid`/`.pill`/`details.more`），在 `.fin-*` 保留 alias 以免動到 finance.js。這是 spec §78 允許的「最小 Design Token Layer」，不是 redesign。

---

# 4. State Management / API / 資料層

## 4.1 State Management

**沒有狀態管理框架，也沒有全域 store。** 模式是：

- 每頁一個 IIFE-less 的 inline `<script>`，狀態就是那支 script 的頂層 `let` 變數
- `web/js/finance.js` 是唯一獨立的 JS 檔（672 行），用一個模組級 `let profile = {...}` 當作單一狀態來源，任何輸入變動 → 更新 `profile` → debounce → `POST /api/finance/simulate` → 重繪
- 沒有 localStorage，沒有 URL state（除了 `stock.html?id=`）
- 共用工具在 `web/js/common.js`：`api()`（fetch 封裝 + 錯誤轉 `Error(detail)`）、`fmt`（數字格式化）、`toast()`、`makeChart()`/`baseOption()`/`axisStyle()`、`qs()`

**對 Strategy Lab 的意義**：`finance.js` 的「單一 state 物件 + debounce + 整包重算」模式可以照抄給 Strategy Builder（表單 state）。但**回測結果不能照抄** — 它必須持久化（spec §7 版本、§56 可重現、§73 cache）。

## 4.2 資料源層（`app/datasources/`）

`base.py` 提供：單例 `httpx.Client`、**依 host 的禮貌限速**（`config.POLITE_INTERVAL`）、4xx 不重試 / 429+5xx 退避重試、`cache_ttl` 記憶體快取、`num()`/`roc_to_iso()` 清洗。

| 模組 | 來源 | 給 Strategy Lab 的價值 |
|---|---|---|
| `twse.py` | 證交所 OpenAPI + 官網 rwd JSON | **`daily_all_by_date(yyyymmdd)` 是全市場歷史回補的唯一管道** |
| `tpex.py` | 櫃買 OpenAPI + www JSON | 同上（上櫃） |
| `mops.py` | 公開資訊觀測站 Big5 HTML | 一次拿整月全市場月營收 |
| `finmind.py` | FinMind v4 REST | 個股深度：價量、月營收、三大財報、法人、融資券、股利 |
| `mis_snapshot.py` / `fugle.py` / `realtime_base.py` | 即時報價（可插拔 Provider） | 回測用不到 |

**限速成本試算**（回測歷史回補的真實代價）：

`market_daily.backfill_history()` 每個交易日打 5 個請求：TWSE MI_INDEX(2s) + T86(2s) + MI_INDEX/IND(2s) + TPEx dailyQuotes(2s) + TPEx dailyTrade(2s)。兩個 host 的限速各自獨立但請求是序列的 → 約 **10 秒 / 交易日**。

| 目標 | 交易日數 | 預估時間 |
|---|---|---|
| 1 年 | ~245 | ~40 分鐘 |
| 3 年 | ~735 | ~2 小時 |
| **5 年** | **~1,225** | **~3.5 小時** |

可中斷續跑（已入庫日期自動跳過，`market_daily.py:112-118`）。這是一次性成本，可接受，但**必須在 UI 明講**，不能讓使用者按下按鈕後以為是 30 秒的事。

## 4.3 Database Schema

SQLAlchemy **Core**（非 ORM）+ SQLite WAL。`app/db/tables.py` 是唯一 schema 來源。所有寫入走 `db/__init__.py:upsert_many()`（依主鍵 upsert，**只更新該批實際帶有的欄位**，避免把舊值洗成 NULL）。查詢多用 `query_all`/`query_one` + 原生 `text()` SQL。

### 現有 16 張表與實測資料量

| 表 | 主鍵 | 列數 | 實際覆蓋 |
|---|---|---|---|
| `stocks` | stock_id | 2,719 | 2,452 股票 + 267 ETF。**只有目前在市的** |
| `daily_quotes` | (stock_id, date) | 332,289 | 1,223 個日期，但**全市場只有 121 天**（2026-02-03~09-18） |
| `valuations` | (stock_id, date) | 9,841 | **只有 5 個日期** |
| `institutional_daily` | (stock_id, date) | 270,687 | 全市場 119 天（同期） |
| `market_summary` | date | 121 | 大盤指數（**價格指數，非 Total Return**） |
| `month_revenue` | (stock_id, ym) | 23,933 | **72 個月 × 1,852 檔 — 這是唯一有多年深度的全市場資料** |
| `financials` | (stock_id, quarter) | 553 | **只有 29 檔**（深度層按需） |
| `margin_daily` | (stock_id, date) | 7,494 | 深度層 |
| `dividends` | (stock_id, date) | 533 | 深度層。**只有合併後的 `dividend`（現金+股票）** |
| `deep_fetch_log` | (stock_id, dataset) | 155 | 31 檔 × 5 dataset |
| `watchlist` / `scores` / `trade_plans` / `trades` | — | 3 / 0 / 0 / 0 | 使用層 |
| `finance_profiles` / `finance_targets` | — | 2 / 26 | 財務規劃 |

### 🔴 索引：沒有任何自訂索引

```
sqlite_master 裡只有 13 個 sqlite_autoindex_*（全部是 PK 自動索引）
EXPLAIN QUERY PLAN SELECT stock_id, close FROM daily_quotes WHERE date='2026-09-18'
  → SCAN daily_quotes          ← 全表掃描
```

`daily_quotes` 的 PK 是 `(stock_id, date)`，前綴是 `stock_id`，所以**任何以 date 為條件的橫斷面查詢都走全表掃描**。目前 33 萬列還撐得住；補完 5 年全市場後會是 **~300 萬列**，而回測要對每個再平衡日做一次這種查詢。

**必須新增**（Stage B 第一件事）：
```sql
CREATE INDEX idx_quotes_date ON daily_quotes(date);
CREATE INDEX idx_insti_date  ON institutional_daily(date);
CREATE INDEX idx_val_date    ON valuations(date);
CREATE INDEX idx_revenue_ym  ON month_revenue(ym);
```

### 🔴 沒有 migration 機制

`db/__init__.py:init_db()` 只做 `metadata.create_all(engine)`。SQLAlchemy 的 `create_all` **只建立不存在的表，不會對既有表 ALTER**。

這代表：**在 `tables.py` 給 `month_revenue` 加一個 `publish_date` 欄位，現有的 `data/stocks.db` 不會有那個欄位，而且不會報錯 — 直到查詢時才炸。**

Strategy Lab 必然要改既有表（加 publish date、加公司行動表）。所以 **Stage B 必須先做一個最小 migration 機制**。建議做法（符合現有極簡風格，不引 Alembic）：

```python
# app/db/__init__.py
def init_db():
    metadata.create_all(engine)
    _apply_column_migrations()   # 比對 PRAGMA table_info，缺欄位就 ALTER TABLE ADD COLUMN
```

SQLite 的 `ALTER TABLE ... ADD COLUMN` 是 O(1) 且安全的，涵蓋 90% 的需求。

## 4.4 Financial calculation utilities（現有公式清單）

| 公式 | 位置 | 回測是否重用 |
|---|---|---|
| CAGR、年化波動、最大回撤、含息總報酬 | `finance/etf_stats.py:compute()` | ✅ **直接重用慣例**（252 日、log return、n-1 變異數） |
| 複利 FV（單筆 / 年金）、三情境、通膨折現、里程碑 | `finance/compound.py` | 🔸 Financial Planner 整合時用（Stage I） |
| 退休缺口、提款模擬、SoRR | `finance/retirement.py` | ❌ spec §5 明確禁止在 Strategy Lab 塞退休模擬 |
| 角色權重 → 標的權重、重疊/國家/資產類別提示 | `finance/allocation.py` | 🔸 Portfolio Lab 的 Equal Weight 可參考其權重正規化寫法 |
| 現金流式再平衡、drift 閾值 | `finance/rebalance.py` | 🔸 Portfolio Lab 的再平衡可參考 |
| **交易成本：手續費 0.1425% × 折扣、證交稅 0.3%（最低手續費 1 元）** | `services/journal.py:add_trade()` + `config.py:FEE_RATE/FEE_DISCOUNT/TAX_RATE` | ✅ **Cost Model 的基準必須與這裡一致**，否則日誌與回測的成本假設會分岔 |
| 勝率、平均賺賠、盈虧比（Profit Factor） | `services/journal.py:performance()` | ✅ **spec §16 要求的 Win Rate / Profit Factor / Average Win / Average Loss 已有實作**，回測的 Trade-level metrics 應沿用同一份定義 |
| 平均成本法持倉、已實現損益 | `services/journal.py:positions()/performance()` | ✅ Trade Log 的 exit 會計方式應與此一致 |
| 歷史百分位排名 | `services/stock_view.py:valuation_band().pct_rank()` | ✅ 因子 percentile rank 的既有寫法 |
| MA/52週高低/量比 | `services/screener.py`（全市場）、`stock_view.technical_summary()`（單檔） | ✅ Momentum / Trend filter 的原料 |

**關鍵發現**：spec §16 要求的 20 個 metrics 裡，**已經有 9 個在專案內有可信的實作**（Total Return、CAGR、Volatility、Max Drawdown、Win Rate、Profit Factor、Average Win、Average Loss、Number of Trades）。真正要新寫的是 Sharpe、Sortino、Calmar、Beta、Alpha、Expectancy、Recovery Time、Turnover、Average Holding Period、Gross/Net Return 拆分。

## 4.5 Chart Library

**Vendored ECharts**（`web/vendor/echarts.min.js`，1.06 MB），`<script src>` 全域載入。共用封裝在 `common.js:105-134`：

```js
CHART_THEME = { backgroundColor:"transparent", textStyle:{color:"#8b93a3"},
                grid:{...}, UP:"#f0524f", DOWN:"#3fb26f", ACCENT:"#4f8ef7", WARN:"#e8a33d" }
makeChart(id)      // echarts.init + window resize 綁定
baseOption(extra)  // 暗色 tooltip + grid
axisStyle()        // 軸線/標籤/分隔線顏色
```

**ECharts 對 spec §48 所需圖表的覆蓋**：

| spec 要求 | ECharts 支援 | 備註 |
|---|---|---|
| Equity Curve（Linear / Log） | ✅ `yAxis.type:'log'` 一行切換 | |
| Drawdown（負值面積） | ✅ line + areaStyle | |
| Rolling Performance | ✅ | |
| Calendar Return Matrix | ✅ `type:'heatmap'` + category 雙軸 | |
| **Parameter Heatmap（spec §26）** | ✅ `type:'heatmap'` + `visualMap` | 已內建於 vendored bundle？**需確認** — 見下 |
| Correlation Matrix | ✅ 同上 | |
| Tooltip 顯示 Date / Portfolio / Benchmark / Difference | ✅ `tooltip.formatter` | |

⚠️ **待驗證項**：vendored 的 `echarts.min.js` 若是 custom build，可能不含 `heatmap` 與 `visualMap`。Stage B 開始前應先在瀏覽器 console 確認 `echarts.getMap` 級別的模組可用性，或直接試渲染一張 heatmap。若缺，換成完整 dist build（檔案大一點，但本機運行無所謂）。

🔴 **Theme 硬編問題（spec §53）**：`CHART_THEME`、`baseOption()`、`axisStyle()` 把 `#8b93a3`、`#2a2f3a`、`#1e222b` 直接寫死在 JS 裡，與 CSS 變數是兩份真實來源。spec §53 要求「新增 Chart 必須 Theme-aware，不要硬編背景色/文字色/網格色」。

目前**平台只有暗色主題，沒有 light mode**，所以現況不算 bug。但 Strategy Lab 會大量新增圖表，硬編會讓未來加 light mode 變成改 N 個檔案。**建議（低成本）**：在 `common.js` 加一個 `cssVar(name)` helper 從 `getComputedStyle(document.documentElement)` 讀 token，讓 `CHART_THEME` 改為在載入時求值。既有圖表不動，新圖表一律用 token。

---

# 5. 資料假設與缺口（Backtest 可行性判定）

這一節是整份稽核最重要的部分。spec §8/§9/§10/§11 的四大原則，逐條對照現況：

## 5.1 🔴 Point-in-Time / Look-Ahead（spec §8、§9）— 目前完全不合規

### 月營收

`month_revenue` 的主鍵是 `(stock_id, ym)`，`ym` 是**營收所屬月份**，不是公布日。按 spec §9 的例子，2025 年 1 月營收若 2 月 10 日公布，策略最早只能在 2 月 10 日之後使用 — 但目前資料庫裡沒有任何欄位能表達這件事。

**✅ 有解，而且是精確解**：實測 FinMind `TaiwanStockMonthRevenue` 回傳含 **`create_time`** 欄位：

```json
{"date":"2026-05-01","stock_id":"2330","revenue":410725118000,
 "revenue_month":4,"revenue_year":2026,"create_time":"2026-05-08"}
```

`create_time` = 2026-05-08 即 4 月營收的實際入庫/公布日。**這是真正的 publication date**，正是 spec §9 要求的東西。目前 `datasources/finmind.py:month_revenue()` 把它丟掉了（只取 stock_id / ym / revenue）。

**但**：MOPS 的全市場彙總表（`mops.monthly_revenue_all()`）沒有逐家公布日。所以：

| 來源 | publish date | 用途 |
|---|---|---|
| FinMind（逐檔） | ✅ `create_time` 精確值 | 深度層、回測 universe 內的個股 |
| MOPS（全市場彙總） | ❌ 無 | 只能用**保守 fallback：`ym` 次月 10 日**（法定申報期限） |

建議 schema：`month_revenue` 加 `publish_date TEXT` + `publish_source TEXT`（`'actual'` / `'assumed_deadline'`），UI 上對 assumed 的部分標示。這符合 spec §67「必須區分 Actual / Assumed」。

### 財報

`financials` 主鍵 `(stock_id, quarter)`，同樣沒有公布日。FinMind `TaiwanStockFinancialStatements` 的 `date` 是**期末日**（`2026-06-30`），不是公布日 — 實測確認，且無其他欄位可用。

**只能用 fallback lag 規則**（台灣法定申報期限）：

| 季別 | 法定申報期限 | 回測可用日 |
|---|---|---|
| Q1 | 5/15 | 次年度 5/15 |
| Q2 | 8/14 | 8/14 |
| Q3 | 11/14 | 11/14 |
| Q4（年報） | 次年 3/31 | 次年 3/31 |

這是**假設而非事實**（實際多數公司提前公布），必須在 UI 標示為 `Assumed`，且做成 configurable（spec §63 的門檻可設定精神）。

### 估值（PE/PB/殖利率）

`valuations` 只有 5 天，形同不存在。

**✅ 有解**：實測 FinMind `TaiwanStockPER` 可用（免費 register 層級），逐檔回傳每日 PER / PBR / dividend_yield：

```json
{"date":"2026-08-03","stock_id":"2330","dividend_yield":0.93,"PER":31.86,"PBR":10.43}
```

這是**已經 point-in-time 的日頻資料**（當日估值用當日已知的股價與最近已公布的財報算出），沒有 look-ahead 問題。缺點：逐檔查詢，補齊 universe（例如 800 檔流動性足夠的股票）× 5 年需要 800 次請求 × 0.4s ≈ 6 分鐘 — 完全可接受。

另一條路是用 `daily_quotes.close` ÷ `financials.eps`(TTM, 套 publish lag) 自算，好處是 lag 規則統一、壞處是要自己處理股本變動。**建議兩者都存**：FinMind PER 當主值、自算值當交叉驗證（spec §72 sanity check 精神）。

## 5.2 🟠 Survivorship Bias（spec §10）— 有部分解

`stocks` 表只有**目前在市**的 2,719 檔，且沒有上市日 / 下市日欄位。`sync_stock_list()` 用的 `TaiwanStockInfo` 也只給現況。

**✅ 部分有解**：實測兩個 FinMind dataset 可用（免費層級）：

1. **`TaiwanStockDelisting`** — 725 列，格式 `{"date":"2023-04-21","stock_id":"00732","stock_name":"國泰RMB短期報酬"}`。給出**下市日期**。
2. **`TaiwanStockInfoWithWarrant`** — 200,158 列，含 `date` 欄位，範圍涵蓋已下市標的。

**能做到什麼**：
- 在 `stocks` 加 `listed_date` / `delisted_date`，universe 選股時以 `date BETWEEN listed_date AND COALESCE(delisted_date, '9999')` 過濾 → **消除「用今天的在市名單回測過去」這個最大的 survivorship 來源**
- 下市股的歷史價格：`daily_all_by_date()` 是按日期抓全市場，**下市前的資料本來就會被抓到**，所以只要回補歷史，下市股的價格自然在庫裡

**做不到什麼**：
- 下市當下的清算價值 / 最後成交價之後的處理沒有資料 → 引擎需要一個明確的「下市處置規則」（例如：下市日以最後收盤價全數出清，並標記為 `exit_reason='delisted'`）
- 完整的**歷史指數成分股**（例如 2018 年的台灣 50 成分股）免費源拿不到 → spec §10 說「如果資料來源無法完整支援，UI 必須清楚標示 Survivorship Bias Warning」。**這條必須做**

**結論**：可達到「以全市場 + 上市/下市日期過濾」的水準，優於絕大多數個人回測工具，但達不到「歷史指數成分股」水準。UI 必須誠實標示等級。

## 5.3 🟠 Corporate Actions（spec §11）— 有解但需新表

`dividends` 表目前只有一個 `dividend` 欄位，來自 FinMind `stock_and_cache_dividend`（**現金 + 股票股利合併成一個數字**）。這對 spec §11 要求的「區分 Stock Split / Reverse Split / Capital Reduction / Rights Issue / Dividend / Cash Dividend / Stock Dividend」完全不夠。

而 `etf_stats.split_factors()` 用的「單日 ratio 超出 [0.55, 1.8] 就判定為分割」是**啟發式補救**，不能當回測基礎。

**✅ 有解，而且是精確解**：實測 FinMind 兩個免費 dataset 直接給出**除權息參考價**：

1. **`TaiwanStockDividendResult`**：
```json
{"date":"2024-03-18","stock_id":"2330","before_price":753.0,"after_price":749.5,
 "stock_and_cache_dividend":3.499789,"stock_or_cache_dividend":"息",...}
```
`after_price / before_price` = **還原係數**。這個數字由交易所依法計算，已經同時涵蓋現金股利、股票股利、**以及減資**（因為它就是「除權息/減資參考價」）。`stock_or_cache_dividend` 欄位區分「息」/「權」。

2. **`TaiwanStockSplitPrice`**：
```json
{"date":"2026-01-19","stock_id":"7780","type":"面額變更","before_price":185.0,"after_price":18.5,...}
```
分割 / 面額變更，同樣給 before/after。

**建議新表**：
```
corporate_actions(stock_id, date, kind, before_price, after_price,
                  cash_dividend, stock_dividend, ratio, source)
  kind: dividend_cash | dividend_stock | split | reverse_split | capital_reduction
  ratio = after_price / before_price   ← 價格還原用
```

有了這張表，回測引擎就能同時產出 spec §11 要求的兩條序列：
- **Price Return**：只套 `ratio`（把除權息造成的價格跳空還原），不計股利
- **Total Return**：套 `ratio` + 股利再投入（沿用 `etf_stats.compute()` 的 `shares *= 1 + div/close` 模型）

⚠️ FinMind 的 `TaiwanStockPriceAdj`（官方還原股價）**實測需付費**（`Your level is register. Please update your user level.`），所以還原必須自己算。同樣付費的還有 `TaiwanStockCapitalReductionReferencePrice`、`TaiwanStockMarketValue`、`TaiwanStockGovernmentBankBuySell`。

## 5.4 🟠 Benchmark（spec §15）— 意外的好消息

spec 要求 TAIEX **Total Return**、0050 Total Return、Custom。

| Benchmark | 現況 | 解法 |
|---|---|---|
| TAIEX（價格指數） | ✅ `market_summary.taiex_close`，121 天 | 回補即可 |
| **TAIEX Total Return** | ❌ 無 | **✅ 實測 FinMind `TaiwanStockTotalReturnIndex`（`stock_id="TAIEX"`）可用（免費層級）**：`{"price":100027.02,"stock_id":"TAIEX","date":"2026-08-03"}` — 這正是發行量加權股價**報酬**指數 |
| 0050 Total Return | 🔸 可自算 | `daily_quotes['0050']` + `dividends['0050']`，用 `etf_stats.compute()` 同一套再投入模型 |
| Custom | 🔸 | 任何 stock_id 或使用者上傳序列 |

spec §15 說「Benchmark 原則上優先使用 Total Return Benchmark」— **可以做到，而且是官方指數，不是自己拼的**。建議新表 `benchmark_series(benchmark_id, date, value)`。

## 5.5 🔴 Risk-Free Rate（spec §17）

Sharpe 需要 risk-free rate 且必須可設定。**現有資料源完全沒有利率資料**。

**建議**：做成 `config.py` 的常數 + 策略設定可覆寫，預設值用台灣一年期定存利率（約 1.5%），UI 標示為 `Assumed`。不值得為此引入新資料源。

## 5.6 🟠 市值與流動性（spec §14）

spec §14 要求 Minimum Average Daily Trading Value / Minimum Market Cap / Minimum Listing History。

| 條件 | 資料 | 評估 |
|---|---|---|
| 日均成交金額 | ✅ `daily_quotes.amount`（全市場、逐日） | **完全可行**，`screener.py` 已有 `min_amount` 先例 |
| 最小上市年資 | ✅ 上市日（見 §5.2） | 可行 |
| 最小市值 | 🟠 `TaiwanStockMarketValue` **需付費** | **只能近似**：`close × (financials.capital / 10)`（股本/10元面額=股數）。但 `financials` 只有深度層 29 檔 → 市值篩選對全市場不可行 |

**建議**：第一版**用「日均成交金額」取代「市值」**作為主要流動性/規模門檻，並在 UI 說明原因。這其實更貼近 spec §14 的真正意圖（「避免回測選到現實中難以成交的小型股票」）。市值門檻列為 Known Limitation。

## 5.7 資料缺口總表與補救成本

| 缺口 | 嚴重度 | 解法 | 一次性成本 |
|---|---|---|---|
| 全市場日線只有 7 個月 | 🔴 阻斷 | `backfill_history(days=1825)` | **~3.5 小時**背景執行 |
| 無 publish date（月營收） | 🔴 阻斷 | FinMind `create_time`；MOPS 用次月 10 日 fallback | schema 改動 + 逐檔重抓 |
| 無 publish date（財報） | 🔴 阻斷 | 法定期限 lag 規則（Assumed） | 純邏輯 |
| `valuations` 只有 5 天 | 🔴 阻斷 Value 策略 | FinMind `TaiwanStockPER` 逐檔 | 800 檔 ≈ 6 分鐘 |
| 無上市/下市日 | 🟠 Survivorship | `TaiwanStockDelisting` + `TaiwanStockInfoWithWarrant` | 2 次請求 |
| 無公司行動明細 | 🟠 報酬失真 | `TaiwanStockDividendResult` + `TaiwanStockSplitPrice` | 逐檔，可與 PER 同批 |
| 無 TR benchmark | 🟠 spec §15 | `TaiwanStockTotalReturnIndex` | 1 次請求 |
| 無 risk-free rate | 🟡 | config 常數 + 可設定 | 0 |
| 無市值 | 🟡 | 改用成交金額門檻 | 0 |
| **無歷史指數成分股** | 🟠 **無解** | UI 標示 Survivorship Bias Warning | 0 |

---

# 6. 是否已有 Backtest 相關程式？

**沒有。** 全專案 `grep -i "backtest|回測|strategy|策略|sharpe|drawdown|portfolio"` 在 `app/`、`web/`、`tests/` 的結果為零（`web/vendor/` 除外）。

最接近的三處：
1. `screener.py` — 單時點橫斷面掃描（見 §2.1）
2. `etf_stats.compute()` — CAGR / 波動 / MDD（見 §2.2）
3. `journal.performance()` — 勝率 / 盈虧比（見 §4.4）

`PRODUCT.md`（未追蹤檔）已把 backtesting 列為確認的產品方向，並寫明「Backtests must prevent look-ahead bias and data leakage」與「Backtesting and strategy-signal workflows are confirmed product direction but are not yet documented as complete capabilities」。本次工作即是把這句話變成實作。

---

# 7. 建議的資訊架構（spec §1、§43、§76）

## 7.1 頂層導覽（7 項，沿用 Top Nav）

```js
const NAV = [
  ["index.html",     "儀表板"],      // Dashboard
  ["stock.html?...", "個股分析"],     // （不放 nav，從搜尋/清單進入，維持現況）
  ["screener.html",  "篩選器"],       // Stock Analysis 的入口之一
  ["watchlist.html", "觀察名單"],
  ["strategy.html",  "策略實驗室"],   // ← 新增 Strategy Lab
  ["portfolio.html", "組合實驗室"],   // ← 新增 Portfolio Lab
  ["journal.html",   "交易日誌"],
  ["finance.html",   "財務規劃"],
];
```

**不新增 Sidebar。不在頁面層再加第三層 Tabs**（結果頁內部的 Tabs 是內容切換，不是導覽，符合 spec §45）。

## 7.2 新增頁面與路由

| URL | 角色 | 主要內容 |
|---|---|---|
| `strategy.html` | **Strategy Lab 首頁**（spec §5） | My Strategies 表格（Name / Type / Last Backtest / CAGR / Sharpe / MaxDD / OOS Status）+ `New Strategy` / `Compare` / `Portfolio Lab` 三顆按鈕。首次進入顯示 Empty State：五個 Template 卡片（spec §51） |
| `strategy.html?new=1` 或 `strategy-builder.html` | **Strategy Builder**（spec §6） | 8 步 Step Workflow。沿用 `.fin-steps` / `.fin-step` 模式 |
| `strategy.html?id=<sid>&v=<ver>` | **Strategy Result**（spec §44、§45） | Header → KPI Row(6) → Equity Curve → Drawdown → Tabs |
| `strategy.html?compare=a,b,c` | **Compare**（spec §35） | 最多 5 策略的指標對照表。**不輸出 Winner** |
| `portfolio.html` | **Portfolio Lab**（spec §37-§41） | 策略組合、配置方法、相關性、風險貢獻 |

採 query-string 分頁（`strategy.html?id=...`）而非多檔案，理由：與現有 `stock.html?id=` 慣例一致，且 Builder / Result 共用大量 state。

## 7.3 模組間資料流（spec §2）

必須連接，但不互相污染 UI：

```
個股分析 (stock.html)
   └─[按鈕] 加入策略股票池 / 送至 Strategy Lab
        ↓  (只傳 stock_id 清單，不在個股頁顯示任何回測圖表 — spec §2)
篩選器 (screener.html)
   └─[按鈕] 以此結果建立 Universe
        ↓
策略實驗室 (strategy.html)
   Builder → Backtest → Result（Performance / Robustness / WalkForward / OOS）
   └─[按鈕] 加入 Portfolio Lab
        ↓  (只傳 strategy_id + version，不在 Strategy Lab 塞退休模擬 — spec §5)
組合實驗室 (portfolio.html)
   多策略配置 / 相關性 / 風險貢獻
   └─[按鈕] 套用到財務規劃
        ↓  (只傳「組合的歷史年化與波動」作為假設報酬的候選值)
財務規劃 (finance.html)
   每月投入金額 / 長期資產規劃
```

**三個邊界規則**（對應 spec §2、§5、§76）：
1. 個股頁**不出現** equity curve / drawdown / 回測指標
2. Strategy Lab **不出現**退休模擬、提款率、通膨折現
3. Portfolio Lab → Financial Planner 的傳遞只是「一個假設報酬率的候選值」，且在財務規劃頁必須標示為 `Backtested`（歷史），與使用者自填的 `Assumed` 分開（spec §67）

## 7.4 Dashboard 整合（spec §42）

新增**一列** Research Snapshot（四格，用 `.fin-sum .b` 樣式），放在現有四張 KPI card 之下：

| 已儲存策略 | 最近回測 | 目前組合 | OOS 狀態 |
|---|---|---|---|
| 5 | Momentum-v3（3 天前） | Core Multi-Strategy | 3 通過 / 1 觀察 |

**不放** equity curve、不放策略排行、不放任何圖表。並建議把現有「資料維護」卡改成 `<details>` 收合，避免該區繼續膨脹。

## 7.5 Strategy Result Page 結構（spec §44、§45、§46）

```
┌ Header ─────────────────────────────────────────────────┐
│ Momentum-v3  [OOS 通過]     回測期間 2021-01~2026-09     │
│ Benchmark: TAIEX TR   最後執行 2026-09-19 14:32  [編輯] │
└─────────────────────────────────────────────────────────┘
┌ KPI Row（只有 6 個，.grid.cols-3 × 2 或 .fin-sum）────────┐
│ CAGR │ Sharpe │ Max DD │ Sortino │ Net Return │ Turnover │
└─────────────────────────────────────────────────────────┘
┌ Equity Curve（Strategy vs Benchmark, Linear/Log 切換）───┐
└─────────────────────────────────────────────────────────┘
┌ Drawdown（Strategy vs Benchmark）─ 第二張主圖，不可收合 ─┐
└─────────────────────────────────────────────────────────┘
┌ Tabs ───────────────────────────────────────────────────┐
│ Performance │ Robustness │ Walk Forward │ Parameters │   │
│ Trades │ Costs │ Regimes                                 │
└─────────────────────────────────────────────────────────┘
```

設定面板依 spec §46 走 **Side Drawer / Edit Strategy**，不常駐佔畫面。

---

# 8. 最合理的 Integration 方式（spec §0 最後一項）

## 8.1 後端分層（完全比照 `app/services/finance/` 的成功範本）

```
app/services/strategy/
  __init__.py       分層說明（比照 finance/__init__.py）
  ── 純計算引擎（不 import app.db，吃 dict/list 吐 dict，可離線單測）──
  templates.py      五套 Strategy Template 常數（比照 finance/universe.py 與 screener.PRESETS）
  factors.py        因子計算與標準化（z-score / rank / winsorize）
  rules.py          Entry/Exit Rule、Position Sizing、Rebalance 排程
  costs.py          Cost Model（commission/tax/slippage/impact；常數對齊 config.FEE_RATE 等）
  engine.py         回測主迴圈（吃「已備好的 point-in-time 面板資料」，不自己查 DB）
  metrics.py        spec §16 的全部指標（Sharpe/Sortino/Calmar/Beta/Alpha/Expectancy/Recovery...）
  regimes.py        市場環境判定（明確規則，可在設定頁查看 — spec §24）
  optimize.py       Grid / Random Search + Plateau Detection（spec §25-§27）
  walkforward.py    Rolling Window（spec §29-§30）
  robustness.py     Historical Robustness 分數與 Warning 規則（spec §32、§62）
  ── 碰 DB 的層 ──
  panel.py          Point-in-Time 面板組裝：把 daily_quotes/month_revenue/financials/
                    corporate_actions 依 publish date 攤成「每個交易日當下可知的資料」
  store.py          Strategy / Version / BacktestRun / Trades / Metrics 的 CRUD + config hash cache
  service.py        API 組裝層

app/services/portfolio/
  allocation.py     Equal Weight / Inverse Vol / Risk Parity（純函式）
  risk.py           Risk Contribution、Correlation、Rolling Correlation（純函式）
  service.py        組裝 + DB
```

**`panel.py` 是整個設計的關鍵**。它是唯一知道「publish date」的地方，引擎完全不碰 DB、只吃它吐出來的面板。這樣做的好處：
- **No Look-Ahead 只需要在一個檔案裡保證**，而且可以用「餵一個假面板、確認引擎在 T 日看不到 T+1 的值」來單測
- `engine.py` 變成純函式 → spec §71/§72 的 sanity test（2 支股票、10 個交易日、手算）可以直接跑，不需要 DB

## 8.2 新增資料表

```
── 策略定義與結果 ──
strategies(id, name, type, hypothesis, thesis, failure_regimes, expected_risk,
           notes, created_at, updated_at, archived)
strategy_versions(strategy_id, version, created_at,
                  universe_config, factor_config, rule_config, sizing_config,
                  rebalance_config, cost_config, validation_config,   ← JSON 字串
                  config_hash)                                        ← cache key (spec §73)
backtest_runs(id, strategy_id, version, config_hash, started_at, finished_at,
              status, data_version, start_date, end_date, benchmark_id,
              random_seed, warnings)                                  ← spec §56 可重現
backtest_metrics(run_id, scope, key, value)   ← scope: gross/net/is/oos/benchmark
backtest_equity(run_id, date, equity, benchmark, drawdown, benchmark_drawdown)
backtest_trades(run_id, stock_id, entry_date, entry_price, exit_date, exit_price,
                shares, gross_pnl, cost, net_pnl, holding_days, exit_reason,
                entry_scores)                                         ← spec §57、§58 Explainability
optimization_runs(id, strategy_id, version, method, param_grid, created_at)
optimization_results(run_id, params, sharpe, cagr, max_dd, trades, is_oos)
walkforward_windows(run_id, idx, train_start, train_end, test_start, test_end,
                    params, oos_return, oos_sharpe)
── 組合 ──
portfolios(id, name, notes, created_at, updated_at)
portfolio_members(portfolio_id, strategy_id, version, weight_mode, weight)
portfolio_runs(id, portfolio_id, config_hash, ...)
── 資料層補強（見 §5）──
corporate_actions(stock_id, date, kind, before_price, after_price,
                  cash_dividend, stock_dividend, ratio, source)
benchmark_series(benchmark_id, date, value)       ← TAIEX_TR / 0050_TR / custom
── 既有表加欄位（需要 migration 機制）──
month_revenue  + publish_date, publish_source
financials     + publish_date, publish_source
stocks         + listed_date, delisted_date
```

## 8.3 API 設計（延續薄路由慣例）

```
GET    /api/strategy/templates            五套 Template 定義
GET    /api/strategy/list                 My Strategies（含最近一次回測摘要）
POST   /api/strategy                      建立（需帶 hypothesis — spec §61）
GET    /api/strategy/{id}                 策略 + 所有版本
POST   /api/strategy/{id}/version         存新版本（spec §7、§28）
POST   /api/strategy/{id}/backtest        觸發回測（BackgroundTasks，回 run_id）
GET    /api/strategy/runs/{run_id}        回測狀態 + 進度（spec §49 的 Current Step）
GET    /api/strategy/runs/{run_id}/result 完整結果
GET    /api/strategy/runs/{run_id}/trades Trade Log（分頁）
POST   /api/strategy/{id}/optimize        Grid/Random Search
POST   /api/strategy/{id}/walkforward     Walk Forward
POST   /api/strategy/compare              最多 5 個 run_id 的對照
GET/POST/DELETE /api/portfolio[/{id}]     組合 CRUD
POST   /api/portfolio/{id}/analyze        組合回測 + 相關性 + 風險貢獻
POST   /api/admin/backfill-for-backtest   全市場 5 年回補（長時間背景）
POST   /api/admin/fetch-corporate-actions 公司行動 + PER + 上下市日
```

回測是長時間運算 → 沿用既有的 `BackgroundTasks` + 前端輪詢模式（`admin/backfill`、`finance/refresh-etf` 已是這個模式），**不引入 Celery / Redis**（spec §74：不要為此突然導入複雜 Backend）。

## 8.4 前端

```
web/strategy.html          Lab 首頁 / Builder / Result（依 query string 切換）
web/portfolio.html         Portfolio Lab
web/js/strategy.js         Builder state + Result 渲染
web/js/portfolio.js
web/js/charts.js           ← 新增：共用圖表建構器（equity/drawdown/heatmap/correlation）
web/css/app.css            檔尾新增 .lab-* 規則（比照 .fin-* 的放法）
```

`charts.js` 是必要的新共用層：spec §48 的七種圖表會在 Strategy Lab、Portfolio Lab 重複使用，不能像現在這樣每頁複製一份 ECharts option。

## 8.5 測試（spec §71、§72）

現有 `tests/smoke.py` 有四個 suite（`sources` / `light` / `deep` / `finance`），其中 **`finance` 是純數學、不打 API、可任意重跑**。

**新增第五個 suite `backtest`**，同樣不打 API：

```bash
uv run python tests/smoke.py backtest
```

必測項目（對應 spec §71、§72）：
- **Sanity case**：2 支股票、10 個交易日、固定 signal，手算交易/報酬/成本/組合價值，逐項比對
- **No Look-Ahead**：餵一個「T 日之後才 publish」的營收，確認 T 日的訊號不含它
- **Trade Timing**：signal 用 T 日 close → 成交日必須是 T+1 Open/Close（spec §12）
- **Cost**：手續費 × 折扣、賣出證交稅、slippage 的三段驗算，且與 `journal.add_trade()` 的公式一致
- **Metrics 交叉驗證**：同一條 equity curve 丟給 `metrics.py` 與 `etf_stats.compute()`，CAGR / 波動 / MDD 必須一致（比照 `compound.py` 封閉解 vs 迴圈的交叉驗證做法）
- Edge cases：Zero Trade / Single Trade / Negative Return / Missing Data / Rebalance / Corporate Actions

---

# 9. 潛在技術債（優先序）

| # | 項目 | 嚴重度 | 是否阻斷 Strategy Lab |
|---|---|---|---|
| 1 | **無 DB migration** — `create_all` 不會 ALTER 既有表 | 🔴 | **是**。加 publish_date 一定會踩到 |
| 2 | **無自訂索引** — 所有 date 條件查詢全表掃描 | 🔴 | **是**。5 年資料後回測會慢到不可用 |
| 3 | **全市場歷史只有 7 個月 / valuations 只有 5 天** | 🔴 | **是**（見 §5） |
| 4 | **Chart theme 硬編在 JS** — 與 CSS token 兩份真實來源 | 🟠 | 否，但新增大量圖表前應先修（低成本） |
| 5 | **無 JS 模組系統** — 每頁一個 inline script，全域變數共享 `common.js` 的命名空間；`finance.js` 672 行單檔 | 🟠 | 否，但 `strategy.js` 會更大。建議新檔一律用 `<script type="module">` + `export`，既有頁面不動 |
| 6 | **無 linter / formatter / type check** — 唯一驗證手段是 smoke test | 🟠 | 否 |
| 7 | `.fin-*` 前綴的通用元件（steps/alert/kv/statgrid/pill/more）被綁在財務規劃頁 | 🟡 | 否，建議改名 + alias |
| 8 | `valuations_latest()` 是「最新快照」而非逐日，導致估值表永遠只有幾天 | 🟠 | 對 Value 策略是 |
| 9 | `dividends` 只存合併股利，無現金/股票拆分、無減資/分割 | 🟠 | 對 Total Return 正確性是 |
| 10 | `etf_stats.split_factors()` 的 ±10% 啟發式在漲跌停附近可能誤判 | 🟡 | 否（有 corporate_actions 表後就不需要它） |
| 11 | 無等寬字體 / tabular-nums，數字欄位不對齊 | 🟡 | 否（spec §47 相關，單一 CSS 規則可解） |
| 12 | vendored ECharts 是否含 heatmap / visualMap 未確認 | 🟡 | 對 spec §26 Parameter Heatmap 可能是 |
| 13 | `backfill_history()` 無進度回報，前端只顯示「背景執行中」 | 🟡 | 否，但 3.5 小時的作業需要進度（spec §49 精神） |
| 14 | 三張全市場表（quotes/insti/valuations）無 `market` 欄位，跨市場查詢需 JOIN `stocks` | 🟡 | 否 |

---

# 10. 建議的 Stage 執行順序（修正 spec §80）

spec §80 的 Stage 順序是 `A Audit → B Backtest Core → ...`。稽核後建議在 B 之前插入一個資料基礎 Stage，否則 Backtest Core 寫完會沒有資料可跑：

| Stage | 內容 | 產出 |
|---|---|---|
| **A** ✅ | Audit + IA | 本文件 |
| **A+**（新增） | **資料基礎** | ① migration 機制 ② 索引 ③ `corporate_actions` / `benchmark_series` 表 ④ publish_date 欄位與抓取 ⑤ 上市/下市日 ⑥ 觸發 5 年全市場回補（背景 3.5 小時） |
| **B** | Backtest Core | `panel.py` / `engine.py` / `metrics.py` / `costs.py` + `tests/smoke.py backtest` sanity case |
| **C** | Strategy Templates | 五套 Template + `factors.py` / `rules.py` |
| **D** | Performance UI | Strategy Lab 首頁 / Builder / Result（Overview + Equity + Drawdown + Performance tab） |
| **E** | Optimization + Robustness | Grid/Random + Heatmap + Plateau + Robustness Score |
| **F** | Walk Forward + OOS | |
| **G** | Strategy Comparison | |
| **H** | Portfolio Lab | |
| **I** | Financial Planner Integration | |

Stage A+ 的 5 年回補可以在 Stage B 開發期間背景跑，不阻塞。

---

# 11. Known Limitations（必須在 UI 誠實標示）

依 spec §66/§67「不要產生虛假精確度、必須區分 Historical / Backtested / Assumed / Estimated / Projected / Actual」：

| 限制 | UI 標示方式 |
|---|---|
| 無歷史指數成分股 → 部分 survivorship bias 仍在 | Universe 設定頁常駐 **Survivorship Bias Warning**，說明「已排除當時未上市/已下市標的，但無法還原歷史指數成分」 |
| 財報公布日為法定期限假設，非實際公布日 | 財報相關因子標 `Assumed publish date` |
| MOPS 全市場月營收無逐家公布日 | 該部分標 `Assumed (法定期限)`；FinMind 來源標 `Actual` |
| 市值門檻無免費資料 | 流動性篩選改用日均成交金額，並說明原因 |
| Risk-free rate 為使用者設定值 | Sharpe 旁標註所用 rf |
| 免費資料源的 restatement（財報追溯調整）無法還原 | 說明「財報數值為目前公告值，不含歷史 restatement 前的原始值」 |
| 回測不含停牌、漲跌停無法成交、零股等微結構 | Cost / Execution 設定頁說明 |
| 歷史回測結果一律稱 `Historical` / `Backtested`，不得稱預測 | 全站文案規範 |

---

# 12. 下一步

Stage A 交付完成。建議確認以下三個決策後再進 Stage A+：

1. **回補範圍**：5 年（~3.5 小時）還是先 3 年（~2 小時）？建議 5 年 — 只做一次，且 spec §29 的 Walk Forward 需要足夠視窗切分。
2. **Universe 規模**：逐檔資料（PER / 公司行動）要補多少檔？建議以「近一年日均成交金額 ≥ 3,000 萬」篩出約 700~900 檔作為可回測 universe，其餘保留輕量層。
3. **`.fin-*` 元件是否改名為共用**（§3.4 的建議）— 影響 `finance.js` 與 `app.css` 的既有規則，但只是加 alias，風險低。

確認後即可進入 Stage A+。
