"""Tests for merge mode: matching heuristic + payload builder."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from hevy2garmin.merge import (
    MergeFailed,
    MergeResult,
    attempt_merge,
    build_exercise_sets_payload,
    reset_circuit_breaker,
    _category_to_string,
    _exercise_to_string,
    _strip_exercise_names,
    _is_subcategory_rejection,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_garmin_activity(
    activity_id: int = 12345,
    start: str = "2026-03-15 18:02:00",
    duration_s: float = 43 * 60,
    type_key: str = "strength_training",
) -> dict:
    return {
        "activityId": activity_id,
        "startTimeGMT": start,
        "startTimeLocal": start,
        "duration": duration_s,
        "activityType": {"typeKey": type_key},
    }


HEVY_WORKOUT = {
    "id": "test-123",
    "title": "Push",
    "start_time": "2026-03-15T18:00:00+00:00",
    "end_time": "2026-03-15T18:45:00+00:00",
    "exercises": [
        {
            "title": "Bench Press (Barbell)",
            "sets": [
                {"type": "warmup", "weight_kg": 40, "reps": 12},
                {"type": "normal", "weight_kg": 60, "reps": 10},
                {"type": "normal", "weight_kg": 60, "reps": 8},
            ],
        },
        {
            "title": "Shoulder Press (Dumbbell)",
            "sets": [
                {"type": "normal", "weight_kg": 14, "reps": 12},
                {"type": "normal", "weight_kg": 14, "reps": 10},
            ],
        },
    ],
}


# ---------------------------------------------------------------------------
# Matching heuristic tests
# ---------------------------------------------------------------------------

class TestFindMatchingActivity:

    def test_exact_overlap_matches(self):
        """Strength training with high overlap → match."""
        from hevy2garmin.garmin import find_matching_garmin_activity

        client = MagicMock()
        client.get_activities_by_date.return_value = [
            _make_garmin_activity(start="2026-03-15 18:02:00", duration_s=43 * 60),
        ]
        match = find_matching_garmin_activity(client, HEVY_WORKOUT)
        assert match is not None
        assert match["activityId"] == 12345

    def test_low_overlap_rejected(self):
        """10 of the recording's 25 minutes overlap (40% of the shorter) → no match."""
        from hevy2garmin.garmin import find_matching_garmin_activity

        client = MagicMock()
        # Starts 15 min early, inside the drift limit, so only the overlap can reject it.
        client.get_activities_by_date.return_value = [
            _make_garmin_activity(start="2026-03-15 17:45:00", duration_s=25 * 60),
        ]
        match = find_matching_garmin_activity(client, HEVY_WORKOUT)
        assert match is None

    def test_recording_inside_a_workout_finished_late_matches(self):
        """Watch stopped after 60 min, Hevy finished 12 h later: the watch covers 8% of it.

        With a 17 min start drift the ranking score is negative, and the only
        candidate must still win.
        """
        from hevy2garmin.garmin import find_matching_garmin_activity

        client = MagicMock()
        client.get_activities_by_date.return_value = [
            _make_garmin_activity(start="2026-03-15 18:17:00", duration_s=60 * 60),
        ]
        late = {**HEVY_WORKOUT, "end_time": "2026-03-16T06:00:00+00:00"}
        match = find_matching_garmin_activity(client, late)
        assert match is not None and match["activityId"] == 12345

    def test_workout_inside_a_recording_left_running_matches(self):
        from hevy2garmin.garmin import find_matching_garmin_activity

        client = MagicMock()
        client.get_activities_by_date.return_value = [
            _make_garmin_activity(start="2026-03-15 17:58:00", duration_s=5 * 3600),
        ]
        match = find_matching_garmin_activity(client, HEVY_WORKOUT)
        assert match is not None and match["activityId"] == 12345

    @pytest.mark.parametrize("stray_first", [True, False])
    def test_stray_short_recording_does_not_beat_the_real_one(self, stray_first):
        """Both lie inside the workout; the closer start must not decide."""
        from hevy2garmin.garmin import find_matching_garmin_activity

        stray = _make_garmin_activity(activity_id=1, start="2026-03-15 18:01:00", duration_s=2 * 60)
        real = _make_garmin_activity(activity_id=2, start="2026-03-15 18:04:00", duration_s=40 * 60)
        client = MagicMock()
        client.get_activities_by_date.return_value = [stray, real] if stray_first else [real, stray]
        match = find_matching_garmin_activity(client, HEVY_WORKOUT)
        assert match["activityId"] == 2

    def test_lone_stray_short_recording_is_not_a_match(self):
        """A webhook attempt can run before the real recording reaches Garmin."""
        from hevy2garmin.garmin import find_matching_garmin_activity

        client = MagicMock()
        client.get_activities_by_date.return_value = [
            _make_garmin_activity(start="2026-03-15 18:01:00", duration_s=2 * 60),
        ]
        assert find_matching_garmin_activity(client, HEVY_WORKOUT) is None

    def test_short_workout_still_matches_its_recording(self):
        """An 8 min workout cannot overlap anything for ten minutes."""
        from hevy2garmin.garmin import find_matching_garmin_activity

        client = MagicMock()
        client.get_activities_by_date.return_value = [
            _make_garmin_activity(start="2026-03-15 18:00:30", duration_s=7 * 60),
        ]
        short = {**HEVY_WORKOUT, "end_time": "2026-03-15T18:08:00+00:00"}
        match = find_matching_garmin_activity(client, short)
        assert match is not None and match["activityId"] == 12345

    def test_evening_workout_is_searched_on_its_local_date(self):
        """02:30 UTC is the evening before in the Americas, where Garmin files the recording."""
        from hevy2garmin.garmin import find_matching_garmin_activity

        client = MagicMock()
        client.get_activities_by_date.return_value = []
        evening = {
            **HEVY_WORKOUT,
            "start_time": "2026-03-16T02:30:00+00:00",
            "end_time": "2026-03-16T03:30:00+00:00",
        }
        find_matching_garmin_activity(client, evening)
        client.get_activities_by_date.assert_called_once_with("2026-03-15", "2026-03-17")

    def test_failed_listing_is_not_no_match(self):
        """A caller told "no match" uploads next to a recording it could not see."""
        from hevy2garmin.garmin import find_matching_garmin_activity

        client = MagicMock()
        client.get_activities_by_date.side_effect = RuntimeError("Garmin unreachable")
        with pytest.raises(RuntimeError, match="Garmin unreachable"):
            find_matching_garmin_activity(client, HEVY_WORKOUT)

    def test_wrong_type_rejected(self):
        """Running activity with perfect overlap → no match."""
        from hevy2garmin.garmin import find_matching_garmin_activity

        client = MagicMock()
        client.get_activities_by_date.return_value = [
            _make_garmin_activity(type_key="running"),
        ]
        match = find_matching_garmin_activity(client, HEVY_WORKOUT)
        assert match is None

    def test_non_strength_type_rejected_by_default(self):
        """Climbing activity with perfect overlap → no match unless opted in."""
        from hevy2garmin.garmin import find_matching_garmin_activity

        client = MagicMock()
        client.get_activities_by_date.return_value = [
            _make_garmin_activity(type_key="bouldering"),
        ]
        match = find_matching_garmin_activity(client, HEVY_WORKOUT)
        assert match is None

    def test_non_strength_type_matches_when_configured(self):
        """Climbing activity matches when its type is added to activity_types."""
        from hevy2garmin.garmin import find_matching_garmin_activity

        client = MagicMock()
        client.get_activities_by_date.return_value = [
            _make_garmin_activity(type_key="bouldering"),
        ]
        match = find_matching_garmin_activity(
            client, HEVY_WORKOUT, activity_types={"strength_training", "bouldering"},
        )
        assert match is not None
        assert match["activityId"] == 12345

    def test_incomplete_activity_rejected(self):
        """Activity still in progress (end time in future) → no match."""
        from datetime import datetime, timezone
        from hevy2garmin.garmin import find_matching_garmin_activity

        now = datetime.now(timezone.utc)
        # Activity started 10 min ago with a very long duration (still recording)
        recent_start = now.strftime("%Y-%m-%d %H:%M:%S")
        hevy_now = {
            **HEVY_WORKOUT,
            "start_time": now.isoformat(),
            "end_time": (now + __import__("datetime").timedelta(minutes=45)).isoformat(),
        }
        client = MagicMock()
        client.get_activities_by_date.return_value = [
            _make_garmin_activity(start=recent_start, duration_s=999999),
        ]
        match = find_matching_garmin_activity(client, hevy_now)
        assert match is None

    def test_best_of_multiple_candidates(self):
        """When multiple candidates overlap, pick the highest-scoring one."""
        from hevy2garmin.garmin import find_matching_garmin_activity

        client = MagicMock()
        client.get_activities_by_date.return_value = [
            _make_garmin_activity(activity_id=1, start="2026-03-15 18:10:00", duration_s=35 * 60),
            _make_garmin_activity(activity_id=2, start="2026-03-15 18:01:00", duration_s=44 * 60),
        ]
        match = find_matching_garmin_activity(client, HEVY_WORKOUT)
        assert match is not None
        assert match["activityId"] == 2  # Better overlap + closer start


