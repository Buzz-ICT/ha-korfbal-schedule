"""Tests for Mijn Korfbal discovery parsing."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def api():
    """Load the parser module without requiring a Home Assistant install."""
    module_path = (
        Path(__file__).parents[1]
        / "custom_components"
        / "korfbal_schedule"
        / "api.py"
    )
    spec = importlib.util.spec_from_file_location("korfbal_discovery_api", module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_parse_grouped_clubs_preserves_ids_and_sorts(api) -> None:
    """Grouped club responses become sorted selector options."""
    payload = [
        {
            "letter": "K",
            "list": [
                {"name": "KVZ", "ref_id": "C2"},
                {"name": "Korfbal Club", "ref_id": "C1"},
            ],
        }
    ]

    clubs = api.parse_clubs(payload)

    assert [(club.code, club.label) for club in clubs] == [
        ("C1", "Korfbal Club"),
        ("C2", "KVZ"),
    ]


def test_duplicate_club_names_include_location(api) -> None:
    """Duplicate club names receive a useful location suffix."""
    payload = [
        {
            "clubs": [
                {
                    "name": "Korfbal Vereniging",
                    "ref_id": "C1",
                    "address": {"city": "Arnhem"},
                },
                {
                    "name": "Korfbal Vereniging",
                    "ref_id": "C2",
                    "address": {"city": "Zwolle"},
                },
            ]
        }
    ]

    clubs = api.parse_clubs(payload)

    assert [club.label for club in clubs] == [
        "Korfbal Vereniging (Arnhem)",
        "Korfbal Vereniging (Zwolle)",
    ]
    assert [club.code for club in clubs] == ["C1", "C2"]


def test_duplicate_team_names_include_age_group(api) -> None:
    """Grouped team names are disambiguated with their inherited age group."""
    payload = [
        {
            "teams": [{"name": "KVZ 1", "ref_id": "T1"}],
            "age": {"name": "Senioren", "ref_id": "011"},
        },
        {
            "teams": [{"name": "KVZ 1", "ref_id": "T2"}],
            "age": {"name": "A-jeugd", "ref_id": "012"},
        },
    ]

    teams = api.parse_teams(payload)

    assert [team.code for team in teams] == ["T2", "T1"]
    assert [team.label for team in teams] == [
        "KVZ 1 (A-jeugd)",
        "KVZ 1 (Senioren)",
    ]


def test_current_competition_api_team_records_keep_pool_ids(api) -> None:
    """Current grouped team responses retain the selected competition pool."""
    payload = {
        "success": True,
        "statusCode": 200,
        "data": [
            {
                "teams": [
                    {
                        "name": "KVZ '23 J1",
                        "ref_id": "T385260525",
                        "pool": {"name": "Ro-045", "ref_id": 104892},
                        "sport": {"name": "Zaal Week"},
                    }
                ]
            }
        ],
    }

    teams = api.parse_teams(payload)

    assert len(teams) == 1
    assert teams[0].code == "T385260525"
    assert teams[0].pool_code == "104892"
    assert teams[0].pool_name == "Ro-045"


@pytest.mark.asyncio
async def test_discovery_calls_current_competition_endpoints(api, monkeypatch) -> None:
    """Club and team selection use the endpoints currently used by the site."""
    responses = iter(
        [
            {"success": True, "data": [{"name": "Club", "ref_id": "NCX1"}]},
            {
                "success": True,
                "data": [
                    {
                        "teams": [
                            {
                                "name": "Team",
                                "ref_id": "T1",
                                "pool": {"name": "Poule", "ref_id": 104892},
                            }
                        ]
                    }
                ],
            },
        ]
    )
    requested_urls: list[str] = []

    async def fetch_json(_hass, url: str):
        requested_urls.append(url)
        return next(responses)

    monkeypatch.setattr(api, "_async_get_json", fetch_json)

    clubs = await api.async_fetch_clubs(None)
    teams = await api.async_fetch_teams(None, "NCX1")

    assert clubs[0].code == "NCX1"
    assert teams[0].pool_code == "104892"
    assert requested_urls == [
        f"{api.MIJN_KORFBAL_API}/clubs",
        f"{api.MIJN_KORFBAL_API}/clubs/NCX1/teams",
    ]


def test_empty_responses_are_valid_parser_results(api) -> None:
    """An empty but well-shaped response is distinct from malformed data."""
    assert api.parse_clubs([]) == []
    assert api.parse_teams({"teams": []}) == []


def test_malformed_responses_raise_explicit_error(api) -> None:
    """Unexpected outer shapes do not silently create empty selectors."""
    with pytest.raises(api.DiscoveryResponseError):
        api.parse_clubs("not-json-data")

    with pytest.raises(api.DiscoveryResponseError):
        api.parse_teams({"message": "temporarily unavailable"})