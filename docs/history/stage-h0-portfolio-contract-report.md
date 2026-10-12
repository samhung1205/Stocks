# Stage H0 — Portfolio Contract + Exact Sleeve Ledger

範圍：只建立 Portfolio Lab 的正確資料／資金契約。**沒有** Portfolio UI、沒有資金權重最佳化演算法、沒有 Financial Planner 整合、沒有重寫 Stage B engine。程式碼：`app/services/portfolio/`（9 個檔案，純函式 + 一個唯讀 DB boundary）；測試：`tests/test_portfolio_contract.py`（45 個，含對真實 DB 的 smoke reconciliation）。全專案 `python -m unittest discover -s tests -p 'test_*.py'` **275/275 通過**（既有 230 + 新增 45）。`compileall` 通過。未跑 `smoke.py sources/light/deep`（未動任何資料源網路層）。

## 1. Exact sleeve ledger（Task 1）

Stage G 的 sleeve（`cross_strategy/sleeve.py`）用「進場當日 reference_notional / 當日 gross_equity」近似權重，明確標註「不是逐日 mark-to-market」，不得直接用於資金配置。本階段從頭重建：

`app/services/portfolio/ledger.py:replay_sleeve(dates, fills, actions, closes, initial_cash)` 純函式，只用三種已存在的資料重放：

1. **`fill` trades**（`walkforward_trades`）：每筆的現金效果完全由自己的欄位決定 —— 買 `cash -= shares*fill_price + commission`、賣 `cash += shares*fill_price - commission - tax`（用 `fill_price` 不用 `reference_price`，因為前者已含滑價，才是 engine 實際扣的錢；`reference_notional` 只是名目金額，slippage_bps≠0 時會偏）。**不需要 `CostConfig`**——完全由已存的欄位反推，不重算成本模型。
2. **`corporate_actions`**（`cash_dividend` / `share_multiplier`）：與 `engine._apply_action` 完全同構——只在持股 >0 時套用；`cash_dividend` 非空即入帳、`share_multiplier` 非空且 >0 即依比例調整股數；兩欄都是空值的「unresolved」事件自然是 no-op，不需要另外複製 engine 的 `known` 分類邏輯。
3. **`daily_quotes.close`**，逐股前向填補：無論 template 的 `execution.price` 設定是 `open` 還是 `close`，equity 逐日估值永遠用收盤價——因為 engine 的 `last_prices`（估值用）在每天迴圈最上方就從 `bar.close` 更新，與成交價欄位無關（已對照 `engine.py` 逐行確認，見 `ledger.py` docstring）。缺收盤價的持股在 `total_equity` 貢獻 0（與 `engine._equity()` 的 `prices.get(sid, 0.0)` 語意一致），但會被列在 `missing_marks`，不會偽裝成「權重 0」。

輸出：每個交易日一筆 `{date, cash, shares_by_stock, mtm_value_by_stock, stock_weight_by_stock, cash_weight, total_equity, daily_return, missing_marks}`。`no_candidate` fold 沒有東西可重放（從未持有任何股票），直接用第 4 節的固定契約：`holdings=empty, cash_weight=1, return=0`，`cash`/`total_equity` 取既有 `net_equity`（應為常數）。

**與既有 independent OOS 對帳（`reconciliation.py`）**：用真實 DB 對五套策略、全部 20 個 sleeve（4 fold × 5 策略）逐日比對 `total_equity` vs 既有 `walkforward_equity.net_equity`：

| 策略 | job_id | fold 結果 | 對帳 |
|---|---|---|---|
| momentum_trend | `9a3612c981e44cf88daabc655abc43ed` | 4 evaluated | 4/4 fold 全部 reconciled，共 460 個交易日全部吻合 |
| value_quality | `098c6aa9252544cc88c910c68c05c490` | 4 evaluated | 4/4 fold 全部 reconciled，共 460 個交易日全部吻合 |
| quality_growth_momentum | `052d3472688740c89d8e4701bbc358cd` | 1 evaluated / 3 no_candidate | 4/4 fold 全部 reconciled |
| revenue_growth_acceleration | `15f5a27ea6174a969ca5c2ebeeefe795` | 2 evaluated / 2 no_candidate | 4/4 fold 全部 reconciled |
| low_volatility_quality | `90f389547ad74c219c0074f5d2570b1d` | 1 evaluated / 3 no_candidate | 4/4 fold 全部 reconciled |

