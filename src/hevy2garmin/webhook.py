"""Durable, workout-specific webhook processing for the single server worker."""

from __future__ import annotations

import logging
import time
from uuid import UUID

from garminconnect import GarminConnectAuthenticationError

from hevy2garmin.hevy import HevyAuthError, HevyClient
from hevy2garmin.garmin import get_client
from hevy2garmin.merge import reset_circuit_breaker
from hevy2garmin.sync import sync_one_workout

logger = logging.getLogger("hevy2garmin")


def workout_id_from_payload(body: object) -> str:
    """Read the workoutId field documented in Hevy's developer settings."""
    if not isinstance(body, dict):
        raise ValueError("Expected workoutId")
    value = body.get("workoutId")
    if not isinstance(value, str):
        raise ValueError("Expected workoutId to be a UUID")
    try:
        return str(UUID(value))
    except ValueError:
        raise ValueError("Expected workoutId to be a UUID") from None


def _terminal_error(exc: Exception) -> bool:
    """Inspect the original error when merge wraps an API failure."""
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc, (HevyAuthError, GarminConnectAuthenticationError, ValueError)):
            return True
        if getattr(getattr(exc, "response", None), "status_code", None) in (401, 403):
            return True
        exc = exc.__cause__ or exc.__context__
    return False


def process_job(store, job: dict, config: dict, *, retry_seconds: int, max_attempts: int) -> None:
    """Execute one due job with the server sync lock held.

    Keep the persisted job pending until its outcome is saved. If the process
    stops mid-attempt, startup retries it and checks the sync ledger first.
    """
    wid = job["hevy_id"]
    attempts = job["attempts"] + 1
    status, error = "pending", None
    synced = False
    try:
        if store.is_synced(wid):
            status = "completed"
        elif store.get_pending(wid):
            # An existing upload owns this workout. Its reconciliation path
            # decides the outcome; the webhook must never start a second one.
            status = "handed_off"
        else:
            if not config.get("hevy_api_key"):
                raise ValueError("Hevy API key not configured")
            if not config.get("merge_mode", True):
                raise ValueError("Enable merge mode to process webhook workouts")
            workout = HevyClient(api_key=config["hevy_api_key"]).get_workout(wid)
            if workout is None:
                error = "Workout not available in Hevy yet"
            elif workout.get("id") != wid:
                raise ValueError("Hevy returned a different workout ID")
            else:
                reset_circuit_breaker()
                one = sync_one_workout(
                    workout, cfg=config, garmin_client=get_client(config.get("garmin_email")),
                    merge_only=True, respect_grace=False, database=store,
                )
                if one.status == "synced":
                    status, synced = "completed", True
                elif one.status == "merge_pending":
                    error = "Waiting for matching Garmin activity"
                elif one.status in ("processing", "needs_review", "failed"):
                    status = "handed_off"
                    error = f"Existing upload requires reconciliation: {one.status}"
                else:
                    error = f"Sync did not complete: {one.status}"
    except Exception as exc:
        status = "failed" if _terminal_error(exc) else "pending"
        error = str(exc)[:500]
    if status == "pending" and attempts >= max_attempts:
        status = "exhausted"
    store.save_webhook(wid, status, attempts, time.time() + retry_seconds, error)
    logger.info("Webhook workout %s: %s (attempt %d/%d)%s", wid, status,
                attempts, max_attempts, f": {error}" if error else "")
    if synced or status in ("failed", "exhausted"):
        store.record_sync_log(synced=int(synced), failed=int(not synced), trigger="webhook")
