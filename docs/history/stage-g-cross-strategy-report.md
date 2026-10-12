# Stage G — Cross-Strategy Analysis / Complementarity

範圍：對五套策略既有的 real-data OOS / Walk Forward independent-fold evidence（F1.5 五個正式 job）做描述性比較與互補性分析，為下一階段 Portfolio Lab 準備資料。**沒有**策略排行榜、Winner、推薦，也**沒有**計算任何資金配置權重。程式碼：`app/services/cross_strategy/`（`data.py` 是唯一 DB boundary，其餘六個模組純函式）；新增路由 `POST /api/strategy-lab/cross-strategy/analyze`（read-only，不寫入、不複製 Walk Forward 資料）；測試 `tests/test_cross_strategy.py`（24 個，多數離線建構 fixture，1 組對真實 Walk Forward 流程跑一個小型 temp-DB job）。全專案 unittest 套件 **230/230 通過**（既有 206 + 新增 24）。分析對象是 F1.5 report 裡同一批 job：

| 策略 | job_id |
|---|---|
| momentum_trend | `9a3612c981e44cf88daabc655abc43ed` |
| quality_growth_momentum | `052d3472688740c89d8e4701bbc358cd` |
| revenue_growth_acceleration | `15f5a27ea6174a969ca5c2ebeeefe795` |
| value_quality | `098c6aa9252544cc88c910c68c05c490` |
| low_volatility_quality | `90f389547ad74c219c0074f5d2570b1d` |

五個 job 的 fold test window 逐一核對（`fold_windows.mismatches`），四個 fold index 在五套策略間完全一致：F0 2024-11-01..2025-04-30、F1 2025-05-02..2025-10-31、F2 2025-11-03..2026-04-30、F3 2026-05-04..2026-09-18（truncated）。這是後續所有 cross-strategy 比較能用 fold index 對齊的前提，程式碼裡有主動檢查（不是假設）。

## 1. OOS behavior comparison（描述性，不排名）

| 策略 | evaluated/no_candidate（共4 fold） | OOS Sharpe（evaluated folds, min/median/max） | OOS CAGR%（min/median/max） | OOS MaxDD%（min/median/max） | ΔSharpe(OOS−IS) median | primary-on-edge folds |
|---|---|---|---|---|---:|---|
| momentum_trend | 4 / 0 | −0.98 / 0.79 / 3.67 | −36.6 / 13.0 / 302.3 | −44.0 / −25.3 / −8.1 | −0.29 | 0,1,2,3（4/4）|
| quality_growth_momentum | 1 / 3 | 0.43（n=1） | 9.6（n=1） | −29.6（n=1） | −1.05 | 3（1/1）|
| revenue_growth_acceleration | 2 / 2 | 1.02 / 1.48 / 1.93 | 33.7 / 50.3 / 66.9 | −22.7 / −16.8 / −10.9 | +0.35 | 2,3（2/2）|
| value_quality | 4 / 0 | −0.51 / 0.58 / 1.84 | −18.2 / 7.7 / 41.7 | −29.9 / −9.1 / −3.5 | −0.69 | 0,1,2,3（4/4）|
| low_volatility_quality | 1 / 3 | −0.33（n=1） | −8.1（n=1） | −15.2（n=1） | −2.88 | 0（1/1）|

（數字與 F1.5 report 完全一致 — Stage G 沒有重算 OOS，只是把既有 `walkforward/diagnostics.analyze()` 摘要並排。）三個結構性觀察：

1. **evaluated-fold 數量本身就是第一個互補訊號**：QGM 與 LVQ 大部分時間持現金（3/4 no_candidate），momentum 與 value_quality 全程都有 stable candidate。這不是「哪個策略比較好」，是四個 fold 的市場狀態剛好落在不同策略各自的 `NO_STABLE_CANDIDATE`（`isolated_peak`）門檻兩側。
2. **primary-on-edge 是全面性的**：12/12 real evaluated folds（延續 F1.5 的結論）在至少一個搜尋軸上頂到邊界，Stage G 沒有新增或擴大任何 search space，這個限制原封不動帶進來。
3. **ΔSharpe 的方向和幅度沒有一致模式**（−2.88 到 +0.35），且每個策略最多 4 個、常常只有 1–2 個 evaluated fold——本節任何數字都只是「這幾個 fold 發生了什麼」，不是穩定性估計。