# ---------------------------------------------------------------------------
# Payload builder tests
# ---------------------------------------------------------------------------

class TestBuildPayload:

    def test_payload_structure(self):
        """Payload has activityId and exerciseSets list."""
        payload = build_exercise_sets_payload(
            HEVY_WORKOUT,
            activity_id=12345,
            activity_start_time="2026-03-15 18:00:00",
            activity_duration_s=45 * 60,
        )
        assert payload["activityId"] == 12345
        assert isinstance(payload["exerciseSets"], list)
        assert len(payload["exerciseSets"]) > 0

    def test_active_and_rest_sets(self):
        """Payload contains both ACTIVE and REST sets."""
        payload = build_exercise_sets_payload(
            HEVY_WORKOUT,
            activity_id=12345,
            activity_start_time="2026-03-15 18:00:00",
            activity_duration_s=45 * 60,
        )
        types = {s["setType"] for s in payload["exerciseSets"]}
        assert "ACTIVE" in types
        assert "REST" in types

    def test_exercise_count_matches(self):
        """Number of ACTIVE sets matches total sets in Hevy workout."""
        payload = build_exercise_sets_payload(
            HEVY_WORKOUT,
            activity_id=12345,
            activity_start_time="2026-03-15 18:00:00",
            activity_duration_s=45 * 60,
        )
        active = [s for s in payload["exerciseSets"] if s["setType"] == "ACTIVE"]
        # 3 bench sets + 2 shoulder sets = 5
        assert len(active) == 5

    def test_weight_in_grams(self):
        """Weight is converted from kg to grams."""
        payload = build_exercise_sets_payload(
            HEVY_WORKOUT,
            activity_id=12345,
            activity_start_time="2026-03-15 18:00:00",
            activity_duration_s=45 * 60,
        )
        first_active = next(s for s in payload["exerciseSets"] if s["setType"] == "ACTIVE")
        assert first_active["weight"] == 40000  # 40 kg warmup = 40000 grams

    def test_wkt_step_index_groups_exercises(self):
        """wktStepIndex groups sets by exercise (0 for bench, 1 for shoulder)."""
        payload = build_exercise_sets_payload(
            HEVY_WORKOUT,
            activity_id=12345,
            activity_start_time="2026-03-15 18:00:00",
            activity_duration_s=45 * 60,
        )
        active = [s for s in payload["exerciseSets"] if s["setType"] == "ACTIVE"]
        bench_steps = {s["wktStepIndex"] for s in active[:3]}
        shoulder_steps = {s["wktStepIndex"] for s in active[3:]}
        assert bench_steps == {0}
        assert shoulder_steps == {1}

    def test_category_string_mapping(self):
        """Exercise categories are strings, not ints."""
        payload = build_exercise_sets_payload(
            HEVY_WORKOUT,
            activity_id=12345,
            activity_start_time="2026-03-15 18:00:00",
            activity_duration_s=45 * 60,
        )
        first_active = next(s for s in payload["exerciseSets"] if s["setType"] == "ACTIVE")
        assert first_active["exercises"][0]["category"] == "BENCH_PRESS"
        assert isinstance(first_active["exercises"][0]["name"], str)

    def test_exercise_objects_match_garmin_shape(self):
        """Every exercise object must have category (str) + name (str|None) + probability.

        Verified against the real Garmin exerciseSets response shape:
        {"category": "BENCH_PRESS", "name": "INCLINE_DUMBBELL_BENCH_PRESS", "probability": ...}
        — `name` is the SUBCATEGORY, never the parent or "TOTAL_BODY", or Garmin
        renders it as "Unknown" (#138).
        """
        payload = build_exercise_sets_payload(
            HEVY_WORKOUT,
            activity_id=12345,
            activity_start_time="2026-03-15 18:00:00",
            activity_duration_s=45 * 60,
        )
        for s in payload["exerciseSets"]:
            if s["setType"] != "ACTIVE":
                assert s["exercises"] == []
                continue
            for ex in s["exercises"]:
                assert set(ex) == {"category", "name", "probability"}
                # 0/null confidence makes Connect render the exercise as "Unknown"
                # on watch-recorded activities (#325); the web UI's edits send 100
                assert ex["probability"] == 100
                assert isinstance(ex["category"], str) and ex["category"] != "UNKNOWN"
                assert ex["name"] is None or isinstance(ex["name"], str)
                # name must never echo the parent or the TOTAL_BODY placeholder
                assert ex["name"] != ex["category"]
                assert ex["name"] != "TOTAL_BODY"

    def test_unknown_exercise_uses_total_body_parent_with_null_name(self):
        """An unmapped exercise → category=TOTAL_BODY parent, name=None (not 'TOTAL_BODY')."""
        workout = {
            "title": "Odd",
            "start_time": "2026-03-15T18:00:00+00:00",
            "end_time": "2026-03-15T18:10:00+00:00",
            "exercises": [
                {"title": "Totally Invented Movement 9000",
                 "sets": [{"type": "normal", "weight_kg": 10, "reps": 5}]},
            ],
        }
        payload = build_exercise_sets_payload(
            workout, activity_id=1, activity_start_time="2026-03-15 18:00:00",
            activity_duration_s=10 * 60,
        )
        active = next(s for s in payload["exerciseSets"] if s["setType"] == "ACTIVE")
        ex = active["exercises"][0]
        assert ex["category"] == "TOTAL_BODY"
        assert ex["name"] is None  # never "TOTAL_BODY" as the name

    def test_catalog_only_subcategory_keeps_full_name(self):
        """(23, 46) postdates the pinned fit-tool's enums; the bundled FIT SDK
        catalog must resolve it so the set keeps its full name (#328)."""
        workout = {
            "title": "Pull",
            "start_time": "2026-03-15T18:00:00+00:00",
            "end_time": "2026-03-15T18:10:00+00:00",
            "exercises": [
                {"title": "Bent Over Row (Barbell)",
                 "sets": [{"type": "normal", "weight_kg": 60, "reps": 8}]},
            ],
        }
        payload = build_exercise_sets_payload(
            workout, activity_id=1, activity_start_time="2026-03-15 18:00:00",
            activity_duration_s=10 * 60,
        )
        active = next(s for s in payload["exerciseSets"] if s["setType"] == "ACTIVE")
        assert active["exercises"][0]["category"] == "ROW"
        assert active["exercises"][0]["name"] == "BENT_OVER_ROW_WITH_BARBELL"

    def test_empty_workout(self):
        """Workout with no exercises produces empty sets list."""
        workout = {**HEVY_WORKOUT, "exercises": []}
        payload = build_exercise_sets_payload(
            workout,
            activity_id=12345,
            activity_start_time="2026-03-15 18:00:00",
            activity_duration_s=45 * 60,
        )
        assert payload["exerciseSets"] == []


