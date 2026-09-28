"""Webhook validation, durable jobs, and exact-workout retries."""

import asyncio
import os
import threading
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from requests import ConnectionError

from hevy2garmin import server, webhook
from hevy2garmin.db_sqlite import SQLiteDatabase
from hevy2garmin.hevy import HevyAuthError
from hevy2garmin.merge import MergeFailed
from hevy2garmin.sync import SyncOneResult

WID = "f1085cdb-32b2-4003-967d-53a3af8eaecb"
OTHER = "11111111-1111-4111-8111-111111111111"
HEADERS = {"Authorization": "Bearer cron-123"}


@pytest.fixture(params=["sqlite", "postgres"] if os.environ.get("DATABASE_URL") else ["sqlite"])
def store(request, tmp_path):
    if request.param == "postgres":
        from hevy2garmin.db_postgres import PostgresDatabase
        db = PostgresDatabase(os.environ["DATABASE_URL"])
        with db._get_conn() as conn:
            with conn.cursor() as cur:
                for table in ("webhook_jobs", "synced_workouts", "pending_uploads", "sync_log"):
                    cur.execute(f"DELETE FROM {table}")
        yield db
        db._conn_cache.close()
    else:
        yield SQLiteDatabase(tmp_path / "webhooks.db")


@pytest.fixture
def client(store, monkeypatch):
    monkeypatch.setenv("CRON_SECRET", "cron-123")
    monkeypatch.delenv("HEVY2GARMIN_SECRET", raising=False)
    monkeypatch.setattr(server, "_is_configured_cache", True)
    monkeypatch.setattr(server.db, "get_db", lambda: store)
    monkeypatch.setattr(server, "WEBHOOK_DELAY_SECONDS", 30)
    return TestClient(server.app)


def test_authentication_precedes_body_parsing(client, monkeypatch):
    assert client.post("/api/cron/webhook").status_code == 401
    assert client.post("/api/cron/webhook", headers={"Authorization": "Bearer wrong"}).status_code == 401
    monkeypatch.delenv("CRON_SECRET")
    assert client.post("/api/cron/webhook", headers=HEADERS).status_code == 503


@pytest.mark.parametrize("body", [None, [], {}, {"id": WID}, {"workoutId": 7},
                                      {"workoutId": "../invalid"}, {"payload": {"workoutId": WID}}])
def test_rejects_missing_or_invalid_workout_id(client, store, body):
    assert client.post("/api/cron/webhook", headers=HEADERS, json=body).status_code == 400
    assert store.get_due_webhook(float("inf")) is None


def test_delay_dedup_and_queue_bursts(client, store):
    with patch.object(server.time, "time", return_value=100):
        response = client.post("/api/cron/webhook", headers=HEADERS, json={"workoutId": WID})
    assert response.json() == {"status": "accepted", "workout_id": WID}
    with patch.object(server.time, "time", return_value=110):
        response = client.post("/api/cron/webhook", headers=HEADERS, json={"workoutId": WID})
    assert response.json()["status"] == "duplicate"
    assert store.get_due_webhook(129) is None
    assert store.get_due_webhook(130)["attempts"] == 0
    for n in range(6):
        wid = f"00000000-0000-4000-8000-{n:012d}"
        assert client.post("/api/cron/webhook", headers=HEADERS, json={"workoutId": wid}).json()["status"] == "accepted"


def test_persistence_failure_is_not_acknowledged(client, store):
    with patch.object(store, "enqueue_webhook", side_effect=RuntimeError("database unavailable")):
        assert client.post("/api/cron/webhook", headers=HEADERS, json={"workoutId": WID}).status_code == 503


def test_restart_and_terminal_dedup(store):
    store.enqueue_webhook(WID, 30)
    if isinstance(store, SQLiteDatabase):
        reopened = SQLiteDatabase(store.db_path)
    else:
        reopened = type(store)(store.database_url)
    try:
        assert reopened.get_due_webhook(30)["hevy_id"] == WID
        reopened.save_webhook(WID, "completed", 1, 330, None)
        assert store.enqueue_webhook(WID, 500) is False
        assert store.get_due_webhook(1000) is None
    finally:
        if getattr(reopened, "_conn_cache", None):
            reopened._conn_cache.close()


@pytest.fixture
def execution(store, monkeypatch):
    store.enqueue_webhook(WID, 30)
    hevy = MagicMock()
    hevy.get_workout.return_value = {"id": WID, "title": "Push"}
    monkeypatch.setattr(webhook, "HevyClient", lambda **kw: hevy)
    monkeypatch.setattr(webhook, "get_client", lambda *a: "garmin")
    reset = MagicMock()
    monkeypatch.setattr(webhook, "reset_circuit_breaker", reset)
    sync = MagicMock(return_value=SyncOneResult(status="merge_pending"))
    monkeypatch.setattr(webhook, "sync_one_workout", sync)
    return hevy, sync, reset


def run_job(store, now=30, config=None, max_attempts=24):
    with patch.object(webhook.time, "time", return_value=now):
        webhook.process_job(store, store.get_due_webhook(now), config or {"hevy_api_key": "test"},
                            retry_seconds=300, max_attempts=max_attempts)


def test_exact_id_and_retry_schedule(store, execution):
    hevy, sync, reset = execution
    store.enqueue_webhook(OTHER, 900)
    run_job(store)
    hevy.get_workout.assert_called_once_with(WID)
    hevy.get_workouts.assert_not_called()
    assert sync.call_args.kwargs["merge_only"] is True
    assert sync.call_args.kwargs["database"] is store
    assert store.get_due_webhook(329) is None
    assert store.get_due_webhook(330)["attempts"] == 1
    sync.return_value = SyncOneResult(status="synced")
    run_job(store, 330)
    assert reset.call_count == 2
    assert store.get_due_webhook(899) is None
    assert store.get_due_webhook(900)["hevy_id"] == OTHER
    assert store.get_sync_log()[0]["synced"] == 1