## 2. Return correlation（真正重疊的 OOS 日期；cash-restart 語意見下）

方法：每個策略的每日報酬只在**同一個 fold 內部**計算（`net_equity` 逐日 pct-change，fold 第一天沒有前值、直接捨棄，不用起始資金回推、也不跨 fold 邊界相減）。跨策略比較時只用「同一個實際日曆日期」配對——因為四個 fold 的測試窗口完全對齊、且不同 fold 之間日期不重疊，所以把一個策略所有 evaluated fold 的 (日期→報酬) 直接聯集，並不會把 fold 邊界誤接成連續序列（`test_fold_boundary_is_never_bridged` 測試專門驗證這點：即使某 fold 結束值與下一 fold 起始值有數倍落差，聯集後也偵測不到那個假報酬）。`no_candidate` fold 全程是常數現金、視為零變異數序列，**整段排除**、不會顯示成假的 0.0 相關係數。

### 2a. 全樣本相關係數（pairwise-complete，最少 5 個重疊交易日才計算）

| Pair | r | n 天 | 來自 fold |
|---|---:|---:|---|
| momentum_trend – quality_growth_momentum | 0.90 | 97 | 3 |
| quality_growth_momentum – revenue_growth_acceleration | 0.91 | 97 | 3 |
| momentum_trend – revenue_growth_acceleration | 0.89 | 213 | 2,3 |
| revenue_growth_acceleration – value_quality | 0.83 | 213 | 2,3 |
| quality_growth_momentum – value_quality | 0.83 | 97 | 3 |
| low_volatility_quality – value_quality | 0.87 | 118 | 0 |
| low_volatility_quality – momentum_trend | 0.78 | 118 | 0 |
| momentum_trend – value_quality | 0.74 | 456 | 0,1,2,3 |
| low_volatility_quality – quality_growth_momentum | 不可計算 | 0 | — |
| low_volatility_quality – revenue_growth_acceleration | 不可計算 | 0 | — |

**沒有一對是負相關**，全部落在 0.74–0.91；最低的一對（momentum × value_quality, 0.74）也有 456 天重疊、橫跨全部 4 個 fold，是樣本量最厚的一對。LVQ 和 QGM／RGA 的相關係數完全算不出來，因為兩兩沒有任何 fold 同時 evaluated（LVQ 只有 F0 evaluated，QGM/RGA 的 evaluated folds 是 F3/F2+F3）——這是 candidate-availability 造成的資料缺口，不是「這兩套策略真的無關」的證據。

### 2b. Downside 相關（限市場基準下跌日；用 walk-forward equity 表裡本來就存在、且經核對在同一 fold 內對所有策略完全一致的 `benchmark` 欄位當市場參考）

同樣的 8 對中，可計算的維持 0.65–0.90（momentum×value_quality 從 0.74 降到 0.65，是降幅最大的一對），其餘方向不變。**下跌日相關沒有一對明顯高於全樣本**，意味著目前能看到的少數共同下跌日裡，這幾套策略沒有出現「平時分散、下跌時同步」的擁擠訊號——但重疊天數本就少（多數對僅 40–90 天），這是觀察而非顯著性結論。

### 2c. Drawdown-period 相關（限任一策略自己 ≥5% 回撤期間的日期）

數值與全樣本相近（0.74–0.92），同樣沒有系統性放大，同樣受限於重疊天數。

## 3. Holding overlap（依月重建，見方法論限制）

方法：從 `walkforward_trades`（`fill` 紀錄）依日期重放買賣，得到每個「有成交的日期」持股快照；因為 `missing.execution=partial` 可能讓不同策略的同一次月度再平衡相差一兩個交易日，改用**日曆月**取每月最後一次快照當「這個月的持股」，而不是要求精確同一天。權重是「進場當日的 `reference_notional / 當日 gross_equity`」的近似值（entry-notional，不隨後續價格重新估值）——這是為了不假造一個實際上引擎沒有輸出的逐日 mark-to-market 權重。

