"""煙霧測試：實際打各資料源，驗證欄位與入庫流程。

執行：uv run python tests/smoke.py [step]
step: sources / light / deep / finance / all（預設 all）

注意：finance 是純數學驗證，不打任何外部 API，可以任意重跑；
其餘三個會真的打資料源，請勿頻繁執行。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

TWSE_ID = "2330"   # 台積電（上市）
TPEX_ID = "5347"   # 世界先進（上櫃）


def check(name, rows, minimum=1):
    n = len(rows) if hasattr(rows, "__len__") else (1 if rows else 0)
    ok = n >= minimum
    print(f"{'✓' if ok else '✗'} {name}: {n} 筆")
    if rows and hasattr(rows, "__getitem__"):
        print(f"    範例: {str(rows[0])[:160]}")
    if not ok:
        raise SystemExit(f"FAILED: {name}")
    return rows


def test_sources():
    from app.datasources import twse, tpex, finmind, mis_snapshot
    print("── 資料源 ──")
    check("TWSE 全市場收盤(OpenAPI)", twse.daily_all_latest(), 900)
    check("TWSE 估值(OpenAPI)", twse.valuations_latest(), 900)
    check("TPEx 全市場收盤(OpenAPI)", tpex.daily_all_latest(), 700)
    check("TPEx 估值(OpenAPI)", tpex.valuations_latest(), 700)
    check("FinMind 2330 價量", finmind.price_history(TWSE_ID, "2026-07-01"), 5)
    check("FinMind 5347 月營收", finmind.month_revenue(TPEX_ID, "2026-01-01"), 3)
    check("MIS 快照", mis_snapshot.snapshot([(TWSE_ID, "twse"), (TPEX_ID, "tpex")]), 2)

    latest = twse.daily_all_latest()[0]["date"].replace("-", "")
    check(f"TWSE 歷史全市場 {latest}", twse.daily_all_by_date(latest), 900)
    check(f"TWSE 法人 T86 {latest}", twse.institutional_by_date(latest), 900)
    check(f"TWSE 大盤 {latest}", [twse.taiex_by_date(latest)])
    check(f"TPEx 歷史全市場 {latest}", tpex.daily_all_by_date(latest), 700)
    check(f"TPEx 法人 {latest}", tpex.institutional_by_date(latest), 500)


def test_light():
    from app.db import init_db
    from app.services import market_daily
    print("── 輕量層 ──")
    init_db()
    check("同步股票清單", [market_daily.sync_stock_list()])
    r = market_daily.update_daily()
    print(f"✓ 每日更新: {r}")
    assert r.get("quotes", 0) > 1500, "全市場報價筆數不足"


def test_deep():
    from app.db import init_db
    from app.services import deep_fetch, stock_view, scoring, alerts
    print("── 深度層（2330）──")
    init_db()
    r = deep_fetch.ensure_deep(TWSE_ID, force=True)
    print(f"✓ deep_fetch: {r}")
    check("K線", stock_view.kline(TWSE_ID), 200)
    check("月營收", stock_view.revenue_series(TWSE_ID), 12)
    check("財報季度", stock_view.financial_series(TWSE_ID), 8)
    chips = stock_view.chips_series(TWSE_ID)
    check("法人明細", chips["institutional"], 100)
    print(f"✓ 技術摘要: {stock_view.technical_summary(TWSE_ID)}")
    print(f"✓ 警訊: {alerts.scan(TWSE_ID)}")
    card = scoring.get_scorecard(TWSE_ID)
    print("✓ 評分表:")
    for it in card["items"]:
        print(f"    {it['label']}: auto={it['auto']} manual={it['manual']} / {it['max']}")


def _assert(label, cond, detail=""):
    print(f"{'✓' if cond else '✗'} {label}{'  ' + detail if detail else ''}")
    if not cond:
        raise SystemExit(f"FAILED: {label} {detail}")


ETFS = ["0050", "00646", "00662", "00878", "00719B", "CASH"]


def _case(**kw):
    """組出一份試算 payload，未指定的欄位留空。"""
    base = {
        "name": "smoke", "retire_age": 65, "life_expectancy": 90,
        "targets": [{"stock_id": s, "enabled": 1} for s in ETFS],
    }
    base.update(kw)
    return base


def test_finance():
    """財務規劃引擎：純數學，不打外部 API。"""
    from app.services.finance import compound, retirement, service

    print("── 財務引擎：公式與 edge case ──")
    # 封閉解（spec §11 指定公式）vs 逐月迴圈，兩者必須一致
    closed = compound.fv_total(300_000, 20_000, 7, 35)
    loop = compound.project(300_000, 20_000, 35, 7, inflation=2)["final_value"]
    _assert("封閉解與月迴圈一致", abs(closed - loop) / closed < 1e-4,
            f"closed={closed:,.0f} loop={loop:,.0f}")

    zero = compound.project(100_000, 10_000, 10, 0)
    _assert("年報酬 0% → 純累加", zero["final_value"] == 100_000 + 10_000 * 120,
            f"{zero['final_value']:,}")
    _assert("年限 0 → 回初始本金",
            compound.project(100_000, 10_000, 0, 7)["final_value"] == 100_000)
    _assert("月投入 0 → 只有單筆複利",
            compound.project(100_000, 0, 10, 7)["final_value"] > 100_000)
    _assert("全部為 0 不拋錯", compound.project(0, 0, 0, 0)["final_value"] == 0)
    _assert("負報酬情境不拋錯",
            compound.project(1_000_000, 0, 10, -5)["final_value"] < 1_000_000)

    no_reinvest = compound.project(1_000_000, 0, 20, 7, dividend_yield=3, reinvest=False)
    reinvest = compound.project(1_000_000, 0, 20, 7, dividend_yield=3, reinvest=True)
    _assert("關閉股息再投入後結果較低",
            no_reinvest["final_value"] < reinvest["final_value"],
            f"{no_reinvest['final_value']:,} < {reinvest['final_value']:,}")

    sim = retirement.withdraw_simulation(1_000_000, 500_000, 65, 90, 3)
    _assert("提款超過資產會耗盡並回報年齡", sim["depleted_age"] is not None,
            f"depleted_age={sim['depleted_age']}")

    print("\n── Case A：25 歲青年（月投 2 萬 / 35 年 / 成長型）──")
    a = service.simulate(_case(
        age=25, retire_age=60, income_monthly=60_000, exp_living=30_000,
        asset_cash=300_000, asset_etf=300_000, monthly_invest=20_000,
        risk_answers={"q1": "hold", "q2": "gt10", "q3": "max",
                      "q4": "yes", "q5": "yes"},
        assumptions={"initial_capital": 300_000}))
    pr = a["projection"]
    _assert("風險型態為成長或積極", a["risk"]["effective_type"] in ("growth", "aggressive"),
            a["risk"]["label"])
    _assert("可支配所得 = 60000 − 30000", a["health"]["disposable"] == 30_000)
    _assert("總投入 = 30萬 + 2萬×420",
            pr["total_contributed"] == 300_000 + 20_000 * 420,
            f"{pr['total_contributed']:,}")
    _assert("期末資產 = 總投入 + 投資增值",
            pr["final_value"] == pr["total_contributed"] + pr["investment_gain"])
    _assert("35 年後複利貢獻過半", pr["gain_ratio"] > 50, f"{pr['gain_ratio']}%")
    _assert("實質購買力低於名目", pr["final_real_value"] < pr["final_value"])
    roles = a["roles"]["weights"]
    _assert("成長導向：股票部位 > 85%",
            a["allocation"]["exposure"]["asset"]["equity"] > 85,
            str(a["allocation"]["exposure"]["asset"]))
    _assert("債券＋現金 < 15%", roles["defense"] + roles["cash"] < 15,
            f"defense={roles['defense']} cash={roles['cash']}")
    _assert("里程碑有算出達成年份",
            any(m["reached"] for m in a["milestones"]),
            str([(m["target"], m["year"]) for m in a["milestones"] if m["reached"]][:3]))

    print("\n── Case B：40 歲家庭（月投 2 萬 / 25 年 / 平衡型）──")
    b = service.simulate(_case(
        age=40, income_monthly=100_000, exp_living=65_000,
        asset_cash=600_000, asset_etf=2_000_000, monthly_invest=20_000,
        risk_answers={"q1": "hold", "q2": "5to10", "q3": "growth",
                      "q4": "yes", "q5": "partly"},
        assumptions={"initial_capital": 2_000_000}))
    ex = b["allocation"]["exposure"]["asset"]
    _assert("平衡型：股票部位介於 70%～95%", 70 <= ex["equity"] <= 95, str(ex))
    _assert("平衡型：持有債券部位", ex.get("bond", 0) > 0, str(ex))
    _assert("投資年限 = 到退休的 25 年", b["assumptions"]["horizon_years"] == 25)
    _assert("三情境由低到高遞增", (
        b["scenarios"]["scenarios"]["conservative"]["final_value"]
        < b["scenarios"]["scenarios"]["base"]["final_value"]
        < b["scenarios"]["scenarios"]["optimistic"]["final_value"]))

    print("\n── Case C：60 歲接近退休（月投 0 / 退休 65 / 月支出 5 萬 / 退休金 2.5 萬）──")
    c = service.simulate(_case(
        age=60, income_monthly=80_000, exp_living=50_000,
        asset_cash=1_000_000, asset_etf=10_000_000,
        monthly_invest=0, need_income=1,
        risk_answers={"q1": "sell_part", "q2": "3to5", "q3": "income",
                      "q4": "yes", "q5": "partly"},
        assumptions={"initial_capital": 10_000_000},
        retirement={"expense_monthly": 50_000, "pension_monthly": 25_000}))
    RT = c["retirement"]
    _assert("每月缺口 = 50000 − 25000", RT["gap"]["monthly_gap"] == 25_000)
    _assert("每年缺口 = 300,000", RT["gap"]["annual_gap_today"] == 300_000,
            f"{RT['gap']['annual_gap_today']:,}")
    _assert("提款模擬逐年回傳餘額", len(RT["normal"]["series"]) == 26,
            f"{len(RT['normal']['series'])} 筆（65→90 歲）")
    _assert("可支撐年數有算出", RT["normal"]["years_supported"] > 0,
            f"{RT['normal']['years_supported']} 年")
    _assert("SoRR 壓力情境不優於正常情境",
            RT["sorr"]["final_balance"] <= RT["normal"]["final_balance"],
            f"sorr={RT['sorr']['final_balance']:,} normal={RT['normal']['final_balance']:,}")
    exc = c["allocation"]["exposure"]["asset"]
    _assert("接近退休：配置含債券與現金",
            exc.get("bond", 0) > 0 and exc.get("cash", 0) > 0, str(exc))
    _assert("接近退休：成長型衛星 ≤ 10%", c["roles"]["weights"]["growth"] <= 10,
            f"growth={c['roles']['weights']['growth']}")

    print("\n── 重疊與曝險提示 ──")
    d = service.simulate(_case(
        age=30, income_monthly=80_000, exp_living=40_000,
        asset_etf=1_000_000, monthly_invest=20_000,
        risk_answers={"q1": "hold", "q2": "gt10", "q3": "max",
                      "q4": "yes", "q5": "yes"},
        targets=[{"stock_id": s, "enabled": 1}
                 for s in ["0050", "006208", "0056", "00878", "00919"]]))
    levels = [w["level"] for w in d["allocation"]["warnings"]]
    _assert("同時持有 0050+006208 出重疊提示", levels.count("overlap") >= 1,
            str([w["message"][:24] for w in d["allocation"]["warnings"]]))
    _assert("全台股組合出集中提示", "region" in levels,
            f"TW={d['allocation']['exposure']['region'].get('TW')}%")

    print("\n── ETF 歷史統計：分割還原 ──")
    from app.services.finance import etf_stats

    # 合成序列：第 300 天做 1 股換 4 股，若不還原會被算成 -75% 崩跌
    closes = [100 * (1.0002 ** i) for i in range(600)]
    closes = [c if i < 300 else c / 4 for i, c in enumerate(closes)]
    factors, events = etf_stats.split_factors(closes)
    _assert("偵測到 1 次分割", len(events) == 1, str(events))
    adj = [c * f for c, f in zip(closes, factors)]
    _assert("還原後價格序列連續",
            all(0.9 < adj[i] / adj[i - 1] < 1.1 for i in range(1, len(adj))))
    _assert("還原後為正報酬", adj[-1] > adj[0], f"{adj[0]:.2f} → {adj[-1]:.2f}")

    # 0050 與 006208 追蹤同一指數，含息年化應該接近（需先跑過補齊 ETF 歷史資料）
    s50, s6208 = etf_stats.stats("0050"), etf_stats.stats("006208")
    if s50["available"] and s6208["available"]:
        _assert("0050 與 006208 含息年化接近（分割已還原）",
                abs(s50["cagr_total"] - s6208["cagr_total"]) < 5,
                f"0050={s50['cagr_total']}% 006208={s6208['cagr_total']}%")
    else:
        print("  （略過 0050/006208 對照：尚未補齊 ETF 歷史資料）")

    print("\n── 再平衡（現金流式） ──")
    e = service.simulate(_case(
        age=35, income_monthly=90_000, exp_living=45_000,
        asset_etf=1_000_000, monthly_invest=30_000,
        risk_answers={"q1": "hold", "q2": "gt10", "q3": "growth",
                      "q4": "yes", "q5": "yes"},
        assumptions={"custom_weights": True},
        targets=[{"stock_id": "0050", "enabled": 1, "weight": 50, "actual_value": 700_000},
                 {"stock_id": "00646", "enabled": 1, "weight": 50, "actual_value": 300_000}]))
    rb = e["rebalance"]
    _assert("算出偏離度", rb["max_drift"] == 20.0, f"max_drift={rb['max_drift']}pp")
    low = next(x for x in rb["rows"] if x["stock_id"] == "00646")
    high = next(x for x in rb["rows"] if x["stock_id"] == "0050")
    _assert("新資金全部補到低配的部位",
            low["suggested_contribution"] == 30_000 and high["suggested_contribution"] == 0,
            f"00646={low['suggested_contribution']:,} 0050={high['suggested_contribution']:,}")
    _assert("投入後偏離縮小", abs(low["after_drift"]) < abs(low["drift"]),
            f"{low['drift']}pp → {low['after_drift']}pp")


if __name__ == "__main__":
    step = sys.argv[1] if len(sys.argv) > 1 else "all"
    if step in ("sources", "all"):
        test_sources()
    if step in ("light", "all"):
        test_light()
    if step in ("deep", "all"):
        test_deep()
    if step in ("finance", "all"):
        test_finance()
    print("\n全部通過 ✓")
