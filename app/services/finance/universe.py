"""ETF 精選名單與其分類 metadata。

這些欄位（角色、資產類別、國家曝險、成分重疊群組、內扣費用）**免費資料源抓不到**，
只能人工維護——所以它們是常數而不是查詢結果，介面上會標明「人工維護，可能過時」。
使用者可以在頁面上自行加代號或停用某檔；加入的自訂代號會沿用該 role 的預設假設。

role 決定配置引擎怎麼分配權重：
  core_tw  台灣市值型核心      core_us  美國大型股核心
  growth   成長型衛星          income   現金流/高股息
  defense  防守（債券）        cash     現金

overlap 是「成分高度重疊」的群組代號：同群組同時持有多檔時出提示（spec §25），
但不自動剔除——要不要調整是使用者的決定。
"""

# role 預設假設報酬（年化 %）與說明。使用者可逐檔覆寫。
# dividend_yield 是「總報酬中以現金配息形式發放」的部分，只有在關閉「股息再投入」時
# 才會影響試算（配息會離開複利、單獨累積），不會憑空增加總報酬。
ROLE_DEFAULTS = {
    "core_tw": {"label": "台灣市值型", "assumed_return": 7.0, "asset": "equity",
                "dividend_yield": 3.0},
    "core_us": {"label": "美國大型股", "assumed_return": 7.0, "asset": "equity",
                "dividend_yield": 1.5},
    "growth": {"label": "成長型衛星", "assumed_return": 8.0, "asset": "equity",
               "dividend_yield": 0.8},
    "income": {"label": "高股息現金流", "assumed_return": 6.0, "asset": "equity",
               "dividend_yield": 6.0},
    "defense": {"label": "防守（債券）", "assumed_return": 3.0, "asset": "bond",
                "dividend_yield": 3.5},
    "cash": {"label": "現金", "assumed_return": 1.0, "asset": "cash",
             "dividend_yield": 1.0},
    # Stage I1: pseudo-role for a Strategy Portfolio investment source (see is_portfolio_key()
    # below). assumed_return is deliberately None -- there is no ETF-style constant that can
    # stand in for a Strategy Portfolio's future return, and this role must never let
    # allocation.allocate() silently fall back to a number (AGENTS.md "no silent fallback").
    "strategy_portfolio": {"label": "策略組合（Strategy Portfolio）", "assumed_return": None,
                           "asset": "equity", "dividend_yield": 0.0},
    # Stage I1.1 hotfix: source_type='manual' 的專屬角色——一個使用者自訂、平台完全
    # 不知道是什麼資產的項目（例如「儲蓄險」「私募基金」），不得沿用 core_tw / TW
    # equity 這種「看起來是台股 ETF」的分類（見 docs/RESEARCH_CONTRACTS.md §16、
    # docs/history/stage-i0-planner-integration-contract-report.md §2）。asset/region 都是
    # 明確的「未分類」值，不是任何一種真實資產類別的猜測。
    "manual": {"label": "自訂資產（未分類）", "assumed_return": None, "asset": "other",
              "dividend_yield": 0.0},
}

ASSET_LABELS = {"equity": "股票", "bond": "債券", "cash": "現金", "other": "其他／未分類"}
REGION_LABELS = {"TW": "台灣", "US": "美國", "-": "現金", "unspecified": "未指定"}

# 內扣費用（fee）為經理費＋保管費的概略年費率(%)，人工維護。
UNIVERSE = {
    "0050": {
        "role": "core_tw", "region": "TW", "overlap": "tw_large",
        "fee": 0.43, "income_focus": False,
        "note": "追蹤台灣50指數，台股市值型核心",
    },
    "006208": {
        "role": "core_tw", "region": "TW", "overlap": "tw_large",
        "fee": 0.24, "income_focus": False,
        "note": "同樣追蹤台灣50指數，與 0050 高度重疊",
    },
    "00646": {
        "role": "core_us", "region": "US", "overlap": "us_large",
        "fee": 0.61, "income_focus": False,
        "note": "追蹤 S&P 500，美股大型股核心",
    },
    "00662": {
        "role": "growth", "region": "US", "overlap": "us_tech",
        "fee": 0.72, "income_focus": False,
        "note": "追蹤 NASDAQ-100，科技權重高、波動大於大盤",
    },
    "00757": {
        "role": "growth", "region": "US", "overlap": "us_tech",
        "fee": 0.89, "income_focus": False,
        "note": "FANG+ 指數，集中度極高，屬衛星部位",
    },
    "0056": {
        "role": "income", "region": "TW", "overlap": "tw_dividend",
        "fee": 0.66, "income_focus": True,
        "note": "台灣高股息，配息型",
    },
    "00878": {
        "role": "income", "region": "TW", "overlap": "tw_dividend",
        "fee": 0.55, "income_focus": True,
        "note": "ESG 高股息，季配息",
    },
    "00919": {
        "role": "income", "region": "TW", "overlap": "tw_dividend",
        "fee": 0.61, "income_focus": True,
        "note": "台灣精選高息，季配息",
    },
    "00713": {
        "role": "income", "region": "TW", "overlap": "tw_dividend",
        "fee": 0.55, "income_focus": True,
        "note": "高息低波，波動低於一般高股息",
    },
    "00719B": {
        "role": "defense", "region": "US", "overlap": "us_treasury_short",
        "fee": 0.15, "income_focus": True,
        "note": "美國公債 1-3 年，短天期、利率風險低",
    },
    "00864B": {
        "role": "defense", "region": "US", "overlap": "us_treasury_short",
        "fee": 0.14, "income_focus": True,
        "note": "美國公債 0-1 年，接近現金等價",
    },
    "00679B": {
        "role": "defense", "region": "US", "overlap": "us_treasury_long",
        "fee": 0.19, "income_focus": True,
        "note": "美國公債 20 年以上，長天期、對利率極敏感",
    },
    "CASH": {
        "role": "cash", "region": "-", "overlap": "cash",
        "fee": 0.0, "income_focus": False,
        "name": "現金／定存",
        "note": "不投入市場的安全墊，退休提款期特別重要",
    },
}

