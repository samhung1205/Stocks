# Stage I1 — Financial Planner × Strategy Portfolio Backend Integration

範圍：**backend only**。實作 [Stage I0 contract](stage-i0-planner-integration-contract-report.md) 定義的最小 integration surface；`web/finance.html`/`web/js/finance.js` 完全未動，沒有做任何 Planner UI redesign，沒有修改 Strategy Portfolio research semantics（`app/services/portfolio/`、`app/services/portfolio_lab.py` 一行未改）。核心 invariant 貫穿全部程式碼與測試：**Strategy Portfolio 的 historical/OOS evidence 與 Planner 的 future planning assumption 完全分離，沒有任何一條路徑會把 CAGR 悄悄寫進 `expected_return`。**

I0 report 與現有 source/AGENTS.md 沒有發現衝突；I0 提出的形狀（六個 nullable 欄位、opaque key 慣例、evidence/assumption 分離）與現有 `app/services/finance/*`、`portfolio_lab.py` 的既有慣例（immutable versions、`_lab()` 400/404/409 映射、no-silent-fallback）完全吻合，直接照 I0 的設計落地，沒有需要偏離的地方。

## 1. Migration / Schema

`schema_migrations` **v5**（[app/db/migrations.py](../../app/db/migrations.py)），與 v3/v4 同一慣例（`PRAGMA table_info` 檢查缺欄位再 `ALTER TABLE ADD COLUMN`，全部 nullable，不 backfill）：

```sql
ALTER TABLE finance_targets ADD COLUMN source_type TEXT;
ALTER TABLE finance_targets ADD COLUMN strategy_portfolio_id INTEGER;
ALTER TABLE finance_targets ADD COLUMN portfolio_version_id INTEGER;
ALTER TABLE finance_targets ADD COLUMN research_run_id TEXT;
ALTER TABLE finance_targets ADD COLUMN assumption_source TEXT;
ALTER TABLE finance_targets ADD COLUMN assumption_evidence_ref TEXT;
```

`finance_targets` 的既有 PK `(profile_id, stock_id)` 完全沒動。已依 AGENTS.md 的 DB 備份規則，用 SQLite backup API（`sqlite3.Connection.backup()`，不是 `cp`）在本機 `data/stocks.db` 套用 migration 前建立 `data/stocks.pre-stage-i1.db`；套用後確認既有 2 筆 `finance_profiles`／26 筆 `finance_targets` 完全保留，`schema_migrations` 從 4 筆變 5 筆。三個依賴「schema_migrations 剛好 4 筆」的既有測試（`test_data_correctness.py`／`test_data_foundation.py`／`test_walkforward_contract.py`）已更新為 5（這是新增一版 migration 的必然結果，不是行為改變）。

## 2. Source model

`app/services/finance/service.py::_prepare_target()` 是新的、唯一的驗證/正規化入口，在 `save_profile()` 真正寫入 DB 之前，對每一筆 target 跑完所有驗證與唯讀查詢（`portfolio_source.validate_pin`/`evidence`），驗證失敗直接 `ValueError`，不落地任何一筆資料。

- **`source_type IS NULL`／`'etf'`**：完全沿用現況行為，`_prepare_target` 的 `else` 分支跟過去一模一樣（`stock_id` 就是使用者填的字串，`expected_return`/`actual_value` 照舊）。
- **`'manual'`**：一樣走 `else` 分支（`stock_id` 是任意字串），但**明確拒絕**任何 pin 欄位（`strategy_portfolio_id`/`portfolio_version_id`/`research_run_id` 出現就 400）。真正的行為差異在 §4 的 return-fallback，不在這裡。
- **`'strategy_portfolio'`**：`stock_id` 由伺服器端用 `universe.portfolio_key(strategy_portfolio_id)`（即 `"portfolio:<id>"`）衍生，**完全不信任呼叫端自己組字串**——呼叫端甚至不需要在 payload 裡帶 `stock_id`。`portfolio_source.validate_pin()` 直接重用既有的 `portfolio_lab.get_version`/`get_research_run`（不是重造一份查詢邏輯）：version 必須屬於該 portfolio、research_run（若給了）必須屬於同一個 version，任一不吻合都是 `ValueError` → API 層 400，不猜、不自動挑最新版本。

