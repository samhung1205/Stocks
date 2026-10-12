# Deployment Profile

> 2026-10-12：已部署前端介面更新（commit `b0584f0`），健康檢查通過，登入後畫面尚待使用者確認；詳見 [Railway 部署紀錄](docs/railway-deployment.md)。
>
> 2026-10-06 部署進度：Railway Hobby 的 Stocks 已部署至 [正式網址](https://stocks-production-eeb3.up.railway.app)。資料庫一致性與重啟保存、台北時區排程、HTTPS 及未登入保護均已驗證；Compute 硬上限 US$10、提醒 US$8 已儲存。使用者已確認登入成功且使用正常。10/5 部署驗證時官方資料更新回報 partial（9/29、9/30 缺漏），本次未重新核對資料更新狀態，詳見 [Railway 部署紀錄](docs/railway-deployment.md)。下方為 2026-09-27 的原始部署評估與當時的待辦清單，不代表現行未完成事項；最新進度以部署紀錄為準。新 Railway 服務使用 IaC。

產生日期：2026-09-27。使用者提供的平台資料原核對於 2026-09-07；以下限制已於 2026-09-27 再查官方文件。價格與限制可能變動，實際申請前請重新核對文末連結。

## Project profile

- **部署服務數量**：一個持續運作的 Python Web 服務。[`app/main.py`](app/main.py) 以 FastAPI 同時提供 [`web/`](web/index.html) 靜態頁面與 [`app/api/routes.py`](app/api/routes.py) 的 API；[`web/js/common.js`](web/js/common.js) 呼叫同站 API。排程也在同一個服務程序內。
- **需要伺服器運算**：需要。[`app/api/routes.py`](app/api/routes.py) 提供資料維護、財務規劃及策略研究；[`app/config.py`](app/config.py) 與 [`app/db/__init__.py`](app/db/__init__.py) 使用可寫入的本機 SQLite。現行 `data/stocks.db` 為 **635,662,336 bytes，約 636 MB**；WAL 暫存寫入檔另占空間且可能增長。資料庫必須放在重新部署後仍保留資料的「持久磁碟」。
- **長時間工作**：[`app/api/routes.py`](app/api/routes.py) 會在服務程序內啟動回填、最佳化及 Walk Forward 等背景工作。本機實測單次最佳化 **194–540 秒**（[`docs/history/stage-e1-real-grid-validation.md`](docs/history/stage-e1-real-grid-validation.md)）、Walk Forward **649–2,112 秒**（[`docs/history/stage-f1-5-real-validation-report.md`](docs/history/stage-f1-5-real-validation-report.md)）；完整歷史回填可能超過 **3.5 小時**（[`docs/history/stage-a-plus-report.md`](docs/history/stage-a-plus-report.md)）。雲端速度未實測。[`app/main.py`](app/main.py) 啟動時也會執行補資料工作。
- **排程**：使用者目前需要自動更新盤後資料，未來可能加入自動盤中更新。[`app/scheduler.py`](app/scheduler.py) 在平日盤後的限定時段重複檢查、每月 10–12 日更新營收、每週清理；[`app/services/data_freshness.py`](app/services/data_freshness.py) 在服務重啟後補跑。**目前是盤後資料自動更新，尚非盤中自動蒐集**（[`README.md`](README.md)、[`app/services/data_freshness.py`](app/services/data_freshness.py)）。排程在 Web 程序內，程序休眠時不會準點執行。另須注意：[`app/scheduler.py`](app/scheduler.py) 目前只取 `AUTO_REFRESH_CHECK_TIME` 的「小時」；文件寫 15:05、間隔 15 分鐘時，實際檢查從 15:00、15:15……開始。
- **預期使用量**：主要一人使用，未來最多增加一至兩位朋友（使用者提供）。
- **本機資料清單**：整個 `data/` 目錄約 **2.7 GB**，其中包含多份 `stocks.pre-stage-*.db` 舊備份；`.gitignore` 排除了 `data/`，因此只部署程式碼不會帶上現行資料庫。正式磁碟只需匯入現行資料庫的一份一致備份，舊備份保留在其他位置。現行 SQLite 的 `freelist_count` 為 0；主要占用來自行情、研究再平衡結果與估值資料及索引，不能期待單靠 `VACUUM` 就縮到 Railway Free 的 0.5 GB。
- **程式讀取的環境變數名稱**：`STOCKS_DATA_DIR`、`STOCKS_DB_URL`、`FINMIND_TOKEN`、`FUGLE_API_KEY`、`ENABLE_SCHEDULER`、`AUTO_REFRESH_ENABLED`、`AUTO_REFRESH_TIMEZONE`、`AUTO_REFRESH_CHECK_TIME`、`AUTO_REFRESH_POLL_INTERVAL_MINUTES`、`AUTO_REFRESH_MAX_RETRIES`、`AUTO_REFRESH_RETRY_WINDOW_MINUTES`、`AUTO_REFRESH_STATUS_CACHE_SECONDS`、`FEE_RATE`、`FEE_DISCOUNT`、`TAX_RATE`、`STOCKS_MIN_AVG_AMOUNT`、`FINMIND_INTERVAL`（[`app/config.py`](app/config.py)）；`STOCKS_FINANCIAL_DEADLINES`、`STOCKS_PUBLICATION_OVERRIDES`（[`app/publication.py`](app/publication.py)）。此處只列名稱，未讀取或寫入秘密值。

## Recommendation

**首選：Railway Hobby，一個持續運作的 Web 服務加一個持久磁碟。** 它可執行現有 Python/FastAPI、程序內排程與長時間研究工作。官方列 Hobby 持久儲存上限 **5 GB**；以現行 636 MB 資料庫估算，尚有約 **4.36 GB**，但須為 WAL 與未來成長留空間。Railway Free 僅 **0.5 GB**，現行資料庫已超過。將磁碟掛載在資料庫目錄，並確認 `STOCKS_DATA_DIR`、`STOCKS_DB_URL` 指到該磁碟；維持單一服務程序與副本。**不要開啟 Railway Serverless 休眠**來依賴 [`app/scheduler.py`](app/scheduler.py) 準點更新；睡著的程序無法觸發自己的排程。Hobby 是每月 **US$5 起**，用量超過內含額度仍可能收費，應先在 Workspace Usage 設 Compute Usage 費用硬上限。[Railway 價格](https://railway.com/pricing)、[持久磁碟](https://docs.railway.com/volumes)、[費用控制](https://docs.railway.com/pricing/cost-control)、[休眠機制](https://docs.railway.com/deployments/serverless)。

**備選：AWS Lightsail 2 GB RAM／60 GB SSD 的 Linux 主機，官網標價每月 US$12。** 它能保留 SQLite，讓 [`app/scheduler.py`](app/scheduler.py) 持續運作，磁碟餘量也較大；但作業系統更新、部署、HTTPS、監控和備份須自己維護。最大型的 [`app/services/optimization_lab.py`](app/services/optimization_lab.py) 與 [`app/services/walkforward_lab.py`](app/services/walkforward_lab.py) 工作是否可在 2 GB RAM 內完成，尚未實測。[Lightsail 價格](https://aws.amazon.com/lightsail/pricing/)。

**何時改選**：若現行資料庫加 WAL 接近 Railway Hobby 的 5 GB 上限，或您更重視固定月費且願意自行維護主機，重新評估 Lightsail。若未來要多個伺服器副本共享寫入資料，須先改造 [`app/db/__init__.py`](app/db/__init__.py) 的 SQLite 專屬資料層；更換平台本身不會解決。自動盤中蒐集也尚須另行實作（[`app/services/data_freshness.py`](app/services/data_freshness.py)）。

## Collision forecast

以下判定以**目前程式原樣**為準；「受阻」代表需改變部署架構或程式才可正確運作。

- **Railway**：**Hobby：可用，但有條件；Free：受阻。** Free 持久磁碟 **0.5 GB**，裝不下 [`app/config.py`](app/config.py)、[`app/db/__init__.py`](app/db/__init__.py) 使用的 636 MB SQLite。Hobby 可用到 5 GB；容量滿時資料庫將無法寫入。若開休眠，[`app/scheduler.py`](app/scheduler.py) 的排程會錯過時間。[價格](https://railway.com/pricing)、[磁碟](https://docs.railway.com/volumes)、[休眠](https://docs.railway.com/deployments/serverless)。
- **AWS Lightsail**：**2 GB／60 GB 方案：可用，但有條件。** [`app/api/routes.py`](app/api/routes.py) 的背景工作不受函式執行時間上限限制；仍須實測記憶體、管理主機並備份。磁碟滿時 SQLite 無法寫入。[價格](https://aws.amazon.com/lightsail/pricing/)。
- **Render**：**Free：受阻；付費服務加持久磁碟：可用，但有條件。** Free 閒置休眠或重新部署後會失去 [`app/db/__init__.py`](app/db/__init__.py) 的本機 SQLite 變更。付費磁碟可保留資料，但每月 US$7 的入門運算方案只有 512 MB RAM，研究工作是否放得下尚未驗證。[Free 限制](https://render.com/docs/free)、[磁碟](https://render.com/docs/disks)、[價格](https://render.com/pricing)。
- **Vercel**：**受阻。** 函式檔案系統唯讀、暫存空間至多 **500 MB**，小於現有 [`app/config.py`](app/config.py) 指向的 636 MB SQLite。即使重做資料庫，Hobby 函式 **300 秒**上限仍小於 [`app/services/optimization_lab.py`](app/services/optimization_lab.py) 已實測的 540 秒；若把它改成單次函式請求，超時會見到 HTTP 504／`FUNCTION_INVOCATION_TIMEOUT`。[執行環境](https://vercel.com/docs/functions/runtimes)、[限制](https://vercel.com/docs/functions/limitations)。
- **Netlify**：**受阻。** 現有 [`app/main.py`](app/main.py) 是持續運作的 Python/FastAPI 服務，不能直接當作現有 Netlify 函式部署；即使重寫，現行同步函式上限 **60 秒**、背景函式 **15 分鐘**，都小於 [`app/services/walkforward_lab.py`](app/services/walkforward_lab.py) 已實測的 2,112 秒。背景函式只會先回 HTTP 202，結果還要另外查詢。[函式限制](https://docs.netlify.com/build/functions/configuration/)、[背景函式](https://docs.netlify.com/build/functions/background-functions/)。
- **Cloudflare Workers／Pages**：**受阻。** Workers 的 JavaScript／WebAssembly 環境不能原樣執行 [`app/main.py`](app/main.py) 的 Python/FastAPI；虛擬檔案系統也不能持久保存 [`app/db/__init__.py`](app/db/__init__.py) 的 SQLite。Free 每次僅 **10 毫秒 CPU**、每個執行環境 **128 MB RAM**；若改寫後超限，可能見到 Error 1102。[限制](https://developers.cloudflare.com/workers/platform/limits/)、[虛擬檔案系統](https://developers.cloudflare.com/workers/runtime-apis/nodejs/fs/)。

**最先撞到的牆**：[`app/config.py`](app/config.py)／[`app/db/__init__.py`](app/db/__init__.py) 必須讀寫約 636 MB SQLite，現在已超過 Railway Free 的 0.5 GB 持久磁碟及 Vercel 的 500 MB 暫存空間。改用 Railway Hobby 後，第一個營運陷阱是啟用休眠會讓 [`app/scheduler.py`](app/scheduler.py) 錯過定時更新。若不休眠，下一個容量上限為 5 GB；目前只有單次容量觀測，無法誠實推算幾個月後才會滿。Vercel 與 Netlify 另有上述長時間研究工作超時問題。

## Pre-launch checklist（2026-09-27 歷史評估）

- [ ] 為現行 SQLite 建立持久磁碟，確認掛載路徑，僅啟動一個應用程序／副本（[`app/config.py`](app/config.py)、[`app/db/__init__.py`](app/db/__init__.py)、[`app/main.py`](app/main.py)）；留足 WAL 與成長空間。
- [ ] 匯入 `data/stocks.db` 時，依 [`AGENTS.md`](AGENTS.md) 使用 **SQLite backup API** 製作一致備份；WAL 模式下不要只複製正在寫入的 `.db` 檔。舊 `data/stocks.pre-stage-*.db` 備份另存，不佔正式磁碟。驗證重啟後資料仍在，並安排持續備份。
- [ ] 在平台設定中確認環境變數**名稱**：`STOCKS_DATA_DIR`、`STOCKS_DB_URL`、`ENABLE_SCHEDULER`、`AUTO_REFRESH_ENABLED`、`AUTO_REFRESH_TIMEZONE`、`AUTO_REFRESH_CHECK_TIME`、`AUTO_REFRESH_POLL_INTERVAL_MINUTES`、`AUTO_REFRESH_MAX_RETRIES`、`AUTO_REFRESH_RETRY_WINDOW_MINUTES`、`AUTO_REFRESH_STATUS_CACHE_SECONDS`；使用對應資料源時再設定 `FINMIND_TOKEN`、`FUGLE_API_KEY`（[`app/config.py`](app/config.py)）。實際值與秘密只放平台設定，不寫進此檔或 Git。
- [ ] 啟動命令須讓 Uvicorn 綁定平台提供的主機介面與連接埠；[`app/main.py`](app/main.py) 的範例只供本機使用。驗證服務啟動、[`app/api/routes.py`](app/api/routes.py) 的 `/api/admin/data-freshness`、一次排程更新及重啟後資料仍在。
- [ ] 若需要精確從 15:05 開始檢查，先修正 [`app/scheduler.py`](app/scheduler.py) 忽略 `AUTO_REFRESH_CHECK_TIME` 分鐘部分的問題。
- [ ] 公開網址前加上私人存取保護。[`app/api/routes.py`](app/api/routes.py) 有可寫入的管理、日誌、財務與研究 API；該路由模組目前未見登入驗證相依項。
- [ ] **超過 400 KB 的網頁靜態檔**：[`web/vendor/echarts.min.js`](web/vendor/echarts.min.js) **1,030,855 bytes**，由 [`app/main.py`](app/main.py) 的靜態檔服務提供。留意重複下載與流量；400 KB 是檢查門檻，不是 Railway 硬限制。
- [ ] **免費額度餘裕**：以目前程式及資料為 **零**。Railway Free 容量不足；Render Free 無法持久保存 SQLite；Vercel、Netlify、Cloudflare 需要架構變更。預計 1–3 人的流量不是最先遇到的限制，但尚無每月流量或資料庫成長測量，不能換算成可用月份。
- [ ] **Railway Hobby 超額行為**：超出內含 US$5 用量會繼續計費。在 Workspace Usage 設 Compute Usage 提醒與硬上限；碰到硬上限時工作負載會停下，包括排程。官方目前列的最低硬上限為 **US$10**。[費用控制](https://docs.railway.com/pricing/cost-control)、[用量指南](https://docs.railway.com/guides/right-size-cpu-memory)。Vercel Hobby、Cloudflare Free、Netlify Free 的免費額度到頂屬停用／暫停型；Render 有付款方式時可能續計費，Lightsail 超額傳輸會計費。請核對各平台官方方案。
- [ ] 用代表性的 [`app/services/optimization_lab.py`](app/services/optimization_lab.py) 或 [`app/services/walkforward_lab.py`](app/services/walkforward_lab.py) 工作量測記憶體，並持續監控資料庫與 WAL 占用。不要為了塞進免費方案任意刪除歷史研究結果；[`AGENTS.md`](AGENTS.md) 要求研究結果可重現。

## Sources

- Railway：[價格](https://railway.com/pricing)、[持久磁碟](https://docs.railway.com/volumes)、[費用控制](https://docs.railway.com/pricing/cost-control)、[休眠](https://docs.railway.com/deployments/serverless)、[用量指南](https://docs.railway.com/guides/right-size-cpu-memory)。
- AWS Lightsail：[價格](https://aws.amazon.com/lightsail/pricing/)。
- Render：[價格](https://render.com/pricing)、[Free 限制](https://render.com/docs/free)、[持久磁碟](https://render.com/docs/disks)。
- Vercel：[價格](https://vercel.com/pricing)、[執行環境](https://vercel.com/docs/functions/runtimes)、[函式限制](https://vercel.com/docs/functions/limitations)。
- Netlify：[價格](https://www.netlify.com/pricing/)、[函式限制](https://docs.netlify.com/build/functions/configuration/)、[背景函式](https://docs.netlify.com/build/functions/background-functions/)。
- Cloudflare：[Workers 價格](https://developers.cloudflare.com/workers/platform/pricing/)、[限制](https://developers.cloudflare.com/workers/platform/limits/)、[虛擬檔案系統](https://developers.cloudflare.com/workers/runtime-apis/nodejs/fs/)。
