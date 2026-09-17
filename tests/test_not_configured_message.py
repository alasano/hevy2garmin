"""The 'not configured' message must be context-aware (#224): telling a user whose
dashboard runs on Postgres to run 'hevy2garmin init' (a local interactive wizard) is
wrong; the real fix there is the dashboard setup + a matching DATABASE_URL."""
from unittest.mock import patch

from hevy2garmin.cli import _not_configured_message


def test_cloud_path_points_to_dashboard_not_init():
    with patch("hevy2garmin.db.get_database_url", return_value="postgres://x/db"):
        msg = _not_configured_message()
    assert "dashboard" in msg.lower()
    assert "DATABASE_URL" in msg
    assert "hevy2garmin init" not in msg  # wrong advice for the Postgres path


def test_local_path_mentions_init_and_the_secret():
    with patch("hevy2garmin.db.get_database_url", return_value=None):
        msg = _not_configured_message()
    assert "hevy2garmin init" in msg
    assert "DATABASE_URL" in msg  # also nudges Postgres users to set it
