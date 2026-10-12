# Stage L1.1 交付報告：Daily Auto Refresh Correctness Hardening

日期：2026-09-26。範圍：只修正 Stage L1 audit 後發現的 correctness/regression 問題，沒有新增功能，沒有開始 intraday/live quote。

## 0. 前置：Stage L1 audit 摘要

在動手前重讀了 `docs/history/stage-l1-daily-auto-refresh-report.md`、`AGENTS.md`、當時的 `app/services/data_freshness.py`、`app/services/market_daily.py`、`app/services/history_backfill.py`、`app/scheduler.py`、Dashboard freshness API/UI（`web/index.html`）。確認了 6 個真實問題（詳見下方各節），全部屬於「既有邏輯不夠精確」而不是「架構錯誤」——不需要重寫，只需要把幾個判斷基準換成更準確的證據來源。

## 1. Multi-day gap correctness（internal hole）

**問題**：原本 `gap_days = (target - local_max).days`（日曆天數）`> 3` 才用 `backfill_history()`，否則一律 `update_daily()`（只補 target 這一天）。日曆天數差不等於「缺幾個交易 session」——例如 Thursday 有資料、Friday 是國定假日、Monday/Tuesday 都是真交易日但都缺，若在 Tuesday 啟動，`resolve_latest_trading_date()` 正確回傳 Tuesday，但 `(Tuesday - Thursday).days = 5`，剛好還是 `>3` 觸發多日回補——這個特定例子沒事；但只要調整成 3 天內的窗口（例如 Thursday→Friday 只隔 1 天，但 Friday 若不是假日而是真的被漏掉的交易日，只補 Friday 這天其實沒錯）。真正的漏洞在別處：**`already_fresh` 判斷只看 `MAX(daily_quotes.date) >= target`**——如果較晚的日期已經有資料、較早的某天卻是空的（例如先前一次不完整的多日回補漏了中間一天），`MAX(date)` 完全看不到這個 hole，會被判定 `fresh`。

**修正**（`app/services/data_freshness.py`）：

- 新函式 `_missing_sessions_for_write(conn, local_max, target)`：回傳「`local_max` 到 `target` 之間、`daily_quotes` 裡真的沒有資料的交易 session 清單」，而不是單看 `MAX(date)`。
  - **優先用 `trading_calendar`**（Strategy Lab 回補建立的真實交易日曆）：`SELECT date FROM trading_calendar WHERE date > :a AND date <= :b` 再減掉 `daily_quotes` 裡已有的日期——這個模式下允許往回看（bounded `HOLE_CHECK_LOOKBACK_DAYS=10` 天），因為 calendar 是真證據，不是猜的。
  - **沒有 calendar 覆蓋時退回 weekday 候選清單**——但這個 fallback **只往前看**（`local_max` 到 `target`），**絕不往回猜過去** `local_max` 之前的日期：往回猜會把「從來沒嘗試過的歷史」誤判成 hole（第一版實作犯過這個錯，見下方 §1.1）。
- `attempt_refresh()` 的 `already_fresh` 判斷改用 `_missing_sessions_for_write()` 算出的清單是否為空（外加沿用既有的「上次 target 的紀錄是 partial 就繼續重試」邏輯），不再只比較 `MAX(date)`。
- 補資料策略：`len(missing) <= 1` 用 `update_daily()`（單日）；`> 1` 用 `backfill_history(days=...)`，天數從**最早缺失的 session** 算起，不是從 `local_max` 算日曆差。
- **補完後一定重新驗證**：呼叫端無論走哪個分支，fetch 完都會重新跑一次 `_missing_sessions_for_write()`；只要還有缺，狀態是 `partial`，絕不是 `completed`。`MIN_COMBINED_QUOTE_ROWS` 門檻（單日路徑）也保留。

### 1.1 為什麼沒有做「用官方證據確認候選日是不是真的假日」

