# 開發與資料維護參考

這份文件承接原 README 中的技術操作、環境設定、模組對照與研究資料回補說明。使用者操作手冊見 [README](../README.md)；現行架構與契約分別以 [ARCHITECTURE.md](ARCHITECTURE.md)、[RESEARCH_CONTRACTS.md](RESEARCH_CONTRACTS.md) 和 [AGENTS.md](../AGENTS.md) 為準。

以下命令在專案根目錄執行。環境需求由 `pyproject.toml` 定義（Python 3.12 以上），相依套件由 `uv.lock` 固定；部署的帳密、磁碟與時區設定另見 [Railway 部署紀錄](railway-deployment.md)。本機 `.env` 與 `spec/` 均不在版控中，新 checkout 不一定包含本機規格文件。

## 本機啟動與設定

安裝 Python 3.12 以上及 `uv` 後，在專案根目錄執行：

```bash
uv sync
# 停止舊服務並確認 DB 路徑；只初始化一次，腳本先備份既有 DB
uv run python scripts/accounts.py init --username owner
STOCKS_COOKIE_SECURE=0 uv run uvicorn app.main:app --port 8787
```

瀏覽器開啟 <http://localhost:8787>。`app/config.py` 讀取行程環境變數及根目錄 `.env`；`STOCKS_DB_URL`／`STOCKS_DATA_DIR` 控制資料位置，必須在啟動前設為行程環境變數。全新本機資料庫的首次準備順序見 [使用手冊](../README.md)。私人 Railway 必須先匯入一致的既有 SQLite 備份，不能沿用本機建立空 DB 的流程，詳見 [部署紀錄](railway-deployment.md)。

## 每日更新與排程實作備註

Stage L1/L1.1 的自動更新只處理每日盤後輕量層資料，不寫盤中快照，也不更新 Strategy Lab 的 `benchmark_series`。研究基準由歷史回補工作另行維護。

預設 `AUTO_REFRESH_CHECK_TIME=15:05`，但目前 `app/scheduler.py` 的 `CronTrigger` 依小時範圍和 15 分鐘格點落在 15:00、15:15…，沒有精確套用設定的分鐘。伺服器停止或電腦睡眠期間不執行排程；啟動時嘗試 catch-up，結果仍須由缺失交易日與 freshness 回應確認。每月 10–12 日另排程取得月營收。

儀表板管理員使用 `GET /api/admin/data-freshness`，成員使用 `/api/market/freshness`（不含 runner logs/errors/config）。來源查詢與缺漏判定、有限重試、共用鎖及快取契約見 [L1 報告](history/stage-l1-daily-auto-refresh-report.md)、[L1.1 報告](history/stage-l1-1-daily-refresh-hardening-report.md) 及 [AGENTS.md](../AGENTS.md)。

### 設定載入順序與格式

目前 `.env` 在部分常數初始化後才載入；只有後續重新讀取的設定（例如 `FINMIND_TOKEN`、`FUGLE_API_KEY`、存取保護及歷史回補門檻）會採用 `.env` 的值。資料路徑、交易成本、`ENABLE_SCHEDULER` 及 `AUTO_REFRESH_*` 要以啟動前的行程環境變數設定；Railway 使用 Variables。`.env` parser 不移除行尾註解，請使用單純的 `KEY=value`，註解另放獨立一行。完整載入行為以 `app/config.py` 為準。

## 測試 / 檢查指令

```bash
# 先把 STOCKS_DB_URL / STOCKS_DATA_DIR 指到隔離 fixture 副本，勿用日常 DB
# 舊單人服務測試明確選 basic；帳號測試自行切 accounts
STOCKS_AUTH_MODE=basic uv run python -m unittest discover -s tests -p 'test_*.py'

# 煙霧測試 — 實際打資料源 API 或跑真實研究流程，非 mock
uv run python tests/smoke.py            # 全部
uv run python tests/smoke.py sources    # 只測資料源（twse/tpex/finmind/mis_snapshot）
uv run python tests/smoke.py light      # 輕量層（股票清單、每日更新）
uv run python tests/smoke.py deep       # 深度層（單股：K線、營收、財報、法人、評分）
uv run python tests/smoke.py finance    # 財務規劃引擎（純數學，不打 API，可任意重跑）
uv run python tests/smoke.py backtest   # Backtest 核心手算交叉驗證（純數學，不打 API，可任意重跑）

# 語法/編譯檢查（沒有另外設定 linter 或型別檢查）
uv run python -m compileall app tests scripts
```