# ---------------------------------------------------------------------------
# Category string conversion tests
# ---------------------------------------------------------------------------

class TestCategoryConversion:

    def test_known_category(self):
        assert _category_to_string(0) == "BENCH_PRESS"
        assert _category_to_string(28) == "SQUAT"
        assert _category_to_string(23) == "ROW"

    def test_unknown_category(self):
        assert _category_to_string(65534) == "UNKNOWN"
        assert _category_to_string(9999) == "UNKNOWN"

    def test_subcategory_resolves_to_valid_enum_string(self):
        # (0, 1) BENCH_PRESS → a real FIT subcategory string
        result = _exercise_to_string(0, 1)
        assert isinstance(result, str) and result  # e.g. "BARBELL_BENCH_PRESS"

    def test_subcategory_returns_none_when_unresolvable(self):
        # Out-of-range subcategory must yield None, NOT the parent name (#138)
        assert _exercise_to_string(0, 9999) is None
        # Unknown category likewise yields None (never "UNKNOWN" / parent fallback)
        assert _exercise_to_string(65534, 0) is None


# ---------------------------------------------------------------------------
# Integration: attempt_merge
# ---------------------------------------------------------------------------

_APPLIED = {"exerciseSets": [
    {"setType": "ACTIVE", "exercises": [{"category": "BENCH_PRESS", "name": "BARBELL_BENCH_PRESS"}]}
]}
_DROPPED = {"exerciseSets": [
    {"setType": "ACTIVE", "exercises": [{"category": None, "name": None}]}
]}


