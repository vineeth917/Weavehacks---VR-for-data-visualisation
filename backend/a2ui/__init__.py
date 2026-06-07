"""A2UI v0.8 envelope helpers.

See ./SPEC_NOTES.md for the full rationale (we ship v0.8 envelope names per the
user's spec; CopilotKit's A2UIMiddleware accepts both).
"""
from __future__ import annotations

SPEC_VERSION = "0.8"  # flip to "0.9" if Person C's renderer is v0.9-only
DEFAULT_CATALOG = "basic"

# Button action wire shape. Controlled by env var A2UI_BUTTON_MODE.
#   "v08"  → flat:   {action: "<name>", context: {contents: [...]}}
#   "v09"  → nested: {action: {event: {name: "<name>", context: {literal map}}}}
# Person C's @copilotkit/a2ui-renderer v1.59.5 reads v0.8 buttons correctly;
# if her v0.9 renderer dispatches via the canonical Action schema we should
# flip to "v09". Default stays "v08" because that's what's tested green.
import os
BUTTON_MODE = os.getenv("A2UI_BUTTON_MODE", "v08").lower()
if BUTTON_MODE not in ("v08", "v09"):
    BUTTON_MODE = "v08"

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