- **Jaccard 從未超過 0.25**：最高的一對是 quality_growth_momentum × revenue_growth_acceleration（單月最高 0.241，14 檔重疊），其次是 momentum_trend 與這兩者（各自最高 0.146–0.148）。
- **low_volatility_quality 與 momentum_trend 在整個觀察期內沒有出現過一次共同持股**（overlap_count 恆為 0）——是五套裡持股重疊最少的一對，方向與其定義（低波動防禦 vs. 動能+趨勢）一致。
- **五套同時持有的個股極少**：只在 2026-06 到 2026-09（fold 3，四套 evaluated 的唯一區間）出現過，股票 2408 連續四個月被 momentum_trend / quality_growth_momentum / revenue_growth_acceleration / value_quality 同時選中，2026-09 另有股票 3006 加入；momentum_trend × value_quality 在 2025-06..10 也有零星共同持股（2059、6596）。**沒有任何一個月五套全部同時持有同一檔股票**（因為 LVQ 從未與 QGM/RGA 同期 evaluated）。
- **持股集中度（HHI）普遍偏低**：五套的月度 HHI 落在 0.003–0.046 之間（對應「有效持股數」約 22–300+ 檔的等權近似），QGM 因為 evaluated 樣本少（只有 5 個月）波動較大，其餘四套穩定在 0.005–0.02。沒有任何一套出現持股高度集中的月份。

## 4. Sector exposure / concentration（明確限制：只有現行 `stocks.industry`）

`stocks.industry` 是**現在**的產業分類快照，不是歷史 point-in-time taxonomy（延續 Stage C 已知限制）——一檔 2024 年底持有的股票，這裡顯示的產業可能是它「今天」的分類，不是「當時」的分類。每一筆 sector 數字都帶著 `CURRENT_SECTOR_TAXONOMY_ONLY` 這個 caution 級警告。

在此限制下：sector overlap coefficient（Σ min(權重)，0=完全不同產業組合、1=完全相同）在五套之間介於 0.12–0.37（平均）；**low_volatility_quality 與 value_quality 的產業重疊最低**（平均 0.122，最低到 0.018），**momentum_trend 與 quality_growth_momentum / revenue_growth_acceleration 的產業重疊最高**（平均 0.35+），與第 3 節的持股 Jaccard 排序方向一致——兩個獨立指標（個股、產業）沒有互相矛盾。

## 5. Drawdown complementarity（≤4 個 fold，只描述、不做頻率宣稱）

方法：drawdown episode 定義為單一 fold 內、相對該 fold 自己起點重新歸零的 peak-to-trough，觸及 ≥5% 才算一段（`drawdown.DRAWDOWN_THRESHOLD`）；「fold 邊界不跨接」在 `episodes()`／`drawdown_curve()` 都是從每個 fold 自己的第一天重新起算 peak，測試 `test_drawdown_never_crosses_fold_boundary` 專門驗證一個刻意製造的大幅下跳不會被誤判成延續中的回撤。

具體、真實觀察到的例子（不是頻率、是逐次記錄）：

- **Fold 1（2025-05..10）**：momentum_trend 有 3 段回撤（−5%~−8%），**value_quality 整個 fold 沒有任何一段回撤**；momentum 三次回撤期間 value_quality 的同期報酬分別是 −1.65% / +0.14% / +0.14%，三次都不是「同時回撤」——這是目前資料裡最清楚的一組互補案例。
- **Fold 3（2026-05..09）**：momentum_trend 最深一段回撤 −44%（同期 quality_growth_momentum 為 −0.48%、revenue_growth_acceleration 為 −6.4%，都同步在回撤），但另兩段較淺的回撤（−8.8%、−14.0%）發生時 value_quality 分別是 +6.71%／+1.89%（`other_also_in_drawdown=False`）。
- **Fold 0（2024-11..2025-04）**：momentum_trend 唯一一段回撤深達 −37%、橫跨整個 fold 後段且未在 fold 結束前收復（`recovered=false`）；同期 low_volatility_quality／value_quality 都同步下跌（−4.7%／−1.8%~−18.4%），這段是**同步而非互補**的例子。

