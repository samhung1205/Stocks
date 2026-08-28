"""個股評分表（框架第五章，總分 100）。

量化面向自動預填，質化面向（產業/競爭力/催化劑）需人工評分；
人工分數一律可覆寫自動分數。分數解讀：80+ 深入研究、65-79 觀察、
50-64 波段/事件、<50 不優先。
"""
import datetime as dt

from app.db import engine, query_one, upsert_many
from app.db.tables import scores
from app.services import stock_view, alerts as alerts_svc

WEIGHTS = {
    "industry": 15, "moat": 15, "growth": 15, "cashflow": 15,
    "valuation": 15, "catalyst": 10, "chips": 10, "governance": 5,
}
LABELS = {
    "industry": "產業趨勢", "moat": "公司競爭力", "growth": "營收與獲利",
    "cashflow": "現金流與財務", "valuation": "估值", "catalyst": "催化劑",
    "chips": "籌碼與技術", "governance": "治理與風險",
}
MANUAL_ONLY = {"industry", "moat", "catalyst"}


def _clip(x, lo, hi):
    return max(lo, min(hi, x))


def auto_scores(stock_id: str) -> dict[str, dict]:
    """回傳各面向 {auto, max, reason}。質化面向 auto=None。"""
    out = {k: {"auto": None, "max": WEIGHTS[k], "reasons": []} for k in WEIGHTS}

    # 營收與獲利（15）：月營收 YoY、3 月累計、毛利率方向、EPS 成長
    rev = stock_view.revenue_series(stock_id, 15)
    fins = stock_view.financial_series(stock_id)
    pts, why = 7.5, []
    if rev:
        last = rev[-1]
        if last.get("yoy") is not None:
            yoy = last["yoy"]
            pts += _clip(yoy / 10, -4, 4)
            why.append(f"最新月營收 YoY {yoy:.1f}%")
        if last.get("acc_yoy") is not None:
            pts += _clip(last["acc_yoy"] / 15, -2, 2)
            why.append(f"累計 YoY {last['acc_yoy']:.1f}%")
    if len(fins) >= 5:
        gm_now, gm_prev = fins[-1].get("gross_margin"), fins[-5].get("gross_margin")
        if gm_now is not None and gm_prev is not None:
            diff = gm_now - gm_prev
            pts += _clip(diff / 2, -1.5, 1.5)
            why.append(f"毛利率年變化 {diff:+.1f}pp")
    out["growth"].update(auto=round(_clip(pts, 0, 15), 1), reasons=why)

    # 現金流與財務（15）：OCF/淨利、負債比、FCF
    pts, why = 7.5, []
    if len(fins) >= 4:
        ocf4 = sum(f.get("op_cashflow") or 0 for f in fins[-4:])
        ni4 = sum(f.get("net_income") or 0 for f in fins[-4:])
        if ni4 > 0:
            ratio = ocf4 / ni4
            pts += _clip((ratio - 0.8) * 5, -4, 4)
            why.append(f"近四季 OCF/淨利 {ratio:.2f}")
        fcf4 = sum(f.get("fcf") or 0 for f in fins[-4:] if f.get("fcf") is not None)
        if fcf4 > 0:
            pts += 1.5
            why.append("近四季自由現金流為正")
        dr = fins[-1].get("debt_ratio")
        if dr is not None:
            pts += _clip((55 - dr) / 10, -2, 2)
            why.append(f"負債比 {dr:.0f}%")
    out["cashflow"].update(auto=round(_clip(pts, 0, 15), 1), reasons=why)

    # 估值（15）：PE/PB 自身歷史百分位（低位=高分）
    vb = stock_view.valuation_band(stock_id)
    pts, why = 7.5, []
    if vb.get("pe_percentile") is not None:
        pts += (50 - vb["pe_percentile"]) / 50 * 5
        why.append(f"PE 位於歷史第 {vb['pe_percentile']:.0f} 百分位")
    if vb.get("pb_percentile") is not None:
        pts += (50 - vb["pb_percentile"]) / 50 * 2.5
        why.append(f"PB 位於歷史第 {vb['pb_percentile']:.0f} 百分位")
    out["valuation"].update(auto=round(_clip(pts, 0, 15), 1), reasons=why)

    # 籌碼與技術（10）：均線位置、法人 20 日累計
    tech = stock_view.technical_summary(stock_id)
    chips = stock_view.chips_series(stock_id, 20)
    pts, why = 5.0, []
    if tech.get("above_ma60") is not None:
        pts += 1.5 if tech["above_ma60"] else -1.5
        why.append("站上60日線" if tech["above_ma60"] else "跌破60日線")
    if tech.get("above_ma240") is not None:
        pts += 1.0 if tech["above_ma240"] else -1.0
        why.append("站上年線" if tech["above_ma240"] else "年線之下")
    if chips.get("net20"):
        pts += _clip(chips["net20"] / 5e6, -2.5, 2.5)
        why.append(f"法人20日買賣超 {chips['net20']/1000:,.0f} 張")
    out["chips"].update(auto=round(_clip(pts, 0, 10), 1), reasons=why)

    # 治理與風險（5）：警訊數扣分
    alist = alerts_svc.scan(stock_id)
    pts = _clip(5 - len(alist) * 1.5, 0, 5)
    out["governance"].update(
        auto=round(pts, 1),
        reasons=[a["message"] for a in alist] or ["未偵測到量化警訊"])
    return out


