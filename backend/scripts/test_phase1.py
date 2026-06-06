#!/usr/bin/env python3
"""Phase-1 test: profiling.py + plots.py + a real panels payload.

Assertions:
  1. price/income flagged right_skewed
  2. balance flagged left_skewed
  3. notes flagged heavy_missing
  4. tx_amount flagged outliers
  5. flag_v1 flagged near_constant
  6. pca1/pca2 in top correlations
  7. histogram(price) renders to a PNG ≤80 KB
  8. build_panel returns a dict matching contracts.Panel
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.contracts import Panel, Panels  # noqa: E402
from backend.tools.plots import build_panel, render_plot  # noqa: E402
from backend.tools.profiling import flag_columns, profile_dataset  # noqa: E402

CSV = ROOT / "data" / "sample.csv"
if not CSV.exists():
    print(f"ERROR: missing {CSV} — run scripts/gen_sample_csv.py first", file=sys.stderr)
    sys.exit(2)


def passed(msg: str) -> None: print(f"  PASS  {msg}")
def failed(msg: str) -> None: print(f"  FAIL  {msg}"); FAILURES.append(msg)


FAILURES: list[str] = []

print("=== loading", CSV.name, "===")
df = pd.read_csv(CSV)
print(f"  shape={df.shape}")

# ---- profile_dataset ----
t0 = time.time()
prof = profile_dataset(df, top_corr=5)
dt = time.time() - t0
print(f"\n=== profile_dataset ({dt*1000:.1f} ms) ===")
print(f"  n_rows={prof['n_rows']} n_cols={prof['n_cols']}")
for c in prof["columns"]:
    sk = c.get("skew")
    print(f"  - {c['name']:<10} dtype={c['dtype']:<8} miss={c['missing_pct']:>5.1f}%  "
          f"skew={sk if sk is None else f'{sk:>6.2f}'}  "
          f"out%={c.get('outlier_pct', 0):>5.1f}  flags={c['flags']}  plot={c.get('plot')}")
print(f"  top_correlations={prof['top_correlations']}")
print(f"  notes={prof['notes']}")

# ---- assertions ----
print("\n=== assertions ===")
right = set(flag_columns(prof, "right_skewed"))
left  = set(flag_columns(prof, "left_skewed"))
miss  = set(flag_columns(prof, "heavy_missing"))
outl  = set(flag_columns(prof, "outliers"))
near  = set(flag_columns(prof, "near_constant"))

(passed if {"price", "income"} <= right else failed)(
    f"right_skewed contains price+income (got {right})"
)
(passed if "balance" in left else failed)(
    f"left_skewed contains balance (got {left})"
)
(passed if "notes" in miss else failed)(
    f"heavy_missing contains notes (got {miss})"
)
(passed if "tx_amount" in outl else failed)(
    f"outliers contains tx_amount (got {outl})"
)
(passed if "flag_v1" in near else failed)(
    f"near_constant contains flag_v1 (got {near})"
)
# Regression: category (4 evenly distributed classes) must NOT be near_constant
(passed if "category" not in near else failed)(
    f"near_constant does NOT contain category (got {near})"
)
corr_pairs = {(p["a"], p["b"]) for p in prof["top_correlations"]} | \
             {(p["b"], p["a"]) for p in prof["top_correlations"]}
(passed if ("pca1", "pca2") in corr_pairs else failed)(
    f"top_correlations contains pca1×pca2 (got {prof['top_correlations']})"
)

# ---- plot rendering ----
print("\n=== render_plot ===")
for col, kind in [("price", "histogram"), ("balance", "kde"), ("tx_amount", "box")]:
    t0 = time.time()
    b64, size = render_plot(df, col, kind)
    dt = (time.time() - t0) * 1000
    kb = size / 1024
    status = "PASS" if size <= 80 * 1024 else "FAIL"
    print(f"  {status}  {kind:<10} of {col:<10}  {kb:6.1f} KB  ({dt:.0f} ms)")
    if size > 80 * 1024:
        failed(f"{kind}({col}) exceeded 80KB: {kb:.1f}KB")

# also exercise dataset-wide plots
for kind in ("corr", "missing"):
    t0 = time.time()
    b64, size = render_plot(df, None, kind)
    dt = (time.time() - t0) * 1000
    print(f"  PASS  {kind:<10} of <dataset>  {size/1024:6.1f} KB  ({dt:.0f} ms)")

# ---- build_panel returns a contracts.Panel-shaped dict ----
print("\n=== build_panel ↔ contracts.Panel ===")
panel = build_panel(df, "price", "histogram",
                    flags=["right_skewed", "outliers"],
                    position_hint="left")
panel_clean = {k: v for k, v in panel.items() if not k.startswith("_")}
try:
    Panel.model_validate(panel_clean)
    passed("Panel validates against contracts.Panel")
except Exception as e:
    failed(f"Panel validation: {e}")

# Full Panels payload (what we'd send over /ws)
panels_payload = Panels(panels=[
    Panel.model_validate(panel_clean),
    Panel.model_validate({k: v for k, v in build_panel(df, "balance", "kde",
                                                      flags=["left_skewed"],
                                                      position_hint="center").items()
                          if not k.startswith("_")}),
    Panel.model_validate({k: v for k, v in build_panel(df, "tx_amount", "box",
                                                      flags=["outliers"],
                                                      position_hint="right").items()
                          if not k.startswith("_")}),
])

# Print a redacted version (truncate image_b64) so the dev can see the shape
serialized = panels_payload.model_dump()
for p in serialized["panels"]:
    full = p["image_b64"]
    p["image_b64"] = f"<{len(full)//1024} KB png ({full[:24]}…)>"
print(json.dumps(serialized, indent=2))

# ---- exit ----
print("\n=== summary ===")
if FAILURES:
    print(f"FAILED ({len(FAILURES)}):")
    for f_ in FAILURES:
        print("  -", f_)
    sys.exit(1)
print("ALL GREEN")
