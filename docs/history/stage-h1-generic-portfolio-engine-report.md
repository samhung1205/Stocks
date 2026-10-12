# Stage H1 — Generic Strategy Portfolio Engine

範圍：backend only，沒有 Portfolio UI、沒有 Financial Planner 整合。Portfolio Lab **不寫死**目前五套策略——它們只是第一批 real-data validation components。任何未來新增的 Single Strategy 都能直接成為某個 Strategy Portfolio 的 component，不需要改動 Portfolio Engine 任何一行程式碼。程式碼：`app/services/portfolio/`（H0 的 9 個檔案不動，新增 6 個純函式模組）＋ `app/services/portfolio_lab.py`（新的 DB-touching 服務層，1 個檔案）；測試：`tests/test_portfolio_lab.py`（48 個，37 個純邏輯 + 11 個對真實 DB 的整合測試）；真實資料驗證腳本：`scripts/h1_portfolio_smoke.py`。全專案 `python -m unittest discover -s tests -p 'test_*.py'` **323/323 通過**（既有 275 + 新增 48）。`compileall` 通過。新增 3 個真實 Strategy Portfolio（在本機 DB 中永久保留，如同 F1.5 驗證腳本留下的 strategies/jobs 一樣是本階段的證據基礎）。

## 1. Domain model

三張新表（`app/db/tables.py`，`metadata.create_all` 自動建立，無需新的 `schema_migrations` 版本）：

| 表 | 角色 |
|---|---|
| `strategy_portfolios` | 長期身份（如「Core Five」）：`id / name / description / archived / created_at / updated_at` |
| `strategy_portfolio_versions` | 不可變版本：`allocation_method / allocation_config_json / no_candidate_policy / rebalance_json / constraints_json / execution_json / definition_hash` |
| `strategy_portfolio_components` | 每個 component **pin** `strategy_id / strategy_version_id / strategy_version_no / walkforward_job_id`（+ `fixed_weight` 專用的 `target_weight`），`UNIQUE(portfolio_version_id, strategy_id)` |

