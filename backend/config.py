"""Centralised runtime config. Loads .env from repo root."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env", override=False)


def _req(name: str) -> str:
    v = os.environ.get(name, "").strip()
    if not v:
        raise RuntimeError(f"Missing required env var: {name}")
    return v


# --- secrets ---
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
WANDB_API_KEY = os.environ.get("WANDB_API_KEY", "").strip()

# --- endpoints ---
WANDB_INFERENCE_BASE = os.environ.get(
    "WANDB_INFERENCE_BASE", "https://api.inference.wandb.ai/v1"
).rstrip("/")
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")

# --- weave / wandb ---
WANDB_PROJECT = os.environ.get("WANDB_PROJECT", "hololab")
WANDB_ENTITY = os.environ.get("WANDB_ENTITY", "").strip()  # optional override

# --- model routing (picked from bench: see /tmp/bench_models.py) ---
ROUTER_MODEL = os.environ.get(
    "ROUTER_MODEL", "Qwen/Qwen3-235B-A22B-Instruct-2507"
)
REASONING_MODEL = os.environ.get(
    "REASONING_MODEL", "Qwen/Qwen3-235B-A22B-Instruct-2507"
)
DEEP_REASONING_MODEL = os.environ.get(
    "DEEP_REASONING_MODEL", "nvidia/NVIDIA-Nemotron-3-Ultra-550B-A55B"
)
FALLBACK_MODEL = os.environ.get("FALLBACK_MODEL", "gpt-4o-mini")

# --- runtime ---
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8080"))

# --- safety flags ---
USE_WEAVE = os.environ.get("USE_WEAVE", "1") not in ("0", "false", "False", "")
ENABLE_PREPROCESSOR = os.environ.get("ENABLE_PREPROCESSOR", "") in ("1", "true", "True")
ENABLE_EVALS = os.environ.get("ENABLE_EVALS", "") in ("1", "true", "True")
ENABLE_TRAINER = os.environ.get("ENABLE_TRAINER", "") in ("1", "true", "True")


def weave_project_full() -> str:
    """Return `entity/project` if entity set, else just project."""
    return f"{WANDB_ENTITY}/{WANDB_PROJECT}" if WANDB_ENTITY else WANDB_PROJECT


def assert_keys() -> None:
    """Fail loud at startup if mandatory keys are missing."""
    _req("WANDB_API_KEY")
    # OPENAI optional (fallback only); warn but don't fail.
