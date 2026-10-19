"""Tests for Mijn Korfbal schedule parsing and requests."""

from __future__ import annotations

import pytest

pytest.importorskip("homeassistant")

from custom_components.korfbal_schedule.coordinator import (
    KorfbalScheduleCoordinator,
)


class _Response:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args) -> None:
        return None

    def raise_for_status(self) -> None:
        return None

    async def json(self, content_type=None):
        return self.payload


class _Session:
    def __init__(self, payloads: list[dict]) -> None:
        self.payloads = iter(payloads)
        self.requests: list[dict] = []

    def get(self, url: str, *, headers: dict, params: dict, timeout):
        self.requests.append(
            {"url": url, "headers": headers, "params": params, "timeout": timeout}
        )
        return _Response(next(self.payloads))


def _coordinator() -> KorfbalScheduleCoordinator:
    coordinator = object.__new__(KorfbalScheduleCoordinator)
    coordinator.club_code = "NCX35C2"
    coordinator.team_code = "T385260525"
    coordinator.pool_code = "104892"
    coordinator.team_name = "KVZ '23 J1"
    return coordinator


def test_parse_current_competition_match_row() -> None:
    coordinator = _coordinator()

    match = coordinator._parse_mijn_korfbal_match(
        {
            "ref_id": 12345,
            "datetime": "2026-04-18T12:30:00+02:00",
            "date": "2026-04-18",
            "time": "12:30:00",
            "time_display": "12:30",
            "homeTeam": {
                "teamName": "KVZ '23 J1",
                "ref_id": "T385260525",
                "score": 12,
            },
            "awayTeam": {"teamName": "Opponent", "ref_id": "T2", "score": 10},
            "pool": {"name": "Ro-045", "ref_id": 104892},
            "facility": {
                "name": "Sporthal",
                "address": {"city": "Arnhem"},
            },
            "status": {"game": "Uitgespeeld", "status": "FINAL"},
            "stats": {"home": {"score": 12}, "away": {"score": 10}},
        },
        is_results=True,
    )

    assert match is not None
    assert match.match_id == "12345"
    assert match.home_team == "KVZ '23 J1"
    assert match.away_team == "Opponent"
    assert match.start.isoformat() == "2026-04-18T12:30:00+02:00"
    assert match.location == "Sporthal, Arnhem"
    assert match.competition == "Ro-045"
    assert match.status == "played"
    assert (match.home_score, match.away_score) == (12, 10)


@pytest.mark.asyncio
async def test_fetch_uses_selected_team_and_pool_for_both_endpoints() -> None:
    coordinator = _coordinator()
    session = _Session(
        [
            {"draw": 1, "recordsTotal": 0, "recordsFiltered": 0, "data": []},
            {"draw": 1, "recordsTotal": 0, "recordsFiltered": 0, "data": []},
        ]
    )

    matches = await coordinator._fetch_mijn_korfbal(session)

    assert matches == []
    assert [request["url"] for request in session.requests] == [
        "https://api-saas-site-prod-236.dotlab.net/general/api/competition/"
        "clubs/NCX35C2/program",
        "https://api-saas-site-prod-236.dotlab.net/general/api/competition/"
        "clubs/NCX35C2/results",
    ]
    for request in session.requests:
        assert request["params"]["team"] == "T385260525"
        assert request["params"]["pool"] == "104892"
    assert session.requests[1]["params"]["_dateFrom"] == "2000-01-01"


@pytest.mark.asyncio
async def test_fetch_endpoint_reads_all_pages() -> None:
    coordinator = _coordinator()
    session = _Session(
        [
            {"draw": 1, "recordsTotal": 2, "recordsFiltered": 2, "data": [{"id": 1}]},
            {"draw": 2, "recordsTotal": 2, "recordsFiltered": 2, "data": [{"id": 2}]},
        ]
    )

    rows = await coordinator._fetch_mijn_korfbal_endpoint(
        session,
        "program",
        {"team": "T385260525", "pool": "104892"},
        {"Accept": "application/json"},
    )

    assert rows == [{"id": 1}, {"id": 2}]
    assert [request["params"]["start"] for request in session.requests] == ["0", "1"]
