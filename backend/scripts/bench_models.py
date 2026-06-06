#!/usr/bin/env python3
"""Benchmark W&B CoreWeave inference models on latency + reasoning quality.

Tasks:
  T1 (router, fast):  classify utterance into {EDA, TRAINING, NARRATOR}
  T2 (reasoning):     verdict on a train/val loss series (overfitting?)

Pass criteria:
  T1 — output contains "EDA"
  T2 — output contains "overfitting"
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get("WANDB_INFERENCE_BASE", "https://api.inference.wandb.ai/v1")
KEY = os.environ.get("WANDB_API_KEY")
if not KEY:
    print("ERROR: set WANDB_API_KEY (and source .env)", file=sys.stderr)
    sys.exit(2)

CANDIDATES = [
    "openai/gpt-oss-20b",
    "Qwen/Qwen3-30B-A3B-Instruct-2507",
    "meta-llama/Llama-3.3-70B-Instruct",
    "microsoft/Phi-4-mini-instruct",
    "nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-FP8",
    "nvidia/NVIDIA-Nemotron-3-Ultra-550B-A55B",
    "Qwen/Qwen3-235B-A22B-Instruct-2507",
    "Qwen/Qwen3-235B-A22B-Thinking-2507",
    "deepseek-ai/DeepSeek-V4-Pro",
    "openai/gpt-oss-120b",
    "zai-org/GLM-5.1",
]

T1 = (
    "You are a router. Respond with ONLY one token from {EDA, TRAINING, NARRATOR}.",
    "User said: 'which columns are skewed and have outliers?' Which agent?",
    "EDA",
)
T2 = (
    "You are a strict ML reviewer. Reply with exactly: VERDICT=<overfitting|ok|underfitting>; REASON=<one short clause>.",
    "Train loss (step,loss): (10,0.80)(20,0.55)(30,0.40)(40,0.30)(50,0.22)(60,0.16)(70,0.12)(80,0.09)(90,0.07)(100,0.05).\n"
    "Val loss:               (10,0.82)(20,0.60)(30,0.50)(40,0.45)(50,0.44)(60,0.46)(70,0.50)(80,0.55)(90,0.62)(100,0.71).\nDecide.",
    "overfitting",
)


def call(model: str, system: str, user: str, max_tokens: int = 128):
    body = json.dumps({
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "max_tokens": max_tokens,
        "temperature": 0,
    }).encode()
    req = urllib.request.Request(
        f"{BASE}/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {KEY}",
            "Content-Type": "application/json",
            "User-Agent": "hololab-bench/1.0",
            "Accept": "application/json",
        },
        method="POST",
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read())
        dt = time.time() - t0
        msg = (data.get("choices") or [{}])[0].get("message", {}) or {}
        content = msg.get("content") or msg.get("reasoning_content") or ""
        toks = (data.get("usage") or {}).get("completion_tokens", 0)
        return dt, content.strip(), toks, None
    except urllib.error.HTTPError as e:
        return time.time() - t0, "", 0, f"HTTP {e.code}: {e.read()[:120].decode(errors='ignore')}"
    except Exception as e:  # noqa: BLE001
        return time.time() - t0, "", 0, f"ERR {type(e).__name__}: {str(e)[:120]}"


def row(*cells, widths=(46, 5, 5, 8, 70)):
    return "  ".join(str(c)[:w].ljust(w) for c, w in zip(cells, widths))


print(row("model", "task", "tok", "sec", "output / error"))
print("-" * 150)
agg: dict[str, dict] = {}
for m in CANDIDATES:
    for label, (sys_p, usr_p, expect) in (("T1", T1), ("T2", T2)):
        dt, out, toks, err = call(m, sys_p, usr_p, 64 if label == "T1" else 128)
        ok = (expect.lower() in out.lower()) if not err else False
        a = agg.setdefault(m, {"T1": False, "T2": False, "sec": 0.0, "tok": 0})
        a[label] = ok
        a["sec"] += dt
        a["tok"] += toks
        print(row(m, label, toks, f"{dt:.2f}", (err or out or "<empty>").replace("\n", " ⏎ ")))

print("\n=== SUMMARY (passed both T1 and T2) ===")
winners = sorted(
    [(m, a["sec"], a["tok"]) for m, a in agg.items() if a["T1"] and a["T2"]],
    key=lambda x: x[1],
)
for m, sec, tok in winners:
    print(f"  {sec:6.2f}s  {tok:4d}tok  {m}")
