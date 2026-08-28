"""ETF 資產配置引擎（spec §8-§10、§25-§27）。

設計原則（spec §9）：規則透明、可被使用者覆寫，不做黑箱推薦。

流程分兩步，刻意分開是為了讓推薦過程可解釋：
  1. role_weights()  — 先決定「角色級距」（核心/成長/現金流/防守/現金各佔幾 %）
  2. allocate()      — 再把角色權重攤到使用者實際啟用的標的上

角色權重不是查表，而是以 spec §9 的三組模板為錨點、依「距退休年數」做分段線性內插，
再套四條調整規則。理由全部回傳給前端顯示，使用者看得到為什麼是這個數字。

純函式：不碰 DB，標的名稱由 service 層補。
"""
from app.services.finance import universe

ROLES = ("core_tw", "core_us", "growth", "income", "defense", "cash")

# spec §9 的三組錨點，key = 距退休年數。各自對應的風險型態隱含 bias 見 ANCHOR_BIAS。
ANCHORS = {
    35: {"core_tw": 45, "core_us": 35, "growth": 15, "income": 5,
         "defense": 0, "cash": 0},        # 25 歲 / 積極型 / 30 年以上
    25: {"core_tw": 32, "core_us": 30, "growth": 8, "income": 15,
         "defense": 15, "cash": 0},       # 40 歲 / 平衡型
    5: {"core_tw": 28, "core_us": 14, "growth": 0, "income": 20,
        "defense": 25, "cash": 13},       # 60 歲 / 接近退休 / 穩健型
}
ANCHOR_BIAS = {35: 1.0, 25: 0.5, 5: 0.25}

GROWTH_ROLES = ("core_tw", "core_us", "growth")
DEFENSIVE_ROLES = ("defense", "cash")

MAX_RISK_SHIFT = 25.0      # 風險型態相對錨點最多搬動幾個百分點
INCOME_BOOST = 8.0         # 需要配息現金流時，income 角色加碼幾個百分點

INCOME_DISCLAIMER = ("配息不等於額外報酬，應以含息總報酬衡量長期績效。")


def _interp(years: float) -> dict[str, float]:
    """依距退休年數在錨點之間做分段線性內插。"""
    keys = sorted(ANCHORS)                      # [5, 25, 35]
    y = max(keys[0], min(keys[-1], years))
    for lo, hi in zip(keys, keys[1:]):
        if lo <= y <= hi:
            f = (y - lo) / (hi - lo) if hi > lo else 0.0
            return {r: ANCHORS[lo][r] + (ANCHORS[hi][r] - ANCHORS[lo][r]) * f
                    for r in ROLES}
    return dict(ANCHORS[keys[-1]])


def _interp_bias(years: float) -> float:
    keys = sorted(ANCHOR_BIAS)
    y = max(keys[0], min(keys[-1], years))
    for lo, hi in zip(keys, keys[1:]):
        if lo <= y <= hi:
            f = (y - lo) / (hi - lo) if hi > lo else 0.0
            return ANCHOR_BIAS[lo] + (ANCHOR_BIAS[hi] - ANCHOR_BIAS[lo]) * f
    return ANCHOR_BIAS[keys[-1]]


def _shift(w: dict, frm: tuple, to: tuple, amount: float) -> float:
    """把 amount 個百分點從 frm 角色群依現有比例搬到 to 角色群。回傳實際搬動量。"""
    pool = sum(w[r] for r in frm)
    amount = min(amount, pool)
    if amount <= 0 or pool <= 0:
        return 0.0
    for r in frm:
        w[r] -= amount * (w[r] / pool)
    to_pool = sum(w[r] for r in to)
    for r in to:
        # 目標群全為 0 時平均分配，否則依現有比例
        w[r] += amount * (w[r] / to_pool if to_pool > 0 else 1 / len(to))
    return amount


