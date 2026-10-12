# Stage H1.5 — Portfolio Reliability + Real Validation Gate

範圍：不做 Portfolio UI、不做 Financial Planner、不新增投資策略、不因績效結果調整策略或權重、不實作 risk_parity/minimum_variance/correlation_aware。目標：讓 Stage H1 的 Portfolio backend 在進入 H2 UI 前達到 operationally reliable，並針對既有引擎做一輪真實資料的驗證與補強測試。程式碼變動：`app/services/portfolio_lab.py`（新增 `cancel_research_run`/`resume_research_run`/`recover_interrupted`）、`app/services/portfolio/research.py`（`run_portfolio_backtest` 新增可選的 `cancel_check`）、`app/db/tables.py` + `app/db/migrations.py`（新增 `portfolio_research_runs.cancel_requested` 欄位，schema_migrations v4）、`app/main.py`（lifespan 接上第 4 個 `recover_interrupted`）、`app/api/routes.py`（新增 cancel/resume 兩個 route）。測試：`tests/test_portfolio_lab.py` 新增 21 個（48 → 69），全部針對既有 H1 引擎與新 lifecycle 程式碼，沒有新增或修改任何研究邏輯。全專案 `python -m unittest discover -s tests -p 'test_*.py'` **344/344 通過**（既有 323 + 新增 21）。`compileall` 通過。

## 1. Research-run lifecycle reliability

**Audit 起點**：AGENTS.md 與 Stage H1 report 都明確記錄 `portfolio_lab.py` 缺少 `recover_interrupted()`，是本階段 DOC-1 指出的既知 gap——`strategy_lab`/`optimization_lab`/`walkforward_lab` 三者都有，且都在 `app/main.py` 的 `lifespan` 裡呼叫；`portfolio_lab` 沒有,代表 server 在一個 research run 進行中重啟,那個 row 永遠卡在 `queued`/`running`,無法自動恢復。

**與 Strategy Lab/Optimization/Walk Forward 的關鍵差異**：後三者都有「細粒度、可持久化的進度」——Optimization 逐 grid point 存 `optimization_runs`,Walk Forward 逐 fold 存 `walkforward_folds`,重啟後可以只重跑未完成的部分。Portfolio research run 的引擎（`portfolio/research.py:run_portfolio_backtest`）是**單一、整段的 in-memory 迴圈**：對整個研究窗口的所有交易日逐日重放,只有在**完全成功跑完之後**才在同一個 transaction 裡一次寫入 `portfolio_research_equity`/`portfolio_research_rebalances`,然後才把 run 標記 `completed`。這代表：
- Portfolio research run **沒有「部分完成」的持久化狀態可以恢復**——中途被打斷（無論是例外或是 server 被殺掉）,DB 裡永遠不會有「半份」的 equity/rebalance rows（例外會被外層 `try/except BaseException` 接住並 rollback 所在的 `with engine.begin()` transaction；真正的 server 被殺掉,則連「claim 成功」那個更早的獨立 transaction 都已經 commit,但後面完全沒有東西寫入）。
- 因此「resume」對這個模組沒有「跳過已完成的部分」的意義——resume 的正確定義是「用同一個已經 pin 好的 definition（`portfolio_version_id`／`components_json`／`request_json`）重新跑一次全部」,而不是像 optimization/walkforward 那樣只補跑未完成的 grid point / fold。

