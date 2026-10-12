# Final Product Design Stage K0 — Full-site UX Audit + Visual Identity Direction

日期：2026-09-23。範圍：設計審查與 K1 方向；本階段未修改 UI、API、資料模型或研究計算。

本次先讀 `PRODUCT.md`、`AGENTS.md`、`docs/ARCHITECTURE.md`、`docs/PROJECT_STATUS.md`、`docs/history/stage-j-final-system-audit-report.md`，再於本機網站實看 Dashboard、2330 Stock Detail 與月營收頁籤、Screener 預設條件、Watchlist、Journal、Strategy Lab 回測結果、Optimization 列表與結果、Walk Forward 建立表單與 independent/stitched 結果、Strategy Portfolio 六步驟建立流程與研究/持股頁籤，以及 Financial Planner 七步驟與 Portfolio 證據選取。審查方法參照已安裝的 `ui-ux-pro-max`（系統方向、表格與無障礙準則）和 `impeccable`（產品辨識度、資訊層級與實作偵測）。桌面畫面已實看；本次瀏覽器無法切換窄 viewport，手機判斷依目前響應式 CSS 與 Stage J 紀錄，K1 必須做 375/768px 實測。

## 1. Current UX diagnosis

目前的強項是研究誠實性：來源、版本、樣本內外、現金語意、資料限制都出現在決策附近，Portfolio/Planner 的證據分界尤其清楚。主要問題不是缺少功能，而是全站缺少與「個人研究工作桌」相符的編輯秩序。

| 優先 | 全站問題與實看證據 | 影響 |
|---|---|---|
| **Must change** | 頂部導覽把七個入口等權平鋪；Strategy Lab、Optimization、Walk Forward、Portfolios 實際是一條研究鏈，卻以獨立英文/中文入口與返回連結拼接。 | 使用者必須記住研究順序與各結果的上下游關係。 |
| **Must change** | `web/css/app.css` 的 14px 基本文字、10–12px 輔助文字、相近權重的卡片/框線遍布頁面；Optimization 和 Walk Forward 的長報告以連續同形區塊呈現。 | 標題、結論、限制、來源與操作同時爭奪注意，長時間研究易疲勞。 |
| **Must change** | 共用主按鈕使用白字 `#fff`／藍底 `#4f8ef7`，對比約 3.2:1；不少純文字 label 未以 `for` 綁定輸入欄，Screener 表頭用可點的 `<th>` 排序。 | 小字控制項可讀性及鍵盤/輔助科技操作需要修正。 |
| **Must change** | Journal 無成交時仍以四張績效卡開場；Watchlist 空分級保留完整表頭；Planner 在結果未就緒時仍顯示十個空結果區塊。 | 空狀態像載入失敗，且把真正下一步推到畫面下方。 |
| **Should change** | Dark panel、12px 圓角卡片、膠囊 badge、藍色 CTA、左側粗色條反覆出現；`impeccable detect` 對共用 CSS 找到 16 處側邊色條訊號。 | 可用，但視覺語法與一般 SaaS/AI dashboard 可互換，缺少產品個性。偵測數量含共用樣式重複引用，不代表 16 個獨立缺陷。 |
| **Should change** | Dashboard 把過期資料提醒、四項 KPI、走勢與資料維護置於同一視覺強度；Stock Detail 的 K 線很大，右側判讀與人工評分擠在窄欄。 | 「現在知道什麼／還需判斷什麼」沒有先於資料量被看見。 |
| **Should change** | Optimization 紀錄長表缺乏縮小範圍的入口；Portfolio 有 3 個 Active、671 個 Archived，Planner 的原生 Portfolio 下拉一次列出約 675 項（包含大量封存測試項）。 | 找到可用的研究證據比閱讀它更費力。 |
| **Should change** | Portfolio 結果與 Planner 來源表單有長警告與原始技術字串；若都使用同一種色條通知，`info`、`caution`、`critical` 的語意難以快速掃讀。 | 使用者可讀到揭露，但不易判斷哪一條會改變決策。 |
| **Should change** | 圖表已有標題、單位及數值 fallback，但 Stock 月營收圖受極端 YoY 值拉伸，局部趨勢難辨；部分績效以台股漲跌紅/綠呈現，與風險好壞易混淆。 | 圖形的比較任務與數值語意不夠穩定。 |
| **Keep** | ECharts 圖有必要的文字/表格替代；主要頁面有 `main`、標題、可見焦點樣式，研究結果的警告與來源並未被隱藏。 | K1 以此為基礎提高可讀性，不移除證據。 |

