# Stage B — Backtest Core 交付報告

日期：2026-09-19。範圍僅 Backtest Core；未實作 Optimization、Walk Forward／OOS、Strategy Templates、Strategy Lab UI 或無關重構。

## 實作內容

- 新增 `app/services/strategy/`：
  - `panel.py`：唯一 DB 邊界。組裝行情、benchmark、上市／下市、公司行動與依公布日釋出的基本面資料；也提供 synthetic panel。
  - `engine.py`：不 import DB 的純事件式回測核心；同步維護 frictionless Gross 與含成本 Net ledger、FIFO 成本批次、fills、closed trades、equity curve、warnings。
  - `metrics.py`：完成 Total Return、CAGR、Annualized Volatility、Sharpe、Sortino、Max Drawdown、Calmar、Beta、Alpha、Win Rate、Payoff Ratio、Profit Factor、Average Win/Loss、Expectancy、Average Holding Period、Turnover、Number of Trades、Recovery Time、Gross Return、Net Return。
  - `costs.py`：commission、sell tax、slippage 與 fill price 的純函式成本模型。
  - `rules.py`：daily／weekly／monthly／指定日期再平衡；signal weights／equal／score-weighted sizing；最大單一權重、現金權重與 lot size。
  - `types.py`：Panel、Bar、Publication、CorporateAction、SignalContext、execution／missing data／rebalance／sizing／cost config 契約。
- `strict` 只載入 `actual`／`observed_finmind`；`assumed` 額外允許 `assumed_deadline`。日期型公布證據採 `publish_date < signal_date`，公布當日不可見。
- publication mode 是 `Panel` 必填欄位，並保存於每次回測結果的 `metadata.publication_mode`；下游持久化層應原樣保存，不能從策略設定重新推測。
- 新增 `tests/smoke.py backtest` 與 `tests/test_backtest_core.py`。未等待五年 backfill，全部使用 synthetic 或 temporary SQLite 小資料。

## Engine execution model

1. Panel 在 T 日只交付 T 日收盤前可知的行情、當日事件，以及 `publish_date < T` 的基本面資料；signal callback 只收到截至 T 的 history，不持有未來 panel。
2. Signal 在 T 產生 target portfolio；`delay_sessions >= 1` 是型別層硬限制。預設於下一個交易日 T+1 open 成交，也可設定 T+1 close 或更長交易日延遲。非交易日事件不計入 delay。
3. 交易依目標權重與執行價換算股數，按 lot size 向下取整；再平衡先賣後買。較新的 signal 會取代仍未成交的舊 target order，避免缺值期間累積過期訂單。
4. 缺執行價可設定整筆 target order 延至下一可用交易日或取消。估值缺值可 carry-forward 並依 stale session 門檻警告，或直接報錯；新部位若無收盤價，carry-forward 模式以已知成交價暫估並警告。
5. Gross ledger 使用原始成交價且不扣成本；Net ledger 使用 adverse slippage 後成交價並扣手續費與賣出稅。兩者獨立再平衡，輸出 Gross／Net equity 及 return。
6. 下市日強制以當日有效收盤或最後有效收盤退出，`exit_reason=delisted`；沒有價格時不發明清算價，保留警告。
7. 公司行動只處理資料明示的 `cash_dividend` 與 `share_multiplier`。Stage A+ 的 `ratio` 明確只作參考價比，不視為股數倍率；缺可靠倍率的 split／stock dividend／capital reduction 只警告、不套猜測公式。

## 公式與成本假設

