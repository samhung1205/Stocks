# Stage A+ 資料基礎交付報告

日期：2026-09-19。範圍限資料基礎；未實作 Backtest Engine、Strategy UI 或 Stage B。

## 1. 實作內容

- 新增 SQLite migration ledger `schema_migrations`。使用 `BEGIN IMMEDIATE`、欄位存在檢查、additive DDL 與同交易提交；啟動及 CLI 均可重跑，失敗會連同 DDL 回滾。未導入 Alembic、Celery、Redis 或新增專案依賴。
- `month_revenue`、`financials` 增加 `publish_date / publish_source`；既有列補為 `assumed_deadline`。所有既有匯入路徑經共用 writer 補日期，因此 MOPS、deep fetch 及新回補均涵蓋。修正 sparse upsert，避免同批另一列缺欄位洗掉既有值；較低可信來源不覆寫較高可信來源整列。
- `stocks` 增加 `listed_date / delisted_date`。上市／上櫃日期取 TWSE／TPEx 公司基本資料，下市資料取 FinMind；不把 `TaiwanStockInfoWithWarrant.date` 更新日當上市日。
- 新增 `corporate_actions`（含來源、原始 JSON、參考價比及可辨識的股利欄位）、`benchmark_series`、`trading_calendar`、`backfill_jobs`、`backfill_tasks`。
- 新增行情、法人、估值的 date index；營收 ym／publish_date、財報 quarter／publish_date、公司行動 date、benchmark date 及工作進度索引。
- 接入 FinMind `TaiwanStockPER`、`TaiwanStockDividendResult`、`TaiwanStockSplitPrice`、`TaiwanStockTotalReturnIndex`、`TaiwanStockDelisting`、`TaiwanStockInfoWithWarrant`、`TaiwanStockTradingDate`；沿用營收及三大財報 datasets。Token 改用 Authorization header，避免出現在 URL。
- 建立 `TAIEX_TR` 官方報酬指數序列，不以加權價格指數代替、不自行拼裝報酬。
- 可回測 Universe 以判斷日之前一年的有效日成交金額平均篩選，預設 NT$30M、至少 120 個觀察日；金額與觀察日均可設定，沒有固定 800 檔。逐檔回補使用歷史每月 Universe 聯集；明確指定股票只供小範圍驗證。新增 `GET /api/admin/data-universe`。
- 全市場行情按「市場 × 日期」分開保存 checkpoint，不再依賴 market_summary 或今日在市名單判斷已完成。保留歷史股票，排除權證；交易日收到空／明顯殘缺回應則失敗，不能當成休市。TAIEX TR 依交易日檢查缺日；估值缺日標為 partial。
- 網路請求在寫入交易外；每單位資料與 checkpoint 原子提交。檔案鎖限制同 DB 一個本機 runner；Ctrl-C、程序中断、限流及失敗均可用同工作 ID 續跑。支援單位上限、暫停、錯誤、空資料與資料集進度。API 單次最多 100 單位，CLI 可明確指定跑至完成。
- 補抓逐檔估值、公司行動、營收及財報；現金流缺前季累計時保留未知，不誤當單季值。深度資料清理不刪除已由正式工作補入的財報。
- Dashboard 資料維護加入建立／續跑／暫停／進度入口。`.steps / .step / .alert / .kv / .statgrid / .pill / details.more` 與所有原 `.fin-*` selector 共用規則，保留原有 specificity；未修改 `finance.html` 或 `finance.js`。

公布日契約：

