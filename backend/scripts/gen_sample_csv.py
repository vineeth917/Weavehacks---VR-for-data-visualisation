#!/usr/bin/env python3
"""Generate data/sample.csv — small synthetic dataset with intentional issues.

Issues planted (the EDA test asserts these):
    price        : right-skewed (lognormal) + outliers
    income       : right-skewed (exponential)
    balance      : left-skewed (negated lognormal)
    age          : ~normal, no flags
    tx_amount    : heavy outliers (mixture)
    notes        : ~25% missing
    flag_v1      : near-constant (98% zero)
    pca1, pca2   : correlated ~0.9
    category     : categorical (low cardinality)
"""
import csv
from pathlib import Path

import numpy as np

OUT = Path(__file__).resolve().parents[2] / "data" / "sample.csv"
N = 500
rng = np.random.default_rng(7)

price       = rng.lognormal(mean=4.0, sigma=1.2, size=N)
income      = rng.exponential(scale=55_000, size=N)
balance_raw = rng.lognormal(mean=3.0, sigma=0.9, size=N)
balance     = balance_raw.max() - balance_raw  # left-skew
age         = np.clip(rng.normal(loc=40, scale=12, size=N), 18, 90).astype(int)

tx_normal   = rng.normal(loc=50, scale=8, size=N)
tx_outliers = rng.choice([0, 1], p=[0.92, 0.08], size=N) * rng.normal(loc=2_500, scale=400, size=N)
tx_amount   = tx_normal + tx_outliers

# pca1 / pca2: correlated pair (~0.9)
z1 = rng.normal(size=N)
z2 = 0.9 * z1 + 0.44 * rng.normal(size=N)

flag_v1     = np.where(rng.random(N) < 0.98, 0, 1)
category    = rng.choice(["A", "B", "C", "D"], p=[0.5, 0.3, 0.15, 0.05], size=N)
notes_text  = rng.choice(["lorem", "ipsum", "dolor", "amet", ""], p=[0.2, 0.2, 0.18, 0.17, 0.25], size=N)

# Inject ~25% missing into notes (in addition to the empty strings above)
notes_missing_idx = rng.choice(N, size=int(N * 0.25), replace=False)

OUT.parent.mkdir(parents=True, exist_ok=True)
with OUT.open("w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["price", "income", "balance", "age", "tx_amount",
                "pca1", "pca2", "flag_v1", "category", "notes"])
    for i in range(N):
        note = "" if i in notes_missing_idx else (notes_text[i] or "")
        w.writerow([
            round(float(price[i]), 2),
            round(float(income[i]), 2),
            round(float(balance[i]), 2),
            int(age[i]),
            round(float(tx_amount[i]), 2),
            round(float(z1[i]), 4),
            round(float(z2[i]), 4),
            int(flag_v1[i]),
            category[i],
            note,
        ])

print(f"wrote {OUT} ({N} rows)")
