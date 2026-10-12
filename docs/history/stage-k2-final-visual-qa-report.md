# Stage K2 Final Visual QA Report

## Final visual consistency verdict

**全站已形成一致的 Academic Research Desk C+ 產品語言。** 這次檢查涵蓋 18 個主要 route / state，在 1280px、768px、375px 共 54 次版面檢視，另實際走過 Backtest → Walk Forward 建立脈絡，以及 Planner 的 Portfolio Evidence → Planning Assumption 流程。

三個工作群組、頁面身分、紙面層級、表格密度、研究證據與限制的呈現方式已一致。長研究頁大致遵循：Identity / Context → Conclusion / Summary → Evidence → Limitations → Details。沒有發現會阻止發佈的舊 palette、document-level overflow、瀏覽器 console error / warning，或研究揭露弱化。

## Fixes made

- 導覽與 current-page 標示改為以繁體中文為主，三群導覽維持「市場研究／策略研究／個人決策」。
- Stock Detail 依既有 query context 提供返回 Dashboard、Screener 或 Watchlist 的合理返回脈絡，沒有新增 routing system。
- Strategy Lab、Backtest、Optimization、Walk Forward、Portfolio 與 Planner 移除剩餘的裝飾性英文標題與重複 section index；CAGR、Sharpe、IS/OOS、no_candidate 等必要研究術語保留。
- Strategy Lab 模板與狀態文案、Backtest 圖表與頁籤、Portfolio tabs / history / holdings 等主要介面文案統一為繁體中文。
- Planner 的 Research Evidence 與 Planning Assumption 用語統一；明確使用「Historical / OOS ≠ forecast」，並保留兩者分離的操作與視覺層級。
- Watchlist 動態分級 select 補上可識別的 accessible name。
- Dashboard 與 Stock chart 的舊 blue hardcode 改讀既有 C+ semantic chart accent；狀態色與台股紅漲綠跌語意未變。
- 舊 RGBA tint 改為由 evidence / status semantic tokens 派生，沒有重定義 palette。

## Page-level remaining issues

| 頁面 | 結果 | 剩餘事項 |
|---|---|---|
| Dashboard / Stock / Screener / Watchlist / Journal | 一致 | 375px 的資料表仍採區塊內橫向捲動；這是保留欄位完整性的預期行為。 |
| Strategy Lab / Builder / Backtest | 一致 | 策略名稱與 contract 名稱可能含英文，屬使用者資料或研究識別，不做翻譯。 |
| Optimization / Walk Forward | 一致 | 參數名、warning code 與 evidence code 保留原始名稱以維持可追溯性；長表格在窄螢幕使用 local scroll。 |
| Portfolio list / builder / detail / result / holdings / history | 一致 | 大量 rebalance 明細仍然很長，但已置於主要結論與限制之後，並使用可展開的逐期明細。 |
| Financial Planner | 一致 | 七步流程在 375px 仍屬長頁，但導覽、欄位、圖表、Evidence / Assumption 分區均可用。 |

以上均不是 release blocker，也不需要另一輪 redesign。

## Accessibility / responsive

- 1280px、768px、375px 的代表頁皆無 document-level horizontal overflow；寬表格使用自己的 scroll container。
- 以 1280 physical / 640 CSS effective width 檢查 200% 等效 reflow，Portfolio Result 未產生頁面級 overflow。
- Keyboard-only 檢查可依序到達導覽、表單與表格排序控制；focus ring 為 3px、清楚可見。
- `prefers-reduced-motion` 可被偵測，既有 transition / animation restraint 保持有效。
- 主要操作與表單控制的 touch target 合理。小於 32px 的項目只剩原生 checkbox 本體、表格排序文字按鈕與 disclosure summary；前者有可點 label，後兩者的有效儲存格／文字區域仍可操作。
- 動態表單控制有 label 或 accessible name；Screener sortable headers 維持 button + `aria-sort`。
- C+ tokens 對 near-white paper 的 contrast：primary 13.43:1、secondary 6.31:1、action 6.32:1、focus 8.13:1、evidence-secondary 4.74:1；status 與 price colors 為 5.78:1–6.90:1。
- 原始碼沒有公開可用的 dark-mode / theme control，因此本階段未新增主題系統，也沒有 dark-mode release blocker。

## Chart/table/form consistency

- ECharts 的研究線、比較線、drawdown、cash、heatmap 與市場價格色均使用既有 C+ chart semantics；台股 red-up / green-down 僅用於價格方向。
- 研究績效、風險與 warning 不用紅綠判斷好壞；critical / caution / info 仍使用獨立 status tokens。
- 圖表均保留 period / as-of、axis / unit、legend 與 readable tooltip。月營收極值仍保留完整資料與表格 fallback，沒有為了視覺整齊裁資料。
- 數值欄維持靠右與 tabular numerals；長研究表格與 Portfolio 明細在區塊內橫向捲動。
- Portfolio / Research selector 的搜尋與結果範圍仍可用；Planner 的 portfolio、version、run 選擇實際以真實資料完成一次 evidence walkthrough。
- Labels、descriptions 與 errors 保持在欄位附近；這次修改未改動任何送出、計算、API 或資料保存語意。

## Legacy CSS cleanup

- 在 `rg` 與 browser coverage 都確認沒有 consumer 後，移除已停用的 `.section-index` rules。
- 清除本階段碰到的 legacy blue / yellow / red hardcoded tint，改由既有 semantic tokens 派生。
- 其餘 legacy selectors 未因追求 CSS 整潔而刪除；沒有足夠 coverage 證據的規則一律保留。
- Impeccable detector 回報一個 `cramped-padding` warning 與兩個 em-dash advisory。前者是 wrapper 誤判：實際可見的 Dashboard 子卡已有 20px padding；後者主要計入載入佔位符、title 與少量既有說明。沒有為清除 detector 數字而造成版面或語意變更。

## Remaining product limitations

- 現有 unresolved corporate action、assumed publication、partial execution 與資料不足等研究限制會繼續如實顯示；這些是 evidence 狀態，不是視覺缺陷。
- `no_candidate` 仍是 valid cash result；Independent OOS 仍為 primary、stitched 仍為 secondary。
- Portfolio 的 holdings 仍明示 as of last rebalance；Planner Evidence 不會自動成為 Planning Assumption。
- 進階研究頁在手機上需要 local horizontal scroll，因完整欄位與 disclosure 不能被裁切或省略。
- 測試程序會輸出既有的 Starlette deprecation 與 SQLite `ResourceWarning`；389 項測試仍全部通過，且 browser console 為 0 error / 0 warning。這些非本階段 UI 變更造成。

## Release readiness

- Full browser walkthrough：完成。
- Browser console：0 error / 0 warning。
- Full test suite：389 tests passed。
- JavaScript syntax：8 個 external JS 與 5 個 inline scripts 全部通過 `node --check`。
- Python：`compileall app tests scripts` 通過。
- Patch integrity：`git diff --check` 通過。
- Impeccable detector：完成；沒有需要再改動的有效 blocker。
- ui-ux-pro final audit：完成；navigation context、responsive table、focus visibility、contrast、reduced motion、chart semantics 與 disclosure hierarchy 均符合本階段目標。

**READY WITH MINOR RESTRICTIONS**
