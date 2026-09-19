# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

個人用台股投資分析平台（不下單，只分析＋交易日誌＋財務規劃）。分析邏輯以 [台股投資分析實戰框架.md](台股投資分析實戰框架.md) 為藍本 — 個股頁、評分表、篩選器條件都對映此框架的章節，改動這些功能前應先看對應章節。財務規劃／ETF 長期投資功能的規格書是 [spec/個人財務狀況與 ETF 長期投資平台開發.md](spec/個人財務狀況與%20ETF%20長期投資平台開發.md)，`app/services/finance/` 各模組的 docstring 都標了對映章節。純本機運行，免費資料源，SQLite 單檔資料庫（`data/stocks.db`）。

## Commands

```bash
# 啟動（含排程：平日 15:05 自動更新）
uv run uvicorn app.main:app --port 8787

# 煙霧測試 — 實際打資料源 API，非 mock
uv run python tests/smoke.py            # 全部
uv run python tests/smoke.py sources    # 只測資料源（twse/tpex/finmind/mis_snapshot）
uv run python tests/smoke.py light      # 輕量層（股票清單、每日更新）
uv run python tests/smoke.py deep       # 深度層（單股：K線、營收、財報、法人、評分）
uv run python tests/smoke.py finance    # 財務規劃引擎（純數學，不打 API，可任意重跑）
```

沒有其他測試框架、linter 或型別檢查設定 — `tests/smoke.py` 是唯一的驗證手段。`sources`/`light`/`deep` 會真的打外部 API（有禮貌限速，勿頻繁重跑）；`finance` 只做純計算與讀本機 DB，可放心反覆執行。

環境變數在 `.env`（見 `app/config.py:load_dotenv`，極簡自製 parser，不用 python-dotenv）：`FINMIND_TOKEN`、`FUGLE_API_KEY`、`FEE_DISCOUNT`、`TAX_RATE` 等。

## Architecture

### 三層資料策略（貫穿整個後端設計）

1. **輕量層**（全市場、每日全表，永遠在庫）— `stocks` / `daily_quotes` / `valuations` / `institutional_daily` / `market_summary` / `month_revenue`。由 `app/services/market_daily.py` 維護，排程每平日 15:05 跑 `update_daily()`。
2. **深度層**（按需抓取單一個股，`DEEP_FRESH_HOURS=12` 內視為新鮮）— `financials` / `margin_daily` / `dividends` / 及 `daily_quotes` 的 5 年歷史回補。由 `app/services/deep_fetch.py:ensure_deep()` 驅動，觸發時機：開個股頁（`GET /api/stocks/{id}` 用 `BackgroundTasks` 背景抓）、加入觀察名單、篩選器命中。`deep_fetch_log` 表追蹤每檔股票每個 dataset 的新鮮度與最後存取時間。
3. **清理層** — `deep_fetch.cleanup()`，非觀察名單且 `DEEP_RETENTION_DAYS=90` 天未存取的深度資料會被釋放（輕量層資料不受影響），排程每週日 03:00 跑。

新增/修改抓取邏輯時要清楚自己在哪一層：輕量層是全市場批次、深度層是單股按需，兩者的新鮮度與清理策略完全不同。

### 資料源層（`app/datasources/`）

`base.py` 提供共用 HTTP client：單例 `httpx.Client`、依 host 的禮貌限速（`app/config.py:POLITE_INTERVAL`）、4xx 不重試但 429/5xx 重試退避、`num()`/`roc_to_iso()` 清洗工具。每個資料源模組（`twse.py` / `tpex.py` / `mops.py` / `finmind.py` / `mis_snapshot.py` / `fugle.py`）都只透過 `base.get_json()` 打 API，不自己開 client。

新增資料源或修改抓取邏輯前，**務必看 memory 裡的 [台股資料源陷阱](twse-data-source-gotchas.md)** — 裡面記錄了實測過的坑（TPEx SSL 憑證需關 `VERIFY_X509_STRICT`、MOPS 月營收表 2024 改版後的網域與 Big5 編碼、FinMind 現金流量表是累計值需去累計化、TWSE 法人欄位「自營」字樣陷阱、TPEx 法人欄位索引等），重踩會浪費大量除錯時間。

**即時行情可插拔**：`app/datasources/realtime_base.py` 定義 `RealtimeProvider` 抽象介面（`quotes(pairs) -> list[dict]`）。`get_provider()` 依 `FUGLE_API_KEY` 是否存在自動選擇 `FugleProvider`（真即時，免費方案限速 60 次/分鐘、只能逐檔查詢）或退回 `MISSnapshotProvider`（免金鑰，約 5 秒延遲）；富果金鑰失效時 `FugleProvider.quotes()` 會捕捉 `FugleAuthError` 自動降級為 MIS，不會讓報價功能整個掛掉。日後要接新券商 API，比照新增一個 Provider 實作即可，服務層與前端不用改。

### Service 層（`app/services/`）

