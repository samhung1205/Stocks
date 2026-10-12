# Stage I1.1 — Manual Investment Source Classification Hotfix

範圍：小範圍 hotfix，修正 Stage I1 的一個遺漏，不開始 Planner UI，不改 Strategy Portfolio integration semantics（`app/services/portfolio/`、`portfolio_lab.py`、`portfolio_source.py` 一行未動）。

## 問題

[Stage I0 contract](stage-i0-planner-integration-contract-report.md) §2 明確要求 `source_type='manual'` 不得套用 `universe.meta()` 對未知 `stock_id` 的 `core_tw`/TW-equity fallback。[Stage I1 report](stage-i1-planner-backend-integration-report.md) §5 正確擋掉了**報酬率**這一半（`no_role_default` 讓 `expected_return` 對 manual 永遠不會拿到 role 預設值），但**分類**這一半沒有修：`allocation.allocate()` 的 `m = universe.meta(sid)` 呼叫沒有帶任何 source_type 資訊,一個 `manual` 標的的 `role`/`asset`/`region`/`dividend_yield` 仍然沿用「不在名單內的自訂代號 → `core_tw`/TW/3% 股息殖利率」這條舊 fallback——跟 Stage I1 report 自己 §2 寫的「這只是既有行為,本階段沒有為 manual 重新設計分類」是同一個已知但沒解決的落差,現在依指示補上。

**額外抓到的第二個洩漏點**（同一個根因）：`service.py::simulate()` 算組合層股息殖利率（`port_yield`）時直接呼叫 `universe.default_yield(t["stock_id"])`，完全不知道這個 target 的 source_type，繞過了 `allocation.allocate()` 內部已經正確算出的 role——一個 `manual` 標的即使角色分類修好了,這裡仍然會撈到 legacy `core_tw` 的 3% 股息假設,悄悄污染組合的殖利率。

## 修正

### 1. `universe.meta(stock_id, source_type=None)`

新增可選參數，預設 `None`（**任何既有呼叫端不改，行為完全不變**——這就是 backward compatibility 的落地方式，不需要另外寫一個 legacy 分支）：

```python
def meta(stock_id, source_type=None):
    if is_portfolio_key(stock_id):
        return {...}                      # 不變：strategy_portfolio，跟 source_type 無關
    if source_type == "manual":
        return {"role": "manual", "region": "unspecified", ...}   # 新分支
    m = UNIVERSE.get(stock_id)
    ...                                    # 不變：etf / legacy custom fallback（core_tw/TW）
```

新增 `ROLE_DEFAULTS["manual"] = {"label": "自訂資產（未分類）", "assumed_return": None, "asset": "other", "dividend_yield": 0.0}`（跟 Stage I1 已有的 `ROLE_DEFAULTS["strategy_portfolio"]` 同一種寫法），`ASSET_LABELS["other"]`、`REGION_LABELS["unspecified"]` 補上對應的顯示文字。`asset_of()`/`default_return()`/`default_yield()` 三個既有函式都補上同一個可選 `source_type` 參數（原因見下方「第二個洩漏點」）。

**選最小、相容的表示方式**：沒有新增任何 schema 欄位、沒有改變 `ROLE_DEFAULTS`/`meta()` 回傳 dict 的既有 key 形狀（`role`/`region`/`asset` 等既有欄位照舊，只是多了一組新的值）——`app/services/finance/exposure`（`allocation.exposures()`）跟 `warnings()` 本來就是用這些 key 動態分桶（`asset[r["asset"]] = ...`），加一個新的 `"other"`/`"unspecified"` 值不需要改動任何桶分類邏輯。

### 2. `allocation.allocate()`：`no_role_default` → `source_types`

`no_role_default: set[str]` 只夠讓 `expected_return` 跳過 role 預設，不夠讓**分類**也跳過——把參數換成 `source_types: dict[str, str]`（`{stock_id: source_type}`），一次把「這個標的到底是什麼來源」的完整資訊傳進去：

```python
by_role.setdefault(universe.meta(sid, source_types.get(sid))["role"], []).append(sid)   # 分類，也要修
...
m = universe.meta(sid, source_types.get(sid))
...
elif source_types.get(sid) == "manual":
    exp_return = None
else:
    exp_return = universe.ROLE_DEFAULTS[m["role"]]["assumed_return"]
```

原本第一處 `by_role`（餵進 `role_weights()` 規則引擎、決定每個角色該分到多少建議權重）**沒有**修 source_type 也是一個真實的行為缺陷——修之前，一個 `manual` 標的會被歸進 `core_tw` 那一桶,跟真正的 core_tw ETF 一起瓜分 `role_weights()` 算出來的 core_tw 角色權重百分比,等於讓一個未分類的自訂資產偷走了台股核心配置的建議額度。修完之後,`manual`（跟 `strategy_portfolio` 一樣）沒有專屬的 role 錨點,建議權重是 0%,完全靠使用者自己設定 `weight`——這才是「未分類」該有的行為。

`service.py::simulate()` 對應把 `no_role_default = {...if source_type=='manual'}` 換成 `source_types = {_target_key(t): t.get("source_type") for t in targets_in}`（全量傳，manual 以外的值 `meta()` 一律當作沒特殊意義）。