第一版實作在 `_missing_sessions_for_write()` 沒有 `trading_calendar` 覆蓋時，額外呼叫 `twse.daily_all_by_date()` 逐一確認每個候選日是不是真的假日（模仿 `history_backfill._official_market_state` 的手法），想避免「沒有 calendar 時，假日被永遠標成 partial」。**這個呼叫點在 `attempt_refresh()` 的快速判斷路徑（每次 scheduler tick 都會跑，即使沒有東西要做）**，在離線測試環境下造成一次測試掛起超過 120 秒（`unittest` 沒 mock 到 `daily_all_by_date`，打了真實網路且不像 `resolve_latest_trading_date()` 有既有的 retry/timeout 慣例）。這證明了這個設計即使在正式環境也有風險：scheduler 每 15 分鐘跑一次，任何一次網路慢或資料源異常都可能讓這次 tick 卡住。

**最終決定**：拿掉這個網路確認步驟。沒有 `trading_calendar` 時，一個真的假日夾在缺口裡，最壞情況是被判定成 `partial`/`failed`（因為 `daily_quotes` 永遠不會有該日資料）直到 retry budget 用完，過程中會多打幾次已經在打的 `update_daily()`/`backfill_history()`（本來就 idempotent、有節流），不會多開新的網路呼叫類型。**這個失敗模式是誠實的「無法確認」，不是危險的假 `fresh`**——完全符合本階段的核心要求，且比引入一個會 hang 的呼叫點安全。已在程式碼註解與 `AGENTS.md` 記錄這個取捨，避免未來又把它加回去。

### 1.2 Regression 測試

`tests/test_data_freshness.py::StartupCatchupTests`：
- `test_internal_hole_not_fresh_calendar_evidence` — Thursday 有資料、`trading_calendar` 記錄 Thu/Mon/Tue（Fri 是假日），在 Tuesday 啟動；`backfill_history()` mock 只填 Tuesday（模擬「原本的 bug」），驗證最終狀態是 `partial`，不是 `completed`，且正確選擇了多日路徑（不是 `update_daily()`）。
- `test_internal_hole_not_fresh_weekday_fallback` — 同樣 Mon+Tue 缺口，但完全沒有 `trading_calendar`；驗證 weekday fallback 一樣能抓到兩個 session 都缺（連同中間的 Friday 一起視為候選，因為沒有證據排除它），backfill mock 全部補齊後才是 `completed`。
- `test_weekday_fallback_never_flags_dates_before_local_max` — 確認 fallback 不會把 `local_max` 之前的歷史誤判成 hole（§1.1 提到的第一版 bug 的直接回歸測試）。

## 2. Cross-runner locking audit

**Audit 結果**：

| Writer | `daily_quotes` | `valuations` | `institutional_daily` | `market_summary` |
|---|---|---|---|---|
| `market_daily.update_daily()` / `backfill_history()`（Stage L1 daily refresh 呼叫） | ✅ | ✅（僅 update_daily） | ✅ | ✅ |
| `history_backfill.py`（Strategy Lab 5 年回補） | ✅（歷史日期，`end < today` 的驗證擋住今天） | ✅ | ❌ | ❌ |
| `deep_fetch.py`（個股深度抓取） | ✅（per-stock） | ❌ | ✅ | ❌ |

`daily_quotes` 與 `valuations` 是**兩個 runner 都會寫**的表。Stage L1 原本的 `refresh_lock()`（`daily-refresh-<digest>.lock`）與既有 `history_backfill.runner_lock()`（`backfill-<digest>.lock`）是**兩個獨立的鎖檔**——彼此看不到對方，理論上可能同時寫入。SQLite WAL 模式下沒有設定 `busy_timeout`，兩個 writer 真的同時 commit 時會直接丟 `database is locked`，不會等待重試。

**最小安全修正**（採用題目給的選項 A：共享同一個 DB writer coordination mechanism）：

- `data_freshness.refresh_lock()` 與 `history_backfill.runner_lock()` 改成**同一個鎖檔**：兩邊都用 `db-writer-<digest>.lock`（原本分別是 `daily-refresh-<digest>.lock` 與 `backfill-<digest>.lock`）。沒有新增鎖的抽象層、沒有 distributed lock——單純讓兩個既有的 `fcntl.flock` 使用者指向同一個檔案，這正是 `flock` 本來的用途。
- 各自的例外型別維持不變（`refresh_lock()` 拋 `RuntimeError`、`runner_lock()` 拋 `ValueError`），不影響既有呼叫端與既有測試（`tests/test_data_foundation.py` 對 `ValueError` 的斷言原封不動通過）。
- 效果：Strategy Lab 5 年回補在跑的時候，daily auto-refresh／手動 update-daily／手動 180 天回填全部會被鎖住（回傳「locked」而不是搶著寫），反之亦然。兩者都是相對低頻、重量級操作，完全序列化是可接受、也是最簡單正確的做法。

