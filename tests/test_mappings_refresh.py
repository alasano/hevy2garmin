"""Mapped exercises must leave the unmapped list immediately (#172)."""
from __future__ import annotations
from unittest.mock import MagicMock, patch
from hevy2garmin.server import _get_unmapped_exercises


def test_filters_out_now_mapped_exercises():
    fake_db = MagicMock()
    fake_db.get_app_config.return_value = {
        "Totally Invented Movement 9000": 3,  # genuinely unmapped
        "Bench Press (Barbell)": 2,           # has a built-in mapping
    }
    with patch("hevy2garmin.server.db.get_db", return_value=fake_db):
        result = _get_unmapped_exercises()
    names = [n for n, _ in result]
    assert "Totally Invented Movement 9000" in names
    assert "Bench Press (Barbell)" not in names


def test_custom_mapped_exercise_is_filtered():
    import hevy2garmin.mapper as m
    m._custom_mappings["Seitheben (Kurzhantel)"] = (14, 11)  # just mapped
    try:
        fake_db = MagicMock()
        fake_db.get_app_config.return_value = {"Seitheben (Kurzhantel)": 1}
        with patch("hevy2garmin.server.db.get_db", return_value=fake_db):
            result = _get_unmapped_exercises()
        assert all(n != "Seitheben (Kurzhantel)" for n, _ in result)
    finally:
        m._custom_mappings.pop("Seitheben (Kurzhantel)", None)


def test_a_mapping_posted_without_a_sub_id_is_generic(monkeypatch):
    """0 is the first exercise of every category, not "no exercise"."""
    from fastapi.testclient import TestClient

    from hevy2garmin import server

    saved = {}
    monkeypatch.setattr(server, "_is_configured_cache", True)
    monkeypatch.setattr(server.db, "get_database_url", lambda: None)
    monkeypatch.setattr(server.db, "get_db", lambda: MagicMock())
    monkeypatch.setattr("hevy2garmin.mapper.save_custom_mapping", lambda name, cat, sub: saved.update({name: (cat, sub)}))

    TestClient(server.app).post("/api/mapping", data={"hevy_name": "Band Pull Apart", "category": "14"})
    assert saved == {"Band Pull Apart": (14, 65535)}
