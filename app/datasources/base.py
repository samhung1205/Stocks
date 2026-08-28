"""資料源共同基礎：單一 HTTP client、每主機禮貌限速、重試、短期記憶快取。"""
import ssl
import threading
import time
from urllib.parse import urlparse

import httpx

from app.config import POLITE_INTERVAL

# 櫃買中心憑證缺 Subject Key Identifier，會被 Python 3.13+ 的
# VERIFY_X509_STRICT 擋下；關閉 strict 旗標但保留完整憑證鏈驗證。
_ssl_ctx = ssl.create_default_context()
_ssl_ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT

_client = httpx.Client(
    verify=_ssl_ctx,
    headers={
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) personal-research",
        "Accept": "application/json, text/html;q=0.9, */*;q=0.8",
    },
    timeout=30,
    follow_redirects=True,
)

_lock = threading.Lock()
_last_hit: dict[str, float] = {}
_cache: dict[str, tuple[float, object]] = {}


def _throttle(host: str) -> None:
    interval = POLITE_INTERVAL.get(host, 1.0)
    with _lock:
        wait = _last_hit.get(host, 0) + interval - time.time()
        if wait > 0:
            time.sleep(wait)
        _last_hit[host] = time.time()


def get(url: str, params: dict | None = None, cache_ttl: float = 0,
        retries: int = 2) -> httpx.Response:
    """帶限速與重試的 GET。cache_ttl>0 時以 URL+params 快取回應。"""
    key = f"{url}|{params}"
    if cache_ttl > 0:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < cache_ttl:
            return hit[1]
    host = urlparse(url).netloc
    last_err: Exception | None = None
    for attempt in range(retries + 1):
        _throttle(host)
        try:
            resp = _client.get(url, params=params)
            resp.raise_for_status()
            if cache_ttl > 0:
                _cache[key] = (time.time(), resp)
            return resp
        except httpx.HTTPStatusError as e:
            # 4xx（除 429 限速外）是永久性錯誤，重試無意義：直接向上拋出讓
            # 呼叫端依狀態碼判斷（如金鑰無效 401、代號不存在 404）。
            if e.response.status_code != 429 and e.response.status_code < 500:
                raise
            last_err = e
            time.sleep(1.5 * (attempt + 1))
        except httpx.HTTPError as e:
            last_err = e
            time.sleep(1.5 * (attempt + 1))
    raise ConnectionError(f"GET {url} 失敗：{last_err}") from last_err


def get_json(url: str, params: dict | None = None, cache_ttl: float = 0):
    return get(url, params=params, cache_ttl=cache_ttl).json()


def num(v) -> float | None:
    """證交所/櫃買數字欄位清洗：'1,234'、'--'、''、'+95.00' → float 或 None。"""
    if v is None or isinstance(v, (int, float)):
        return v if v is None else float(v)
    s = str(v).strip().replace(",", "").replace("+", "")
    if s in ("", "--", "-", "─", "X", "除權息", "N/A", "不適用"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def roc_to_iso(roc: str) -> str | None:
    """民國日期 '1150731' 或 '115/07/31' → '2026-07-31'。"""
    s = str(roc).strip().replace("/", "")
    if not s.isdigit() or len(s) < 6:
        return None
    y, m, d = int(s[:-4]) + 1911, s[-4:-2], s[-2:]
    return f"{y}-{m}-{d}"
