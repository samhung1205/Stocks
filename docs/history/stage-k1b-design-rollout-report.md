# Final Product Design Stage K1B — Full-page Research Desk Rollout

日期：2026-09-26。方向沿用 [K0：研究卷宗 / Research Desk](final-design-direction.md) 與 [K1A 設計基礎](stage-k1a-design-system-report.md)。本階段只修改原生 HTML/CSS/JS 的呈現與互動；沒有修改 API、DB/schema、研究邏輯、策略／Portfolio 配置邏輯或 Financial Planner 計算語意。

## 1. 正式色票：Academic Research Desk C+

production semantic tokens 已採用 C+：

| 語意 | 色值 | 用途 |
|---|---:|---|
| Canvas | `#F0F2F3` | 冷中性工作底 |
| Paper | `#FCFCFA` | 低暖度正文紙面 |
| Inset | `#E9ECEE` | 中性冷灰嵌入區 |
| Text primary | `#2B2D31` | graphite 正文 |
| Text secondary | `#5B5E66` | 次要資訊 |
| Rule | `#CDD2D4` | 一般分隔線 |
| Control rule | `#8A9499` | 表單控制項邊界 |
| Action / evidence | `#555D7A` | desaturated indigo 行動與主要證據 |
| Focus | `#3E4C79` | 鍵盤焦點 |
| Evidence secondary | `#6C7180` | 次要證據 |

status 與台股價格語意未改：neutral `#59666B`、info `#315F68`、caution `#835516`、critical `#9A3730`、紅漲 `#A63732`、綠跌 `#247048`。

在 paper 上的對比：正文 13.43:1、次要文字 6.31:1、action 6.32:1、focus 8.13:1、secondary evidence 4.74:1；status 5.78–6.90:1、紅漲 6.36:1、綠跌 5.86:1。白字 action CTA 為 6.50:1；control rule 為 3.02:1，符合非文字控制項 3:1。

## 2. K1B rollout

### Screener

- 建立 Research Desk 頁首、條件工作紙與結果上下文。
- 初始、載入、無命中與錯誤狀態分開；錯誤提供重試。
- preset、checkbox fieldset、排序表頭與加入觀察名單維持既有行為，補足 button、`aria-sort` 與錯誤處理。
- 375 / 768 / 1280px 不產生整頁水平溢位。

### Watchlist

- 顯示持有／等買點／研究中三層摘要與真實筆數。
- 空分級改為精簡任務提示；全空時連到 Screener。
- 保留三種任務語意與台股紅漲綠跌，不把價格顏色拿來表示研究品質。

### Strategy Builder、Optimization、Walk Forward

- step pills 收斂成有順序的研究序列；目前步驟以 indigo rule 與文字標示。
- Strategy Builder 改為卷宗頁首，主要欄位使用中文；必要的模板與契約術語保留英文原名。
- Optimization 建立頁先說明「樣本內探索、非 winner」，並把策略模板、排序目標、研究期間、預算與搜尋空間改為中文主標。
- Walk Forward 建立頁先顯示 independent OOS 主證據、stitched 次要證據、freeze rule 與來源；`no_candidate` 仍明示為 OOS 持現金，不用 IS peak 補位。
- 粗側色條與持續移動的 indeterminate progress 已移除，改用細頂部規則及靜態狀態。

### Strategy Portfolios

- Active / Archived 清單增加可搜尋範圍、顯示筆數與分批載入；大量封存研究不再一次佔滿頁面。
- 六步建立流程改成同一研究序列，名稱、元件、配置、無候選政策、限制與版本確認都有明確順序。
- Portfolio 結果仍保留固定版本、`Historical / OOS ≠ forecast`、holdings as of last rebalance、no-candidate cash、配置不足不 fallback、reporting-only 限制。

### Financial Planner

- 七步流程改成 case-file index；結果區改為較平的研究紙面。
- Portfolio picker 增加搜尋、顯示範圍與保留目前選取；不會自動切到 latest version。
- Research Evidence 顯示固定版本、研究期間、metrics、內部現金、成本拖累與限制摘要；原始限制保留在可展開明細。
- Planning Assumption 維持獨立區塊，選 Portfolio／Run 不會自動填入未來報酬。
- 動態 ETF 與其他來源欄位補上 `label/for`，同時保留既有 accessible name。

### Charts

