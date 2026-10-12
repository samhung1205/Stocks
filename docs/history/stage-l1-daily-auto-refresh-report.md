# Stage L1 交付報告：Daily Auto Refresh + Data Freshness Status

> **Stage L1.1 hotfix 已修正本報告下述幾項 correctness 問題**（multi-day gap 只補最新一天可能留下 internal hole、cross-runner locking 看不見彼此、`latest_benchmark_date` 命名誤導、`status()` 每次 poll 都打外部 TWSE、scheduler 重構後 watchlist deep-fetch 的 trigger 範圍）——見 [`docs/history/stage-l1-1-daily-refresh-hardening-report.md`](stage-l1-1-daily-refresh-hardening-report.md)。本檔案保留原樣作為當時的紀錄，不回頭改寫。

日期：2026-09-26。前置條件：v1.0.0（Stage K2 後）已建立 — 本階段在既有 production 之上加功能，未修改 backtest / strategy / walk-forward / portfolio research semantics，未做 intraday/live quote。

## 1. Existing pipeline audit（先讀後改的結果）

在動手前先確認目前一次「daily update」實際涵蓋哪些 dataset，避免假設：

- **`app/services/market_daily.py`** 是唯一正確的 daily/EOD 輕量層寫入路徑：
  - `update_daily()`：以 `twse.resolve_latest_trading_date()`（真的查詢官網逐日端點，找到「全市場 1000+ 檔」的最近交易日，不是猜 weekday）為基準日，一次補齊 `daily_quotes`（twse+tpex）、`valuations`（估值常晚一天發布，允許沿用最新可得的）、`institutional_daily`、`market_summary`（大盤/三大法人金額）。
  - `backfill_history(days)`：逐日回補 `daily_quotes` + `institutional_daily` + `market_summary`，已入庫日期自動跳過（`have` set），空回應視為假日直接跳過，可中斷續跑。
  - `fetch_month_revenue_bulk()`：月營收，Stage L1 未動。
- **`app/scheduler.py`**（Stage L1 前）：APScheduler 平日 15:05 觸發 `_daily_job()`（呼叫 `update_daily()` 一次 + 觀察名單 `deep_fetch.ensure_deep()`），加上月營收（每月 10-12 日）、週日清理。**沒有任何 startup catch-up、重試、鎖、或 freshness 狀態** — 排程被跳過（筆電睡眠、伺服器沒開）就只能等下一次固定時間，或使用者手動點按鈕。
- **Dashboard 既有手動更新**：`POST /api/admin/update-daily`（同步呼叫 `update_daily()`）、`POST /api/admin/backfill`（背景呼叫 `backfill_history(days)`）— Stage L1 前兩者互不協調，也不與排程協調，理論上可能同時寫同一批 `daily_quotes`/`market_summary`。
- **既有 partial/failed/checkpoint 語意的參考對象**：`app/services/history_backfill.py`（Strategy Lab 5 年回補）用 `fcntl.flock` 做同 DB 單一 runner 鎖（`runner_lock()`），`backfill_jobs`/`backfill_tasks` 兩張表記錄 pending/running/done/partial/empty/failed，crash 後 `status()` 會用「能不能拿到鎖」判斷 `running` 是否其實是 `interrupted`。Stage L1 的 `daily_refresh_runs` + `refresh_lock()` + `recover_interrupted()` 直接沿用同一套慣例（獨立鎖檔、獨立表，不共用 `backfill_jobs`，因為那是不同的邏輯資源——5 年歷史回補 vs. 每日輕量層）。
- **`trading_calendar`**：只由 Strategy Lab 5 年回補（`history_backfill.py` dataset='calendar'，抓 FinMind `TaiwanStockTradingDate` 全量交易日曆）寫入；全新安裝、只用基本面分析功能的使用者可能完全沒有這張表的資料。輕量層 `update_daily()`/`backfill_history()` 本身從不依賴它，交易日判斷完全靠向資料源查詢的結果（`resolve_latest_trading_date()`、空回應=假日）。
- **既有 dashboard 手動 UI**（`web/index.html`）在 Stage L1 前用**前端 JavaScript 猜 weekday**（平日 15:00 前後、跳過六日）判斷「應該已收盤的最近交易日」，明確在文案中承認「此判斷不知道國定假日」。這正是需求 §3 要避免的模式——Stage L1 把這個判斷整個搬到後端，改用 §3 的真實資料源證據。

