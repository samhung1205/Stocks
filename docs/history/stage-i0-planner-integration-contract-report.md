# Stage I0 — Financial Planner × Strategy Portfolio Integration Contract

範圍：**只定義與驗證 integration contract，不寫程式碼、不做 UI、不動 DB schema、不改 Strategy Portfolio research semantics。** 本報告是 I1（實作）開始前的設計基準，回答「Financial Planner 要用什麼形狀去讀 Strategy Portfolio 的研究結果，同時不違反 [AGENTS.md](../../AGENTS.md) 的 research/portfolio invariants」。核心結論貫穿全文：**Strategy Portfolio 的 historical/OOS research evidence 是唯讀的 reference data，Financial Planner 的 planning assumption 是使用者明確確認的另一個欄位——兩者之間沒有、也不能有任何自動賦值路徑。**

## 0. 讀過的既有文件與程式碼

[AGENTS.md](../../AGENTS.md)、[PRODUCT.md](../../PRODUCT.md)、[docs/ARCHITECTURE.md](../ARCHITECTURE.md)、[docs/RESEARCH_CONTRACTS.md](../RESEARCH_CONTRACTS.md)、[docs/PROJECT_STATUS.md](../PROJECT_STATUS.md)、[stage-h1-generic-portfolio-engine-report.md](stage-h1-generic-portfolio-engine-report.md)、[stage-h1-5-portfolio-validation-report.md](stage-h1-5-portfolio-validation-report.md)、[stage-h2-portfolio-ui-report.md](stage-h2-portfolio-ui-report.md)；Financial Planner 全部程式碼（`app/services/finance/*.py`：`service.py`／`universe.py`／`allocation.py`／`health.py`／`compound.py`／`retirement.py`／`risk.py`／`rebalance.py`／`etf_stats.py`）、`app/db/tables.py` 的 `finance_profiles`/`finance_targets`、`app/api/routes.py` 的 `/finance/*` 路由、`web/finance.html`/`web/js/finance.js`。Financial Planner **沒有** `tests/test_finance.py`——目前唯一的驗證是 `tests/smoke.py::test_finance()`（純數學、不打 DB／API，見該檔案 docstring 第 6 行）。

## 1. 現有 Financial Planner 稽核

### 1.1 Domain model（現況）

