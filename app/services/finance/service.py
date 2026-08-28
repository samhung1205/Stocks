"""財務規劃組裝層：情境（profile）CRUD 與完整試算結果的組裝。

這是 finance 套件裡唯一同時碰 DB 與計算引擎的地方——引擎全部是純函式，
所以整套財務邏輯都可以離線測試（tests/smoke.py finance）。

試算刻意設計成**無狀態**：`simulate()` 吃一份完整 payload、吐完整結果，
前端每次輸入變動就 debounce 後重打一次（spec §31 要求即時更新）。
這樣公式只有一份、不會在 JS 裡複製第二份而漂移。
"""
import datetime as dt
import json

from sqlalchemy import insert

from app.db import engine, query_all, query_one, upsert_many
from app.db.tables import finance_profiles, finance_targets
from app.services.finance import (
    allocation, compound, etf_stats, health, rebalance, retirement, risk, universe,
)

JSON_FIELDS = ("risk_answers", "assumptions", "retirement")

DEFAULT_ASSUMPTIONS = {
    "initial_capital": None,        # None → 用現有投資資產合計
    "use_portfolio_return": True,   # 基準情境改用組合加權預期報酬
    "return_conservative": 4.0,
    "return_base": 7.0,
    "return_optimistic": 10.0,
    "inflation": 2.0,
    "fee_mode": "simple",           # simple 忽略費用 / advanced 計入
    "extra_fee": 0.0,               # 交易成本、稅費等其他年度費用(%)
    "dividend_reinvest": True,
    "custom_weights": False,        # 使用者是否手動改過權重
    "milestones": compound.DEFAULT_MILESTONES,
    "custom_goal": None,
}

DEFAULT_RETIREMENT = {
    "expense_monthly": 50000.0,
    "pension_monthly": 0.0,
    "other_income_monthly": 0.0,
    "withdraw_mode": "gap",         # gap / rate / amount
    "withdraw_rate": 4.0,
    "withdraw_monthly": None,
    "retire_return": None,          # None → 退休後採保守情境報酬
    "sorr_shock": retirement.SORR_SHOCK,
}


# ── profile 讀寫 ────────────────────────────────────────
def _loads(v, fallback):
    if not v:
        return dict(fallback) if isinstance(fallback, dict) else fallback
    try:
        parsed = json.loads(v)
    except (TypeError, ValueError):
        return dict(fallback) if isinstance(fallback, dict) else fallback
    if isinstance(fallback, dict) and isinstance(parsed, dict):
        return {**fallback, **parsed}
    return parsed


def _hydrate(row: dict) -> dict:
    p = dict(row)
    p["risk_answers"] = _loads(p.get("risk_answers"), {})
    p["assumptions"] = _loads(p.get("assumptions"), DEFAULT_ASSUMPTIONS)
    p["retirement"] = _loads(p.get("retirement"), DEFAULT_RETIREMENT)
    return p


def default_profile(name: str = "基準情境") -> dict:
    """新情境的骨架：欄位留空，只給假設值與預設標的名單。"""
    return {
        "id": None, "name": name,
        "age": None, "retire_age": 65, "life_expectancy": 90,
        "horizon_years": None, "goal": "accumulate",
        "monthly_invest": None, "need_income": 0,
        "risk_answers": {}, "risk_type": None, "risk_type_manual": None,
        "assumptions": dict(DEFAULT_ASSUMPTIONS),
        "retirement": dict(DEFAULT_RETIREMENT),
        "targets": [{"stock_id": sid, "weight": None, "expected_return": None,
                     "actual_value": None, "enabled": 1}
                    for sid in universe.default_selection()],
    }


def list_profiles() -> list[dict]:
    with engine.begin() as conn:
        return query_all(conn, """SELECT id, name, age, retire_age, updated_at
                                  FROM finance_profiles ORDER BY id""")


def get_profile(profile_id: int) -> dict | None:
    with engine.begin() as conn:
        row = query_one(conn, "SELECT * FROM finance_profiles WHERE id=:i",
                        i=profile_id)
        if not row:
            return None
        targets = query_all(conn, """SELECT stock_id, weight, expected_return,
                                            actual_value, enabled
                                     FROM finance_targets WHERE profile_id=:i""",
                            i=profile_id)
    p = _hydrate(row)
    p["targets"] = targets or default_profile()["targets"]
    return p


