"""Fetch a W&B run's history. Falls back to a local replay file when offline
or when the run isn't reachable. Schema is documented in
backend/BROADCAST_NOTE_TRAINING.md.

Wire shape returned:
    {
      "run_id":  str,
      "source":  "wandb_api" | "replay",
      "config":  {...},
      "metrics": [ {step, epoch, train_loss, val_loss, train_acc, val_acc, ...}, ... ],
      "summary": {final_train_loss, final_val_loss, best_val_loss,
                  best_val_loss_step, best_val_loss_epoch},
      "replay_path": str  # only when source == "replay"
    }
"""
from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

import weave

from backend import config

log = logging.getLogger("hololab.tools.wandb_history")

REPO_ROOT = Path(__file__).resolve().parents[2]
REPLAY_DEFAULT = REPO_ROOT / "data" / "replay_run_history.json"


def _summarize(metrics: list[dict[str, Any]]) -> dict[str, Any]:
    if not metrics:
        return {}
    best = min(metrics, key=lambda m: m.get("val_loss", float("inf")))
    return {
        "final_train_loss": metrics[-1].get("train_loss"),
        "final_val_loss":   metrics[-1].get("val_loss"),
        "best_val_loss":    best.get("val_loss"),
        "best_val_loss_step":  best.get("step"),
        "best_val_loss_epoch": best.get("epoch"),
    }


@lru_cache(maxsize=1)
def _load_replay(replay_path: str) -> dict[str, Any]:
    p = Path(replay_path)
    if not p.exists():
        raise FileNotFoundError(
            f"replay file missing at {p} — run "
            "`python backend/scripts/gen_replay_run_history.py`"
        )
    return json.loads(p.read_text())


def _from_replay(run_id: str, replay_path: Path) -> dict[str, Any]:
    data = _load_replay(str(replay_path))
    # support {run_id: run, ...} OR a single-run object at the top level
    if "metrics" in data and "config" in data:
        body = data
        actual_id = data.get("run_id", run_id)
    elif run_id in data:
        body = data[run_id]
        actual_id = run_id
    else:
        actual_id = next(iter(data))
        body = data[actual_id]
        log.info("run_id %r not in replay; defaulting to %r", run_id, actual_id)
    return {
        "run_id":  actual_id,
        "source":  "replay",
        "config":  body.get("config", {}),
        "metrics": body.get("metrics", []),
        "summary": body.get("summary") or _summarize(body.get("metrics", [])),
        "replay_path": str(replay_path),
    }


def _from_wandb(run_id: str) -> dict[str, Any]:
    """Fetch live from W&B; run_id must be 'entity/project/run'."""
    import wandb  # local import — wandb already a dep
    api = wandb.Api()
    run = api.run(run_id)
    rows = list(run.scan_history())
    metrics: list[dict[str, Any]] = []
    for i, row in enumerate(rows):
        m = {
            "step":  row.get("_step", i),
            "epoch": row.get("epoch", row.get("_step", i)),
        }
        for k in ("train_loss", "val_loss", "train_acc", "val_acc"):
            v = row.get(k)
            if v is None:
                # also accept common aliases
                v = row.get({"train_loss": "loss",
                             "val_loss":   "val/loss",
                             "train_acc":  "accuracy",
                             "val_acc":    "val/accuracy"}.get(k))
            if isinstance(v, (int, float)):
                m[k] = float(v)
        metrics.append(m)
    return {
        "run_id":  run_id,
        "source":  "wandb_api",
        "config":  dict(run.config),
        "metrics": metrics,
        "summary": _summarize(metrics),
    }


@weave.op()
def get_run_history(run_id: str,
                    *,
                    replay_path: Path | None = None,
                    force_replay: bool = False) -> dict[str, Any]:
    """Return history for `run_id`.

    Tries the W&B API when `run_id` looks like `entity/project/run` and a
    `WANDB_API_KEY` is configured; otherwise (or on any error) falls back to
    the local replay file at `data/replay_run_history.json`.
    """
    path = replay_path or REPLAY_DEFAULT
    if force_replay:
        return _from_replay(run_id, path)
    if "/" in run_id and config.WANDB_API_KEY:
        try:
            return _from_wandb(run_id)
        except Exception as e:  # noqa: BLE001
            log.warning("wandb fetch failed for %s (%s) — using replay",
                        run_id, type(e).__name__)
    return _from_replay(run_id, path)