| 表/模組 | 內容 |
|---|---|
| `finance_profiles`（[app/db/tables.py:167](../../app/db/tables.py#L167)） | 一列一份「情境」：Step1 基本資料（age/retire_age/life_expectancy/horizon_years/goal）、Step2 收入/支出/資產/負債（實體欄位，使用者手動輸入的**現況總額**，非逐筆）、`monthly_invest`、`risk_answers`/`risk_type`（JSON/欄位）、`assumptions`（JSON）、`retirement`（JSON） |
| `finance_targets`（[app/db/tables.py:204](../../app/db/tables.py#L204)） | PK `(profile_id, stock_id)`；一列一檔「投資標的」：`weight`（目標權重%）、`expected_return`（人工覆寫，留空 → 用 `universe.default_return(stock_id)` 這個依 role 而定的**常數**）、`actual_value`（人工覆寫，留空 → 由 `journal.positions()` 依 `stock_id` 帶入市值）、`enabled` |
| `app/services/finance/universe.py` | 硬編碼的 ETF 名單 + 分類 metadata（role/region/overlap/fee，人工維護常數）；`meta()` 對不在名單內的自訂代號給預設 `role=core_tw` |
| `app/services/finance/allocation.py` | `role_weights()`：依距退休年數在三組錨點模板間內插 + 四條調整規則 → 角色權重(%)；`allocate()`：把角色權重攤到 `selected` 標的、算出組合層 `expected_return`/`expected_fee`/exposure/warnings |
| `app/services/finance/health.py`／`risk.py`／`retirement.py`／`rebalance.py`／`compound.py` | 純函式：財務健檢、風險問卷評分、退休提領模擬、目標-實際再平衡差距、複利/情境投影 |
| `service.simulate()`（[app/services/finance/service.py:212](../../app/services/finance/service.py#L212)） | **無狀態組裝層**：吃一份完整 profile payload，依序呼叫上述純函式，回傳結果頁需要的全部區塊。前端每次輸入變動都重打這支 API（debounce），不是「跑一次存一次」 |

### 1.2 Expected return / volatility 現況

- **沒有 volatility 欄位。** `assumptions` 只有三個純量報酬率（conservative/base/optimistic）與 `inflation`；模型是封閉解複利公式（`compound.fv_lump`/`fv_annuity`），不是任何形式的隨機模擬，沒有地方會用到「波動度」這個輸入。
- 三情境報酬有兩種來源，由 `assumptions.use_portfolio_return`（預設 `True`）切換：
  - **`True`（預設）**：`base = alloc["expected_return"]`（依 role 權重加權的 ETF 假設報酬），`conservative/optimistic = base ∓ 3pp`——**純規則性的 ±3 個百分點平移，不是任何統計量**。
  - **`False`**：直接用使用者填的三個常數 `return_conservative`/`return_base`/`return_optimistic`。
- 個別標的的 `expected_return`：`finance_targets.expected_return` 有值就用它；否則 fallback 到 `universe.default_return(stock_id)`——即該 ETF `role`（`core_tw`/`core_us`/`growth`/`income`/`defense`/`cash`）在 `ROLE_DEFAULTS` 裡的**人工維護常數**（[universe.py:19-32](../../app/services/finance/universe.py#L19)），與任何回測/研究結果無關。

### 1.3 Simulation / projection 現況

`compound.scenarios()`（封閉解 + 逐月序列雙軌，докstring 見 [compound.py:1](../../app/services/finance/compound.py#L1)）吃「一個年化報酬率」為輸入，對三個情境（conservative/base/optimistic）分別跑一次；退休提領（`retirement.analyze`）另外接受一個獨立的 `retire_return`（預設 = conservative）。**輸入永遠是一個純量年化報酬率**，不是一條報酬率序列、不是一個蒙地卡羅分布，也沒有任何「用歷史 OOS 序列驅動未來投影」的路徑。

### 1.4 Persistence 現況（關鍵：這裡沒有版本概念）

`save_profile()`（[service.py:108](../../app/services/finance/service.py#L108)）是**就地覆寫**：`upsert_many(conn, finance_profiles, [row])` 用 `id` 覆蓋既有列；`finance_targets` 每次儲存都先整批 `DELETE FROM finance_targets WHERE profile_id=?` 再重新插入（[service.py:129](../../app/services/finance/service.py#L129)）。**沒有 `finance_profile_versions` 這種東西，沒有 append-only 慣例，沒有「這次試算結果」的持久化紀錄**——`simulate()` 的輸出從不寫回 DB，純粹是「現在這份 profile 現在算出來的樣子」。這與 `strategy_versions`/`strategy_portfolio_versions`（純 INSERT，見 [AGENTS.md](../../AGENTS.md) 第 30/38 行）是**完全不同的兩種慣例**——Planner 是「目前設定」模型，Strategy Portfolio 是「不可變歷史」模型。這個落差是本階段 §4 的核心議題。

### 1.5 UI 現況

`web/finance.html` + `web/js/finance.js`：六步驟表單（基本資料/收支資產/標的配置/假設/風險/結果），與 `finance/universe`、`finance/simulate`、`finance/profiles` 三組 API 對應。**完全沒有 Strategy Portfolio 的痕跡**——`web/portfolio.html` 與 `finance.html` 是兩個互不知道對方存在的頁面（[PRODUCT.md:58](../../PRODUCT.md#L58)、[docs/PROJECT_STATUS.md:36](../PROJECT_STATUS.md#L36) 已明確記載）。

### 1.6 整合真正需要的最小新增（結論）

比對上面六點與本階段指令的十個要求，Planner 已經有的東西（財務健檢、風險問卷、角色配置引擎、三情境複利、退休模擬、再平衡差距）**完全不需要改動**——它們是通用的「金額怎麼長大」計算層，不知道錢的來源是 ETF 還是 Strategy Portfolio。真正需要的最小新增只有兩件事，且都只發生在 `finance_targets` 這一張表／它的組裝邏輯：

1. **一個新的 investment source 類型**（`strategy_portfolio`），取代/補充「`stock_id` 只能是 ETF 代號」這個隱含假設，並且**必須 pin 具體版本**（§2）。
2. **一個與 `expected_return` 分離的 assumption-source 標記**，讓「這個報酬率假設是怎麼來的」可追溯，同時**技術上阻斷** evidence → assumption 的自動賦值（§3）。

其餘（cash 語意 §5、provenance §4、上游狀態變化 §6）都是這兩個新增欄位帶出的、必須同時想清楚的連帶問題，不是獨立的新增。

## 2. Investment Source model

`finance_targets` 目前的隱含 source 只有一種：「`stock_id` 對到 `universe.UNIVERSE` 或是自訂代號」，`expected_return`/`actual_value` 是否人工填寫只是同一種 source 內部的兩個開關。本階段把它拆成三個**顯式**的 source type（新增欄位 `source_type`，`I1` 才落地，此處只定義形狀）：

| `source_type` | 說明 | 對應現況 |
|---|---|---|
| `manual` | 使用者直接輸入一個 `expected_return`，沒有任何標的名稱/ETF metadata 綁定（例如「其他資產」「私募」「儲蓄險」這類平台管不到的資產） | 現況的「有填 `expected_return`」路徑，但現況同時強制要有一個 `stock_id`——`manual` 應該允許 `stock_id` 只是一個使用者自訂標籤，`universe.meta()` 對它的 fallback（role=core_tw）不應該再套用；I1 需要讓 `manual` 明確跳過 role 分類，而不是被硬套一個不相關的 role 常數 |
| `etf` | 現況的預設行為：`stock_id` 對到 `universe.UNIVERSE`（或落入自訂代號 fallback），`expected_return`/`actual_value` 兩者都可留空用預設。**這是唯一一個舊資料的隱含類型**——見 §6 backward compatibility |
| `strategy_portfolio` | 本階段新增：`stock_id` 這個欄位不再是股票代號，而是一個**指向 Strategy Portfolio 的 opaque key**（建議慣例：`f"portfolio:{strategy_portfolio_id}"`，避免與真實 `stock_id` 撞名，且沿用 PK 是 String 這個既有 schema，不需要改 `finance_targets` 的主鍵形狀） |

`strategy_portfolio` 類型**必須 pin**（新增三個欄位，全部只在 `source_type='strategy_portfolio'` 時有值）：

- `strategy_portfolio_id`（對應 `strategy_portfolios.id`）
- `portfolio_version_id`（對應 `strategy_portfolio_versions.id`——**不是** portfolio 的「目前最新版本」，是使用者選擇當下明確指定的那一版，之後永遠讀這一版，見 §4）
- `research_run_id`（optional，對應 `portfolio_research_runs.id`——使用者選擇要參照哪一次已完成的研究結果作為 evidence；可以是 `NULL`，代表「只是把這個 portfolio 當一個未來要投入的容器，目前沒有選定要參照的證據」）

**明確禁止只存 portfolio 名稱**——這是指令原文的硬性要求，也符合 [AGENTS.md](../../AGENTS.md) 第 37 行「A Strategy Portfolio component pins the exact strategy version AND the OOS evidence」的同一慣例（component pin 的是 `walkforward_job_id`，這裡 Planner pin 的是 `portfolio_version_id`/`research_run_id`——同一種「pin id，不 pin 名字」的設計語言，讓 Planner 讀到的東西天生不可能因為 rename/re-clone 而指錯目標）。

**驗證規則**（比照 `portfolio_lab._validate_components` 的精神，[stage-h1 report §1](stage-h1-generic-portfolio-engine-report.md)）：儲存一個 `strategy_portfolio` target 時，`portfolio_version_id` 必須真的屬於 `strategy_portfolio_id`；若給了 `research_run_id`，必須屬於同一個 `portfolio_version_id`。三者不吻合直接拒絕（400），不做「找最接近的那個」之類的猜測。

## 3. Research Evidence vs. Planning Assumption（本階段最核心的分離）

### 3.1 Evidence（唯讀，永遠 read-through，永不複製存一份）

當 `source_type='strategy_portfolio'` 且 `research_run_id` 有值時，Planner 可以**顯示**（不可寫回）以下欄位，全部即時從既有 Portfolio API 讀出，**不新增任何一張表去複製存**（沿用 [stage-h1-5 report §2](stage-h1-5-portfolio-validation-report.md) 已經定調的原則：「所有欄位都是既有表已經存的東西，只是組合讀出來」）：

- portfolio/version identity、components 清單、allocation method（`get_version`/`_components_view`）
- OOS/research window、CAGR、Sharpe、Max Drawdown、volatility（若有）——`get_research_result` 的 `metrics`
- cash 行為（`no_candidate_cash_allocation`/`strategic_cash`，見 rebalance payload）、turnover、cost drag（`turnover_json`）
- warnings / evidence limitations（`warnings_json`，含 `insufficient_evidence`/`no_candidate`/`ASSUMED_PUBLICATION_MODE`/`STITCHED_NOT_INDEPENDENT_EVIDENCE` 等既有 warning code——沿用 [RESEARCH_CONTRACTS.md §11](../RESEARCH_CONTRACTS.md#L78) 的三段嚴重度，Planner 不重新分類、只是原樣顯示）

因為 `portfolio_research_runs` 本身已經是不可變、pin 死自己的 `portfolio_version_id`/`components_json`/config（[RESEARCH_CONTRACTS.md §14](../RESEARCH_CONTRACTS.md#L110)），**pin 這三個 id 本身就已經是「evidence 快照」**——不需要 Planner 自己另外複製一份數字。這也是為什麼「歷史 Financial Plan 必須可重現」（§6）在 evidence 這一層幾乎是免費的：只要 Planner 存的是 id 而不是「當下算出的數字」，evidence 永遠可重現，因為底層本來就不可變。

### 3.2 Planning Assumption（使用者明確確認，與 evidence 完全分離的欄位）

`finance_targets` 新增 `assumption_source`（`manual` / `scenario` / `research_informed`；`NULL` = 舊資料，等同現況行為，見 §6）。三種都只是「`expected_return` 這個既有欄位是怎麼決定的」的標籤，**`expected_return` 欄位本身的形狀完全不變**——不新增第二個報酬率欄位、不新增波動度欄位（Planner 現有模型沒有波動度輸入,見 §1.2,本階段不新增,以免建立一個沒有任何下游計算會用到的欄位）：

- **A. manual**：使用者直接輸入 `expected_return`——現況行為，欄位不變。
- **B. scenario**：使用者選 conservative/base/optimistic 三檔之一,或直接沿用 profile 層級已有的 `assumptions.return_conservative/base/optimistic` 三個常數——這也是現況已有的行為（`use_portfolio_return=False` 分支),本階段只是給它一個顯式標籤,不新增邏輯。
- **C. research_informed**：使用者**看過** `strategy_portfolio` evidence 的 CAGR/Sharpe/MaxDD 之後,**自己再輸入或按下「採用」把某個數字複製進 `expected_return`**。技術上必須是兩個分開的動作：
  1. 讀 evidence（§3.1,唯讀 API,不寫任何東西）
  2. 使用者按確認 → 前端把使用者看到、同意的那個數字（可能就是 CAGR,也可能是使用者自己打折後的數字）送進 `save_profile` 的 `expected_return` 欄位,同時把 `assumption_source` 設成 `research_informed`,並存一個 `assumption_evidence_ref`（JSON,含 `{portfolio_version_id, research_run_id, snapshot_cagr, snapshot_asof}`——**只是「使用者當時參考了什麼」的稽核紀錄,不是一個會被重新解析的 live binding**;之後 evidence 若透過 §6 的 relink 换了一個新的 `research_run_id`,舊的 `assumption_evidence_ref` 依然指向使用者當初實際看到的那個快照,不會跟著漂移）。

**禁止事項的技術落地**：`service.py` 的 `simulate()`/`save_profile()` 兩支函式，**任何時候都不得**用 `strategy_portfolio_id`/`research_run_id` 反查 evidence 再直接塞進 `expected_return`——也就是說，evidence 讀取的 API（新的、只回傳 §3.1 欄位的唯讀 endpoint）與寫入 `expected_return` 的 API（既有的 `save_profile`）**必須是兩個不同的呼叫，中間沒有伺服器端的自動拼接**。這是 `planning_expected_return = portfolio_oos_cagr` 這種 silent mapping 唯一有效的防呆方式——不是靠 UI 上「請使用者自己填」的口頭約定，而是後端根本不存在一條可以自動完成這件事的程式路徑。

### 3.3 沒有 assumption 的 `strategy_portfolio` target 怎麼辦

一個 `strategy_portfolio` target 如果 `expected_return` 是 `NULL`（使用者選了來源但還沒確認任何假設報酬），`allocation.allocate()` **不得**比照現況 ETF 的行為 fallback 到 `universe.default_return()`——因為 `strategy_portfolio` 不屬於任何 `ROLE_DEFAULTS` 的 role，沒有對應常數可以 fallback，且就算有，也違反「不能有預設值等於研究結果」的精神。正確行為（沿用 [AGENTS.md](../../AGENTS.md) 「No silent fallback, anywhere」）：該 target 在算組合層 `expected_return`/`scenarios` 時記一筆 `warnings`（比照 `allocation.warnings()` 現有的 info/level 慣例）「此標的尚未設定假設報酬，不計入報酬率加權」，並將其目標權重排除在報酬率加權分母之外（而不是當作 0% 報酬硬算進去，那會系統性低估整體預期報酬）——金額欄位（`initial_amount`/`monthly_amount`）不受影響，只有報酬率相關計算受影響。

## 4. Provenance / Versioning

### 4.1 Financial Plan 必須保存的欄位（逐項對映到既有/新增欄位）

| 要求 | 落地位置 |
|---|---|
| selected investment source | `finance_targets.source_type` |
| selected portfolio version | `finance_targets.portfolio_version_id`（pin，非「目前最新版」） |
| selected research evidence（若有） | `finance_targets.research_run_id`（optional） |
| planning assumptions | `finance_targets.expected_return`（沿用既有欄位） |
| assumption source | `finance_targets.assumption_source` + `assumption_evidence_ref`（僅 `research_informed` 時有值） |
| created/updated time | `finance_profiles.updated_at`（既有，粗粒度到整份 profile）——**已知落差**：見 §4.3 |

### 4.2 「不得自動從 v2 跳到 v3」的落地方式

因為 `portfolio_version_id` 是一個 pin 死的整數欄位，**唯一能改變它的路徑就是使用者重新呼叫 `save_profile()` 送出一個新的 `portfolio_version_id`**——沒有任何背景邏輯、排程、或 `simulate()` 內部程式碼會去查「這個 `strategy_portfolio_id` 目前的 latest version 是多少」然後覆蓋掉使用者選過的版本。這件事在目前 codebase 的寫法下是**自動成立**的，因為：

1. `simulate()` 是唯讀組裝層，本來就不寫回 `finance_targets`（[service.py:212](../../app/services/finance/service.py#L212)）。
2. `strategy_portfolio_versions` 本身不可變（[RESEARCH_CONTRACTS.md §14](../RESEARCH_CONTRACTS.md#L110)），不存在「同一個 id 的內容變了」這種情況需要煩惱。

I1 唯一需要刻意做對的事，是 UI 的「更新到最新版本」必須是一個**顯眊、獨立的按鈕**（例如「重新連結到最新版本」），送出一個新的 `save_profile()` 呼叫、附上新的 `portfolio_version_id`（以及使用者重新確認過的 `research_run_id`/`expected_return`，因為新版本的 evidence 可能整套不一樣）——不是「打開 Planner 就自動幫你換」。

### 4.3 已知落差：Financial Plan 本身沒有版本歷史（誠實揭露，本階段不解決）

§1.4 已經指出：`finance_profiles`/`finance_targets` 是「目前設定」模型，`save_profile()` 就地覆寫、`finance_targets` 整批刪除重建。這代表：

- Evidence 這一層的可重現性（§3.1）**已經免費滿足**——pin 的 id 永遠指向不可變資料。
- 但「這份 Financial Plan 昨天長什麼樣子」**沒有辦法回答**——覆寫就是真的覆寫掉了，沒有 undo，沒有歷史版本可以比較。這不是本階段新引入的問題，是 Planner 從 Stage 開始就有的既有設計；本階段的兩個新增欄位（`portfolio_version_id`/`research_run_id`）繼承了同一個限制：**它們自己是 pin 死的，但「哪一個 target 曾經 pin 過哪個版本」這段歷史不會被保留**——使用者把 target 從 v2 relink 到 v3 之後，v2 那個選擇本身就從 DB 消失了（除非 `assumption_evidence_ref` 剛好留下了一份快照，但那只在 `research_informed` 路徑才會寫入，且只記錄使用者當初看到的**評估數字**，不記錄「當初 pin 的是哪個 version_id」）。

**本階段的立場**：不在 I0 貿然新增一整套 `finance_plan_versions` 不可變版本系統——指令明確要求「不要開始大型 UI redesign」，而幫 Planner 補上完整版本歷史是一個跟 Strategy Portfolio 整合本身無關、範圍大得多的既有技術債（Planner 從第一天就是這樣，不是這次整合造成的）。I0 只保證：**evidence 引用本身的可重現性**（因為 pin 的是不可變 id），至於「Financial Plan 這個容器本身要不要有版本歷史」是一個獨立的、值得未來單獨立項的產品決策，本報告只誠實記錄這個落差，不假裝解決它。

## 5. Cash 語意

Strategy Portfolio 內部可能因為 strategic cash（`fixed_weight` 殘餘）、no-candidate cash（component 無候選標的）、execution residual cash（[stage-h1 report §6](stage-h1-generic-portfolio-engine-report.md#L54) 的確定性縮量/遞延）而持有大量現金——這些現金在 Portfolio 自己的 `equity[].cash_weight` 裡完整記錄，是 evidence 的一部分。

**選擇 Model A：把整個 Strategy Portfolio 當成一個單一 investment asset**（一個 `finance_targets` row，一個 `actual_value`），**不拆解 Portfolio 內部的 cash/stock 組成**。理由：

1. 這是指令原文明確建議的第一版方向（「第一版優先選擇最簡單且不 double-count cash 的 contract」）。
2. Portfolio 的現金屬於「策略暫時沒有標的可投」的**研究層現金**，跟使用者個人的「緊急預備金」/「CASH 這個 role」是完全不同性質的東西——硬拆開來混進 Planner 自己的 `asset_cash`/`CASH` target 反而會製造一個假象：讓使用者以為 Portfolio 的策略性現金跟他自己銀行帳戶的現金是同一種可以互相調度的資源。
3. Model B（拆出 Portfolio 內部 cash）需要 Planner 的 `allocation.py`/`exposures()` 理解「一個 target 內部又分成好幾個子部位」，這是一個真正的架構改動（`allocate()` 現在假設一個 target = 一筆金額 = 一個 role/asset/region），遠超本階段「只定義最小 contract」的範圍。

**明確避免的雙重計算**（兩個既有、且不因本次整合而新產生的風險點，一併記錄）：

- **Planner 個人現金 vs. Portfolio 內部現金**：Model A 下不會發生——Portfolio 的 `actual_value` 是一個含現金在內的**總市值**單一數字，Planner 完全不知道、也不需要知道裡面有多少是現金。
- **Step 2 手動資產總額 vs. `finance_targets.actual_value`**：這是**現況已經存在、且現況完全沒解決**的落差——`health.analyze()` 的 `invested_assets`（[health.py:43](../../app/services/finance/health.py#L43)）來自 Step 2 使用者手動填的 `asset_stock`/`asset_etf` 總額，跟 `finance_targets[].actual_value`（逐檔實際市值，[service.py:198](../../app/services/finance/service.py#L198) `_portfolio_actuals`）之間**沒有任何一致性檢查**——使用者完全可能兩邊都填、造成雙重計算，現況的 ETF targets 就有這個問題。本階段對 `strategy_portfolio` target **沿用同一個（已知不完美）慣例**，不去發明一個只對 Strategy Portfolio 生效的特例修正——如果未來要解決，應該是一次性地解決 Step2 vs targets 的一致性問題（對所有 source type 都適用），而不是在整合 Strategy Portfolio 時順手打補丁製造兩套不同的規則。I1 唯一該做的是在 UI 文案上提醒：「若此 Strategy Portfolio 的市值已計入 Step 2 的『現有股票/ETF資產』，請勿在此重複計入」——與現況 ETF targets 已有的隱含假設一致。
- **`actual_value` 永遠是使用者自報，絕不從研究結果回推**：`strategy_portfolio` target 的 `actual_value` 只能是（a）人工輸入，或（b）維持 `NULL`（尚未有實際部位）。**絕對不能**用 `portfolio_research_runs` 的最終 equity 自動帶入——那是回測重放出來的模擬數字，不是使用者真的持有的部位市值（平台本來就「不下單」，見 [PRODUCT.md](../../PRODUCT.md#L44)，沒有任何管道知道使用者是否、以多少錢真的跟隨了這個 Portfolio）。這點與現況 ETF target 的 `actual_value` 邏輯有一個關鍵差異需要明確劃線：ETF target 可以從 `journal.positions()`（使用者自己在本平台記錄的真實交易）自動帶入，因為那些確實是使用者的真實持倉；Strategy Portfolio 的研究結果從來不是任何人的真實持倉，兩者不可混用同一條 fallback 路徑。

**資產配置分類（`allocation.py` 的 role/asset/region）的簡化近似**：`strategy_portfolio` target 沒有單一 role 可以套（它是多策略、可能持有上百檔股票的組合，見 [stage-h1 report §9](stage-h1-generic-portfolio-engine-report.md#L100) 的真實 holdings 數字）。v1 對它的 `asset`/`region` 分類只做**報表用的粗略近似**（例如固定標成 `asset=equity, region=TW`，因為現有五套策略的股票池全部是台股），並在 exposure 提示文字加一句「此為簡化近似值，實際配置依策略持股與現金水位而定」——這與 `max_sector_weight` 在 Portfolio Engine 端「reporting-only，不假裝是精確值」（[AGENTS.md](../../AGENTS.md#L50)）是同一種誠實原則的延伸，不是新發明一套規則。

## 6. Portfolio 狀態變化對既有 Financial Plan 的影響

沿用「historical plan remains reproducible; 不因上游變動 silent mutation」的原則，逐項定義：

| 上游事件 | 對已 pin 的 Financial Plan 的影響 |
|---|---|
| **Portfolio archived** | 無影響。`archived` 是軟刪除，`strategy_portfolio_versions`/`portfolio_research_runs` 完整保留可讀（[AGENTS.md](../../AGENTS.md#L116) 「Archiving...always a soft delete」）。Planner 讀 evidence 時額外顯示一則 info：「來源 Portfolio 已封存」，但數字/圖表照常渲染——不因為上游封存而讓 Planner 這邊報錯或消失。 |
| **Portfolio 產生新版本** | 無影響。已 pin 的 `portfolio_version_id` 永遠指向同一個不可變版本（§4.2）。Planner 可以（非必要）額外顯示「有新版本可用」的提示（比對 `get_portfolio().latest_version.id` 與已 pin 的 id），但**絕不自動切換**；使用者要換版本必須走 §4.2 的顯式 relink 動作。 |
| **research_run 缺失（`research_run_id` 為 `NULL`，或使用者選了 source 但還沒選定任何已完成的 run）** | 這不是「錯誤」，是正常狀態——evidence 區塊顯示「尚無研究結果可參照」，`expected_return` 的假設（若已用 `manual`/`scenario` 設定）不受影響，`assumption_source='research_informed'` 在沒有 evidence 可查時應被視為不合法組合（拒絕儲存這個組合，而不是儲存一個查無來源的「research_informed」標籤）。 |
| **warnings 惡化（同一個 run 的 warning 變嚴重）** | **結構上不會發生**——`portfolio_research_runs` 完成後不可變（[RESEARCH_CONTRACTS.md §14](../RESEARCH_CONTRACTS.md#L110)），一個已經 pin 的 `research_run_id` 的 `warnings_json` 永遠不變。「warnings 變嚴重」唯一可能的情境是使用者**主動**把 `research_run_id` relink 到另一個（通常是較新的）run——那是一次顯式動作，新 warnings 在 relink 當下的確認畫面完整顯示，不是背景悄悄發生。 |

## 7. Cash semantics 決策摘要（回應 §8 指令）

已在 §5 完整展開；摘要重申：**選 Model A**（Portfolio 整體視為一個 investment asset），第一版不拆分內部 cash，明確記錄 Step2 手動資產與逐檔 `actual_value` 之間現況已存在、本階段沿用不修的既知落差，`actual_value` 對 Strategy Portfolio 一律人工輸入、絕不從研究結果的 equity 曲線回推。

## 8. Migration / Backward compatibility

- **新增欄位全部 nullable，且不改變任何既有欄位的語意**：`finance_targets.source_type`/`portfolio_version_id`/`research_run_id`/`assumption_source`/`assumption_evidence_ref`。既有資料列這五個欄位皆為 `NULL`。
- **`source_type IS NULL` 的讀取行為 = 現況行為，一字不改**：`service.py` 判斷 source 時，`NULL` 一律視同 `etf`（現況唯一路徑），`assumption_source IS NULL` 視同 `manual`（現況「有填 `expected_return` 就用，沒填就退回 role 預設」的既有邏輯）。**不需要一次性 backfill** 舊資料——這五個新欄位是「新增能力」而不是「重新分類既有資料」，舊 profile 開啟、編輯、儲存全程不受影響，因為程式碼路徑本來就是「NULL → 走現況分支」。
- **`finance_targets` PK 沒有變**：`strategy_portfolio` target 的 `stock_id` 用 `f"portfolio:{id}"` 這個字串慣例塞進既有的 `stock_id` 欄位，不需要改動主鍵形狀，也因此對任何既有查詢/index 都是透明的——唯一需要小心的地方是 `_names()`（[service.py:149](../../app/services/finance/service.py#L149)）目前會把非 `CASH` 的 `stock_id` 全部拿去查 `stocks` 表要名字，`portfolio:<id>` 前綴的 key 必須在這支函式裡被排除（比照現況排除 `CASH` 的寫法），否則會對一個不存在的股票代號查表落空——這是 I1 實作時的一個具體、已知的小陷阱，此處先記錄。
- **舊 Planner 資料的 backward compatibility 驗證方式**：不需要資料遷移腳本，只需要一個測試（見 §9）斷言「一個完全沒有這五個新欄位值的既有 profile，`get_profile()`/`simulate()` 的輸出與本次改動之前逐 bit 相同」。

## 9. Tests / Guards（I1 落地時需要的測試清單）

本階段是 contract-only，以下測試**尚未寫成程式碼**，是 I1 實作的驗收清單；每一項後面標註它依賴的既有、已通過的不變量（表示「這個保證不是憑空要求，底層本來就有」）：

| # | 要驗證的事 | 依賴的既有保證 |
|---|---|---|
| 1 | `strategy_portfolio` target 的 `expected_return` 絕不會被 `simulate()`/`save_profile()` 自動設成該 portfolio 任一 research_run 的 CAGR——用「對同一個 target 跑 `simulate()` 前後，`expected_return` 欄位完全不變（除非測試自己顯式呼叫 `save_profile` 寫入）」的方式斷言 | §3.2 的「兩個分開的 API 呼叫，中間沒有伺服器端拼接」設計 |
| 2 | pin 的 `portfolio_version_id` 不可變——`strategy_portfolio_versions` 本身的不可變性（純 INSERT）已由 `tests/test_portfolio_lab.py::test_immutable_versions_and_new_version_on_edit` 覆蓋；I1 只需新增「Planner 讀到的版本內容與當初 pin 時完全一致」這一層薄斷言 | [stage-h1 report §10](stage-h1-generic-portfolio-engine-report.md#L114) 已通過的既有測試 |
| 3 | Portfolio 產生新版本不影響已 pin 舊版本的 Financial Plan——`test_old_run_unchanged_after_new_portfolio_version` 已經驗證了 research run 層級的同一保證；I1 新增「同一個 `portfolio_version_id` 在 portfolio 產生 v3 之後，Planner 讀出的 evidence 數字不變」 | [stage-h1 report §8](stage-h1-generic-portfolio-engine-report.md#L84) 已通過的既有測試 |
| 4 | Portfolio archived 不刪除、不阻擋 Planner 讀取——`test_archive_does_not_delete_research_history` 已驗證 research history 存活；I1 新增「archived portfolio 的 evidence API 呼叫依然回傳完整資料，只多一個 `archived: true` 旗標」 | [stage-h1 report §10](stage-h1-generic-portfolio-engine-report.md#L114) 已通過的既有測試 |
| 5 | `research_run_id` 缺失時的 fallback/display——`research_run_id IS NULL` 時 evidence 區塊回傳一個明確的 `{available: false, reason: "no_research_run_selected"}` 形狀，而不是 500 或空物件 | 新邏輯，I1 需新增 |
| 6 | planning assumptions 可重現——同一個 `finance_targets` row（`expected_return`/`assumption_source` 皆為靜態欄位值）重複呼叫 `simulate()` 得到完全相同的 `rates`/`projection` | 現況 `simulate()` 本來就是純函式組裝（無隨機性），沿用即可 |
| 7 | no cash double counting——针對 Model A 寫一個整合測試：一個只含一個 `strategy_portfolio` target（`actual_value` 人工填 100 萬）的 profile，`health.analyze()`／`rebalance.analyze()` 的輸出裡「這 100 萬」只出現一次，不會因為同時存在於 `finance_targets.actual_value` 與任何 Step2 欄位而被算兩次（測試層面只能斷言「這是同一個數字來源」，無法阻止使用者在 Step2 手動重複輸入——見 §5 的已知限制） | 新邏輯，I1 需新增 |
| 8 | manual planner source backward compatible——見 §8 最後一項，逐 bit 比對改動前後既有 profile 的 `simulate()` 輸出 | 新增欄位全 nullable + 現況分支不變，I1 需新增回歸測試固定這個保證 |
| 9 | 未來 portfolio 結果變動不改過去 projection 的輸入——`portfolio_research_runs` 完成後不可變已由 `test_daily_equity_has_no_nan_inf_and_cash_weight_stays_in_bounds` 等既有測試間接保證其只讀不變；I1 新增「對同一個已 pin 的 `research_run_id` 重複讀取 evidence，兩次結果逐欄位相同」 | [stage-h1-5 report §5](stage-h1-5-portfolio-validation-report.md#L86) 已通過的既有測試 |

## 10. I1 Readiness

**Ready to start**，且範圍清楚收斂在：`finance_targets` 五個新 nullable 欄位、一支新的唯讀 evidence API（讀 `strategy_portfolio_id`/`portfolio_version_id`/`research_run_id` 組合，回傳 §3.1 列的欄位）、`service.py`/`allocation.py`/`universe.py` 對 `source_type='strategy_portfolio'` 的三處分支（跳過 role fallback、跳過 evidence→assumption 自動賦值、報表用的簡化資產分類）、§9 的九項測試。**明確不在 I1 範圍**（沿用本階段的邊界，且未經使用者另外確認前不應擴大）：

- Financial Plan 本身的版本歷史系統（§4.3 已誠實記錄的既有落差，不是本次整合要解決的問題）。
- Step2 手動資產總額與 `finance_targets.actual_value` 的一致性檢查（現況已存在、本階段沿用不修，見 §5）。
- 任何形式的「用 Strategy Portfolio 的歷史報酬序列驅動蒙地卡羅/機率式投影」——Planner 現有模型是純量報酬率的封閉解/月迴圈複利，沒有既有的、可追蹤的統計模型可以承接一條完整報酬率序列；指令第 5 節明確要求「若沒有，不要新增複雜模型」，本階段判斷維持現況純量假設報酬即可，`research_informed` assumption source（§3.2）已經足以讓使用者把 evidence 的 CAGR 當參考、自己決定要不要採用同一個數字。
- Portfolio 內部 cash 拆分（Model B，§5 已說明留給未來）。
- 完整 Planner UI 改版——本報告只定義後端 contract 形狀；UI 端要怎麼呈現「選擇 Strategy Portfolio 作為來源 → 看 evidence → 確認 assumption」這個兩步驟流程,是 I1 自己的最小新增,不在本階段範圍。

本階段依指示到此停止，不開始任何 Planner UI 或後端程式碼變動。