三個例子並存：同一組策略在不同 fold 可以是同步、也可以是互補，樣本只有 1–5 段/pair，任何「A 通常對沖 B」的敘述都超出目前證據能支持的範圍。

## 6. Candidate availability complementarity

| Fold | evaluated | no_candidate | active count / 5 | illustrative equal-weight cash fraction* |
|---|---|---|---:|---:|
| 0 | momentum_trend, value_quality, low_volatility_quality | quality_growth_momentum, revenue_growth_acceleration | 3 | 0.40 |
| 1 | momentum_trend, value_quality | low_volatility_quality, quality_growth_momentum, revenue_growth_acceleration | 2 | 0.60 |
| 2 | momentum_trend, revenue_growth_acceleration, value_quality | low_volatility_quality, quality_growth_momentum | 3 | 0.40 |
| 3 | momentum_trend, quality_growth_momentum, revenue_growth_acceleration, value_quality | low_volatility_quality | 4 | 0.20 |

\* 純算術示例：假設五套各佔 1/5、no_candidate 一律轉現金——**不是** Portfolio Lab 的配置規則，只是讓 Stage H 有一個具體數字可以反應。

**四個 fold 都沒有出現「五套同時 no_candidate」**（`any_fold_all_strategies_no_candidate = False`），active count 落在 2–4 之間。如果 Portfolio Lab 真的採用「no_candidate → 現金」的簡單規則，這四個 fold 換算出的組合現金比例會落在 [0.20, 0.60] 這個範圍（同樣是等權示例下的算術結果，不是建議值）——這是 Stage H 需要正面處理的問題：目前的 evaluated fold 數量本來就很少（1–4 個），如果現金比例經常這麼高，會讓 Portfolio Lab 的可用曝險天數進一步縮水。

## 7. Portfolio-Lab readiness data contract（sleeve）

`app/services/cross_strategy/sleeve.py` 定義並用全部五套 × 4 fold（20 個 sleeve）驗證過一次的 schema，`contract_version = "cross-strategy-sleeve/g/1"`：

| 欄位 | 說明 |
|---|---|
| `strategy_id` / `strategy_version_id` / `strategy_version_no` | 對映 `strategy_versions`，來自 job provenance |
| `fold_index` / `evidence_type` | `evidence_type` 恆為 `"independent_fold"` — stitched 永遠不會進入 sleeve |
| `candidate_status` | `"evaluated"` \| `"no_candidate"` |
| `dates` | 該 fold OOS 曝險的完整交易日清單（已排序） |
| `return_by_date` / `equity_by_date` | 逐日報酬、逐日 net_equity |
| `cash_weight_by_date` | `no_candidate` 恆為 1.0；`evaluated` 只在有重新平衡的日期才有值（entry-notional 近似，非逐日 mark-to-market——見第 3 節限制） |
| `target_holdings_by_rebalance_date` | 只在再平衡日期出現（稀疏），`no_candidate` 恆為空 |
| `turnover` / `cost_drag_pct` | fold 層級彙總（來自既有 OOS metrics，非逐日拆解） |
| `warnings` | 該 fold 的 `max_severity` / `counts`（沿用既有 `warning_contract`） |

`sleeve.validate()` 是結構檢查（欄位齊全、`no_candidate` 必須 100% 現金且無持股、`evidence_type` 必須是 independent_fold、日期已排序）；對五套真實 job 的全部 20 個 sleeve **全部通過**（`all_valid = True`）。這個 schema 本身**不計算任何資金配置**，只是把已有的 independent-fold evidence 重新排版成 Portfolio Lab 之後可以逐 sleeve 讀取的形狀。

## 8. Persistence / API

`POST /api/strategy-lab/cross-strategy/analyze`（payload `{"jobs": {strategy_key: job_id, ...}}`）即時運算、**不寫入任何新資料表**，每次呼叫都重新從 `walkforward_jobs/folds/equity/trades` 讀取（透過既有 `walkforward_lab` accessor，未新增 SQL 查詢路徑，只多了一個 `stocks.industry` 的唯讀查詢）。任何策略缺 job_id、job_id 不存在、或某個 fold 尚未完成，都會被記錄在回應的 `failed` / `missing_from_job` 欄位裡並附帶 warning code（`MISSING_STRATEGY_DATA`），不會讓整支請求 500。