**實作**（沿用既有三個 lab 的 lifecycle pattern,不引入 Celery/Redis）：
- `recover_interrupted()`：啟動時把任何 `queued`/`running` 的 `portfolio_research_runs` row 標記 `failed`,`error` 訊息明確標注「interrupted, resumable」——與 `strategy_lab.recover_interrupted()` 的做法完全同構（`backtest_runs` 同樣是單一整段引擎,沒有可恢復的細粒度進度）。已接到 `app/main.py` 的 `lifespan`,順序排在 walkforward 之後,與另外三個一致。
- `cancel_research_run(run_id)`：`queued` 立即取消；`running` 設定 `cancel_requested=1`,由引擎在下一個 per-date checkpoint 自己發現並停止（見下）。對已終結（`completed`/`failed`/`cancelled`）的 run 呼叫會 raise `RuntimeError`（映射到 API 的 409),不可能取消一個已經完成的 run。
- `resume_research_run(run_id)`：只接受 `failed`/`cancelled` 狀態（`completed` 呼叫會 raise `RuntimeError`,見下方「completed run 不得被改動」),把 row 重設回 `queued`（清空 `cancel_requested`/`error`/`started_at`/`finished_at`),呼叫端照常排 `execute_research_run`。因為沒有部分進度,resume 永遠是「用同一份 pinned definition 重新算一次全部」,不是「接著算」。
- `run_portfolio_backtest` 新增可選參數 `cancel_check: Callable[[], bool] | None`,在**每個交易日迴圈最上方**（做任何當日 corporate action/rebalance 工作之前）呼叫一次；一旦回傳 True,迴圈立即停止,回傳的 result 帶 `cancelled: True` 及跑到當下為止的 `daily`/`rebalances`（呼叫端從不持久化這些——見下）。**協作式（cooperative）,不是搶占式**——已經在處理的那一天一定會跑完。這是「若現有架構可安全支援」的最小可行版本：因為引擎是單一 pure function 迴圈,加一個每次迭代檢查一次的 callback 是唯一不需要重寫整個 daily-replay 迴圈就能做到的方式。
- `execute_research_run` 檢查 `result["cancelled"]`：若為 True,只把 row 標記 `cancelled`（連同 `finished_at`）,**完全不寫入任何 equity/rebalance row**——因為這個模組本來就只在「完整、未被取消的一次跑完」才會持久化任何東西,一個被取消的 run 不會留下部分／誤導性的結果。

**真實跑速的誠實揭露**：三個永久保留的真實 Portfolio（Core Five/Momentum+Value/Custom Mix,~22 個月、月度 rebalance,約 460 個交易日）跑一次 research run 在本機測試環境下是**次秒級**完成（見 §7 的 HTTP 端對端 smoke：`create research-run` 後幾乎立刻是 `completed`,`cancel`/`resume` 呼叫時已經來不及、回 409）。這代表：對現有規模的真實資料,「跑到一半被取消」在實務上幾乎不會發生——cancel 主要的實際價值是「取消一個還沒開始的 queued run」與「配合 `recover_interrupted` 處理真正的 server crash」,而不是「打斷一個正在跑的長迴圈」。這不是程式缺陷,是誠實反映目前研究窗口/component 數量下這個引擎有多快；如果未來 component 數量或窗口大幅擴大導致單次 run 變慢,per-date 的 `cancel_check` 已經是現成的掛勾,不需要再改架構。

**In-flight BackgroundTasks 不會造成 duplicate run/evidence**：`execute_research_run` 沿用 Stage H1 就有的 atomic claim（`UPDATE ... WHERE status='queued'` 的 `rowcount`),只有一個呼叫者能把 row 從 `queued` 轉成 `running`；第二個呼叫（無論是 API 的 BackgroundTasks 重複觸發,或是 resume 後又被舊的背景任務追上）在 `claimed=0` 時直接 return,不會重跑、不會產生重複的 equity/rebalance rows（因為 equity 表的 primary key 是 `(run_id, date)`,重複 insert 本來就會失敗,但 claim 機制讓這個情境根本不會發生）。`test_no_duplicate_execution_on_a_second_execute_call` 直接驗證：對同一個已完成的 run 再呼叫一次 `execute_research_run`,metrics 與 equity rows 完全不變。

**Startup recovery 實際接到 app startup**：`app/main.py` 的 `lifespan` 現在依序呼叫 `strategy_lab.recover_interrupted()` → `optimization_lab.recover_interrupted()` → `walkforward_lab.recover_interrupted()` → `portfolio_lab.recover_interrupted()`,四個 lab 對稱。

## 2. Portfolio result contract audit for H2

逐項比對 H2 UI 需要的欄位與目前已有的 read-side API（`get_portfolio`/`get_version`/`list_versions`/`list_research_runs`/`get_research_run`/`get_research_result`/`get_research_equity`/`get_research_rebalances`）：

