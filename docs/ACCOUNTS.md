# 邀請制帳號與資料隔離

2026-10-14：日常與 Railway 資料庫均已備份並遷移至 v7，新帳號模式已部署，原管理員帳密保留。正式 HTTPS 登入、CSRF 及登出已驗證，詳見 [部署紀錄](railway-deployment.md)。兩份資料庫與後續密碼變更仍各自獨立，不自動同步。

## 使用方式

- `/login.html`：登入；忘記密碼時聯絡管理員取得重設連結。
- `/account.html`：登出、修改密碼；管理員另外可建立邀請、重發／撤銷連結、停用／重新啟用成員。
- 邀請由管理員先指定帳號與顯示名稱，再私下傳送連結。新成員自行設定密碼；沒有公開註冊、郵件寄送或自助信箱驗證。
- 邀請有效 48 小時、重設有效 30 分鐘，皆只能使用一次；重發取代同種類舊連結，撤銷或停用會作廢連結。連結只在產生時顯示，不寫入日誌。
- 登出撤銷目前 session；修改／重設密碼、停用帳號會撤銷該帳號所有 sessions。Session 最長 24 小時，閒置 120 分鐘到期。
- 管理員不能查看密碼；管理頁不列出朋友的私人資料。主機與資料庫持有人仍可從底層讀取，這是應用層存取隔離，並非對主機擁有人的端對端加密。

## 權限與資料範圍

| 範圍 | 管理員 | 成員 |
|---|---|---|
| 共用行情／財報／交易日曆／benchmark／策略模板 | 可讀 | 可讀 |
| 本人的觀察名單、人工評分、筆記、交易計畫／紀錄／持股、Planner | 可管理 | 可管理 |
| 本人的策略／版本／Backtest／Optimization／Walk Forward／Portfolio／研究結果 | 可管理 | 可管理 |
| 他人的私人資料、跨帳號引用／複製／取消／續跑 | 不開放 | 不開放 |
| 帳號邀請、撤銷連結、停用／重啟成員 | 可操作 | 不可操作 |
| `/api/admin/`、ETF 歷史補齊、強制個股重抓、API docs | 可操作 | 不可操作 |
| `/api/market/freshness` | 可讀 | 可讀（不含 runner logs、errors、設定） |

一般瀏覽仍沿用按需抓取與共用快取。強制個股更新使用 `POST /api/stocks/{stock_id}/deep`；GET 的 `force=true` 不再執行強制更新。帳號模式每人最多一個排隊／執行中的頂層研究，全站一個 FIFO worker；Walk Forward 的內部訓練在同一 worker 執行。重啟仍依原契約把中斷研究標成 failed，使用原 pinned definition 手動續跑，不自動重播。

## 架構與安全界線

- `app/identity.py`：middleware 與 worker 維護的 ContextVar 身分；不接受 payload 的 user/owner/role。缺少身分就拒絕私人存取。
- `app/services/accounts.py`：Argon2id 密碼雜湊、不透明 session、CSRF、邀請／reset、節流與帳號稽核記錄。DB 只保存 session／邀請／reset token 的 SHA-256；密碼使用成熟 Argon2 函式庫。稽核只記 action、actor/subject ids 與時間，不記密碼或連結。
- `app/access.py:AccountAccessMiddleware`：每次查 session 與 active 狀態；管理路徑需 admin；寫入需 session 綁定的 CSRF header 及同來源檢查。Cookie 使用 HttpOnly（session）、Secure、SameSite=Lax，回應 no-store。公開內容僅 health、登入／設定密碼所需最小靜態檔，以及 login/complete POST；私人資料無匿名入口。
- `app/db/tables.py`：12 個私人根表有 `owner_user_id`；12 個子表沿既有 parent id 繼承歸屬。`watchlist` / `scores` 的 PK 包含使用者，允許各自記錄同一檔股票。schema migrations v7：舊資料明確歸管理員保留 id=1，不修改研究定義、version id、pin、fills 或結果。
- `app/db/ownership.py`：`query_all/query_one` 以固定 CTE 遮蔽私人根表及子表，按可信身分限定查詢；join、子查詢、列表與快取都使用同一界線。不是用 regex 改寫 WHERE。新私人 SELECT 必須使用這些 helpers，不能用 schema-qualified `main.<private_table>` 或直接 Core/driver SELECT 繞過。regex 只用於辨識是否涉及私人表／拒絕 schema qualification，CTE 名稱與關聯只來自 tables.py，身分以參數綁定。
- SQLite triggers 對私人根／子表的 INSERT/UPDATE/DELETE 再檢查 actor 與原有 owner，包含 raw SQL 寫入。純計算模組不讀 DB／不接收認證邏輯；計算公式、PIT、IS/OOS、現金、費率與研究契約版本不變。
- `system_scope()` 僅用於本機 bootstrap／migration、啟動 recovery，以及維護共用資料時讀取全體 watchlist 以保留／補齊共用快取；不提供 HTTP system scope。
- 密碼錯誤與不存在帳號使用同一訊息。登入依來源與帳號節流；token 使用也節流，計數持久化，重啟不清除限制。未設定任意來源信任；代理 IP 轉送需依實際部署配置驗證。

