"""交易日誌：買進前八句話計畫、交易紀錄、持倉與績效統計。

核心紀律（框架第六章）：先決定最多能虧多少，再反推部位大小。
本機研究助手只整理現有資料與待確認假說；不呼叫外部模型、不替使用者
宣稱市場共識或個人資訊優勢，也不自動建立計畫或記錄成交。
"""
import datetime as dt

from sqlalchemy import insert

from app.config import FEE_RATE, FEE_DISCOUNT, TAX_RATE
from app.db import engine, query_all, query_one
from app.db.tables import trade_plans, trades


PLAN_QUESTIONS = {
    "q1_reason": "研究這家公司的理由",
    "q2_growth_from": "成長來源與後續驗證",
    "q3_market_view": "市場目前的預期",
    "q4_my_edge": "我的看法或研究優勢",
    "q5_priced_in": "價格反映了多少成長",
    "q6_catalyst": "下一個追蹤事件",
    "q7_wrong_signal": "判斷失效的訊號",
}
RESEARCH_FOCUS = {
    "growth": "營收與獲利成長", "quality": "獲利與現金流品質",
    "trend": "價格趨勢與籌碼", "valuation": "估值與安全邊際",
    "observe": "先建立觀察，再決定是否進場",
}


def draft_plan(stock_id: str, focus: str = "observe") -> dict:
    """Read-only evidence draft; all seven answers require human review.

    Retains the original q5/q6/q7/price response keys. New fields contain an
    observed-data snapshot and explicit unknowns, never invented consensus,
    catalysts, risk budgets or personal advantages. No external service call.
    """
    from app.services import stock_view, alerts as alerts_svc

    if focus not in RESEARCH_FOCUS:
        raise ValueError("未知的研究方向")
    overview = stock_view.overview(stock_id)
    if not overview:
        raise LookupError("找不到這檔股票，請先確認代號")
    info, quote = overview["info"], overview.get("quote") or {}
    val = stock_view.valuation_band(stock_id)
    tech = stock_view.technical_summary(stock_id)
    alist = alerts_svc.scan(stock_id)
    revenue = stock_view.revenue_series(stock_id, 3)
    financials = stock_view.financial_series(stock_id)
    cur = val.get("current") or {}
    facts = []

    def fact(label, value, as_of, source):
        facts.append({"label": label, "value": value, "as_of": as_of, "source": source})

    if quote.get("close") is not None:
        fact("最近收盤", f"{quote['close']:,.2f} 元", quote.get("date"), "每日行情")
    growth_parts = []
    if revenue and revenue[-1].get("yoy") is not None:
        r = revenue[-1]
        text = f"{r['ym']} 月營收年增率 {r['yoy']:+.1f}%"
        growth_parts.append(text)
        fact("月營收", text, r["ym"], "月營收資料")
    if financials:
        f = financials[-1]
        if f.get("eps") is not None:
            text = f"{f['quarter']} 每股盈餘 {f['eps']:.2f} 元"
            growth_parts.append(text)
            fact("獲利", text, f["quarter"], "季財報")
        if f.get("op_cashflow") is not None:
            fact("營業現金流", f"{f['op_cashflow']/1e8:,.2f} 億元", f["quarter"], "季財報")
    q5_parts = []
    if cur.get("pe") is not None and val.get("pe_percentile") is not None:
        q5_parts.append(f"本益比 {cur['pe']:.1f} 倍，位於現有歷史樣本第 {val['pe_percentile']:.0f} 百分位")
    if val.get("eps_ttm") is not None:
        q5_parts.append(f"近四季EPS合計 {val['eps_ttm']:.2f} 元")
    if cur.get("dividend_yield") is not None:
        q5_parts.append(f"殖利率 {cur['dividend_yield']:.1f}%")
    if q5_parts:
        fact("估值", "；".join(q5_parts), cur.get("date"), "估值紀錄及財報")
    wrong = [a["message"] for a in alist]
    if tech.get("ma60"):
        wrong.append(f"追蹤股價是否跌破60日線（本次參考值 {tech['ma60']} 元）")
    if tech.get("ma240"):
        wrong.append(f"追蹤股價是否跌破年線（本次參考值 {tech['ma240']} 元）")
    company = f"{stock_id} {info['name']}"
    answers = {
        "q1_reason": f"研究假說：以「{RESEARCH_FOCUS[focus]}」為方向追蹤 {company}；先確認下列證據與風險，再決定是否進場。行情資料日期：{quote.get('date') or '尚無資料'}。",
        "q2_growth_from": ("已知資料：" + "；".join(growth_parts) + "。後續需確認產品、訂單或價格能否延續；歷史數字不代表未來成長。"
                           if growth_parts else "待查證：目前缺少足以整理成長來源的營收或獲利資料，需先補充公司營運證據。"),
        "q3_market_view": "待查證：目前沒有經查核的市場共識資料；不能把股價走勢或我的研究假說當成市場預期。",
        "q4_my_edge": "待查證：尚未記錄可驗證的個人研究優勢；先以公開資料持續追蹤，不假設自己擁有資訊優勢。",
        "q5_priced_in": ("已知資料：" + "；".join(q5_parts) + f"。估值資料日期：{cur.get('date') or '未提供'}。是否反映未來成長仍需結合獲利假設判斷。"
                         if q5_parts else "待查證：估值資料不足，尚無法判斷價格反映程度。"),
        "q6_catalyst": "待查證：下一次月營收、季報或法說會是追蹤節點；實際日期與事件內容需查核公司公告，尚不視為已確認的催化劑。",
        "q7_wrong_signal": ("追蹤條件：" + "；".join(wrong) + "。均線會變動，應搭配營運證據重新檢視。"
                            if wrong else "待查證：目前沒有足夠資料產生具體失效條件；進場前需補上可觀察的條件。"),
    }
    entry, stop = quote.get("close"), tech.get("ma60")
    if entry is None or stop is None or not 0 < stop < entry:
        stop = None
    return {
        **answers, "stock_id": stock_id, "name": info["name"], "focus": focus,
        "assistant_mode": "local_evidence", "generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "quote_date": quote.get("date"), "evidence": facts,
        "questions": [{"key": key, "label": label, "text": answers[key],
                       "pending": answers[key].startswith("待查證：")} for key, label in PLAN_QUESTIONS.items()],
        "warnings": [a["message"] for a in alist],
        "entry_price": entry, "stop_price": stop,
        "price_note": "收盤與60日線只是本次資料參考，請確認實際計畫價格；最大虧損及目標價由你設定。",
    }


