"""Strategy Portfolio 作為 Financial Planner 的 investment source（Stage I1）。

這是 finance 套件裡第二個會碰 DB 的模組（第一個是 service.py）——它只透過
``app.services.portfolio_lab`` 的既有唯讀函式讀取 Strategy Portfolio 的身份/版本/
研究結果，從不直接碰任何 portfolio 相關的表，也從不寫入任何東西回 Strategy Portfolio
那一側。

核心 invariant（見 docs/history/stage-i0-planner-integration-contract-report.md、
docs/RESEARCH_CONTRACTS.md）：**這個模組只回傳唯讀的 evidence，絕不把任何
research 數字（CAGR/Sharpe/...）直接寫進 Planner 的 expected_return。**
``evidence()`` 是純讀取；真正把一個數字寫進 ``finance_targets.expected_return``
永遠是 ``service.save_profile()`` 自己接住使用者明確送出的值,這裡不提供任何
「幫你填」的捷徑。
"""
from __future__ import annotations

from app.services import portfolio_lab as plab
from app.services.finance.universe import is_portfolio_key, portfolio_key

__all__ = ["is_portfolio_key", "portfolio_key", "validate_pin", "evidence", "display_name"]

# service.save_profile() builds the assumption_evidence_ref audit snapshot inline,
# right after calling evidence() for its own availability check -- that keeps the
# "never write expected_return from evidence" guarantee in one obvious place instead
# of splitting it across two modules, and avoids a second, redundant evidence() read.

EVIDENCE_METRIC_KEYS = ("cagr", "annualized_volatility", "max_drawdown", "sharpe", "sortino", "total_return")


def validate_pin(strategy_portfolio_id: int, portfolio_version_id: int,
                 research_run_id: str | None) -> dict:
    """驗證一組 pin 是否自洽：version 必須屬於該 portfolio；research_run（若有）
    必須屬於同一個 portfolio_version。找不到／不吻合一律 ``ValueError``（呼叫端
    ``service.save_profile()`` 用它讓使用者看到清楚的 400，而不是猜一個「最接近」
    的版本）。回傳該版本的完整 view，供呼叫端沿用（避免重複查詢）。"""
    try:
        version = plab.get_version(strategy_portfolio_id, portfolio_version_id)
    except LookupError as exc:
        raise ValueError(f"strategy_portfolio_id={strategy_portfolio_id} / "
                         f"portfolio_version_id={portfolio_version_id}: {exc}") from exc
    if research_run_id:
        try:
            run = plab.get_research_run(research_run_id)
        except LookupError as exc:
            raise ValueError(f"research_run_id={research_run_id!r} not found") from exc
        if run["portfolio_id"] != strategy_portfolio_id or run["portfolio_version_id"] != portfolio_version_id:
            raise ValueError(
                f"research_run_id={research_run_id!r} does not belong to strategy_portfolio_id="
                f"{strategy_portfolio_id} / portfolio_version_id={portfolio_version_id}")
    return version


def display_name(strategy_portfolio_id: int) -> str | None:
    """只讀身份（不是 evidence）：一個 Strategy Portfolio 的目前名稱，純粹方便顯示。
    pin 失效（已刪除——目前平台沒有硬刪除，理論上不會發生）時回傳 None 而不是拋錯，
    因為呼叫端（simulate()）必須永遠成功,不能因為顯示用的名稱查不到就整個試算失敗。"""
    try:
        return plab.get_portfolio(strategy_portfolio_id)["name"]
    except LookupError:
        return None


def evidence(strategy_portfolio_id: int, portfolio_version_id: int,
            research_run_id: str | None = None) -> dict:
    """唯讀 evidence 組裝。找不到 portfolio/version 讓 LookupError 往上拋
    （呼叫端的 API 層用既有的 ``_lab()`` 映射成 404）；research_run 相關的任何
    問題（缺、不吻合、還沒跑完）一律回 ``available: False`` + ``reason``，
    絕不 500，也絕不拋錯讓呼叫端需要額外處理。"""
    portfolio = plab.get_portfolio(strategy_portfolio_id)   # LookupError -> 404 (via _lab)
    version = plab.get_version(strategy_portfolio_id, portfolio_version_id)  # LookupError -> 404
    out = {
        "strategy_portfolio_id": strategy_portfolio_id,
        "portfolio_name": portfolio["name"],
        "archived": portfolio["archived"],
        "portfolio_version_id": version["id"],
        "version_no": version["version_no"],
        "allocation_method": version["allocation_method"],
        "components": [{"strategy_id": c["strategy_id"], "strategy_name": c["strategy_name"],
                        "template_key": c["template_key"], "strategy_version_no": c["strategy_version_no"],
                        "target_weight": c["target_weight"]} for c in version["components"]],
        "research_run_id": research_run_id,
    }
    if not research_run_id:
        out["research"] = {"available": False, "reason": "no_research_run_selected"}
        return out
    try:
        run = plab.get_research_run(research_run_id)
    except LookupError:
        out["research"] = {"available": False, "reason": "research_run_not_found"}
        return out
    if run["portfolio_id"] != strategy_portfolio_id or run["portfolio_version_id"] != portfolio_version_id:
        out["research"] = {"available": False, "reason": "research_run_mismatch"}
        return out
    if run["status"] != "completed":
        out["research"] = {"available": False, "reason": f"run_status_{run['status']}"}
        return out
    result = plab.get_research_result(research_run_id)   # already completed; won't raise
    equity_rows = plab.get_research_equity(research_run_id)
    cash_weights = [r["cash_weight"] for r in equity_rows if r["cash_weight"] is not None]
    metrics = result["metrics"]
    out["research"] = {
        "available": True,
        "window": result["request"],
        "metrics": {k: metrics.get(k) for k in EVIDENCE_METRIC_KEYS},
        "cash": {
            "last_cash_weight": equity_rows[-1]["cash_weight"] if equity_rows else None,
            "avg_cash_weight": (sum(cash_weights) / len(cash_weights)) if cash_weights else None,
        },
        "turnover": result["turnover"],
        "warnings": result["warnings"],
    }
    return out