`"portfolio:"` 前綴是保留字：任何非 `strategy_portfolio` 來源若 `stock_id` 剛好長這樣會被拒絕（`test_stock_id_prefix_is_reserved_for_strategy_portfolio_source`），避免使用者手動輸入的自訂代號意外撞到這個慣例。

`_names()`（[service.py](../../app/services/finance/service.py)）與 `_portfolio_actuals()` 都已修正：前者過濾掉 `is_portfolio_key()` 的 key 才去查 `stocks` 表（原本會白跑一次注定落空的查詢，現在直接跳過）；後者**在函式自己內部**（不只依賴呼叫端先濾過）排除 portfolio key，這是測試過程中抓到的真實 gap——最早只在 `simulate()` 呼叫端過濾，直接對 `_portfolio_actuals()` 灌一個 portfolio key 仍然會被 journal 假資料污染，補上內部防呆後才通過 `test_no_journal_positions_fallback_for_strategy_portfolio`。

## 3. Strategy Portfolio evidence read API

`app/services/finance/portfolio_source.py`（新模組，finance 套件裡第二個碰 DB 的模組，只透過 `portfolio_lab` 既有的唯讀函式）：

```
GET /api/finance/portfolio-evidence?strategy_portfolio_id=&portfolio_version_id=&research_run_id=
```

`portfolio_source.evidence()` 組裝：portfolio identity/`archived`、pinned version、components（含 pinned strategy version）、allocation method、research window、CAGR/Sharpe/MaxDD/annualized_volatility/sortino/total_return（直接來自 `strategy_metrics.compute()` 既有欄位，原樣透傳，沒有另外算一套）、cash 行為（`last_cash_weight`/`avg_cash_weight`，從 `get_research_equity()` 現算，不新建表）、turnover/cost drag（`get_research_result().turnover`）、warnings。

- `research_run_id=NULL` → `{"research": {"available": false, "reason": "no_research_run_selected"}}`，identity/components 仍然完整回傳；HTTP 200，不是 500（`test_evidence_route_no_research_run_returns_200_not_500`）。
- `research_run_id` 存在但不屬於這個 portfolio/version、還沒完成、或整個查無此 run，一律回 `available: false` + 對應 `reason`（`research_run_mismatch`/`run_status_<status>`/`research_run_not_found`），**同樣不是錯誤**——因為這是一個唯讀顯示端點，讀不到證據是正常狀態,不是使用者做錯了什麼。
- portfolio/version 本身查無此 id 才是真的 404（透過 `app/api/routes.py` 既有的 `_lab()` helper，跟 Strategy Portfolio 自己的 API 用同一套 400/404/409 映射；`finance_save_profile` 這條路由也补上了同一個 `_lab()` 包裝，因為它現在會拋 `ValueError`，之前完全沒有錯誤映射，會變成未處理的 500）。

**沒有把任何 evidence 數字複製存進新的 finance table**——`evidence()` 每次呼叫都重新讀 `portfolio_lab` 既有的表；`assumption_evidence_ref`（§4）是唯一的例外，且明確是 audit snapshot 而非 evidence 的另一份副本。

## 4. Planning assumptions

`finance_targets.expected_return` 欄位形狀完全沒變。新增 `assumption_source`（`NULL`/`'manual'`/`'scenario'`/`'research_informed'`）：

- `manual`/`scenario`：只是標籤，沒有新邏輯——`scenario`（conservative/base/optimistic）本來就是 Planner 既有的 `assumptions.return_*` 三個常數,I1 沒有新增任何情境模型。
- `research_informed`：`_prepare_target()` 同時檢查三個條件都成立才放行：`source_type='strategy_portfolio'`、payload 明確帶了非空的 `expected_return`、`research_run_id` 指向一個 `evidence().research.available=True` 的 run。三個條件任一沒滿足都是 `ValueError`（`test_research_informed_requires_explicit_expected_return`、`test_research_informed_requires_evidence`）。

