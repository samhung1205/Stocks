# 文件導覽

本目錄分成**現行參考**與**歷史紀錄**。新任務先讀對應的現行參考，再依需要查閱當時的 Stage 報告與測試；歷史報告的測試數、待辦與「下一階段」敘述只代表成文當時。

## 現行參考：日常會用到

| 需求 | 先讀 | 再核對 |
|---|---|---|
| 產品定位、使用方式 | [README](../README.md)、[PRODUCT](../PRODUCT.md) | [專案狀態](PROJECT_STATUS.md) |
| 開發環境、測試、金鑰設定及歷史回補指令 | [DEVELOPMENT.md](DEVELOPMENT.md) | `pyproject.toml`、`app/config.py`、`app/backfill.py` |
| 目前做到了什麼、哪些仍有限制 | [PROJECT_STATUS.md](PROJECT_STATUS.md) | 程式及相關測試 |
| 模組、資料流、DB／API／UI 邊界 | [ARCHITECTURE.md](ARCHITECTURE.md) | `app/`、`web/`、`tests/` |
| 時點資料、OOS、版本、Portfolio 等研究契約 | [RESEARCH_CONTRACTS.md](RESEARCH_CONTRACTS.md)、[AGENTS.md](../AGENTS.md) | 契約版本、相關 Stage 報告與測試 |
| Railway 部署與最後驗證狀態 | [railway-deployment.md](railway-deployment.md)、[DEPLOY.md](../DEPLOY.md) | 線上健康與資料 freshness；紀錄是 dated snapshot |
| AI 或開發代理要修改專案 | [AGENTS.md](../AGENTS.md) | `CLAUDE.md` 僅含 `@AGENTS.md` 相容匯入；共用規則只維護一份 |

正式 `v1.0.0` tag 是 Stage K2 checkpoint；本工作樹後續的 L1/L1.1 與 Railway 部署不可倒寫成 v1.0.0 已包含的功能。更新現行文件時，依程式和實測結果同步修正；不要只複製 Stage 報告的舊敘述。

## `history/`：需要追溯時才讀

- `stage-*.md`：Stage A–L1.1 的交付、契約設計、hotfix 與驗證報告。依 [PROJECT_STATUS.md](PROJECT_STATUS.md) 的階段表找到相關報告；若早期規則被 hotfix 取代，以 [RESEARCH_CONTRACTS.md](RESEARCH_CONTRACTS.md) 和較新報告為準。
- `coverage-gate-report.md`、`strategy-lab-integration-audit.md`、`documentation-sync-h1-report.md`、`product-sync-h1-5-report.md`：當時的覆蓋率、整合與文件同步稽核。
- [GitHub 上傳前隱私稽核](history/github-publication-privacy-audit.md)：2026-10-12 排除資料與圖片、保留本機 checkpoint、乾淨提交及驗證紀錄。
- [README 使用手冊整理報告](history/readme-user-manual-reorganization-report.md)：2026-10-08 使用手冊改寫、技術內容移轉與驗證紀錄。
- `final-design-direction.md`、`k1a-palette-comparison.md`：設計探索與比較；目前 UI 採 K2 完成的樣式，這些檔案不代表待實作規格。

歷史紀錄保留供查核研究決策及回歸原因，不作為「目前狀態」的獨立真相來源。`assets/k1a/` 是本機歷史截圖，因可能含財務畫面而不納入 GitHub；`output/` 與 `.playwright-cli/` 是本機產物，不屬於版控文件，歷史報告若指向它們可能無法在新 checkout 顯示。

## 維護順序

1. 改變產品行為或部署狀態時，先驗證程式、設定與測試，再更新 [PROJECT_STATUS.md](PROJECT_STATUS.md) 及相關現行文件。
2. 若架構、DB schema、API 或研究／Portfolio 契約改變，同步更新 [AGENTS.md](../AGENTS.md) 與 [ARCHITECTURE.md](ARCHITECTURE.md) 或 [RESEARCH_CONTRACTS.md](RESEARCH_CONTRACTS.md)；保留舊 Stage 報告的歷史語境。
3. 引用歷史材料時使用 `docs/history/` 路徑；新增文件先決定它是現行維護參考，或當次交付紀錄。