`walkforward_job_id` 是本階段刻意額外 pin 的一環：H0 的 exact sleeve 契約需要具體的 Walk Forward job 才能重放逐日 mark-to-market；只 pin `strategy_version_id` 不足以取得證據來源，所以 component 同時 pin「哪個策略版本」與「哪份已完成的 OOS 證據」。建立/編輯 component 時，`strategy_version_id`/`strategy_version_no` **不是使用者輸入**，而是從該 `walkforward_job_id` 的 provenance 直接讀出並比對 `strategy_id` 是否吻合（[app/services/portfolio_lab.py:107](../../app/services/portfolio_lab.py#L107) `_validate_components`）——不可能建立一個 component 宣稱「這是策略 X 的版本 3」但實際 pin 到一個屬於策略 Y 的 job。

三個真實 Strategy Portfolio（見第 9 節）目前是 DB 裡僅有的實例，但 domain model 本身對 component 數量、component 的策略身份完全沒有限制——`tests/test_portfolio_lab.py::ResearchEngineTests::test_arbitrary_number_of_components_and_new_strategy_needs_no_engine_change` 用 9 個從未出現在任何硬編碼清單裡的合成策略 key 跑過完整的 rebalance/netting/execution 迴圈。

## 2. Versioning

服務層慣例與 `strategy_lab.py`／H0 的 `strategy_versions` 完全一致：**版本只 INSERT、從不 UPDATE**。以下任何一項改變都會建立新版本（[app/services/portfolio_lab.py:293](../../app/services/portfolio_lab.py#L293) `update_portfolio`）：components、component 版本、allocation method、weights、no_candidate_policy、rebalance config、constraints、execution config。`_definition_hash` 覆蓋全部七個欄位，值沒有實際改變時（例如重送同一份 components）**不會**產生新版本（`test_no_op_update_does_not_create_a_new_version`）。`name`/`description` 是可變的展示欄位，走同一個 row 的 in-place UPDATE，不觸發版本。

`archive_portfolio` 只是軟刪除（`archived=1`），版本、components、以及所有 `portfolio_research_runs` 完整保留（`test_archive_does_not_delete_research_history`，真實 DB 驗證：封存後重新讀取同一個 run 的 metrics 逐 bit 相同）。`clone_portfolio` 建立一個全新、獨立的 portfolio 身份，複製來源版本（預設最新版本，或指定 `version_id`）的完整定義為新身份的版本 1；複製後編輯任一邊都不影響另一邊（`test_clone_portfolio_is_independent`，真實 DB）。

## 3. Generic component contract

Portfolio Engine 的每一層只依賴兩個東西：`portfolio-sleeve/h0/2`（component 的逐日 exact sleeve）與一組不透明的 component key（本階段用 `str(strategy_id)`，從未在任何 engine 程式碼裡出現 template 名稱)：

- `app/services/portfolio/series.py:build_component_series` 把一個 component 的每個 fold（`contract.build_exact_sleeve`）攤平成單一 `{date: {...}}` 序列，供 engine 像讀一般時間序列一樣使用，完全不知道 Walk Forward fold 邊界。
- `app/services/portfolio/allocation.py:resolve`、`app/services/portfolio/target.py:resolve_rebalance_target`、`app/services/portfolio/research.py:run_portfolio_backtest` 全部只收 `component_keys: Sequence[str]` + 對映資料，沒有任何 `if template == ...` 分支。

`ResearchEngineTests` 的 9-component 合成測試與真實 DB 的 5-component「Core Five」、「同一策略同時存在於多個 portfolio」（`test_arbitrary_component_count_and_same_strategy_in_multiple_portfolios`：momentum_trend 的 `strategy_id` 同時是「H1 test: five」與「H1 test: momentum only」的 component）共同證明：新增一個 Single Strategy 唯一要做的事，是建立它自己的 strategy + 跑一個 Walk Forward job，再把 `{strategy_id, walkforward_job_id}` 放進某個 portfolio 的 components 清單——不需要碰 `allocation.py` / `target.py` / `research.py` / `netting.py` 任何一行。

## 4. Allocation methods

`app/services/portfolio/allocation.py`，`resolve(method, component_keys, ...)` 是唯一的 dispatch 點：

- **A. equal_weight**——`1/N`，`N` 永遠是該 portfolio version 的**完整** component 數（不因某些 component 當天 no_candidate 而縮小分母），符合規格「以完整 component list 為基準」。
- **B. fixed_weight**——使用者指定的 `target_weight`（存在 component row，不是另一份 JSON）；`weights >= 0` 且 `sum <= 1`，於**建立 portfolio 當下**就用與 engine 每次 rebalance 相同的驗證函式擋掉非法權重（`_validate_components` 直接呼叫 `allocation.fixed_weight` 做驗證），不會等到真正跑 research run 才失敗。剩餘比例是 strategic cash，從不自動投入。真實驗證：「Custom Mix」（momentum 0.4／RGA 0.35／LVQ 0.15，10% strategic cash）與「Momentum + Value」（50/50）都完整跑出結果（見第 9 節）。
- **C. inverse_volatility**——實作了真正的加權計算（`weight ∝ 1/vol`），但只在 H0 的 `readiness.inverse_volatility_readiness`（餵入已用 `leakage.prior_only_returns` 過濾過的逐日報酬）回報整體 `ready` 時才產生數字；否則回傳 `insufficient_evidence`/`unavailable` 且 `weights={}`，呼叫端（`target.resolve_rebalance_target`）據此**跳過該次 rebalance、維持前一個 target**，不會 silent fallback 成 equal weight 或任何猜測值（`TargetTests::test_inverse_volatility_not_ready_refuses_not_fallback`）。五套真實策略在 H0 報告已確認對這個方法整體是 `insufficient_evidence`（QGM 早期無 evaluated fold），本階段未針對五套策略跑 inverse_volatility 的真實 smoke，原因見第 10 節限制 1。
- **D/E. risk_parity / minimum_variance / correlation_aware**——維持 H0 的 capability/readiness contract，**不強行實作**。`allocation.resolve` 對這三個名字明確 `raise NotImplementedError`；`portfolio_lab.validate_definition` 在**建立 portfolio 當下**就擋掉（不是等到跑 research run 才失敗），錯誤訊息指向 H0 的 readiness contract。

## 5. Portfolio constraints

`app/services/portfolio/constraints.py`。所有 clamp 規則都是**單向**（只把超額部分丟進現金，從不把釋出的額度重分配給別人），且每次 clamp 都留下可追蹤的 diagnostics：

- **`max_strategy_weight`**——完整實作：clamp 每個 component 的 in-market 權重（no-candidate baseline 之後、netting 之前）；`test_max_strategy_weight_constraint_real_run`（真實 DB，momentum_trend + value_quality equal weight，limit=0.3）驗證第一次 rebalance 的 `cash_weight >= 0.4`（2 個 component 各自被砍掉 0.2 excess）。
- **`max_single_stock_weight`**——完整實作：對 netting 之後的**聚合**單一股票權重 clamp（不是任一策略自己的權重），超額同樣丟現金，不分給其他股票。
- **`max_sector_weight`**——刻意**只做 reporting**（沿用 H0 `netting.sector_exposure_hook`，`stocks.industry` 是現況快照非 point-in-time）——本階段不把一個已知非時點正確的資料拿去當真正的約束執行,這與規格「至少完整實作 max_strategy_weight 與 max_single_stock exposure detection」的字面範圍一致。
- **`min_cash`**——額外實作（規格允許但非必須）：若聚合曝險使現金低於門檻，對**全部**股票權重做同一個比例的 haircut，是唯一會「動用真正 renormalization」的規則，且與 no-candidate/no-redistribution 規則明確分開、獨立留下 diagnostics（`constraints.py` docstring 有講清楚這條規則跟上面兩條的差異）。

驗證順序（`target.resolve_rebalance_target`）：allocation method → no-candidate cash baseline (H0 `baseline.apply_baseline`) → `max_strategy_weight` clamp → netting 聚合曝險 (H0 `netting.aggregate_stock_exposure` / `strategy_contribution_by_stock`) → `max_single_stock_weight` clamp → `min_cash` haircut。

## 6. Portfolio target construction / execution

`app/services/portfolio/research.py:run_portfolio_backtest`，逐日重放（純函式，DB 存取全部在呼叫端 `portfolio_lab.py` 做完）：

1. 套用當日 corporate actions（與 H0 `ledger.py` 完全同構的語意：`share_multiplier`/`cash_dividend` 只在持股 > 0 時生效）。
2. **若是 rebalance date**：`target.resolve_rebalance_target` 算出聚合 target 股票權重 → 用 rebalance 前的 equity（今日收盤市值 + 現金）換算目標股數 → 賣單先執行（釋放現金）→ 買單依股票代號排序、若現金不足則**確定性**縮小該筆買單（`_execute_orders`，兩輪重算以吸收 commission 四捨五入的次分錢殘差）並記錄 `insufficient_cash` diagnostic → 目標之外但仍持有的股票全額出清；缺當日收盤價的目標股票或待出清股票一律**遞延**（保留原持股，記錄 `missing_price`/`missing_price_suspended_liquidation`），不會用捏造價格交易。
3. 每日 mark-to-market（缺價持股計入 `missing_marks`、對總市值貢獻 0，同 H0 語意)。

