"""Unsync endpoints: bulk Unsync All is confirm-gated (#174); a single unsync can delete on Garmin."""
from __future__ import annotations
import os
from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("HEVY2GARMIN_SECRET", None)
        import hevy2garmin.server as srv
        srv._is_configured_cache = True
        yield TestClient(srv.app)


def test_requires_confirm(client):
    with patch("hevy2garmin.server.is_configured", return_value=True):
        resp = client.post("/api/unsync-all")  # no confirm
    assert resp.status_code == 400
    assert resp.json()["ok"] is False


def test_clears_all_records(client):
    fake_db = MagicMock()
    with patch("hevy2garmin.server.is_configured", return_value=True), \
         patch("hevy2garmin.server.db.unsync_all", return_value=65) as mock_unsync, \
         patch("hevy2garmin.server.db.get_db", return_value=fake_db):
        resp = client.post("/api/unsync-all", data={"confirm": "RESET"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "count": 65}
    mock_unsync.assert_called_once()


@pytest.mark.parametrize("garmin_error", [None, RuntimeError("Garmin 500")])
def test_unsync_deletes_the_garmin_activity_through_the_rate_limiter(client, garmin_error):
    garmin = MagicMock()
    garmin.delete_activity.side_effect = garmin_error
    with patch("hevy2garmin.server.is_configured", return_value=True), \
         patch("hevy2garmin.server.load_config", return_value={"garmin_email": "a@b.c"}), \
         patch("hevy2garmin.server.db.get_garmin_id", return_value="42"), \
         patch("hevy2garmin.server.db.unsync", return_value=True), \
         patch("hevy2garmin.server.db.get_db", return_value=MagicMock()), \
         patch("hevy2garmin.garmin.get_client", return_value=garmin), \
         patch("hevy2garmin.garmin._limiter") as limiter:
        limiter.call.side_effect = lambda func, *args: func(*args)
        resp = client.post("/api/unsync/w1", data={"delete_garmin": "true"})

    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "garmin_deleted": garmin_error is None}
    limiter.call.assert_called_once_with(garmin.delete_activity, 42)


def test_cli_unsync_deletes_the_garmin_activity_through_the_rate_limiter():
    from argparse import Namespace

    from hevy2garmin import cli

    garmin = MagicMock()
    with patch("hevy2garmin.cli.db.get_garmin_id", return_value="42"), \
         patch("hevy2garmin.cli.db.unsync", return_value=True), \
         patch("hevy2garmin.cli.load_config", return_value={"garmin_email": "a@b.c"}), \
         patch("hevy2garmin.garmin.get_client", return_value=garmin), \
         patch("hevy2garmin.garmin._limiter") as limiter:
        limiter.call.side_effect = lambda func, *args: func(*args)
        cli.cmd_unsync(Namespace(all=False, confirm=False, hevy_id="w1", delete=True))

    limiter.call.assert_called_once_with(garmin.delete_activity, 42)
    garmin.delete_activity.assert_called_once_with(42)
