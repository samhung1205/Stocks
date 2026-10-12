"""The ONE authoritative Walk Forward contract served to clients.

Independent-fold semantics live in ``oos.CONTRACT``, stitched semantics in ``stitched.CONTRACT``,
protocol defaults in ``protocol``.  Everything a client (UI) may rely on is returned by ``get()``;
both API routes that expose contracts return exactly this object.
"""
from __future__ import annotations

from app.services.walkforward import freeze, oos, protocol, stitched

VERSION = "wf-contracts/f1.5/1"


def get() -> dict:
    return {
        "version": VERSION,
        "protocol": {"version": protocol.PROTOCOL_VERSION, "defaults": protocol.DEFAULT_PROTOCOL},
        "candidate_rule": freeze.RULE_VERSION,
        "evidence": {"primary": "independent_fold", "secondary": "stitched_secondary"},
        "independent_fold": oos.CONTRACT,
        "stitched_oos": stitched.CONTRACT,
    }
