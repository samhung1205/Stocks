"""退休規劃與提款模擬（spec §20-§21）。

兩個問題：
  1. 退休時需要多少資產？→ 先算「退休後每年缺口」，再用提款率反推所需資產
  2. 退休後每年提款，資產可以撐到幾歲？→ 逐年模擬餘額

提款金額採「固定實質購買力」：第一年提 X 元，之後每年隨通膨調高，
這是 4% 法則的標準假設。提款在年初發生（較保守），餘額再以該年報酬率成長。

Sequence of Returns Risk（spec §21）：退休初期若遇上負報酬，同樣的平均報酬率
會得到完全不同的結果——因為提款把低點的部位永久賣掉了。這裡以「前 N 年套用
負報酬、其餘年份補回同樣的長期平均」呈現差異。

純函式：不碰 DB。
"""

DEFAULT_WITHDRAW_RATES = [3.0, 4.0, 5.0]
SORR_YEARS = 3
SORR_SHOCK = -10.0          # 退休前 3 年的年報酬（%）

SORR_NOTE = ("如果退休初期就遇到負報酬，提款會在低點賣出部位，即使之後市場回升，"
             "資產也可能回不到原本的軌道，這稱為 Sequence of Returns Risk。"
             "保留一部分短天期債券或現金，可以在市場下跌時先動用，避免被迫賣股。")


def gap(retire_expense_monthly: float, pension_monthly: float,
        other_income_monthly: float, years_to_retire: float,
        inflation: float = 2.0) -> dict:
    """退休後每年的收支缺口。以今日購買力輸入，同時給退休當年的名目金額。"""
    expense = max(float(retire_expense_monthly or 0), 0.0)
    income = max(float(pension_monthly or 0), 0.0) + max(float(other_income_monthly or 0), 0.0)
    monthly_gap = expense - income
    annual_today = monthly_gap * 12
    infl = (1 + inflation / 100) ** max(years_to_retire, 0)
    return {
        "expense_monthly": round(expense),
        "income_monthly": round(income),
        "monthly_gap": round(monthly_gap),
        "annual_gap_today": round(annual_today),
        "annual_gap_at_retire": round(annual_today * infl),
        "monthly_gap_at_retire": round(monthly_gap * infl),
        "inflation_factor": round(infl, 3),
        "covered_by_income": monthly_gap <= 0,
    }


def required_corpus(annual_gap_at_retire: float,
                    rates: list[float] | None = None) -> list[dict]:
    """用不同提款率反推退休時所需資產（4% 法則的算法：年支出 ÷ 提款率）。"""
    rates = rates or DEFAULT_WITHDRAW_RATES
    out = []
    for r in rates:
        out.append({
            "rate": r,
            "required": round(annual_gap_at_retire / (r / 100)) if r > 0 else None,
        })
    return out


def withdraw_simulation(balance_at_retire: float, annual_withdraw: float,
                        retire_age: float, life_expectancy: float,
                        annual_return: float, inflation: float = 2.0,
                        sorr: bool = False, sorr_shock: float = SORR_SHOCK,
                        sorr_years: int = SORR_YEARS) -> dict:
    """退休後逐年提款模擬。回傳每年餘額、耗盡年齡、可支撐年數。"""
    balance = max(float(balance_at_retire or 0), 0.0)
    withdraw = max(float(annual_withdraw or 0), 0.0)
    retire_age = float(retire_age or 65)
    horizon = max(int(round((life_expectancy or 90) - retire_age)), 0)

    series = [{"age": round(retire_age), "year": 0, "withdraw": 0,
               "balance": round(balance), "real_balance": round(balance),
               "return_pct": None}]
    depleted_age = None
    for y in range(1, horizon + 1):
        r = annual_return
        if sorr and y <= sorr_years:
            r = sorr_shock
        actual = min(withdraw, balance)
        balance -= actual
        balance *= (1 + r / 100)
        balance = max(balance, 0.0)
        if depleted_age is None and balance <= 0:
            depleted_age = retire_age + y
        deflator = (1 + inflation / 100) ** y
        series.append({
            "age": round(retire_age + y), "year": y,
            "withdraw": round(actual),
            "balance": round(balance),
            "real_balance": round(balance / deflator) if deflator else round(balance),
            "return_pct": round(r, 2),
        })
        withdraw *= (1 + inflation / 100)      # 維持固定實質購買力

    return {
        "series": series,
        "annual_withdraw_first_year": round(max(float(annual_withdraw or 0), 0.0)),
        "annual_return": round(annual_return, 2),
        "depleted_age": depleted_age,
        "years_supported": (depleted_age - retire_age) if depleted_age else horizon,
        "lasts_through": depleted_age is None,
        "final_balance": series[-1]["balance"],
        "final_real_balance": series[-1]["real_balance"],
        "sorr": sorr,
    }


def analyze(*, age: float, retire_age: float, life_expectancy: float,
            balance_at_retire: float, retire_expense_monthly: float,
            pension_monthly: float, other_income_monthly: float,
            annual_return: float, inflation: float = 2.0,
            withdraw_mode: str = "gap", withdraw_rate: float = 4.0,
            withdraw_monthly: float | None = None,
            sorr_shock: float = SORR_SHOCK) -> dict:
    """整合：缺口 → 所需資產 → 提款模擬（正常 vs SoRR）→ 三種提款率對照。

    withdraw_mode:
      gap    依退休後收支缺口提款（預設，最貼近實際需求）
      rate   依退休當下資產的固定提款率
      amount 使用者自訂每月提款金額（今日購買力）
    """
    retire_age = float(retire_age or 65)
    age = float(age or 0)
    years_to_retire = max(retire_age - age, 0)
    g = gap(retire_expense_monthly, pension_monthly, other_income_monthly,
            years_to_retire, inflation)

    if withdraw_mode == "rate":
        annual_withdraw = balance_at_retire * (withdraw_rate / 100)
    elif withdraw_mode == "amount":
        infl = (1 + inflation / 100) ** years_to_retire
        annual_withdraw = max(float(withdraw_monthly or 0), 0.0) * 12 * infl
    else:
        annual_withdraw = g["annual_gap_at_retire"]

    common = dict(retire_age=retire_age, life_expectancy=life_expectancy,
                  annual_return=annual_return, inflation=inflation)
    normal = withdraw_simulation(balance_at_retire, annual_withdraw, **common)
    shocked = withdraw_simulation(balance_at_retire, annual_withdraw,
                                  sorr=True, sorr_shock=sorr_shock, **common)

    corpus = required_corpus(g["annual_gap_at_retire"])
    target = next((c for c in corpus if abs(c["rate"] - withdraw_rate) < 0.01), corpus[1])
    required = target["required"]
    shortfall = (required - balance_at_retire) if required else None

    return {
        "years_to_retire": round(years_to_retire, 1),
        "already_retired": age >= retire_age if age else False,
        "gap": g,
        "required_corpus": corpus,
        "required_at_rate": {"rate": withdraw_rate, "required": required},
        "balance_at_retire": round(balance_at_retire),
        "surplus": round(-shortfall) if shortfall is not None else None,
        "sufficient": (shortfall is not None and shortfall <= 0),
        "withdraw_mode": withdraw_mode,
        "annual_withdraw": round(annual_withdraw),
        "monthly_withdraw": round(annual_withdraw / 12),
        "normal": normal,
        "sorr": shocked,
        "sorr_note": SORR_NOTE,
        "sorr_cost_years": (normal["years_supported"] - shocked["years_supported"]),
    }