**最關鍵的 regression guard**：`_prepare_target()` 在算完 `assumption_evidence_ref`（audit snapshot）之後，寫入 DB 的 `expected_return` 欄位**只可能等於呼叫端一開始就送進來的那個值**——程式碼裡沒有任何一行把 `ev["research"]["metrics"]["cagr"]` 指派給 `expected_return`。`test_expected_return_never_auto_filled_from_cagr`／`test_research_informed_valid_case_and_evidence_ref_is_audit_snapshot` 直接驗證這件事：後者刻意讓使用者選一個跟 evidence CAGR 不同的數字（`cagr/2`），存檔後 `expected_return` 與 `assumption_evidence_ref["snapshot_cagr"]` 確實不相等——證明 snapshot 只是紀錄「使用者當時看到什麼」，不是驅動 `expected_return` 的活綁定。

`assumption_evidence_ref` 的內容（`portfolio_version_id`/`research_run_id`/`snapshot_cagr`/`snapshot_asof`）由伺服器端在 `save_profile()` 呼叫 `evidence()` 之後**當場組出來**，不接受呼叫端自己送這幾個欄位——避免任何偽造稽核紀錄的空間。

## 5. Missing planning return

`app/services/finance/universe.py` 新增 `ROLE_DEFAULTS["strategy_portfolio"]`，`assumed_return: None`——`universe.meta()` 對 `"portfolio:"` 前綴的 key 一律分類成這個角色，所以 `universe.default_return()` 對它天生就回傳 `None`,不需要額外的特殊分支。`manual` 沒有專屬角色可用（跟舊有的自訂 ETF 代號用同一個 `meta()` fallback，避免動到既有行為），所以 `allocation.allocate()` 新增 `no_role_default: set[str]` 參數，`simulate()` 對 `source_type='manual'` 的 target 餵進這個集合，強制跳過 role 預設。

`allocation.allocate()` 的組合層 `expected_return` 計算改成**排除**任何 `expected_return is None` 的 row（保留它的 `weight`/`initial_amount`/`monthly_amount`，只是不進加權平均的分子分母）——原本的寫法會把「沒設定假設」的標的當成報酬率貢獻 0 灌進分母，等同於一種 silent fallback。`allocation.warnings()` 新增一則 `{"level": "assumption", "message": "...：尚未設定假設報酬（planning return not configured）..."}`，只要有 weight>0 且 unpriced 的標的就一定出現。

## 6. Scenario semantics（既有 use_portfolio_return 的實際行為）

沒有新增 volatility-based simulation、Monte Carlo，或用歷史報酬序列驅動投影——`compound.scenarios()` 一行沒動，`simulate()` 仍然只吃一個純量年化報酬率。

發現的實際衝突與最小改動：`assumptions.use_portfolio_return=True`（預設值）時，`base = alloc["expected_return"]`——如果使用者選擇的所有標的都還沒設定假設報酬（§5 的 unpriced 情境），`alloc["expected_return"]` 現在會是 `None`，直接拿去 `round(None - 3, 2)` 會整個 crash。**實際行為**：`simulate()` 改成只在 `alloc["expected_return"] is not None` 時才真的採用「用組合報酬」這條路徑；否則退回 Planner 自己既有的 `assumptions.return_base` 常數（`DEFAULT_ASSUMPTIONS["return_base"]=7.0`，使用者可覆寫）——這是 Planner 原本就有的手動假設欄位，跟任何 Strategy Portfolio 證據無關，不是新的 silent fallback。這個分支只在「使用者選的每一個標的都還沒設定假設報酬」這個邊界情況觸發，一般情況（至少一個標的有價格）完全不受影響。

## 7. Provenance / version pinning

`finance_targets` 一筆 row 完整保存：`source_type`、`strategy_portfolio_id`、`portfolio_version_id`、`research_run_id`、`expected_return`、`assumption_source`、`assumption_evidence_ref`（`get_profile()` 的 SELECT 已加上這六欄，`assumption_evidence_ref` 讀出時用既有的 `_loads()` 解成 dict）。