## 2. Product personality

方向名稱：**研究卷宗 / Research Desk**。像一本持續更新的個人市場研究筆記：有日期、來源、註解、判斷、反證與下一步。主語氣是分析、安靜、精確、台灣市場在地；不扮演交易終端，也不製造「最佳策略」的勝利敘事。

- **Must change**：第一視線回答「目前結論是什麼、證據等級是什麼、風險在哪、接下來做什麼」。把 provenance、as-of 日期與資料狀態當成內容結構，而非裝飾 badge。
- **Should change**：介面用繁體中文作主文；保留 CAGR、Sharpe、IS/OOS、`no_candidate` 等需與研究契約對照的術語，首次出現附中文說明。移除非必要的 `RESEARCH WORKSPACE`、`Template research baseline` 式英文裝飾標題。
- **Keep**：產品名稱「台股分析」與不下單、local-first、個人研究的定位；保留可以承認不確定性的平實語氣。

## 3. Visual direction

採 **暖紙色工作底＋墨色正文＋節制的海松色研究標記**。桌面讀報告時以淺色為主要提案；深色可保留為對等主題，由相同語意 token 映射，K1 先做一個完整方案並實測對比。這是設計方向，非本階段已實作的主題切換。

| 面向 | 方向與優先 |
|---|---|
| 色彩 | **Must change**：候選基色 paper `#F5F3ED`、surface `#FFFEFA`、ink `#243039`、muted ink `#54626A`、rule `#D7D9D3`、research accent `#315F68`；以實測對比定稿。台股紅漲/綠跌只用於價格方向，不代表回測品質或風險級別。 |
| 字體 | **Must change**：繁體中文正文以可用的系統無襯線維持可靠性；標題用字重、行距與欄寬建立編輯感，不依賴遠端字體。數字用 tabular numerals，代碼/ID 才用 monospace。正文桌面 15–16px、手機至少 16px；註解盡量不低於 13px。 |
| 版面 | **Must change**：一頁一個主論點區，研究報告採「結論 → 證據 → 限制 → 明細」的固定順序。長文控制行長；表格可以密，說明文字要有呼吸。 |
| 表面 | **Should change**：以底色、細分隔線及少量紙頁面建立層級。卡片只包有獨立語意/操作的模組；同層不靠每塊 12px 圓角外框區分。 |
| 邊角與陰影 | **Should change**：主要內容面 0–6px 半徑；浮層可 8px。常態區塊不用重陰影，只有選單/對話框需要輕微提升。 |
| 節奏與密度 | **Should change**：沿用現有 4/8/12/16/24px spacing 原子，補 32/48px 用於章節。表格行距緊湊；解讀段落與警告有較大間隔。 |
| 圖表 | **Should change**：低彩度、明確軸/單位/期間/資料截至日；主要系列最醒目，基準線與參考區退後。極值提供註記或可切換縮放，不能悄悄裁切；保留數值表 fallback。 |
| 狀態與動態 | **Must change**：資訊、注意、重大限制、無候選、證據不足各有文字標籤與一致位置，不能只靠顏色。載入與互動轉場只做短而必要的回饋，支援 reduced motion。 |
| 圖示 | **Should change**：以文字優先；必要圖示使用同一線條風格與可讀名稱。不要用 emoji、裝飾性閃光或圖示代替研究語意。 |

## 4. Design system changes

- **Must change**：把目前 `--bg`、`--panel`、`--panel2`、`--text`、`--muted`、`--accent` 保留為過渡映射，建立語意層 `canvas / paper / inset / text-primary / text-secondary / rule / action / focus / evidence-primary / evidence-secondary / status-* / price-up / price-down`。逐頁遷移，不讓同一藍色同時表示可點、主要證據、選中與資訊通知。
- **Must change**：定義型別階層（page title、section title、subsection、body、table、caption、numeric、code）及對應行高/最大行長；驗證主按鈕文字、focus、警告及深色變體的對比。主按鈕需達一般文字 4.5:1；目前白字藍底不達標。
- **Should change**：定義三種表面（頁面底、正文紙面、嵌入資料）、兩種密度（閱讀、資料）、按鈕三層（主要、次要、文字）、狀態四層（中性、資訊、注意、重大），避免再增殖局部 `.card`/`.badge` 特例。
- **Keep**：現有 spacing 原子、`--up`/`--down` 的台股慣例、tabular numerals、ECharts、原生 HTML/CSS/JS。視覺重整不引入前端框架。

