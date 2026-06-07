# HoloLab demo — morning launch

## Server (all flagged agents ON)

```bash
cd /path/to/hacksweave
source .venv/bin/activate
set -a && source .env && set +a

ENABLE_PREPROCESSOR=1 ENABLE_EVALS=1 ENABLE_TRAINER=1 \
  uvicorn backend.main:app --host 0.0.0.0 --port 8080
```

Dev UI: http://127.0.0.1:8080/dev-ui

## Full demo sequence (use **titanic** — best numbers)

One session, in order:

1. **load titanic**
2. **what problem are we solving?** → problem_type
3. **show me the data — which columns are skewed or missing?** → eda
4. **remove nulls and duplicates** → preprocessor (v0→v1)
5. **is my data ready to train?** → preprocessor readiness
6. **train the model** → trainer (50× `training_update`, W&B run)
7. **is my model overfitting?** → training_monitor (reads real trainer run)
8. **run the evals** → evals (~0.78 accuracy on titanic)
9. **wrap up — narrate what we found** → narrator

## Flags (default off — today’s behavior unchanged without them)

| Flag | Agent |
|------|--------|
| `ENABLE_PREPROCESSOR=1` | versioned clean + readiness |
| `ENABLE_EVALS=1` | real sklearn test metrics |
| `ENABLE_TRAINER=1` | iterative SGD + W&B logging |

## Quick tests

```bash
ENABLE_PREPROCESSOR=1 ENABLE_EVALS=1 ENABLE_TRAINER=1 python backend/scripts/test_trainer.py
```