`tests/test_*.py`（`unittest`）是主要的正確性驗證；其中大多數是離線純函式測試，部分 fixture-gated 整合測試會讀取真實資料；`tests/test_portfolio_lab.py` 與 `tests/test_finance_portfolio_integration.py` 的部分測試還會建立 Portfolio／研究紀錄，部分以 archive 收尾，並非唯讀。fixtures 不存在時會自動跳過。要跑含真實 fixtures 的完整套件，先用 `scripts/backup_sqlite.py` 建立隔離副本，透過 `STOCKS_DB_URL` 與 `STOCKS_DATA_DIR` 指向副本及其暫存資料目錄，避免把測試紀錄寫入日常 DB。請以當次完整測試的輸出為準，勿沿用 Stage J 的 389 項歷史數字。`tests/smoke.py` 才會打外部 API（有禮貌限速，勿頻繁重跑）。策略研究的真實資料驗證另見 `scripts/` 的一次性腳本；它們不是日常測試的替代品。

## 代理指令、本機規格與資料源筆記

所有代理共用根目錄 `AGENTS.md`。`CLAUDE.md` 僅含 `@AGENTS.md`，兼容尚未直接載入 AGENTS 的 Claude Code；不要在入口另存一份共用規則。更新後可在 Claude Code 使用 `/context` 核對 Memory files 的載入來源。

修改對應功能前，若本機規格存在，先閱讀相關章節：

- 個股分析、評分、篩選器：`spec/台股投資分析實戰框架.md`。
- 財務規劃／ETF：`spec/個人財務狀況與 ETF 長期投資平台開發.md`。
- Strategy Lab／Portfolio：`spec/Strategy Lab-Backtesting Engine-Portfolio Lab.md`。

三份均位於忽略版控的 `spec/`；新 checkout 可能沒有。現行架構與研究契約仍以本庫共用文件為準，不能用舊規格推翻已揭露的 hotfix。

修改 `app/datasources/` 時先讀該模組與相關測試。若 Claude session 提供 `twse-data-source-gotchas.md` 記憶筆記，可查已解決的 TPEx SSL、MOPS domain/Big5、FinMind 累計現金流及法人欄位陷阱；這是可選的本機上下文，不是跨代理保證存在的共用依賴。缺少筆記時以來源程式／測試及任務需要的官方文件查證，不臆測筆記內容。

## 研究模組與原始框架對照

### 個股分析（對映本機規格 `spec/台股投資分析實戰框架.md`）

| 頁面 | 功能 | 框架章節 |
|---|---|---|
| 儀表板 | 大盤、法人金額、市場寬度、觀察名單速覽 | 二之1 大盤與總體 |
| 個股頁 | K線均線、月營收、獲利能力、財務品質、估值、籌碼 六面向 | 二之2~7 |
| 個股頁右欄 | 100 分評分表（量化自動預填＋質化人工）、警訊、治理查核清單 | 四、五 |
| 篩選器 | 全市場掃描（營收動能/轉機/價值存股/突破），命中自動預抓深度資料 | 三之2 中期波段 |
| 觀察名單 | 研究中/等買點/持有中 三級，盤中準即時報價（約 5 秒延遲） | 七之4 等待買點 |
| 交易日誌 | 買進前八句話、最大虧損反推部位、持倉損益、勝率與盈虧比 | 六、三之3 |

### Strategy Lab（策略實驗室，`web/strategy.html` 單頁，Stage D～H1）

