"""Redis state helpers — single source of truth for session data (PLAN.md §6.5).

Key schema:
    session:{sid}:dataset_profile      JSON  {schema, dtypes, n_rows, n_cols}
    session:{sid}:eda_findings         JSON  [EDA result, ...]
    session:{sid}:training:{run_id}    JSON  {latest_metrics, history_ref, status}
    session:{sid}:memory               LIST  conversation turns
    session:{sid}:scratch:{agent}     JSON   per-agent working memory
"""
from __future__ import annotations

import json
import logging
from typing import Any

import redis

from backend.config import REDIS_URL

log = logging.getLogger(__name__)

# Lazy singleton — fail soft so dev can boot without Redis.
_client: redis.Redis | None = None


def client() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.from_url(REDIS_URL, decode_responses=True)
    return _client


def ping() -> bool:
    try:
        return bool(client().ping())
    except Exception as e:  # noqa: BLE001
        log.warning("redis ping failed: %s", e)
        return False


# ---- key builders ----

def _k_profile(sid: str) -> str:           return f"session:{sid}:dataset_profile"
def _k_findings(sid: str) -> str:          return f"session:{sid}:eda_findings"
def _k_training(sid: str, rid: str) -> str: return f"session:{sid}:training:{rid}"
def _k_memory(sid: str) -> str:            return f"session:{sid}:memory"
def _k_scratch(sid: str, agent: str) -> str: return f"session:{sid}:scratch:{agent}"


# ---- generic JSON get/set ----

def jget(key: str) -> Any | None:
    raw = client().get(key)
    return json.loads(raw) if raw else None


def jset(key: str, value: Any, ex: int | None = 60 * 60) -> None:
    client().set(key, json.dumps(value, default=str), ex=ex)


# ---- typed helpers ----

def set_profile(sid: str, profile: dict[str, Any]) -> None:
    jset(_k_profile(sid), profile)


def get_profile(sid: str) -> dict[str, Any] | None:
    return jget(_k_profile(sid))


def append_findings(sid: str, findings: list[dict[str, Any]]) -> None:
    existing = jget(_k_findings(sid)) or []
    existing.extend(findings)
    jset(_k_findings(sid), existing)


def get_findings(sid: str) -> list[dict[str, Any]]:
    return jget(_k_findings(sid)) or []


def set_training(sid: str, run_id: str, state: dict[str, Any]) -> None:
    jset(_k_training(sid, run_id), state)


def get_training(sid: str, run_id: str) -> dict[str, Any] | None:
    return jget(_k_training(sid, run_id))


def push_memory(sid: str, turn: dict[str, Any], cap: int = 200) -> None:
    c = client()
    c.rpush(_k_memory(sid), json.dumps(turn, default=str))
    c.ltrim(_k_memory(sid), -cap, -1)
    c.expire(_k_memory(sid), 60 * 60)


def get_memory(sid: str, n: int = 50) -> list[dict[str, Any]]:
    raws = client().lrange(_k_memory(sid), -n, -1)
    return [json.loads(r) for r in raws]


def set_scratch(sid: str, agent: str, value: Any) -> None:
    jset(_k_scratch(sid, agent), value)


def get_scratch(sid: str, agent: str) -> Any | None:
    return jget(_k_scratch(sid, agent))


def reset_session(sid: str) -> int:
    """Delete all keys for a session. Returns count deleted."""
    c = client()
    keys = list(c.scan_iter(match=f"session:{sid}:*"))
    return c.delete(*keys) if keys else 0
