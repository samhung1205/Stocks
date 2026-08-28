"""財務健檢（spec §4-§6）：可支配所得、緊急預備金、負債、建議每月投資金額。

刻意回傳「區間」而不是硬性數字——平台沒有辦法知道使用者的工作穩定度、家庭狀況、
未來大額支出，所以給範圍與理由，讓使用者自己決定。所有語氣避免絕對化。

純函式：吃 dict、吐 dict，不碰 DB。
"""

ESSENTIAL_KEYS = ("exp_living", "exp_housing", "exp_insurance", "exp_loan", "exp_other")
ASSET_KEYS = ("asset_cash", "asset_deposit", "asset_stock", "asset_etf",
              "asset_bond", "asset_other")
DEBT_KEYS = ("debt_mortgage", "debt_car", "debt_credit", "debt_card", "debt_other")

GOAL_LABELS = {
    "accumulate": "長期累積資產", "house": "買房", "education": "子女教育",
    "fire": "提早退休", "retire": "一般退休", "passive": "建立被動收入",
    "preserve": "資產保值", "other": "其他",
}
# 短期內可能要動用大筆現金的目標 → 建議投資比例上限下修
SHORT_TERM_GOALS = {"house", "education"}


def _n(profile: dict, key: str) -> float:
    v = profile.get(key)
    try:
        return float(v) if v is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _sum(profile: dict, keys) -> float:
    return sum(_n(profile, k) for k in keys)


def analyze(profile: dict) -> dict:
    """回傳財務健檢結果 + 建議每月投資區間。"""
    income = (_n(profile, "income_monthly") + _n(profile, "income_other")
              + _n(profile, "bonus_annual") / 12)
    essential = _sum(profile, ESSENTIAL_KEYS)
    disposable = income - essential

    liquid = _n(profile, "asset_cash") + _n(profile, "asset_deposit")
    invested = (_n(profile, "asset_stock") + _n(profile, "asset_etf")
                + _n(profile, "asset_bond") + _n(profile, "asset_other"))
    assets = _sum(profile, ASSET_KEYS)
    debts = _sum(profile, DEBT_KEYS)

    months = round(liquid / essential, 1) if essential > 0 else None
    level, emergency_msg = _emergency_verdict(months)

    return {
        "income_total": round(income),
        "essential_expense": round(essential),
        "disposable": round(disposable),
        "liquid_cash": round(liquid),
        "invested_assets": round(invested),
        "total_assets": round(assets),
        "total_debt": round(debts),
        "net_worth": round(assets - debts),
        "debt_ratio": round(debts / assets * 100, 1) if assets > 0 else None,
        "debt_service_ratio": round(_n(profile, "exp_loan") / income * 100, 1)
                              if income > 0 else None,
        "savings_rate": round(disposable / income * 100, 1) if income > 0 else None,
        "emergency_months": months,
        "emergency_level": level,
        "emergency_message": emergency_msg,
        "suggestion": suggest_monthly_invest(profile, disposable, months, level),
    }


def _emergency_verdict(months: float | None) -> tuple[str, str]:
    """spec §5 的三段提示。語氣為觀察與提醒，不下指令。"""
    if months is None:
        return "unknown", "尚未填寫每月必要支出，無法評估緊急預備金月數。"
    if months < 3:
        return "short", (f"可動用現金約可支應 {months} 個月的必要支出。"
                         "緊急預備金可能不足，建議優先增加現金準備，"
                         "再考慮提高投資金額。")
    if months <= 6:
        return "basic", (f"可動用現金約可支應 {months} 個月的必要支出，"
                         "已有基本緊急預備金。")
    return "ample", (f"可動用現金約可支應 {months} 個月的必要支出，"
                     "現金安全墊相對充足，可進一步評估可投資資金。")


# 緊急預備金分級 → 可投資比例區間（佔可支配所得）
_RATIO_BY_LEVEL = {
    "short": (0.10, 0.30),
    "basic": (0.40, 0.60),
    "ample": (0.50, 0.70),
    "unknown": (0.30, 0.50),
}


def suggest_monthly_invest(profile: dict, disposable: float,
                           months: float | None, level: str) -> dict:
    """依可支配所得、緊備金、年齡、投資目標給「區間」建議（spec §6）。"""
    reasons: list[str] = []
    if disposable <= 0:
        return {
            "low": 0, "high": 0, "mid": 0, "reasons": [
                "目前每月收入扣除必要支出與固定負債後沒有結餘，"
                "在增加投資之前，先檢視支出結構可能更有幫助。"],
            "summary": "每月可支配所得為 0 或負值，暫不建議設定定期投資金額。",
        }

    low_r, high_r = _RATIO_BY_LEVEL[level]
    reasons.append({
        "short": "緊急預備金低於 3 個月，投資比例先抓保守，優先補足現金。",
        "basic": "緊急預備金在 3～6 個月，可投入可支配所得的中等比例。",
        "ample": "緊急預備金超過 6 個月，可投入的比例相對可以高一些。",
        "unknown": "缺少必要支出資料，先用中性比例估算。",
    }[level])

    age = _n(profile, "age")
    retire_age = _n(profile, "retire_age") or 65
    years_to_retire = retire_age - age
    if age and age < 35 and years_to_retire > 25:
        low_r, high_r = low_r + 0.05, high_r + 0.05
        reasons.append("距離退休超過 25 年，時間本身是最大的優勢，"
                       "投資比例可略為提高。")
    elif 0 < years_to_retire <= 5:
        high_r -= 0.05
        reasons.append("距離退休不到 5 年，保留較多現金有助於降低"
                       "退休初期被迫賣出資產的風險。")

    goal = profile.get("goal")
    if goal in SHORT_TERM_GOALS:
        low_r, high_r = low_r - 0.05, high_r - 0.15
        reasons.append(f"投資目的為「{GOAL_LABELS.get(goal, goal)}」，"
                       "數年內可能需要動用大筆現金，投資比例宜留餘裕。")

    low_r, high_r = max(0.0, low_r), max(0.0, high_r)
    if high_r < low_r:
        low_r = high_r
    low = _round_to(disposable * low_r, 1000)
    high = _round_to(disposable * high_r, 1000)
    mid = _round_to((low + high) / 2, 1000)

    summary = (f"依目前財務資料，每月可支配所得為 NT${disposable:,.0f}。"
               + (f"若維持目前緊急預備金，投資金額可考慮設定在 "
                  f"NT${low:,.0f}～{high:,.0f}。" if high > 0 else
                  "現階段可考慮先把資金用於補足緊急預備金。")
               + "這是依規則算出的參考範圍，實際金額請自行決定。")
    return {"low": low, "high": high, "mid": mid,
            "reasons": reasons, "summary": summary}


def _round_to(x: float, unit: int) -> float:
    return round(x / unit) * unit


def resolve_monthly_invest(profile: dict, health: dict) -> float:
    """使用者填了就用他的；沒填就用建議區間中位數。"""
    v = profile.get("monthly_invest")
    if v is not None and str(v) != "":
        try:
            return max(0.0, float(v))
        except (TypeError, ValueError):
            pass
    return float(health["suggestion"]["mid"])