**沒有涵蓋的部分（`deep_fetch.py`）**：個股深度抓取（如觀察名單自動刷新、股票頁按需抓取）也會寫 `daily_quotes`/`institutional_daily`，但**沒有**被要求納入這次的三個 runner（題目明確列出的是 Stage L1 daily refresh / market_daily backfill / Strategy Lab history_backfill）。把 `deep_fetch` 的所有呼叫點都包進同一把鎖，會讓「開股票頁」這種輕量操作被一個可能跑十分鐘的歷史回補卡住，不符合「最小安全修正」的精神。改用**互補做法**：

- `app/db/__init__.py` 的 SQLite connect pragma 加上 `PRAGMA busy_timeout=5000`（5 秒）。這是「證明 upsert 足以安全並行」的那個選項的實作——WAL 模式本來就只允許一個 writer transaction 同時進行，沒有 `busy_timeout` 時第二個 writer 會立刻收到 `database is locked` 錯誤；有了它，第二個 writer 會等待、重試，最多 5 秒，幾乎必定能在這個時間內拿到鎖（實測所有既有 upsert 都是單批 ≤500 列、毫秒級提交）。這對「三個主要 runner 之外」的所有殘餘並行寫入（主要是 `deep_fetch`）是一個安全網，不是取代上面的共享鎖。

### 2.1 Regression 測試

`tests/test_data_freshness.py::CrossRunnerLockingTests`：
- `test_history_backfill_lock_blocks_daily_refresh_lock` / `test_daily_refresh_lock_blocks_history_backfill_lock` — 雙向都會擋住。
- `test_both_locks_use_the_same_file` — 直接斷言兩邊算出的鎖檔路徑字串相同。
- `test_manual_update_blocked_while_history_backfill_runs` — `run_manual_update()` 在 Strategy Lab 回補跑的時候會拋 `RuntimeError`（對應 admin route 的 409）。
- `test_scheduler_tick_skips_quietly_while_history_backfill_runs` — auto-refresh 遇到 Strategy Lab 回補在跑，安靜跳過（`reason: "locked"`），不是報錯。

## 3. Benchmark freshness

**Audit 結果**：

- `daily_quotes`/`valuations`/`institutional_daily`/`market_summary`（Stage L1 daily refresh 涵蓋的四張表）裡，`market_summary` 存的是**大盤摘要卡**（加權指數、成交金額、三大法人金額），不是 Strategy Lab 用的 benchmark。
- Strategy Lab 真正的研究 benchmark 是 `benchmark_series`（`benchmark_id='TAIEX_TR'`，總報酬指數），只有 `history_backfill.py`（Strategy Lab 5 年回補）的 `_fetch_benchmark()` 會寫入，**Stage L1 daily refresh 從未更新它**。
- Stage L1 的 `status()` 卻把 `market_summary.date` 命名成 `latest_benchmark_date`——容易讓人誤以為它代表 Strategy Lab benchmark 的新鮮度，實際上兩者是完全不同的表、不同的 pipeline、不同的更新頻率。
- 新的 Strategy Lab research run（`panel.build_panel()`）拿到的 benchmark 覆蓋範圍，就是上一次 Strategy Lab 歷史回補跑到哪天，**跟今天的 daily auto-refresh 完全無關**。

**回答三個問題**：
1. Daily auto refresh 是否會更新 benchmark？**不會。**
2. 若不會，global freshness 可以叫 fresh 嗎？**不行**——global `freshness` 只描述輕量層四張表，不能被誤讀成「連 benchmark 也是最新的」。
3. Strategy / new research run 如何得到最新 benchmark？**維持現狀**——只有再跑一次 Strategy Lab 的歷史回補（`history_backfill.py` 的 `benchmark` dataset task）才能推進 `benchmark_series`。這是既有、獨立的機制，這次沒有改它。

**選擇的方案**：選項 B（dataset-level state，不要用 TAIEX price index 取代 Total Return benchmark）：

