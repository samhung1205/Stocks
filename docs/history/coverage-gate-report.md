# Strategy Lab — Coverage Gate 報告

日期：2026-09-20。範圍僅資料 coverage / research readiness 稽核；未修改產品邏輯、未跑 Optimization / Walk Forward / OOS / Portfolio Lab。
稽核腳本（唯讀，只 SELECT）：`scripts/coverage_audit.py`（`uv run python scripts/coverage_audit.py [job|prices|universe|fundamentals|actions|size]`）。

## 結論

**Stage E：BLOCKED（窄）。** 行情、universe、基本面 coverage 都達標；但有 3 個小而確定的正確性問題，會讓 Optimization 對系統性偏誤過擬合。三項修完後判定為 **READY WITH RESTRICTIONS**。

## 1. Backfill job `e743c5a3…` 完整性

- 無 pending / failed / running / error。9,377 done、1,226 skipped_closed（非交易日）、128 empty。
- 128 empty = **43 檔 × 營收/財報/估值**，全部合理：
  - 債券 ETF 34、其他 ETF 6（00877/00887/00888/00928/00955/009815）
  - 特別股 2887I、海外 DR 9103、9105（DR 只有營收有資料，財報/估值空）
- **沒有真正股票資料異常缺失。** 月度 universe（stock-only, 2022-10 起）內僅 2887I、9105 無任何基本面；3717、5820、6907 為新上市／短歷史，屬正常。
- 副作用：這 43 檔（DR、特別股、ETF）以 `type='stock'` 進入 universe，見 Blocking #3。

## 2. 五年行情 coverage（2021-09-18 ～ 2026-09-18）

| 項目 | 結果 |
|---|---|
| 交易日 | 日曆 1,214 = 行情 1,214 = TAIEX_TR 1,214；缺日 0、多日 0、重複列 0 |
| 每日筆數 | 1,876 ～ 2,425（中位 1,987）；TWSE 1,083–1,369、TPEx 789–1,056；無低於中位 85% 的異常日 |
| 個股內部缺口 | 流動股共 119.6 萬列，缺 626 列（0.05%）；春節為主，無系統性缺洞 |
| close 為 NULL | 26,285 列（1.1%，多為 TPEx 無收盤價）；流動 universe 僅 468 列（0.05%） |
| TAIEX_TR | 完整（首日 2021-09-22）；單日 >7% 僅 2024-08-05、2025-04-07 等真實事件 |
| 2026-07-10 | 日曆／行情／benchmark／估值皆 0 筆，四者一致（颱風休市修正有效） |
| 2021-08-02～09-17 | 35 日僅 20–41 檔（深度層殘留），**不可用** |

可靠 price backtest 起始：**2021-09-22 起行情完整**，但因 universe 暖機與因子回看，實際研究起點見 §3。

註：每日筆數自 2026-02 起含 TPEx ETF（updater 寫入），歷史日不含，造成組成不一致，見 Blocking #3。

## 3. Universe coverage（NT$30M、120 observations、判斷日前一年）

| as_of | 規模 |
|---|---|
| 2021-09-18 ～ 2022-02 | 0–20（無意義） |
| 2022-03-22 起（第一個滿 120 obs 日）；2022-04-01 實測 | 728 |
| 2022-04 ～ 2026-09 | min 685 / **median 842** / max 973（2022-10 起：685 / 843 / 973） |

- 暖機影響：原起點 2021-09-18 **不適合立即使用**；2022-03-22 前 universe 為空，之後到 2022-09-22 前流動性窗不足一年（只有 6–12 個月）。
- 因子回看：12-1 Momentum 需 273 個交易日 → 最早訊號日 **2022-10-31**；MA200、126 日波動較短；一年流動性窗於 2022-09-22 補滿。
- **建議 research start：首次再平衡 2022-11-01，訊號截至 2026-09-18（約 3.9 年、47 次月再平衡）。** 不建議為拉長期間而補用更早的殘缺資料。
- 提示：D1 的 universe 是以 `as_of=start` 一次凍結成 stock_ids，日後才符合流動性／新上市的股票不會進入候選（模板內動態流動性過濾仍會逐日運作）。Stage E 應改傳「各月 universe 聯集」。

## 4. Fundamentals coverage