- **Portfolio 產生新版本不會動舊 pin**：`portfolio_version_id` 是一個寫死的整數欄位，`simulate()`/`save_profile()` 都沒有任何程式碼會去查「這個 `strategy_portfolio_id` 目前最新版本是多少」再覆蓋掉它——`test_new_portfolio_version_does_not_mutate_old_planner_pin` 直接驗證：`plab.update_portfolio()` 產生 v2 之後，重新 `get_profile()` 讀回的 target 仍然 `portfolio_version_id == v1`，且 v1 的 evidence 仍然完整可讀。要換版本必須是使用者自己重新呼叫 `save_profile()` 帶新的 `portfolio_version_id`（I1 沒有做任何「一鍵更新到最新版」的 UI/API，那是 I2+ 的範圍）。
- **archived 只加資訊，不影響讀取**：`evidence()` 對一個已封存的 portfolio 一樣完整回傳（`archived: true`），`test_archived_portfolio_evidence_still_readable` 驗證。
- **沒有在 I1 建立 finance plan versioning system**：`finance_profiles`/`finance_targets` 仍然是 Stage I0 report §4.3 記錄的既有「目前設定」模型（`save_profile()` 就地覆寫，targets 整批刪除重建）——I1 沒有碰這個既有落差，只是保證「Strategy Portfolio 那一側」pin 住的 id 本身不可變（因為底層本來就不可變），不代表 Planner 自己的編輯歷史被保留。

## 8. Cash semantics

維持 I0 的 Model A，逐項驗證：

- **不拆內部現金**：`evidence()` 只回傳 `last_cash_weight`/`avg_cash_weight` 作為 evidence 的一部分展示，從未寫進 Planner 自己的任何現金欄位。
- **`actual_value` 只能人工或 `NULL`**：`_prepare_target()` 對 `actual_value` 的處理完全沒變（`t.get("actual_value")` 原樣寫入），沒有任何程式碼路徑會去讀 `portfolio_research_runs`/`get_research_equity()` 的數字塞進去——`test_no_actual_value_fallback_from_research_equity` 驗證：一個沒填 `actual_value` 的 strategy_portfolio target，即使該 portfolio 真實研究出來的期末 equity 是一個很大的正數，`rebalance.analyze()` 看到的 `total_value` 仍然是 0（`has_holdings: False`）。
- **不走 journal fallback**：`test_no_journal_positions_fallback_for_strategy_portfolio` 直接 monkeypatch `journal.positions()` 回傳一筆偽造的、`stock_id` 剛好等於 `"portfolio:<id>"` 的持倉,確認 `_portfolio_actuals()` 完全忽略它（見 §2 的內部防呆修正）。
- **不重複計算**：`test_no_internal_portfolio_cash_double_counting` 驗證一個只含一筆 strategy_portfolio target（`actual_value=1,000,000`）的 profile：`health.analyze()` 的 `invested_assets`/`liquid_cash`（只讀 Step2 手動資產欄位）完全不受影響、維持 0；`rebalance.analyze()` 的 `total_value` 剛好是 1,000,000，不多不少——這 100 萬只在 rebalance 這一個地方出現一次。Step2 手動資產與 `finance_targets.actual_value` 之間既有的（ETF 就已經存在的）潛在重複輸入風險，I1 依指示不動，只在本報告與 `docs/RESEARCH_CONTRACTS.md` §15 保留說明——沒有新增任何 API 層的強制檢查，因為那是一個跟本次整合無關、範圍更大的既有落差。

## 9. Reporting classification

`universe.meta()` 對 `"portfolio:"` 前綴的 key 回傳 `role="strategy_portfolio"`、`region="TW"`、`fee=0.0`，`ROLE_DEFAULTS["strategy_portfolio"]["asset"]="equity"`——固定假設「目前五套策略全部是台股」的簡化近似值。`allocation.warnings()` 新增一則 `{"level": "approximation", ...}`，只要有 weight>0 的 strategy_portfolio 標的就一定出現，明確寫「資產類別／地區曝險為簡化近似值...不作為研究或執行的限制條件」。這個分類只餵進 Planner 自己的 `exposures()`/`warnings()`（純顯示用），完全不影響、也讀不到 Strategy Portfolio Engine 那一側的任何 constraint 或 allocation 邏輯。

