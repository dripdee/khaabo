"""Wikidata enricher tests.

External calls are mocked — what is verified is the parsing of the SPARQL
result rows and the alias policy (dedup + generic-token guard). Attachment
behaviour is covered by the mention-extraction tests.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import httpx

from app.models.enums import SourceType
from scripts.enrich_aliases import _attach_aliases, fetch_wikidata_places


def _sparql_bindings(qid: str = "Q4732", label: str = "Arsalan") -> list[dict]:
    label_row = {
        "item": {"value": f"http://www.wikidata.org/entity/{qid}"},
        "label": {"value": label},
        "lat": {"value": "22.5726"},
        "lng": {"value": "88.3639"},
    }
    alias_row = {
        **label_row,
        "alias": {"value": "Arsalan Park Circus"},
    }
    return [label_row, alias_row]


def _mock_client(monkeypatch, payload: dict) -> None:
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json = MagicMock(return_value=payload)
    client = AsyncMock()
    client.get = AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client)


class TestParseWikidataResponse:
    async def test_label_and_alias_are_collected(self, monkeypatch):
        _mock_client(monkeypatch, {"head": {}, "results": {"bindings": _sparql_bindings()}})

        places = await fetch_wikidata_places("Q1348")

        assert len(places) == 1
        place = places[0]
        assert place.qid == "Q4732"
        assert place.label == "Arsalan"
        assert place.aliases == {"Arsalan Park Circus"}

    async def test_alias_equal_to_label_is_not_double_counted(self, monkeypatch):
        row = _sparql_bindings()[0]
        row["alias"] = {"value": "Arsalan"}
        _mock_client(monkeypatch, {"head": {}, "results": {"bindings": [row]}})

        places = await fetch_wikidata_places("Q1348")
        assert places[0].aliases == set()

    async def test_places_without_coordinates_are_kept_for_alias_matching(self, monkeypatch):
        row = _sparql_bindings()[0]
        del row["lat"]
        del row["lng"]
        _mock_client(monkeypatch, {"head": {}, "results": {"bindings": [row]}})

        places = await fetch_wikidata_places("Q1348")
        assert places[0].lat is None
        assert places[0].lng is None

    async def test_empty_results_yield_no_places(self, monkeypatch):
        _mock_client(monkeypatch, {"head": {}, "results": {"bindings": []}})
        assert await fetch_wikidata_places("Q1348") == []

    async def test_network_failure_is_swallowed_not_raised(self, monkeypatch):
        """A flaky SPARQL endpoint must not kill a scheduled enrichment run."""
        client = AsyncMock()
        client.get = AsyncMock(side_effect=httpx.ConnectError("unreachable"))
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client)

        assert await fetch_wikidata_places("Q1348") == []


class _FakeAliasRow:
    def __init__(self, restaurant_id, alias, normalized_alias, confidence):
        self.restaurant_id = restaurant_id
        self.alias = alias
        self.normalized_alias = normalized_alias
        self.confidence = confidence
        self.source = SourceType.MANUAL


class _FakeRestaurant:
    def __init__(self):
        self.id = uuid.uuid4()
        self.normalized_name = "arsalan"
        self.aliases = []


class _FakeSession:
    """Just enough of the SQLAlchemy Session surface for _attach_aliases."""

    def __init__(self, existing: set[str]):
        self._existing = existing
        self.added: list[_FakeAliasRow] = []

    def add(self, row):
        self.added.append(row)

    @property
    def query(self):
        outer = self

        class _Q:
            def filter_by(self, **kwargs):
                target = kwargs.get("normalized_alias")

                class _R:
                    def first(_self):
                        return (object(),) if target in outer._existing else None

                return _R()

        return lambda *cols: _Q()


class TestAliasPolicy:
    def test_own_name_and_generic_labels_are_never_seeded(self):
        session = _FakeSession(existing=set())
        restaurant = _FakeRestaurant()

        added = _attach_aliases(
            session,
            restaurant,
            {"Arsalan", "Arsalan Park Circus", "kolkata", "momo"},
        )

        assert added == 1
        row = session.added[0]
        assert row.normalized_alias == "arsalan park circus"
        assert row.confidence > 0
        assert not any(r.normalized_alias == "arsalan" for r in session.added)
        assert not any(r.normalized_alias == "kolkata" for r in session.added)

    def test_existing_alias_rows_are_not_duplicated(self):
        session = _FakeSession(existing={"arsalan park circus"})
        restaurant = _FakeRestaurant()

        added = _attach_aliases(session, restaurant, {"Arsalan Park Circus"})

        assert added == 0
