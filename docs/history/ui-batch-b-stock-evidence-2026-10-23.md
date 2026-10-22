# 第二批 B：個股錯誤恢復與等價證據

日期：2026-10-23（Asia/Taipei）。依使用者已選定的 A → B → C 分批交付，使用 Impeccable harden／Operate 指引，保留既有研究卷宗樣式。本批完成 [UI 改善清單](../UI_IMPROVEMENTS.md) 的全部五項 B 待辦；C 與其餘項目仍未完成。原始設計評估分數不倒寫為改善後分數。

## 行為與範圍

- 無股票代號、無效代號或 overview 載入失敗時，結束載入提示、收起相依研究／價格與日誌入口、停用觀察名單及抓取動作，提供重新載入、聚焦全站搜尋與返回市場概況。主資料成功後才載入相依面向。
- 六個圖表面向皆有可讀取的期間／日期摘要和數值表。K 線表含原始開高低收、成交量與四條均線；獲利含四種利潤／報酬率及 EPS；財務含現金流、淨利、自由現金流、存貨、應收與負債比；估值含 PE／PB／殖利率；籌碼含法人、區間累計與融資券。保留月營收尺度與極值揭露。
- 圖表與數值使用相同載入 rows，表格標示日期、單位、筆數及缺值，位於具名、可鍵盤捲動的區域。圖形不再是唯一證據；ECharts 未載入時仍可查閱摘要及表格。
- 六組 `tab`／`tabpanel` 互相關聯，以 roving tabindex、aria-selected 和 hidden 管理選取，左右方向鍵循環、Home／End 到首尾。每個面向獨立管理 loading／error／retry，技術摘要單獨失敗也保留量價證據。
- 五個治理連結將查核項目放進連結名稱，箭頭為裝飾，並標明另開視窗。
- 稀疏 OHLC：沒有可觀測高／低時，技術服務回傳 null，不再使用 0／巨大哨兵值或對空集合求 min。K 棒缺開／高／低時不繪製該筆，收盤及可計算均線保留；未知均線位置不顯示為跌破。只有融資券、沒有法人資料時仍呈現融資券證據；法人累計遇缺值停止累計，不補零。
- 估值說明改為現有歷史樣本及實際圖表期間，不保證已累積五年。近年高低亦明示可用觀測筆數，不以短樣本宣稱完整年度。
- 320px／200% 文字測試發現共用導覽群組缺少換行，已以窄視窗 flex-wrap 修正。

主要來源：[stock.html](../../web/stock.html)、[stock.js](../../web/js/stock.js)、[app.css](../../web/css/app.css)、[stock_view.py](../../app/services/stock_view.py)。原有 inline JS 移入 stock.js；無前端框架、schema migration、新端點或外部服務。AGENTS、架構與使用手冊同步更新 nullable technical 回應及 UI 邊界。未更改研究／Portfolio 計算契約。

## 驗證

使用真正 FastAPI routes／帳號 middleware 與獨立暫存 SQLite，合成四種資料：完整公司、僅收盤公司、僅融資券公司、空白公司。預覽程序停用 deep_fetch 與排程；未連接外部市場來源，未讀寫日常 DB，也未部署 Railway。HTTP 503 和圖表工具失效由瀏覽器攔截注入，其餘資料經實際 API 讀取。

**38 項後端測試通過**：

- `tests/test_stock_view.py`：4 項，真 SQLite 驗證全無收盤、僅收盤、部分高低與完整均線／高低回歸。
- `tests/test_journal.py`：7 項，確認共用技術服務不破壞日誌助手。
- `tests/test_accounts.py`：27 項，確認帳號／私人資料邊界回歸。

以上各以 `STOCKS_AUTH_MODE=basic`、`STOCKS_DB_URL`／`STOCKS_DATA_DIR` 指向 `/tmp/` 隔離位置執行 `.venv/bin/python -m unittest discover -s tests -p '<對應檔名>'`；測試自身再建立暫存 fixture。變更 Python 經 compileall，stock.js 經 `node --check`，`git diff --check` 通過。

**43 項瀏覽器檢查通過**：六個面向的摘要／原始筆數、日期／現金流／累計值、具名治理連結、選取與方向鍵、Home／End、1280／768／375／320px、320px 文字 200%、營收極值仍可查看、無效／缺少代號、主 API 恢復、單一面向與技術摘要重試、稀疏 OHLC、融資券獨立資料、全空白及 ECharts 未載入。無 JavaScript runtime error；刻意注入的 HTTP／asset failures 不作為未預期程式錯誤。

依 Impeccable 限定兩輪：首輪桌面／手機與邊界情境合併檢視，修正導覽換行等項目後做一次完整確認，未繼續打磨。靜態 detector 對 stock.html／stock.js 回傳 `[]`（套用現有專案設定）；不等同實機螢幕閱讀器稽核或完整無障礙認證。

本機證據（output 依專案規則忽略版控）：

- [43 項驗證結果](../../output/playwright/stock-evidence/validation.json)
- [桌面數值入口](../../output/playwright/stock-evidence/desktop-values.png)
- [手機數值入口](../../output/playwright/stock-evidence/mobile-values.png)
- [無效代號恢復畫面](../../output/playwright/stock-evidence/invalid-stock.png)
- [靜態 detector](../../output/playwright/stock-evidence/detector.json)

下一批 C：Planner 情境與策略建立／編輯的未儲存狀態、切換與離頁保護；私人草稿持久化仍須明確的帳號隔離／保存政策，不默認共用 browser storage。
