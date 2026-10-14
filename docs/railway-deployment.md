# Railway 部署紀錄

更新：2026-10-12。**正式應用、持久資料庫與登入保護已上線；HTTPS 驗證通過，使用者已於 2026-10-06 確認登入成功且使用正常。最近一次部署為 2026-10-12 的前端介面更新，登入後畫面尚待使用者確認。**

正式網址：[台股研究平台](https://stocks-production-eeb3.up.railway.app)。使用 Railway Variables 設定的帳密登入；目前沒有註冊或獨立使用者資料隔離。

## 已完成

- Railway Hobby 私人工作區已建立；工作區名稱不收錄於版控文件。
- 已建立私人專案 Stocks，production 環境及 `stocks` 服務。官方 CLI 已完成授權與連結。
- 工作區 Compute 硬上限 US$10、提醒 US$8，已在 Usage 頁儲存並確認。這是用量限制，不代表整張帳單固定 US$10；Agent、方案費及帳單抵扣依 Railway 規則。
- `Dockerfile`、`.dockerignore`、`.railwayignore`、`scripts/start-railway.sh` 已準備；Python 3.12，依 `uv.lock` 安裝，一個 Uvicorn worker。
- 本機 `.railway/railway.ts`（含特定環境設定，不上傳 GitHub）使用官方 SDK 3.12.0：新加坡、一個 replica、不休眠、2 GiB 記憶體／1 vCPU 上限、5,000 MB 持久磁碟、`/api/health`。設定已套用，`stocks-data` 已掛載 `/data`。
- 初次部署 已完成雲端 build、push 與健康檢查。匯入期間以 `STOCKS_IMPORT_MODE=1` 僅執行 `scripts/railway-import-server.py`；核對完成後已切換為正常應用。後續正式部署 為 SUCCESS，正式 HTTPS 網址已建立。
- 整站 HTTP Basic 登入保護已加入；未設定完整帳密時拒絕存取。健康檢查不透露個人資料。
- SQLite backup API 的一致性快照已通過 `PRAGMA integrity_check`：636,481,536 bytes。快照包含已提交 WAL 資料，不含舊備份。
- 本次快照保存在本機暫存目錄，是暫存搬移用檔；原始 `data/stocks.db` 保留。已上傳到持久磁碟 `/stocks.db`（容器 `/data/stocks.db`），沒有覆蓋既有檔案。
- 使用者已明確授權公開金鑰登錄，部署用公開金鑰已登錄到私人工作區；私鑰未上傳。一般 CLI SSH 需以 `-i` 指定這把已授權金鑰。
- 遠端 SHA-256 與本機快照相同；實際檔案指紋保留於本機。遠端 `PRAGMA integrity_check` 為 `ok`。
- 已關閉匯入模式，正式應用 `/api/health` 回傳 200／`status: ok`。啟用 `ENABLE_SCHEDULER=1`、`AUTO_REFRESH_ENABLED=1`，並設定 `TZ` 與 `AUTO_REFRESH_TIMEZONE` 為 `Asia/Taipei`；新容器中實際建立的 CronTrigger 時區也是 `Asia/Taipei`。
- 使用者已自行新增兩個登入變數；原先因為 2 項設定尚未 Deploy，執行中容器仍回傳 503。已在 Railway 套用變更，現在兩個名稱皆存在，未登入請求回傳 401。代理未讀取帳密值。

## 驗證結果

- `tests/test_private_access.py`：5 項通過。
- `tests/test_data_freshness.py`：48 項通過。
- `compileall app tests scripts` 及 `git diff --check` 通過。
- 在正式快照的另一份 SQLite backup 上執行 lifespan：GET／HEAD `/api/health` 回傳 200；缺少帳密時首頁、財務 API、API 文件回傳 503。
- 使用只存於記憶體的測試帳密：首頁、ECharts、市場 dashboard、OpenAPI 回傳 200；未登入回傳 401；回應禁止快取。
- 缺少或無效 SQLite 檔時，雲端 startup 在 migration 前拒絕啟動，不會建立空白資料庫。
- 遠端 build、資料上傳、重啟保存及官方資料來源連線驗證已完成。
- 公開 HTTPS 實測：GET／HEAD `/api/health` 回傳 200，GET 回應 `status: ok`；首頁、API 文件、管理 freshness API、ECharts 未登入均回傳 401 並帶 Basic challenge；POST `/api/health` 也回傳 401。所有這些回應帶 `Cache-Control: no-store`。TLS 驗證正常，未停用憑證檢查。
- 最後一次 `railway config plan` 沒有變數、網域新增／刪除，也沒有 destroy；僅再次列出 Railway 讀回 null 的 restartPolicyType／sleepApplication 預設差異，未為此重複 apply。
- 自動瀏覽器開啟正式頁面收到 `ERR_BLOCKED_BY_CLIENT`，未繞過瀏覽器阻擋；已提供連結讓使用者自行登入。使用者於 2026-10-06 回覆「登入成功且使用正常」，完成使用者端登入與使用確認。
- 遠端搬移前後核對：stocks 3,202、daily_quotes 2,476,523、valuations 1,582,700、strategy_versions 11、walkforward_jobs 7、strategy_portfolio_versions 1,155、portfolio_research_runs 660、finance_targets 26。匯入時行情最新日期 2026-09-24。
- 中斷後官方 CLI 連線已恢復；匯入容器觀測記憶體約 17 MB、CPU < 0.01 vCPU。這不是正式研究工作負載的用量。
- 重新部署後 strategy_versions 11、strategy_portfolio_versions 1,155、portfolio_research_runs 660、finance_targets 26 保持不變。自動啟動補資料已將行情增加至 2,481,370 筆、最新 2026-10-02；法人與大盤摘要也推進到 2026-10-02。
- 2026-10-05 部署驗證時自動更新為 `partial`：交易日曆核對出 2026-09-29、2026-09-30 缺漏；部署驗證使用既有上鎖、有限重試流程再執行一次，回傳 days_filled 0、days_skipped 2，仍未補齊。沒有放寬完成判定或標記為 fresh。當時估值最新日期為 2026-09-24；既有多日 backfill 不抓估值。當時研究 benchmark 為 2026-09-18，明確不由 daily auto-refresh 更新。2026-10-06 僅記錄使用者登入確認，未重新核對行情更新狀態。
- 正式應用補資料後觀測：記憶體約 410 MB（10 分鐘最大 460 MB）、CPU 最大 0.13 vCPU、磁碟約 761 MB（15%）。這不代表長時間最佳化／Walk Forward 的最大負載已驗證。

## 2026-10-12 前端介面更新部署

- 部署內容：commit `b0584f0`（前端介面更新）。只改 `web/` 下的 HTML／CSS／JS，沒有改 Python、資料庫結構、API 或 Railway 設定與變數。
- 變更範圍：導覽選單（滑鼠移開、點擊外部、Esc 可關閉；箭頭置中）、K 線圖不再攔截頁面滾輪、篩選器條件與排序寫入網址、個股頁結論列、失敗訊息常駐顯示，以及策略研究頁（策略實驗室、參數穩健性、樣本外驗證、策略投資組合）的用語中文化。
- 部署方式：這台機器未安裝 Railway CLI，改以 `npx @railway/cli`（5.64.2）沿用既有授權與專案連結執行 `railway up --service stocks`；沒有執行 `railway config apply`。
- 結果：部署 `141875ad` 為 SUCCESS（2026-10-12 16:19，台北時間），健康檢查通過。
- 公開 HTTPS 實測：GET `/api/health` 回傳 200／`status: ok`；未登入開首頁回傳 401。
- 未驗證：代理沒有帳密，登入後的頁面未實際檢視；資料表筆數與 `/api/admin/data-freshness` 這次也沒有重新核對。
- 重新部署會重啟服務；當時若有執行中的回測或研究工作會被標記為失敗，需由使用者續跑。這次沒有確認部署當下是否有工作在執行。
- 本次沒有新增自動化測試：變更不涉及 Python，現有測試也不涵蓋前端；前端在本機瀏覽器逐頁檢查過。

## 下一步

1. 先重新讀取線上 `/api/admin/data-freshness`（需登入）與實際表資料，確認 2026-09-29、2026-09-30 缺口及估值延遲是否仍存在；不要把 2026-10-05 的 `partial` 當作今日即時狀態，也不要未經驗證就宣稱已修復。
2. 為 production volume 建立持續的一致性 SQLite 備份與還原演練；目前只驗證了首次匯入與重啟保存，持久磁碟本身不等於備份。
3. 2026-10-14 工作樹已實作邀請制帳號、admin/member 與私人資料隔離；**尚未遷移正式資料庫或部署**。請依 [ACCOUNTS.md](ACCOUNTS.md) 的維護、備份、初始化及驗收順序上線，不能把目前線上的共用帳密視為多使用者系統。

## 注意事項

- 最後驗證的線上版本使用共用帳密及資料；新程式的個別使用者隔離必須完成上述遷移部署後才生效。
- 上線後本機與 Railway 的 SQLite 是兩份獨立資料庫，不會自動雙向同步。
- 持久磁碟避免重啟丟資料，但仍需要獨立備份；不要把舊 DB 備份全放在 production volume。
- 目前既有排程使用 15 分鐘的時鐘格點（15:00、15:15…），`AUTO_REFRESH_CHECK_TIME` 的分鐘尚未精確套用；此部署沒有修改這項既有行為。自動更新仍以實際缺漏交易日及重試契約為準。
- 官方文件已改用 IaC；新服務不能使用 `railway.json`／`railway.toml`。既有格式於 2026-12-01 停止讀取。

## 官方文件

- [IaC 與設定格式](https://docs.railway.com/infrastructure-as-code)
- [IaC SDK](https://docs.railway.com/infrastructure-as-code/reference)
- [CLI 部署](https://docs.railway.com/cli/up)
- [持久磁碟檔案操作](https://docs.railway.com/cli/volume)
- [用量控制](https://docs.railway.com/pricing/cost-control)
