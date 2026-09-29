"""Stable identifiers for validation integrity checks."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def geometry_fingerprint(geometry: Any) -> str | None:
    """Hash an exact canonical GeoJSON geometry without retaining coordinates."""

    if not isinstance(geometry, dict):
        return None
    canonical = json.dumps(
        geometry,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