| 資料 | 範圍 | 來源 | Universe 涵蓋 |
|---|---|---|---|
| month_revenue | 1,937 檔、2020-08～2026-08，101,011 列；每月約 1,280–1,340（2025-07～2026-06 為 1,920，含額外名單） | **92,113 assumed / 8,898 observed_finmind / 0 actual**（assumed = 次月 10 日） | ≥6 個月可見 YoY：96.4–99.7%（中位 98.3%） |
| financials | 1,373 檔、2021Q1～2026Q2，22 季無斷層，29,581 列 | **100% assumed_deadline**（Q1 5/15、Q2 8/14、Q3 11/14、Q4 隔年 3/31） | ≥4 季可見 99.8–100%；1,283 檔完整 18 季 |
| valuations | 1,214/1,214 交易日皆有，每日 >1,000 列，1,578,761 列 | 逐日 | PE>0 且 PB>0：78–91%（中位 85%，其餘為虧損，`pe=0`，因子已排除，屬正常） |

- **Strict 模式**：財報 **0 列可用**；營收只有 publish_date ≥ 2026-04-21 的 observed 資料，Revenue Acceleration 需 6 個月 YoY，約 2026-08-11 起才有訊號 → 歷史 **不可用**。
- **Assumed 模式**：營收自 2022-04、財報 TTM 自 2022-04-01（Q4'21 於 3/31 可見）、5 季 YoY 自 2022-05-16 起 coverage 達 99%+；**無明顯時間斷層**。
- Assumed 風險：財報所有公司同一天可見（截斷式、偏保守）；金控／保險（industry 約 80 檔）Q1 實際期限可至 6/1，可能有輕微前視；不是修訂版本庫。屬 warning，不需修。

## 5. Corporate actions

corporate_actions 6,853 列（FinMind DividendResult 6,832 + Split 21），全部含 `ratio`（參考價比）；`dividends` 表僅 534 列／39 檔（深度層，引擎不用）。

| 類別 | 列數 | 引擎處理 |
|---|---:|---|
| dividend_cash（標籤「息」） | 3,135 | 有 `cash_dividend`，正確入帳 |
| **unknown（標籤「除息」2,637／「除權息」237／「除權」232）** | **3,106（45%）** | **不入帳、無警告以外處理** |
| dividend_stock（權）／dividend_mixed（權息） | 303／288 | 無 share_multiplier，只出 warning |
| split（面額變更等） | 21 | 無 share_multiplier，只出 warning |

**流動 universe（2022-10-31 起）**：純現金股利 3,146 筆中 **1,062 筆（34%）現金股利未入帳**；股票股利／權息／減資 673 筆＋分割 14 筆，持有跨越時帳上等於憑空虧損（股票股利參考價 −5～−10%，分割最高 −95%）。粗估對 Top-30 等權組合每年拖累約 1–3%，並使策略與 TAIEX_TR（含息）比較失真。

**Raw close 對 Stage C 因子的扭曲（流動 universe、單日 >11% 非漲跌幅跳動）**：
- 353 次事件／256 檔；285 次有對應 action 列（其中 211 次 `1+報酬 ≈ ratio`，±0.06，即 `ratio` 可作價格調整因子），**68 次無對應 action**（14 次 >50%，多為減資／停牌復牌／缺資料後跳價，如 2603 2022-09-19 缺 6 個交易日後 +109%）。
- 每個月末，動能視窗（273 日）內有此類跳動的股票平均 **6.9%（最高 10.9%）**；126 日波動視窗 3.1%；無 action 解釋者 0.8%。
- 方向：分割／大額配股 → 動能被壓成極負、波動被放大（好股被排除）；減資／反分割 → 假性暴漲，會被 Momentum 誤選。
- 現金除息：中位 −3.2%/次，raw 動能系統性低估高殖利率股。
- **判斷：Stage E 前必須先有還原價（adjusted-price factor series）。** Optimization 會利用這些人工假象（尤其動能回看視窗）。資料已足夠：`ratio` 覆蓋全部 action 列，只需建 price-only 的累積調整係數，不必等待完整 share multiplier。

## 6. 五套策略 readiness（不排名、不跑最佳化）

共同：assumed 為唯一可用的基本面模式；共同起點 2022-11-01；universe 800–970 檔。