def role_weights(age: float, retire_age: float, equity_bias: float,
                 need_income: bool, emergency_level: str,
                 horizon_years: float | None = None) -> dict:
    """算出各角色的目標權重（%）與每一條調整的理由。"""
    retire_age = retire_age or 65
    years = (retire_age - age) if age else 30
    if horizon_years:
        # 投資年限比距退休年數短時，以較短者為準（錢更早要用）
        years = min(years, horizon_years)
    retired = age >= retire_age if age and retire_age else False

    w = _interp(years)
    reasons = [f"距離退休約 {years:.0f} 年，以此在「年輕成長」與「接近退休保全」"
               f"兩組模板之間內插出基準配置。"]

    # 規則一：風險型態相對於該年齡層預設的偏移
    delta = equity_bias - _interp_bias(years)
    if abs(delta) > 0.01:
        amount = abs(delta) * MAX_RISK_SHIFT
        if delta > 0:
            moved = _shift(w, DEFENSIVE_ROLES, GROWTH_ROLES, amount)
            if moved > 0.5:
                reasons.append(f"風險承受度高於同年齡層的預設，"
                               f"自防守部位移轉約 {moved:.0f} 個百分點到股票部位。")
        else:
            moved = _shift(w, GROWTH_ROLES, DEFENSIVE_ROLES, amount)
            if moved > 0.5:
                reasons.append(f"風險承受度低於同年齡層的預設，"
                               f"自股票部位移轉約 {moved:.0f} 個百分點到防守部位。")

    # 規則二：需要配息現金流 / 已退休
    if need_income or retired:
        moved = _shift(w, ("core_tw", "core_us", "growth"), ("income",), INCOME_BOOST)
        if moved > 0.5:
            reasons.append(f"{'已進入退休階段' if retired else '勾選了需要配息現金流'}，"
                           f"現金流部位加重約 {moved:.0f} 個百分點。{INCOME_DISCLAIMER}")

    # 規則三：緊急預備金不足 → 現金加碼（先把安全墊補起來再談成長）
    if emergency_level == "short":
        moved = _shift(w, GROWTH_ROLES, ("cash",), 5.0)
        if moved > 0.5:
            reasons.append("緊急預備金低於 3 個月，配置中先保留較多現金，"
                           "避免市場下跌時被迫賣出資產。")

    # 規則四：距退休 10 年內壓低成長型衛星（降低 Sequence of Returns Risk）
    if years < 10 and w["growth"] > 10:
        excess = w["growth"] - 10
        w["growth"] = 10
        to_pool = sum(w[r] for r in DEFENSIVE_ROLES)
        for r in DEFENSIVE_ROLES:
            w[r] += excess * (w[r] / to_pool if to_pool > 0 else 0.5)
        reasons.append(f"距離退休不到 10 年，成長型衛星壓到 10% 以內，"
                       f"降低退休初期遇到大跌的衝擊（Sequence of Returns Risk）。")

    total = sum(w.values())
    w = {r: round(w[r] / total * 100, 1) for r in ROLES} if total > 0 else w
    return {"weights": w, "reasons": reasons,
            "years_to_retire": round(years, 1), "retired": retired}


def allocate(selected: list[str], roles: dict[str, float],
             initial_capital: float, monthly: float,
             override: dict[str, float] | None = None,
             returns: dict[str, float] | None = None) -> dict:
    """把角色權重攤到啟用中的標的，算出每檔的目標權重與投入金額。

    selected  啟用的標的代號（含 CASH）
    roles     各角色目標權重 %
    override  使用者自訂的每檔權重 %（有值就用它，規則結果仍會回傳供對照）
    returns   使用者自訂的每檔假設報酬 %（缺的用 role 預設）
    """
    returns = returns or {}
    selected = [s for s in selected if s]
    if not selected:
        return {"targets": [], "suggested": [], "using_override": False,
                "expected_return": 0.0, "expected_fee": 0.0,
                "exposure": {"asset": {}, "region": {}}, "warnings": [],
                "weight_sum": 0.0}

    by_role: dict[str, list[str]] = {}
    for sid in selected:
        by_role.setdefault(universe.meta(sid)["role"], []).append(sid)

    # 規則權重：角色權重在該角色的標的間均分；沒有標的的角色，其權重按比例回流到有標的的角色
    live = {r: v for r, v in roles.items() if by_role.get(r)}
    live_total = sum(live.values())
    suggested: dict[str, float] = {}
    for r, ids in by_role.items():
        w = (live.get(r, 0.0) / live_total * 100) if live_total > 0 else 100 / len(selected)
        for sid in ids:
            suggested[sid] = round(w / len(ids), 1)

    using_override = bool(override) and any(
        v is not None for v in override.values())
    weights = {sid: float(override.get(sid) or 0) for sid in selected} if using_override \
        else dict(suggested)
    weight_sum = round(sum(weights.values()), 1)

    rows = []
    for sid in selected:
        m = universe.meta(sid)
        r = returns.get(sid)
        rows.append({
            "stock_id": sid, "role": m["role"],
            "role_label": universe.ROLE_DEFAULTS[m["role"]]["label"],
            "asset": universe.ROLE_DEFAULTS[m["role"]]["asset"],
            "region": m["region"], "overlap": m["overlap"],
            "fee": m["fee"], "note": m["note"], "custom": m["custom"],
            "income_focus": m["income_focus"],
            "weight": round(weights[sid], 1),
            "suggested_weight": suggested[sid],
            "expected_return": float(r) if r not in (None, "") else universe.default_return(sid),
            "initial_amount": round(initial_capital * weights[sid] / 100),
            "monthly_amount": round(monthly * weights[sid] / 100),
        })
    rows.sort(key=lambda x: (ROLES.index(x["role"]) if x["role"] in ROLES else 9,
                             -x["weight"]))

    # 組合層級指標：以權重加權（權重和不為 100 時以實際和為分母，避免灌水）
    denom = weight_sum if weight_sum > 0 else 100.0
    exp_return = sum(r["weight"] * r["expected_return"] for r in rows) / denom
    exp_fee = sum(r["weight"] * r["fee"] for r in rows) / denom

    return {
        "targets": rows,
        "suggested": suggested,
        "using_override": using_override,
        "weight_sum": weight_sum,
        "expected_return": round(exp_return, 2),
        "expected_fee": round(exp_fee, 3),
        "exposure": exposures(rows, denom),
        "warnings": warnings(rows, denom),
    }