- `status()` 新增 `research_benchmark` 區塊：`{"benchmark_id": "TAIEX_TR", "latest_date": <benchmark_series 裡 TAIEX_TR 的 MAX(date)>, "updated_by": "history_backfill.py (Strategy Lab historical backfill) only", "tracked_by_daily_auto_refresh": false}`——明確、獨立、不會被誤讀成跟著 light layer 一起新鮮。
- 原本誤導的 `latest_benchmark_date`（其實是 `market_summary.date`）**改名**為 `latest_market_summary_date`，語意才正確。這是這個欄位第一次真正被外部使用前的修正（`web/index.html` 原本沒有讀它），不算破壞既有前端。
- **沒有**把 `market_summary` 的 TAIEX 價格指數拿來冒充 Total Return benchmark——兩張表繼續分開報告，完全遵守題目的明確要求。
- **沒有**把 `benchmark_series` 納入 daily auto-refresh 的寫入範圍：`_fetch_benchmark()` 的邏輯相當複雜（月分塊抓取、與 `trading_calendar` 交叉核對、`calendar_false_positive_dates`/`calendar_missing_open_dates` 等假日修正），把它塞進每 15 分鐘一次的 tick 裡是真正的新功能開發，不是本階段允許的「correctness hardening」。留給未來一個獨立、有明確範圍的 stage 決定。

### 3.1 Regression 測試

`tests/test_data_freshness.py::BenchmarkFreshnessTests`：
- `test_research_benchmark_not_claimed_fresh_by_daily_refresh` — light layer `fresh` 時，`research_benchmark.latest_date` 仍然是 `None`（`benchmark_series` 完全沒資料），`tracked_by_daily_auto_refresh` 是 `False`。
- `test_research_benchmark_date_reads_taiex_tr_only` — 塞一筆 `TAIEX_TR` 跟一筆假的 `SOME_OTHER_INDEX`，確認只讀 `TAIEX_TR`。
- `test_market_summary_field_not_mislabelled_as_research_benchmark` — 確認新欄位名 `latest_market_summary_date` 存在、舊的誤導欄位名 `latest_benchmark_date` 不存在。

實機驗證（`data/stocks.db` 真實資料）：`GET /api/admin/data-freshness` 回傳 `latest_market_summary_date: "2026-09-24"`（跟 light layer 一致，fresh）同時 `research_benchmark.latest_date: "2026-09-18"`（明顯落後——這正是修正前會被完全隱藏、修正後才看得見的落差）。

## 4. Freshness polling / upstream traffic

**Audit 結果**：`status()` 原本每次呼叫都會執行 `_resolve_target_trading_day()` → `twse.resolve_latest_trading_date()`，這個函式內部最多往回查 10 天、每天一個 HTTP request（找到第一個 >1000 列的回應才停）。Dashboard 每 20 秒 poll 一次 `/api/admin/data-freshness`，等於**平均每 20 秒對 TWSE 官網打至少一次請求**（多半是 1-2 次，因為通常很快就找到），使用者開著分頁一整天就是幾千次請求——不符合「禮貌抓取」的專案慣例（`POLITE_INTERVAL` 本來就是為了這個目的存在的）。

**修正**：

- 新增 process-local TTL 快取：`_target_cache = {"value": None, "at": 0.0}`，`_cached_target_trading_day()` 在快取存活時間內（`AUTO_REFRESH_STATUS_CACHE_SECONDS`，預設 60 秒，可設定）直接回傳快取值，不打網路。
- 快取只用在 `status()`（read-side）。**任何一次真正的 attempt**（`attempt_refresh()`，也就是 startup catch-up、scheduler tick、`run_manual_update()`）一律呼叫 `_resolve_target_trading_day()`（uncached），確保「要不要真的補資料」這個決策永遠基於最新查詢，不會被 60 秒的快取耽誤或誤導。
- `_resolve_target_trading_day()` 每次成功解析都會**順便更新共用快取**，所以 `status()` 在任何一次真實 attempt 之後幾乎立刻就能看到最新值，不用等自己的 60 秒 TTL 到期。
- 效果：dashboard 20 秒 poll 一次 `status()`，只要在 60 秒 TTL 內，最多每 60 秒才會有一次真正的 TWSE 查詢（不是每 20 秒）；scheduler tick 本來就是 15 分鐘一次，不受影響，一樣每次都重新驗證。

### 4.1 Regression 測試 + 實機驗證

