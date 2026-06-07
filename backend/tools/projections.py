"""3D EDA payload builders (PLAN §6.2).

Three tools the EDA agent can call:

  project_3d(df)     → Scatter3D payload (PCA→3 comps + optional color)
  kde_surface(df,x,y)→ Surface payload (gaussian KDE grid on two columns)
  corr_field(df)     → Field payload (full correlation matrix as a tensor)

All return plain dicts compatible with backend.contracts.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import gaussian_kde
from sklearn.decomposition import PCA
from sklearn.preprocessing import StandardScaler

# Palette for default colorisation when a categorical column is provided
_PALETTE = ["#3cb371", "#4677e2", "#e2884d", "#a55ee2",
            "#e25d6c", "#f5c948", "#34c4cf", "#cc4d8a"]


# ---------------------------------------------------------------------------
# 1. project_3d  →  Scatter3D
# ---------------------------------------------------------------------------

def project_3d(
    df: pd.DataFrame,
    *,
    color_by: str | None = None,
    max_points: int = 2_000,
    title: str = "PCA projection",
) -> dict[str, Any]:
    """Project numeric columns to 3 PCA components.

    Returns dict matching contracts.Scatter3D.
    """
    num = df.select_dtypes(include="number").dropna(axis=0)
    if num.shape[1] < 3:
        raise ValueError(
            f"project_3d needs ≥3 numeric columns after dropna, got {num.shape[1]}"
        )
    if len(num) > max_points:
        num = num.sample(n=max_points, random_state=0).sort_index()

    # Standardize first — otherwise a single high-variance column (e.g. income
    # at scale ~5e4 vs pca1 at scale ~1) eats the entire first component.
    scaler = StandardScaler()
    z = scaler.fit_transform(num.values)
    pca = PCA(n_components=3, random_state=0)
    coords = pca.fit_transform(z)
    # rescale to a ~1m room
    coords = coords / (np.abs(coords).max() + 1e-9)
    explained = pca.explained_variance_ratio_

    # color
    color_series: pd.Series | None = None
    if color_by and color_by in df.columns:
        color_series = df.loc[num.index, color_by]

    points: list[dict[str, Any]] = []
    if color_series is not None and not pd.api.types.is_numeric_dtype(color_series):
        # categorical → palette
        cats = list(pd.Categorical(color_series).categories)
        cat_idx = {c: i % len(_PALETTE) for i, c in enumerate(cats)}
    else:
        cat_idx = {}

    for i, (idx, (x, y, z)) in enumerate(zip(num.index, coords)):
        color = "#3cb371"
        if color_series is not None:
            v = color_series.iloc[i] if i < len(color_series) else None
            if pd.api.types.is_numeric_dtype(color_series):
                # numeric → gradient (simple normalize)
                vmin, vmax = float(color_series.min()), float(color_series.max())
                t = 0.0 if vmax == vmin else (float(v) - vmin) / (vmax - vmin)
                r = int(60 + 195 * t)
                g = int(180 - 100 * t)
                b = int(120 + 50 * (1 - t))
                color = f"#{r:02x}{g:02x}{b:02x}"
            else:
                color = _PALETTE[cat_idx.get(v, 0)]
        points.append({
            "id": f"r{idx}",
            "x": round(float(x), 4),
            "y": round(float(y), 4),
            "z": round(float(z), 4),
            "color": color,
            "size": 0.03,
            "shape": "sphere",
            "label": str(idx),
        })

    return {
        "type": "scatter3d",
        "title": f"{title} (PC1 {explained[0]*100:.0f}%, "
                 f"PC2 {explained[1]*100:.0f}%, PC3 {explained[2]*100:.0f}%)",
        "axes": {"x": "PC1", "y": "PC2", "z": "PC3"},
        "points": points,
    }


# ---------------------------------------------------------------------------
# 2. kde_surface  →  Surface payload (new outbound type)
# ---------------------------------------------------------------------------

def kde_surface(
    df: pd.DataFrame,
    x: str,
    y: str,
    *,
    grid: int = 48,
    title: str | None = None,
) -> dict[str, Any]:
    """2D gaussian KDE on (x, y) → surface payload.

    Returns dict shape (new outbound type 'surface', see contracts.Surface):
        { "type":"surface", "title", "axes":{x,y,z}, "grid":N,
          "x_extent":[xmin,xmax], "y_extent":[ymin,ymax],
          "z":[[...],...]  # shape (grid, grid), row = y, col = x
        }
    """
    if x not in df.columns or y not in df.columns:
        raise ValueError(f"unknown column(s): {x}, {y}")
    xs = pd.to_numeric(df[x], errors="coerce").to_numpy()
    ys = pd.to_numeric(df[y], errors="coerce").to_numpy()
    mask = np.isfinite(xs) & np.isfinite(ys)
    xs, ys = xs[mask], ys[mask]
    if len(xs) < 5:
        raise ValueError(f"need ≥5 finite rows for KDE, got {len(xs)}")

    xmin, xmax = float(xs.min()), float(xs.max())
    ymin, ymax = float(ys.min()), float(ys.max())
    # pad 5% so the surface doesn't clip the edge density
    pad_x = (xmax - xmin) * 0.05 or 1.0
    pad_y = (ymax - ymin) * 0.05 or 1.0
    xmin, xmax = xmin - pad_x, xmax + pad_x
    ymin, ymax = ymin - pad_y, ymax + pad_y

    kde = gaussian_kde(np.vstack([xs, ys]))
    gx = np.linspace(xmin, xmax, grid)
    gy = np.linspace(ymin, ymax, grid)
    xx, yy = np.meshgrid(gx, gy)
    zz = kde(np.vstack([xx.ravel(), yy.ravel()])).reshape(grid, grid)
    # normalise to [0, 1] so the renderer can scale comfortably
    zmax = float(zz.max()) or 1.0
    zz = zz / zmax

    return {
        "type": "surface",
        "title": title or f"KDE({x}, {y})",
        "axes": {"x": x, "y": y, "z": "density"},
        "grid": grid,
        "x_extent": [round(xmin, 4), round(xmax, 4)],
        "y_extent": [round(ymin, 4), round(ymax, 4)],
        "z": [[round(float(v), 5) for v in row] for row in zz.tolist()],
    }


# ---------------------------------------------------------------------------
# 3. corr_field  →  Field payload (new outbound type)
# ---------------------------------------------------------------------------

def corr_field(df: pd.DataFrame, *, title: str = "Correlation field") -> dict[str, Any]:
    """Full correlation matrix as a 'field' payload.

    Returns dict shape (new outbound type 'field', see contracts.Field):
        { "type":"field", "title":..., "labels":[col,...],
          "values":[[...]], "range":[-1,1] }
    """
    num = df.select_dtypes(include="number")
    if num.shape[1] < 2:
        raise ValueError("corr_field needs ≥2 numeric columns")
    corr = num.corr(numeric_only=True)
    labels = [str(c) for c in corr.columns]
    values = [[round(float(v), 4) for v in row] for row in corr.to_numpy().tolist()]
    return {
        "type": "field",
        "title": title,
        "labels": labels,
        "values": values,
        "range": [-1.0, 1.0],
    }