**全部 20 個 sleeve、0 個 mismatch**（容差 `abs<1e-6` 或 `rel<1e-9`，純浮點加總誤差用的鬆度，不是給建模差異留的空間）。這些 job 正是 F1.5／Stage G 已經驗證過的同一批真實資料（folds 含有真實的 unresolved corporate actions、partial rebalance），對帳完全吻合表示：exact ledger 的每一筆現金/股數效果都跟 engine 內部帳本一致，不是巧合或平均後的近似。`tests/test_portfolio_contract.py::PortfolioRealDBReconciliationTests` 把 momentum_trend、value_quality 的全部 fold 與 quality_growth_momentum 的三個 no_candidate fold 釘進 CI 級測試（read-only，查真實本機 DB，job 不存在時自動 skip）。

若對帳失敗，`reconciliation.reconcile()` 回傳 `reconciled=False` 與逐日 `mismatches` 清單（含 `missing_on_one_side` / `value_mismatch` 兩種原因），**不會自動 normalize 或吞掉**——這是 `tests/test_portfolio_contract.py::ReconciliationTests` 專門測試的行為。本階段實際跑出來的結果是全部吻合，沒有觸發這條路徑，但路徑本身有測試覆蓋。

## 2. Portfolio sleeve contract v2（Task 2）

`app/services/portfolio/contract.py`，`contract_version = "portfolio-sleeve/h0/2"`，與 Stage G 的 `cross-strategy-sleeve/g/1` **完全分離、不改舊語意**（舊模組一行未動）：

| 欄位 | 內容 |
|---|---|
| `strategy_id` / `strategy_version_id` / `strategy_version_no` | 同 v1，來自 job provenance |
| `fold_index` / `evidence_type` | `evidence_type` 恆為 `independent_fold`（stitched 永不進入 sleeve，v1/v2 一致） |
| `candidate_status` | `evaluated` \| `no_candidate` |
| `daily` | 逐日陣列，每筆含 `date/equity(total_equity)/daily_return/cash/shares_by_stock/mtm_value_by_stock/stock_weight_by_stock/cash_weight` —— 這是 v2 相對 v1 的核心差異：真正逐日 mark-to-market，不是進場權重 |
| `turnover` / `cost_drag_pct` | fold 層級彙總，沿用既有 `oos_metrics`（v1 同款，未新增逐日拆解——引擎本來就沒有輸出逐日周轉率） |
| `warnings` | 該 fold 的 `max_severity` / `counts`（同 v1） |
| `reconciliation` | v2 新增：對帳結果（`reconciled`, `mismatches`, 容差） |

`validate()` 是結構檢查：欄位齊全、`daily` 已排序、每日 `stock_weight` 加總 + `cash_weight` = 1（容差 1e-6）、`no_candidate` 必須 100% 現金＋空持股＋`daily_return=0`。**不檢查 `reconciliation.reconciled`**（那是呼叫端要自己看的正確性問題，不是 schema 驗證失敗）。對五套策略全部 20 個 sleeve 跑 `validate()`，**全部通過**。

## 3. Cross-strategy netting contract（Task 3）

`app/services/portfolio/netting.py`。輸入永遠是呼叫端（H1）給的策略層資金配置 `{strategy_key: fraction}`；本階段不產生這個配置，只定義配置存在之後怎麼合併：

- `aggregate_stock_exposure(strategy_allocation, sleeve_stock_weight)`：`aggregate[stock] = Σ_strategy allocation[s] * sleeve_stock_weight[s][stock]`——多套策略同時持有同一股票時直接相加成單一 portfolio exposure。
- `strategy_contribution_by_stock(...)`：同樣的數字拆成 `{stock: {strategy: contribution}}`，方便追蹤某檔股票曝險的來源。
- `total_cash(...)`：策略缺 sleeve 資料時，該策略那一份預設當作 100% 現金（呼應第 4 節「no_candidate 留現金」的精神，寧可少曝險不可亂曝險）。
- `max_single_stock_exposure_hook` / `sector_exposure_hook`：只回報數字（含是否超過呼叫端傳入的 `limit`），**不做任何強制或再平衡**——沒有配置引擎，這兩個是給 H1 用的檢查點，不是本階段的規則。`sector_exposure_hook` 明確標註用的是 `stocks.industry` 現況快照（延續 Stage G 第 4 節的限制揭露）。