def exposures(rows: list[dict], denom: float) -> dict:
    """資產類別（spec §27）與國家曝險（spec §26）。"""
    asset: dict[str, float] = {}
    region: dict[str, float] = {}
    for r in rows:
        asset[r["asset"]] = asset.get(r["asset"], 0) + r["weight"]
        region[r["region"]] = region.get(r["region"], 0) + r["weight"]
    norm = lambda d: {k: round(v / denom * 100, 1) for k, v in d.items() if v > 0}
    return {"asset": norm(asset), "region": norm(region)}


def warnings(rows: list[dict], denom: float) -> list[dict]:
    """組合層級提示。全部是「陳述觀察 + 可以評估」，不要求使用者一定要改。"""
    out: list[dict] = []

    # 重疊（spec §25）：同 overlap 群組有兩檔以上且都有實際權重
    groups: dict[str, list[dict]] = {}
    for r in rows:
        if r["weight"] > 0:
            groups.setdefault(r["overlap"], []).append(r)
    for gid, members in groups.items():
        if len(members) < 2:
            continue
        note = universe.OVERLAP_NOTES.get(gid)
        if not note:
            continue
        ids = "、".join(m["stock_id"] for m in members)
        out.append({"level": "overlap",
                    "message": f"{ids}：{note}",
                    "detail": f"合計權重 {sum(m['weight'] for m in members):.0f}%"})

    exp = exposures(rows, denom)
    tw = exp["region"].get("TW", 0)
    if tw > 80:
        out.append({"level": "region",
                    "message": f"目前投資組合 {tw:.0f}% 集中於台灣市場，"
                               "可以評估是否需要海外分散。",
                    "detail": "這是觀察，不是必須調整的指示。"})
    equity = exp["asset"].get("equity", 0)
    if equity > 90:
        out.append({"level": "asset",
                    "message": f"股票部位佔 {equity:.0f}%，組合幾乎完全暴露在股市波動。"
                               "持有多檔 ETF 不等於資產類別分散。",
                    "detail": "若這個波動程度超出你能接受的範圍，可考慮加入債券或現金。"})
    if any(r["income_focus"] and r["weight"] > 0 for r in rows):
        out.append({"level": "info", "message": INCOME_DISCLAIMER,
                    "detail": "高股息 ETF 的配息來自淨值，除息當下總資產不會增加。"})
    custom = [r["stock_id"] for r in rows if r["custom"] and r["weight"] > 0]
    if custom:
        out.append({"level": "info",
                    "message": f"{'、'.join(custom)} 不在內建名單中，"
                               "其角色、資產類別與國家曝險為預設值，請自行確認。",
                    "detail": "內建名單的分類為人工維護，免費資料源無法提供這些欄位。"})
    return out
