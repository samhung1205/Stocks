# Final Product Design Stage K1A — Design System Foundation + Representative Flow Redesign

日期：2026-09-23。設計方向沿用 [K0：研究卷宗 / Research Desk](final-design-direction.md)。本階段只修改原生 HTML/CSS/JS、圖表樣式及 favicon；未修改 API、DB/schema、研究/配置邏輯或 Financial Planner 計算語意。原工作樹已有未提交變更，本階段沒有重置它們。

## 1. K1A 完成範圍

| 優先 | 範圍 | 已完成 |
|---|---|---|
| **Must change** | 共用設計基礎 | `web/css/app.css` 建立 canvas、paper、inset、文字、rule、action/focus、證據、狀態及台股漲跌語意 token；舊 token 以 alias 過渡。正文提高到 16px，採較平的紙面、細規則、低半徑與 tabular numbers。主 CTA 白字／深海松色約 **7.08:1**；正文、次要文字及 caution/critical 皆超過 4.5:1。 |
| **Must change** | 共用元件 | 按鈕、欄位、頁籤、表格、證據通知、empty/loading/error、可見焦點及 reduced motion 共用樣式；粗左色條改為細框與標題語意，研究績效不再以股價紅綠評好壞。Screener 可排序表頭改成 button 並更新 `aria-sort`。代表頁與 Planner 可見控制項補足 label/for 或可存取名稱。 |
| **Must change** | 全站導覽 | `web/js/common.js` 分為「市場研究／策略研究／個人決策」，常駐顯示群組與目前頁面；手機以原生 details 展開群組，不新增 sidebar。Stock 顯示為目前個股頁；沒有新增 Cross-Strategy UI。搜尋結果可用連結與鍵盤操作。 |
| **Should change** | 圖表語言 | ECharts 沿用現有實作；共用研究色票改為低彩度，Optimization heatmap 在淺底可讀。個股月營收提供「聚焦常態區間／完整極值」尺度切換，畫面明示超出聚焦畫幅的極值數，並提供全部月份原始數值表；資料沒有刪除或重新計算。 |
| **Keep** | 前端架構及披露 | 原生 HTML/CSS/JS + vendored ECharts；保留所有研究警告、版本、來源、cash、近似報告與非預測語意。 |

共用結果頁首由 `researchMetadata()` 與 `.research-header` 組成，固定顯示研究身分、版本、證據型別、期間/as-of、來源、目前階段及下一步。`web/favicon.svg` 補上簡單卷宗符號，避免瀏覽器初次開站的 favicon 404。

## 2. 代表流程 A：Dashboard → Stock Detail → Journal

- **Dashboard**：資料截至日進入頁首；大盤數值改為一條市場摘要線，指數優先，其餘數值是次級脈絡。走勢與觀察名單維持研究主區；資料維護與回補放進可展開區。資料過期提醒保留，但按鈕退為次要操作。
- **2330 Stock Detail**：價格／收盤日先於圖表；圖表後依序是系統證據、人工判斷與評分、風險與治理查核、下一步寫交易計畫。窄右欄改為有閱讀寬度的研究區。月營收聚焦尺度對 2330 明示 **9 個極值超出 ±56% 畫幅**，完整尺度與全部原始數值隨時可切換／展開。
- **Journal**：交易計畫為第一工作區；成交與持倉次之，已實現績效放在紀錄後。真實資料目前 `closed_trades=0`，因此不顯示四張空績效卡，改顯示「撰寫交易計畫」的下一步。另用瀏覽器暫時模擬三筆已平倉績效回應，驗證非空版面；沒有寫入資料庫。

## 3. 代表流程 B：Strategy Lab → Optimization → Walk Forward

- **Strategy Lab**：列表入口標示「策略研究／研究起點」；回測結果在績效線之前顯示 Historical/Backtest 身分、版本、期間、Run 與 publication mode，並明示「歷史回測 ≠ 未來預測」。後續可進入 Optimization 與 OOS。
- **Optimization**：結果頁首先標成 IS exploratory、非 winner；Research Context、Robustness、邊界警告與資料品質先於 heatmap／完整點表。所有參數點仍可排序，不改搜尋或 plateau 契約。
- **Walk Forward**：頁首先標明 Independent-fold OOS 為 primary evidence；protocol、OOS 摘要與 fold timeline 接在圖表前。`no_candidate` 仍是有效持現金結果；stitched 留在獨立的 secondary 頁籤，並保留非獨立證據聲明。

## 4. Portfolio / Planner 樣本處理

- **Portfolio Result**：研究結果頁首補固定版本、Run、研究期間及 holdings as of last rebalance 日期；績效改為單行 metric line，披露 Historical/OOS ≠ forecast。Holdings 快照區的版本、日期與「非即時／非逐日」說明仍緊貼表格。`no_candidate` 現金、成本、限制與 reporting-only 說明保持原語意。
- **Planner**：Research Evidence 用白色正文紙面與研究標記；Planning Assumption 用 inset 與 caution 標記，兩區並列在相同來源流程但不混為一個數字。用真實固定 Portfolio v1／已完成 Run 檢查：Evidence 顯示 CAGR 等歷史數值；Planning return 未因選取 Run 自動填入。Portfolio 的實際市值仍須手動輸入或留空。

