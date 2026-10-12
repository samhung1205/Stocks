# Stage I2 — Financial Planner × Strategy Portfolio UI Integration

## Source-selection UX

`web/finance.html` 保留現有七步流程（current source 實際為七步），在 Step 5 加入 Investment Source：ETF 保持原有名單與代號加入方式；Manual 使用自訂名稱及明確的 planning return，顯示為「自訂／未分類」；Strategy Portfolio 依序選 Portfolio、固定 Version、選填已完成的 Research Run。選擇來源不會寫入 Planner target，必須按「確認來源與假設」，再按頁面頂端「儲存」。

## Evidence vs assumption UX

Research Evidence 使用獨立區塊顯示期間、CAGR、Sharpe、最大回撤、總報酬、波動、內部現金、turnover、cost drag 與原研究 warnings（API 有值才顯示），並標示「Historical / OOS research evidence · Not a forecast of future returns」。沒有 run 是正常空狀態。Planning Assumption 在其下方的另一區塊，支援 Manual、Scenario、Research-informed；Scenario 可由使用者主動採用 Planner 情境假設，Research-informed 可自行填數字或主動按「Use as reference」。選 Portfolio、Version 或 Run 都會讓尚未確認的輸入保持空白／清空，絕不直接把 CAGR 放進 `expected_return`。

Step 6 在 `use_portfolio_return=true` 且沒有任何 configured planning return 時明示「Currently using Planner Base Assumption: 7%. This is not the Strategy Portfolio historical return.」實際數值讀自當前 `return_base`。結果表的 Portfolio 歷史欄引導使用者回看 evidence，假設報酬未設定時顯示「未設定」。

## Relink/versioning

既有 target 顯示 pinned version、run、assumption source；有新版本時只提示「Newer version available」，不修改 pin。`Relink / Update Source` 重新要求版本、run、planning assumption，原 assumption 不帶入。若改連另一個 Portfolio，原 `actual_value` 也清空。取消 relink 保留舊設定。封存 Portfolio 仍可選取和閱讀 evidence，並有文字標籤。

## Cash/actual-value handling

Strategy Portfolio 的 `actual_value` 欄只供手動輸入或留空。研究末日 equity 從未帶入。Portfolio 內部現金只列在 evidence；介面提醒 Step 2 股票／ETF 資產若已包含該實際市值，應避免重複輸入。Equity / TW 在 Planner 僅為 reporting approximation。

## Legacy compatibility

未分類型的既有 ETF targets 繼續按原 ETF 名單讀取、試算和儲存；沒有 backfill 或 Planner schema/model 變更。Manual 不顯示 ETF metadata，也不套用台股核心分類。現有 Planner 七步與原 ETF 加入流程保留。

## Browser verification

在本機 FastAPI 與真實瀏覽器操作確認：

| Case | Result |
|---|---|
| A Legacy ETF profile | 既有 `Case A 25歲` 可開啟，ETF 配置與結果正常。 |
| B Manual source | 新增自訂名稱、4.5% 假設；儲存重載後保留，顯示「自訂／未分類」。 |
| C Portfolio without run | pinned v1、無 run，正常空狀態；return 與 actual value 均空白。 |
| D Completed evidence | archived `I1 test: source A` 的完成 run 顯示研究期間、績效、現金、成本與 warnings。 |
| E Research-informed | 選 run 後欄位仍空白；主動按 Use as reference 才帶入 CAGR，再確認與儲存。 |
| F Different manual assumption | 同一 run 的 CAGR 與手動 3% 並列，assumption source=`manual`。 |
| G Base fallback | 關閉 ETF、Portfolio 權重設 100% 且不設回報時，Step 6 明示 Planner Base Assumption 7%，並否認來自 Portfolio 歷史報酬。 |
| H Archived | evidence 可讀且標示「來源已封存」。 |
| I Newer version/relink | v1 保留並提示 v2；進入 relink 後提案的 run 與 return 皆空白，取消仍保留原值。 |
| J Actual value | 有完成 run 的 target 仍是空白，不從 research equity 帶入。 |
| K Save/reload | v1 pin、run ID、research-informed 與後續 manual assumption source 均可重載保持。 |
| L Mobile/keyboard | 390px viewport 的 `documentElement.scrollWidth <= clientWidth`；Tab 焦點有實線 outline，數值皆有文字呈現。 |

乾淨的 Planner 與 Strategy Portfolio 分頁在載入和上述正常操作後，console 均為 0 error / 0 warning。`node --check web/js/finance.js`、`git diff --check`、`compileall` 均通過；完整 unittest **384/384 通過**。瀏覽器暫時測試情境已刪除，刪除前以 SQLite backup API 備份真實 DB 至 `/private/tmp/stocks-stage-i2-precleanup.db`。

## Responsive/accessibility

沿用現有 design tokens、focus-visible 樣式與表格的局部橫向捲動。新增選單、按鈕與表單可用鍵盤操作；表單錯誤以文字及 `role="alert"` 呈現。Research Evidence、Planning Assumption、封存與較新版提醒都有明確文字，並非只靠色彩區分。

## Known limitations

- Planner 原有 Step 2 資產與 target `actual_value` 的重複輸入可能性仍由提示處理；未重構 accounting。
- Research Run 選單使用既有 API 最多讀取最近 200 筆；更舊的已存 pin 仍可顯示 evidence，但 relink 選單可能不列出極舊 run。
- Financial Plan 仍是可覆寫的目前設定，沒有 Planner version history；Stage I2 未加入新投資模型或 Portfolio research semantics。

## Final integration readiness

Stage I2 UI 已接上 I1 的來源、pin 與唯讀 evidence API。研究數值與未來假設的寫入操作明確分離；舊 ETF profile 可繼續使用。此階段完成，未開始全站 final polish。
