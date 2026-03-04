"""Canonical hashing for snapshots and Merkle tree nodes."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any


def canonical_snapshot_hash(
    model_id: str,
    period_start: datetime,
    period_end: datetime,
    prediction_count: int,
    result_summary: dict[str, Any],
) -> str:
    """Compute a deterministic SHA-256 hash of snapshot content.

    Uses sorted-key JSON with no whitespace so any implementation
    can independently reproduce the same hash.
    """
    payload = {
        "model_id": model_id,
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "prediction_count": prediction_count,
        "result_summary": result_summary,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def sha256_concat(left: str, right: str) -> str:
    """Hash two hex-encoded hashes together: SHA-256(left + right)."""
    combined = left + right
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()