**成本只對淨額訂單收一次**：聚合 target 股票權重已經把「多套策略同時持有同一股票」netting 成一個數字（`netting.aggregate_stock_exposure`），engine 對每一檔股票永遠只算一筆 `costs.calculate`（重用 Stage B 的純函式），不存在「五套 sleeve 各自成本」這條路徑可以走。真實資料證實這一點：`ResearchEngineTests::test_overlapping_stock_nets_to_one_order_not_n`（合成）與真實 smoke 的 `overlap_stock_rebalance_incidences`（Core Five 195 次、Custom Mix 107 次、Momentum+Value 12 次——見第 9 節）都是「多個策略對同一股票的 contribution 被算進同一筆聚合 exposure」的直接計數。

三個真實 portfolio 的 smoke 都在真實資料上撞到了 partial execution 的兩種情境：
- **missing price（停牌/無報價）**：股票 5481 在 2025-06/07/08 連續三個月被三個 portfolio 同時遞延（同一檔股票、同一批月份，三個獨立 portfolio 各自重放出一致的結果，互相印證引擎行為一致）。
- **insufficient cash**：「Momentum + Value」在 2025-04-01 對股票 9945 觸發現金不足，買單從 24.76 股確定性縮小到 22.24 股，`cash_shortfalls` diagnostic 完整記錄請求量與實際執行量。