def suggest_position(entry: float, stop: float, max_loss: float) -> dict:
    """由最大虧損金額與停損價反推部位（股）。"""
    risk_per_share = entry - stop
    if risk_per_share <= 0:
        return {"error": "停損價必須低於進場價"}
    shares = int(max_loss / risk_per_share)
    lots = shares // 1000
    return {
        "risk_per_share": round(risk_per_share, 2),
        "max_shares": shares,
        "suggest_lots": lots,
        "suggest_shares": lots * 1000 if lots else shares,
        "capital_needed": round(shares * entry),
    }


def create_plan(payload: dict) -> int:
    row = {k: payload.get(k) for k in (
        "stock_id", "horizon", "q1_reason", "q2_growth_from", "q3_market_view",
        "q4_my_edge", "q5_priced_in", "q6_catalyst", "q7_wrong_signal",
        "q8_max_loss", "entry_price", "stop_price", "target_price",
        "position_shares")}
    row["created_at"] = dt.datetime.now().isoformat(timespec="seconds")
    row["status"] = "open"
    with engine.begin() as conn:
        result = conn.execute(insert(trade_plans).values(**row))
        return result.inserted_primary_key[0]


def plan_completeness(plan: dict) -> list[str]:
    """八句話未填清單（提示，不強制擋單）。"""
    labels = {
        "q1_reason": "1. 我買這家公司是因為什麼？",
        "q2_growth_from": "2. 未來獲利成長來自哪裡？",
        "q3_market_view": "3. 市場目前的主要預期是什麼？",
        "q4_my_edge": "4. 我的看法與市場有何不同？",
        "q5_priced_in": "5. 目前價格已經反映多少成長？",
        "q6_catalyst": "6. 下一個催化事件是什麼？",
        "q7_wrong_signal": "7. 哪些數據代表我判斷錯誤？",
        "q8_max_loss": "8. 最多能承受多少虧損？",
    }
    return [v for k, v in labels.items() if not plan.get(k)]


def add_trade(payload: dict) -> int:
    shares = float(payload["shares"])
    price = float(payload["price"])
    side = payload["side"]
    gross = shares * price
    fee = round(max(gross * FEE_RATE * FEE_DISCOUNT, 1))
    tax = round(gross * TAX_RATE) if side == "sell" else 0
    row = {
        "plan_id": payload.get("plan_id"),
        "stock_id": payload["stock_id"],
        "date": payload.get("date") or dt.date.today().isoformat(),
        "side": side, "shares": shares, "price": price,
        "fee": payload.get("fee", fee), "tax": payload.get("tax", tax),
        "note": payload.get("note"),
    }
    with engine.begin() as conn:
        if row['plan_id'] is not None and not query_one(conn, 'SELECT id FROM trade_plans WHERE id=:i', i=row['plan_id']):
            raise LookupError('找不到交易計畫')
        result = conn.execute(insert(trades).values(**row))
        return result.inserted_primary_key[0]


