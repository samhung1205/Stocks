"""風險承受能力問卷（spec §7）。

不讓使用者直接選「低/中/高」——那只會反映當下心情。改用五題行為與處境題計分，
每題 0～4 分，總分 0～20 對映五種型態。使用者仍可人工覆寫結果（比照
scoring.py 的「人工分數一律可覆寫自動分數」）。

題目定義同時是 API 回傳給前端渲染用的來源，避免題目在前後端各寫一份。
"""

QUESTIONS = [
    {
        "key": "q1",
        "text": "如果投資 100 萬元，一年後變成 75 萬元，你會？",
        "options": [
            {"value": "sell_all", "label": "全部賣出", "score": 0},
            {"value": "sell_part", "label": "賣出部分", "score": 1},
            {"value": "hold", "label": "繼續持有", "score": 3},
            {"value": "buy_more", "label": "加碼", "score": 4},
        ],
    },
    {
        "key": "q2",
        "text": "這筆資金多久內不需要使用？",
        "options": [
            {"value": "lt3", "label": "3 年以內", "score": 0},
            {"value": "3to5", "label": "3～5 年", "score": 1},
            {"value": "5to10", "label": "5～10 年", "score": 3},
            {"value": "gt10", "label": "10 年以上", "score": 4},
        ],
    },
    {
        "key": "q3",
        "text": "投資主要目標是？",
        "options": [
            {"value": "preserve", "label": "保存本金", "score": 0},
            {"value": "income", "label": "穩定收入", "score": 1},
            {"value": "growth", "label": "穩定成長", "score": 3},
            {"value": "max", "label": "長期資產最大化", "score": 4},
        ],
    },
    {
        "key": "q4",
        "text": "目前是否有穩定的工作收入？",
        "options": [
            {"value": "no", "label": "沒有", "score": 0},
            {"value": "unstable", "label": "有，但不穩定", "score": 2},
            {"value": "yes", "label": "有，且穩定", "score": 4},
        ],
    },
    {
        "key": "q5",
        "text": "如果股市發生 30% 修正，是否仍能維持正常生活？",
        "options": [
            {"value": "no", "label": "會明顯影響生活", "score": 0},
            {"value": "partly", "label": "會有壓力但撐得過", "score": 2},
            {"value": "yes", "label": "不影響日常生活", "score": 4},
        ],
    },
]

MAX_SCORE = sum(max(o["score"] for o in q["options"]) for q in QUESTIONS)  # 20

# 由低到高。equity_bias 供配置引擎做內插（0=最保守、1=最積極）。
RISK_TYPES = [
    {"key": "conservative", "label": "保守型", "min": 0, "equity_bias": 0.0,
     "desc": "以保住本金為優先，能接受的波動很有限。"},
    {"key": "moderate", "label": "穩健型", "min": 5, "equity_bias": 0.25,
     "desc": "願意承擔小幅波動換取略高於定存的報酬。"},
    {"key": "balanced", "label": "平衡型", "min": 9, "equity_bias": 0.5,
     "desc": "接受中等波動，兼顧成長與穩定。"},
    {"key": "growth", "label": "成長型", "min": 13, "equity_bias": 0.75,
     "desc": "以長期資產成長為主，能接受明顯的短期波動。"},
    {"key": "aggressive", "label": "積極型", "min": 17, "equity_bias": 1.0,
     "desc": "追求長期最大化，可以承受大幅回檔而不改變計畫。"},
]

_BY_KEY = {t["key"]: t for t in RISK_TYPES}


def score(answers: dict | None) -> dict:
    """把問卷答案換算成分數與型態。未作答的題目以 0 分計，並回報完成度。"""
    answers = answers or {}
    total, answered, detail = 0, 0, []
    for q in QUESTIONS:
        chosen = answers.get(q["key"])
        opt = next((o for o in q["options"] if o["value"] == chosen), None)
        if opt:
            answered += 1
            total += opt["score"]
        detail.append({"key": q["key"], "text": q["text"],
                       "answer": chosen, "score": opt["score"] if opt else None})
    return {
        "score": total, "max": MAX_SCORE,
        "answered": answered, "total_questions": len(QUESTIONS),
        "complete": answered == len(QUESTIONS),
        "type": classify(total)["key"],
        "detail": detail,
    }


def classify(total: float) -> dict:
    """分數 → 型態。"""
    out = RISK_TYPES[0]
    for t in RISK_TYPES:
        if total >= t["min"]:
            out = t
    return out


def resolve(profile: dict) -> dict:
    """決定實際採用的風險型態：人工覆寫優先，其次問卷結果。

    問卷未作答完時仍會給一個型態（未答題以 0 分計 → 偏保守），但會標 complete=False，
    介面上提示「先把問卷做完，配置建議才有意義」。
    """
    result = score(profile.get("risk_answers"))
    manual = profile.get("risk_type_manual")
    effective = manual if manual in _BY_KEY else result["type"]
    t = _BY_KEY[effective]
    return {
        **result,
        "auto_type": result["type"],
        "manual_type": manual if manual in _BY_KEY else None,
        "effective_type": effective,
        "label": t["label"], "desc": t["desc"], "equity_bias": t["equity_bias"],
    }