class TestAttemptMerge:

    def setup_method(self):
        reset_circuit_breaker()

    @patch("hevy2garmin.merge.time.sleep")  # don't wait for the verify read-back
    @patch("hevy2garmin.merge.find_matching_garmin_activity")
    @patch("hevy2garmin.merge.get_activity_exercise_sets")
    @patch("hevy2garmin.merge.push_exercise_sets")
    @patch("hevy2garmin.merge.rename_activity")
    @patch("hevy2garmin.merge.set_description")
    def test_merge_path_taken(self, mock_desc, mock_rename, mock_push, mock_get_sets, mock_find, _sleep):
        """When a match is found, PUT is called and (if names stick) merged=True."""
        mock_find.return_value = _make_garmin_activity()
        # first read = backup, second read = verify (names applied)
        mock_get_sets.side_effect = [{"exerciseSets": []}, _APPLIED]
        mock_db = MagicMock()
        mock_db.get_app_config.return_value = None  # a first merge: no backup yet

        result = attempt_merge(MagicMock(), HEVY_WORKOUT, mock_db)

        assert result.merged is True
        assert result.force_fresh_upload is False
        assert result.activity_id == 12345
        mock_push.assert_called_once()
        mock_rename.assert_called_once()

    @patch("hevy2garmin.merge.time.sleep")
    @patch("hevy2garmin.merge.find_matching_garmin_activity")
    @patch("hevy2garmin.merge.get_activity_exercise_sets")
    @patch("hevy2garmin.merge.push_exercise_sets")
    @patch("hevy2garmin.merge.rename_activity")
    def test_names_dropped_forces_fresh_upload(self, mock_rename, mock_push, mock_get_sets, mock_find, _sleep):
        """Garmin drops the names on one of our own uploads -> merged=False, force_fresh_upload=True (#159)."""
        mock_find.return_value = _make_garmin_activity()
        # backup read, then verify read shows the names were dropped
        mock_get_sets.side_effect = [{"exerciseSets": []}, _DROPPED]
        mock_db = MagicMock()
        mock_db.get_app_config.return_value = {"original_sets": {"exerciseSets": []}}

        result = attempt_merge(MagicMock(), HEVY_WORKOUT, mock_db)

        assert result.merged is False
        assert result.force_fresh_upload is True
        # PUT happened twice: the merge push + the restore
        assert mock_push.call_count == 2
        # we did NOT rename the activity since we abandoned the merge
        mock_rename.assert_not_called()

    @patch("hevy2garmin.merge.find_matching_garmin_activity")
    def test_no_match_fallback(self, mock_find):
        """When no match, result is merged=False with reason."""
        mock_find.return_value = None
        mock_db = MagicMock()

        result = attempt_merge(MagicMock(), HEVY_WORKOUT, mock_db)

        assert result.merged is False
        assert "No matching" in result.fallback_reason

    @patch("hevy2garmin.merge.time.sleep")
    @patch("hevy2garmin.merge.find_matching_garmin_activity")
    @patch("hevy2garmin.merge.get_activity_exercise_sets")
    @patch("hevy2garmin.merge.push_exercise_sets")
    def test_second_merge_keeps_the_backup_of_the_watch_sets(self, mock_push, mock_get_sets, mock_find, _sleep):
        """Unsync and sync again: the activity then holds Hevy sets, not the watch's."""
        watch = _make_garmin_activity()
        watch["manufacturer"] = "GARMIN"
        mock_find.return_value = watch
        watch_sets = {"exerciseSets": [{"setType": "ACTIVE", "repetitionCount": 7}]}
        pushed_sets = {"exerciseSets": [{"setType": "ACTIVE", "repetitionCount": 8}]}
        mock_get_sets.side_effect = [watch_sets, pushed_sets]
        stored: dict = {}
        database = MagicMock()
        database.get_app_config.side_effect = stored.get
        database.set_app_config.side_effect = stored.__setitem__

        for _ in range(2):
            assert attempt_merge(MagicMock(), HEVY_WORKOUT, database).merged

        assert stored["merge_backup_12345"]["original_sets"] == watch_sets
        assert mock_push.call_count == 2

    @patch("hevy2garmin.merge.find_matching_garmin_activity")
    @patch("hevy2garmin.merge.get_activity_exercise_sets")
    @patch("hevy2garmin.merge.push_exercise_sets")
    def test_circuit_breaker_trips(self, mock_push, mock_get_sets, mock_find):
        """After 3 consecutive PUT failures, merge is disabled."""
        mock_find.return_value = _make_garmin_activity()
        mock_get_sets.return_value = {"exerciseSets": []}
        mock_push.side_effect = RuntimeError("PUT failed")
        mock_db = MagicMock()

        for _ in range(3):
            attempt_merge(MagicMock(), HEVY_WORKOUT, mock_db)

        # 4th attempt should be blocked by circuit breaker
        result = attempt_merge(MagicMock(), HEVY_WORKOUT, mock_db)
        assert result.merged is False
        assert "Circuit breaker" in result.fallback_reason

    @patch("hevy2garmin.merge.find_matching_garmin_activity")
    @patch("hevy2garmin.merge.get_activity_exercise_sets")
    @patch("hevy2garmin.merge.push_exercise_sets")
    def test_tripped_breaker_fails_a_watch_merge_instead_of_falling_back(self, mock_push, mock_get_sets, mock_find):
        """The fourth watch workout of a run must not be renamed without sets either."""
        watch = _make_garmin_activity()
        watch["manufacturer"] = "GARMIN"
        mock_find.return_value = watch
        mock_get_sets.return_value = {"exerciseSets": []}
        mock_push.side_effect = RuntimeError("PUT failed")

        for _ in range(4):
            with pytest.raises(MergeFailed):
                attempt_merge(MagicMock(), HEVY_WORKOUT, MagicMock())
        assert mock_push.call_count == 3