def get_scorecard(stock_id: str) -> dict:
    auto = auto_scores(stock_id)
    with engine.begin() as conn:
        manual = query_one(conn, "SELECT * FROM scores WHERE stock_id=:s", s=stock_id) or {}
    items, total, filled = [], 0.0, True
    for key, w in WEIGHTS.items():
        m = manual.get(key)
        a = auto[key]["auto"]
        eff = m if m is not None else a
        if eff is None:
            filled = False
        else:
            total += eff
        items.append({
            "key": key, "label": LABELS[key], "max": w,
            "auto": a, "manual": m, "effective": eff,
            "reasons": auto[key]["reasons"],
            "manual_only": key in MANUAL_ONLY,
        })
    verdict = None
    if filled:
        t = total
        verdict = ("值得深入研究，仍須確認估值與買點" if t >= 80 else
                   "放入觀察名單，等待價格或基本面確認" if t >= 65 else
                   "可能僅適合波段或事件交易" if t >= 50 else
                   "除非有明確短線策略，否則不優先考慮")
    return {"items": items, "total": round(total, 1), "complete": filled,
            "verdict": verdict, "note": manual.get("note")}


def narrative_summary(stock_id: str) -> dict:
    """把量化評分的 reasons 串成白話摘要，附多空傾向與待人工判斷清單。

    只陳述量化面向算出來的東西（營收/現金流/估值/籌碼/治理），
    不生成產業、競爭力、催化劑這些需要主觀判斷的內容 — 避免假裝系統
    有能力做出它其實做不到的判斷。
    """
    card = get_scorecard(stock_id)
    quant_items = [it for it in card["items"] if it["key"] not in MANUAL_ONLY]
    quant_max = sum(it["max"] for it in quant_items)
    quant_score = sum((it["effective"] or 0) for it in quant_items)
    lines = [{"label": it["label"], "effective": it["effective"], "max": it["max"],
              "detail": "；".join(it["reasons"]) if it["reasons"] else "資料不足"}
             for it in quant_items]
    tilt = ("偏多" if quant_score >= quant_max * 0.65 else
            "偏空" if quant_score <= quant_max * 0.4 else "中性")
    missing = [it["label"] for it in card["items"] if it["manual_only"] and it["manual"] is None]
    return {
        "tilt": tilt, "quant_score": round(quant_score, 1), "quant_max": quant_max,
        "lines": lines, "missing": missing,
    }


def save_manual(stock_id: str, payload: dict) -> None:
    row = {"stock_id": stock_id,
           "updated_at": dt.datetime.now().isoformat(timespec="seconds")}
    for key in WEIGHTS:
        if key in payload:
            v = payload[key]
            row[key] = None if v in (None, "") else _clip(float(v), 0, WEIGHTS[key])
    if "note" in payload:
        row["note"] = payload["note"]
    with engine.begin() as conn:
        upsert_many(conn, scores, [row])