- `actual` 僅供有正式公告證據的資料；本次未把任何推估或 FinMind 入庫日冒稱 Actual。
- `observed_finmind` 為月營收 `create_time`。該欄自 2026-04-21 開始記錄，代表進入 FinMind 的時間，並非正式公告日期；初始日的紀錄也只作觀察日使用。[FinMind 官方說明](https://finmind.github.io/tutor/TaiwanMarket/Fundamental/#taiwanstockmonthrevenue)
- `assumed_deadline` 為名目申報期限。Q1 修正為**同年** 5/15、Q2 8/14、Q3 11/14、Q4 次年 3/31；金控、假日及展延不是通用期限能涵蓋，提供季別規則及公司／期間覆寫。官方 2026 年公告明載一般公司 Q1 為 5/15、金控順延至 6/1。[證交所公告](https://www.twse.com.tw/staticFiles/news/news/tsecnews/8a8216d69dbea9fd019e16a235e70191.pdf)
- `available_fundamentals()` 預設只讀 Actual／Observed，且要求 `publish_date < as_of`；只有日期而無時刻的資料，公布當日不可使用。Assumed 必須明確 opt-in，不能宣稱嚴格無前視偏誤。

已建立但**未啟動**五年正式工作：

- ID：`e743c5a372cc487287fa4054290640fd`
- 區間：`2021-09-18 ～ 2026-09-18`；NT$30M；120 個觀察日。
- 狀態：pending，3,666 個初始單位；Universe 確定後再加入逐檔單位。
- 可續跑正式指令（在 repo 根目錄）：

```bash
uv run python -m app.backfill run e743c5a372cc487287fa4054290640fd --max-units 100
uv run python -m app.backfill status e743c5a372cc487287fa4054290640fd
# 僅在終端機與網路適合長時間運作時：
caffeinate -i uv run python -m app.backfill run e743c5a372cc487287fa4054290640fd --max-units 0
```

完整 CLI、API、設定與續跑說明已加入 `README.md`。本次不維持數小時全量背景程序。

## 2. 測試結果

| 驗證 | 結果 |
|---|---|
| `python -m unittest discover -s tests -p 'test_*.py' -v` | **20 / 20 通過** |
| `python tests/smoke.py all` | **sources、light、deep、finance 全部通過**，含實際外部資料源 |
| `node --check web/js/data-maintenance.js`、Python compileall | 通過 |
| 舊 schema／新 DB migration 重跑 | 通過；ledger version 1 僅一列，舊資料保留 |
| migration 故障回滾 | 通過；故意無效期間造成失敗後，DDL 與 ledger 均未殘留 |
| 正式 DB migration 連續重跑 | 通過；`PRAGMA quick_check = ok` |
| 公布日前、當天、未來值、Assumed 過濾 | 營收與財報均通過；日期正規化與跨年 Q4 亦通過 |
| 中断、quota 失敗、寫入失敗、鎖、暫停競態、續跑 | 通過；已提交單位不重抓，失敗單位可重試，排隊後的暫停不遺失 |
| Universe 時間窗、門檻、上市／下市過濾 | 通過；未來成交金額不影響當時選取 |
| 權證排除與不在現況主檔的歷史股票保留 | 通過 |
| Financial Planner 使用資料與備份逐列比較 | 2 份 profiles、26 列 targets 完全一致；watchlist／scores／trade_plans／trades 亦一致 |
| 瀏覽器維護控制項 | 可展開、顯示日期／門檻／工作進度；已完成工作續跑按鈕成功且沒有重抓 |
| Financial Planner 瀏覽器驗證 | 原情境與七個步驟可載入，simulate API 200；**圖表完整驗證受既有 ECharts 檔案 offload 阻擋**，見限制 |

正式 DB 的等值及範圍 `EXPLAIN QUERY PLAN` 均確認使用索引，範圍查詢例：

```text
daily_quotes.date       SEARCH ... USING INDEX idx_quotes_date (date>? AND date<?)
institutional_daily.date SEARCH ... USING INDEX idx_insti_date (date>? AND date<?)
valuations.date         SEARCH ... USING INDEX idx_val_date (date>? AND date<?)
month_revenue.ym        SEARCH ... USING INDEX idx_revenue_ym (ym>? AND ym<?)
month_revenue.publish_date SEARCH ... USING INDEX idx_revenue_publish (...)
financials.publish_date SEARCH ... USING INDEX idx_financial_publish (...)
corporate_actions.date  SEARCH ... USING INDEX idx_actions_date (...)
benchmark_series.date   SEARCH ... USING INDEX idx_benchmark_date (...)
```

正式 DB 已先以 SQLite backup API 備份至 `data/stocks.pre-stage-a-plus.db`，另有日期命名備份。

## 3. 資料覆蓋

小範圍工作 `db67aff598db4580919631be41c5302b`：`2026-09-16 ～ 2026-09-18`，逐檔標的 2330／5347，23 個工作單位完成，無 failed／empty／partial；再續跑不重抓。

- 雙市場行情：6 個市場／日期單位，寫入 6,632 筆普通股／ETF 行情。
- 估值：6 筆；TAIEX TR：3 筆；現金除息：1 筆；分割 dataset 此小區間無事件。
- 財報：4 季別列；營收：24 列（含計算成長率所需的前一年資料）。
- TAIEX TR 三日值：106011.29、107026.66、109090.87。

實測與既有 smoke tests 完成後，正式 DB 覆蓋如下；這些總量不等於五年全市場完整：

| 資料 | 列數 | 期間數 | 範圍 |
|---|---:|---:|---|
| daily_quotes | 337,149 | 1,228 日 | 2021-08-02 ～ 2026-09-18 |
| 每日 >1,000 檔的行情日期（粗略覆蓋指標） | — | **123 日** | 2026-02-03 ～ 2026-09-18 |
| valuations | 9,849 | 7 日 | 2026-07-31 ～ 2026-09-18 |
| benchmark_series | 3 | 3 日 | 2026-09-16 ～ 2026-09-18 |
| corporate_actions | 1 | 1 日 | 2026-09-16 |
| month_revenue | 23,937 | 73 月 | 2020-08 ～ 2026-08 |
| financials | 556 | 20 季 | 2021Q3 ～ 2026Q2 |

主檔 3,202 列；已知 listed_date 1,986 列、delisted_date 725 列。以 2026-09-19、NT$30M、120 觀察日查詢，Universe 為 **957 檔**，來自資料計算而非固定數量。

營收：Observed 13 列、Assumed 23,924 列；財報 556 列均為 Assumed。沒有把缺失的 Actual 覆蓋偽裝成已完成。

## 4. 未解限制

- 五年全量尚未下載；正式工作只有計畫，並未在本次對話背景偷偷執行。官方／FinMind 限流、網路及逐檔財報請求可能使總時間超過 audit 的 3.5 小時。預設 FinMind 間隔 6.2 秒，其他程序仍共用帳號額度。
- 嚴格模式目前無可用的 Actual 財報日期；Assumed 不能證明公司未延遲申報。若要使用五年基本面策略，必須接受清楚標示的假設模式，或補上可信公告證據。
- 不是歷史修訂版本庫；即使已按公布日過濾，也不能保證還原所有財報／營收當時尚未修訂的數值。
- 上市日未知保留 NULL，不能宣稱完整 point-in-time 證券主檔；轉板、重新上市、代碼重用及歷史指數成分仍未完整建模。
- 完整工作採每月 Universe 聯集，可能漏掉月中短暫符合門檻的股票。120 觀察日不等於完整一年；要在五年起點就有完整一年流動性暖機，需額外向前補一年行情。
- 公司行動混合除權息未提供可可靠拆分數值時保留 NULL；沒有免費、完整的減資／現金增資／下市清算資料。`ratio` 是參考價格比，不能當股數倍率，也不能與股利重複計入 Total Return。未實作任何還原引擎。
- 空的公司行動回應表示此查詢無事件，不證明來源涵蓋全部事件種類；估值 partial、基本面 empty 會標記完成但有缺口。
- 日期／市場行情使用筆數與日期檢查防止空回應、明显殘缺；尚無官方歷史逐日證券名冊可證明每一檔都齊全。
- 本機部分 `.venv`、Git pack、`web/vendor/echarts.min.js` 是 iCloud dataless 檔案。Python 套件還原後測試已通過，但既有 ECharts 檔讀取仍回傳短內容，造成 `echarts is not defined`；因此不能宣稱圖表端到端回歸全部通過。未變更 ECharts 版本或原圖表程式。Git diff 也受同一環境問題阻擋；未提交或修改 Git objects。
- Runner 為本機 SQLite／檔案鎖設計，不支援多主機共享任務或 Windows `fcntl`。

## 5. 進入 Stage B 前是否 ready

**工程資料基礎：有條件 ready。** Schema、migration、索引、來源接入、嚴格公布日過濾、可續跑工作、CLI／API／UI 入口已完成並通過上述測試；可作為 Stage B 開發的資料契約。

**五年正式回測資料：尚未 ready。** 必須先完成五年工作並检查缺日／缺檔；涉及財報的策略還需解決 Actual 公布日與版本限制。ECharts 資產需恢復本機可讀後，才能完成現有圖表的視覺回歸。

本階段到此停止，未開始 Stage B。

## Stage A+ hotfix：TAIEX Total Return completeness（2026-09-19）

正式工作 `e743c5a372cc487287fa4054290640fd` 的
`benchmark::2026-01-01:2026-09-18` 原先因完整性檢查失敗而停止。原始交易日曆預期
174 日，FinMind `TaiwanStockTotalReturnIndex` 實際取得 173 日；missing 僅
`2026-07-10`，extra 為空。整段請求、月分段請求及兩次缺日重抓結果一致。TWSE 與
TPEx 官方逐日行情在該日均為 0 筆；該日因巴威颱風全市場休市，根因是 FinMind
`TaiwanStockTradingDate` 未納入臨時休市，而非 Total Return 大區間截斷、日期邊界或
Total Return 資料缺漏。

修正後 benchmark 一律按月抓取、以日期去重，缺日會做兩次窄區間重試，再以 TWSE／
TPEx 官方逐日資料確認市場是否開市。確認開市但缺 Total Return 時，task 會標成
`partial` 並保留 missing dates；官方回應不完整時仍會失敗；不會以 TAIEX price index
補值。確認為臨時休市的錯誤日曆列，會與 benchmark 資料及 checkpoint 在同一 SQLite
transaction 內修正，並將完整診斷留在 task status/API/UI。

Regression 與既有測試合計 **25 / 25 通過**。原 job 已用 `--max-units 1` 原地續跑；
該 task 現為 `done`、寫入 173 筆，最終 expected 173／actual 173、missing／extra 均為空。
先前完成的 10 個 checkpoint 未重抓；job 目前為 `paused`，剩餘 3,655 個 pending 單位，
可直接用相同 job ID 安全續跑。
