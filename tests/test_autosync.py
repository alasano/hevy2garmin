"""Tests for _do_sync_one, the sync lock and the auto-sync helpers in server.py."""

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


class TestSyncOneAfterAnError:
    """`_failed_ids` lets one Sync Now session step past a workout that errored."""

    WORKOUT = {"id": "w-err", "title": "Push", "exercises": []}

    def _sync_one(self, *, scan, merge_only=False, sync_error=None, pending=()):
        hevy = MagicMock()
        hevy.get_workout_count.return_value = 1 + len(pending)
        database = MagicMock()
        database.list_pending.return_value = list(pending)
        with (
            patch.object(server, "load_config", return_value={"hevy_api_key": "k", "merge_mode": True}),
            patch("hevy2garmin.hevy.HevyClient", return_value=hevy),
            patch.object(server.db, "get_db", return_value=database),
            patch.object(server.db, "get_synced_count", return_value=0),
            patch.object(server, "_scan_for_unsynced", return_value=(scan, {})),
            patch("hevy2garmin.garmin.get_client", return_value=MagicMock()),
            patch("hevy2garmin.sync.sync_one_workout", side_effect=sync_error),
            patch.object(server, "_failed_ids", set()) as failed,
        ):
            response = asyncio.run(server._do_sync_one(merge_only=merge_only))
            return json.loads(response.body), set(failed)

    def test_a_failed_sync_now_skips_the_workout_for_the_session(self) -> None:
        data, failed = self._sync_one(scan=self.WORKOUT, sync_error=RuntimeError("Garmin unreachable"))
        assert data["skipped_error"] is True
        assert failed == {"w-err"}

    def test_a_failed_merge_only_poll_does_not_hide_the_workout(self) -> None:
        """The webhook's background poll must not take the workout away from Sync Now."""
        data, failed = self._sync_one(
            scan=self.WORKOUT, merge_only=True, sync_error=RuntimeError("Garmin unreachable")
        )
        assert data["skipped_error"] is True
        assert failed == set()

    def test_only_uploads_garmin_is_importing_count_as_processing(self) -> None:
        """A rejected upload or one needing review waits for the owner, not for Garmin."""
        pending = [
            {"hevy_id": "p1", "phase": "uploaded"},
            {"hevy_id": "p2", "phase": "failed"},
            {"hevy_id": "p3", "phase": "needs_review"},
        ]
        data, _ = self._sync_one(scan=None, pending=pending)
        assert data["processing"] == 1
        assert data["remaining"] == 4

    def test_nothing_to_work_on_ends_the_sync_now_loop(self) -> None:
        """One workout is unsynced but skipped: the dashboard loops while remaining > 0."""
        data, _ = self._sync_one(scan=None)
        assert data["remaining"] == 1
        assert data["done"] is True


class TestForceReleasedLock:
    """A sync that outlasts _SYNC_LOCK_TIMEOUT can lose its lock to another caller."""

    def _held_too_long(self):
        assert _acquire_sync_lock() is True
        return patch.object(server, "_sync_lock_acquired_at", server.time.time() - server._SYNC_LOCK_TIMEOUT - 1)

    def test_a_merge_only_poll_never_takes_a_held_lock(self) -> None:
        try:
            with self._held_too_long():
                assert _acquire_sync_lock(force=False) is False
                assert _acquire_sync_lock() is True  # a manual sync still can
        finally:
            try:  # a failed assert leaves the lock in either state
                _sync_executing.release()
            except RuntimeError:
                pass

    def test_autosync_reschedules_after_its_lock_was_force_released(self) -> None:
        """Auto-sync uploads what the webhook could not merge, so it must not stop."""

        def sync_whose_lock_is_taken(**kwargs):
            _sync_executing.release()  # what a force-release by another caller does
            return {"synced": 0, "skipped": 0, "failed": 0}

        config = {"auto_sync": {"enabled": True, "interval_minutes": 60}}
        with (
            patch.object(server, "load_config", return_value=config),
            patch.object(server, "sync", side_effect=sync_whose_lock_is_taken),
            patch.object(server, "_record_sync_log"),
            patch.object(server, "_schedule_autosync") as schedule,
        ):
            server._run_autosync()
        schedule.assert_called_once_with(60)
