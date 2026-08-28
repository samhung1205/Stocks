"""再平衡建議（spec §24）。

優先設計 Cash-flow Rebalancing：用「下一期的新資金」優先補足低於目標比例的部位，
而不是叫使用者賣掉漲多的 ETF。理由是賣出會產生交易成本與稅，而定期定額的人本來
每個月就有新資金可以用來調整方向。

只有在新資金連續數月都補不回來時，才會提示可以考慮調整既有部位——而且是提示，
不是指令。

純函式：不碰 DB。actual_value 由 service 層從交易日誌帶入或由使用者填。
"""

DRIFT_THRESHOLD = 5.0        # 偏離幾個百分點以上才視為需要處理
CONVERGE_MONTHS_LIMIT = 12   # 新資金若超過這個月數還補不回來，才提示考慮賣出


def analyze(targets: list[dict], monthly_contribution: float,
            drift_threshold: float = DRIFT_THRESHOLD) -> dict:
    """比較目前配置與目標配置，並把下一期投入分配到低於目標的部位。

    targets 每筆需有 stock_id / weight(目標 %) / actual_value(目前市值)。
    """
    rows = [t for t in targets if (t.get("weight") or 0) > 0
            or (t.get("actual_value") or 0) > 0]
    total_value = sum(float(t.get("actual_value") or 0) for t in rows)
    contribution = max(float(monthly_contribution or 0), 0.0)

    if total_value <= 0:
        return {
            "has_holdings": False,
            "total_value": 0, "contribution": round(contribution),
            "rows": [], "max_drift": 0.0, "needs_action": False,
            "message": "尚未有實際持倉資料，記錄成交或直接填入目前市值後即可比較配置偏離。",
        }

    future = total_value + contribution
    needs, out = {}, []
    for t in rows:
        sid = t["stock_id"]
        value = float(t.get("actual_value") or 0)
        target_w = float(t.get("weight") or 0)
        current_w = value / total_value * 100
        # 投入後要達到目標權重，這個部位還缺多少錢
        need = max(0.0, future * target_w / 100 - value)
        needs[sid] = need
        out.append({
            "stock_id": sid,
            "role": t.get("role"), "role_label": t.get("role_label"),
            "name": t.get("name"),
            "target_weight": round(target_w, 1),
            "current_weight": round(current_w, 1),
            "drift": round(current_w - target_w, 1),
            "value": round(value),
            "need": round(need),
        })

    need_total = sum(needs.values())
    for r in out:
        share = (needs[r["stock_id"]] / need_total) if need_total > 0 else 0.0
        r["suggested_contribution"] = round(contribution * share)
        r["after_weight"] = round(
            (r["value"] + r["suggested_contribution"]) / future * 100, 1) if future else 0.0
        r["after_drift"] = round(r["after_weight"] - r["target_weight"], 1)

    out.sort(key=lambda r: r["drift"])
    max_drift = max((abs(r["drift"]) for r in out), default=0.0)
    needs_action = max_drift >= drift_threshold

    # 以目前投入速度，要幾個月才能把缺口補完
    months = None
    if contribution > 0 and need_total > 0:
        months = round(need_total / contribution, 1)

    return {
        "has_holdings": True,
        "total_value": round(total_value),
        "contribution": round(contribution),
        "rows": out,
        "max_drift": round(max_drift, 1),
        "needs_action": needs_action,
        "need_total": round(need_total),
        "months_to_converge": months,
        "message": _message(needs_action, max_drift, months, contribution),
    }


def _message(needs_action: bool, max_drift: float, months: float | None,
             contribution: float) -> str:
    if not needs_action:
        return (f"目前配置與目標配置最大偏離 {max_drift:.1f} 個百分點，"
                "在一般可接受的範圍內，維持原本的定期投入即可。")
    base = (f"目前配置與目標配置偏離，最大偏離 {max_drift:.1f} 個百分點。"
            "建議透過下一期投入資金優先補足低於目標比例的資產，"
            "而不是賣出已經上漲的部位。")
    if contribution <= 0:
        return base + "（目前沒有設定每月投入，所以無法用新資金調整。）"
    if months and months > CONVERGE_MONTHS_LIMIT:
        return (base + f"不過以目前每月投入的金額估算，約需 {months:.0f} 個月才能補回，"
                "若你希望更快回到目標比例，也可以評估調整既有部位——"
                "但要一併考慮交易成本與稅。")
    if months:
        return base + f"以目前每月投入金額估算，約 {months:.0f} 個月可以補回。"
    return base
