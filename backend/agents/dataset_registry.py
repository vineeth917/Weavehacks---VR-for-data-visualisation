"""Per-session active dataset.

Available datasets:
    titanic : seaborn.load_dataset("titanic") — 891×15, real, has missing values
              and a trainable target ("survived").
    sample  : data/sample.csv — synthetic, planted issues (right_skewed, outliers,
              heavy_missing, near_constant, correlated pair). Used by Phase 1 test.
    mpg     : seaborn.load_dataset("mpg") — 200×8 subset, continuous target ("mpg")
              for regression demos (RMSE / scatter, SGDRegressor).

The active dataset for a session is held in-process (cheap) and the dataset
profile is mirrored to Redis per PLAN §6.5.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from backend.tools.profiling import profile_dataset
from backend.tools import redis_state

log = logging.getLogger("hololab.datasets")

REPO_ROOT = Path(__file__).resolve().parents[2]
SAMPLE_CSV = REPO_ROOT / "data" / "sample.csv"
SEABORN_CACHE = REPO_ROOT / "data" / ".seaborn_cache"

AVAILABLE = ("titanic", "sample", "mpg")
DEFAULT = "titanic"

_active: dict[str, tuple[str, pd.DataFrame]] = {}


_TITANIC_URL = "https://raw.githubusercontent.com/mwaskom/seaborn-data/master/titanic.csv"


def _load_titanic() -> pd.DataFrame:
    """Load seaborn's titanic with a local CSV cache and certifi-backed TLS.

    macOS system Python lacks a CA bundle in many setups, so seaborn's online
    lookup fails with CERTIFICATE_VERIFY_FAILED. We download once with httpx
    (which ships certifi) into a repo-local cache and reuse from disk thereafter.
    """
    SEABORN_CACHE.mkdir(parents=True, exist_ok=True)
    cache_path = SEABORN_CACHE / "titanic.csv"
    if not cache_path.exists():
        import httpx
        log.info("downloading titanic.csv -> %s", cache_path)
        r = httpx.get(_TITANIC_URL, timeout=30.0)
        r.raise_for_status()
        cache_path.write_bytes(r.content)
    df = pd.read_csv(cache_path)
    # Match seaborn's schema convention: drop the duplicate "alive" column.
    drop = [c for c in ("alive",) if c in df.columns]
    return df.drop(columns=drop).reset_index(drop=True)


def _load_sample() -> pd.DataFrame:
    if not SAMPLE_CSV.exists():
        raise FileNotFoundError(
            f"{SAMPLE_CSV} missing — run `python backend/scripts/gen_sample_csv.py`"
        )
    return pd.read_csv(SAMPLE_CSV)


_MPG_URL = "https://raw.githubusercontent.com/mwaskom/seaborn-data/master/mpg.csv"
_MPG_MAX_ROWS = 200


def _load_mpg() -> pd.DataFrame:
    """Load seaborn mpg with local cache; keep a small row cap for fast demos."""
    SEABORN_CACHE.mkdir(parents=True, exist_ok=True)
    cache_path = SEABORN_CACHE / "mpg.csv"
    if not cache_path.exists():
        import httpx
        log.info("downloading mpg.csv -> %s", cache_path)
        r = httpx.get(_MPG_URL, timeout=30.0)
        r.raise_for_status()
        cache_path.write_bytes(r.content)
    df = pd.read_csv(cache_path)
    if len(df) > _MPG_MAX_ROWS:
        df = df.head(_MPG_MAX_ROWS).reset_index(drop=True)
    return df


_LOADERS = {"titanic": _load_titanic, "sample": _load_sample, "mpg": _load_mpg}


def load(sid: str, name: str) -> pd.DataFrame:
    """Load (or reload) a named dataset for a session; cache + mirror profile."""
    if name not in _LOADERS:
        raise ValueError(f"unknown dataset {name!r}; pick one of {AVAILABLE}")
    df = _LOADERS[name]()
    _active[sid] = (name, df)
    # mirror profile to Redis so other agents see it without recomputing
    try:
        prof = profile_dataset(df)
        prof["dataset_name"] = name
        redis_state.set_profile(sid, prof)
    except Exception as e:  # noqa: BLE001
        log.warning("profile mirror to redis failed: %s", e)
    log.info("dataset loaded sid=%s name=%s shape=%s", sid, name, df.shape)
    return df


def get_active(sid: str) -> tuple[str, pd.DataFrame] | None:
    return _active.get(sid)


def get_or_default(sid: str) -> tuple[str, pd.DataFrame]:
    """Return (name, df) for the active dataset; lazily load DEFAULT if none."""
    a = _active.get(sid)
    if a is not None:
        return a
    df = load(sid, DEFAULT)
    return DEFAULT, df


def reset(sid: str) -> None:
    _active.pop(sid, None)