# 重疊群組的提示文字（spec §25 指定語氣：陳述事實，不下指令）
OVERLAP_NOTES = {
    "tw_large": "兩者追蹤相同／高度相似的大型台股指數，持股高度重疊，"
                "增加 ETF 數量不代表增加資產分散。",
    "tw_dividend": "皆屬台灣高股息策略，雖選股邏輯不同，但資產類別仍高度集中於台股。",
    "us_tech": "皆為美股科技成長型，成分股重疊度高，波動會同向放大。",
    "us_treasury_short": "皆為短天期美國公債，利率敏感度接近，分散效果有限。",
    "us_treasury_long": "長天期公債對利率變動極敏感，波動可能不低於股票。",
}


PORTFOLIO_KEY_PREFIX = "portfolio:"


def portfolio_key(strategy_portfolio_id) -> str:
    """Stage I1：一個 Strategy Portfolio 在 finance_targets.stock_id 裡的 opaque key
    慣例——絕不是真實股票代號，見 app/services/finance/portfolio_source.py。"""
    return f"{PORTFOLIO_KEY_PREFIX}{strategy_portfolio_id}"


def is_portfolio_key(stock_id) -> bool:
    return isinstance(stock_id, str) and stock_id.startswith(PORTFOLIO_KEY_PREFIX)


def meta(stock_id: str, source_type: str | None = None) -> dict:
    """取某檔的 metadata；不在名單內的自訂代號給一組保守的預設值。

    Stage I1：``portfolio:<id>`` 前綴的 key 一律分類成 ``strategy_portfolio`` 角色
    （見 ROLE_DEFAULTS），資產/地區只是簡化 reporting 近似值，不作為配置或研究限制——
    這個角色的 ``assumed_return`` 是 None，所以 default_return() 對它永遠不會給出
    一個看起來像「預測」的數字。

    Stage I1.1 hotfix：``source_type`` 是可選參數，預設 ``None``——**任何既有呼叫端
    不傳這個參數，行為與 Stage I1 之前完全一樣**（backward compatible by construction，
    不需要一次性遷移任何呼叫點）。只有呼叫端明確傳入 ``source_type="manual"`` 時，
    才會拿到 `manual` 這個「未分類」角色，而不是「不在名單內就當成 core_tw / TW
    equity」的舊 fallback——那個舊 fallback 只保留給 ``source_type`` 不是 `"manual"`
    的情況（即 legacy ``NULL``、``'etf'``），語意完全不變。
    """
    if is_portfolio_key(stock_id):
        return {
            "stock_id": stock_id, "role": "strategy_portfolio", "region": "TW",
            "overlap": stock_id, "fee": 0.0, "income_focus": False, "custom": False,
            "note": "Strategy Portfolio 整體部位；資產類別／地區為簡化近似值，"
                    "詳細研究證據請見 /api/finance/portfolio-evidence。",
        }
    if source_type == "manual":
        return {
            "stock_id": stock_id, "role": "manual", "region": "unspecified",
            "overlap": f"manual_{stock_id}", "fee": 0.0, "income_focus": False, "custom": True,
            "note": "使用者自訂資產：不屬於任何 ETF role，未分類為特定資產類別／地區，"
                    "請自行設定假設報酬。",
        }
    m = UNIVERSE.get(stock_id)
    if m:
        return {**m, "stock_id": stock_id, "custom": False}
    return {
        "stock_id": stock_id, "role": "core_tw", "region": "TW",
        "overlap": f"custom_{stock_id}", "fee": 0.0, "income_focus": False,
        "note": "自訂標的：角色與分類為預設值，請自行確認是否合適", "custom": True,
    }


def asset_of(stock_id: str, source_type: str | None = None) -> str:
    return ROLE_DEFAULTS[meta(stock_id, source_type)["role"]]["asset"]


def default_return(stock_id: str, source_type: str | None = None) -> float:
    return ROLE_DEFAULTS[meta(stock_id, source_type)["role"]]["assumed_return"]


def default_yield(stock_id: str, source_type: str | None = None) -> float:
    return ROLE_DEFAULTS[meta(stock_id, source_type)["role"]]["dividend_yield"]


def default_selection() -> list[str]:
    """新情境的預設啟用名單：各 role 各挑一檔具代表性的，避免一開始就重疊。"""
    return ["0050", "00646", "00662", "00878", "00719B", "CASH"]