## 4. No-candidate baseline（Task 4）

`app/services/portfolio/baseline.py`，`apply_baseline(strategy_allocation, candidate_status)`：`no_candidate` 策略的整份資金配置留在現金，**絕不自動轉給其他 active 策略**。`candidate_status` 缺該策略的 entry 時，安全預設也是現金（fail-safe 現金，不是 fail-safe 進場）。

Redistribution 定義為具名、**未實作**的未來 policy：`apply_baseline(..., redistribute=True)` 直接 `raise NotImplementedError`，訊息說明這是未來可選項——不會有人不小心傳一個從前是 no-op 的參數就默默啟用重分配。

## 5. Allocation-method readiness（Task 5）

`app/services/portfolio/readiness.py`。五個方法：

- **A. equal_weight** — 只要有 ≥1 個 sleeve 就 `ready`。
- **B. inverse_volatility** — 每個策略的波動只能用**呼叫端已經過 `leakage.prior_only_returns` 過濾**（嚴格早於配置日）的樣本；樣本數 <20 個交易日時該策略回報 `insufficient_evidence`（不是猜一個數字）；全部策略都 ready 才整體 ready。
- **C/D/E. risk_parity / minimum_variance / correlation_aware** — 都需要**完整**的兩兩 pairwise overlap；沿用 Stage G `cross_strategy/correlation.py` 已經定義的「pairwise-complete，最少 5 個重疊交易日」門檻（不新造一個不一致的數字），並在報告與程式碼裡明講**這只是契約下限，不是統計最適門檻**。任一配對重疊天數 = 0（結構上不可計算,如 LVQ×QGM/RGA）→ 整個方法 `unavailable`；重疊天數 >0 但 < 5 → `insufficient_evidence`；缺配對或重疊不足時**絕不**填造相關係數或做 0 插補——`test_covariance_zero_overlap_is_unavailable_never_fabricated` 專門斷言回傳裡沒有 `correlation` 這個 key。

用五套真實策略（同一批 F1.5 job）實跑一次 readiness（腳本輸出，未寫入任何表）：

| 方法 | 狀態 | 原因 |
|---|---|---|
| equal_weight | **ready** | 5 個 sleeve |
| inverse_volatility | **insufficient_evidence** | 4/5 策略 ready（momentum_trend n=359、revenue_growth_acceleration n=116、value_quality n=359、low_volatility_quality n=118，波動 0.0128–0.0231），quality_growth_momentum 在「fold 3 之前」完全沒有 evaluated fold（n=0）→ `insufficient_evidence` 拖垮整體 |
| risk_parity / minimum_variance / correlation_aware | **unavailable** | `low_volatility_quality × quality_growth_momentum` 與 `low_volatility_quality × revenue_growth_acceleration` 重疊天數皆為 0（與 Stage G 報告第 2a 節「不可計算」的兩對完全一致），只要五套都要納入，完整矩陣就缺兩格，方法整體不 ready |

這組數字與 Stage G 報告的 pairwise overlap（118/97/213/456/0/0）逐一核對**完全一致**——不是重新定義，是同一份底層資料的兩種消費方式互相印證。`inverse_volatility` 的例子用 `2026-05-04`（fold 3 的 test_start）當配置日，展示「只用配置日之前的 evidence」的實際效果，而不是空談。

## 6. Portfolio-level execution contract（Task 6）

同樣在 `netting.py`（跟第 3 節共用檔案，因為 netting 的資料結構就是這個 contract 的核心）：