@patch("hevy2garmin.merge.time.sleep")
@patch("hevy2garmin.merge.find_matching_garmin_activity")
@patch("hevy2garmin.merge.get_activity_exercise_sets")
@patch("hevy2garmin.merge.push_exercise_sets")
@patch("hevy2garmin.merge.rename_activity")
@patch("hevy2garmin.merge.set_description")
def test_development_upload_still_merges(mock_desc, mock_rename, mock_push, mock_get, mock_find, _sleep):
    """A hevy2garmin upload (manufacturer DEVELOPMENT) is still merged normally."""
    reset_circuit_breaker()
    act = _make_garmin_activity()
    act["manufacturer"] = "DEVELOPMENT"
    mock_find.return_value = act
    mock_get.side_effect = [{"exerciseSets": []}, _APPLIED]
    first_merge = MagicMock()
    first_merge.get_app_config.return_value = None  # no backup yet, so the first read is the backup

    result = attempt_merge(MagicMock(), HEVY_WORKOUT, first_merge)

    assert result.merged is True
    mock_push.assert_called_once()


@patch("hevy2garmin.merge.time.sleep")
@patch("hevy2garmin.merge.find_matching_garmin_activity")
@patch("hevy2garmin.merge.get_activity_exercise_sets")
@patch("hevy2garmin.merge.push_exercise_sets")
@patch("hevy2garmin.merge.rename_activity")
@patch("hevy2garmin.merge.set_description")
@patch("hevy2garmin.merge.generate_description")
def test_watch_activity_is_merged_in_place(mock_gen, mock_desc, mock_rename, mock_push, mock_get, mock_find, _sleep):
    """A watch match gets the sets pushed into it and is kept as one activity
    (merged=True), without verifying names or forcing a fresh upload (#159)."""
    reset_circuit_breaker()
    act = _make_garmin_activity()
    act["manufacturer"] = "GARMIN"
    mock_find.return_value = act
    mock_get.return_value = {"exerciseSets": []}  # only the backup read; verify is skipped
    mock_gen.return_value = "Dumbbell Row: 3 sets"

    result = attempt_merge(MagicMock(), HEVY_WORKOUT, MagicMock())

    assert result.merged is True
    assert result.activity_id == 12345
    assert result.force_fresh_upload is False
    mock_push.assert_called_once()     # pushed the sets into the watch activity
    mock_rename.assert_called_once()   # renamed + described it in place


