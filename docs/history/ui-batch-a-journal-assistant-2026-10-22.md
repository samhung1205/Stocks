# UI 第一批 A：日誌回顧與本機研究助手

日期：2026-10-22（Asia/Taipei）。使用者選擇全部 UI 待辦分批交付，優先 A → B → C，並明確選擇免外部服務的本機研究助手。本報告只記錄 A 的交付；後續狀態以 [UI 改善清單](../UI_IMPROVEMENTS.md) 為準。

## 完成的流程

1. 輸入股票、選擇研究方向與投資週期，按 Enter 或整理按鈕。
2. 助手讀取既有行情、月營收、財報、估值與警訊，提出七項草稿及資料日期；缺少市場共識、個人優勢、催化日期等依據時，直接記為待查證。
3. 使用者直接閱讀，只有需要修改的部分才展開文字欄位。重新整理不蓋掉人工修改；變更股票須先明確重設，避免混用理由。
4. 確認參考進場／停損價，自行設定最大虧損及選填目標價，使用既有後端部位計算，再勾選已檢視後儲存。可保留缺項，記錄完整不代表假說已驗證。
5. 於完整寬度的計畫列表展開原始理由與風險。支援進行中／結案／取消／全部篩選，結案可回看與重開；助手不會改寫既有計畫。

日誌同時補上未儲存狀態、離頁提示、替換草稿確認、重複儲存防護及區塊載入重試。實際成交維持獨立的明確操作，沒有代下單或自動記交易。草稿僅在頁面記憶體，沒有新增跨帳號 browser storage。

## 邊界與實作

- `app/services/journal.py`：只讀證據草擬、原始計畫明細與狀態驗證。
- `app/api/routes.py`：既有 draft-plan 增加 focus query 與向後相容的回應欄位；新增 owner-scoped 計畫 GET 明細。路由契約見 [ARCHITECTURE](../ARCHITECTURE.md#journal-evidence-and-original-plan-readback)。
- `web/js/journal.js`：日誌工作區、人工檢視、未儲存保護、非同步結果隔離與原始計畫 disclosure。
- 沿用既有 trade_plans 欄位，沒有 schema migration。原有不完整計畫、既有私人資料／身分邊界維持。
- 人工檢視勾選是前端流程，沒有宣稱它是後端保存的簽認紀錄。
- 本批沒有接外部語言模型；它是以現有資料與明確規則運作的本機研究助手。

## 當批驗證

使用隔離 SQLite 與合成資料，沒有修改日常／Railway 資料庫。

- `tests/test_journal.py`：7 項通過。覆蓋有日期的事實、明確未知、唯讀草擬、稀疏資料、無效股票／方向、有效停損參考、結案後原文保留及狀態驗證。
- `tests/test_accounts.py`：27 項通過。新增明細的跨帳號 404、管理員不能讀他人原始理由、本人結案後可讀等驗證。
- `tests/test_api_error_handling.py`：5 項通過。既有 position-size／simulate 的正確與錯誤回應仍通過。
- Playwright：28 項流程檢查通過，包括人工修正保留、一次明確儲存、重複儲存防護、回讀／結案／重開、離頁保護、失敗保留輸入、避免跨股票混用、稀疏資料、不自動記成交。
- 1280px 桌面檢視，以及 768／375／320px 無整頁橫向溢出；200% 文字大小檢查通過。
- Python changed-files compileall、JavaScript node --check、git diff --check 通過。
- Impeccable 對 journal.html 掃描一次，結果為空陣列。未做實機螢幕閱讀器認證或完整後端研究 suite；本批沒有改研究／Portfolio 行為。

本機展示截圖與機器檢查結果位於 `output/playwright/journal-assistant/`，屬忽略版控的本機產物。第一次 full-page 截圖的拼接有重複 viewport，已核對 DOM 只有一個導覽與七個問題，改以 viewport 圖確認；這不是應用程式重複內容。

## 下一批

B：個股無效／失敗狀態、圖表等價資訊、tabs、治理連結。C：Planner 與策略建立的未儲存保護。其餘評估待辦仍在追蹤清單，未被本次完成狀態掩蓋。