`tests/test_data_freshness.py::StatusUpstreamTrafficTests`：
- `test_repeated_status_reads_share_one_cached_resolution` — 連續呼叫 `status()` 10 次，`resolve_latest_trading_date` 只被呼叫 1 次。
- `test_cache_expires_after_ttl` — TTL 設 0 秒時，兩次呼叫各自重新解析。
- `test_a_real_attempt_always_resolves_uncached` — 先呼叫一次 `status()`（填入快取），緊接著呼叫 `attempt_refresh()`，確認它繞過快取、真的重新查詢並正確完成補資料。

實機驗證（真實 `data/stocks.db`、真實伺服器）：連續呼叫 `GET /api/admin/data-freshness` 5 次（每次間隔 300ms），伺服器 log 顯示**這 5 次呼叫沒有產生任何新的 TWSE HTTP request**（唯一的兩筆 TWSE request 是伺服器啟動時 startup catch-up 的那一次，發生在這 5 次 poll 之前）。

## 5. Preserve previous scheduler behavior（watchlist deep-fetch）

**Audit 結果**：Stage L1 前的 `_daily_job()`：

```python
def _daily_job():
    try: market_daily.update_daily()
    except Exception as e: log.error(...)
    try:
        with engine.begin() as conn:
            watch = query_all(conn, "SELECT stock_id FROM watchlist")
        for r in watch: deep_fetch.ensure_deep(r["stock_id"])
    except Exception as e: log.error(...)
```

固定平日 15:05 觸發一次，**不論 `update_daily()` 成功與否都會接著跑觀察名單深度刷新**（因為第一個 `try/except` 吞掉例外後繼續往下）。Stage L1 把 `_daily_job()` 整個換成 `data_freshness.scheduler_tick()`，deep-fetch 邏輯搬進了 `_record_attempt()`：**沒有被意外移除**（`_watchlist_deep_refresh()` 仍然存在且會被呼叫），但範圍條件變了——`if status == "completed" and not was_completed_before`。

進一步 audit 發現一個**沒有被記錄的行為擴張**：`_record_attempt()` 對所有 `trigger`（含 `manual`）一視同仁，代表 Stage L1 上線後，使用者手動點「立即更新今日行情」也會觸發整個觀察名單的深度刷新——這是 Stage L1 前從未有過的行為（手動按鈕過去只呼叫 `market_daily.update_daily()`，deep-fetch 完全綁在排程上）。

**判定**：這是**刻意但未記錄的擴張**，不符合「不污染既有 contract」的精神——手動點擊不應該無聲地多做一件過去不會做的事。**修正**：

- `_record_attempt()` 的觸發條件加上 `and trigger != "manual"`，恢復成「只有自動驅動（`scheduler`／`startup_catchup`）才會觸發觀察名單深度刷新」，跟 Stage L1 前完全一致。
- `startup_catchup` 保留觸發資格：它在語意上就是「排程被跳過、下次啟動時補跑」，跟原本 15:05 固定排程的精神一致，不是新增功能，是還原「排程有跑就該做的事」。
- 確認 deep-fetch 的成功/失敗**不會**回頭影響 `daily_refresh_runs` 記錄的 `status`（本來就是先寫完 status 才呼叫 `_watchlist_deep_refresh()`，例外被內部 try/except 吞掉只記 log）——符合「不要把 deep-fetch 納入 daily research freshness 判定」的要求，這部分 Stage L1 從一開始就是對的，不需要改。

### 5.1 Regression 測試

`tests/test_data_freshness.py::WatchlistDeepFetchScopeTests`：
- `test_scheduler_completion_triggers_watchlist_deep_fetch` / `test_startup_catchup_completion_triggers_watchlist_deep_fetch` — 自動觸發確實會呼叫 `deep_fetch.ensure_deep()`。
- `test_manual_completion_does_not_trigger_watchlist_deep_fetch` — 手動觸發**不會**呼叫（這是本節修正的核心回歸測試）。
- `test_deep_fetch_failure_does_not_change_recorded_status` — `ensure_deep` 拋例外，`daily_refresh_runs` 記錄的 `status` 依然是 `completed`（deep-fetch 從不影響 freshness 判定）。

## 6. Verification

### 6.1 新增/更新的自動化測試

