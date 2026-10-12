# Stage D2 — Strategy Lab UI / UX

## 頁面與 user flow

- Top Nav 新增「策略實驗室」，沿用既有導覽，沒有新增 sidebar。
- `strategy.html` 依 query string 呈現三個狀態：Home、Builder、Result。
- Home 包含 My Strategies、New Strategy、五個 Templates、最近回測摘要，以及 loading / empty / error state；不做策略排名或 Winner。
- Builder 沿用既有 steps 語彙：Template → Universe → Factors / Parameters → Portfolio Rules → Rebalance → Cost → Data / Publication Mode → Review & Run。先建立 Strategy，再設定並於 Run 時儲存新版本；次要設定收進 `details`。
- Result 固定為 Header、六個 KPI、Equity Curve、Drawdown、Performance / Parameters / Trades / Data & Warnings 四個 tabs。Run 的 queued / running / failed 狀態都有獨立畫面與自動輪詢。

## UI 架構

- 保留原生 HTML / CSS / JavaScript 與 query-string routing，不引入前端 framework 或 build step。
- 使用既有 CSS tokens、card、table、tabs、steps、alert、details；新增規則只在 `app.css` 的 Strategy Lab 區塊。
- `charts.js` 提供共用 Equity / Drawdown builder；Equity 同時顯示 Strategy Net、Strategy Gross、Benchmark，支援 Linear / Log，Drawdown 固定顯示 Strategy vs Benchmark。
- Performance table 明確區分 Gross / Net / cost drag；Payoff Ratio = 平均獲利 ÷ 平均虧損絕對值，Profit Factor = 總獲利 ÷ 總虧損絕對值。
- Warnings 以嚴重度圓點、摘要列與 disclosure 分層；publication mode、data coverage、survivorship、assumed publication、missing data 都保留文字說明，不用滿頁警告框。

## 新增/修改檔案

| 檔案 | 變更 |
|---|---|
| `web/strategy.html` | 新增 Strategy Lab 頁面入口與本機 ECharts 載入 |
| `web/js/strategy.js` | 新增 Home / Builder / Run lifecycle / Result / tabs / tables UI |
| `web/js/charts.js` | 新增共用 Equity 與 Drawdown ECharts builders |
| `web/js/common.js` | Top Nav 新增策略實驗室；chart theme 改讀 CSS tokens |
| `web/css/app.css` | 新增最小 tokens、數字 tabular-nums、Strategy Lab desktop / responsive / accessibility 樣式 |
| `docs/history/stage-d2-strategy-ui-report.md` | 本報告 |
| `output/playwright/stage-d2/` | Browser verification 的 screenshots / accessibility snapshots（非產品 runtime） |

沒有修改 Backtest Core、Optimization、Walk Forward、OOS 或 Portfolio Lab。

## browser verification

- `web/vendor/echarts.min.js` 是本機實體檔，1,030,855 bytes；瀏覽器實測 `GET /vendor/echarts.min.js` 回傳 200，沒有改用 CDN。
- 以 Playwright 實際走過 Template → Create → Configure → Run → Loading → Result → Equity → Drawdown → Trades / Data & Warnings。
- 建立專用策略 `D2 Browser Verify`，以 5 檔真實資料、2026-08-03～2026-09-18、weekly rebalance、短 lookback 執行；另用 force queued run 實測 Loading 頁，執行後自動輪詢切換 Result。此短跑只驗證 UI / contract，不作績效結論。
- 既有 completed run `D1 smoke` 亦驗證 485 個交易日 Equity / Drawdown、16 筆 closed trades、Gross / Net、Payoff Ratio / Profit Factor 與 coverage。
- Linear / Log 切換、Trades / Fills、Parameters、warnings disclosure 與 keyboard tab ArrowLeft / ArrowRight 均實測；最終 browser console 0 errors / 0 warnings。
- 前端 `node --check`、`git diff --check` 通過；完整離線測試 `58 / 58` 通過。

## responsive / accessibility

- Desktop-first；1180px 調整 template/KPI grid，760px 以下改單欄與可水平捲動 steps。375 × 812 實測 Result 與 Builder 的 document scroll width 等於 client width，沒有整頁水平溢出。
- 表格數字右對齊並使用 tabular numerals；正負數同時保留 `+ / −` 符號，不只依賴顏色。
- 表單都有對應 label；tabs 使用 `tablist / tab / tabpanel`、`aria-selected` 與 roving tabindex；圖表開啟 ECharts aria description。
- 焦點有可見 outline；Loading 使用 `aria-busy` / live region；reduced-motion 下 spinner 降速、progress animation 停止，已在瀏覽器實測。

## known limitations

- 五年全市場 backfill 與部分 valuations / fundamentals 尚未完整；UI 以每次 run 的 coverage 為準，不宣稱正式多年績效。
- 沒有完整歷史指數成分與 point-in-time sector taxonomy，survivorship / sector fallback 仍需警示。
- `observed_finmind` 不是發行公司正式公告日；`assumed_deadline` 仍是假設，且資料不是 revision-vintage database。
- 回測仍在 web process threadpool 執行，沒有取消 API；服務重啟會把 queued / running 標為 failed。
- ECharts Canvas 有文字化 aria description 與表格補充，但尚未提供逐日 Equity / Drawdown 的完整可排序 fallback table。

## Stage E 前還缺什麼

- 完成並稽核五年 backfill coverage，確定可用研究區間與 universe 規模。
- 在進入 Optimization 前定義搜尋空間、run budget、cache / progress contract 與 plateau 判定；不可把最佳單點包裝成 Winner。
- 建立 Optimization 結果 API 與持久化後，才新增 heatmap / robustness UI；D2 tabs 不預留假資料。
- 若 Stage E 要以基本面策略做穩健性分析，需先補強 strict publication、valuation history 與 point-in-time sector coverage。