- Total／Gross／Net Return：`ending_equity / initial_equity - 1`。
- CAGR：`(ending / initial)^(1 / ((end_date-start_date)/365.25)) - 1`。
- Annualized Volatility：每日 log return 的 sample standard deviation（`n-1`）× `sqrt(252)`；CAGR、Volatility、MDD 與 `finance.etf_stats.compute()` 保持相同慣例。
- Max Drawdown：逐日 `equity / running_peak - 1` 的最小值；Recovery Time 是已完成回復區間由前高至重新達到前高的最長 calendar days；Calmar = CAGR / `abs(MDD)`。
- Sharpe：每日 arithmetic excess return 的平均／sample standard deviation × `sqrt(252)`；年化 risk-free rate 先轉 effective daily rate。
- Sortino：相同 excess return，downside deviation 使用 `min(excess, 0)` 的平方平均，再乘 `sqrt(252)`。
- Beta：策略與 benchmark 對齊日報酬的 sample covariance／benchmark sample variance；Alpha 使用日頻 CAPM excess alpha × 252 年化。
- Win Rate／Average Win／Average Loss 延續 `journal.performance()`，損益為零歸 loss。為避免 legacy 命名污染 Strategy Lab，Backtest 明確輸出 `payoff_ratio = average_win / abs(average_loss)`，只有此欄相容 journal 過去名為 Profit Factor 的公式；標準 `profit_factor = gross_profit / abs(gross_loss)`，其中 gross loss 只加總負損益。分母為零時回傳 `None`，避免 JSON 出現 Infinity。Expectancy = `P(win)×avg_win - P(loss)×avg_loss`。
- Average Holding Period 使用實現賣出批次的股數加權 calendar days；Number of Trades 是實現賣出事件數，未平倉部位不計。
- Turnover 是買賣雙邊 `sum(abs(reference_notional)) / average_net_equity`。
- 預設成本與既有 journal 一致：手續費率 0.1425% × 0.6 折扣、每筆最低 1 元、賣出稅 0.3%；金額採既有 `round()` 慣例。Slippage 為可設定 adverse bps，買進加價、賣出減價。所有成本均可在 `CostConfig` 覆寫。
- 所有百分比型 metric 以 percent 輸出；risk-free rate 與權重設定用 decimal。

## 測試結果

| 驗證 | 結果 |
|---|---|
| `.venv/bin/python -m compileall -q app tests` | 通過 |
| `.venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v` | **26 / 26 通過**（既有 22 + Backtest Core 4） |
| `.venv/bin/python tests/smoke.py backtest` | **全部通過** |
| `.venv/bin/python tests/smoke.py all` | sources、light、deep、finance、backtest **全部通過**；實際外部資料源亦成功 |

Backtest smoke 涵蓋：2 股票 × 10 交易日手算、No Look-Ahead、T→T+1、commission／tax／slippage、Gross／Net、`etf_stats.compute()` 的 CAGR／Volatility／MDD 交叉驗證、Beta／Alpha 對齊、Payoff Ratio／標準 Profit Factor 語意、Zero Trade、Single Trade、Negative Return、Missing Data、Rebalance、Delisting、cash dividend、ratio 不誤用及 explicit share multiplier。手算案例 Gross ending equity = 10,000；Net ending equity = 9,952；Gross Return = 0%；Net Return = -0.48%。

## Known limitations

- 五年全市場 backfill 仍未完成；目前通過的是核心正確性與現有資料源 smoke，不代表五年資料覆蓋已完整。
- `assumed` 模式使用法定期限假設，不是實際公告證據；歷史 restatement vintage 仍無法還原。結果雖保存 mode，但不能把 assumed 宣稱為嚴格無前視偏誤。
- 公司行動不推導未知倍率；混合除權息、減資、現增、下市清算價若來源未提供明確現金或股數效果，結果會警告而非猜測。下市 exit 的最後收盤不等於最終清算價值。
- 成交模型不模擬漲跌停鎖死、停牌期間排隊、order book、部分成交、volume participation 或非線性 market impact。Slippage 目前是固定 bps。
- 成本設定目前是每次 run 一組；股票／ETF 不同稅率需由呼叫端分 run 設定，尚無逐證券費率表。
- 僅支援 long-only、無槓桿 target portfolio。成交股數採 lot floor，未做零股撮合細節。
- 既有 journal 的 legacy `profit_factor` 鍵仍代表 Payoff Ratio，為相容性不修改；Strategy Backtest 已拆成 `payoff_ratio` 與標準 `profit_factor`。未來 Strategy Lab UI 必須使用 Backtest 的欄位語意，不可把 journal legacy 欄位當標準 Profit Factor。
- Stage B 沒有建立 run store、API 或 UI；結果 payload 已包含完整 config 與 publication mode，持久化與呈現留給後續既定階段。

## Stage C 是否 ready

**Ready。** Stage C 可只新增 Strategy Templates／factor 與 signal rules，直接消費 `SignalContext` 並輸出 target portfolio，不需要改動 DB 邊界或回測會計核心。進入實際多年策略驗證前，仍須完成五年 backfill、檢查 coverage，並依策略是否使用基本面明確選擇 `strict` 或 `assumed`；本階段到此停止。
