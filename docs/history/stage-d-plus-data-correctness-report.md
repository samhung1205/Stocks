# Stage D+ — Research Data Correctness 報告

日期：2026-09-20。只修 Coverage Gate 的 3 個 blocking issues（＋其衍生的 share multiplier 問題）；未做 Optimization / Walk Forward / UI / Portfolio Lab，未重構 Backtest Core 的成交、成本與績效邏輯。

**結論：Stage E → READY WITH RESTRICTIONS。**

## 1. 修正內容

| 項目 | 檔案 | 內容 |
|---|---|---|
| 公司行動分類 | `app/corporate_actions.py`（新）、`app/datasources/finmind.py` | 「息/除息」「權/除權」「權息/除權息」三組拼法全部辨識；同一函式供新抓取與既有資料修復使用 |
| 既有資料修復 | `app/db/migrations.py`（ledger v2）、`corporate_actions` 新增 `share_multiplier`、`resolution` 欄位 | 只用 `raw_json` 重算，不重新下載；可重跑，第二次 `updated=0` |
| 還原價因子序列 | `app/services/strategy/adjustment.py`（新）、`Bar.adj_close`、`factors.py` | 只供 Momentum / MA / Volatility；執行價、成交、權益不變 |
| Universe 分類 | `app/security_class.py`（新）、`data_foundation.liquid_universe`、`panel.py` | 明確分類規則，每筆帶 `class_basis` |
| 帳務 | `engine._apply_action` | `UNRESOLVED_CORPORATE_ACTION` 契約；`metadata.unresolved_actions` |
| 稽核 | `scripts/data_correctness_audit.py`（新）、`scripts/coverage_audit.py` 更新 | before/after 對照（before 取自 `data/stocks.pre-stage-d-plus.db` 備份） |

`ENGINE_VERSION` 升為 `strategy-core/stage-b+c+d-plus/2`，舊 run 的快取因此失效。

## 2. Corporate-action handling

來源事實（對全部 6,832 列 DividendResult 驗證）：`stock_and_cache_dividend` = `before_price − 未四捨五入參考價`，殘差 ≤ 0.0101（參考價四捨五入至 0.01）。據此：

| 類型 | 處理 | 為何可靠 |
|---|---|---|
| 純現金（息/除息） | `cash_dividend = value`，僅在 `value>0` 且 `\|before−value−after\| ≤ 0.0101` 時填入 → `cash_verified` | 該欄位即價差本身，全部 5,772 列通過 |
| 純除權（權/除權） | `share_multiplier = before/(before−value)` → `stock_multiplier_derived` | 只有一個未知數（無現金成分），由未取整的價差唯一解出，如 50.7→48.28 得 1.05 |
| 面額變更（SplitPrice） | 取唯一吻合 `\|before/m−after\| ≤ 0.00501` 的 n/2（或 1/k）→ `split_multiplier_derived` | 21 列全數解出（2、2.5、3.9993→4、10、20…）；其他 split 類型一律 unresolved |
| **權息混合** | **不填**，`unresolved_mixed` | 一條方程式、兩個未知數（現金與配股率），此來源無法拆分 |
| value ≤ 0 或與參考價不符 | 不填，`unresolved_invalid_value` | 例：1312 2026-06-11（參考價高於前收） |

`ratio`（參考價比）**從不**被當成 share multiplier；它只用於還原價序列。

帳務契約（engine）：持有中遇到「無已驗證現金或股數效果」的事件 → 寫入 `metadata.unresolved_actions`（date、stock_id、kind、resolution、shares_held）與 warning `UNRESOLVED_CORPORATE_ACTION …`，計數在 `metadata.unresolved_action_count`。Stage E 可據此排除或標記該持有期間；沒有預設自動排除。未持有的事件不記錄。

真實資料抽驗（單檔持有跨越除權息日，零成本）：1210 配股 1.05 → 權益連續（+1.8% 為當日市場變動）；2330 現金股利入帳；5904 面額變更 ×10（分割前停牌，事件日才買進，無重複調整）；1101 權息混合 → 標記 1 筆 unresolved，權益下降 8.5% 未被硬補。

## 3. Adjusted-price convention

**Start-anchored、向前累乘（forward accumulation）、point-in-time 安全。**