def test_already_synced_never_merges(store, execution):
    store.mark_synced(WID, "123", title="Push")
    run_job(store)
    execution[0].get_workout.assert_not_called()
    execution[1].assert_not_called()
    assert store.get_due_webhook(1000) is None


def test_pending_upload_keeps_ownership(store, execution):
    with patch.object(store, "get_pending", return_value={"phase": "processing"}):
        run_job(store)
    execution[1].assert_not_called()
    assert store.get_due_webhook(1000) is None


@pytest.mark.parametrize("failure", [ConnectionError("temporary"), None])
def test_transient_failure_or_not_yet_visible_retries(store, execution, failure):
    hevy, _, _ = execution
    hevy.get_workout.side_effect = failure
    hevy.get_workout.return_value = None
    run_job(store)
    assert store.get_due_webhook(330)["attempts"] == 1


def test_wrapped_auth_failure_stops(store, execution):
    failure = MergeFailed("Garmin login required")
    failure.__cause__ = HevyAuthError("Reconnect account")
    execution[1].side_effect = failure
    run_job(store)
    assert store.get_due_webhook(1000) is None
    assert store.get_sync_log()[0]["failed"] == 1


def test_disabled_merge_stops_without_upload(store, execution):
    run_job(store, config={"hevy_api_key": "test", "merge_mode": False})
    execution[1].assert_not_called()
    assert store.get_due_webhook(1000) is None


def test_exhaustion_stops_and_records_failure(store, execution):
    run_job(store, max_attempts=1)
    assert store.get_due_webhook(1000) is None
    assert store.get_sync_log()[0]["failed"] == 1


def test_worker_busy_does_not_consume_attempt(store, monkeypatch):
    store.enqueue_webhook(WID, 0)
    stop = threading.Event()
    def busy(**kwargs):
        stop.set()
        return False
    monkeypatch.setattr(server, "_webhook_stop", stop)
    monkeypatch.setattr(server, "_acquire_sync_lock", busy)
    monkeypatch.setattr(server.db, "get_db", lambda: store)
    monkeypatch.setattr(server.db, "get_database_url", lambda: getattr(store, "database_url", None))
    server._webhook_worker()
    assert store.get_due_webhook(30)["attempts"] == 0


def test_worker_recovers_due_job_on_startup(store, execution, monkeypatch):
    # A real thread consumes the durable job; stopping waits for its current attempt.
    stop = threading.Event()
    monkeypatch.setattr(server, "_webhook_stop", stop)
    monkeypatch.setattr(server, "_webhook_thread", None)
    monkeypatch.setattr(server.db, "get_db", lambda: store)
    monkeypatch.setattr(server.db, "get_database_url", lambda: getattr(store, "database_url", None))
    monkeypatch.setattr(server, "load_config", lambda **kw: {"hevy_api_key": "test"})
    def finish(*args, **kwargs):
        stop.set()
        return SyncOneResult(status="synced")
    execution[1].side_effect = finish
    asyncio.run(server._start_webhook_worker())
    server._webhook_thread.join(timeout=5)
    assert not server._webhook_thread.is_alive()
    assert store.get_due_webhook(float("inf")) is None
    asyncio.run(server._stop_webhook_worker())


def test_manual_sync_cannot_race_worker(client, store):
    assert server._acquire_sync_lock()
    try:
        assert client.post(f"/api/sync/{WID}").status_code == 409
    finally:
        server._sync_executing.release()
    store.mark_synced(WID, "123", title="Push")
    with patch("hevy2garmin.hevy.HevyClient") as hevy:
        response = client.post(f"/api/sync/{WID}")
    assert response.headers["HX-Refresh"] == "true"
    hevy.assert_not_called()
    assert not server._sync_executing.locked()


def test_merge_only_disabled_never_generates_fit():
    from hevy2garmin.sync import sync_one_workout
    store = MagicMock()
    store.get_pending.return_value = None
    with patch("hevy2garmin.sync.generate_fit") as fit:
        with pytest.raises(ValueError, match="Enable merge mode"):
            sync_one_workout({"id": WID}, cfg={"merge_mode": False},
                             database=store, merge_only=True)
    fit.assert_not_called()


def test_development_merge_preserves_auth_failure():
    from hevy2garmin.sync import sync_one_workout
    from hevy2garmin.merge import MergeResult
    from garminconnect import GarminConnectAuthenticationError
    failure = GarminConnectAuthenticationError("Reconnect Garmin")
    store = MagicMock()
    store.get_pending.return_value = None
    with patch("hevy2garmin.sync.attempt_merge", return_value=MergeResult(merged=False, error=failure)):
        with pytest.raises(GarminConnectAuthenticationError):
            sync_one_workout({"id": WID}, cfg={}, garmin_client=object(), database=store, merge_only=True)


def test_merge_only_without_client_never_generates_fit():
    from hevy2garmin.sync import sync_one_workout
    store = MagicMock()
    store.get_pending.return_value = None
    with patch("hevy2garmin.sync.generate_fit") as fit:
        with pytest.raises(ValueError, match="Garmin client required"):
            sync_one_workout({"id": WID}, cfg={}, database=store, merge_only=True)
    fit.assert_not_called()
