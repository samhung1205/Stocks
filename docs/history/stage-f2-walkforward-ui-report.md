# Stage F2 — Walk Forward / OOS UI

## User flow

- Strategy Lab 首頁可進入 Walk Forward 研究紀錄；Strategy Result 與完成的 Optimization Research 都提供 `Walk Forward / OOS` 入口。
- Create Research 顯示 expanding / rolling、initial training length、test length、step、final partial-fold policy、execution policy 與 candidate freeze rule。預設使用 F1 protocol；Advanced settings 預設收合。
- 從 Optimization 進入時沿用該研究的 disclosed search space；從 Strategy Result 進入時帶入該版本定義。execution 固定為 `partial`。每次建立都是新的 disclosed research trial。
- 本階段沒有重跑五套正式 Walk Forward，也沒有建立額外 lifecycle job。

## UI hierarchy

結果固定依序呈現：Research Protocol → OOS Summary → Fold Timeline → IS → OOS → Parameter Stability → OOS Equity Evidence（Independent primary / Stitched secondary tabs）→ Data & Warnings。

OOS Summary 只放 evaluated folds、no-candidate folds、independent OOS Sharpe / CAGR / MaxDD 的 evaluated-fold median，以及 median Δ Sharpe；不混入 stitched 指標。全頁不使用 Winner、Best、Recommended 或交通燈式策略評分。

## Fold timeline

- 每 fold 以 `Train → Freeze → Test` 顯示 train/test dates、freeze 結果、first fill 與 cash-start 語意。
- 文字 badges 同時標示 evaluated、no candidate、truncated、plateau label、primary-on-edge 與 OOS severity，不只靠顏色。
- `NO_STABLE_CANDIDATE` 顯示為有效研究結果：「本 fold 無穩健候選，OOS 持現金」，並揭露 test data 未載入、沒有 IS-peak fallback。

## IS/OOS design

- 每個 evaluated fold 使用 compact paired comparison，呈現 Sharpe、CAGR、MaxDD、turnover、cost drag 的 IS → OOS 與 `Δ = OOS − IS`。
- 下方保留完整數值表格 fallback；明示 folds ≤ 4、descriptive only，沒有 significance / confidence claim。

## Parameter stability design

- Fold table 顯示 frozen medoid parameters、primary changed / unchanged、candidate digest changed、consecutive evaluated-fold region Jaccard、plateau label、primary edge axes 與 IS-peak edge。
- No-candidate fold 明確顯示沒有 frozen primary；IS peak edge 與 primary edge 分開。
- 12/12 real-data evaluated folds 的 primary-on-edge 會觸發固定提示：「目前 default search space 可能受邊界限制。」結果頁沒有直接擴大並重跑；擴張只能建立新的 disclosed research trial。

## Independent vs stitched presentation

- Independent OOS 是預設與 primary tab。圖表將每個 evaluated fold 各自標準化為 100，橫軸為 fold 內交易日，並固定說明每 fold 從現金開始、各自支付進場成本，不能解讀成連續真實投資曲線。
- Stitched OOS 放在 secondary tab，固定顯示 `Secondary evidence`、`Not independent-fold evidence`、`prior OOS terminal state only`、`no training replay`。
- Stitched 指標與 chart 完全留在 secondary panel；segment table 揭露 state source、inherited positions、fills 與 segment return。
- No-candidate 接在既有部位後時顯示 boundary liquidation event；LVQ real job 正確顯示 30 inherited positions / 30 fills 後轉現金。

## Warning handling

- Training data quality 與 OOS fold quality 分欄；training critical 固定解釋為目前多由 unresolved corporate actions 造成的資料品質限制，不呈現為策略失效。
- 支援 unresolved actions、partial rebalance、assumed publication、primary on edge、no stable candidate、partial final fold、empty signals。
- QGM 顯示 training early cash / empty signals、first usable fill `2023-05-02`，並明示 `OOS leakage: no`。

## Browser verification

- 直接使用 F1.5 保留 jobs，沒有重跑：
  - `momentum_trend`：4/4 evaluated、四個 first fill、final truncated、4/4 primary-on-edge、independent / stitched 分離。
  - `quality_growth_momentum`：1/4 evaluated、3/4 no-candidate、training empty signals、first usable fill 2023-05-02。
  - `value_quality`：4/4 evaluated、final truncated、4/4 primary-on-edge、parameter / digest / Jaccard 顯示正確。
  - `revenue_growth_acceleration`：2/4 evaluated、2/4 no-candidate，contract 相容。
  - `low_volatility_quality`：1/4 evaluated、3/4 no-candidate；stitched Fold 2 liquidation 30 positions / 30 fills 正確。
- Strategy Result 與 Optimization Research 入口、Create Research 預設 protocol、Advanced 收合皆以 browser 驗證；未送出新 job。
- 所有驗證頁 browser console：0 error / 0 warning。JavaScript syntax、`git diff --check`、完整 Python suite `206/206` 通過。

## Responsive / accessibility

- 390×844 實測 `documentElement.scrollWidth = 390`，整頁無水平溢出；寬表格保留局部 horizontal scroll。
- Summary / protocol / stitched metrics 在窄螢幕改為單欄，timeline 改成垂直 Train → Freeze → Test；圖表高度縮減。
- Evidence tabs 使用原生 button、完整 tab / tabpanel ARIA、roving tabindex 與左右鍵切換；focus ring 可見。
- ECharts 有數值語意 ARIA description；IS/OOS、Independent equity、Stitched segments 都有 table fallback。數值使用 tabular numerals，狀態都有文字與邊框，不只靠紅綠。

## Known limitations

- ECharts canvas 本身沒有逐點鍵盤導覽；精確值由可閱讀、可局部捲動的表格補足。
- OOS Summary 使用 evaluated-fold median；no-candidate cash folds 另列數量，不將空 Sharpe 混入 median。
- Create Research 可選 rolling，但 F1.5 的五套正式驗證只涵蓋 default expanding protocol。
- Warning 區聚焦研究決策需要的 structural / data-quality signals；完整 raw engine warnings 仍留在後端 evidence，不在 F2 頁面逐則展開。

## Stage G 前還缺什麼

- 需要先處理或正式接受 unresolved corporate actions 的上游資料限制；否則 training critical 仍缺乏區辨力。
- 若要調整 default search spaces，必須在新的 research/version 中事前定義並重新做 disclosed validation，不能由 F2 結果頁直接延伸。
- 需決定 QGM early-cash coverage 是否先補足/根因化，再進入跨策略研究。
- Portfolio Lab / cross-strategy allocation、資金共享、組合層級風險與 execution 都尚未開始；F2 沒有推導任何配置或推薦。