```
r        = after / before          # 交易所除權息參考價比（事件日參考價 / 前收）
C(t)     = ∏ r_i  （ex_date_i ≤ t）  # 事件前 C = 1
adj_close(t) = close(t) / C(t)
```

- 事件日調整後報酬 = `close_t / (r × close_{t-1}) − 1`，即相對參考價的真實漲跌，無人工跳空。
- t 日水位只取決於 ≤ t 的事件：**日後事件不會改寫歷史**，訊號日 T 看到的與當時實際可得一致（有測試）。水位不等於市價，只有比值（動能、MA 距離、波動）有意義。
- 每個 (stock, ex-date) **只套一個係數**：同日多列（如 DividendResult 與 SplitPrice 描述同一事件）收斂為一個，優先 split，不相乘；`ratio` 已同時含現金與配股成分，故不會重複計算。
- 因 `r` 含現金股利，序列近似「含息」價格；動能因此不再被除息壓低。
- 無效 ratio（NaN、0、超出 0.01–100）忽略；除權息日調整後仍有 >25% 跳動時，panel 加 warning（參考價與行情不符）。
- **與帳務嚴格分離**：engine 只讀 raw open/close、`cash_dividend`、`share_multiplier`；測試證明開啟 `adjust_prices` 後 equity curve 與 fills 完全相同。

## 4. Universe cleanup

分類器（`app/security_class.py`，依序判斷，每筆回傳 `basis`）：

1. `industry`（交易所產業別，權威）：含 ETN / ETF / 受益證券 / 存託憑證 / 所有證券（權證）→ 各自排除
2. `name`：`[甲乙丙丁戊己庚辛壬癸]特` 且代號為 4 碼＋字母 → 特別股
3. 舊 `stocks.type='etf'` → ETF
4. 有 industry 且代號 `^[1-9]\d{3}$` → 普通股；4 碼＋字母 → 特別股（標 `id_pattern_fallback`）
5. 完全沒有 metadata（471 檔舊下市列）：僅依代號形態判斷，**一律標 `id_pattern_fallback`**；這 471 檔在 universe 內沒有行情資料，實際影響為 0

`liquid_universe` 改為先算流動性再套分類器；不再依賴 `s.type='stock'`，也不是單一 `00%` 規則。`panel.Security.security_type` 同步改用分類結果（`common_stock` → `'stock'`，其餘為其分類名），因此策略內的 `investable_universe` 也自動排除。

| as_of | 舊規則 | 新 | 移除 |
|---|---:|---:|---|
| 2022-11-01 | 727 | 726 | ETF 1 |
| 2024-05-01 | 883 | 879 | ETF 3、DR 1 |
| 2026-06-01 | 867 | 863 | ETF 3、DR 1 |
| 2026-09-01 | 947 | 905 | **ETF 40**、DR 1、特別股 1 |

2026-09-18 的 903 檔全部 `class_basis=industry`；ID 形態像 ETF/DR/特別股者 0 檔。2022-10 起規模 min/median/max = 684 / 839 / 969（舊 685 / 842 / 973）。

## 5. Before / After Coverage

| 指標 | Before | After |
|---|---|---|
| `unknown` 公司行動 | 3,106 列（45%） | **0** |
| 現金股利入帳（cash_dividend 有值） | 3,135 | 5,772（全部 cash_verified） |
| 有 share multiplier | 0 | 553（除權 532＋面額變更 21） |
| 仍 unresolved | — | 528 列：權息混合 525、value 異常 3 |
| 流動 universe 內 unresolved（2022-10-31 起） | — | 400 / 4,760 事件（8.4%）；混合 398、異常 2；約每檔每年 0.11 次 |
| 單日 >11% 非漲跌幅跳動（1,346 檔聯集，5 年） | 436 次／324 檔 | **106 次／98 檔** |
| 動能視窗（273 日）受影響的 universe 比例 | 平均 7.5%、最高 12.4% | **平均 1.3%、最高 2.1%** |
| 波動視窗（126 日）受影響比例 | 平均 3.5%、最高 11.3% | **平均 0.5%、最高 1.0%** |
| 純除權／分割持有跨越（帳務） | 憑空虧損＋warning | 股數依 multiplier 調整，equity 連續 |