## 7. No-candidate handling

第一版只正式支援 `cash_hold`（沿用 H0 `baseline.apply_baseline` 不變）。`redistribute_active` 在 schema 上保留但**建立 portfolio 當下**就被 `_validate_no_candidate_policy` 明確拒絕（訊息說明是保留給未來版本），`research.run_portfolio_backtest` 自己也在執行時再檢查一次（`IMPLEMENTED_NO_CANDIDATE_POLICIES`）——雙重防呆，不會有任何路徑能讓一個未實作的 policy 悄悄跑起來。真實驗證：Core Five 在全部 23 次 rebalance 都有 no-candidate 現金（`no_candidate_cash_rebalance_count=23`，因為 QGM/LVQ 五套裡總有一套 no_candidate），其中三個抽樣日期的現金比例（40.9% / 60.3% / 21.4%）與 Stage G 報告第 6 節「等權示例」表格的三個對應 fold（0.40 / 0.60 / 0.20）**幾乎完全吻合**——這不是巧合對齊，是兩份獨立計算（Stage G 的算術示例 vs. Stage H1 真正跑過完整 netting/execution 的引擎）互相印證同一組 candidate-availability 資料。Momentum+Value 兩個 component 全程都是 evaluated，`no_candidate_cash_rebalance_count=0`，行為符合預期。

## 8. Persistence / API

`app/services/portfolio_lab.py`，REST 層在 `app/api/routes.py`（沿用既有 `_lab()` 400/404/409 error mapping）：

| Method | Path | 對應規格 |
|---|---|---|
| GET/POST | `/api/strategy-lab/portfolios` | list / create |
| GET/PATCH/DELETE | `/api/strategy-lab/portfolios/{id}` | get / update（可能建版本）/ archive |
| POST | `/api/strategy-lab/portfolios/{id}/clone` | clone |
| GET | `/api/strategy-lab/portfolios/{id}/versions[/{version_id}]` | list / get versions |
| POST/GET | `/api/strategy-lab/portfolios/{id}/research-runs` | create（`BackgroundTasks` 排入執行）/ list |
| GET | `/api/strategy-lab/portfolio-research-runs/{run_id}[/result\|/equity\|/rebalances]` | run 狀態、結果（metrics/warnings/turnover）、逐日 equity、稀疏的逐次 rebalance 明細 |

`portfolio_research_runs` 是不可變 research run（pin `portfolio_id / portfolio_version_id / components_json`(含 pinned strategy versions) `/ allocation_method / allocation_config_json / no_candidate_policy / constraints_json / execution_json`），`portfolio_research_equity`（逐日）與 `portfolio_research_rebalances`（**只在 rebalance 日期**存一筆 JSON blob，含 holdings/contribution/orders/constraint diagnostics——沿用 Stage G「稀疏、只在 rebalance 日期」的既有慣例）。`test_old_run_unchanged_after_new_portfolio_version`（真實 DB）：對同一個 portfolio 建立新版本後，舊 run 的 `portfolio_version_id` 與 `metrics` 完全不變。