def positions() -> list[dict]:
    """目前持倉（平均成本法）＋最新市價未實現損益。"""
    with engine.begin() as conn:
        rows = query_all(conn, """
            SELECT t.*, s.name FROM trades t
            LEFT JOIN stocks s ON s.stock_id = t.stock_id
            ORDER BY t.date, t.id""")
        latest = {r["stock_id"]: r["close"] for r in query_all(conn, """
            SELECT stock_id, close FROM daily_quotes
            WHERE (stock_id, date) IN (
                SELECT stock_id, MAX(date) FROM daily_quotes GROUP BY stock_id)""")}
    pos: dict[str, dict] = {}
    for t in rows:
        p = pos.setdefault(t["stock_id"], {
            "stock_id": t["stock_id"], "name": t.get("name"),
            "shares": 0.0, "cost": 0.0})
        if t["side"] == "buy":
            p["cost"] += t["shares"] * t["price"] + (t["fee"] or 0)
            p["shares"] += t["shares"]
        else:
            if p["shares"] > 0:
                avg = p["cost"] / p["shares"]
                p["cost"] -= avg * t["shares"]
            p["shares"] -= t["shares"]
    out = []
    for p in pos.values():
        if p["shares"] <= 0:
            continue
        avg = p["cost"] / p["shares"]
        last = latest.get(p["stock_id"])
        mv = last * p["shares"] if last else None
        out.append({
            **p, "avg_cost": round(avg, 2), "last": last,
            "market_value": round(mv) if mv else None,
            "unrealized": round(mv - p["cost"]) if mv else None,
            "unrealized_pct": round((mv / p["cost"] - 1) * 100, 2) if mv else None,
        })
    return sorted(out, key=lambda x: -(x["market_value"] or 0))


def performance() -> dict:
    """已實現損益統計：勝率、平均賺賠、盈虧比（框架：期望值思維）。"""
    with engine.begin() as conn:
        rows = query_all(conn, "SELECT * FROM trades ORDER BY stock_id, date, id")
    realized: list[dict] = []
    book: dict[str, dict] = {}
    for t in rows:
        b = book.setdefault(t["stock_id"], {"shares": 0.0, "cost": 0.0})
        if t["side"] == "buy":
            b["cost"] += t["shares"] * t["price"] + (t["fee"] or 0)
            b["shares"] += t["shares"]
        else:
            if b["shares"] <= 0:
                continue
            avg = b["cost"] / b["shares"]
            sold = min(t["shares"], b["shares"])
            pnl = (t["price"] - avg) * sold - (t["fee"] or 0) - (t["tax"] or 0)
            realized.append({"stock_id": t["stock_id"], "date": t["date"],
                             "shares": sold, "pnl": round(pnl),
                             "pnl_pct": round((t["price"] / avg - 1) * 100, 2)})
            b["cost"] -= avg * sold
            b["shares"] -= sold
    wins = [r for r in realized if r["pnl"] > 0]
    losses = [r for r in realized if r["pnl"] <= 0]
    avg_win = sum(r["pnl"] for r in wins) / len(wins) if wins else 0
    avg_loss = abs(sum(r["pnl"] for r in losses) / len(losses)) if losses else 0
    return {
        "closed_trades": len(realized),
        "total_pnl": sum(r["pnl"] for r in realized),
        "win_rate": round(len(wins) / len(realized) * 100, 1) if realized else None,
        "avg_win": round(avg_win), "avg_loss": round(avg_loss),
        "profit_factor": round(avg_win / avg_loss, 2) if avg_loss else None,
        "trades": realized[-100:],
    }


def list_plans(status: str | None = None) -> list[dict]:
    sql = """SELECT p.*, s.name FROM trade_plans p
             LEFT JOIN stocks s ON s.stock_id = p.stock_id"""
    if status:
        sql += " WHERE p.status = :st"
    sql += " ORDER BY p.created_at DESC"
    with engine.begin() as conn:
        plans = query_all(conn, sql, st=status) if status else query_all(conn, sql)
    for p in plans:
        p["missing"] = plan_completeness(p)
    return plans


def get_plan(plan_id: int) -> dict | None:
    with engine.begin() as conn:
        return query_one(conn, """SELECT p.*, s.name FROM trade_plans p
                                  LEFT JOIN stocks s ON s.stock_id=p.stock_id
                                  WHERE p.id=:i""", i=plan_id)


def plan_detail(plan_id: int) -> dict:
    """Read the authenticated owner's original answers, including closed plans."""
    plan = get_plan(plan_id)
    if plan is None:
        raise LookupError("找不到交易計畫")
    return {**plan, "missing": plan_completeness(plan),
            "pending": [label for key, label in PLAN_QUESTIONS.items()
                        if str(plan.get(key) or "").startswith("待查證：")]}


def update_plan_status(plan_id: int, status: str) -> None:
    if status not in {"open", "closed", "cancelled"}:
        raise ValueError("計畫狀態必須是 open、closed 或 cancelled")
    with engine.begin() as conn:
        if not query_one(conn, 'SELECT id FROM trade_plans WHERE id=:i', i=plan_id):
            raise LookupError('找不到交易計畫')
        conn.exec_driver_sql("UPDATE trade_plans SET status=? WHERE id=?",
                             (status, plan_id))


def list_trades() -> list[dict]:
    with engine.begin() as conn:
        return query_all(conn, """
            SELECT t.*, s.name FROM trades t
            LEFT JOIN stocks s ON s.stock_id = t.stock_id
            ORDER BY t.date DESC, t.id DESC LIMIT 200""")