結論：**唯一正確的 daily 寫入邏輯就是 `market_daily.update_daily()` / `backfill_history()`，Stage L1 全程只呼叫它們，不新增第二套寫入路徑。**

## 2. Auto-refresh lifecycle 設計

新模組 `app/services/data_freshness.py`，純 orchestration（呼叫既有 pipeline、決定要不要呼叫、什麼時候呼叫、加鎖、記 log），沒有自己的資料寫入邏輯。沒有引入 Celery / Redis，沿用專案既有的 APScheduler + SQLite。

### A. Startup catch-up

`app/main.py` lifespan：

```python
data_freshness.recover_interrupted()   # 先處理上次 crash 留下的 stuck 'running' row
data_freshness.startup_catchup_async() # 在 daemon thread 執行，不阻塞 server 啟動
```

`startup_catchup_async()` 立刻 `return`（thread 已 `start()`），web server 的 `Application startup complete` 不會被網路請求卡住。背景 thread 呼叫 `attempt_refresh('startup_catchup')`：

1. `twse.resolve_latest_trading_date()` 問真實資料源「現在最新的完整交易日是哪天」。
2. 若本機 `MAX(daily_quotes.date)` 已經 `>=` 這天 → 不做事（`already_fresh`）。
3. 若本機完全沒有資料（全新安裝）→ **刻意不自動觸發大規模回補**（`no_baseline_data`），保留「首次使用順序」給使用者手動執行——避免安靜地在背景跑一個小時的回補。
4. 缺口天數 ≤ 3 天 → 呼叫 `market_daily.update_daily()`（單日）。
5. 缺口天數 > 3 天（例如筆電休眠超過一個週末+）→ 呼叫既有 `market_daily.backfill_history(days)`（多日、可跳過已存在日期、遇假日自動略過），不是只補最新一天而放著中間的缺口。

### B. While-running scheduler

`app/scheduler.py`：

```python
sched.add_job(data_freshness.scheduler_tick,
              CronTrigger(day_of_week="mon-fri", hour=f"{start_hour}-{end_hour}",
                          minute=f"*/{interval}"))
```

`start_hour`/`end_hour`/`interval` 全部從 `app.config` 讀（見 §9），不是寫死。`scheduler_tick()` 呼叫同一個 `attempt_refresh('scheduler')`，語意與 startup catch-up 完全相同（同一個函式）。

**平日 CronTrigger 只是「什麼時候值得檢查」的效率考量，不是「今天是不是交易日」的判斷依據**——真正決定要不要寫資料的仍然是 `resolve_latest_trading_date()` 這個真實資料源查詢（見 §3）。

### C. Missed schedule

`attempt_refresh()` 是 idempotent、可重複呼叫的純函式式入口：只要本機還落後、還在重試時間窗內，任何觸發來源（startup / scheduler tick / 下次啟動）呼叫都會嘗試同一件事。不需要使用者記得哪天沒開機——下次啟動的 `startup_catchup_async()` 自動處理，缺口用 §2A 第 5 點的多日回補邏輯補齊。

## 3. Trading-day awareness（不只用 weekday）

`expected_trading_day` 完全由 `twse.resolve_latest_trading_date()` 決定——這個函式本來就是**向官網逐日端點實際查詢**（`daily_all_by_date()`），要求回應筆數 > 1000（全市場正常規模）才承認是一個有效交易日，往前找最多 10 天。假日、臨時休市當天官網回應是空的或殘缺的，函式會自動跳過往前找，**從不用 `weekday() < 5` 猜**。