## 本機初始化與復原

執行前停止舊伺服器與所有 writer，核對 `STOCKS_DB_URL` / `STOCKS_DATA_DIR` 指向正確資料庫。既有檔案會先以 SQLite backup API 備份到該 data 目錄的 `backups/`，通過 integrity_check 後才執行 migration。v7 只重建可變的 watchlist/scores 以改 PK；研究表不重建、不改寫不可變 payload。

```bash
uv sync --frozen
uv run python scripts/accounts.py init --username owner
```

`owner` 是帳號名稱範例，可換成自己的英數帳號。密碼以終端機隱藏輸入兩次，不能放在 CLI 參數、Git 或聊天。此指令只可初始化一次；主要管理員固定 id=1，朋友的啟用流程永遠不能接手舊資料。

若要保留已設定在可信行程環境中的原 Basic 帳密，改用一次性的 operator 指令：

```bash
uv run python scripts/accounts.py init --from-basic-env
```

它直接讀取 `STOCKS_AUTH_USERNAME` / `STOCKS_AUTH_PASSWORD`，不顯示其值、不接受 HTTP 呼叫，也不覆蓋已初始化的帳號。舊密碼可保留原長度（上限 128）；轉入後修改密碼與新邀請仍要求 12–128 字元。不要為此把雲端 secrets 寫入本機 `.env` 或 Git。

新帳號模式預設開啟，沒有未登入本機捷徑。只在本機 HTTP 開發時設定 `STOCKS_COOKIE_SECURE=0`，再啟動：

```bash
STOCKS_COOKIE_SECURE=0 uv run uvicorn app.main:app --port 8787
```

HTTPS／Railway 的 Cookie 維持 Secure；Railway 不能透過該變數關閉 Secure。管理員忘記密碼時，在可信本機／容器終端執行：

```bash
uv run python scripts/accounts.py recover
```

此操作同樣先備份；輸出 30 分鐘單次 reset 路徑，請直接在自己的瀏覽器使用，不放入文件、日誌或分享連結。它不是網頁端的公開 bootstrap／復原入口。

`STOCKS_AUTH_MODE=basic` 僅為明確選用的舊版單人相容／既有測試模式，仍使用原 HTTP Basic 規則，沒有多帳號隔離保證。帳號模式失敗時不會退回 basic；不可把 basic 當成朋友共用方案。

## Railway 上線／新環境遷移順序（本次已完成，重用時先核對現況）

1. 準備並確認備份與回復方式，安排停止寫入的維護時段。保留舊版部署資訊，實際完成證據另記於部署紀錄，此節是操作順序。
2. 新映像包含 `scripts/accounts.py` 與 `scripts/backup_sqlite.py`。需要可運行容器進行首次遷移時，使用既有 `STOCKS_IMPORT_MODE=1` health-only 程序；它不啟動 app 或 jobs，`status: importing` 不代表帳號系統就緒。
3. 在可信容器終端，確認 `/data` 的既有 DB 路徑，執行上述 init 指令。新密碼由擁有人直接輸入；保留原 Basic 帳密可用 `init --from-basic-env` 直接轉入既有環境設定。不要把 secrets 輸出或寫入一般啟動指令。
4. 設 `STOCKS_AUTH_MODE=accounts`，關閉 import mode，保留既有磁碟、單 worker、Taiwan TZ 與排程設定，再啟動正常 app。啟動前檢查 migration v7 及已初始化的主要管理員；未就緒會 fail closed。
5. 驗證 HTTPS 登入、logout、admin/member 界線、A/B 隔離及資料完整性，再建立真實朋友邀請。不要只以 health 200 宣稱完成。

回復舊程式時不能忽略 v7 的 watchlist/scores 複合 PK；使用遷移前一致備份及相容程式整體復原，不能直接以舊版程式繼續寫入新 schema。

## 驗證

`tests/test_accounts.py` 使用臨時 SQLite，涵蓋帳號生命週期、sessions/CSRF、跨帳號 24 表查詢、資料庫寫入防護、研究結果／取消／續跑／Planner pin、快取隔離與 worker 身分。既有研究／Portfolio 測試仍須完整通過。

