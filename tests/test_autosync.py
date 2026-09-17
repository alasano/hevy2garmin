"""Tests for auto-sync helpers in server.py."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock, patch

from hevy2garmin import server
from hevy2garmin.server import _acquire_sync_lock, _sync_executing


class TestSyncLock:
    def test_acquire_and_release(self) -> None:
        """Lock can be acquired and released without crashing (verifies time module is imported)."""
        assert _acquire_sync_lock() is True
        _sync_executing.release()

    def test_acquire_blocks_second(self) -> None:
        """Second acquire returns False when lock is held."""
        assert _acquire_sync_lock() is True
        assert _acquire_sync_lock() is False  # Already held
        _sync_executing.release()


class TestCronGraceDeferral:
    def test_all_fresh_workouts_are_deferred_without_calling_sync_helper(self) -> None:
        """Cron returns a useful response when every candidate is in grace."""
        workout = {"id": "fresh-1", "title": "Fresh", "exercises": []}
        hevy = MagicMock()
        hevy.get_workout_count.return_value = 1
        database = MagicMock()

        with (
            patch.object(
                server,
                "load_config",
                return_value={
                    "hevy_api_key": "test-key",
                    "sync": {"grace_period_minutes": 120},
                },
            ),
            patch("hevy2garmin.hevy.HevyClient", return_value=hevy),
            patch.object(server.db, "get_db", return_value=database),
            patch.object(server.db, "get_synced_count", return_value=0),
            patch.object(
                server,
                "_scan_for_unsynced",
                side_effect=[(workout, {}), (None, {})],
            ),
            patch("hevy2garmin.sync._workout_within_grace", return_value=True),
            patch("hevy2garmin.sync.sync_one_workout") as sync_one,
        ):
            response = asyncio.run(server._do_sync_one(respect_grace=True))

        assert json.loads(response.body) == {
            "synced": 0,
            "deferred": 1,
            "remaining": 1,
            "done": False,
        }
        sync_one.assert_not_called()
