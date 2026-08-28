"""複利試算核心（spec §11-§19、§22、§23）。

公式（年化 r、投資 n 年、每月底投入 PMT）：

    r_m       = (1 + r)^(1/12) − 1
    FV_初始   = PV × (1 + r_m)^months
    FV_定額   = PMT × ((1 + r_m)^months − 1) / r_m        ；r_m = 0 → PMT × months
    FV_總計   = FV_初始 + FV_定額
    r_net     = r − 年度總費用                            （Advanced mode）
    實質價值  = 名目 / (1 + 通膨)^years

上面的封閉解是 spec 指定的公式，`fv_lump()` / `fv_annuity()` 就是它們的實作。
但**逐年序列以月迴圈 `project()` 為準**——因為關閉「股息再投入」時配息會離開複利、
單獨累積，那不是一條封閉解能表達的。兩者在單純情境下必須一致，smoke test 會交叉驗證。

edge case（r=0 / years=0 / PMT=0 / 分母為零）在入口統一處理，回合理值而不是拋錯。

純函式：不碰 DB。金額單位為元，報酬率/通膨/費用單位為百分比。
"""

DEFAULT_MILESTONES = [1_000_000, 3_000_000, 5_000_000, 10_000_000, 20_000_000]

DISCLAIMER = ("本工具提供的是財務規劃與投資情境模擬，所有報酬率均屬假設值或歷史資料，"
              "不代表未來績效，也不構成個別證券之買賣建議。")
SCENARIO_NOTE = "以上為報酬率假設與數學模擬，不代表未來實際報酬。"


def monthly_rate(annual_pct: float) -> float:
    """年化報酬率（%）→ 月化報酬率（小數）。"""
    r = annual_pct / 100
    if r <= -1:
        return -1.0
    return (1 + r) ** (1 / 12) - 1


def fv_lump(pv: float, annual_pct: float, years: float) -> float:
    """單筆本金複利 FV = PV(1+r_m)^months。"""
    months = int(round(years * 12))
    if months <= 0 or pv <= 0:
        return max(pv, 0.0)
    return pv * (1 + monthly_rate(annual_pct)) ** months


def fv_annuity(pmt: float, annual_pct: float, years: float) -> float:
    """每月底定期定額 FV = PMT × ((1+r_m)^months − 1)/r_m；r_m=0 時退化為 PMT×months。"""
    months = int(round(years * 12))
    if months <= 0 or pmt <= 0:
        return 0.0
    r_m = monthly_rate(annual_pct)
    if abs(r_m) < 1e-12:
        return pmt * months
    return pmt * (((1 + r_m) ** months - 1) / r_m)


def fv_total(pv: float, pmt: float, annual_pct: float, years: float) -> float:
    return fv_lump(pv, annual_pct, years) + fv_annuity(pmt, annual_pct, years)


def project(initial: float, monthly: float, years: float, annual_return: float,
            *, inflation: float = 2.0, fee: float = 0.0,
            dividend_yield: float = 0.0, reinvest: bool = True,
            start_age: float | None = None,
            start_year: int | None = None) -> dict:
    """逐月推進、逐年取樣的資產成長模擬。

    reinvest=False 時，配息不參與複利：本金以（報酬率 − 配息率）成長，
    配息每月落入不生息的現金池。這會讓長期結果低於再投入，是刻意呈現的差異。
    """
    initial = max(float(initial or 0), 0.0)
    monthly = max(float(monthly or 0), 0.0)
    years = max(float(years or 0), 0.0)
    months_total = int(round(years * 12))

    net_annual = annual_return - max(fee, 0.0)
    dy = max(dividend_yield, 0.0) if not reinvest else 0.0
    dy = min(dy, max(net_annual, 0.0))          # 配息率不會超過總報酬
    growth_annual = net_annual - dy
    r_m = monthly_rate(growth_annual)
    dy_m = monthly_rate(dy) if dy > 0 else 0.0

    value, cash_pot = initial, 0.0
    series = [_snapshot(0, initial, 0.0, initial, 0.0, inflation,
                        start_age, start_year)]
    for m in range(1, months_total + 1):
        if dy_m > 0:
            cash_pot += value * dy_m
        value = value * (1 + r_m) + monthly
        if m % 12 == 0:
            y = m // 12
            series.append(_snapshot(y, initial, monthly * m, value + cash_pot,
                                    cash_pot, inflation, start_age, start_year))
    # 非整年的尾巴（如 10.5 年）也補一筆，讓期末數字對得上
    if months_total % 12 and months_total > 0:
        series.append(_snapshot(years, initial, monthly * months_total,
                                value + cash_pot, cash_pot, inflation,
                                start_age, start_year))

    final = series[-1]
    contributed = initial + monthly * months_total
    gain = final["value"] - contributed
    return {
        "series": series,
        "years": years, "months": months_total,
        "annual_return": round(annual_return, 2),
        "net_return": round(net_annual, 2),
        "fee": round(max(fee, 0.0), 3),
        "reinvest": reinvest,
        "dividend_yield": round(dy, 2),
        "initial": round(initial),
        "monthly": round(monthly),
        "total_contributed": round(contributed),
        "monthly_contributed": round(monthly * months_total),
        "final_value": final["value"],
        "final_real_value": final["real_value"],
        "investment_gain": round(gain),
        "gain_ratio": round(gain / final["value"] * 100, 1) if final["value"] > 0 else 0.0,
        "cash_pot": round(cash_pot),
    }


def _snapshot(year: float, initial: float, contributed: float, value: float,
              cash_pot: float, inflation: float,
              start_age: float | None, start_year: int | None) -> dict:
    """單一年度的拆解：初始本金 / 累積投入 / 投資收益（spec §16 堆疊面積圖用）。"""
    returns = value - initial - contributed
    deflator = (1 + inflation / 100) ** year if inflation > -100 else 1.0
    return {
        "year": round(year, 2),
        "calendar_year": int(start_year + year) if start_year else None,
        "age": round(start_age + year) if start_age else None,
        "initial": round(initial),
        "contributed": round(contributed),
        "total_contributed": round(initial + contributed),
        "returns": round(returns),
        "value": round(value),
        "real_value": round(value / deflator) if deflator else round(value),
        "cash_pot": round(cash_pot),
    }


def scenarios(initial: float, monthly: float, years: float, rates: dict,
              **kwargs) -> dict:
    """三情境模擬（spec §12）。rates 形如 {conservative: 4, base: 7, optimistic: 10}。"""
    out = {}
    for key in ("conservative", "base", "optimistic"):
        if key in rates and rates[key] is not None:
            out[key] = project(initial, monthly, years, float(rates[key]), **kwargs)
    return {"scenarios": out, "note": SCENARIO_NOTE}


def milestones(series: list[dict], targets: list[float] | None = None,
               custom_goal: float | None = None) -> list[dict]:
    """每個里程碑第一次被跨過的年份（spec §19）。沒達到就回 reached=False。"""
    targets = list(targets or DEFAULT_MILESTONES)
    if custom_goal:
        targets.append(float(custom_goal))
    out = []
    for t in sorted(set(targets)):
        hit = next((s for s in series if s["value"] >= t), None)
        out.append({
            "target": t,
            "reached": hit is not None,
            "year": hit["year"] if hit else None,
            "age": hit["age"] if hit else None,
            "calendar_year": hit["calendar_year"] if hit else None,
            "is_custom": custom_goal is not None and abs(t - float(custom_goal)) < 1,
        })
    return out
