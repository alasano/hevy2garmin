"""Garmin login rate-limit cooldown with exponential backoff.

When Garmin rate-limits a login (HTTP 429), retrying resets and deepens the timer
on Garmin's side, so we enforce a local cooldown: record when it happened, block
further login attempts until the window passes, and back off exponentially on
repeat hits. State lives in the ``app_config`` key-value store so it survives
restarts. Reset to the base window after a clean login.

All functions take a ``db`` (a Database instance exposing ``get_app_config`` /
``set_app_config``) and are best-effort: a storage failure never raises.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from hevy2garmin._isotime import parse_iso

logger = logging.getLogger("hevy2garmin")

_KEY = "garmin_ratelimit"
_BASE_SECONDS = 2 * 3600      # first hit: 2 hours
_MAX_SECONDS = 24 * 3600      # cap at 24 hours


def _now() -> datetime:
    return datetime.now(timezone.utc)


def record_rate_limit(db) -> int:
    """Record a Garmin rate-limit hit and enter (or extend) the cooldown with
    exponential backoff (2h, 4h, 8h, ... capped at 24h). Returns the cooldown
    length in seconds."""
    try:
        prev = db.get_app_config(_KEY) or {}
    except Exception:
        prev = {}
    hits = int(prev.get("hits", 0)) + 1
    seconds = min(_BASE_SECONDS * (2 ** (hits - 1)), _MAX_SECONDS)
    until = _now() + timedelta(seconds=seconds)
    try:
        db.set_app_config(_KEY, {"until": until.isoformat(), "hits": hits, "seconds": seconds})
    except Exception:
        logger.debug("could not persist rate-limit cooldown", exc_info=True)
    logger.warning("Garmin rate-limited (hit #%d); cooling down for %d min", hits, seconds // 60)
    return seconds


def cooldown_remaining(db) -> int:
    """Seconds remaining in the cooldown, or 0 if not currently cooling down."""
    try:
        state = db.get_app_config(_KEY)
    except Exception:
        return 0
    if not state or not state.get("until"):
        return 0
    try:
        until = parse_iso(state["until"])
    except Exception:
        return 0
    remaining = (until - _now()).total_seconds()
    return int(remaining) if remaining > 0 else 0


def clear_rate_limit(db) -> None:
    """Clear the cooldown after a successful Garmin login (resets the backoff)."""
    try:
        db.set_app_config(_KEY, {"until": None, "hits": 0, "seconds": 0})
    except Exception:
        logger.debug("could not clear rate-limit cooldown", exc_info=True)


def format_cooldown(seconds: int) -> str:
    """Human-readable cooldown duration, e.g. 'about 1h 45m' or 'about 5m'."""
    minutes = int(seconds) // 60
    hours = minutes // 60
    mins = minutes % 60
    if hours > 0:
        return f"about {hours}h {mins}m" if mins > 0 else f"about {hours}h"
    return f"about {mins}m"