# ---------------------------------------------------------------------------
# Resilience: one rejected subcategory must not drop the whole merge
# ---------------------------------------------------------------------------

def test_strip_exercise_names_nulls_names_keeps_category():
    """_strip_exercise_names returns a copy with every name nulled, categories kept."""
    payload = build_exercise_sets_payload(
        HEVY_WORKOUT, activity_id=1,
        activity_start_time="2026-03-15 18:00:00", activity_duration_s=45 * 60,
    )
    stripped = _strip_exercise_names(payload)
    # the original still has real names (copy, not mutated)
    assert any(ex["name"] for s in payload["exerciseSets"] for ex in s["exercises"])
    for s in stripped["exerciseSets"]:
        for ex in s["exercises"]:
            assert ex["name"] is None
            assert "category" in ex


def test_is_subcategory_rejection_detects_garmin_400():
    assert _is_subcategory_rejection(RuntimeError("API Error 400 - Invalid Sub-Category Passed in the request"))
    assert _is_subcategory_rejection(Exception("invalid subcategory"))
    assert not _is_subcategory_rejection(RuntimeError("connection reset"))
    assert not _is_subcategory_rejection(RuntimeError("PUT failed"))


@patch("hevy2garmin.merge.time.sleep")
@patch("hevy2garmin.merge.find_matching_garmin_activity")
@patch("hevy2garmin.merge.get_activity_exercise_sets")
@patch("hevy2garmin.merge.push_exercise_sets")
@patch("hevy2garmin.merge.rename_activity")
@patch("hevy2garmin.merge.set_description")
@patch("hevy2garmin.merge.generate_description")
def test_subcategory_400_retries_without_names(mock_gen, mock_desc, mock_rename, mock_push, mock_get, mock_find, _sleep):
    """A subcategory 400 on the atomic PUT retries once with names stripped so the
    sets still land (merged=True), instead of dropping the entire merge."""
    reset_circuit_breaker()
    act = _make_garmin_activity()
    act["manufacturer"] = "GARMIN"          # a watch activity skips the name-verify
    mock_find.return_value = act
    mock_get.return_value = {"exerciseSets": []}
    mock_gen.return_value = "Bench: 3 sets"
    # First push (full payload) is rejected; the strip retry(s) then succeed. The
    # surgical strip (#222) bisects, so it may take a few PUTs — accept any.
    calls: list = []

    def push_side_effect(client, aid, payload):
        calls.append(payload)
        if len(calls) == 1:
            raise RuntimeError("API Error 400 - Invalid Sub-Category Passed in the request")
        return None

    mock_push.side_effect = push_side_effect

    result = attempt_merge(MagicMock(), HEVY_WORKOUT, MagicMock())

    assert result.merged is True
    assert len(calls) >= 2  # retried after the subcategory rejection
    # The landed retry stripped at least one exercise name (category kept).
    landed = calls[-1]
    all_names = [ex.get("name") for s in landed["exerciseSets"] for ex in s.get("exercises", [])]
    assert any(n is None for n in all_names)