這與 `update_daily()` 本身原本使用的基準日邏輯完全一致（Stage L1 沒有改它），也是既有 `history_backfill.py._official_market_state()` 判斷「官方休市」的同一種精神（雙邊掛牌都拿不到資料才算休市，避免把單邊殘缺回應誤判成假日）。

`trading_calendar`（Strategy Lab 回補才會有資料）被用在 §6 freshness API 的 `pending_missing_dates` 上作為**更精準的「哪幾天沒補」列表**（優先），沒有資料時明確標記 `pending_missing_dates_source: "weekday_estimate"` 退回粗估並在文案提示「可能含國定假日」——這是唯讀的呈現用途，不是拿來決定要不要寫入資料，因此不違反§3的核心要求。

市場開盤但資料不完整（例如 `update_daily()` 抓到的 `quotes` 筆數低於 `MIN_COMBINED_QUOTE_ROWS=800`，仿照既有「全市場正常 1000+ 檔」與 `history_backfill.py` 500/400 門檻的精神）一律標記 `status='partial'`，**不會被標成 `completed`**，且下一次 `attempt_refresh()` 即使 `MAX(date)` 已經等於 target 也會因為「上一筆是 partial」繼續重試（見 §5），不會卡死在假的「已同步」狀態。

## 4. Concurrency safety

`refresh_lock()`：與 `history_backfill.runner_lock()` 完全同一種手法（同 DB 路徑 sha256 → 獨立 `.lock` 檔、`fcntl.flock(LOCK_EX|LOCK_NB)`），但用**獨立的鎖檔**（`daily-refresh-<digest>.lock`），因為這是不同的邏輯資源（每日輕量層 vs. 5 年歷史回補）。

- **Auto 與 manual 同時觸發**：`POST /api/admin/update-daily`（`data_freshness.run_manual_update()`）與 `POST /api/admin/backfill`（`data_freshness.run_manual_backfill()`）都改走同一把鎖；拿不到鎖時 manual 路由回 HTTP 409，auto tick 安靜跳過這次（`reason: "locked"`，不是錯誤，下次 tick 再試）。
- **App restart**：`recover_interrupted()`（啟動時 + `status()` 內每次防禦性呼叫）探測鎖——鎖拿得到代表沒有真的在跑，任何卡在 `running` 的 row 標記 `failed`（`error: "interrupted: process restarted"`），不會永遠卡住，也不會被誤判成「還在進行中」。
- **Update interrupted（鎖確實被別的 runner 持有）**：`recover_interrupted()` 探測失敗（`RuntimeError`）就直接放著不動，不誤傷真的在跑的 run。
- **Repeated same-day check / already complete date**：`attempt_refresh()` 每次都重新查 `MAX(daily_quotes.date)` 與 `daily_refresh_runs` 最新一筆狀態，`already_fresh` 直接 no-op，完全 idempotent（重複呼叫零副作用、零多餘網路請求）。

## 5. Retry policy

`daily_refresh_runs` 是 append-only attempt log（一次 attempt 一筆，running → 終態原地更新，同一個 `id`）。`_retry_budget_exhausted()`：

- 對同一個 `target_date`，累計 `status IN ('failed','partial')` 的次數 ≥ `AUTO_REFRESH_MAX_RETRIES`（預設 6）→ 不再重試。
- 或距離第一次失敗/部分的時間 > `AUTO_REFRESH_RETRY_WINDOW_MINUTES`（預設 180 分鐘）→ 不再重試。
- 兩者任一觸發即回 `retry_budget_exhausted`，freshness 狀態進入 `failed`，**保留 `latest_error`**（最後一次錯誤訊息），不會無限快速重試（受 scheduler 的 15 分鐘 poll 間隔 + 上述門檻雙重限制）。
- `partial` 不會被當成「已經補完」而停止重試——見 §3 最後一段；`update_daily()` 對同一天重跑天生是安全的（`upsert_many` 只更新這批資料實際提供的欄位，不會用較少的資料洗掉既有較完整的資料）。

