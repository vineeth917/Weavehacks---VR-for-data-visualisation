"""Defensive JSON extraction for agent final outputs.

Qwen on CoreWeave (and most chat-tuned models) reliably emit JSON when asked
in the prompt, but they sometimes wrap it in ```json ... ``` fences, sometimes
prefix with prose, sometimes append a trailing sentence. This module gives
every agent the same tolerant parser so we don't reinvent it in three places.
"""
from __future__ import annotations

import json
import re
from typing import Any

_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*(\{.*?\})\s*```", re.DOTALL)


def parse_json_object(raw: str) -> dict[str, Any] | None:
    """Try hard to find a JSON object inside `raw`. Returns dict or None.

    Order of attempts:
      1. fenced ```json {...} ```
      2. whole text
      3. brace-trimmed slice (first '{' through last '}')
      4. last-resort: each fenced block in the text, returning the first dict
    """
    if not raw:
        return None
    text = raw.strip()
    candidates: list[str] = []
    m = _FENCE_RE.search(text)
    if m:
        candidates.append(m.group(1))
    candidates.append(text)
    if "{" in text and "}" in text:
        candidates.append(text[text.find("{"): text.rfind("}") + 1])
    candidates.extend(m2.group(1) for m2 in _FENCE_RE.finditer(text))
    for c in candidates:
        try:
            obj = json.loads(c)
        except Exception:  # noqa: BLE001
            continue
        if isinstance(obj, dict):
            return obj
    return None
