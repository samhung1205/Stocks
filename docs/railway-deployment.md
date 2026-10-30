# Railway 部署紀錄

更新：2026-10-31。**首頁觀察名單速覽等高、分頁與篩選已部署；既有邀請制帳號、精簡資料狀態與 v7 資料庫沿用。** 最新前端部署見 2026-10-31 一節；資料修復與核對日期見 2026-10-30 一節。早期 Basic 與較舊資料狀態保留歷史語境。

正式網址：[台股研究平台登入](https://stocks-production-eeb3.up.railway.app/login.html)。原擁有人使用既有帳密登入管理員，朋友由管理員邀請，各自私人資料隔離，沒有公開註冊。

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

## 2026-10-14 邀請制帳號與資料遷移部署

- 使用者授權保留原帳號並部署、commit／push。程式 commit `83960d5`，完整隔離 DB 套件 468 項通過（其中帳號測試 26 項）；compileall／JS 語法／diff 檢查通過。Docker build 明確包含 accounts.py／backup_sqlite.py，修正 `.dockerignore` 的 allowlist。
- 遷移前確認雲端 Backtest／Optimization／Walk Forward／Portfolio 沒有 queued/running；先部署 health-only 維護版 `9ce52247`，health=`importing`、私人頁拒絕存取，app/jobs 不執行，再進行 DB 操作。
- 本機先備份為 `data/backups/before-accounts-20261014-182622-845606.db`（645,578,752 bytes）；雲端先備份為 `/data/backups/pre-accounts-20261014-cloud.db`（642,273,280 bytes），operator CLI 另建 `/data/backups/before-accounts-20261014-183217-225727.db`。所有備份使用 SQLite backup API 並通過 integrity_check。
- 雲端備份已透過 SSH gzip 串流下載，本機保留 `data/backups/pre-accounts-20261014-cloud.db` 及壓縮副本；解壓後與遠端 SHA-256 相同、大小相同、integrity_check=ok。中斷的一般下載 partial 已清除；備份均不入 Git 或部署映像。
- 本機與雲端各自遷移為 schema v7；兩者的 24 張私人表原有欄位／內容指紋在遷移前後一致，舊資料根表全部歸 id=1。兩份資料庫互不覆蓋，之後的資料與密碼變更也不自動同步。
- 使用 operator-only `init --from-basic-env` 保留原帳密，直接 Argon2id 雜湊轉入管理員，不列印帳密、不寫入本機 `.env` 或 Git；沒有真實朋友帳號。既有短密碼只允許這次轉入，後續新密碼要求仍為 12–128 字元。
- 解除維護：`STOCKS_IMPORT_MODE=0`、`STOCKS_AUTH_MODE=accounts`。正式部署 `17331cbf-d4bc-4246-89e3-295953a09bd0` 於 2026-10-14 18:35（台北時間）SUCCESS，app startup／scheduler 正常。保留既有 `/data`、單 replica/worker 與 Taiwan TZ，未執行 config apply 或新增資源。
- 正式 HTTPS 驗收通過：原帳密登入 200，me 為 id=1/admin；帳號頁、管理員帳號列表、Planner、持股、策略及 Portfolio 讀取 200。匿名 me 401、帳號頁 303 至登入頁；Secure/HttpOnly/SameSite=Lax cookies；缺 CSRF logout 403，正確 logout 200，撤銷後 me 401。TLS 未放寬。沒有列印任何私人頁面的回應內容。
- 本機服務已在 loopback 8787 啟動，使用相同原帳密完成同一組登入／讀取／CSRF／登出檢查。本機 HTTP 的非 Secure cookie 僅限此 loopback 開發設定，Railway 固定 Secure。
- 本次沒有送出真實邀請、變更朋友權限或執行正式還原演練。成員禁止管理與 A/B 資料隔離由隔離 DB 測試驗證；資料 freshness 完整性仍依當下回應與研究契約核對，不能從帳號部署成功推定。

## 2026-10-23 UI 改善與日誌研究助手部署

- 程式 commit `1db91e7`：日誌本機證據草稿、原計畫回看、個股證據與圖表表格、各頁易用性改善。`app/db/tables.py` 與 `app/db/migrations.py` 均無變更；日誌使用原有欄位，故沒有執行資料庫遷移或修改正式私人資料。
- 在本機資料庫的 SQLite backup API 隔離副本上執行完整 `unittest`，480 項全數通過；Python `compileall`、改動的 JS 語法與 `git diff --check` 通過。先前空白隔離 DB 因既有 Walk Forward API 測試需要交易日曆而有一項錯誤，改用文件規定的隔離 fixture 副本後通過，未將測試寫入日常 DB。
- 部署前只讀核對正式 DB：schema v7、`PRAGMA quick_check=ok`，Backtest／Optimization／Walk Forward／Portfolio 均無 queued/running 工作。以 `scripts/backup_sqlite.py` 建立 `/data/backups/pre-ui-release-20261023.db`（645,345,280 bytes，`integrity_check=ok`）；既有工作區 SSH 公鑰已登錄，無需新增金鑰。
- 壓縮副本也已下載到本機忽略版控的 `data/backups/pre-ui-release-20261023-cloud.db.gz`；`gzip -t` 通過，解壓資料的 SHA-256 與遠端原始快照完全相同。Railway volume SFTP 曾逾時，改用既有 SSH 連線傳輸完成。
- 沿用現有 CLI 來源、volume 與環境設定，`railway up --service stocks --environment production` 部署 `8307de34-7e7b-42b6-afc5-90b9a0ded530`，Railway 顯示 SUCCESS 且健康檢查通過。新容器啟動、排程器啟動；startup catch-up 當時回報 `already_fresh`，這不等於獨立核實所有行情與研究資料完整。
- 部署後遠端再次確認 schema v7、`quick_check=ok`、備份仍存在、四類研究工作無 queued/running；`STOCKS_IMPORT_MODE=0`、`STOCKS_AUTH_MODE=accounts`，`TZ`／`AUTO_REFRESH_TIMEZONE` 都是 `Asia/Taipei`。公開 HTTPS `/api/health` 為 200／`status: ok`、登入頁 200；未登入私人 HTML 轉至登入頁（303）、日誌與 Planner API 回 401。
- 當次只讀最新日期：`daily_quotes` 2026-10-22、`valuations` 2026-10-21、`benchmark_series` 2026-09-18；交易日曆已排到 2026-12-31。`MAX(date)` 只說明最近一筆日期，不證明期間每個交易日都完整；估值及研究基準仍需分別檢查與維護。
- 未持有使用者帳密，本次未重新執行登入後的真實資料操作與瀏覽器驗收。行情完整性仍應看登入後的資料狀態及獨立研究基準欄位。

## 2026-10-30 資料核對、精簡狀態與修復

- 使用者授權本批 commit／push、部署及先備份再修復。程式 `62a8b92` 已推送 main；部署 `1711d871-09f4-44a7-b048-533caf01bb1d` 為 SUCCESS，正式容器本次 11 個 app/web 檔案指紋符合該 commit。
- 備份 `/data/backups/pre-freshness-20261030.db` 為 648,056,832 bytes，SQLite backup API 與完整性檢查通過；本機忽略版控的 `data/backups/pre-freshness-20261030-cloud.db.gz` 解壓指紋與遠端一致。部署前無 queued/running 研究或歷史回補；部署後 schema v7、quick_check=ok。
- 原帳密 HTTPS 登入、admin 身分、新首頁／JS 及登出撤銷均通過；匿名市場 API 401。帳號記錄、主要擁有人 24 張私人表筆數及 12 張保存內容指紋與備份一致。未進行 schema 遷移、私人研究重算或朋友帳號操作。
- 自動修復已用官方證據修正 10/23 休市日及近期法人缺漏。待共用鎖釋放後，近 30 天手動修復 completed、7 日補寫／12 日略過／無錯誤。獨立 benchmark 工作 completed、24 筆，TAIEX_TR 已補至 10/29。
- 修復後獨立唯讀核對 10/1～10/30 的 19 個交易日：行情／法人逐市場均達有效筆數門檻，摘要核心欄位存在；缺漏日期清單皆為空。
- 最終登入核對為 fresh/idle：行情、法人及摘要至 10/30；估值維持實際 10/29 日期並揭露一交易日發布落差。自動更新、排程、台北時區與 16 次／240 分鐘上限均由實際容器確認。單 worker/replica、既有 volume、不休眠與環境變數保持既有設定。
- 495 項隔離 DB 測試與桌面／平板／手機合成畫面驗證見 [完整紀錄](history/freshness-repair-ui-2026-10-30.md)。正式驗證為 HTTPS/API 與容器資料核對，未擷取私人畫面的瀏覽器截圖。

## 2026-10-31 首頁觀察名單速覽

- 使用者授權本次 commit／push 及部署，程式 `573b36e` 已推送 main。Railway 部署 `266714ab-ef56-4a49-bd2b-6223eb0af4c3` 為 SUCCESS；前端改為桌面等高、每頁 4 檔、決策階段與產業主題交叉篩選，手機維持分頁與上下布局。
- 部署前四類研究與歷史回補均無 queued/running。沿用既有 service、volume、單 worker/replica 及環境設定；此批沒有 Python、API 或 schema 變更，也沒有操作資料修復。
- 正式 HTTPS：health 200/ok；匿名 watchlist 401；原帳密登入與 admin 身分 200；首頁、CSS、新速覽 JS 回應指紋符合提交；名單讀取 200；登出後 me 401。未輸出私人名單內容或擷取正式私人畫面。
- 39 項本機合成資料瀏覽器檢查、600 檔壓力版面及四種寬度驗證見 [交付紀錄](history/dashboard-watch-preview-2026-10-31.md)。這次未重新核對市場資料完整性，勿把前端部署成功當成新的 freshness 證據。

## 下一步

1. 之後依線上 `/api/admin/data-freshness`（需登入）、各資料集日期及獨立研究基準核對新交易日；不要把此次 fresh 當作所有歷史期間、所有股票都已完整的保證。9/29、9/30 已有有效收盤資料，但本次沒有重做那段所有資料集的完整性認證。
2. 為 production volume 建立持續的一致性 SQLite 備份與還原演練；目前只驗證了首次匯入與重啟保存，持久磁碟本身不等於備份。
3. 帳號模式已完成下節的遷移、部署與 HTTPS 驗收。後續由管理員邀請朋友使用各自帳號，新環境操作與復原見 [ACCOUNTS.md](ACCOUNTS.md)。

## 注意事項

- 2026-10-14 起正式版本採個別帳號隔離；原 Basic 帳密只透過 operator 轉入主要管理員，沒有公開 bootstrap。舊紀錄中的 Basic 驗證不代表現行登入方式。
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