| H2 需要 | 來源 | 結論 |
|---|---|---|
| portfolio/version identity | `get_portfolio`/`get_version` | 已足夠 |
| component strategies + pinned versions | `_components_view`（JOIN `strategies`,含 `strategy_name`/`template_key`/`strategy_version_id`/`strategy_version_no`/`walkforward_job_id`） | 已足夠 |
| allocation method / fixed weights | `version.allocation_method` + `components[].target_weight` | 已足夠 |
| no-candidate cash | `rebalance.target.no_candidate_cash_allocation`（每次 rebalance payload 內） | 已足夠 |
| strategic cash | `rebalance.target.strategic_cash`（fixed_weight 殘餘) | 已足夠 |
| equity | `get_research_equity` | 已足夠 |
| drawdown | `metrics.max_drawdown`（`strategy_metrics.compute`,沿用 Stage B 公式） | 已足夠 |
| daily cash weight | `equity[].cash_weight` | 已足夠 |
| aggregate holdings | `rebalance.holdings`（sparse,只在 rebalance 日期——與 Stage G `target_holdings_by_rebalance_date` 同一慣例） | 已足夠,見下方說明 |
| strategy contribution by stock | `rebalance.contribution_by_stock` | 已足夠 |
| turnover / costs | `turnover_json`（`total_cost`/`average_equity`/`cost_drag_pct`/`rebalance_count`）+ 每筆 `rebalance.cost_total` | 已足夠 |
| warnings / constraint diagnostics | `warnings_json`（`ALLOCATION_NOT_READY`/`PARTIAL_EXECUTION`)+ 每筆 `rebalance.constraint_diagnostics` | 已足夠 |
| rebalance orders | `rebalance.orders`（含 `delta_shares`/`side`/`cost`/`reference_notional`） | 已足夠 |

**結論：現有 API 已經足夠支撐 H2 UI 列出的全部欄位,不需要新增任何 read-side endpoint（唯一的例外是 lifecycle 的 cancel/resume,已在 §1 補上）。** 没有任何資料需要複製存一份新的——所有欄位都是既有 `portfolio_research_runs`/`portfolio_research_equity`/`portfolio_research_rebalances` 三張表已經存的東西,只是把它們組合讀出來。

**"aggregate holdings" 的已知限制,刻意不補**：holdings 只在 rebalance 日期存一筆（Stage G/H1 就定的慣例）,兩次 rebalance 之間股價會讓實際權重逐日漂移,但目前沒有逐日 holdings 的持久化。這是 Stage H1 report 第 11 節限制 5 就明確揭露的設計（「非 rebalance 日只做 mark-to-market」）,不是本階段的新發現。H2 UI 若要顯示「兩次 rebalance 之間的持股權重漂移」,可以用「上次 rebalance 的股數 + 期間逐日收盤價」在前端／或一個薄的 read-only 組合層重算,不需要後端改變儲存方式——本階段刻意不新增逐日 holdings 表,因為那會是「複製存一份新資料」,違反第 2 節「若已有資料已經存在,只建立 read-side aggregation,不要複製存一份新資料」的指示。

## 3. Constraint interaction validation

`target.resolve_rebalance_target` 的固定順序（Stage H1 report 第 5 節就已經是這個順序,本階段沒有改動）：

```
allocation method -> no-candidate cash baseline -> max_strategy_weight clamp
  -> netting.aggregate_stock_exposure -> max_single_stock_weight clamp -> min_cash haircut
```

Stage H1 的既有測試分別驗證了每一條規則單獨作用,但沒有一個測試三條規則**同時開啟**。新增 `TargetTests::test_combined_constraints_apply_in_fixed_order_no_extra_exposure`：3 個 equal-weight component（a、b 都 100% 押 S1,c 100% 押 S2),同時設定 `max_strategy_weight=0.3`、`max_single_stock_weight=0.4`、`min_cash=0.5`,手算整條 pipeline 的精確數字並逐項斷言：