## 9. Tests

`tests/test_cross_strategy.py`，24 個，涵蓋 Stage 要求的全部項目：

| 要求 | 測試 |
|---|---|
| correlation date alignment | `test_date_alignment_only_uses_overlapping_calendar_dates` |
| downside correlation | `test_downside_correlation_restricts_to_benchmark_down_days` |
| fold boundary 不被錯接 | `test_fold_boundary_is_never_bridged`、`test_drawdown_never_crosses_fold_boundary` |
| holdings overlap | `test_holdings_reconstruction_and_jaccard`、`test_sell_removes_position`、`test_monthly_snapshot_skips_no_candidate_folds` |
| no-candidate / cash semantics | `test_no_candidate_cash_fold_is_excluded_not_zero_correlated`、`test_no_candidate_sleeve_is_all_cash_and_valid` |
| strategy sleeve contract | `test_evaluated_sleeve_has_holdings_and_is_valid`、`test_validate_catches_missing_field` |
| deterministic results | `test_build_all_is_deterministic` |
| missing strategy/fold handling | `test_load_all_reports_missing_job_without_raising`、`test_active_count_and_missing_fold`、`test_mismatched_fold_window_is_flagged`、`test_analyze_handles_one_real_and_one_missing_strategy` |

其中 `CrossStrategyDataTests` 用 temp SQLite + 真的跑一個小型 Walk Forward job（沿用 `test_walkforward_backend.py` 的 fixture 風格），驗證 `data.py` 對 `walkforward_lab` accessor 與 `stocks` 表的實際呼叫方式正確，不只是對 mock 資料。全專案套件 `python -m unittest discover -s tests` **230/230 通過**（含既有 206）。`compileall` 通過。本階段完全沒有跑外部資料源（`sources`/`light`/`deep` smoke 未執行，因為沒有改動任何抓取邏輯）。

## Portfolio Lab blockers（給 Stage H）

1. **Evaluated-fold 樣本天生就少而且不對齊**：LVQ 與 QGM/RGA 從未同期 evaluated，兩兩相關係數算不出來；任何跨這幾對的「分散效果」目前沒有直接證據，只能從已對齊的那幾對（都在 0.74–0.91、全部正相關）外推，外推本身要在 Stage H 明說。
2. **前述 F1.5/F2 report 已知的上游限制原封不動**：unresolved corporate actions 讓 training severity 全面 critical、QGM 的空訊號早期現金、12/12 primary-on-edge——Stage G 沒有新增這些問題，也沒有解決它們，Portfolio Lab 一樣會繼承。
3. **權重永遠是近似值**：holdings overlap／sector exposure／sleeve 的 `cash_weight` 全部用 entry-notional 近似，不是逐日 mark-to-market；Portfolio Lab 如果要做任何跟金額相關的計算，需要先決定要不要重建真正的逐日組合價值（目前引擎沒有輸出）。
4. **Sector 是現在快照**：任何以產業做的分散/重疊敘述都不能宣稱「當時」的產業結構；需要歷史 point-in-time taxonomy 才能做更嚴謹的版本（Stage C 已提過同樣限制）。
5. **No-candidate → cash 只有算術示例，沒有規則**：本報告給出的現金比例區間（[0.20, 0.60]，等權假設）純粹是讓 Stage H 有具體數字，Portfolio Lab 需要自己決定 no_candidate 時的資金去處，本階段刻意不做這個決定。

## Stage H readiness

Cross-strategy 的**描述性**證據（behavior 差異、correlation、holdings/sector overlap、drawdown 互補、candidate availability）已經備齊，且全部可追溯到既有 independent-fold OOS evidence，沒有引入新的假設或重算。Sleeve 資料契約已定義並在五套真實策略上驗證過形狀正確。**尚未開始、也不在本階段範圍**：任何資金配置演算法、re-balancing across strategies、組合層級風險預算或 execution 邏輯——這些留給 Portfolio Lab 階段，且必須先面對上面五個 blocker。