## 9. Real-data validation：三個 Strategy Portfolio

全部使用 H0/F1.5/G 已驗證過的同一批五個真實 Walk Forward job，研究窗口 `2024-11-01..2026-09-18`（F1.5 全窗口），月度 rebalance（22 個月 → 23 次 rebalance），`scripts/h1_portfolio_smoke.py`：

| Portfolio | Method | Components | CAGR% | Sharpe | MaxDD% | 年化周轉% | 總交易成本 | Cost drag% |
|---|---|---|---:|---:|---:|---:|---:|---:|
| Core Five | equal_weight | 全部五套 | 14.18 | 0.83 | -21.28 | 816.5 | 20,169 | 1.92 |
| Momentum + Value | fixed_weight 50/50 | momentum_trend, value_quality | 23.70 | 0.90 | -32.05 | 1359.8 | 33,816 | 3.11 |
| Custom Mix | fixed_weight 0.4/0.35/0.15 | momentum_trend, revenue_growth_acceleration, low_volatility_quality | 23.73 | 1.09 | -25.87 | 969.1 | 24,932 | 2.24 |

（這些數字是「把已驗證過的 OOS 證據重新聚合並模擬每月 rebalance」的結果，不是新的策略評分或排名——三個 portfolio 之間刻意不比較優劣，指令明確要求本階段不做這件事。）

- **Equity / cash**：三個 portfolio 起始 equity 皆為 999,000～999,500（初始 100 萬扣掉首次 rebalance 的交易成本），Core Five 期末（2026-09-18）equity 1,282,119、cash_weight 21.4%；Custom Mix 期末 equity 1,491,060；Momentum+Value 期末 equity 1,489,849、cash_weight 僅 1.4%（兩個 component 全程滿倉，幾乎沒有 strategic/no-candidate cash）。
- **Holdings**：Core Five 在三個抽樣 rebalance 日期分別持有 128/99/142 檔股票（五套策略的聯集，含大量小權重長尾持股）；Custom Mix（三套）持有 80/50/93 檔；Momentum+Value（兩套）持有 98/99/98 檔。
- **Stock overlap / netting**：`overlap_stock_rebalance_incidences`（一檔股票在某次 rebalance 被 ≥2 個策略同時貢獻曝險的次數）Core Five 195 次、Custom Mix 107 次、Momentum+Value 12 次——與 component 數量、策略間持股重疊程度（呼應 Stage G 第 3 節的 Jaccard 排序）方向一致。
- **Transaction cost**：三個 portfolio 的 cost drag 1.9%–3.1%，Momentum+Value 最高（兩套策略都全程投入、周轉率最高）。
- **No-candidate cash 行為**：見第 7 節，Core Five 與 Custom Mix 的現金比例走勢跟 Stage G 報告的獨立算術示例高度吻合；Momentum+Value 全程無 no-candidate 現金。
- **Provenance**：每個 portfolio 的 `latest_version.components` 都完整帶著 `strategy_id / strategy_version_id / strategy_version_no / walkforward_job_id`（見上方 API smoke 輸出），可以逐一回溯到 H0/F1.5 已驗證過的原始證據。

## 10. Tests

`tests/test_portfolio_lab.py`，**48 個**：

