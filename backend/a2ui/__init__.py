"""A2UI v0.8 envelope helpers.

See ./SPEC_NOTES.md for the full rationale (we ship v0.8 envelope names per the
user's spec; CopilotKit's A2UIMiddleware accepts both).
"""
from __future__ import annotations

SPEC_VERSION = "0.8"  # flip to "0.9" if Person C's renderer is v0.9-only
DEFAULT_CATALOG = "basic"

# Frozen action strings. Any change is a stop-and-broadcast event.
ACTIONS = frozenset({
    "confirm_transform",
    "dismiss",
    "stop_training",
    "keep_training",
})

# Frozen surface IDs.
SURFACE_IDS = frozenset({
    "eda-findings",
    "eda-action",
    "training-verdict",
})