1. **weight conservation**：`sum(stock_weight) + cash_weight == 1`（誤差 < 1e-9）。
2. **cash >= 0**。
3. **stock exposure 不超過 constraint**：每檔股票 `weight <= max_single_stock_weight + 1e-9`。
4. **strategy clamp 釋出的資金進 cash、不 redistribution**：a/b/c 各自從 1/3 被夾到 0.3,多出來的 0.1 總量進現金,不會讓其他 component 的權重變大（沿用既有單條規則測試的斷言方式,本測試額外驗證這件事在三條規則同時作用時依然成立——用「clamp 後的每檔股票權重 <= clamp 前的 raw aggregate 權重」這個單調性斷言直接證明沒有任何一步把已經釋出的容量還給別人）。
5. **stock clamp 釋出的資金進 cash**：S1 聚合曝險 0.6 被夾到 0.4,S2 維持 0.3 完全不變（沒有被 S1 釋出的容量「補上去」）。
6. **min_cash haircut deterministic**：implied cash（0.3）低於 `min_cash`（0.5）,觸發等比例縮放,`scale = (1-0.5)/0.7`,精確驗證 S1/S2 縮放後的數字。
7. **多 constraint 同時開啟仍不產生額外曝險**：每檔股票最終權重 `<` 未受任何 constraint 影響的 raw aggregate 權重（S1: 0.408 vs 未受限 0.667；S2 同理),證明三條規則疊加只會讓曝險單調下降,不會互相抵消或放大。
8. **diagnostics 可追蹤每次 clamp 原因**：5 筆 diagnostics（3 筆 strategy clamp,各自帶 `component` key；1 筆 stock clamp,帶 `stock_id` key；1 筆 min_cash haircut,帶 `rule: min_cash_proportional_haircut`),每一筆都能明確歸因到觸發它的規則與對象。

另外新增 `TargetTests::test_missing_candidate_status_entry_defaults_to_cash`：`candidate_status` mapping 缺一個 component 的 entry 時（比對應 sleeve 載入失敗的情境),`baseline.apply_baseline` 的既有 fail-safe（`candidate_status.get(strat, "no_candidate")`）確實讓它的整份配額留在現金,不會被誤判成 evaluated。

**結論**：三條 constraint 的組合交互作用行為正確、確定性、可追蹤,沒有「renormalize 讓結果好看」的路徑。

## 4. Point-in-time / fold-boundary validation

`series.build_component_series` 把一個 component 的**所有已載入 fold** 攤平進同一個 `by_date` dict,而 `research.run_portfolio_backtest` 的逐日迴圈對每個日期 `d` 只讀 `component_series[k]["by_date"].get(d, ...)`——**結構上**保證了 PIT：一個日期的 target 只能看到「那個日期所屬的那個 fold」算出來的 sleeve row,不可能被同一個 dict 裡已經載入、但屬於未來 fold 的資料影響,因為查找是用日期當 key,不是照 fold 順序掃描。本階段新增測試把這個結構性保證變成明確的、有牙齒的回歸測試：

- `FoldBoundaryTests::test_no_candidate_to_evaluated_transition_lands_exactly_on_fold_boundary`：合成一個 2-fold 的 component series（fold 0 = 1、2 月,`no_candidate`；fold 1 = 3 月,`evaluated` 且有真實股票權重),兩個 fold 的資料**同時**已經載入同一個 `by_date` dict（跟真實情況一樣——`build_component_series` 一次載入所有已完成 fold）。驗證 1 月、2 月的 rebalance 仍然是 100% 現金,3 月才切到有曝險——`no_candidate → evaluated` 的轉換精確落在 fold 邊界那一天,不早不晚。
- `FoldBoundaryTests::test_evaluated_to_no_candidate_reversion_also_lands_on_fold_boundary`：反向（`evaluated → no_candidate`）同理驗證,套用同一個既有月度 rebalance 契約（下一個合法 rebalance 才生效,不會提前或延後）。
- `SeriesTests::test_build_component_series_reports_date_collision_never_silently_overwrites`：Stage H1 的 `series.py` docstring 已經寫了「fold 邊界理論上不會撞期,但如果真的撞期,回報而不是讓後面的 fold 悄悄蓋掉前面的」——本階段補上這個路徑真的有牙齒的測試（monkeypatch `build_exact_sleeve` 製造一個人工的撞期),確認撞期會被記錄進 `date_collisions`,且第一個 fold 的資料贏,不會被覆蓋。
- **missing sleeve / missing evidence fail-safe 為 cash**：`TargetTests::test_missing_candidate_status_entry_defaults_to_cash`（見 §3）與既有 `baseline.apply_baseline` 的 fail-safe 邏輯共同驗證。
- **stitched evidence 永遠不能成為 Portfolio component**：這是結構性保證,不是 runtime 檢查——`app/services/portfolio/` 與 `portfolio_lab.py` 從未 import `walkforward_lab.get_stitched`,`series.build_component_series` 只呼叫 `contract.build_exact_sleeve`（H0 的 `evidence_type` 恆為 `independent_fold`）。新增 `RealDataAccountingTests::test_component_sleeve_is_always_independent_fold_evidence_never_stitched`,對 `app/services/portfolio/` 全部原始碼 + `portfolio_lab.py` 做字串掃描,確認不存在 `get_stitched`/`stitched_secondary` 的引用——把這個結構性保證釘成一個會在有人不小心接上 stitched 資料時失敗的回歸測試。