def save_profile(payload: dict) -> dict:
    """建立或更新一份具名情境（含標的設定）。"""
    now = dt.datetime.now().isoformat(timespec="seconds")
    cols = {c.name for c in finance_profiles.columns}
    row = {k: v for k, v in payload.items() if k in cols and k != "id"}
    for f in JSON_FIELDS:
        if f in payload:
            row[f] = json.dumps(payload[f], ensure_ascii=False)
    row["name"] = payload.get("name") or "未命名情境"
    row["updated_at"] = now
    # 風險型態存的是問卷算出來的自動值；人工覆寫另存 risk_type_manual
    row["risk_type"] = risk.score(payload.get("risk_answers"))["type"]

    pid = payload.get("id")
    with engine.begin() as conn:
        if pid:
            row["id"] = int(pid)
            upsert_many(conn, finance_profiles, [row])
        else:
            row["created_at"] = now
            pid = conn.execute(insert(finance_profiles).values(**row)).inserted_primary_key[0]
        conn.exec_driver_sql("DELETE FROM finance_targets WHERE profile_id=?", (pid,))
        targets = [{
            "profile_id": pid, "stock_id": t["stock_id"],
            "weight": t.get("weight"), "expected_return": t.get("expected_return"),
            "actual_value": t.get("actual_value"),
            "enabled": 1 if t.get("enabled", 1) else 0,
        } for t in payload.get("targets", []) if t.get("stock_id")]
        upsert_many(conn, finance_targets, targets)
    return get_profile(pid)


def delete_profile(profile_id: int) -> dict:
    with engine.begin() as conn:
        conn.exec_driver_sql("DELETE FROM finance_targets WHERE profile_id=?",
                             (profile_id,))
        conn.exec_driver_sql("DELETE FROM finance_profiles WHERE id=?", (profile_id,))
    return {"ok": True}


# ── ETF 名單 ────────────────────────────────────────────
def _names(stock_ids: list[str]) -> dict[str, str]:
    ids = [s for s in stock_ids if s != "CASH"]
    if not ids:
        return {}
    ph = ",".join(f":i{n}" for n in range(len(ids)))
    with engine.begin() as conn:
        rows = query_all(conn, f"SELECT stock_id, name FROM stocks WHERE stock_id IN ({ph})",
                         **{f"i{n}": s for n, s in enumerate(ids)})
    return {r["stock_id"]: r["name"] for r in rows}


def universe_info(extra_ids: list[str] | None = None) -> dict:
    """名單 + metadata + 歷史統計，供前端渲染標的選擇區。"""
    ids = list(universe.UNIVERSE) + [s for s in (extra_ids or [])
                                     if s not in universe.UNIVERSE]
    names = _names(ids)
    items = []
    for sid in ids:
        m = universe.meta(sid)
        role = universe.ROLE_DEFAULTS[m["role"]]
        items.append({
            **m,
            "name": m.get("name") or names.get(sid) or sid,
            "role_label": role["label"], "asset": role["asset"],
            "asset_label": universe.ASSET_LABELS[role["asset"]],
            "region_label": universe.REGION_LABELS.get(m["region"], m["region"]),
            "default_return": universe.default_return(sid),
            "default_yield": universe.default_yield(sid),
            "history": etf_stats.stats(sid),
        })
    return {
        "items": items,
        "roles": universe.ROLE_DEFAULTS,
        "questions": risk.QUESTIONS,
        "risk_types": risk.RISK_TYPES,
        "goals": health.GOAL_LABELS,
        "defaults": {"assumptions": DEFAULT_ASSUMPTIONS,
                     "retirement": DEFAULT_RETIREMENT},
        "disclaimer": compound.DISCLAIMER,
        "metadata_note": "角色、資產類別、國家曝險與內扣費用為人工維護的常數，"
                         "免費資料源無法提供，可能與最新公開資訊有落差。",
    }


def refresh_etf_history(stock_ids: list[str] | None = None) -> dict:
    return etf_stats.refresh(stock_ids)


# ── 試算組裝 ────────────────────────────────────────────
def _portfolio_actuals(stock_ids: list[str]) -> dict[str, float]:
    """從交易日誌帶入目前持倉市值（spec §24 的「目前實際配置」）。

    只帶股票代號；CASH 不自動帶，避免和緊急預備金重複計算。
    """
    from app.services import journal
    try:
        pos = journal.positions()
    except Exception:
        return {}
    return {p["stock_id"]: p["market_value"] for p in pos
            if p["stock_id"] in stock_ids and p.get("market_value")}


