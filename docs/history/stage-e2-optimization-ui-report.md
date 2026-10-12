# Stage E2 — Optimization / Robustness UI

## User flow

- Strategy Lab 首頁的 `Optimization` 進入研究紀錄與 template baseline 入口；沒有新增 sidebar。
- Strategy Result 的 `Optimization` 會帶入該 run 的 strategy、version、template config、backtest config 與 benchmark，開啟 Create Research。
- Create Research 支援 objective、default/custom search space、max runs、budget policy、seed、research period 與 publication mode。Advanced search-space 預設收合，custom 時才展開 API 允許範圍與 JSON 編輯器。
- Job 狀態支援 queued/running progress、cancel 與 resume；完成後進入同一個 robustness research result。

## UI hierarchy

1. Research Context：strategy/version、research period、objective、evaluated points/grid fraction、publication mode、budget/seed 與 in-sample notice。
2. Robustness Summary：surface label、region size、objective spread、top-set dispersion、peak-neighbour drop 與 edge warning。
3. Data-quality signals：search edge、empty signals、unresolved corporate actions、high warning count、grid coverage anomaly。
4. Parameter Surface：ECharts heatmap 與 axis/aggregation controls。
5. Sensitivity：簡潔的 axis marginal range 與可展開 marginal means。
6. Results Table：所有 parameter points 與風險、成本、資料品質欄位，每欄可排序。

## Heatmap / robustness design

- Heatmap 每格直接顯示 objective value，tooltip 顯示 `n / min / max`；色階有數值 legend，不以顏色代替數字。
- X/Y 可從 scalar axes 或 weight keys 切換，aggregation 支援 median/mean/min/max。`aggregated_over` 總是顯示在圖表上方。
- Robustness 使用 plateau / isolated peak / scattered / flat / insufficient data 語彙，不顯示 Winner、Best 或 Recommended。Exploration rank #1 沒有特殊卡片或色彩。
- `edge_of_search_range` 存在時固定顯示「候選區域位於目前搜尋邊界，可能需要擴大搜尋範圍」，並明言 plateau 不等於已找到穩定最適參數。

## Edge / warning handling

- Edge、empty signals、unresolved actions、high warning count 與 coverage anomaly 均有文字、符號與邊框，不只靠紅綠色。
- QGM 的 36/36 points 會顯示 empty signals，effective first fill `2023-05-02`，empty signal count `5–5`，避免被誤認為與其他 template 擁有完全相同的有效投資期間。
- Results Table 每列顯示 unresolved count、warning count；有 empty signals 時再顯示 first fill。
- Coverage anomaly 納入 grid fraction、failed runs、panel notes 與 quote coverage。High warning 目前以 `>= 50` 則為 UI 顯著提示門檻。

## Browser verification

- 使用保留的五個 real-data full-grid jobs（非 cache rerun）驗證：momentum trend 36 rows、QGM 36、revenue growth acceleration 36、value quality 48、low volatility quality 36；全部正常顯示 plateau、edge flag、heatmap 與 results table。
- 重點實測 momentum trend、QGM、value quality；其餘兩套以相同 browser flow 確認 contract 相容。
- Value quality 實際切換 X axis 為 `value.weights.earnings_yield`、aggregation 為 `max`，`aggregated_over` 與 heatmap 同步更新。
- Results Table 實際將 Objective 改為 ascending，前兩列由 `0.4679` 至 `0.4924`，`aria-sort=ascending`。
- 另建立一個 2-point 小型 UI 驗證 job（未重跑五套 grid），確認 running progress、cancelled、Resume 以及回到 running/cancel 狀態。
- 所有驗證頁的 browser console：0 error / 0 warning。JavaScript syntax 與 `git diff --check` 通過；完整 Python suite `127/127` 通過。

## Responsive / accessibility

- 390×844 viewport 實測：內容區無水平溢出，context/robustness/quality/sensitivity 改為單欄，heatmap 高度 370px，寬表格保留獨立水平捲動區。
- 所有 form controls 有 label，table sort 使用原生 button 與 `aria-sort`，advanced/parameters/marginal means 使用原生 `details/summary`。
- ECharts 啟用 ARIA 描述；圖下有完整可排序表格作為數值 fallback。
- 實測 Tab 可進入導覽，focus outline 為可見 solid ring；所有主要互動元件均可鍵盤聚焦。

## Known limitations

- E1 Optimization job 契約未持久化 `strategy_id/version_no`；從 Strategy Result 進入時 UI 可精確顯示來源版本，但從歷史 job 直接開啟時只能誠實標示 template research baseline。
- Canvas heatmap 的鍵盤逐格導覽受 ECharts 限制；完整資料以鍵盤可排序 table 補足。
- High-warning `>= 50` 是可讀性門檻，不是後端統計顯著性或風險分級。
- 所有 robustness 仍是單一 in-sample window；本階段沒有 Walk Forward / OOS 或 Portfolio Lab。

## Stage F 前還需確認的事項

- 是否在後端正式持久化 Optimization job 的 strategy/version provenance，以便歷史研究可獲得精確版本身分。
- Walk Forward / OOS 啟動前，需定義 in-sample candidate region 如何凍結、傳遞與防止二次選擇偏誤。
- 確認 warning severity 是否需後端統一契約，取代 UI 的數量門檻。
- 確認 edge 延伸研究是建立新 job，還是未來支援可追蹤的 search-space revision。