## 6. Data freshness status（`GET /api/admin/data-freshness`）

統一 read-side contract，`app.services.data_freshness.status()`，從不回單一 boolean：

```json
{
  "now": "...", "timezone": "Asia/Taipei",
  "expected_trading_day": "...", "latest_completed_quote_date": "...",
  "latest_institutional_date": "...", "latest_valuation_date": "...", "latest_benchmark_date": "...",
  "freshness": "fresh|partial|stale|failed|unknown",
  "current_status": "idle|running", "current_run": {...}|null,
  "last_successful_refresh": {...}|null, "last_attempted_refresh": {...}|null,
  "pending_missing_dates": [...], "pending_missing_dates_source": "trading_calendar|weekday_estimate|none",
  "retry_count_for_target": N, "retry_budget_exhausted": bool, "latest_error": "..."|null,
  "auto_refresh": {"enabled": bool, "check_time": "15:05", "poll_interval_minutes": 15,
                    "max_retries": 6, "retry_window_minutes": 180}
}
```

`freshness` 判斷（永遠結合 `daily_refresh_runs` 記錄 + 實際資料表 `MAX(date)`，不是單一旗標）：`running` 中 → `partial`/`stale`（依本機是否已達 target）；本機完全無資料 → `stale`；資料源查不到 → `unknown`；本機已達 target 且上一筆非 partial → `fresh`；已達 target 但上一筆 partial → `partial`；未達 target 且重試預算已盡 → `failed`；否則 → `stale`（還在重試窗內，下一次 tick 會再試）。

## 7. Dashboard UI（`web/index.html`）

沿用既有 Academic Research Desk C+ 卡片樣式，把原本前端 weekday 猜測（`expectedTradingDate()`/`checkStale()`）整段換成 `loadFreshness()` 輪詢 `/api/admin/data-freshness`（每 20 秒，`!document.hidden` 才打）：

- 「資料截至 … ｜最新交易日 … ｜狀態：已同步／待更新／更新中／部分資料未完成／更新失敗／無法確認」+ 錯誤訊息 + 待補交易日清單（含來源標記）。
- **stale/partial/failed/running 一律不隱藏**（`banner.style.display` 只在 fetch 本身失敗時才顯示錯誤訊息，其餘任何 freshness 狀態都保持可見）——不像舊版只在「過期」才彈出、「已同步」時整個藏起來。
- 「立即更新今日行情」保留作為 manual override，按下時走同一把鎖、同一個 log（不會跳過 §6 status 的記錄），成功/失敗都會反映在下一次 poll。
- Advanced「資料維護與回補工作」`<details>` 區塊維持原樣不變（同步股票清單／更新今日行情／回填 180 天／抓月營收／Strategy Lab 5 年回補）。

## 8. Research isolation

- `app/services/data_freshness.py` 全程只 import `app.datasources.twse`（僅用 `resolve_latest_trading_date`）與延遲 import 的 `market_daily`/`deep_fetch`；**沒有 import `mis_snapshot` 或 `fugle`**（`tests/test_data_freshness.py::test_intraday_or_future_date_is_never_treated_as_a_completed_daily_close` 用 AST 解析 import 語句斷言這件事，不是字串比對）。
- 唯一會把 `daily_refresh_runs` 標成 `completed` 的路徑是 `market_daily.update_daily()`/`backfill_history()` 的回傳值，兩者都是官方 EOD 端點，從不讀盤中快照。
- 完全沒有觸碰 Strategy Lab / Walk Forward / Portfolio 的任何程式碼、契約版本字串、或 immutable research evidence；新的 `daily_refresh_runs` 表只影響「輕量層今天有沒有資料」，不影響任何既有 research run 的 reproducibility（`backtest_runs`/`walkforward_folds`/`portfolio_research_runs` 完全沒被碰）。

