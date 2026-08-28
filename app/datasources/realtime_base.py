"""即時行情抽象介面。

MISSnapshotProvider（約 5 秒延遲，免帳戶）為預設後備；設定 FUGLE_API_KEY 後
自動改用 FugleProvider（真即時）。日後要接永豐 Shioaji，比照新增一個
Provider 實作同一介面即可插拔替換，前端與服務層完全不用改。
"""
import logging
from abc import ABC, abstractmethod

from app.config import FUGLE_API_KEY

log = logging.getLogger(__name__)


class RealtimeProvider(ABC):
    """即時（或準即時）行情提供者介面。"""

    name: str = "base"
    delay_seconds: int = 0

    @abstractmethod
    def quotes(self, pairs: list[tuple[str, str]]) -> list[dict]:
        """pairs: [(stock_id, market)] → 統一格式報價 dict 列表。"""


class MISSnapshotProvider(RealtimeProvider):
    """證交所 MIS 公開快照，約 5 秒延遲，免帳戶。"""

    name = "mis"
    delay_seconds = 5

    def quotes(self, pairs):
        from app.datasources import mis_snapshot
        return mis_snapshot.snapshot(pairs)


class FugleProvider(RealtimeProvider):
    """富果行情 REST API，真即時（免費方案僅能逐檔查詢，約60次/分鐘限速）。"""

    name = "fugle"
    delay_seconds = 0

    def quotes(self, pairs):
        from app.datasources import fugle
        out = []
        for stock_id, _market in pairs:
            try:
                q = fugle.quote(stock_id)
                if q:
                    out.append(q)
            except fugle.FugleAuthError:
                log.warning("Fugle 金鑰無效，本次降級為 MIS 快照")
                return MISSnapshotProvider().quotes(pairs)
            except Exception as e:
                log.warning("Fugle quote %s 失敗：%s", stock_id, e)
        return out


def get_provider() -> RealtimeProvider:
    """依設定回傳目前使用的行情提供者：有富果金鑰就用富果，否則退回 MIS 快照。"""
    if FUGLE_API_KEY:
        return FugleProvider()
    return MISSnapshotProvider()