`strategy.html` 一頁整合 Strategy Lab、Optimization、Walk Forward 三塊 UI（沒有各自獨立的頁面）；Strategy Portfolio 有獨立的 `portfolio.html` 介面（Stage H2）。Cross-Strategy 分析目前**只有 API，沒有 UI**。所有研究功能共用純函式回測計算核心；`app/services/strategy/panel.py` 是同套件內的 DB 讀取 adapter。`*_lab.py` 負責持久化與生命週期，其他既有 DB adapters 及 routes 的直接 DB 例外見 [ARCHITECTURE.md](ARCHITECTURE.md) §2；新增路由邏輯應委派 service。

- **Strategy Lab**（`strategy_lab.py`）— 五套預設策略模板（quality_growth_momentum / revenue_growth_acceleration / momentum_trend / value_quality / low_volatility_quality），策略/版本不可變（改參數會產生新版本，不覆寫）、單次回測、逐日 equity curve、成交明細。
- **Optimization / Robustness**（`optimization_lab.py`）— 網格搜尋 + plateau/robustness region 偵測；目的是找「穩健區域」不是找「歷史最高 Sharpe」——API 回應裡沒有任何 `winner`/`best` 欄位。
- **Walk Forward / OOS**（`walkforward_lab.py`）— expanding/rolling 訓練窗、每個 fold 訓練 → freeze candidate → out-of-sample 測試，independent-fold OOS 是主要證據，stitched（縫合）序列只是次要參考且明確標記 `stitched_secondary`；無法凍結穩定候選的 fold 是合法結果（`no_candidate`，全期持有現金），不是失敗。
- **Cross-Strategy 分析**（`cross_strategy/`，`POST /api/strategy-lab/cross-strategy/analyze`）— 對多個已完成 Walk Forward job 做**描述性**比較（報酬相關性、持股/產業重疊、回撤互補、candidate availability），不排名、不算資金配置。
- **Strategy Portfolio**（`portfolio_lab.py`，Stage H0/H1）— 任意數量、任意組合的 Single Strategy 組成「策略組合」，equal_weight / fixed_weight 已可用，inverse_volatility 已實作但目前五套策略證據不足（`insufficient_evidence`，不會 silent fallback）；risk_parity / minimum_variance / correlation_aware 尚未實作。月度 rebalance、聚合多策略對同一股票的曝險後只收一次淨額交易成本、`no_candidate` 一律留現金絕不自動轉給其他策略。有獨立的 `portfolio.html` 介面（Stage H2），也可作為 `finance.html` 財務規劃的投資來源之一（Stage I1/I2：唯讀研究證據，不會自動變成報酬假設）。

## 研究方法論原則（Research methodology principles）

Strategy Lab 全鏈（Backtest → Optimization → Walk Forward → Portfolio）遵守幾條不可打破的原則，詳細契約見 [docs/RESEARCH_CONTRACTS.md](RESEARCH_CONTRACTS.md)：

1. **Point-in-time only**：任何日期 T 的訊號/配置只能用 T 當時已知的資料；財報/月營收用公告日期契約（`actual` > `observed_finmind` > `assumed_deadline`），從不用「今天已知、但當時未公告」的數字。
2. **調整股價只給因子計算用**：實際成交/損益一律用原始（未調整）股價，避免除權息調整污染真實現金流。
3. **IS/OOS 嚴格分離**：Walk Forward 的 independent-fold OOS 是主要證據；stitched 序列是次要、非獨立的參考，永遠明確標記，不會混成同一種證據。
4. **不用測試資料調參**：Optimization 只在訓練窗做網格搜尋、找穩健區域；Walk Forward 的 candidate 只從訓練窗凍結，OOS 期間不會回頭調整參數。
5. **研究修訂要揭露**：擴大搜尋空間是一個新的、有記錄的 revision，不會默默重新定義同一個 job。
6. **`no_candidate` 是合法證據**：找不到穩定候選時全期持有現金並如實記錄，不會被當成錯誤丟棄，也不會在 Portfolio 層被自動轉給其他策略。
7. **絕不 silent fallback**：任何方法（inverse_volatility 配置、covariance-based 方法、資料 coverage 不足時）證據不足就明確回報 `insufficient_evidence`/`unavailable`，不會偷偷退回一個猜測值。