## 5. Navigation / IA recommendations

- **Must change**：把全站工作分成「市場研究」（Dashboard、Screener、Watchlist、Stock Detail）、「策略研究」（Strategy Lab → Optimization → Walk Forward → Strategy Portfolios）與「個人決策」（Journal、Financial Planner）。桌面全域導覽顯示群組與目前位置；手機使用清楚的群組展開，避免頂欄多行連結擠壓內容。
- **Must change**：研究鏈頁首固定顯示目前階段、上游來源（策略版本/工作）、下游可做的下一步。Optimization 與 Walk Forward 仍可留在 `strategy.html` 的現有路由；IA 重整不要求新增頁面或 API。
- **Should change**：Stock Detail 從搜尋、篩選、觀察名單進入時保留返回脈絡；Portfolio 的 Overview/Components/Allocation/Research/Holdings/History 頁籤維持網址可分享，但提高當前版本/研究工作提示。
- **Keep**：Cross-Strategy 目前是 API-only 的產品事實；K1 不把它虛構成已完成頁面。

## 6. Component recommendations

| 優先 | 共用元件方向 |
|---|---|
| **Must change** | **Research header**：標題、as-of/期間、版本與來源、證據等級、下一步。主行動不與編輯/複製/封存等次要操作同權。 |
| **Must change** | **Evidence notice**：固定「結論／為何重要／依據／限制或下一步」欄位；`info`、`caution`、`critical` 用標籤、細規則和圖示語意，不再每條都是粗色邊卡片。不得把重大限制藏到唯一的折疊區。 |
| **Must change** | **Empty/loading/error**：空清單說明原因及下一步；等待時保留頁面骨架與 context，錯誤給可重試動作；已有資料時不要閃過全頁大型 loading card。 |
| **Must change** | **Data table**：一致的數值右對齊、單位/期間表頭、黏著表頭與第一識別欄；可排序表頭用語意 button 並標示排序狀態。長表有篩選/搜尋與摘要，手機在有意義的欄位間捲動或改成重點清單。 |
| **Should change** | **Metric line**：用一行數值加小型定義/比較取代滿頁 KPI cards；Journal 無樣本時顯示「尚無可計算績效」及建計畫/記成交入口。 |
| **Should change** | **Form field / stepper**：label 與欄位建立程式關聯；說明文字緊貼相關欄位；重大研究設定在送出前給摘要與不可回寫語意。 |
| **Should change** | **Chart frame**：圖名、證據身分、單位、區間、as-of、圖例及文字 fallback 固定位置；不同證據型別不能共用同一 KPI 樣式。 |
| **Keep** | Portfolio/Planner 已有明確版本釘選、`aria-live`、警告區、原始數值與試算假設分欄等基礎。 |

## 7. Page-by-page priorities

| 頁面 | 優先 | K1 頁面重點 |
|---|---|---|
| Dashboard | **Must change** | 先顯示資料日期與市場觀察，再安排走勢/名單；資料維護移入清楚的操作區，不與每日閱讀內容搶位。 |
| Stock Detail | **Must change** | 主價格、資料日期、系統證據、人工判斷與風險分層；讓長判讀有完整閱讀寬度，圖表極值可理解。 |
| Screener | **Should change** | 條件分組、預設條件的套用狀態與資料前提；結果空/載入/無命中分開，保留排序與來源。 |
| Watchlist | **Should change** | 空分級使用精簡引導，已有項目用任務狀態與警訊作掃讀主軸；保留持有/等買點/研究中語意。 |
| Journal | **Must change** | 交易計畫是主工作，記錄成交次之；無樣本績效不佔四張卡。八句話與風險部位在同一閱讀流程內。 |
| Strategy Lab | **Must change** | 列表顯示版本、最近證據與研究階段；把回測的 historical 身分放在績效前，縮減等權 KPI 卡。 |
| Optimization | **Must change** | 長紀錄可尋找；結果先給穩健區域分類、邊界/資料品質，再給熱圖與全點表。保留「樣本內探索，非 winner」。 |
| Walk Forward | **Must change** | independent 摘要是主視圖；fold timeline、no-candidate 現金與 training/OOS 品質容易逐 fold 對照；stitched 明確次要。 |
| Strategy Portfolios | **Must change** | 研究結果與持股快照優先顯示版本/日期；長再平衡明細有導覽與篩選；建立流程維持六步但簡化視覺噪音。 |
| Financial Planner | **Must change** | 七步左側輸入與右側十段結果建立進度關係；Portfolio 選擇先顯示 Active 並可搜尋封存項；證據與規劃假設保持兩個明確區塊。 |