- `net_orders(sleeve_orders)`：`sleeve_orders[strategy][stock]` 是每個 sleeve **各自獨立**算出的目標股數增減；輸出是合併後的單一淨額——五套同時對同一股票一買一賣時直接抵銷（數值上淨額 ≈0 的訂單會被丟棄），不是各自成交五筆。
- `net_transaction_cost(sleeve_orders, prices, cost_config)` vs `naive_summed_cost(...)`：前者只對淨額算一次成本，後者模擬「五套各自獨立下單」的總成本。測試 `test_net_transaction_cost_is_cheaper_than_naive_sum_when_orders_cancel`（完全對沖 → 淨成本 0，naive > 0）與 `test_net_transaction_cost_never_exceeds_naive_sum`（一般情形淨成本 ≤ naive）都通過。

契約明確寫在 docstring：sleeves 先產生各自的 desired exposure/order（step 1）→ portfolio 層 aggregate/net（step 2，本節）→ 才真正送出執行（step 3，屬於 H1，本階段不做）。**不重寫 Stage B engine**——`net_transaction_cost` 只是借用既有 `strategy.costs.calculate` 這個純函式算一次淨額成本，不建立新的執行路徑。

## 7. Point-in-time / leakage guards（Task 7）

`app/services/portfolio/leakage.py`：

- `assert_point_in_time(as_of, dated_inputs)`：任何配置用到的輸入（sleeve 狀態、歷史報酬、風險估計、持股）只要日期晚於 `as_of` 就 `raise LeakageError`，明確指出是哪個輸入。
- `prior_only_returns(returns_by_date, as_of)`：把一段 `{date: return}` 過濾成嚴格早於 `as_of` 的子集，**回傳新 dict**——之後來源字典被修改（例如晚到的 fold 3 結果寫回同一個 series），已經算出來的過濾結果不會被追溯改動（`test_mutating_source_after_filtering_does_not_leak_into_result`、`test_future_oos_mutation_does_not_change_past_allocation_inputs` 兩個測試專門驗證這件事，不是空言）。
- `readiness.inverse_volatility_readiness` 的用法規定呼叫端必須先過這道 filter——`test_inverse_vol_readiness_differs_with_and_without_future_leakage` 是個有牙齒的測試：同一組資料，經過 filter 算出的波動明顯小於未過濾（含一筆未來極端值）算出的波動，證明這道 guard 真的會改變結果，不是擺著好看。

## 8. Tests

`tests/test_portfolio_contract.py`，**45 個**：

| 要求 | 測試 |
|---|---|
| exact sleeve equity 與 independent OOS 對帳 | `PortfolioRealDBReconciliationTests`（真實 DB，momentum_trend/value_quality 全部 fold + quality_growth_momentum 的 no_candidate fold）、`ReconciliationTests` |
| stock weights sum + cash = 1 | `test_stock_weight_plus_cash_weight_sums_to_one`、`test_weight_sum_violation_is_caught` |
| corporate action 後 shares/value 正確 | `test_corporate_action_share_multiplier_and_cash_dividend`、`test_action_on_stock_not_held_is_a_no_op`、`test_unresolved_action_both_fields_none_is_a_no_op` |
| no_candidate = 100% cash | `test_no_candidate_must_be_all_cash_empty_holdings_zero_return`、`test_no_candidate_with_holdings_is_rejected`、`test_no_candidate_sleeve_is_all_cash_and_reconciles`（真實 DB） |
| overlapping stock aggregation | `test_overlapping_stock_aggregation`、`test_strategy_contribution_by_stock_breaks_out_the_same_numbers` |
| opposite sleeve orders 正確 netting | `test_opposite_sleeve_orders_net_to_zero`、`test_opposite_sleeve_orders_partial_netting` |
| net transaction cost contract | `test_net_transaction_cost_is_cheaper_than_naive_sum_when_orders_cancel`、`test_net_transaction_cost_never_exceeds_naive_sum` |
| single-stock exposure | `test_max_single_stock_exposure_hook`、`test_max_single_stock_exposure_hook_empty` |
| missing sleeve | `test_missing_sleeve_does_not_raise_and_defaults_its_cash_to_full` |
| pairwise covariance insufficient evidence | `test_covariance_zero_overlap_is_unavailable_never_fabricated`、`test_covariance_thin_overlap_is_insufficient_evidence`、`test_covariance_one_bad_pair_fails_the_whole_method` |
| inverse-vol uses past only | `test_inverse_vol_readiness_differs_with_and_without_future_leakage` |
| future OOS mutation 不影響過去 allocation inputs | `test_future_oos_mutation_does_not_change_past_allocation_inputs`、`test_mutating_source_after_filtering_does_not_leak_into_result` |
| deterministic contract | `test_deterministic`（ledger）、`test_build_exact_sleeve_is_deterministic_on_real_data`、`test_assess_all_is_deterministic` |