## 資料來源（全部免費）

- **證交所 OpenAPI / 官網 JSON** — 全市場收盤、估值、三大法人、大盤（輕量層，每日 6-8 個請求）
- **櫃買中心 OpenAPI / 官網 JSON** — 上櫃對應資料
- **FinMind** — 個股深度資料：5 年價量、月營收、三大財報、法人/融資券、股利（按需抓取）
- **MOPS 公開資訊觀測站** — 全市場月營收彙總（每月 2 個請求）
- **證交所 MIS** — 盤中準即時快照（無金鑰時的後備，約 5 秒延遲、限速輪詢）
- **富果 Fugle 行情 API**（選用）— 設定金鑰後自動升級為真即時逐檔報價，取代 MIS 快照

### FinMind 金鑰（建議但非必要）

到 <https://finmindtrade.com> 取得 token（可用額度依帳號及來源當下規則），在專案根目錄建 `.env`：

```
FINMIND_TOKEN=你的token
```

不填也能用，但深度抓取額度較低。

### 富果即時行情金鑰（選用）

到 <https://developer.fugle.tw> 申請行情 API 金鑰，寫入 `.env`：

```
FUGLE_API_KEY=你的金鑰
```

觀察名單按「更新盤中報價」時會依序查詢每一檔；可用額度依帳號及來源當下規則。原 README 記錄的 60 次／分鐘是當時實測值，不保證後續方案相同。`.env` 已加入 `.gitignore`，請維持金鑰只在本機或部署環境設定，不要強制加入版控。

### 交易成本設定（啟動前的行程環境變數）

```
export FEE_DISCOUNT=0.6   # 日誌成本估計的手續費折數（預設 6 折）
export TAX_RATE=0.003     # 全域稅率；不是依每筆商品自動判斷
```

### 每日自動補齊設定（Stage L1，啟動前的行程環境變數）

```
export AUTO_REFRESH_ENABLED=1                    # 設為 0 關閉自動補齊，只留手動「立即更新」
export AUTO_REFRESH_TIMEZONE=Asia/Taipei
export AUTO_REFRESH_CHECK_TIME=15:05             # 盤後檢查設定；目前排程以小時範圍及 15 分鐘時鐘格點執行
export AUTO_REFRESH_POLL_INTERVAL_MINUTES=15     # 檢查時間之後多久 poll 一次
export AUTO_REFRESH_MAX_RETRIES=6                # 單一交易日最多重試幾次
export AUTO_REFRESH_RETRY_WINDOW_MINUTES=180     # 超過這個時間窗就停止重試（保留 last error，見 freshness 狀態）
export AUTO_REFRESH_STATUS_CACHE_SECONDS=60      # Stage L1.1：/api/admin/data-freshness 快取官方交易日查詢結果的秒數，避免 dashboard 20 秒 poll 打上游
```

判斷「今天缺哪幾個交易日」一律以實際缺失的 session 數為準（優先用 Strategy Lab 回補的 `trading_calendar`；沒有時退回平日粗估但只往前補、不回頭猜過去），不是單看 `MAX(daily_quotes.date)` 或日曆天數差——避免「中間某個交易日缺失，但比它晚的日期已入庫」被誤判為已同步。詳見 [docs/history/stage-l1-1-daily-refresh-hardening-report.md](history/stage-l1-1-daily-refresh-hardening-report.md)。

## 架構與目錄（現行定義見 [ARCHITECTURE.md](ARCHITECTURE.md)）