**結論**：no future fold evidence 影響較早的 rebalance、candidate_status 只在合法的下一個 rebalance 生效、missing evidence fail-safe 為現金、stitched 資料永遠進不了 Portfolio component——五項全部驗證通過。

## 5. Execution / accounting validation

Stage H1 既有的 `ResearchEngineTests`（合成資料)已經涵蓋：overlapping stock 聚合成一筆 aggregate order（不是 N 筆)、淨額成本比分別付便宜、corporate action 正確套用、缺價遞延（不用捏造價格交易)、insufficient cash 確定性縮小買單、deterministic 重跑結果相同。本階段新增的是**真實資料**上的帳務完整性檢查：

- `RealDataAccountingTests::test_daily_equity_has_no_nan_inf_and_cash_weight_stays_in_bounds`：對一個真實兩元件（momentum_trend + value_quality,2024-11 至 2025-10,約一年）的 portfolio research run,逐日檢查 `total_equity`/`cash` 皆為有限數且 `total_equity > 0`,`cash_weight` 落在 `[-0.01, 1+1e-6]`（下界留一點餘裕給既有、已經在 H1 測試裡被記錄過的「insufficient-cash 確定性縮放可能留下次分錢負殘差」的現實情況,不是新發現的 bug）,`daily_return` 若非 None 也必須是有限數。真實一年期資料跑過,全部通過——equity 沒有 NaN/inf,cash/stock weight 的帳務關係守恆。
- `RealDataAccountingTests::test_provenance_traces_portfolio_version_to_strategy_version_to_wf_job`：確認一個真實 component 的 `strategy_version_id`/`strategy_version_no` 與其 pinned `walkforward_job_id` 的 provenance 完全吻合（Stage H1 建立時就是這樣驗證的,本測試只是把它變成一個明確的、獨立於「run 完成」路徑的回歸測試）。
- **關於 "opposite desired exposures 正確 netting" 與 execution 契約的設計澄清**：H0 的 `netting.net_orders`/`net_transaction_cost`（`app/services/portfolio/netting.py`）定義的是「先算出各 sleeve 各自的 signed order,再加總淨額」這條路徑,在 `tests/test_portfolio_contract.py` 已經測過。但 **Stage H1 的實際引擎（`research.py`）走的是另一條、更早就淨額的路徑**：先在 weight 層用 `netting.aggregate_stock_exposure` 把所有 component 對同一檔股票的曝險加總成單一目標權重,再用這個單一目標權重與目前持股比較,得出**單一筆**目標差額（`_rebalance_orders`）。這代表「一買一賣互相抵銷」這件事,在 H1 引擎裡根本不會出現「先各自產生訂單再抵銷」的中間狀態——從頭到尾只有一個目標權重、一筆差額、一筆成本。這不是 bug,是比 H0 設計的執行契約更早、更乾淨地達成同一個保證（AGENTS.md「Transaction costs apply to the net portfolio order only」),`netting.net_orders`/`net_transaction_cost` 仍然正確、仍然被 H0 自己的測試覆蓋,只是不在 H1 的真實執行路徑上被呼叫——記錄在此以免未來誤判成「H1 沒有用 netting 模組所以有 bug」。
- **corporate actions / missing price / insufficient cash 在真實資料上的案例**：Stage H1 report 第 6 節已經記錄了三個真實 portfolio smoke 跑到的真實案例（股票 5481 連續三個月停牌遞延、Momentum+Value 在 2025-04-01 觸發 insufficient cash 確定性縮放)。本階段沒有重新產生新的真實案例（指示明確要求「不要無必要重跑大量研究」),而是引用並延伸既有證據,額外用 `test_daily_equity_has_no_nan_inf_and_cash_weight_stays_in_bounds` 把這些真實案例跑過的逐日帳務結果收斂成一個可重複執行的回歸斷言。
- **cash 不憑空增加**：結構性保證（`_execute_orders`/`_mark` 都只用已存在的 `cash`/`shares`/`closes`/`corporate_actions` 做加減,沒有任何路徑無中生有增加 cash 或 equity),既有合成測試（`test_insufficient_cash_scales_down_buy_deterministically` 等)與本階段新增的真實資料檢查共同覆蓋。