| 策略 | 可用資料 | 建議期間 | strict / assumed | 主要 warning | Stage E |
|---|---|---|---|---|---|
| Quality × Growth × Momentum | 財報 TTM/YoY、營收 YoY、價格 | 2022-11-01～ | **僅 assumed** | 財報 100% assumed；raw close 動能；金控前視 | 修 Blocking 後可 |
| Revenue Growth Acceleration | 營收（YoY 序列）| 2022-11-01～（營收本身自 2022-04 可用） | **僅 assumed**（strict 僅 2026-08 後） | 營收公布日=次月 10 日推估；基期／缺月；96–99% 覆蓋 | 修 #1/#3 後可（不依賴還原價，但依賴現金股利入帳） |
| Momentum + Trend Following | 價格／MA200 | 2022-11-01～ | 無關（不用基本面） | **raw close 扭曲最嚴重（6.9%/月受影響）**；ETF 汙染 | **須先還原價** |
| Value × Quality | 逐日 PE/PB、財報 | 2022-11-01～（估值可更早） | **僅 assumed** | PE>0 僅 85% 覆蓋（虧損排除）；sector-neutral 退回 global（無歷史產業分類） | 修 Blocking 後可 |
| Low Volatility × Quality | 126 日波動、財報 | 2022-11-01～ | **僅 assumed** | 波動被分割／減資放大（3.1%/月）；財報 assumed | **須先還原價** |

## 7. Stage E Gate

### Blocking issues（皆小修）

1. **股利類別誤判**（`app/datasources/finmind.py:142`）：僅映射「息／權／權息」，FinMind 實際多為「除息／除權息／除權」→ 3,106 列 `unknown`，現金股利漏記。修法：擴充映射並以 `raw_json` 重新分類既有列（不需重抓）；`cash_dividend` 應對「除息／除權息」的現金部分一致填入。
2. **無還原價、無 share multiplier**：a) 建 price-only adjusted close（以 `ratio` 累乘，先驗證與 raw 跳動吻合）供 Momentum／Low Vol／MA 使用；b) 引擎的股票股利／分割：純「權」與面額變更可由 `before/after` 得 multiplier，「權息」需另接 FinMind `TaiwanStockDividend` 的每股股票股利，否則仍屬 warning 並在 Stage E 排除持有跨越事件的結果。
3. **ETF／DR／特別股汙染 universe**：151 檔 TPEx `00xxx` ETF 標為 `type='stock'`（歷史只有 2026-02 後才有資料，導致 2026-09 universe 中 40 檔為債券 ETF 等），另有 2887I、9105。修法：`liquid_universe` 加 `stock_id NOT LIKE '00%'` 並排除 DR／特別股，或將 type 重分類。

### 可以只以 warning 呈現（不要過度工程）

strict 模式無歷史資料；財報／營收全為 assumed（含金控 6/1 期限）；非修訂版資料；sector-neutral 退回 global；上市日 NULL 的 471 檔；每日筆數組成漂移；close=NULL 的 0.05%；68 次無 action 的跳價（還原價建好後，其餘以「單日 >11% 跳動排除」warning 處理）。

### Stage E 設計限制（不需修，但必須遵守）

- 研究期 2022-11-01 ～ 2026-09-18；不做 strict 基本面研究。
- Universe 傳各月聯集，不用單點 as_of。
- 單次 panel 載入約 4 秒（91 萬檔日、104 萬列估值）：Optimization 必須載入一次後記憶體重用，不可每次組合都重讀 DB。

## 8. 資料庫

- `data/stocks.db` **504,541,184 bytes（≈481 MiB）**，freelist 0；`data/` 含 3 份備份共 662 MB。
- 主要資料表：daily_quotes 2,466,809；valuations 1,578,761；month_revenue 101,011；financials 29,581；institutional_daily 270,729；corporate_actions 6,853；benchmark_series 1,214；trading_calendar 6,936；stocks 3,202；backfill_tasks 10,754。
- **仍適合 SQLite local-first**：單人讀多寫少、五年全市場僅約 0.5 GB、PK/日期索引下範圍查詢秒級；瓶頸是 Python 端 panel 組裝而非 DB。若日後要多機並行 backtest 或保留修訂版本庫再評估 Postgres（`STOCKS_DB_URL` 已預留）。

## 附：本次新增檔案

僅 `scripts/coverage_audit.py`（唯讀稽核腳本）與本報告；未變更任何 app／tests／web 程式。