- **三層資料策略**：全市場輕量層（每日全表、永遠在庫）→ 個股深度層（開頁/加觀察/篩中才抓，12 小時內視為新鮮）→ 清理層（非觀察名單 90 天未存取可釋放）
- **行情可插拔**：`app/datasources/realtime_base.py` 定義介面；有 `FUGLE_API_KEY` 就自動用富果真即時，否則退回 MIS 快照（金鑰失效時也會自動降級，不會整個報價功能掛掉）
- **Strategy Lab 是純函式計算核心 + 持久化／adapter 層**：`*_lab.py` 負責版本及 run 生命週期；`strategy/panel.py`、`walkforward/freeze.py`、`cross_strategy/data.py`、`portfolio/data.py` 是既有 DB 邊界，計算核心不碰 DB。新路由委派 services；現有直接 DB 例外見 [ARCHITECTURE.md](ARCHITECTURE.md) §2。
- **私人部署**：Railway 單一 worker／replica、`/data` 持久磁碟。新程式預設邀請制帳號／session／CSRF；啟動要求已初始化管理員與 v7 schema，雲端另外要求既有有效 SQLite。2026-10-14 本機與 Railway 已完成 v7 遷移並保留原管理員登入，正式 HTTPS 帳號流程已驗證。操作、復原與上線順序見 [ACCOUNTS.md](ACCOUNTS.md)；本機與雲端資料各自獨立。

## 高階目錄結構

```
app/
  api/routes.py            # 唯一的 REST 層（薄）
  config.py                # 環境變數（.env，自製 parser）
  access.py                # 帳號 session/CSRF/admin 存取保護；明確 legacy Basic
  identity.py              # request/worker 身分（ContextVar）
  db/tables.py              # table/column 宣告；db/migrations.py 執行 DDL/triggers
  datasources/               # twse / tpex / finmind / mops / mis_snapshot / fugle
  services/
    market_daily.py, deep_fetch.py, stock_view.py, scoring.py,
    screener.py, alerts.py, journal.py          # 基本面分析層
    data_freshness.py                           # Stage L1：每日自動補齊 + freshness 狀態（只呼叫 market_daily，不另開寫入路徑）
    finance/                                     # 財務規劃 / ETF 長期投資（獨立子系統）
    strategy/                                    # 回測引擎（純函式：engine/metrics/factors/templates）
    strategy_lab.py                              # Strategy Lab 持久化/版本/run
    optimization/, optimization_lab.py           # Optimization + robustness
    walkforward/, walkforward_lab.py             # Walk Forward / OOS
    cross_strategy/                              # 跨策略描述性分析（唯讀）
    portfolio/, portfolio_lab.py                 # Generic Strategy Portfolio 引擎
web/
  index/screener/watchlist/journal/finance/strategy/portfolio.html + stock.html
  js/common.js（導覽/api()/格式化）, charts.js, data-maintenance.js,
  strategy.js, optimization.js, walkforward.js, portfolio.js, finance.js
tests/        # unittest（主要驗證）+ smoke.py（打外部 API）
scripts/      # 真實資料驗證、SQLite 一致備份及 Railway 啟動/首次匯入工具
docs/         # 文件導覽 + 開發/架構/契約/狀態/部署參考；history/ 留存階段紀錄
```

## 已知限制

只列會影響開發判斷或使用方式的限制；完整清單見 [docs/PROJECT_STATUS.md](PROJECT_STATUS.md)。