## 10. Backward compatibility

`source_type IS NULL` 的 row 在所有程式碼路徑裡都被當成 `etf`（現況行為）處理，沒有一次性 backfill：`test_nullable_migration_backward_compatible_round_trip` 存一筆完全沒有任何新欄位鍵值的 legacy 形狀 payload，讀回後六個新欄位全部是 `None`，`simulate()` 算出來的組合報酬跟改動前手算的數字完全一致；`test_legacy_etf_profile_simulation_numerics_unchanged` 另外直接驗證 `simulate()`（不落地）在典型 ETF profile 上的數字沒有變化。`tests/smoke.py finance`（純數學、不碰新欄位）改動前後全部通過，一字不差。

## 11. Tests

新檔案 `tests/test_finance_portfolio_integration.py`，**32 個測試**，全部通過，全專案 `python -m unittest discover -s tests -p 'test_*.py'` **376/376 通過**（既有 344 + 新增 32）：

| 要求 | 測試 |
|---|---|
| legacy ETF profile simulation numerics unchanged | `LegacyBackwardCompatTests::test_legacy_etf_profile_simulation_numerics_unchanged` |
| nullable migration / backward compatibility | `test_nullable_migration_backward_compatible_round_trip`；`schema_migrations` v5 三個既有回歸測試已更新為 5 |
| manual source 跳過 role/default-return fallback | `AllocationNoFallbackTests::test_manual_source_skips_role_default_return_fallback`、`LegacyBackwardCompatTests::test_manual_source_missing_expected_return_has_warning_no_silent_fallback` |
| valid strategy portfolio pin | `StrategyPortfolioSourceRealDBTests::test_valid_pin_derives_opaque_stock_id` |
| mismatched portfolio/version/run rejected | `test_mismatched_version_rejected`、`test_mismatched_research_run_rejected`、`test_stock_id_prefix_is_reserved_for_strategy_portfolio_source`、`test_pin_fields_rejected_outside_strategy_portfolio_source`（+ route 層 `EvidenceRouteTests::test_save_profile_route_maps_pin_mismatch_to_400_not_500`） |
| research_run_id=NULL evidence response | `test_research_run_id_null_evidence_response`（+ `EvidenceRouteTests::test_evidence_route_no_research_run_returns_200_not_500`） |
| archived portfolio evidence still readable | `test_archived_portfolio_evidence_still_readable` |
| new portfolio version 不 mutate 舊 Planner pin | `test_new_portfolio_version_does_not_mutate_old_planner_pin` |
| expected_return 永不被 CAGR 自動填入 | `test_expected_return_never_auto_filled_from_cagr` |
| research_informed 需要明確 expected_return + evidence | `test_research_informed_requires_explicit_expected_return`、`test_research_informed_requires_evidence` |
| assumption_evidence_ref 是 audit snapshot | `test_research_informed_valid_case_and_evidence_ref_is_audit_snapshot` |
| missing expected_return 有 warning、無 silent fallback | `test_missing_expected_return_has_warning_and_no_silent_fallback_in_simulate`、`AllocationNoFallbackTests::test_unpriced_target_excluded_from_weighted_return_but_keeps_its_weight` |
| 不從 portfolio equity fallback actual_value | `test_no_actual_value_fallback_from_research_equity` |
| 不從 journal.positions fallback | `test_no_journal_positions_fallback_for_strategy_portfolio` |
| 不重複計算內部 portfolio cash | `test_no_internal_portfolio_cash_double_counting` |
| simulate deterministic | `LegacyBackwardCompatTests::test_simulate_is_deterministic_for_a_plain_etf_profile`、`StrategyPortfolioSourceRealDBTests::test_simulate_deterministic_with_strategy_portfolio_source` |
| future/new research run 不 mutate 舊 projection input | `test_future_research_run_does_not_mutate_old_projection_inputs` |
| `_names()` 不把 `portfolio:<id>` 當股票 | `NamesHelperTests::test_names_does_not_treat_portfolio_key_as_a_stock` |
| API route 錯誤映射（額外） | `EvidenceRouteTests`（3 個，`fastapi.testclient.TestClient`，no lifespan） |