完整既有套件需先建隔離 DB；需要真實 fixtures 時用 backup API 建立副本，並在同一份副本上初始化 schema，再執行：

```bash
STOCKS_AUTH_MODE=basic uv run python -m unittest discover -s tests -p 'test_*.py'
uv run python -m compileall app tests scripts
```

執行完整套件前務必透過環境變數將 `STOCKS_DB_URL` / `STOCKS_DATA_DIR` 指向隔離副本；basic 是既有單人服務測試的明確相容模式，新帳號測試自行切換 accounts 並驗證 fail-closed。不要在日常或正式 DB 上直接跑研究整合測試。

### 2026-10-14 本次驗證範圍

- 一致性備份副本上完整套件 **467 項通過**，其中新帳號測試 25 項；`compileall`、變更的 JS 語法及 `git diff --check` 通過。
- 另在全新臨時 DB 執行 operator CLI 的 init 與 recover，驗證 recovery 前產生一致備份並輸出 reset 路徑；測試 token 未收錄於文件。新路由、script 路徑與現行文件相對連結均已核對。
- v6 → v7 前後逐一比對 24 張私人表的原欄位／列值：研究內容、版本、pins、結果及 ids 保留。其後已依下節完成日常與雲端的實際遷移，並分別再次比對。
- 另在真實行情隔離副本以 accounts 模式啟動 lifespan，由新成員登入、建立策略、透過 FIFO worker 完成 2023 半年單股回測（113 筆 equity rows）。本人結果 200、管理員跨帳號結果 404、成員管理入口 403；這是功能驗證，不是策略成效證據。
- Disposable DB 上檢視登入／帳號頁桌面與 390px 手機、實際登入及建立測試邀請；未向任何朋友傳送。獨立介面審查發現的導覽位置已修正；修正後 source verdict 為 resolved，IAB 無法重開，故未重拍該項。截圖僅為工具 inline 證據，沒有可保存的 screenshot 檔案。
- 一次設計 detector 對新帳號頁／樣式／JS 無 findings；舊儀表板／個股／Planner 的既有排版及破折號提示未在此次重設計。設計文件核對確認沿用既有 tokens，無新視覺系統。
- 初次實作驗證時未部署；其後的實際遷移與 HTTPS 驗證見下節。未測真實朋友邀請交付、正式復原演練或代理來源 IP 完整信任鏈。

### 2026-10-14 實際遷移與部署

- 使用者另行授權保留原帳號、遷移與部署。新增 operator-only `init --from-basic-env`，直接將既有環境設定雜湊轉入；密碼未列印、未寫到本機 `.env` 或 Git。主要管理員固定 id=1，沒有建立真實朋友帳號。
- 完整隔離副本套件 **468 項通過**（帳號測試 26 項），含舊帳密轉入、只能初始化一次、缺少 operator 身分拒絕、後續新密碼仍遵守長度規則；compileall 與 diff 檢查通過。
- 本機與雲端各自先以 backup API 備份，再遷移為 v7；24 張私人表排除新增 owner metadata 的原欄位內容，分別逐表指紋一致。沒有用本機 DB 覆蓋雲端或反向覆蓋。雲端遷移前備份另下載到本機，解壓後大小、SHA-256 與 integrity_check 均吻合。
- 本機 `http://127.0.0.1:8787` 已啟動，原帳密登入／admin／私人讀取／CSRF／logout 通過。本機 HTTP 只對 loopback，使用 `STOCKS_COOKIE_SECURE=0`；正式 HTTPS 保持 Secure。
- Railway 先用 health-only 維護版停止 app/jobs；遷移前確認四類研究均沒有 queued/running。遷移驗收後設 `STOCKS_AUTH_MODE=accounts`、`STOCKS_IMPORT_MODE=0`，正式部署 `17331cbf` 為 SUCCESS；原單 worker、磁碟與排程設定保留。
- 正式 HTTPS 實測：health 200；匿名 me 401、account page 303；login page 200；原帳密登入 200、owner id=1/admin；account/admin accounts/Planner/持股/策略/Portfolio 讀取 200；缺 CSRF 的 logout 403、正確 CSRF logout 200、舊 session 再讀 me 401。Cookie 有 Secure／HttpOnly／SameSite=Lax。TLS 正常驗證，未停用憑證檢查。
- 朋友權限與 A/B 隔離仍以隔離 DB 的自動化／真實行情測試驗證；正式環境只驗證原管理員，不以測試朋友帳號污染正式 users。真實邀請由管理員日後在帳號頁產生。