- Portfolio 持股僅顯示每次 rebalance 當日快照，不是即時或逐日持股；research run 取消一律整段重算（沒有逐 fold checkpoint），目前研究窗口都在秒級完成，是可接受的簡化。
- Portfolio rebalance 只實作了「每月」一種頻率；`inverse_volatility` 配置法已實作但現有五套策略證據不足以產生真實配置；`risk_parity`/`minimum_variance`/`correlation_aware` 尚未實作（呼叫會明確 `NotImplementedError`）。
- `max_sector_weight` 只做回報（reporting），不強制執行（`stocks.industry` 是現況快照，非時點正確的產業分類）。
- 未解析（unresolved）的公司行動仍會讓 Walk Forward 訓練期的 severity 全面判定為 critical（上游資料問題，尚未解決）。
- 每日自動補齊（Stage L1/L1.1）依實際缺失的交易 session 數決定：只缺一天用單日更新，缺兩天以上改用既有的多日回填（不是看日曆天數差）；全新安裝（`daily_quotes` 完全空）不會自動觸發大規模歷史回補，仍需手動執行 [使用手冊](../README.md) 的首次資料準備順序。沒有 `trading_calendar` 資料時，缺口中間剛好卡到國定假日可能多重試幾次才會 settle 成 `failed`（不會誤判 `fresh`）。詳見 [docs/history/stage-l1-daily-auto-refresh-report.md](history/stage-l1-daily-auto-refresh-report.md) 與 [docs/history/stage-l1-1-daily-refresh-hardening-report.md](history/stage-l1-1-daily-refresh-hardening-report.md)。
- Railway 目前是共用帳密、共用資料的私人部署，不提供各使用者資料隔離；本機和 Railway 的 SQLite 不自動同步。部署驗證時曾有行情缺日、估值與研究 benchmark 延後，檢查最新資料時應以儀表板 freshness 與實際資料為準，勿沿用部署當日的快照。
- `listed_date`/`delisted_date` 等歷史事實仍可能是 NULL（來源未提供時保留未知，不猜測）；不是完整的財報/營收歷史修訂版本庫。

## 首次使用（Strategy Lab 資料基礎）

Strategy Lab 需要比基本面分析更長的歷史資料（5 年價量、逐檔財報）。Dashboard →「資料維護」→「Strategy Lab 資料基礎」可建立、續跑 25 單位、暫停及查看進度；也可用 CLI：

```bash
uv run python -m app.backfill create --start 2026-09-16 --end 2026-09-18 --stocks 2330,5347   # 小範圍驗證
uv run python -m app.backfill create --min-avg-amount 30000000                                  # 五年正式工作
uv run python -m app.backfill run JOB_ID --max-units 100
uv run python -m app.backfill status JOB_ID
uv run python -m app.backfill pause JOB_ID
```

啟動服務會自動執行 additive SQLite migration（`app/db/migrations.py`）；也可手動執行 `uv run python -m app.backfill migrate`。**升級或大規模資料遷移前建議先用 SQLite backup API 備份**（WAL 模式下不要直接複製正在寫入的 `.db` 檔）。

預設區間是昨天往前五個曆年；`--min-avg-amount` / `STOCKS_MIN_AVG_AMOUNT`（預設 NT$30,000,000）以判斷日之前一年內有效成交金額的算術平均篩選 universe，無固定檔數，至少 120 個觀察日（`--min-observations` 可設定）。完整工作採區間內每月最後交易日的 universe 聯集；每日個別資格可用 `GET /api/admin/data-universe?as_of=YYYY-MM-DD` 查詢。

API（沒有常駐 worker；服務關閉後以同 ID 續跑）：

- `POST /api/admin/backfill-for-backtest`：`{"start":"2021-09-18","end":"2026-09-18","min_avg_amount":30000000}`
- `GET /api/admin/backfill-for-backtest`：工作清單、資料覆蓋
- `GET /api/admin/backfill-for-backtest/{id}`：各資料集進度、錯誤、空資料單位
- `POST /api/admin/backfill-for-backtest/{id}/resume`：`{"max_units":25}`（1～100）
- `POST /api/admin/backfill-for-backtest/{id}/pause`：目前單位提交後停止

公布日資料契約（詳見 [docs/RESEARCH_CONTRACTS.md](RESEARCH_CONTRACTS.md)）：`actual` > `observed_finmind` > `assumed_deadline`，`available_fundamentals(..., as_of=...)` 只取 `publish_date < as_of`，`allow_assumed=True` 是明確的研究假設、不能宣稱嚴格無前視偏誤。`listed_date` 取交易所正式日期；`corporate_actions.ratio` 是參考價格比不是股數倍率，混合除權息無法拆分時保留 NULL 及原始 JSON。