## 6. Multiple Strategy Portfolio workflow

新增 `RealPortfolioWorkflowTests`,使用**目前保留的三個真實 Strategy Portfolio**（`H1 smoke: Core Five` / `H1 smoke: Momentum + Value` / `H1 smoke: Custom Mix`,id 23/24/25,`archived=0`,由 `scripts/h1_portfolio_smoke.py` 建立,Stage H1 report 第 9 節的證據基礎）：

- **list / open / version history（唯讀,三個原始 portfolio 完全不被修改)**：`test_list_open_version_history_on_the_real_kept_portfolios` 確認三個都在 `list_portfolios(include_archived=False)` 裡、`get_portfolio` 能開啟且 `latest_version.components` 數量正確（Core Five=5),`list_versions`/`get_version` 正確（最新版在前、version 1 抓得到）。`test_real_kept_portfolios_have_readable_completed_research_history` 確認三個都至少有一個 `completed` research run,`get_research_result`/`get_research_equity`/`get_research_rebalances` 都能正常讀出。
- **clone / create new version / old research run unchanged / archive（只作用在 clone 上,原始三個 portfolio 全程不變)**：`test_clone_edit_and_archive_only_ever_touch_the_clone` 對 Momentum+Value clone 出一份獨立的複本,對複本 `update_portfolio`（產生新版本),驗證**原始 portfolio 的 `latest_version` 與 `list_research_runs` 逐 bit 不變**,然後把**複本**（不是原始的）archive 掉,確認 archived 之後版本歷史仍然可讀。測試結束時 `tearDown` 只 archive 測試自己建立的 id（複本),真實的三個永久 portfolio 全程 `archived=0`——已在實作後用一個獨立查詢核對過（見下方驗證結果)。
- **arbitrary component list（短命測試 portfolio,測完 archive)**：`test_arbitrary_short_lived_component_combination_still_runs_end_to_end` 用 `{quality_growth_momentum, value_quality, low_volatility_quality}` 這個**跟三個永久保留 portfolio 都不同**的組合（Core Five 是全部五套、Momentum+Value 是 momentum+value、Custom Mix 是 momentum+RGA+LVQ),建立、跑一次 research run、確認完成、然後 archive,archive 後仍能讀到完整結果——再次證明 engine 對 component 組合完全通用,不需要改任何程式碼。

**驗證**：測試套件跑完後,直接查 DB 確認三個真實 portfolio 仍是 `archived=0`（見下）,version 鏈與 research run 完全沒被本階段的任何測試動過：

```
{'id': 23, 'name': 'H1 smoke: Core Five', 'archived': 0}
{'id': 24, 'name': 'H1 smoke: Momentum + Value', 'archived': 0}
{'id': 25, 'name': 'H1 smoke: Custom Mix', 'archived': 0}
```

## 7. Allocation readiness

未改動任何 allocation 規則,本階段的角色只是重新確認 Stage H1 report 第 4 節的狀態仍然成立：`equal_weight`/`fixed_weight` 可用；`inverse_volatility` 的 engine 實作完整、但對五套真實策略仍是 `insufficient_evidence`（quality_growth_momentum 早期 fold 缺 evaluated 樣本),沒有因為本階段的 real validation 而調整任何門檻或政策——`portfolio/readiness.py` 的 `MIN_PAIRWISE_OVERLAP_DAYS`/`MIN_VOL_SAMPLE_DAYS` 一行未動；`risk_parity`/`minimum_variance`/`correlation_aware` 仍然是 `NotImplementedError`,`allocation.py` 一行未動。沒有 silent fallback 的路徑。

## 8. H2 UI readiness payload

