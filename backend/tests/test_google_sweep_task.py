"""Google sweep task mechanics, without a database.

Covers the pieces that keep a twice-monthly fine-grid sweep correct and fast:
the O(1) candidate append (no quadratic reload), the offset passthrough, the
distinct job kinds for the two runs, the per-task time-limit override, and the
beat schedule wiring.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.ingestion.base import RawPlace
from app.ingestion.pipeline import candidate_from_place
from app.models.enums import SourceType
from app.services.entity_resolution import CandidateRestaurant


def _place(external_id: str = "ChIJx", name: str = "Test Kitchen") -> RawPlace:
    return RawPlace(
        source=SourceType.GOOGLE,
        external_id=external_id,
        name=name,
        lat=22.55,
        lng=88.35,
        rating=4.2,
        rating_count=10,
    )


def _restaurant(rid: str = "r1", name: str = "Test Kitchen") -> SimpleNamespace:
    return SimpleNamespace(id=rid, name=name, normalized_name=name.lower(), lat=22.55, lng=88.35)


class TestCandidateFromPlace:
    def test_builds_a_candidate_with_the_new_source_key(self):
        cand = candidate_from_place(_restaurant(), _place("ChIJabc"))
        assert isinstance(cand, CandidateRestaurant)
        assert cand.id == "r1"
        assert cand.normalized_name == "test kitchen"
        assert cand.source_keys == (("google", "ChIJabc"),)
        assert cand.aliases == ()

    def test_appended_candidate_resolves_by_source_key(self):
        """The whole point: a later place with the same id matches the appended row."""
        from app.services.entity_resolution import IncomingPlace, MatchMethod, resolve_candidate

        cand = candidate_from_place(_restaurant("r9"), _place("ChIJdup"))
        result = resolve_candidate(
            IncomingPlace(
                name="Totally Different Spelling",
                lat=22.6,
                lng=88.4,
                source="google",
                external_id="ChIJdup",
            ),
            [cand],
        )
        assert result.matched_id == "r9"
        assert result.method is MatchMethod.SOURCE_KEY


class TestGoogleSweepTask:
    """Run the real task body against fakes to prove the ingest loop mechanics."""

    def _run(self, monkeypatch, *, offset: bool, actions: list[str]):
        from app.workers import ingestion_tasks as tasks

        seen: dict = {"offset": None, "candidates": None, "appended": 0, "kind": None}

        city = SimpleNamespace(
            id="city-1", slug="kolkata", name="Kolkata", lat=22.57, lng=88.36, radius_m=25000
        )

        class _Ctx:
            def __enter__(self):
                return SimpleNamespace(commit=lambda: None, flush=lambda: None)

            def __exit__(self, *exc):
                return False

        monkeypatch.setattr(tasks, "sync_session", lambda: _Ctx())

        adapter = SimpleNamespace(enabled=True)
        adapter.discover_places = AsyncMock(
            return_value=[_place(f"p{i}") for i in range(len(actions))]
        )
        monkeypatch.setattr(tasks, "get_adapter", lambda source: adapter)
        monkeypatch.setattr(tasks, "_active_cities", lambda session: [city])

        def fake_claim(session, source, c, params, kind=None, interval=None):
            seen["kind"] = kind
            return SimpleNamespace()

        monkeypatch.setattr(tasks, "_claim_job", fake_claim)
        monkeypatch.setattr(tasks, "load_candidates", lambda session, city_id: [])

        created = [_restaurant(f"r{i}") for i in range(len(actions))]

        def fake_upsert(session, c, place, candidates=None):
            seen["candidates"] = candidates
            action = actions.pop(0)
            if action == "created":
                return created.pop(0), "created"
            return None, action

        monkeypatch.setattr(tasks, "upsert_place", fake_upsert)

        real_append = tasks.candidate_from_place

        def counting_from_place(restaurant, place):
            seen["appended"] += 1
            return real_append(restaurant, place)

        monkeypatch.setattr(tasks, "candidate_from_place", counting_from_place)
        monkeypatch.setattr(tasks, "_finish_job", lambda job, counters, status, error=None: None)

        totals = tasks.discover_google_places.run(city_slug="kolkata", offset=offset)
        seen["offset"] = adapter.discover_places.await_args.kwargs.get("offset")
        seen["totals"] = totals
        return seen

    def test_offset_is_passed_to_the_adapter(self, monkeypatch):
        seen = self._run(monkeypatch, offset=True, actions=["created"])
        assert seen["offset"] is True

    def test_base_run_does_not_offset(self, monkeypatch):
        seen = self._run(monkeypatch, offset=False, actions=["created"])
        assert seen["offset"] is False

    def test_offset_run_claims_a_distinct_kind(self, monkeypatch):
        assert self._run(monkeypatch, offset=True, actions=["created"])["kind"] == (
            "discover_places_offset"
        )
        assert self._run(monkeypatch, offset=False, actions=["created"])["kind"] == (
            "discover_places"
        )

    def test_created_places_are_appended_to_candidates(self, monkeypatch):
        seen = self._run(monkeypatch, offset=False, actions=["created", "created", "skipped"])
        assert seen["appended"] == 2
        assert len(seen["candidates"]) == 2
        assert seen["totals"]["kolkata"]["created"] == 2
        assert seen["totals"]["kolkata"]["skipped"] == 1


class TestGoogleSweepTaskConfig:
    def test_time_limit_is_overridden_for_the_long_sweep(self):
        from app.workers.ingestion_tasks import discover_google_places

        assert discover_google_places.time_limit == 16 * 3600
        assert discover_google_places.soft_time_limit == 16 * 3600 - 300

    def test_other_tasks_keep_the_global_time_limit(self):
        from app.workers.ingestion_tasks import discover_places

        # Unset on the task → falls back to the app-wide 1800s limit.
        assert discover_places.time_limit is None

    def test_beat_schedule_has_both_monthly_runs(self):
        from app.workers.celery_app import celery_app

        schedule = celery_app.conf.beat_schedule
        base = schedule["discover-google-places"]
        offset = schedule["discover-google-places-offset"]

        assert base["task"] == "ingestion.discover_google_places"
        assert offset["task"] == "ingestion.discover_google_places"
        assert offset["kwargs"] == {"offset": True}
        assert base["schedule"].day_of_month == {1}
        assert offset["schedule"].day_of_month == {15}
        assert base["schedule"].hour == {3}
        assert offset["schedule"].hour == {3}