真實 DB 抽驗：`momentum_trend`、`value_quality`（皆 4/4 evaluated fold）與 `quality_growth_momentum`（3 個 no_candidate fold）——三個測試類別讀寫都是唯讀，job 不存在時整組自動 `skipUnless`。全專案 `python -m unittest discover -s tests -p 'test_*.py'` **275/275 通過**（既有 230 + 新增 45）。`python -m compileall app/services/portfolio tests/test_portfolio_contract.py` 通過。

## 9. Remaining blockers（給 H1）

1. **inverse_volatility 目前對五套策略整體是 `insufficient_evidence`**：quality_growth_momentum 在能觀察到的最早一段（fold 0-2）完全沒有 evaluated fold，過濾出的「配置日之前」樣本是空的。這不是程式錯誤，是 Stage G 已知的「QGM 大部分時間持現金」現象在時間序上更嚴格的呈現——H1 必須先決定：evidence 不足的策略要整組排除在 inverse-vol 配置之外，還是退回 equal-weight。本階段刻意不做這個決定。
2. **risk_parity / minimum_variance / correlation_aware 目前對全部五套是 `unavailable`**：只要 low_volatility_quality 被納入，就一定撞到它與 QGM、RGA 零重疊的兩對。H1 若要用這些方法，必須先決定「排除 LVQ」或「縮小到有完整矩陣的子集」——本階段的契約只負責誠實回報不可算，不做這個取捨。
3. **MIN_PAIRWISE_OVERLAP_DAYS=5 與 MIN_VOL_SAMPLE_DAYS=20 都是契約下限，不是統計最適值**（前者沿用 Stage G 既有常數，後者是本階段新訂）——兩個數字都只在 4 個 fold、每個 fold 至多 ~120 個交易日的資料規模下勉強有意義，H1 若要更謹慎的門檻，兩個常數都在 `readiness.py` 單一位置好改。
4. **Sector hook 仍是現況快照**：`sector_exposure_hook` 沿用 Stage G 第 4 節同一個限制，未解決、只是原樣傳遞到 Portfolio 層。
5. **執行契約止於「淨額訂單 + 淨額成本」**：`net_orders`/`net_transaction_cost` 沒有處理下單順序、部分成交、或跨策略的執行優先權——這些是 H1 真正接上 Stage B engine 執行時才需要的問題，本階段只保證「淨額先算對」這個前提。
6. **`daily_return` 的第 0 天定義**：v2 contract 用「相對 `initial_cash`」算 fold 第一天報酬（與 Stage G v1 sleeve「fold 第一天沒有前值、直接捨棄」的慣例不同，那是刻意的，因為 v2 需要一個完整、無缺口的逐日序列給 netting/readiness 使用）——這個差異在 `ledger.py` docstring 和本報告都有揭露，但 H1 若同時用到 v1 和 v2 的報酬序列，要注意兩者第一天的口徑不一樣。

## Stage H1 readiness

Exact sleeve ledger 已對五套策略、全部 20 個 fold 完整重建並與既有 independent OOS 100% 對帳，不是近似值，可以安全作為資金配置的輸入。Portfolio sleeve contract v2、netting/execution 契約、no-candidate baseline、allocation-method readiness、leakage guard 五組契約都已定義並有測試覆蓋（含真實資料驗證）。**尚未開始、也不在本階段範圍**：任何資金配置演算法（equal-weight 以外都還沒有一個可以真的跑的實作，只有「能不能跑」的判斷)、Portfolio UI、Financial Planner 整合、真正串接 Stage B engine 執行淨額訂單——這些留給 H1，且必須先處理上面六個 blocker，尤其是 inverse-vol 與 covariance-based 方法目前對五套真實策略的可用性都不理想這件事。