剩餘 106 次調整後跳動：訊號起點（2022-10-31）之後 80 次，其中 43 次發生在超過 5 個交易日無行情之後（停牌復牌、減資、重新上市）；26 次在訊號起點之前（2021–22 新上市階段）；僅 5 次與 action 列相鄰、101 次無對應 action。panel 另有 1 個除權息日殘差 >25% 的 warning（參考價與行情不符）。這些是來源沒有 action 的事件，無法憑空還原。

## 6. Tests

- `uv run python -m unittest discover -s tests -p 'test_*.py'`：**80 / 80 通過**（既有 58＋新增 22）。
- `tests/smoke.py backtest`、`finance`：通過（Stage B 手算、成本、公司行動契約未回歸）。
- 新增 `tests/test_data_correctness.py`（22 項）：兩種拼法的現金／除權／混合分類、value 不一致時不填、除權倍率唯一推導與 value≤0 拒絕、面額變更 n/2 與 1/k、非面額變更不推；舊 schema 遷移＋重跑＋直接 `repair` 冪等（updated=0）、未驗證現金會被清除；調整後報酬在現金股利／配股／分割前後無人工跳空、日後事件不改寫歷史、同日多列不重複套用、無效 ratio 忽略、殘差跳動旗標、缺 close 不產生 adj；動能／MA／波動用調整價而 raw 仍可取得、`adjust_prices` 不改 engine 帳務；20 個分類案例與流動 universe 只留普通股；已解出的配股 equity 連續、現金股利只入帳一次、混合事件標記不猜、未持有不記錄、現金類別但金額未驗證視為 unresolved。
- 既有測試的兩處必要調整：`schema_migrations` 筆數 1→2（新增 v2）；兩個測試檔的假股票 `A/B/C` 改為 `1101/1102/1103`（新分類器不再把非普通股形態代號當普通股）。
- 未重跑 `smoke.py sources / light / deep`（會打外部 API，本階段未動資料源網路層）。
- 正式 DB 已遷移至 v2；遷移前備份：`data/stocks.pre-stage-d-plus.db`。

## 7. Remaining limitations

1. **權息混合 525 列無法拆分股數／現金**（此來源）。要解需接 FinMind `TaiwanStockDividend`（每股現金與股票股利）；本階段只旗標，不猜。
2. 101 次無 action 的跳動（減資、停牌復牌、缺行情）不會被還原；Momentum / Low Vol 對這些股票仍有雜訊（波動視窗 0.5%、動能視窗 1.3%）。
3. 調整價含現金股利，Momentum 因此是含息動能；若研究需要價格動能需另設參數。
4. 分類是「現況」metadata：`stocks.industry` 不含歷史分類；舊下市列以 ID 形態判斷（無行情，無影響）。
5. 一次 ex-date 抓不到 `resolution` 為 `unresolved_split` 的非面額變更類 split（目前資料 0 筆）。
6. 前次 Coverage Gate 列出的非 blocking 限制未變：strict 模式無歷史基本面、財報全 assumed、非修訂版資料、sector-neutral 退回 global、D1 universe 為單點 `as_of` 凍結。

## 8. Stage E readiness

原 3 個 blocking 均已處理：股利類別修復、還原因子序列、Universe 只含普通股；股數帳務只在可唯一推導時補，其餘以明確契約旗標。

| 策略 | 判定 | 限制 |
|---|---|---|
| Quality × Growth × Momentum | READY WITH RESTRICTIONS | 僅 assumed；財報 100% 推估公布日；動能已用調整價 |
| Revenue Growth Acceleration | READY WITH RESTRICTIONS | 僅 assumed（營收公布日推估為次月 10 日）；現金股利已入帳 |
| Momentum + Trend Following | READY WITH RESTRICTIONS | 價格因子已還原；殘餘跳動 1.3%（動能視窗）以 warning 呈現 |
| Value × Quality | READY WITH RESTRICTIONS | 僅 assumed；PE>0 覆蓋約 85%；sector-neutral 退回 global |
| Low Volatility × Quality | READY WITH RESTRICTIONS | 僅 assumed；波動視窗殘餘 0.5% |

Stage E 必須遵守（沿用 Coverage Gate，未變）：研究期 2022-11-01 ～ 2026-09-18；不做 strict 基本面研究；universe 用各月聯集而非單點 as_of；panel 載入一次後在記憶體重用；每次結果須讀 `metadata.unresolved_action_count` 並揭露（約每檔每年 0.11 次混合事件）。