| 要求 | 測試 |
|---|---|
| 任意 component 數量／新策略不需改 engine | `test_arbitrary_number_of_components_and_new_strategy_needs_no_engine_change`（9 個合成策略）、`test_equal_weight_is_generic_over_arbitrary_component_count` |
| 同一策略存在於多個 portfolio | `test_arbitrary_component_count_and_same_strategy_in_multiple_portfolios`（真實 DB） |
| 不可變版本／pin 的 component 版本 | `test_immutable_versions_and_new_version_on_edit`、`test_pinned_component_version_rejects_wrong_strategy`（真實 DB） |
| equal weight／fixed weight／殘餘 strategic cash | `AllocationTests`（9 個）、`test_fixed_weight_real_run_respects_target_weights`（真實 DB） |
| no_candidate → cash／不重分配 | `TargetTests::test_equal_weight_no_candidate_goes_to_cash_not_redistributed`、`test_no_candidate_policy_other_than_cash_hold_rejected`、`test_equal_weight_real_run_completes_with_provenance_and_no_candidate_cash`（真實 DB） |
| 重疊股票聚合／策略貢獻歸因 | `test_overlapping_stock_aggregation_and_contribution`、`test_overlapping_stock_nets_to_one_order_not_n` |
| 淨買賣單／淨成本 | `test_net_cost_cheaper_than_paying_for_each_component_separately` |
| max_strategy_weight／max_single_stock 曝險 | `ConstraintsTests`（8 個）、`TargetTests::test_max_strategy_weight_flows_through_to_stock_weight`／`test_max_single_stock_weight_enforced`、`test_max_strategy_weight_constraint_real_run`（真實 DB） |
| corporate actions | `test_corporate_action_share_multiplier_and_dividend_applied_to_portfolio_ledger` |
| 缺執行／partial execution | `test_missing_price_defers_trade_not_fabricated`、`test_insufficient_cash_scales_down_buy_deterministically` |
| provenance | `test_equal_weight_real_run_completes_with_provenance_and_no_candidate_cash`（真實 DB，斷言 run 的 `components` 快照與 portfolio 的 `latest_version.components` 一致） |
| point-in-time leakage | `TargetTests::test_inverse_volatility_not_ready_refuses_not_fallback`、`SeriesTests::test_prior_returns_excludes_as_of_and_future`（沿用並組合 H0 `leakage.py` 已驗證過的 guard，未重造一份） |
| 舊 run 在新版本後不變 | `test_old_run_unchanged_after_new_portfolio_version`（真實 DB） |
| clone portfolio | `test_clone_portfolio_is_independent`（真實 DB） |
| archive 不刪除 research history | `test_archive_does_not_delete_research_history`（真實 DB） |
| deterministic | `ResearchEngineTests::test_deterministic` |

真實 DB 抽驗（`PortfolioLabRealDBTests`，11 個，job 不存在時整組自動 `skipUnless`）建立、跑完、再讀回的 portfolio/run 全部是這次測試自己建立的（測試結束於 `tearDown` 中封存，不留作永久證據——與第 9 節的三個 smoke portfolio 刻意保留不同）。全專案 `python -m unittest discover -s tests -p 'test_*.py'` **323/323 通過**（既有 275 + 新增 48）。`python -m compileall app tests scripts` 通過。未跑 `smoke.py sources/light/deep`（未動任何資料源網路層）。

## 11. Remaining limitations