## 9. Configuration（`app/config.py`，`.env` 可覆寫）

```
AUTO_REFRESH_ENABLED=1
AUTO_REFRESH_TIMEZONE=Asia/Taipei
AUTO_REFRESH_CHECK_TIME=15:05             # repo 內沒有官方「資料何時就緒」來源，沿用 Stage L1 前排程已用的保守 default
AUTO_REFRESH_POLL_INTERVAL_MINUTES=15
AUTO_REFRESH_MAX_RETRIES=6
AUTO_REFRESH_RETRY_WINDOW_MINUTES=180
```

## 10. Tests

`tests/test_data_freshness.py`（30 tests，全部離線、`twse.resolve_latest_trading_date()`/`market_daily.update_daily()`/`market_daily.backfill_history()` 全部 mock，temp SQLite DB，不打真實網路）：

- Startup catch-up：fully fresh no-fetch、holiday no-fetch（資料源沒有更新交易日）、single missed day → `update_daily()`、multi-day gap → `backfill_history()`、fresh install 不自動 bootstrap、資料源不可達不 crash。
- Incomplete/retry：不完整回應標 `partial` 不是 `completed`、partial 天即使日期已存在仍會重試、重試次數上限、重試時間窗上限、失敗保留 `latest_error`。
- Idempotency/concurrency：同一天完成後第二次呼叫 no-op、manual 與 auto 互相鎖住、auto 遇到鎖安靜跳過、`/api/admin/update-daily` 路由鎖住時回 409。
- Restart safety：crash 留下的 `running` row 會被 `recover_interrupted()` 標成 `failed`、真的在跑時不誤傷、`status()` 呼叫本身也會自我修復。
- Manual 路徑不變：`run_manual_update()` 呼叫的就是原本的 `market_daily.update_daily()`（同簽名、同回傳值包在 `result` 欄位裡）、`run_manual_backfill()` 是薄 passthrough、manual 即使遇到多日缺口也不會升級成 `backfill_history()`（只有 auto catch-up 路徑會）。
- Freshness contract：必要欄位齊全、fresh/stale/unknown/failed 各狀態、`pending_missing_dates` 優先用 `trading_calendar`、無資料時退回並標記 `weekday_estimate`、未來/盤中日期不會被當成 completed close（AST import 檢查）。
- Scheduler：poll 時間窗從 config 算出，不是寫死。

既有測試：3 個測試檔（`test_data_correctness.py`、`test_data_foundation.py`、`test_walkforward_contract.py`）裡 `schema_migrations` 筆數斷言從 5 改為 6（新增 v6 遷移只是幫 `daily_refresh_runs` 補一個 index，符合預期，非回歸）。全部離線測試（419 個，含新增的 30 個）與 `compileall` 均通過。

## 11. Browser verification

在真實本機 DB（`data/stocks.db`，真資料）上跑 `uv run uvicorn app.main:app --port 8788`：

