"""
Ouroboros — Global configuration layer.

Single source of truth for defaults and env var injection.
Call apply_settings_to_env() early in startup to ensure all env vars are set.
"""

from __future__ import annotations

import os
from typing import Optional

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULTS: dict = {
    # MiniMax
    "MINIMAX_BASE_URL": "https://api.minimaxi.com/anthropic",
    "MINIMAX_API_KEY": "",
    # Primary models
    "OUROBOROS_MODEL": "minimax/MiniMax-M2.5",
    "OUROBOROS_MODEL_CODE": "anthropic/claude-sonnet-4.6",
    "OUROBOROS_FALLBACK_MODEL": "anthropic/claude-sonnet-4.6",
}


def get(name: str, fallback: str = "") -> str:
    """Return env var value, falling back to DEFAULTS, then fallback."""
    return os.environ.get(name) or DEFAULTS.get(name) or fallback


def apply_settings_to_env(overrides: Optional[dict] = None) -> None:
    """
    Inject config defaults into os.environ (only if not already set).
    Call this once at startup, before importing llm.py or agent.py.

    Args:
        overrides: Optional dict of values to force-set (overrides existing env).
    """
    for key, default_val in DEFAULTS.items():
        if key not in os.environ and default_val:
            os.environ[key] = default_val

    if overrides:
        for key, val in overrides.items():
            if val:
                os.environ[key] = val
