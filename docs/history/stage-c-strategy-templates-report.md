# Stage C — Strategy Templates 交付報告

日期：2026-09-19。範圍僅五套可 Clone／Modify 的 Strategy Template、factor 與 signal rule；未實作 UI、Optimization、Walk Forward、OOS 或多年績效比較，亦未變更 Backtest accounting、成本或成交邏輯。

## 五套 Template 定義

| Template | Hypothesis | 訊號定義 | Known failure regimes |
|---|---|---|---|
| Quality × Growth × Momentum | 獲利與資產負債品質佳、成長可見，且價格動能確認的公司，表現延續性可能較高 | Quality、Growth、12-1 Momentum 各自橫斷面排名後加權，取 Top N | 因子急遽輪動、景氣初升段低品質反彈、擁擠動能反轉、strict 財報覆蓋稀疏 |
| Revenue Growth Acceleration | 正成長且月營收 YoY 持續加速，可能比季報更早反映營運改善 | 最新可見 YoY 與最近三個月平均 YoY 相對前三個月的加速度排名；預設要求兩者為正 | 基期效應、景氣循環高點、一次性認列、延遲或重編資料 |
| Momentum + Trend Following | 中期相對強勢且仍位於長期均線上方，比未經趨勢確認的動能更可能延續 | 12-1 Momentum 70% + 收盤相對 MA200 距離 30%；預設只保留 MA200 上方標的 | 盤整洗價、跳空反轉、急跌急彈、未能可靠調整的公司行動 |
| Value × Quality | 低估值搭配品質可降低純 Value 的 value-trap 風險 | Earnings Yield、Book Yield 合成 Value，再與 Quality 加權；可做 sector-neutral rank | Value trap、產業結構衰退、利率造成估值重定價、估值歷史不足、無歷史產業分類 |
| Low Volatility × Quality | 低實現波動搭配基本面品質，可能形成較防禦的股票組合 | 126 日年化波動反向排名 60% + Quality 40%；先通過共同流動性 Universe | Risk-on 急漲、波動 regime break、防禦因子擁擠、放寬流動性後的 stale-price 假象 |

每套 metadata 都包含 `hypothesis`、`required_data`、`known_failure_regimes`、`default_config`。`StrategyTemplate.clone_config()`／`signal_rule(overrides)` 可深層複製與局部覆寫，不會改動 catalog 預設值；輸出直接使用 Stage B `Signal.targets / candidates / scores` target portfolio contract。

## Factor / signal 架構

- `panel.py` 仍是唯一 DB boundary。它把 dated valuation observation 與證券 metadata 放入 Panel；engine 只把截至 T 已看見的最新 valuation、securities、publication mode 加入既有 `SignalContext`。交易會計、成本、成交、FIFO 與 metrics 未改動。
- `factors.py` 是純計算層，沒有 DB import。價格、流動性、月營收、財報、估值全部只從 `SignalContext` 讀取；基本面再次檢查 `publish_date < signal_date` 與 strict／assumed source allowlist，未來 valuation date 也會拒絕。
- 橫斷面工具支援線性 quantile winsorization、average-rank ties、`higher_is_better` 方向，以及 `exclude / median / worst` missing policy。預設 `exclude`，避免資料缺漏被誤當中性事實。
- 所有模板先套共同 point-in-time Universe：訊號日仍有 bar、上市／下市有效、security type 為 stock、近 120 個 observations 的平均日成交金額至少 NT$30M。Low Volatility 同樣走此 filter，不以 stale／低流動性價格冒充低風險。
- Value × Quality 的 sector-neutral rank 只接受 `actual / historical / point_in_time` sector source。現有 `stocks.industry` 是 current snapshot，會明確 fallback 到 global rank，並把 warning 放入 `Signal` 與 backtest warnings；小於 minimum sector size 的群組亦同。
- `signals.py` 將因子排名合成 score、以 score 再做 deterministic Top N（同分以 stock ID 穩定排序），並產生 equal 或 score-weighted target。diagnostics 保留 eligible count、原始 factor、rank、入選名單與 publication mode。

## 預設參數與理由

共同預設：月再平衡、Top 30、等權、無策略內現金、5%／95% winsorization、missing=`exclude`、近 120 筆平均日成交金額至少 NT$30M。月頻避免把低頻基本面資料假裝成日頻訊號；Top 30 與等權是可解釋的起始值，不是最佳化結論；winsorization 降低小樣本極端值支配合成分數的風險。

