#!/usr/bin/env python3
"""Deterministically generate data/replay_run_history.json.

Produces three runs with distinct failure modes so the training-monitor
agent has something to chew on offline:

  demo-overfit-001    val_loss bottoms ~ epoch 12 then climbs; classic overfit
  demo-healthy-002    val_loss decreases monotonically, plateaus near epoch 25
  demo-leakage-003    val_loss tracks train_loss almost perfectly (suspicious)

Schema (frozen — coordinated with Person C, see
backend/BROADCAST_NOTE_TRAINING.md):

    {
      "<run_id>": {
        "config":  {model, lr, batch_size, epochs, ...},
        "metrics": [{step, epoch, train_loss, val_loss,
                     train_acc, val_acc}, ...],
        "summary": {final_train_loss, final_val_loss,
                    best_val_loss, best_val_loss_step}
      },
      ...
    }
"""
from __future__ import annotations

import json
import math
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "replay_run_history.json"

STEPS_PER_EPOCH = 20
EPOCHS = 30
TOTAL_STEPS = STEPS_PER_EPOCH * EPOCHS


def _build_run(run_id: str, mode: str, seed: int) -> dict:
    rng = random.Random(seed)
    metrics = []
    for step in range(TOTAL_STEPS):
        epoch = step // STEPS_PER_EPOCH
        t = step / TOTAL_STEPS  # 0..1

        if mode == "overfit":
            # train loss decays smoothly to ~0.05
            train_loss = 2.3 * math.exp(-3.0 * t) + 0.05 + rng.gauss(0, 0.02)
            # val loss bottoms around epoch 12 (t≈0.4) then climbs
            min_t = 0.40
            val_loss = 1.6 * math.exp(-3.5 * t) + 0.35 + 1.4 * max(0, t - min_t) ** 2 \
                + rng.gauss(0, 0.025)
            train_acc = 1.0 - train_loss / 2.5
            val_acc = 0.95 - 1.2 * max(0, t - min_t) ** 1.5 + rng.gauss(0, 0.005)
            val_acc = max(0.45, min(0.95, val_acc))

        elif mode == "healthy":
            train_loss = 2.2 * math.exp(-2.5 * t) + 0.10 + rng.gauss(0, 0.015)
            val_loss = 2.0 * math.exp(-2.2 * t) + 0.18 + rng.gauss(0, 0.02)
            train_acc = 1.0 - train_loss / 2.5
            val_acc = 1.0 - val_loss / 2.5

        else:  # leakage
            # val == train + tiny noise — too good to be true
            train_loss = 2.0 * math.exp(-2.6 * t) + 0.08 + rng.gauss(0, 0.02)
            val_loss = train_loss - 0.02 + rng.gauss(0, 0.01)  # often BELOW train
            train_acc = 1.0 - train_loss / 2.5
            val_acc = 1.0 - val_loss / 2.5

        metrics.append({
            "step": step,
            "epoch": epoch,
            "train_loss": round(train_loss, 4),
            "val_loss": round(val_loss, 4),
            "train_acc": round(max(0.0, min(1.0, train_acc)), 4),
            "val_acc": round(max(0.0, min(1.0, val_acc)), 4),
        })

    best_val = min(metrics, key=lambda m: m["val_loss"])
    summary = {
        "final_train_loss": metrics[-1]["train_loss"],
        "final_val_loss": metrics[-1]["val_loss"],
        "best_val_loss": best_val["val_loss"],
        "best_val_loss_step": best_val["step"],
        "best_val_loss_epoch": best_val["epoch"],
    }

    config = {
        "model": "resnet18" if mode != "leakage" else "resnet18-leaky",
        "dataset": "synthetic-classification",
        "lr": 1e-3,
        "batch_size": 32,
        "epochs": EPOCHS,
        "steps_per_epoch": STEPS_PER_EPOCH,
        "mode_label": mode,
    }
    return {"run_id": run_id, "config": config, "metrics": metrics, "summary": summary}


def main() -> None:
    out = {
        "demo-overfit-001":  _build_run("demo-overfit-001",  "overfit",  seed=1),
        "demo-healthy-002":  _build_run("demo-healthy-002",  "healthy",  seed=2),
        "demo-leakage-003":  _build_run("demo-leakage-003",  "leakage",  seed=3),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2))
    for rid, r in out.items():
        s = r["summary"]
        print(f"  {rid}  best_val={s['best_val_loss']:.3f} @ step {s['best_val_loss_step']}  "
              f"final_val={s['final_val_loss']:.3f}")
    print(f"wrote {OUT}  ({OUT.stat().st_size/1024:.1f} KB)")


if __name__ == "__main__":
    main()