## 5. 代表截圖

| 畫面 | 截圖與觀察 |
|---|---|
| Dashboard | 1280px：`../assets/k1a/dashboard-1280.png`（本機歷史截圖，不納入 GitHub）／375px：`../assets/k1a/dashboard-375.png`（本機歷史截圖，不納入 GitHub）：as-of、過期提示、指數、走勢依序閱讀；維護區退到下方。 |
| 2330 Stock | 1280px 月營收：`../assets/k1a/stock-1280.png`（本機歷史截圖，不納入 GitHub）：可見尺度切換與誠實的極值註記；完整數值表在圖下。 |
| Journal | 真實空狀態：`../assets/k1a/journal-1280.png`（本機歷史截圖，不納入 GitHub）／模擬非空狀態：`../assets/k1a/journal-nonempty-simulated-1280.png`（本機歷史截圖，不納入 GitHub）：無平倉樣本不出現四張空績效卡。 |
| Optimization / Walk Forward | Optimization：`../assets/k1a/optimization-1280.png`（本機歷史截圖，不納入 GitHub）／Walk Forward 1280px：`../assets/k1a/walkforward-1280.png`（本機歷史截圖，不納入 GitHub）／Walk Forward 375px：`../assets/k1a/walkforward-375.png`（本機歷史截圖，不納入 GitHub）：研究身分與證據等級先於指標。 |
| Portfolio / Planner | Portfolio Result：`../assets/k1a/portfolio-1280.png`（本機歷史截圖，不納入 GitHub）／Planner Evidence：`../assets/k1a/planner-evidence-1280.png`（本機歷史截圖，不納入 GitHub）／Planner Assumption：`../assets/k1a/planner-assumption-1280.png`（本機歷史截圖，不納入 GitHub）：固定版本與歷史／假設區隔。 |

## 6. 驗證

- **真實資料 Chromium walkthrough**：Dashboard、2330 Stock（月營收）、Journal 空狀態、Strategy Lab、已完成回測、Optimization、Walk Forward（含 `no_candidate`）、Portfolio research result／holdings、Planner 固定 Portfolio v1 + 已完成 Run。桌面導覽實際展開「策略研究」並進入 Optimization；手機同一路徑可操作。
- **Viewport**：1280、768、375px；上述代表頁各寬度 `documentElement.scrollWidth <= clientWidth`。表格只在自己的 `.scroll-x` 容器內橫向捲動。375px 的 200% **文字放大**檢查可重排且沒有整頁溢出；手機三群導覽會換行。鍵盤 Tab 可到品牌／導覽／控制項，focus ring 可見；`prefers-reduced-motion: reduce` 下停止裝飾性動畫。
- **Console**：最後一輪各代表頁、三種 viewport **0 error / 0 warning**；初次開站的 favicon 404 已修正。Planner 真實 Evidence/Assumption 操作及導覽切換也為 0 error / 0 warning。
- **對比**：paper 上正文 13.37:1、次要字 6.44:1、caution 6.35:1、critical 7.02:1；白字主 CTA 7.08:1。台股紅漲／綠跌分別 6.47:1／5.97:1。
- **靜態與既有測試**：所有 `web/js/*.js` 與所有 HTML inline scripts 通過 `node --check`；`git diff --check` 通過；`compileall -q app tests scripts` 通過；完整 unittest **389 tests，OK**。測試輸出仍有既有 Starlette deprecation / SQLite ResourceWarning，與瀏覽器 console 無關。

## 7. K1B 尚待處理及目前不一致

- **Should change**：Screener／Watchlist 的整頁資訊層級、空狀態與表格密度尚未全面整理；這次只修共用樣式、排序語意與 favicon。
- **Should change**：Strategy Builder、Optimization/WF 建立表單、Portfolio 六步建立流程及 Planner 七步整頁仍保留部分舊的英文標題、step pills、等權卡片與局部 legacy CSS。K1B 應按同一研究卷宗規則逐頁收斂，不能改變資料/研究契約。
- **Should change**：Planner 真實 Evidence 的 warning 明細仍會以很長的原始診斷字串佔據左欄；K1B 可增加摘要與可展開逐筆細節，但重大限制必須保持可見。Portfolio 封存清單與 Planner 長 Portfolio picker 仍需搜尋與範圍提示。
- **Should change**：`app.css` 目前同時保有舊 class 定義與 K1A 過渡覆寫；K1B rollout 後再移除已無引用的舊選擇器，避免提前波及未重做頁面。部分非代表圖表仍有舊色值，需逐圖檢查單位、軸、fallback 與對比。

## 8. K1B readiness

**Ready**：三群 IA、語意 token、研究頁首、證據通知、表格／欄位／焦點基礎已可供 K1B 使用。K1B 應以 Screener、Watchlist、Planner 全流程及 Portfolio Builder／長清單為 rollout 主體；逐頁檢查真實資料、狀態、鍵盤與窄畫面。任何 `Historical/OOS ≠ forecast`、independent/stitched、`no_candidate` 現金、Planner Evidence/Assumption、as-of-last-rebalance、reporting approximation 語意都必須保持。