# ---------------------------------------------------------------------------
# Pure numerical analyzer used by the training-monitor agent
# ---------------------------------------------------------------------------

@weave.op()
def analyze_curve(metrics: list[dict[str, Any]],
                  *,
                  patience_epochs: int = 3) -> dict[str, Any]:
    """Compute overfitting / early-stop / leakage signals from a metric list.

    Returns a dict the LLM can quote in its verdict. We do the math; the LLM
    only narrates and decides which surface buttons to highlight.

        {
          "verdict": "overfitting" | "healthy" | "underfitting" | "leakage",
          "rationale": [str, ...],            # numerical bullet points
          "best_val_loss":          float,
          "best_val_loss_step":     int,
          "best_val_loss_epoch":    int,
          "final_train_loss":       float,
          "final_val_loss":         float,
          "val_minus_train_final":  float,    # +ve → val worse than train
          "early_stop_step":        int | None,
          "early_stop_epoch":       int | None,
          "n_points":               int
        }
    """
    if not metrics:
        return {"verdict": "unknown", "rationale": ["no metrics"], "n_points": 0}

    n = len(metrics)
    val_losses   = [m.get("val_loss")   for m in metrics if m.get("val_loss")   is not None]
    train_losses = [m.get("train_loss") for m in metrics if m.get("train_loss") is not None]

    best_idx = min(range(n), key=lambda i: metrics[i].get("val_loss", float("inf")))
    best = metrics[best_idx]
    last = metrics[-1]

    final_train = float(last.get("train_loss") or 0.0)
    final_val   = float(last.get("val_loss")   or 0.0)
    best_val    = float(best.get("val_loss")   or 0.0)
    gap         = final_val - final_train
    val_rise    = final_val - best_val
    rel_rise    = (val_rise / best_val) if best_val > 1e-9 else 0.0

    # leakage: val_loss <= train_loss for the bulk of the run.
    paired = [(m.get("train_loss"), m.get("val_loss")) for m in metrics
              if m.get("train_loss") is not None and m.get("val_loss") is not None]
    below = sum(1 for t, v in paired if v is not None and t is not None and v <= t)
    below_frac = below / max(1, len(paired))

    # early-stop / overfitting: did val stop improving "patience" epochs ago?
    epochs_seen = max(m.get("epoch", 0) for m in metrics)
    early_stop_step = early_stop_epoch = None
    if epochs_seen - best["epoch"] >= patience_epochs:
        early_stop_step  = best["step"]
        early_stop_epoch = best["epoch"]

    rationale: list[str] = [
        f"best val_loss = {best_val:.3f} at step {best['step']} (epoch {best['epoch']})",
        f"final train_loss = {final_train:.3f}, final val_loss = {final_val:.3f}",
        f"val − train gap at final step = {gap:+.3f}",
        f"val_loss rose by {val_rise:+.3f} ({rel_rise*100:+.1f}%) from best to final",
        f"val ≤ train on {below_frac*100:.0f}% of {len(paired)} paired steps",
    ]

    if below_frac >= 0.85 and final_val <= final_train:
        verdict = "leakage"
    elif rel_rise >= 0.20 and early_stop_step is not None:
        verdict = "overfitting"
    elif final_train > 0.6 and final_val > 0.6:
        verdict = "underfitting"
    else:
        verdict = "healthy"

    return {
        "verdict": verdict,
        "rationale": rationale,
        "best_val_loss":         round(best_val, 4),
        "best_val_loss_step":    int(best["step"]),
        "best_val_loss_epoch":   int(best["epoch"]),
        "final_train_loss":      round(final_train, 4),
        "final_val_loss":        round(final_val, 4),
        "val_minus_train_final": round(gap, 4),
        "early_stop_step":       early_stop_step,
        "early_stop_epoch":      early_stop_epoch,
        "below_frac":            round(below_frac, 3),
        "n_points":              n,
    }