`tests/test_data_freshness.py`：33 → **48** 個測試（新增 18 個，含上面各節列出的），全部離線、全部 mock 外部呼叫，執行時間 < 1 秒（無 hang）：

- internal missing trading day cannot be fresh — §1.2 兩個 `test_internal_hole_not_fresh_*`
- 2-day/3-day catch-up fills all sessions — `test_multi_day_gap_uses_backfill_history_not_update_daily`（改用 `_fill_weekdays` helper 讓 mock 真的填滿整段區間，不再只填最後一天）
- latest date present + middle hole = not fresh — 同 §1.2
- history backfill vs daily refresh concurrency safe — §2.1 四個測試
- benchmark freshness truthful — §3.1 三個測試
- repeated freshness UI reads do not hammer upstream — §4.1 三個測試
- scheduler previous non-research behavior not silently lost — §5.1 四個測試

### 6.2 既有測試

`uv run python -m unittest discover -s tests -p 'test_*.py'`：**437 個測試全部通過**（419 個 Stage L1 結束時的既有測試 + 18 個新增，0 個回歸）。

### 6.3 靜態檢查

- `uv run python -m compileall app tests scripts`：乾淨無錯誤。
- `for f in web/js/*.js; do node --check "$f"; done`：8 個既有 JS 檔全部通過（這次沒有改任何前端檔案，純粹確認基線）。抽出 `web/index.html` 內嵌的 `<script>` 也用 `node --check` 驗證語法正確。
- `git diff --check`：無空白字元錯誤。

### 6.4 實機驗證（真實 `data/stocks.db`）

- 啟動伺服器，觀察 startup catch-up 正確用真實 `trading_calendar` 資料判斷 `internal_hole_check_source: "trading_calendar"`、`internal_hole_detected: false`。
- 連續 5 次 `GET /api/admin/data-freshness`（300ms 間隔）：`freshness` 穩定回報 `fresh`，`research_benchmark.latest_date` 正確顯示落後的 `2026-09-18`（對照 light layer 的 `2026-09-24`），且伺服器 log 證實這 5 次呼叫沒有觸發任何新的 TWSE 請求。
- Dashboard 頁面重新整理：banner 正常顯示「已同步」，console 0 error / 0 warning。
- 確認測試過程沒有在真實 DB 留下任何測試用的假資料（`daily_refresh_runs` 只有 Stage L1 階段那筆合法的 `manual`／`completed` 紀錄）。

## 7. Known limitations（延續／更新）

- 沒有 `trading_calendar` 覆蓋時，weekday fallback 是保守但不完美的：真的假日夾在缺口裡會被當成「應該有資料」，導致多重試幾次才 settle 成 `failed`（不會誤判 `fresh`，見 §1.1 的取捨說明）。裝過 Strategy Lab 5 年回補的使用者不受影響（`trading_calendar` 已有資料）。
- `benchmark_series`／Strategy Lab research benchmark 的新鮮度**仍然**完全獨立於 daily auto-refresh，這次只是把這個既有事實誠實地報告出來（`research_benchmark` 區塊），沒有讓它自動跟上。
- `deep_fetch.py` 的其他呼叫點（股票頁按需抓取、篩選器命中自動抓取）仍然不在共享鎖的範圍內；`PRAGMA busy_timeout=5000` 是這個殘餘重疊的安全網，不是完整的協調機制。目前沒有觀察到實際衝突（不同的存取模式：deep_fetch 多半是單一個股、單一時間點觸發），先記錄、不預先過度設計。
- `HOLE_CHECK_LOOKBACK_DAYS=10` 是個保守的固定常數（不是 config，因為它是演算法的內部細節，不是使用者該調的策略參數）；如果本機資料庫曾經有超過 10 天的未偵測缺口（Stage L1.1 之前就已經發生的舊資料問題），這次修正不會回頭修復它——只保證這次之後不會再發生。

## Output

本報告：`docs/history/stage-l1-1-daily-refresh-hardening-report.md`。`docs/history/stage-l1-daily-auto-refresh-report.md` 頂部加了指向本報告的說明，原文保留不回頭改寫。`docs/ARCHITECTURE.md` §8、`docs/PROJECT_STATUS.md`、`AGENTS.md`、`README.md` 已同步更新。沒有新增功能，沒有開始 Stage L2 intraday/live quote，本階段到此停止。