def simulate(payload: dict) -> dict:
    """吃一份完整 profile payload，吐出結果頁需要的全部區塊。"""
    profile = dict(payload)
    a = {**DEFAULT_ASSUMPTIONS, **(profile.get("assumptions") or {})}
    ret_cfg = {**DEFAULT_RETIREMENT, **(profile.get("retirement") or {})}

    # 1. 財務健檢與每月投資能力
    h = health.analyze(profile)
    monthly = health.resolve_monthly_invest(profile, h)

    # 2. 風險承受度
    r = risk.resolve(profile)

    # 3. 角色權重 → 標的配置
    age = float(profile.get("age") or 0)
    retire_age = float(profile.get("retire_age") or 65)
    horizon = profile.get("horizon_years")
    horizon = float(horizon) if horizon else max(retire_age - age, 1)

    roles = allocation.role_weights(
        age=age, retire_age=retire_age, equity_bias=r["equity_bias"],
        need_income=bool(profile.get("need_income")),
        emergency_level=h["emergency_level"],
        horizon_years=horizon)

    targets_in = [t for t in (profile.get("targets") or []) if t.get("stock_id")]
    selected = [t["stock_id"] for t in targets_in if t.get("enabled", 1)]
    override = ({t["stock_id"]: t.get("weight") for t in targets_in}
                if a.get("custom_weights") else None)
    returns = {t["stock_id"]: t.get("expected_return") for t in targets_in
               if t.get("expected_return") not in (None, "")}

    alloc = allocation.allocate(selected, roles["weights"],
                                initial_capital=0, monthly=0,
                                override=override, returns=returns)

    # 4. 試算假設：初始本金、三情境報酬、費用、配息率
    initial = a.get("initial_capital")
    initial = float(initial) if initial not in (None, "") else float(h["invested_assets"])

    base = alloc["expected_return"] if a.get("use_portfolio_return") else float(a["return_base"])
    if a.get("use_portfolio_return"):
        rates = {"conservative": round(base - 3, 2), "base": round(base, 2),
                 "optimistic": round(base + 3, 2)}
    else:
        rates = {"conservative": float(a["return_conservative"]),
                 "base": float(a["return_base"]),
                 "optimistic": float(a["return_optimistic"])}

    advanced = a.get("fee_mode") == "advanced"
    fee = (alloc["expected_fee"] + float(a.get("extra_fee") or 0)) if advanced else 0.0
    denom = alloc["weight_sum"] or 100.0
    port_yield = sum(t["weight"] * universe.default_yield(t["stock_id"])
                     for t in alloc["targets"]) / denom
    reinvest = bool(a.get("dividend_reinvest", True))
    inflation = float(a.get("inflation", 2.0))

    # 5. 重新用實際金額算一次配置（金額欄位需要 initial / monthly）
    alloc = allocation.allocate(selected, roles["weights"],
                                initial_capital=initial, monthly=monthly,
                                override=override, returns=returns)
    names = _names(selected)
    for t in alloc["targets"]:
        t["name"] = (universe.UNIVERSE.get(t["stock_id"], {}).get("name")
                     or names.get(t["stock_id"]) or t["stock_id"])

    kwargs = dict(inflation=inflation, fee=fee, dividend_yield=round(port_yield, 2),
                  reinvest=reinvest, start_age=age or None,
                  start_year=dt.date.today().year)

    # 6. 複利與三情境
    sc = compound.scenarios(initial, monthly, horizon, rates, **kwargs)
    base_proj = sc["scenarios"]["base"]
    ms = compound.milestones(base_proj["series"], a.get("milestones"),
                             a.get("custom_goal"))

    # 7. 退休：先算到退休當年的資產，再做提款模擬
    years_to_retire = max(retire_age - age, 0) if age else 0
    if years_to_retire > 0:
        to_retire = compound.project(initial, monthly, years_to_retire,
                                     rates["base"], **kwargs)
        balance_at_retire = to_retire["final_value"]
    else:
        to_retire = None
        balance_at_retire = initial
    retire_return = ret_cfg.get("retire_return")
    retire_return = float(retire_return) if retire_return not in (None, "") \
        else rates["conservative"]
    ret = retirement.analyze(
        age=age, retire_age=retire_age,
        life_expectancy=float(profile.get("life_expectancy") or 90),
        balance_at_retire=balance_at_retire,
        retire_expense_monthly=ret_cfg["expense_monthly"],
        pension_monthly=ret_cfg["pension_monthly"],
        other_income_monthly=ret_cfg["other_income_monthly"],
        annual_return=retire_return, inflation=inflation,
        withdraw_mode=ret_cfg["withdraw_mode"],
        withdraw_rate=float(ret_cfg["withdraw_rate"]),
        withdraw_monthly=ret_cfg.get("withdraw_monthly"),
        sorr_shock=float(ret_cfg.get("sorr_shock") or retirement.SORR_SHOCK))
    ret["projection_to_retire"] = to_retire

    # 8. 再平衡：目前實際配置由交易日誌帶入，使用者填的值優先
    actuals = _portfolio_actuals(selected)
    user_actual = {t["stock_id"]: t.get("actual_value") for t in targets_in}
    rb_rows = []
    for t in alloc["targets"]:
        sid = t["stock_id"]
        v = user_actual.get(sid)
        rb_rows.append({**t, "actual_value": float(v) if v not in (None, "")
                        else actuals.get(sid, 0)})
    rb = rebalance.analyze(rb_rows, monthly)
    rb["from_journal"] = {k: round(v) for k, v in actuals.items()}

    return {
        "health": h,
        "monthly_invest": round(monthly),
        "monthly_invest_source": "user" if profile.get("monthly_invest") else "suggested",
        "risk": r,
        "roles": roles,
        "allocation": alloc,
        "assumptions": {**a, "initial_capital": round(initial),
                        "rates": rates, "effective_fee": round(fee, 3),
                        "portfolio_yield": round(port_yield, 2),
                        "horizon_years": horizon},
        "projection": base_proj,
        "scenarios": sc,
        "milestones": ms,
        "retirement": ret,
        "rebalance": rb,
        "disclaimer": compound.DISCLAIMER,
    }
