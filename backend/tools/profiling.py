"""EDA compute for the EDA agent (PLAN §5).

Synchronous, pure-pandas/numpy. Callers (the agent) should wrap in
asyncio.to_thread to keep the WS event loop responsive.

Public API:
    profile_dataset(df, top_corr=5)  -> ProfileDict
    flag_columns(profile, kind)      -> list[str]
"""
from __future__ import annotations

from typing import Any, TypedDict

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Thresholds (tune here — they show up in agent prompts later)
# ---------------------------------------------------------------------------
SKEW_RIGHT = 1.0
SKEW_LEFT = -1.0
HEAVY_MISSING_PCT = 20.0
NEAR_CONSTANT_DOMINANT_FRAC = 0.95      # dominant non-null value covers ≥ 95%
HIGH_CARDINALITY_UNIQUE_FRAC = 0.95     # likely ID-like
OUTLIER_PCT_FLAG = 5.0                  # IQR outliers > 5% → flag


class ColumnProfile(TypedDict, total=False):
    name: str
    dtype: str
    missing_pct: float
    unique_count: int
    mean: float
    std: float
    min: float
    max: float
    skew: float
    outlier_pct: float
    flags: list[str]
    plot: str  # suggested PanelKind: histogram|box|kde|missing|corr


def _is_numeric(s: pd.Series) -> bool:
    return pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s)


def _iqr_outlier_pct(s: pd.Series) -> float:
    x = pd.to_numeric(s, errors="coerce").dropna()
    if len(x) < 4:
        return 0.0
    q1, q3 = np.quantile(x, [0.25, 0.75])
    iqr = q3 - q1
    if iqr == 0:
        return 0.0
    lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    return float(((x < lo) | (x > hi)).mean() * 100.0)


def _safe_skew(s: pd.Series) -> float | None:
    x = pd.to_numeric(s, errors="coerce").dropna()
    if len(x) < 3 or x.nunique() < 2:
        return None
    return float(x.skew())


def _suggest_plot(col: ColumnProfile) -> str:
    dtype = col["dtype"]
    if "missing" in col.get("flags", []) or col["missing_pct"] >= HEAVY_MISSING_PCT:
        return "missing"
    if dtype.startswith(("int", "float", "uint")):
        # prefer KDE for smoother distributions; histogram for small unique counts
        if col.get("unique_count", 0) > 30:
            return "kde"
        return "histogram"
    return "box"


def profile_column(s: pd.Series) -> ColumnProfile:
    n = len(s)
    missing_pct = float(s.isna().mean() * 100.0) if n else 0.0
    unique = int(s.nunique(dropna=True))
    col: ColumnProfile = {
        "name": str(s.name),
        "dtype": str(s.dtype),
        "missing_pct": round(missing_pct, 3),
        "unique_count": unique,
        "flags": [],
    }

    if _is_numeric(s):
        x = pd.to_numeric(s, errors="coerce").dropna()
        if len(x):
            col["mean"] = float(x.mean())
            col["std"] = float(x.std())
            col["min"] = float(x.min())
            col["max"] = float(x.max())
        sk = _safe_skew(s)
        if sk is not None:
            col["skew"] = round(sk, 3)
        col["outlier_pct"] = round(_iqr_outlier_pct(s), 3)

    # Flags
    flags: list[str] = []
    if missing_pct >= HEAVY_MISSING_PCT:
        flags.append("heavy_missing")
    # near_constant: dominant *non-null* value covers >= 95% of non-null rows.
    # This catches "all zeros except a few" without flagging healthy low-cardinality
    # categoricals (e.g. 4 evenly-distributed classes).
    nn = s.dropna()
    if len(nn):
        vc = nn.value_counts()
        if float(vc.iloc[0]) / float(len(nn)) >= NEAR_CONSTANT_DOMINANT_FRAC:
            flags.append("near_constant")
    if n and unique >= int(n * HIGH_CARDINALITY_UNIQUE_FRAC) and unique > 50:
        flags.append("high_cardinality")
    sk = col.get("skew")
    if sk is not None:
        if sk >= SKEW_RIGHT:
            flags.append("right_skewed")
        elif sk <= SKEW_LEFT:
            flags.append("left_skewed")
    if col.get("outlier_pct", 0.0) > OUTLIER_PCT_FLAG:
        flags.append("outliers")
    col["flags"] = flags
    col["plot"] = _suggest_plot(col)
    return col


def top_correlations(
    df: pd.DataFrame, k: int = 5, min_abs: float = 0.3
) -> list[dict[str, Any]]:
    num = df.select_dtypes(include="number")
    if num.shape[1] < 2:
        return []
    corr = num.corr(numeric_only=True).abs()
    pairs: list[tuple[str, str, float]] = []
    cols = corr.columns.tolist()
    for i in range(len(cols)):
        for j in range(i + 1, len(cols)):
            r_signed = float(num.corr(numeric_only=True).iloc[i, j])
            if abs(r_signed) >= min_abs:
                pairs.append((cols[i], cols[j], r_signed))
    pairs.sort(key=lambda t: abs(t[2]), reverse=True)
    return [
        {"a": a, "b": b, "pearson": round(r, 3)} for a, b, r in pairs[:k]
    ]


class DatasetProfile(TypedDict):
    n_rows: int
    n_cols: int
    schema: dict[str, str]
    columns: list[ColumnProfile]
    top_correlations: list[dict[str, Any]]
    notes: list[str]


def profile_dataset(df: pd.DataFrame, top_corr: int = 5) -> DatasetProfile:
    """Compute a full per-column profile + top correlations."""
    columns = [profile_column(df[c]) for c in df.columns]
    schema = {str(c): str(df[c].dtype) for c in df.columns}
    notes: list[str] = []
    if df.empty:
        notes.append("empty dataframe")
    n_dup = int(df.duplicated().sum())
    if n_dup:
        notes.append(f"{n_dup} duplicate rows")
    return {
        "n_rows": int(len(df)),
        "n_cols": int(df.shape[1]),
        "schema": schema,
        "columns": columns,
        "top_correlations": top_correlations(df, k=top_corr),
        "notes": notes,
    }


def flag_columns(profile: DatasetProfile, flag: str) -> list[str]:
    """Return column names that have a given flag (e.g. 'right_skewed')."""
    return [c["name"] for c in profile["columns"] if flag in c.get("flags", [])]