- 共用與 Planner 圖表使用 graphite、slate、desaturated indigo、restrained ochre / teal；沒有改變 series、數值或正負語意。
- 台股紅／綠只留給價格方向；研究績效不以紅綠判定好壞。

## 3. Representative screenshots

- Screener 1280 (`output/playwright/k1b/screener-1280.png`, local untracked artifact)、768 (`output/playwright/k1b/screener-768.png`, local untracked artifact)、375 keyboard focus (`output/playwright/k1b/screener-375-focus.png`, local untracked artifact)
- Watchlist 1280 (`output/playwright/k1b/watchlist-1280.png`, local untracked artifact)
- Strategy Builder 1280 (`output/playwright/k1b/strategy-builder-1280.png`, local untracked artifact)
- Optimization create 1280 (`output/playwright/k1b/optimization-create-1280.png`, local untracked artifact)
- Walk Forward create 1280 (`output/playwright/k1b/walkforward-create-1280.png`, local untracked artifact)
- Portfolio list 1280 (`output/playwright/k1b/portfolio-list-1280.png`, local untracked artifact)、Portfolio Builder 1280 (`output/playwright/k1b/portfolio-builder-1280.png`, local untracked artifact)
- Planner 1280 (`output/playwright/k1b/planner-1280.png`, local untracked artifact)、Planner Step 5 mobile (`output/playwright/k1b/planner-step5-375.png`, local untracked artifact)

真實資料 walkthrough 包含 Screener preset、Watchlist 的 2330／3481／00646、725 筆封存 Portfolio 搜尋、Planner `Case A 25歲`、Portfolio #25 / version 31 / completed run `729e3b9a…`。該 Run 正常顯示 CAGR、Sharpe、Max Drawdown、內部現金、Cost drag 與 `PARTIAL_EXECUTION × 6`，同時保留非預測文字。

## 4. Accessibility and responsive verification

- 1280px：Screener、Watchlist、Portfolio list／builder、Planner、Strategy Builder、Optimization create、Walk Forward create 均無整頁水平溢位。
- 768px、375px：Screener 與 Planner Step 5 實測；表格由自己的 `.scroll-x` 處理，document width 不超出 viewport。
- 200%：以 1280px 視窗對應的 640 CSS px layout viewport 驗證 Portfolio Builder，可重排且無整頁溢位。
- Keyboard：Screener preset、導覽與表單可用 Tab 到達；可見 focus 為 3px indigo outline。
- Reduced motion：`prefers-reduced-motion: reduce` 已命中；動畫與 transition 會縮為 `.01ms`，移動式 progress 已移除。
- Browser console：最後一輪 8 個更新頁面 0 error / 0 warning。

## 5. Research contracts kept unchanged

- `Historical/OOS ≠ forecast`。
- Independent-fold OOS primary；stitched secondary 且不是獨立證據。
- `no_candidate` 是有效現金結果；不自動 redistribution。
- Optimization 是 IS exploratory，沒有 winner。
- Planner Evidence 與 Planning Assumption 分離；研究值不自動寫入規劃假設。
- Holdings 明示 as of last rebalance；不是即時持股。
- Reporting approximation 與資料品質限制仍在決策附近。

## 6. Verification results

- Impeccable detector：**0 primary findings**；只剩 Financial Planner 長文中的 em-dash density advisory，沒有阻擋項。
- JavaScript：修改過的外部 JS 與 Screener／Watchlist／Stock inline scripts 全部通過 `node --check`。
- Python：`uv run python -m compileall -q app tests scripts` 通過。
- Tests：`uv run python -m unittest discover -s tests -p 'test_*.py'`，**389 tests，OK**。
- `git diff --check`：通過。

測試輸出仍有既有 Starlette deprecation 與 SQLite `ResourceWarning`；瀏覽器 console 沒有對應錯誤。

## 7. Remaining boundaries

- Dark mode 未在本階段重新設計。
- Cross-Strategy 仍是 API-only，沒有虛構 UI。
- 技術契約名稱、指標名稱與 API 枚舉值仍保留英文，避免與研究紀錄失去對照。
- `app.css` 仍保留部分舊 selector 作相容層，但實際 cascade 已統一至 C+ tokens；日後可在全站 selector 使用量盤點後移除，不需綁在功能或研究變更中。

K1B 已可進入後續全站視覺 QA 或針對個別長研究結果頁做細節 polish；目前沒有研究契約、資料語意或 responsive blocker。