1. **Rebalance 頻率只實作了 `monthly`**（`app/services/portfolio/calendar.py`，「每個共同交易日集合中，每個日曆月第一個」）。`rebalance_json` schema 允許未來擴充，但 `weekly`/`quarterly`/自訂日期清單等在 Stage H1 會被 `calendar.rebalance_dates` 明確 `raise NotImplementedError`,不會被 portfolio 建立流程悄悄接受。
2. **inverse_volatility 沒有對五套真實策略跑 smoke**：H0 報告已確認整體是 `insufficient_evidence`（QGM 早期無 evaluated fold），Stage H1 的 `allocation.inverse_volatility` 實作本身在合成資料上有測試覆蓋（`test_inverse_volatility_ready_weights_favor_lower_vol`／`test_inverse_volatility_not_ready_refuses_not_fallback`），但沒有一個真實 portfolio 用這個方法跑出完整結果，因為現有五套策略的證據量還不支持——這不是程式缺陷，是資料現況,與 H0 report 的 blocker 1 一致，留給之後策略累積更多 evaluated fold 後再驗證。
3. **risk_parity / minimum_variance / correlation_aware 完全未實作**（只有 H0 的 readiness contract），在建立 portfolio 與執行 research run 兩個時間點都會被明確拒絕，不是本階段範圍。
4. **`max_sector_weight` 只做 reporting，不執行**：`stocks.industry` 現況快照的限制原封不動繼承自 H0/Stage G，本階段沒有解決、也沒有假裝解決。
5. **Rebalance 之間沒有「盤中」重新評估**：portfolio 只在 rebalance 日期重新算 target 並交易，非 rebalance 日只做 mark-to-market——這是刻意的簡化（月度 rebalance 是目前唯一實作的頻率），但代表兩次 rebalance 之間即使某個 component 的 candidate_status 中途改變（例如從 no_candidate 變 evaluated），portfolio 要等到下一個月度 rebalance 日才會反應。
6. **沒有分開 gross/net equity 概念**：portfolio 是純現金結算、無槓桿，`gross_equity` 與 `net_equity` 在 engine 輸出裡永遠相等（直接重用 Stage B `metrics.compute` 的介面，但兩個欄位本階段沒有實質差異）——如果未來要支援槓桿/放空，這裡需要重新設計。
7. **`min_cash` 的比例 haircut 是本階段唯一會做「真正 renormalization」的規則**，且只作用在全體股票權重、不區分策略來源——如果同時設定 `min_cash` 與 `max_strategy_weight`/`max_single_stock_weight`，三者的交互作用（例如 haircut 後某檔股票的權重又低於原本被 clamp 的原因）沒有專門測試覆蓋，Stage H1 只保證每條規則各自正確、套用順序固定，沒有驗證所有排列組合。
8. **`execution.costs` 目前只能整包覆寫 `CostConfig` 的五個欄位**（沿用 Stage B 既有 dataclass），沒有替 portfolio 層另外設計一套獨立的費率模型——刻意重用，不建立第二套互相矛盾的成本模型，但也代表 portfolio 沒有「依策略/依股票不同費率」的能力。

## H1.5 readiness

Domain model（strategy_portfolios / versions / components，全部 pin 到具體的策略版本與 Walk Forward job 證據）、versioning（純 INSERT，舊 run 永遠指向原版本）、generic component contract（對 component 數量與身份完全通用，9-component 合成測試與「同一策略跨多個 portfolio」真實測試都證實）、三個第一版 allocation methods（equal_weight／fixed_weight 完整可跑，inverse_volatility 有真正實作但等待更多真實證據；risk_parity/minimum_variance/correlation_aware 維持 H0 的誠實 readiness contract）、constraints（max_strategy_weight／max_single_stock_weight 完整實作且有真實資料驗證，max_sector_weight 刻意只做 reporting）、target construction／execution（月度 rebalance、netting 後才計費、partial execution／insufficient cash／corporate actions 全部在真實資料上跑出真實案例）、no-candidate handling（cash_hold 唯一實作，真實現金比例與 Stage G 獨立算術示例互相印證）、persistence/API（CRUD + clone + archive + research run 生命週期完整）都已完成並有測試覆蓋（48 個，含 11 個真實 DB 整合測試）。三個真實 Strategy Portfolio（Core Five／Momentum+Value／Custom Mix）已在本機 DB 建立並完整跑出 research run，作為之後階段可以繼續使用的真實證據。**尚未開始、也不在本階段範圍**：Portfolio UI、Financial Planner 整合、週/自訂頻率 rebalance、inverse_volatility 與三個 covariance-based 方法的真實資料驗證、`max_sector_weight` 的真正 point-in-time 執行——這些留給 H1.5，且第 10 節列出的八個限制都應該在啟動前被讀過。