@patch("hevy2garmin.merge.time.sleep")
@patch("hevy2garmin.merge.find_matching_garmin_activity")
@patch("hevy2garmin.merge.get_activity_exercise_sets")
@patch("hevy2garmin.merge.push_exercise_sets")
def test_non_subcategory_error_is_not_retried(mock_push, mock_get, mock_find, _sleep):
    """A non-subcategory push error (e.g. network) falls back immediately with no
    stripped-names retry."""
    reset_circuit_breaker()
    act = _make_garmin_activity()
    act["manufacturer"] = "DEVELOPMENT"
    mock_find.return_value = act
    mock_get.return_value = {"exerciseSets": []}
    mock_push.side_effect = RuntimeError("connection reset by peer")

    result = attempt_merge(MagicMock(), HEVY_WORKOUT, MagicMock())

    assert result.merged is False
    assert "PUT failed" in result.fallback_reason
    mock_push.assert_called_once()   # no retry


@patch("hevy2garmin.merge.time.sleep")
@patch("hevy2garmin.merge.find_matching_garmin_activity")
@patch("hevy2garmin.merge.get_activity_exercise_sets")
@patch("hevy2garmin.merge.push_exercise_sets")
def test_failed_push_into_a_watch_activity_fails_the_workout(mock_push, mock_get, mock_find, _sleep):
    """Reporting "not merged" sends a full sync to its start-time fallback, which
    finds this same watch activity, renames it without sets and records an upload."""
    reset_circuit_breaker()
    act = _make_garmin_activity()
    act["manufacturer"] = "GARMIN"
    mock_find.return_value = act
    mock_get.return_value = {"exerciseSets": []}
    mock_push.side_effect = RuntimeError("connection reset by peer")

    with pytest.raises(MergeFailed, match="connection reset by peer"):
        attempt_merge(MagicMock(), HEVY_WORKOUT, MagicMock())
    mock_push.assert_called_once()   # no retry


class TestNamesApplied:
    """Verify whether Garmin actually kept the exercise identities (#159)."""

    @patch("hevy2garmin.merge.time.sleep")
    @patch("hevy2garmin.merge.get_activity_exercise_sets")
    def test_names_present(self, mock_get, _sleep):
        from hevy2garmin.merge import _names_applied
        mock_get.return_value = _APPLIED
        assert _names_applied(MagicMock(), 1) is True

    @patch("hevy2garmin.merge.time.sleep")
    @patch("hevy2garmin.merge.get_activity_exercise_sets")
    def test_names_dropped(self, mock_get, _sleep):
        from hevy2garmin.merge import _names_applied
        mock_get.return_value = _DROPPED
        assert _names_applied(MagicMock(), 1) is False

    @patch("hevy2garmin.merge.time.sleep")
    @patch("hevy2garmin.merge.get_activity_exercise_sets")
    def test_no_exercises_at_all(self, mock_get, _sleep):
        from hevy2garmin.merge import _names_applied
        mock_get.return_value = {"exerciseSets": []}
        assert _names_applied(MagicMock(), 1) is False

    @patch("hevy2garmin.merge.time.sleep")
    @patch("hevy2garmin.merge.get_activity_exercise_sets")
    def test_read_error_assumes_applied(self, mock_get, _sleep):
        from hevy2garmin.merge import _names_applied
        mock_get.side_effect = RuntimeError("boom")
        assert _names_applied(MagicMock(), 1) is True


# ---------------------------------------------------------------------------
# Sync integration tests
# ---------------------------------------------------------------------------