- **A. Fresh state**：啟動時 startup catch-up 真的打了 TWSE 官網（`resolve_latest_trading_date()` 往回找到 2026-09-24 有完整資料），本機同一天 → `already_fresh`，banner 顯示「✓ … 狀態：已同步」，單行排列、右側按鈕、border-top 綠色語意色。
- **E. Manual update**：點「立即更新今日行情」，按鈕即時變成「更新中…」並 disable，log 顯示真的呼叫了 `update_daily()`（多個 TWSE httpx 請求），完成後 `daily_refresh_runs` 多一筆 `trigger='manual', status='completed'`，`GET /api/admin/data-freshness` 的 `last_successful_refresh`/`last_attempted_refresh` 正確填入，`market_summary`/`valuations`/`institutional_daily` 的日期也一併補到 2026-09-24。
- **C/D. update running / partial / failed**：直接在瀏覽器 console 呼叫 `renderFreshness()` 帶入合成的 stale/running/partial/failed 狀態物件（後端狀態機邏輯已由 §10 的 30 個離線測試覆蓋更精確、更可重現的組合；瀏覽器驗證聚焦在「前端渲染是否正確反映」，沒有等到真的收盤或搞壞真實 DB）——四種狀態皆確認 banner **未被隱藏**、icon/顏色/文案（含錯誤訊息、待補交易日清單）正確、running 時按鈕 disable 且文字變「更新中…」。
- **G. Mobile 375px**：`resize_window(preset:"mobile")` 下 failed 狀態文字可讀、無 overflow、按鈕在文字下方獨立一行（CSS 既有 `@media (max-width:760px)` 規則，未修改）。
- **H. Console 0 error/warning**：開新分頁載入頁面（排除先前多次 resize 造成的無關 `net::ERR_NETWORK_CHANGED` 雜訊），確認 0 error / 0 warning。
- 測試中寫入真實 DB 的合成資料（一筆假 `partial` row）已於驗證後刪除；manual update 產生的真實成功 log 保留（本來就是使用者會想要的最新資料）。

## 12. Known limitations

- **Stage L1 一次只補「最新一個交易日」**（`update_daily()`）；缺口 > 3 天才會用既有的多日 `backfill_history()`，兩者的切分是天數捷徑，不是逐日 checkpoint。
- **全新安裝（`daily_quotes` 完全空）auto-refresh 不會自動觸發**——刻意避免背景默默跑一個大型回補；使用者仍需照 README「首次使用順序」手動執行。
- **`pending_missing_dates` 在沒有 `trading_calendar` 資料時是平日粗估**（明確標記 `weekday_estimate`），可能把國定假日算進「待補」清單——這是唯讀呈現的已知限制，不影響 §3 的寫入正確性判斷。
- **重試視窗與檢查時間都是保守 default**（repo 內沒有官方「資料何時就緒」的權威來源），可用 `.env` 調整。
- **Deep fetch（個股層）與 auto-refresh 的鎖未整合**——`deep_fetch.ensure_deep()` 針對觀察名單個股的補資料仍是各自獨立路徑，衝突機率低（不同表為主）但未強制序列化；如果之後發現實際衝突，應該擴大 `refresh_lock()` 的涵蓋範圍，而不是新開一把鎖。

## 13. Stage L2 intraday readiness

Stage L1 完全沒有觸碰盤中/準即時報價（`app/datasources/mis_snapshot.py`、`app/datasources/fugle.py`、觀察名單既有的 5 秒延遲快照）。若之後要做 Stage L2（intraday/live quote）：

- 應該是**獨立的 service + 獨立的表**（例如 `intraday_quotes` 或類似），不能把 `daily_quotes` 的「官方收盤」語意跟盤中報價混在一起，也不能讓 `data_freshness.py` 的 `daily_refresh_runs`/`completed` 語意被盤中資料污染——這點已寫進 `AGENTS.md`「Daily auto-refresh invariant」。
- `refresh_lock()`/`daily_refresh_runs` 的併發/重試慣例可以參考複用，但盤中資料的「新鮮度」定義（秒級/分鐘級 TTL，而非交易日）與 Stage L1 的 freshness contract 語意完全不同，應該是新的 status 端點而不是塞進 `/api/admin/data-freshness`。
- 現有富果/MIS 的 pluggable 介面（`app/datasources/realtime_base.py`）已經是分開的抽象層，Stage L2 應該接在那裡，不是接在 `market_daily.py`。

## Output

本報告：`docs/history/stage-l1-daily-auto-refresh-report.md`。已同步更新 `README.md`、`docs/ARCHITECTURE.md`（新增 §8 Daily auto-refresh flow，章節重編號）、`docs/PROJECT_STATUS.md`、`AGENTS.md`（新增 invariant + known limitation）。未開始 intraday/live quote，本階段到此停止。
