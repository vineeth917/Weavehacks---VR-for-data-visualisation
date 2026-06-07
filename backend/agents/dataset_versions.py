"""Versioned, non-destructive dataset copies per session (preprocessor only).

Original loaded data is always preserved as v0 in Redis:
    session:{sid}:dataset_v0
    session:{sid}:dataset_v1
    ...
    session:{sid}:dataset_meta   JSON {name, current_version, v0_rows}

When ENABLE_PREPROCESSOR is off, this module is unused and dataset_registry
behaviour is unchanged.
"""
from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from backend.tools import redis_state

log = logging.getLogger("hololab.dataset_versions")

_working: dict[str, tuple[int, str, pd.DataFrame]] = {}


def _meta_key(sid: str) -> str:
    return f"session:{sid}:dataset_meta"


def _version_key(sid: str, version: int) -> str:
    return f"session:{sid}:dataset_v{version}"


def _serialize_df(df: pd.DataFrame) -> dict[str, Any]:
    return {
        "columns": [str(c) for c in df.columns],
        "records": df.to_dict(orient="records"),
    }


def _deserialize_df(blob: dict[str, Any]) -> pd.DataFrame:
    return pd.DataFrame(blob.get("records") or [], columns=blob.get("columns") or [])


def _get_meta(sid: str) -> dict[str, Any] | None:
    return redis_state.jget(_meta_key(sid))


def _set_meta(sid: str, meta: dict[str, Any]) -> None:
    redis_state.jset(_meta_key(sid), meta)


def snapshot_v0(sid: str, dataset_name: str, df: pd.DataFrame) -> None:
    """Store the loaded dataset as immutable v0 and set working copy to v0."""
    v0 = df.copy()
    redis_state.jset(_version_key(sid, 0), _serialize_df(v0))
    meta = {
        "name": dataset_name,
        "current_version": 0,
        "v0_rows": len(v0),
    }
    _set_meta(sid, meta)
    _working[sid] = (0, dataset_name, v0)
    log.info("dataset v0 snapshotted sid=%s name=%s rows=%d", sid, dataset_name, len(v0))


def ensure_baseline(sid: str, dataset_name: str, df: pd.DataFrame) -> None:
    """Ensure v0 exists; if meta missing, snapshot current df as v0."""
    if _get_meta(sid) is None:
        snapshot_v0(sid, dataset_name, df)


def get_working(sid: str) -> tuple[int, str, pd.DataFrame] | None:
    """Return (version, dataset_name, df) for the current working copy."""
    if sid in _working:
        return _working[sid]
    meta = _get_meta(sid)
    if meta is None:
        return None
    ver = int(meta.get("current_version", 0))
    blob = redis_state.jget(_version_key(sid, ver))
    if not blob:
        return None
    df = _deserialize_df(blob)
    name = str(meta.get("name") or "unknown")
    _working[sid] = (ver, name, df)
    return ver, name, df


def get_v0(sid: str) -> pd.DataFrame | None:
    """Return the immutable v0 dataframe from Redis."""
    blob = redis_state.jget(_version_key(sid, 0))
    if not blob:
        return None
    return _deserialize_df(blob)


def save_new_version(sid: str, df: pd.DataFrame, *, changes: list[str]) -> int:
    """Persist df as the next version; returns new version number."""
    meta = _get_meta(sid) or {"name": "unknown", "current_version": 0, "v0_rows": len(df)}
    new_ver = int(meta.get("current_version", 0)) + 1
    meta["current_version"] = new_ver
    meta["last_changes"] = changes[-20:]
    redis_state.jset(_version_key(sid, new_ver), _serialize_df(df))
    _set_meta(sid, meta)
    name = str(meta.get("name") or "unknown")
    _working[sid] = (new_ver, name, df.copy())
    log.info("dataset v%d saved sid=%s rows=%d", new_ver, sid, len(df))
    return new_ver


def current_version(sid: str) -> int | None:
    meta = _get_meta(sid)
    return int(meta["current_version"]) if meta else None


def clear_session(sid: str) -> None:
    _working.pop(sid, None)