| Template | Default config | 理由 |
|---|---|---|
| Quality × Growth × Momentum | Quality/Growth/Momentum = 40/30/30；Quality = ROE 35%、OCF/NI 25%、Gross Margin 20%、Low Debt 20%；Growth = 月營收 YoY／TTM 財報營收成長各 50%；Momentum 252 日、skip 21 日 | 品質為主、成長與價格確認為輔；12-1 是常見且可解釋的研究起點，未做參數搜尋 |
| Revenue Growth Acceleration | YoY/Acceleration = 40/60；3 個月對前 3 個月；兩者預設須為正 | 讓「加速」高於單一成長水準，同時降低只因負成長改善而入選的情況 |
| Momentum + Trend | Momentum/Trend = 70/30；252 日、skip 21；MA200 且預設須在均線上 | 以中期動能為主、長期趨勢作確認；沒有 Optimization |
| Value × Quality | Value/Quality = 55/45；Earnings/Book Yield = 60/40；valuation 最長 10 calendar days；sector-neutral，最小組別 3 | PE 較接近獲利能力但保留 PB 分散單一估值指標；過舊 valuation 不沿用；小 sector 不產生虛假精確度 |
| Low Volatility × Quality | Low Vol/Quality = 60/40；volatility lookback 126 日 | 半年 realized volatility 是穩定性與反應速度間的合理起點；品質避免單純選到業務惡化但價格不動的股票 |

所有 factor weights、lookback、skip、Top N、rebalance、MA、流動性門檻、winsor limits、missing policy、sector-neutral、valuation age、equal／score weighting 與 cash weight 都可在 clone config 覆寫。

## strict / assumed 資料需求

- `strict`：只接受 `actual`／`observed_finmind`，且 date-only evidence 必須滿足 `publish_date < signal_date`。`observed_finmind` 只代表 provider observation，不等於發行公司正式公告時間。現況 strict 財報很稀疏，因此 QGM、Value × Quality、Low Volatility × Quality 可能沒有足夠 Quality score；這是誠實的空訊號，不會以未公告值補齊。
- `assumed`：額外接受 `assumed_deadline`，每個含基本面的 signal 與 backtest result 都產生 warning；不得標示為嚴格無前視偏誤。月營收加速只使用已由 panel 釋出的公告後資料，不以 revenue 所屬月份當可見日期。
- Momentum + Trend 不需基本面，strict／assumed 不改變價格因子。Value 的 dated PE/PB 亦不受 publication mode 控制，但 Quality 受控。
- 真正 sector-neutral 需要歷史 point-in-time sector taxonomy。目前 repo 只有 current `stocks.industry`，預設明確 global fallback，不假裝具備歷史產業分類。

## 測試結果

| 驗證 | 結果 |
|---|---|
| `.venv/bin/python -m compileall -q app tests` | 通過 |
| `.venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v` | **34 / 34 通過**（既有 26 + Stage C 8） |
| `.venv/bin/python tests/smoke.py backtest` | 通過；Stage B 手算、No Look-Ahead、成本、公司行動與 metrics 未回歸 |
| `.venv/bin/python tests/smoke.py all` | sources、light、deep、finance、backtest **全部通過**；外部 TWSE／TPEx／MOPS／FinMind 路徑成功 |

Stage C deterministic synthetic tests 覆蓋五套模板各自固定選股結果、重跑一致性、未來價格／公告不可改變過去 signal、strict 排除 assumed／assumed 明示 warning、ranking ties、winsorization、三種 missing policy、Top N config 改動、sector-neutral group rank 與 current-sector fallback、Low Volatility 流動性門檻，以及完整 metadata contract。未等待五年 backfill，也未輸出正式多年績效或「最佳策略」。

## Known limitations

- 五年全市場 backfill 尚未完成；defaults 未經多年比較、parameter search、Walk Forward 或 OOS 驗證，僅為合理研究起點。
- 財報與月營收不是 revision-vintage database；即使 publication boundary 正確，仍可能看到後來 restatement 的值。
- current `stocks.industry` 不是歷史 taxonomy，Value × Quality 現況通常會 global fallback。需要歷史 sector source 才能宣稱 sector-neutral。
- Momentum／Volatility 使用 panel 的 raw close；Stage C factor 尚未重建 adjusted-price series。即使 engine 能處理明確 `share_multiplier`，來源無可靠倍率的分割、減資或混合除權息仍可能扭曲 price factor，且不能用 `ratio` 猜測。
- Revenue Acceleration 可能受基期、月份天數、一次性認列與缺月影響；它是 signal，不是因果判定。
- Universe 只以現有歷史行情、上市／下市與成交金額判斷；上市日未知、月中短暫符合門檻及歷史證券分類仍有 Stage A+ 已揭露的 coverage 限制。

## Stage D 是否 ready

**工程介面 ready。** Stage D 可直接讀五套 metadata／clone config、呼叫 `signal_rule`、用 `backtest_config` 套用 Stage B rebalance，並呈現 targets、scores、diagnostics 與 warnings；不需要改 Backtest accounting core。

**正式多年績效內容尚未 ready。** 在五年 backfill、coverage 檢查、strict／assumed 標示與 sector fallback 呈現完成前，Stage D 只能使用 synthetic／小範圍 smoke 或清楚標示資料不完整的歷史結果，不能比較或宣稱最佳策略。
