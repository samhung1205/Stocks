# README 使用手冊整理報告

日期：2026-10-08。範圍：文件改寫、技術內容移轉與導覽同步。

## 整理目的

原 README 同時承載使用介紹、模組與框架對照、測試指令、設定、API 及開發限制。依使用需求，將 [README](../../README.md) 改為繁體中文為主、英文名詞為輔的使用手冊，讓使用者依畫面完成操作；原技術資訊轉入現行開發參考，歷史過程仍由既有 Stage 報告保存。

## 內容去向

| 原 README 內容 | 整理後位置與處理 |
|---|---|
| 定位、啟動、首次使用 | README 第 1–3 節，補上本機／雲端資料分離及工作續跑說明 |
| 功能總覽 | README 第 4–7 節改為操作流程；模組與原始框架章節對照保留於 [DEVELOPMENT.md](../DEVELOPMENT.md) |
| 測試、資料來源與金鑰、成本與排程設定 | DEVELOPMENT.md，保留指令並核對實際設定載入順序 |
| 架構、目錄、研究方法與限制 | DEVELOPMENT.md 保留技術摘要；[ARCHITECTURE.md](../ARCHITECTURE.md)、[RESEARCH_CONTRACTS.md](../RESEARCH_CONTRACTS.md)、[AGENTS.md](../../AGENTS.md) 繼續作為正式定義 |
| Strategy Lab 歷史回補 CLI、API 與公告日期契約 | DEVELOPMENT.md；README 只保留網站回補流程與結果判讀 |
| Stage／release／部署狀態 | [PROJECT_STATUS.md](../PROJECT_STATUS.md)、[Railway 部署紀錄](../railway-deployment.md) 與既有 `history/` 報告；README 保留版本範圍提示 |

沒有另建重複的歷史資料夾。現行開發參考放在 `docs/`，這次整理紀錄沿用 `docs/history/`。同步更新文件索引及 AGENTS、PRODUCT、CLAUDE 的文件用途與入口。

## 手冊補充

- 依實際導覽分成市場研究、策略研究與個人決策，說明各頁的操作入口。
- 加入資料狀態、歷史回補、觀察名單與實際持倉的差異，以及日誌計畫／成交紀錄流程。
- 說明回測 → 穩健性 → 樣本外 → 策略組合的研究順序、獨立 OOS 證據與無候選留現金。
- 整理財務規劃七步、儲存情境、研究來源版本連結及報酬假設確認。
- 補上常見問題與名詞表，保留證據不足、研究資料缺漏及未實作配置的限制。

## 核對與修正

核對 `web/` 的導覽、按鈕、表單與結果提示，以及 `app/config.py`、`app/backfill.py`、`app/api/routes.py`、`tests/smoke.py` 和 `pyproject.toml`。

舊文將交易成本及 `AUTO_REFRESH_*` 一律寫成「`.env` 可覆寫」，但 `app/config.py` 在讀取 `.env` 前就初始化這些常數，且沒有再次讀取。開發文件改為啟動前設定行程環境變數，並說明 `.env` 不支援行尾註解；未修改設定程式。來源額度的歷史實測值也不再當成目前方案保證。

`v1.0.0` 仍是 Stage K2 checkpoint；L1/L1.1 與私人部署是後續工作樹內容。部署登入最後確認日仍為 2026-10-06、資料完整性最後檢查日仍為 2026-10-05，未將此次文檔整理描述成新的線上驗證。

## 驗證範圍

- 檢查 47 份現行與歷史 Markdown 文件，共 186 個本機連結目標，沒有缺失。
- 核對手冊操作名稱、技術文件引用的模組／指令選項及 API 路徑。
- `git diff --check` 通過，無空白格式錯誤。

此次只改 Markdown 文件，未修改產品功能、資料庫或設定，也未重跑產品測試或外部資料抓取；先前測試紀錄不當成此次執行結果。本報告記錄文件整理，不建立或移動 release tag。