### 3. 第二個洩漏點：`port_yield`

`service.py` 原本：

```python
port_yield = sum(t["weight"] * universe.default_yield(t["stock_id"]) for t in alloc["targets"]) / denom
```

改成：

```python
port_yield = sum(t["weight"] * universe.default_yield(t["stock_id"], source_types.get(t["stock_id"]))
                 for t in alloc["targets"]) / denom
```

這是測試過程中發現的、Stage I1 report 沒有記錄的真實 bug——不是本次要修的範圍以外的東西，是同一個根因（`meta()`/`default_*()` 對 source_type 無感）的第二個具體症狀，必須一起修，否則 §1 的分類修正在「組合殖利率」這個數字上仍然會被繞過。

## 4. 不影響的東西（明確驗證過）

- **`expected_return`／`initial_amount`／`monthly_amount`／`health`／`rebalance`**：一個字元都沒改——這些欄位的計算路徑跟 role/asset/region 分類是分開的兩件事,`_prepare_target()`／`simulate()` 组装這些數字的程式碼完全沒動。
- **`source_type IS NULL`／`'etf'`**：`meta(stock_id)`（不帶 source_type，或帶除了 `"manual"` 以外的值）走的分支跟 Stage I1 之前**逐行相同**——`test_legacy_null_source_custom_symbol_behavior_unchanged`／`test_existing_etf_behavior_unchanged` 直接斷言 `role=="core_tw"`、`region=="TW"`、`default_return==7.0` 這些具體數字沒有變。
- **`strategy_portfolio`**：`is_portfolio_key()` 前綴判斷在 `meta()` 裡排在 `source_type=="manual"` 檢查**之前**，就算呼叫端不小心對一個 portfolio key 傳了 `source_type="manual"`，還是會先命中 portfolio 分支——`test_existing_strategy_portfolio_classification_unchanged` 驗證傳入無關的 `source_type` 不影響判斷。

## 5. Tests

`tests/test_finance_portfolio_integration.py` 新增 **8 個測試**（既有 32 個全部更新呼叫簽名後繼續通過），全專案 `python -m unittest discover -s tests -p 'test_*.py'` **384/384 通過**（既有 376 + 新增 8）：

| 要求 | 測試 |
|---|---|
| explicit manual source 不會得到 role=core_tw | `ManualClassificationHotfixTests::test_explicit_manual_source_is_not_role_core_tw`、`AllocationNoFallbackTests::test_manual_source_is_not_classified_as_core_tw_or_tw_equity` |
| manual source 不會被報表列為 TW equity | `test_manual_source_not_reported_as_tw_equity`（asset='other'、region≠'TW'）、`LegacyBackwardCompatTests::test_manual_source_missing_expected_return_has_warning_no_silent_fallback`（simulate() 端對端：混合 0050/00719B/manual 三檔,斷言 `exposure.region.TW` 剛好等於 0050 的權重、沒有被 manual 灌水） |
| manual expected_return 仍只能使用明確 planning assumption | `test_manual_expected_return_only_from_explicit_assumption` |
| manual missing expected_return 仍無 silent default-return fallback | `test_manual_missing_expected_return_no_silent_default_return_fallback` |
| legacy source_type=NULL custom symbol behavior unchanged | `test_legacy_null_source_custom_symbol_behavior_unchanged` |
| existing ETF behavior unchanged | `test_existing_etf_behavior_unchanged` |
| existing Strategy Portfolio behavior unchanged | `test_existing_strategy_portfolio_classification_unchanged`（+ 既有 32 個測試裡所有 `StrategyPortfolioSourceRealDBTests`／`EvidenceRouteTests` 原樣重跑一次全部通過，證明這次改動沒有波及 Strategy Portfolio 那一側） |

`python -m compileall app tests scripts` 通過；`tests/smoke.py finance`（純數學）改動前後全部通過，一字不差；本機真實 `finance_profiles`（2 筆）／`finance_targets`（26 筆）在測試前後逐筆核對過完全沒被動到，測試自建的所有 `strategy_portfolios` 都在 `tearDownClass` 正確 archive。

## 6. Documentation

`docs/RESEARCH_CONTRACTS.md` §15 的既有一句話更新為同時涵蓋 return 與分類兩件事，並新增 **§16「Manual investment source classification (Stage I1.1 hotfix)」**記錄這次修正的具體內容與根因；`AGENTS.md` 的 Financial Planner invariants 該條也同步更新（明確提醒：以後不要在 `app/services/finance/` 任何地方對 manual/strategy_portfolio 標的裸呼叫不帶 source_type 的 `meta()`/`default_return()`/`default_yield()`，否則會重新製造這次修的洩漏）。`app/services/finance/service.py::_prepare_target()` 的 docstring 也同步了一句話。README/PRODUCT.md 沒有動——這是一個內部分類正確性的 hotfix，不是新的產品能力。

## I2 Readiness

沒有變化——I1 report 的 I2 readiness 結論依然成立。這次 hotfix 只讓「manual 的 reporting 分類」跟 I0 contract 原本的要求對齊，沒有新增任何 API、schema 或 evidence/assumption 分離的邊界，I2（Planner UI）要做的事情不變。

本階段依指示到此停止，不開始 I2 UI。
