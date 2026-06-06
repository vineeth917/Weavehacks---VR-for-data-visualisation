# Broadcast — `replay_run_history.json` schema (Person A → Person C)

Person A (backend) shipping the training-monitor phase. Locking the
replay-run-history schema now so the dashboard's training-curve panel can
consume it directly. **Please sign off on the schema below — speak up if you
need a field added.** Schema lives at `data/replay_run_history.json`.

## Top-level shape

```jsonc
{
  "<run_id>": {                       // string — unique per run
    "config": {
      "model":           "resnet18",
      "dataset":         "synthetic-classification",
      "lr":              0.001,
      "batch_size":      32,
      "epochs":          30,
      "steps_per_epoch": 20,
      "mode_label":      "overfit"    // overfit | healthy | leakage
                                      //   (only set in the demo replay;
                                      //   real W&B runs won't have this)
    },
    "metrics": [
      { "step": 0, "epoch": 0,
        "train_loss": 2.3144, "val_loss": 2.2837,
        "train_acc": 0.0742, "val_acc": 0.0865 },
      { "step": 1, ... },
      ...
    ],
    "summary": {
      "final_train_loss":     0.0764,
      "final_val_loss":       0.9292,
      "best_val_loss":        0.5388,
      "best_val_loss_step":   350,
      "best_val_loss_epoch":  17
    }
  },
  ...
}
```

## Field guarantees

- `step` is a strictly increasing integer per run starting at 0.
- `epoch = step // steps_per_epoch` (already pre-computed for you).
- The four float metrics (`train_loss`, `val_loss`, `train_acc`, `val_acc`)
  are **always present** on every metric row. If a future replay adds custom
  metrics (e.g. `f1`, `auc`), they will appear as additional optional keys —
  unknown metric keys must be safely ignored, not crash the renderer.
- `summary` is **always present**, but Person C should still compute
  best/final values on the fly if a downstream live-streamed run is being
  rendered before its `summary` block exists.

## Demo runs that ship today

| run_id              | mode     | what to expect                              |
|---------------------|----------|---------------------------------------------|
| `demo-overfit-001`  | overfit  | val bottoms ~ step 350 (epoch 17) then rises; train keeps falling |
| `demo-healthy-002`  | healthy  | val + train decrease together; val plateaus near epoch 29 |
| `demo-leakage-003`  | leakage  | val **below** train consistently — suspicious / leak signal |

These were produced by `backend/scripts/gen_replay_run_history.py`
(deterministic seeds — `python backend/scripts/gen_replay_run_history.py`
will regenerate byte-identical files).

## Live W&B mode (when `WANDB_API_KEY` set + `run_id` is `entity/project/run`)

`backend/tools/wandb_history.py` falls through to `wandb.Api().run(run_id)`
and shapes its output to the **same** schema above. So the dashboard only
needs to handle one shape.

## How the training-monitor agent uses it

The agent calls `get_run_history(run_id)` once, then `analyze_curve(metrics)`,
then synthesizes a verdict citing the actual numerical values (e.g. *"val_loss
bottoms at 0.54 around epoch 17 and rises to 0.93 by epoch 30 — overfitting"*).
The same numbers are sent over the wire as a `training_update` message and
embedded in the `training-verdict` A2UI surface heading/reason.