## 8. Interaction improvements

- **Must change**：搜尋、篩選、選擇固定版本與研究工作時顯示目前範圍和結果數。Portfolio/Planner 的長清單先呈現可操作的 Active 項目，封存資料另有明確搜尋入口；不可默默改選「最新」。
- **Must change**：錯誤與不具備條件的狀態指出原因及恢復路徑；`no_candidate`、`insufficient_evidence`、`unavailable` 彼此不同，也不以 fallback 假裝完成。表單錯誤聚焦至相關欄位/摘要，輸入值不因錯誤遺失。
- **Should change**：長研究頁提供章節索引與回到摘要；表格排序/圖表尺度切換記住當頁選擇。折疊明細只裝補充資料，決策關鍵限制始終可見。
- **Should change**：手機的核心閱讀路徑優先保留標題、日期、結論、風險、下一步；研究重表格可受控橫捲，不能讓整頁溢出。K1 實測 375、768、1280px、200% 文字縮放、鍵盤及 reduced motion。
- **Keep**：瀏覽器返回、既有可分享查詢參數、Portfolio 六步與 Planner 七步的資料/契約行為。

## 9. Patterns to remove

- **Must change**：用滿頁 KPI cards 代替研究敘事；用績效紅綠暗示策略好壞；主按鈕白字藍底對比不足；空資料仍展示完整結果框架。
- **Should change**：每段都加全大寫英文 eyebrow、相同圓角面板、粗左色條、狀態小圓點與泛用藍色 CTA；大量英中混排的裝飾性標題；無功能意義的 loading 動畫。
- **Keep**：有語意的數值、警示和限制。移除視覺樣板不等於刪除研究披露。

## 10. What must remain unchanged

- **Keep**：`Historical/OOS ≠ forecast`，包括回測、Portfolio 結果與 Planner 證據旁的歷史／非預測標記。
- **Keep**：Independent-fold OOS 是主要證據；stitched 是次要、非獨立證據。不得把兩者合併成單一績效敘事或同層 KPI。
- **Keep**：`no_candidate` 是有效結果並持現金；Portfolio 不自動重新分配，`insufficient_evidence` 與 `unavailable` 也不悄悄退回其他方法。
- **Keep**：Planner Evidence 與 Planning Assumption 分離；選 Portfolio/Run 不自動填入規劃報酬，實際市值只由使用者手填或留空，保留 Step 2 重複計入風險揭露。
- **Keep**：持股明示 `as of last rebalance`，不是即時或逐日持股；策略貢獻與實際權重可能不同。`max_sector_weight` 等 reporting approximation 必須明示「僅供報告／未執行限制」。
- **Keep**：point-in-time、版本不可變、IS/OOS 分離、搜尋空間修訂披露、無 winner 排名、交易成本與現金語意，以及原生前端架構。K1 僅重設呈現與操作，不變更研究契約。

## 11. Implementation plan for K1

1. **Must change — 設計基準**：將本文件轉成一頁可比對的視覺樣張（Dashboard 閱讀區、研究結果、Planner 證據/假設各一），先定光暗語意 token、型別階層、密度和警示語法；以實際中文與真實數據檢查，而非 placeholder。
2. **Must change — 共用基礎**：更新 `web/css/app.css` 的 token 映射與 navigation、button、form、table、evidence notice、empty/loading/error 元件；保留既有 JS 路由及 API 契約。先解決主按鈕對比、label 關聯、排序表頭鍵盤語意。
3. **Must change — 兩條代表性流程**：先完成 Dashboard → Stock Detail → Journal，以及 Strategy Lab → Optimization → Walk Forward → Portfolio → Planner 的頁首、章節順序與 disclosures。每條流程都在真實資料上檢查首次進入、空狀態、警告、返回與分享網址。
4. **Should change — 其餘頁面收斂**：Screener/Watchlist/Planner 長清單和表格採同一密度、狀態與篩選規則；圖表顏色、尺度、數值 fallback 統一。不要藉視覺整理新增策略、配置方法或研究演算法。
5. **Must change — 驗收**：桌面與 375/768px 窄畫面逐頁走查；鍵盤、200% 文字縮放、reduced motion、文字/非文字對比與各種 loading/empty/error 驗證。逐項比對第 10 節所有研究揭露。K1 修改後依 `AGENTS.md` 執行相關測試、全套研究/Portfolio 測試與 touched files 的 compileall；K0 無 production code 改動。
