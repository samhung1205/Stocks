"""全域設定：路徑、金鑰、限速參數。全部可用環境變數覆寫，方便日後上雲。"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("STOCKS_DATA_DIR", BASE_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_URL = os.environ.get("STOCKS_DB_URL", f"sqlite:///{DATA_DIR / 'stocks.db'}")

# FinMind 金鑰（免費註冊後到 https://finmindtrade.com 取得，可提高到 600 次/小時；不填也能用但額度較低）
FINMIND_TOKEN = os.environ.get("FINMIND_TOKEN", "")

# 富果行情 API 金鑰（https://developer.fugle.tw 申請；填了會自動取代 MIS 快照做真即時報價）
FUGLE_API_KEY = os.environ.get("FUGLE_API_KEY", "")

# 禮貌抓取間隔（秒）：官網 JSON 屬公開未授權端點，務必低頻
POLITE_INTERVAL = {
    "www.twse.com.tw": 2.0,
    "www.tpex.org.tw": 2.0,
    "mops.twse.com.tw": 3.0,
    "mis.twse.com.tw": 3.0,
    "openapi.twse.com.tw": 0.5,
    "api.finmindtrade.com": 0.4,
    "api.fugle.tw": 1.05,   # 免費方案 60 次/分鐘（實測 x-ratelimit-limit: 60）
}

# 深度資料視為新鮮的時數（期限內重開個股頁不重抓）
DEEP_FRESH_HOURS = 12
# 深度資料清理：非觀察名單且 N 天未存取可釋放（為未來上雲預留）
DEEP_RETENTION_DAYS = 90

ENABLE_SCHEDULER = os.environ.get("ENABLE_SCHEDULER", "1") == "1"

# 交易成本預設：手續費 0.1425%（打折自行調整）、賣出證交稅 0.3%（ETF 0.1%）
FEE_RATE = float(os.environ.get("FEE_RATE", "0.001425"))
FEE_DISCOUNT = float(os.environ.get("FEE_DISCOUNT", "0.6"))
TAX_RATE = float(os.environ.get("TAX_RATE", "0.003"))


def load_dotenv() -> None:
    """極簡 .env 載入（不引第三方套件）。"""
    env_file = BASE_DIR / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())


load_dotenv()
FINMIND_TOKEN = os.environ.get("FINMIND_TOKEN", FINMIND_TOKEN)
FUGLE_API_KEY = os.environ.get("FUGLE_API_KEY", FUGLE_API_KEY)