| # | 項目 | 狀態 | 說明 |
|---|---|---|---|
| A | Strategy Portfolio list | ✅ 已足夠 | `GET /portfolios` |
| B | Create / clone / archive | ✅ 已足夠 | `POST /portfolios`、`POST /portfolios/{id}/clone`、`DELETE /portfolios/{id}` |
| C | Portfolio Builder | ✅ 已足夠（有一點可選加強） | 建立/更新時的驗證錯誤已結構化（`ValueError` → 400,訊息明確指出哪個欄位）,足以驅動表單驗證；沒有獨立的「dry validate,不落地」endpoint,若 H2 真的需要即時、不建立草稿的驗證,屬於 H2 階段自己的最小新增,本階段判斷非必要（見 §9 限制 3） |
| D | Version history | ✅ 已足夠 | `GET /portfolios/{id}/versions[/{version_id}]` |
| E | Research run lifecycle | ✅ 本階段補齊 | 新增 cancel/resume；create/list/get 原本就有；`recover_interrupted` 讓「卡住的 run」不再是死路 |
| F | Portfolio result overview | ✅ 已足夠 | `GET .../result`（metrics/warnings/turnover） |
| G | Equity / Drawdown | ✅ 已足夠 | `GET .../equity` + `metrics.max_drawdown` |
| H | Cash / allocation | ✅ 已足夠 | `equity[].cash_weight` + rebalance payload 的 `no_candidate_cash_allocation`/`strategic_cash` |
| I | Aggregate holdings | ✅ 已足夠（sparse,設計如此) | `rebalance.holdings`,見 §2 說明 |
| J | Strategy contribution | ✅ 已足夠 | `rebalance.contribution_by_stock` |
| K | Costs / turnover | ✅ 已足夠 | `turnover_json` + 每筆 `rebalance.cost_total` |
| L | Warnings / constraints | ✅ 已足夠 | `warnings_json` + 每筆 `rebalance.constraint_diagnostics` |

**結論：A–L 全部已經有 API 支援,本階段唯一的後端新增是 E（cancel/resume + startup recovery）。沒有為了本階段而硬加任何非必要的 endpoint。**

## 9. Tests

`tests/test_portfolio_lab.py`：**48 → 69**（新增 21 個）：

| 要求 | 測試 |
|---|---|
| startup recovery | `PortfolioLifecycleRealDBTests::test_recover_interrupted_marks_stuck_queued_and_running_rows_failed_and_resumable`、`test_recover_interrupted_is_a_no_op_on_completed_runs` |
| cancel/resume | `test_cancel_queued_run_is_immediate`、`test_cancel_already_terminal_run_is_conflict`、`test_resume_only_allowed_from_failed_or_cancelled`、`test_cancel_flag_requested_mid_run_is_honored_by_the_engine`、`ResearchEngineTests::test_cancel_check_stops_the_run_and_reports_cancelled`、`test_no_cancel_check_behaves_exactly_as_before` |
| completed run immutable | `test_resume_only_allowed_from_failed_or_cancelled`（completed 不可 resume）、`test_cancel_already_terminal_run_is_conflict`（completed 不可 cancel） |
| no duplicate execution on resume | `test_no_duplicate_execution_on_a_second_execute_call` |
| combined constraints | `TargetTests::test_combined_constraints_apply_in_fixed_order_no_extra_exposure`、`test_missing_candidate_status_entry_defaults_to_cash` |
| fold boundary / PIT | `FoldBoundaryTests`（2 個）、`SeriesTests::test_build_component_series_reports_date_collision_never_silently_overwrites` |
| no stitched sleeve | `RealDataAccountingTests::test_component_sleeve_is_always_independent_fold_evidence_never_stitched` |
| real multi-portfolio workflow | `RealPortfolioWorkflowTests`（3 個） |
| old run immutable after version change | `RealPortfolioWorkflowTests::test_clone_edit_and_archive_only_ever_touch_the_clone`（延伸既有 `PortfolioLabRealDBTests::test_old_run_unchanged_after_new_portfolio_version`） |
| read-side UI payload deterministic | `RealDataAccountingTests::test_daily_equity_has_no_nan_inf_and_cash_weight_stays_in_bounds`、`test_provenance_traces_portfolio_version_to_strategy_version_to_wf_job` |

