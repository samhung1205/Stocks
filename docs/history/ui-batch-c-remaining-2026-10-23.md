# 第三批 C 與剩餘 UI 待辦

日期：2026-10-23（Asia/Taipei）。依使用者「繼續下一批把剩餘完成」的授權，延續 Impeccable harden／Operate 指引與既有研究卷宗樣式，完成 [UI 改善清單](../UI_IMPROVEMENTS.md) 的 C 與其餘已選項目。先前 A／B 的成果保留；原評估分數不倒寫。

## 交付

- **Planner 草稿**：明確區分試算、未儲存、儲存中與已儲存；切換／新增情境先確認捨棄。儲存失敗保留輸入；儲存回應只標記送出時的修訂，不覆蓋稍後輸入。未確認的來源會阻止儲存並聚焦提示；新情境清除舊來源暫存、返回第一步。情境與試算回應使用世代檢查，避免舊結果覆蓋目前情境。
- **策略草稿**：八步設定有 dirty／beforeunload 保護，寫入期間保護表單、失敗保留設定。沿用不可變版本 API，不改寫舊版本；編輯欄位也保留既有 `partial` 缺價政策，避免載入時被其他選項取代。
- **研究閱讀**：策略、穩健性、WF 的重複來源資訊收束到明細；結論優先，固定版本、日期、原始參數及協定保留。穩健性補上全距、IQR、標準差、峰值鄰點落差說明。自訂 JSON 失敗時保留文字、展開錯誤區並聚焦；解析器提供 position 時選取該字元。WF 空白頁有有效來源入口，協定值使用中文主標。
- **Portfolio**：帶 run 的 URL 預設開啟研究結果，執行與歷史紀錄次層化；結果明示固定版本。建立前顯示已完成 OOS 來源與已評估／無候選 fold 數，無來源先提供入口。這是可選來源準備度，並非配置方法的統計證據認證，仍受既有最近 200 筆工作目錄限制。警告先顯示問題、日期、下一步，原始診斷可展開；critical 保留嚴重度並預設展開。手機增加等價檢視選單。
- **篩選與觀察名單**：條件按營運、估值、交易分組，進階條件漸進展開；預設名稱明示立即執行。觀察名單顯示收盤日期、具名可聚焦警訊，分級成功後恢復焦點並以 live status 宣布。警訊隱藏輔助名稱定位在自己的表格捲動區，避免窄螢幕整頁溢出。
- **Planner 導覽與文案**：輸入步驟可跳到相關試算結果，結果可返回對應輸入；風險問題使用 fieldset／legend。主要來源與規劃文案中文化，原始來源欄位可展開查閱，研究證據與規劃假設保持分離。
- **帳號**：登入、啟用／重設與修改密碼加入具名顯示控制；失效 token 有管理員恢復說明及返回登入入口。管理員先看到成員工作區，按鈕可存取名稱含對象；一般成員看到適用自己的說明。

## 草稿政策與邊界

本批採頁面記憶體與離頁確認，不加入 localStorage／sessionStorage 草稿或新資料表。取消離頁可繼續編輯；接受離開、程序終止或當機後不會自動還原。跨重新整理／裝置持久化須另行設計帳號隔離、保存期限及刪除流程，未宣稱已實作。

本批只改前端與文件，沒有新端點、migration、外部語言模型、研究算法或研究契約版本變更。沒有修改日常資料庫或部署 Railway。A／B 既有未提交變更保留。

## 驗證與限制

**75 項瀏覽器檢查通過**：22 項草稿／帳號操作與 53 項頁面／邊界檢查。實際 FastAPI、帳號 middleware、SQLite 與合成研究工作用於正常流程；HTTP 503、無 OOS 來源及警告樣本以瀏覽器攔截注入。完成策略回測、穩健性、WF 與 Portfolio 的合成結果用於來源及固定版本呈現檢查，並非真實市場有效性驗證。

覆蓋 1280／375px 十種主要頁面狀態、頁面橫向溢出、圖表 resize 後的穩定尺寸、指定結果優先、來源明細、手機切換、風險題組、分級焦點、失效 token 及角色差異。草稿檢查包含取消切換／離頁、失敗重試、儲存中繼續編輯、未完成來源及策略八步返回。未預期 JavaScript runtime errors 為 0；故意注入的 HTTP 失敗不算程式錯誤。

初輪的觀察名單分級實際 API 會附帶 deep-fetch，使暫存 fixture 的收盤日期更新；辨識後，最後頁面檢查已停用該背景抓取及排程、重建固定合成資料再確認。所有預覽 DB 均為臨時 SQLite，未以日常 DB 為 fixture。桌面／手機畫面批次檢視後修正勾選框、手機連結與輔助文字定位；圖表量測等待 resize 完成，避免過渡尺寸誤判。不是實機螢幕閱讀器認證或所有裝置測試。

**後端回歸 70 項通過，21 項跳過**：

| 測試檔 | 結果 |
|---|---|
| `tests/test_accounts.py` | 27 通過 |
| `tests/test_strategy_lab_backend.py` | 24 通過，含版本不可變回歸 |
| `tests/test_finance_portfolio_integration.py` | 19 通過、21 因缺真實固定研究 fixture 跳過 |

命令為 `.venv/bin/python -m unittest discover -s tests -p '<上述檔名>'`，使用 `STOCKS_AUTH_MODE=basic`、`STOCKS_DATA_DIR=/tmp/stocks-ui-tests`、`STOCKS_DB_URL=sqlite:////tmp/stocks-ui-tests/test.db`；該隔離 DB 先以 `app.db.init_db()` 建表。未用真實私人研究填補跳過案例。本批無 Python 變更；七份變更 JS 與兩份 HTML inline scripts 經 `node --check`，`git diff --check` 通過。

本機證據（output 按專案規則不納入版控）：[草稿檢查](../../output/playwright/ui-remaining/flows.json)、[頁面檢查](../../output/playwright/ui-remaining/pages.json)、[桌面分組篩選](../../output/playwright/ui-remaining/screener-1280.png)、[手機投資組合](../../output/playwright/ui-remaining/portfolio-375.png)、[管理員工作區](../../output/playwright/ui-remaining/account-1280.png)、[token 恢復](../../output/playwright/ui-remaining/token-recovery-375.png)。截圖來自驗證過程各批次；隨機研究 ID 僅代表當次暫存 fixture。