`StrategyPortfolioSourceRealDBTests`／`EvidenceRouteTests` 用真實 DB（沿用 `tests/test_portfolio_lab.py` 已驗證的兩個 Stage F1.5 Walk Forward job：`momentum_trend`／`value_quality`），`@unittest.skipUnless(_real_jobs_available())` 保護一個沒有這些 fixture 的全新 checkout。測試自己建立的所有 `"I1 test: ..."` Strategy Portfolio 在 `tearDownClass` 全部 `archive_portfolio()`（軟刪除，跟 `test_portfolio_lab.py` 同一慣例）；本機真實 `finance_profiles`（2 筆）／`finance_targets`（26 筆）在整輪測試前後逐筆核對過完全沒被動到。`python -m compileall app tests scripts` 通過；`tests/smoke.py finance` 全部通過。

## 12. Known limitations

1. **沒有 Planner UI**——依指示，本階段完全沒有動 `web/finance.html`/`finance.js`；使用者目前只能透過 API 直接操作這個新 source type，看不到任何介面。
2. **`finance_profiles`/`finance_targets` 沒有版本歷史**（沿用 I0 report §4.3 記錄的既有落差，本階段沒有解決）——只有 pin 住的 Strategy Portfolio id 本身不可變。
3. **Step2 手動資產與 `finance_targets.actual_value` 沒有一致性檢查**（既有的、ETF 就已經有的落差，見 §8）——I1 沒有為了這次整合而單獨修這個更大範圍的問題。
4. **`strategy_portfolio` 的資產/地區分類是固定的簡化近似值**（`asset=equity, region=TW`，見 §9）——如果未來平台的策略開始交易非台股標的，這個假設會需要重新檢視，但不影響任何研究或執行邏輯（純 reporting）。
5. **一個 Financial Plan 同一時間只能 pin 同一個 Strategy Portfolio 一次**——`finance_targets` PK 是 `(profile_id, stock_id)`，`stock_id` 由 `strategy_portfolio_id` 唯一決定，所以無法在同一份 profile 裡對同一個 portfolio 同時 pin 兩個不同版本（例如「比較 v1 跟 v2」）。這是既有 schema 形狀的自然限制（跟「一份 profile 裡同一檔 ETF 只能出現一次」是同一種限制），I1 沒有為了這個假設性需求擴大 schema。
6. **`evidence()` 的 cash 欄位是簡單平均/期末值**（`last_cash_weight`/`avg_cash_weight`），不是完整的逐日序列——如果之後 Planner UI 想畫一條現金比例的時間序列圖，需要額外呼叫既有的 `GET .../equity`，I1 判斷這超出「唯讀 evidence API 至少要有什麼」的最小需求。

## I2 Readiness

Backend 已經完整可用且測試覆蓋：schema（v5，nullable，backward compatible）、source model（三型別，opaque key，pin 驗證重用既有 `portfolio_lab` 函式）、evidence API（唯讀、永不 500、永不寫回）、assumption 分離（`research_informed` 三條件 + audit snapshot + 永不自動填值的 regression guard）、cash 語意（Model A，三個「不 fallback」保證都有測試）、reporting 分類（disclosed approximation）。I2（若要做 Planner UI）可以直接在這些 API 上疊：

- **不需要**：任何新的 DB 遷移或後端邏輯改動（除非 I2 過程中發現本報告 §12 的某個 known limitation 需要提前解決）。
- **需要**：`web/finance.html`/`finance.js` 新增第三種 investment source 的選擇/顯示流程（含 evidence 展示 + 明確的「採用這個假設」確認動作，對應 §4 的兩步驟設計）、對 `portfolio_version_id`/`research_run_id` 過期或有新版本可用時的顯式 relink UI（後端已保證不會自動切換,但目前沒有介面讓使用者方便地手動切換）。

本階段依指示到此停止，不修改 Planner UI。