- `market_daily.py` — 輕量層維護（股票清單同步、每日更新、歷史回填、儀表板彙總）
- `deep_fetch.py` — 深度層按需抓取與清理；含 FinMind 財報科目名稱容錯對照表（`INCOME_MAP`/`BALANCE_MAP`/`CASHFLOW_MAP`，因為 FinMind 不同股票/年度回傳的科目名稱不一致）
- `stock_view.py` — 個股頁資料組裝（K線、營收序列、財報序列、籌碼序列、估值區間、技術摘要）
- `scoring.py` — 框架第五章 100 分評分表：量化面向自動預填，質化面向（產業趨勢/競爭力/催化劑，見 `MANUAL_ONLY`）需人工評分，人工分數一律可覆寫自動分數
- `screener.py` — 全市場篩選器，條件對映框架「中期波段」邏輯（見 `PRESETS`），在輕量層資料上掃描，命中後觸發該股深度抓取
- `alerts.py` — 個股警訊掃描 + 治理人工查核清單
- `journal.py` — 交易日誌：買進前八句話、依最大虧損反推部位大小（`app/config.py` 的 `FEE_RATE`/`FEE_DISCOUNT`/`TAX_RATE` 用於損益計算）

### 財務規劃層（`app/services/finance/`）

與上面的個股分析完全獨立的一套功能：回答「以我目前的財務狀況，每月該投多少、資產怎麼配、20 年後可能多少、退休夠不夠」。

分兩類模組，界線要守住：

- **純計算引擎**（不 import `app.db`、吃 dict 吐 dict，所以能離線單測）— `universe.py`（ETF 名單與分類 metadata，人工維護的常數，比照 `screener.PRESETS` 的寫法）、`health.py`（可支配所得/緊急預備金/建議投資區間）、`risk.py`（五題風險問卷）、`allocation.py`（role 級距權重 → 攤到標的；含重疊/國家/資產類別提示）、`compound.py`（複利、三情境、通膨、費用、里程碑）、`retirement.py`（退休缺口、提款模擬、SoRR）、`rebalance.py`（現金流式再平衡）
- **碰 DB 的兩層** — `etf_stats.py`（從 `daily_quotes`＋`dividends` 算歷史含息年化）、`service.py`（profile CRUD 與結果組裝）

三個設計約束：

1. **試算是無狀態的**：`POST /api/finance/simulate` 吃完整 payload、吐完整結果，前端每次輸入變動 debounce 後重打。公式只有一份在後端，不要在 JS 裡複製第二份。
2. **歷史報酬與假設報酬必須分開呈現**：`etf_stats` 算的是歷史參考值，使用者設定的是試算假設，介面上不能混為一談。
3. **ETF 的 role/資產類別/國家/內扣費用是人工維護的常數**，免費資料源給不了，改名單時記得同步 metadata。

補 ETF 歷史資料走 `etf_stats.refresh()`，**不要用 `deep_fetch.ensure_deep()`** — 那會連帶去抓 ETF 根本沒有的月營收與財報，白費約 40 次 FinMind 額度。

### DB 層

SQLAlchemy Core（非 ORM）+ SQLite WAL，`app/db/tables.py` 是唯一的 schema 定義來源。`app/db/__init__.py` 的 `upsert_many()` 是所有寫入的共用路徑：依主鍵衝突則更新，且只更新該批資料實際帶有的欄位（避免不同資料源欄位不齊時把舊值洗成 NULL）。查詢多用 `query_all`/`query_one` 搭配原生 SQL（`text()`），不是 ORM 查詢。

DB_URL 走環境變數（`STOCKS_DB_URL`），預留換 Postgres 上雲；資料路徑也走 `STOCKS_DATA_DIR`，無本機路徑硬編碼。

### API 與前端

`app/api/routes.py` 是唯一的 REST 層，前端（`web/`）全部透過 `fetch` 呼叫。前端是原生 JS（無框架）+ vendored ECharts（`web/vendor/echarts.min.js`），共用邏輯在 `web/js/common.js`（導覽列、`api()` fetch 封裝、`fmt` 格式化工具、ECharts 暗色主題）。五個頁面：`index.html`（儀表板）、`screener.html`、`watchlist.html`、`journal.html`、`finance.html`（財務規劃，另有 `web/js/finance.js`），個股頁是 `stock.html?id=`。

CSS 全部在 `web/css/app.css`：先是共用 design system（CSS 變數、`.card`/`.grid cols-N`/`table.data`/`.badge`），檔尾才是 `.fin-*` 前綴的財務規劃頁專用規則。注意 grid 子項的 `min-width: auto` 會被寬表格/圖表撐開導致整頁橫向溢出，財務規劃頁已用 `.fin-layout > *, .fin-result .grid > * { min-width: 0 }` 處理。

新增後端功能時，路由層應保持薄 — 邏輯放 service 層，routes.py 只做參數解析與呼叫。

## Git Attribution Rules

- Always use the repository owner's existing Git identity.
- Never modify `git user.name` or `git user.email`.
- Do not add Claude, Cursor, Anthropic, OpenAI, Codex, ChatGPT, Genspark, or any AI assistant as a Git author, committer, or co-author.
- Never add AI-generated `Co-Authored-By` trailers.
- Preserve legitimate human contributors and human co-authors.
- AI agents may assist with git add, commit, and push, but attribution must remain with the actual human repository author.
- Before committing, verify `git config user.name` and `git config user.email`.
- Expected owner identity for this repository:
  Sam <shaojun5861@gmail.com>