真實資料 validation 全部重用既有三個 completed portfolio runs（Core Five/Momentum+Value/Custom Mix）與既有五個 Stage F1.5 Walk Forward job,只在 §6 的「短命測試 portfolio」與 §5 的帳務檢查另外跑了兩個小規模、可重複的 research run（一個一年期兩元件、一個半年期三元件),沒有大規模重跑研究。全專案 `python -m unittest discover -s tests -p 'test_*.py'` **344/344 通過**（既有 323 + 新增 21）。`python -m compileall app tests scripts` 通過。另外以 `FastAPI TestClient` 對新增的 `cancel`/`resume` HTTP route 做了一次端對端 smoke（create → run → cancel（409,run 已完成)→ resume（409,run 已完成)→ result → archive),確認 route 層與 `_lab()` 的 404/400/409 映射正確接上服務層的例外類型。

## 10. Remaining limitations

沿用並延伸 Stage H1 report 第 11 節的清單（未變動的項目不重複列出,只列本階段新增或有新資訊的）：

1. **Cancel 對「正在跑」的 run 價值有限**：真實規模（~460 個交易日、最多 5 個 component）下,一次 research run 在本機是次秒級完成,協作式 cancel 幾乎沒有機會在引擎真正跑到一半時介入——它主要保護的是「還沒開始跑的 queued run」與「配合 recover_interrupted 處理真正的 crash」。如果未來研究窗口或 component 數量大幅擴大導致單次 run 變慢（分鐘級以上),per-date 的 `cancel_check` 已經是現成的掛勾,不需要重新設計。
2. **Resume 永遠是整段重算,不是接續**：因為這個引擎沒有像 optimization/walkforward 那樣的細粒度持久化進度,`resume_research_run` 唯一正確、安全的定義是「用同一份 pinned definition 重新跑一次全部」。對目前的跑速這完全不是問題,但如果未來某天研究窗口變得很長、單次 run 很貴,這個設計會需要重新評估（例如引入逐月分段持久化)——本階段刻意不做這個預先優化。
3. **Portfolio Builder 沒有獨立的「dry validate」endpoint**：目前建立/更新 portfolio 的驗證錯誤已經結構化（哪個欄位、什麼原因),足以驅動一個基本的表單驗證體驗,但不能「不落地就先檢查」。這是否需要,取決於 H2 UI 實際的互動設計（例如「即時檢查 fixed_weight 總和」可能想要不送出就驗證),本階段判斷不需要為了這個假設性需求預先加 endpoint。
4. **Aggregate holdings 在兩次 rebalance 之間沒有逐日快照**（沿用 Stage H1 report 限制 5,本階段沒有解決、也沒有必要解決——見 §2）。
5. **`min_cash` 與 `max_strategy_weight`/`max_single_stock_weight` 的組合驗證現在有明確的數值回歸測試**（§3),但仍然只驗證了一組具體數字的排列,不是所有可能輸入組合的形式化證明——如果未來這三個 constraint 的邊界值/精確度需求變嚴格（例如要求金融級別的捨入保證),需要更系統性的 property-based 測試,本階段沒有做到那個嚴謹程度。
6. **inverse_volatility / risk_parity / minimum_variance / correlation_aware 對五套真實策略的可用性沒有改變**（沿用 Stage H0/H1 的已知限制,本階段依指示完全不調整任何門檻或實作)。

## H2 是否 READY

**是。** Lifecycle 可靠性（startup recovery、cancel/resume、completed run 不可變、no duplicate execution)、constraint 組合交互作用、PIT/fold-boundary、execution/accounting 帳務完整性、三個真實 Portfolio 的完整 workflow(list/open/version/clone/archive)都已驗證並有測試覆蓋（21 個新增,344/344 全專案通過）。§8 的 H2 API/欄位對照表顯示：H2 UI 規格書列出的 A–L 全部項目,現有 read-side API 已經足夠支撐,本階段唯一必要的後端新增（research-run lifecycle 的 cancel/resume + startup recovery)已經完成並測試。沒有已知的、會阻擋 H2 開始開發的 backend gap。§10 列出的限制都是已揭露、不影響 H2 UI 開發起步的邊界情況,不是阻擋條件。

本階段依指示到此停止,不開始任何 Portfolio UI 工作。
