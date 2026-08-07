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