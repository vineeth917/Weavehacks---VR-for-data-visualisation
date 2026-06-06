"""Plot rendering for EDA panels (PLAN §6.2).

Returns small base64-encoded PNGs (target ≤ 80 KB).
Uses Agg backend explicitly so it runs headless.

Public API:
    render_plot(df, column, kind, *, max_kb=80) -> (b64_str, size_bytes)
    build_panel(df, column, kind, *, panel_id=None) -> Panel dict (per contracts.Panel)
"""
from __future__ import annotations

import base64
import io
import math
from typing import Any, Literal

import matplotlib

matplotlib.use("Agg")  # noqa: E402  must precede pyplot import
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

PanelKind = Literal["histogram", "box", "kde", "corr", "missing"]

# Compact figures by default — fits the VR floating panel form-factor.
_DEFAULT_FIGSIZE = (3.6, 2.6)
_DEFAULT_DPI = 110


def _fig_to_b64(fig: plt.Figure, *, dpi: int = _DEFAULT_DPI, max_kb: int = 80) -> tuple[str, int]:
    """Save fig to PNG, retry with lower DPI until under max_kb."""
    for trial_dpi in (dpi, 90, 75, 60, 48):
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=trial_dpi, bbox_inches="tight", pad_inches=0.05)
        data = buf.getvalue()
        if len(data) <= max_kb * 1024:
            break
    plt.close(fig)
    return base64.b64encode(data).decode("ascii"), len(data)


def _histogram(df: pd.DataFrame, col: str) -> plt.Figure:
    x = pd.to_numeric(df[col], errors="coerce").dropna()
    fig, ax = plt.subplots(figsize=_DEFAULT_FIGSIZE)
    bins = min(50, max(10, int(math.sqrt(len(x))))) if len(x) else 10
    ax.hist(x, bins=bins, color="#3cb371", edgecolor="white", linewidth=0.4)
    ax.set_title(col, fontsize=10)
    ax.set_xlabel("")
    ax.set_ylabel("count", fontsize=8)
    ax.tick_params(axis="both", labelsize=7)
    return fig


def _box(df: pd.DataFrame, col: str) -> plt.Figure:
    x = pd.to_numeric(df[col], errors="coerce").dropna()
    fig, ax = plt.subplots(figsize=_DEFAULT_FIGSIZE)
    ax.boxplot(x, vert=True, patch_artist=True,
               boxprops={"facecolor": "#3cb371", "alpha": 0.6},
               medianprops={"color": "black"})
    ax.set_title(col, fontsize=10)
    ax.set_xticks([])
    ax.tick_params(axis="y", labelsize=7)
    return fig


def _kde(df: pd.DataFrame, col: str) -> plt.Figure:
    from scipy.stats import gaussian_kde  # local import; scipy is heavy

    x = pd.to_numeric(df[col], errors="coerce").dropna().values
    fig, ax = plt.subplots(figsize=_DEFAULT_FIGSIZE)
    if len(x) >= 5 and np.unique(x).size >= 2:
        kde = gaussian_kde(x)
        xs = np.linspace(x.min(), x.max(), 256)
        ax.fill_between(xs, kde(xs), color="#3cb371", alpha=0.55)
        ax.plot(xs, kde(xs), color="#216f48", linewidth=1.0)
    else:
        ax.text(0.5, 0.5, "n<5 — KDE skipped", ha="center", va="center",
                transform=ax.transAxes, fontsize=8)
    ax.set_title(col, fontsize=10)
    ax.set_ylabel("density", fontsize=8)
    ax.tick_params(axis="both", labelsize=7)
    return fig


def _corr(df: pd.DataFrame, _col_unused: str | None = None) -> plt.Figure:
    num = df.select_dtypes(include="number")
    fig, ax = plt.subplots(figsize=(3.6, 3.2))
    if num.shape[1] < 2:
        ax.text(0.5, 0.5, "need ≥2 numeric cols", ha="center", va="center",
                transform=ax.transAxes, fontsize=9)
        return fig
    corr = num.corr(numeric_only=True).values
    im = ax.imshow(corr, vmin=-1, vmax=1, cmap="RdBu_r")
    ax.set_xticks(range(num.shape[1]))
    ax.set_yticks(range(num.shape[1]))
    ax.set_xticklabels(num.columns, rotation=45, ha="right", fontsize=7)
    ax.set_yticklabels(num.columns, fontsize=7)
    for i in range(num.shape[1]):
        for j in range(num.shape[1]):
            ax.text(j, i, f"{corr[i, j]:.2f}", ha="center", va="center",
                    fontsize=6, color="white" if abs(corr[i, j]) > 0.5 else "black")
    ax.set_title("correlation", fontsize=10)
    fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04).ax.tick_params(labelsize=6)
    return fig


def _missing(df: pd.DataFrame, _col_unused: str | None = None) -> plt.Figure:
    """Missing-value matrix — rows = sample, cols = columns. Black = present."""
    fig, ax = plt.subplots(figsize=(3.6, 2.8))
    if df.empty:
        ax.text(0.5, 0.5, "empty dataframe", ha="center", va="center")
        return fig
    sample = df.sample(n=min(len(df), 400), random_state=0).sort_index()
    mat = sample.notna().to_numpy().astype(float)
    ax.imshow(mat, aspect="auto", cmap="Greys", interpolation="nearest")
    ax.set_xticks(range(df.shape[1]))
    ax.set_xticklabels(df.columns, rotation=45, ha="right", fontsize=7)
    ax.set_yticks([])
    ax.set_title("missingness matrix", fontsize=10)
    return fig


_RENDERERS = {
    "histogram": _histogram,
    "box": _box,
    "kde": _kde,
    "corr": _corr,
    "missing": _missing,
}


def render_plot(
    df: pd.DataFrame,
    column: str | None,
    kind: PanelKind,
    *,
    max_kb: int = 80,
) -> tuple[str, int]:
    if kind not in _RENDERERS:
        raise ValueError(f"unknown plot kind: {kind}")
    renderer = _RENDERERS[kind]
    if kind in ("corr", "missing"):
        fig = renderer(df, None)  # type: ignore[arg-type]
    else:
        if column is None or column not in df.columns:
            raise ValueError(f"plot {kind} needs a valid column, got {column!r}")
        fig = renderer(df, column)
    return _fig_to_b64(fig, max_kb=max_kb)


def build_panel(
    df: pd.DataFrame,
    column: str | None,
    kind: PanelKind,
    *,
    panel_id: str | None = None,
    title: str | None = None,
    flags: list[str] | None = None,
    position_hint: str = "center",
) -> dict[str, Any]:
    """Return a dict matching contracts.Panel."""
    b64, size = render_plot(df, column, kind)
    pid = panel_id or (
        f"{column}_{kind}" if column else f"dataset_{kind}"
    )
    return {
        "id": pid,
        "kind": kind,
        "title": title or (column.title() if column else kind.title()),
        "column": column,
        "image_b64": b64,
        "position_hint": position_hint,
        "flags": flags or [],
        "_size_bytes": size,  # internal; callers may strip before WS send
    }
