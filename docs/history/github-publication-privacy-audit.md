# GitHub 上傳前隱私稽核

日期：2026-10-12。目標：提交目前專案程式、測試及文件，排除個資、秘密與非必要本機產物。

## 範圍與處理

檢查 tracked／untracked 檔案、Git 忽略規則、來源及文件中的憑證樣式、個人 email／本機使用者路徑，以及本機 `.env` 的實際金鑰值是否出現在待發布文字中。秘密值只在記憶體中比對，不列印或寫入報告。測試中的範例帳密是固定 fixture，不是部署帳密。

| 不納入 GitHub | 原因 |
|---|---|
| `.env`、`.env.*`、私鑰檔 | API 金鑰、登入或連線秘密 |
| `data/`、SQLite DB／WAL／SHM／journal、備份檔與 `backups/` | 本機行情、研究、財務及交易資料，不是產品原始碼 |
| `.railway/` | 含特定環境的本機 IaC／CLI 設定及相依套件 |
| `.playwright-cli/`、`output/`、測試報告與快取 | 瀏覽器快照、暫存截圖、研究輸出與執行產物 |
| `docs/assets/k1a/` | 歷史 UI 截圖含財務規劃金額，保留本機供追溯 |
| `spec/`、`.venv/` 及本機代理暫存 | 原有本機規格與執行環境 |

已補強 `.gitignore`。舊報告的截圖引用改為標示「本機歷史截圖」，不再承諾 GitHub 上有圖片。部署文件移除私人工作區名稱、專案／部署 UUID、快照指紋與特定暫存位置；開發代理文件移除直接寫出的個人 email，作者規則仍依現有 Git 設定。Git 作者 metadata 沿用遠端既有的人類作者身分，不新增 AI author／committer／co-author。

## 發布歷史

原本尚未上傳的 Stage K2 checkpoint `v1.0.0` 含 UI 截圖。僅在下一個 commit 刪圖，仍會隨 Git 歷史上傳圖片。

使用者選擇保留該 checkpoint 在本機，將清理後的完整來源快照建立為現有 GitHub main 的後續提交。原 `v1.0.0` tag 不移動、不推送；原 checkpoint 另有本機備援分支保留。此次只推送 main，不使用 force push，也不推送所有分支／tags。這是對新增上傳內容的稽核，不重寫已存在於遠端的舊歷史。

## 驗證

- 完整 `unittest`：442 項，通過；Python 3.13 顯示部分 SQLite connection 的 `ResourceWarning`，未造成測試失敗。
- `compileall app tests scripts`：通過。
- JS 語法：9 個 JS 檔案與 5 個 HTML 內嵌 script 通過。
- `sh -n scripts/start-railway.sh`：通過。
- `git diff --check`：通過。
- 待發布索引共 193 份文字檔，沒有二進位截圖或排除路徑；憑證、個資樣式與 2 個本機實際金鑰比對無命中。
- 175 個本機 Markdown 連結目標均包含在發布內容中，沒有缺失。

沒有更動產品功能，也沒有在此工作執行正式 DB 遷移或部署。部署驗證的日期與資料缺漏仍以原部署紀錄為準。
