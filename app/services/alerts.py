"""警訊引擎：框架第四章可量化警訊的自動掃描。

質化警訊（董監質押、關係人交易、說法反覆等）無免費結構化資料，
在個股頁以檢查清單形式提示人工查核（公開資訊觀測站連結）。
"""
from app.db import engine, query_all

RULES_DOC = [
    ("AR_FASTER", "應收帳款年增遠高於營收年增（收款品質疑慮）"),
    ("INV_FASTER", "存貨年增遠高於營收年增（跌價/需求疑慮）"),
    ("OCF_LOW", "近四季營業現金流明顯低於淨利（獲利品質疑慮）"),
    ("NONOP_HIGH", "獲利主要來自業外（一次性收益疑慮）"),
    ("GM_DOWN", "毛利率連三季下滑（價格競爭/成本上升）"),
    ("REV_DOWN", "月營收連三月年減"),
    ("DEBT_HIGH", "負債比偏高（>70%）"),
    ("CAPITAL_UP", "股本明顯膨脹（稀釋疑慮）"),
]


def scan(stock_id: str) -> list[dict]:
    with engine.begin() as conn:
        fins = query_all(conn, """SELECT * FROM financials WHERE stock_id=:s
                                  ORDER BY quarter""", s=stock_id)
        revs = query_all(conn, """SELECT ym, yoy FROM month_revenue
                                  WHERE stock_id=:s ORDER BY ym DESC LIMIT 3""",
                         s=stock_id)
    alerts = []

    def add(code, msg, level="warn"):
        alerts.append({"code": code, "message": msg, "level": level})

    if len(fins) >= 5:
        cur, yr_ago = fins[-1], fins[-5]
        rev_yoy = _yoy(cur.get("revenue"), yr_ago.get("revenue"))
        ar_yoy = _yoy(cur.get("receivables"), yr_ago.get("receivables"))
        inv_yoy = _yoy(cur.get("inventory"), yr_ago.get("inventory"))
        cap_yoy = _yoy(cur.get("capital"), yr_ago.get("capital"))
        if rev_yoy is not None and ar_yoy is not None and ar_yoy > rev_yoy + 30:
            add("AR_FASTER",
                f"應收帳款年增 {ar_yoy:.0f}%，遠高於營收年增 {rev_yoy:.0f}%")
        if rev_yoy is not None and inv_yoy is not None and inv_yoy > rev_yoy + 30:
            add("INV_FASTER",
                f"存貨年增 {inv_yoy:.0f}%，遠高於營收年增 {rev_yoy:.0f}%（注意：也可能是為訂單備貨，需看後續兌現）")
        if cap_yoy is not None and cap_yoy > 10:
            add("CAPITAL_UP", f"股本年增 {cap_yoy:.0f}%，注意可轉債/現增稀釋")

    if len(fins) >= 4:
        ocf4 = _sum(fins[-4:], "op_cashflow")
        ni4 = _sum(fins[-4:], "net_income")
        if ocf4 is not None and ni4 and ni4 > 0 and ocf4 < ni4 * 0.7:
            add("OCF_LOW",
                f"近四季營業現金流 {ocf4/1e8:.1f} 億 < 淨利 {ni4/1e8:.1f} 億 ×0.7")

    if fins:
        cur = fins[-1]
        pretax, nonop = cur.get("pretax_income"), cur.get("nonop_income")
        if pretax and nonop is not None and pretax > 0 and nonop / pretax > 0.4:
            add("NONOP_HIGH", f"最新季業外損益占稅前獲利 {nonop/pretax*100:.0f}%")
        ta, tl = cur.get("total_assets"), cur.get("total_liabilities")
        if ta and tl is not None and tl / ta > 0.7:
            add("DEBT_HIGH", f"負債比 {tl/ta*100:.0f}%（金融/租賃業屬正常，其他產業需留意）")

    if len(fins) >= 3:
        gms = []
        for f in fins[-3:]:
            gm = (f.get("gross_profit") / f["revenue"] * 100
                  if f.get("gross_profit") is not None and f.get("revenue") else None)
            gms.append(gm)
        if all(g is not None for g in gms) and gms[0] > gms[1] > gms[2]:
            add("GM_DOWN", f"毛利率連三季下滑：{gms[0]:.1f}% → {gms[1]:.1f}% → {gms[2]:.1f}%")

    if len(revs) >= 3 and all(r["yoy"] is not None and r["yoy"] < 0 for r in revs):
        add("REV_DOWN", "月營收連三個月年減：" +
            "、".join(f"{r['ym']} {r['yoy']:.1f}%" for r in reversed(revs)))

    return alerts


def _yoy(cur, prev):
    if cur is None or not prev:
        return None
    return (cur / prev - 1) * 100


def _sum(rows, key):
    vals = [r.get(key) for r in rows if r.get(key) is not None]
    return sum(vals) if vals else None


def manual_checklist(stock_id: str) -> list[dict]:
    """無法自動化的治理查核清單（附官方查詢入口）。"""
    return [
        {"item": "董監持股與質押比例", "url": f"https://mopsov.twse.com.tw/mops/web/stapap1?TYPEK=sii&co_id={stock_id}"},
        {"item": "大股東申報轉讓、內部人持股異動", "url": "https://mops.twse.com.tw/mops/#/web/query6_1"},
        {"item": "重大訊息（處分資產、背書保證、訴訟）", "url": "https://mops.twse.com.tw/mops/#/web/t05st01"},
        {"item": "法說會簡報與歷次說法比對", "url": "https://mops.twse.com.tw/mops/#/web/t100sb02_1"},
        {"item": "會計師/財務主管是否頻繁更換", "url": "https://mops.twse.com.tw/mops/#/web/t51sb10"},
    ]