class TestSyncIntegration:
    """Test merge mode wired into sync.py."""

    WORKOUTS = [
        {
            "id": "w1", "title": "Push",
            "start_time": "2026-03-15T18:00:00+00:00", "end_time": "2026-03-15T18:45:00+00:00",
            "updated_at": "2026-03-15T18:45:00+00:00",
            "exercises": [{"title": "Bench Press (Barbell)", "sets": [{"type": "normal", "weight_kg": 60, "reps": 8}]}],
        },
        {
            "id": "w2", "title": "Pull",
            "start_time": "2026-03-16T18:00:00+00:00", "end_time": "2026-03-16T18:45:00+00:00",
            "updated_at": "2026-03-16T18:45:00+00:00",
            "exercises": [{"title": "Bent Over Row (Barbell)", "sets": [{"type": "normal", "weight_kg": 50, "reps": 10}]}],
        },
    ]

    def _mock_hevy(self):
        h = MagicMock()
        h.get_workout_count.return_value = 2
        h.get_workouts.return_value = {"workouts": self.WORKOUTS, "page_count": 1}
        return h

    @patch("hevy2garmin.sync.find_activity_by_start_time")
    @patch("hevy2garmin.sync.upload_fit")
    @patch("hevy2garmin.sync.generate_fit")
    @patch("hevy2garmin.sync.attempt_merge")
    def test_merge_only_without_a_match_touches_nothing(self, mock_merge, mock_fit, mock_upload, mock_find):
        """The webhook's staged sync: no watch activity yet means wait, never upload.

        This return is all that stands between a webhook attempt and a plain FIT
        uploaded next to a watch activity that arrives a few minutes later.
        """
        from hevy2garmin.sync import sync_one_workout

        mock_merge.return_value = MergeResult(merged=False, fallback_reason="No matching Garmin activity found")
        database = MagicMock()
        database.get_pending.return_value = None
        client = MagicMock()

        result = sync_one_workout(
            self.WORKOUTS[0],
            cfg={"hevy_api_key": "t", "merge_mode": True},
            garmin_client=client,
            merge_only=True,
            database=database,
        )

        assert result.status == "merge_pending"
        for untouched in (mock_fit, mock_upload, mock_find, database.claim_pending, database.mark_synced, client.delete_activity):
            untouched.assert_not_called()

    @patch("hevy2garmin.sync.db")
    @patch("hevy2garmin.sync.get_client")
    @patch("hevy2garmin.sync.HevyClient")
    @patch("hevy2garmin.sync.attempt_merge")
    def test_merge_on_both_match(self, mock_merge, mock_hevy_cls, mock_gclient, mock_db):
        """merge ON, both match → both use merge path."""
        mock_hevy_cls.return_value = self._mock_hevy()
        mock_gclient.return_value = MagicMock()
        mock_db.is_synced.return_value = False
        mock_merge.return_value = MergeResult(merged=True, activity_id=12345)

        from hevy2garmin.sync import sync
        stats = sync(config={"hevy_api_key": "t", "merge_mode": True}, limit=2)

        assert stats["merged"] == 2
        assert stats["merge_fallback"] == 0
        assert mock_merge.call_count == 2
        calls = mock_db.mark_synced.call_args_list
        assert all(c.kwargs.get("sync_method") == "merge" for c in calls)

    @patch("hevy2garmin.sync.db")
    @patch("hevy2garmin.sync.get_client")
    @patch("hevy2garmin.sync.HevyClient")
    @patch("hevy2garmin.sync.attempt_merge")
    @patch("hevy2garmin.sync.generate_fit", return_value={"exercises": 1, "total_sets": 1, "calories": 100, "avg_hr": 90})
    @patch("hevy2garmin.sync.upload_fit", return_value={"activity_id": 222})
    @patch("hevy2garmin.sync.find_activity_by_start_time", return_value=None)
    @patch("hevy2garmin.sync.rename_activity")
    @patch("hevy2garmin.sync.set_description")
    @patch("hevy2garmin.sync.generate_description", return_value="test")
    def test_merge_on_second_falls_back(self, *mocks):
        """merge ON, first matches, second doesn't → fallback to upload."""
        (mock_desc, mock_setdesc, mock_rename, mock_find, mock_upload,
         mock_fit, mock_merge, mock_hevy_cls, mock_gclient, mock_db) = mocks

        mock_hevy_cls.return_value = self._mock_hevy()
        mock_gclient.return_value = MagicMock()
        mock_db.is_synced.return_value = False
        call_count = [0]
        def alt(c, w, d, **kwargs):
            call_count[0] += 1
            return MergeResult(merged=True, activity_id=111) if call_count[0] == 1 else MergeResult(merged=False, fallback_reason="No match")
        mock_merge.side_effect = alt

        from hevy2garmin.sync import sync
        stats = sync(config={"hevy_api_key": "t", "merge_mode": True}, limit=2)

        assert stats["merged"] == 1
        assert stats["merge_fallback"] == 1
        calls = mock_db.mark_synced.call_args_list
        assert calls[0].kwargs.get("sync_method") == "merge"
        assert calls[1].kwargs.get("sync_method") == "upload_fallback"

    @patch("hevy2garmin.sync.db")
    @patch("hevy2garmin.sync.get_client")
    @patch("hevy2garmin.sync.HevyClient")
    @patch("hevy2garmin.sync.attempt_merge")
    @patch("hevy2garmin.sync.generate_fit", return_value={"exercises": 1, "total_sets": 1, "calories": 100, "avg_hr": 90})
    @patch("hevy2garmin.sync.upload_fit", return_value={"activity_id": 333})
    @patch("hevy2garmin.sync.find_activity_by_start_time", return_value=None)
    @patch("hevy2garmin.sync.rename_activity")
    @patch("hevy2garmin.sync.set_description")
    @patch("hevy2garmin.sync.generate_description", return_value="test")
    def test_merge_off_normal_upload(self, *mocks):
        """merge OFF → normal upload, merge never attempted."""
        (mock_desc, mock_setdesc, mock_rename, mock_find, mock_upload,
         mock_fit, mock_merge, mock_hevy_cls, mock_gclient, mock_db) = mocks

        mock_hevy_cls.return_value = self._mock_hevy()
        mock_gclient.return_value = MagicMock()
        mock_db.is_synced.return_value = False

        from hevy2garmin.sync import sync
        stats = sync(config={"hevy_api_key": "t", "merge_mode": False}, limit=2)

        assert stats["merged"] == 0
        assert mock_merge.call_count == 0
        calls = mock_db.mark_synced.call_args_list
        assert all(c.kwargs.get("sync_method") == "upload" for c in calls)
