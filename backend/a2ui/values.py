"""v0.8 adjacency-list value encoder.

Convert plain Python (dict | list | str | int | float | bool) into A2UI v0.8
`contents` arrays of the form  [{"key": ..., "value*": ...}, ...].
"""
from __future__ import annotations

from typing import Any


def encode_value(v: Any) -> dict[str, Any]:
    """Encode a single value as the appropriate `value*` dict."""
    if isinstance(v, bool):
        return {"valueBoolean": v}
    if isinstance(v, (int, float)):
        return {"valueNumber": float(v)}
    if v is None:
        return {"valueString": ""}
    if isinstance(v, str):
        return {"valueString": v}
    if isinstance(v, list):
        return {"valueList": [encode_value(x) for x in v]}
    if isinstance(v, dict):
        return {"valueMap": encode_contents(v)}
    return {"valueString": str(v)}


def encode_contents(d: dict[str, Any]) -> list[dict[str, Any]]:
    """Encode a flat dict as an adjacency list of {key, value*} entries."""
    out: list[dict[str, Any]] = []
    for k, v in d.items():
        entry = {"key": str(k)}
        entry.update(encode_value(v))
        out.append(entry)
    return out
