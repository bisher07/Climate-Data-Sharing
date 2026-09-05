"""Canonical serialisation and hashing.

Every hash that appears on the ledger is computed here. Two organizations that
independently hash the same logical record must produce the same digest, or
lineage verification (Section 5) and divergence detection (Section 9) break.
That requires a canonical byte form, not `json.dumps` defaults.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

__all__ = ["canonical_bytes", "sha256_hex", "hash_payload"]


def canonical_bytes(payload: Any) -> bytes:
    """Serialise `payload` to a deterministic byte string.

    Sorted keys, no insignificant whitespace, and escaped non-ASCII so the
    encoding does not depend on the producer's locale or Python version.
    """
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=_fallback,
    ).encode("utf-8")


def _fallback(value: Any) -> Any:
    # Pydantic models and datetimes are the only non-JSON types we anchor.
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "isoformat"):
        return value.isoformat()
    raise TypeError(f"{type(value).__name__} is not canonically serialisable")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def hash_payload(payload: Any) -> str:
    """The content hash anchored on-chain for an off-chain dataset."""
    return sha256_hex(canonical_bytes(payload))
