"""Tests for the Korfbal Schedule config flow."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

pytest.importorskip("homeassistant")
pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.korfbal_schedule import DOMAIN
from custom_components.korfbal_schedule import config_flow
from custom_components.korfbal_schedule.api import (
    ClubOption,
    DiscoveryNetworkError,
    TeamOption,
)


async def _select_menu(hass, menu_result, step: str):
    """Select one of the initial config-flow menu options."""
    return await hass.config_entries.flow.async_configure(
        menu_result["flow_id"], {"next_step_id": step}
    )


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_browse_flow_preserves_selected_ids_and_custom_name(
    hass, monkeypatch
) -> None:
    """Browse selection loads dependent teams and preserves optional settings."""
    monkeypatch.setattr(
        config_flow,
        "async_fetch_clubs",
        AsyncMock(return_value=[ClubOption("C1", "Club One", "Club One")]),
    )
    monkeypatch.setattr(
        config_flow,
        "async_fetch_teams",
        AsyncMock(return_value=[TeamOption("T1", "Team One", "Team One")]),
    )

    menu_result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )
    browse_result = await _select_menu(hass, menu_result, "browse")
    team_result = await hass.config_entries.flow.async_configure(
        browse_result["flow_id"], {"club_code": "C1"}
    )
    settings_result = await hass.config_entries.flow.async_configure(
        team_result["flow_id"], {"team_code": "T1"}
    )
    create_result = await hass.config_entries.flow.async_configure(
        settings_result["flow_id"],
        {"team_name": "Mijn Team", "sportlink_client_id": ""},
    )

    assert create_result["type"] is FlowResultType.CREATE_ENTRY
    assert create_result["title"] == "Mijn Team"
    assert create_result["data"] == {
        "club_code": "C1",
        "team_code": "T1",
        "team_name": "Mijn Team",
        "sportlink_client_id": "",
    }
    assert config_flow.async_fetch_teams.await_args.args[1] == "C1"


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_browse_flow_accepts_display_labels(hass, monkeypatch) -> None:
    """Display labels from older frontend clients resolve to stable IDs."""
    monkeypatch.setattr(
        config_flow,
        "async_fetch_clubs",
        AsyncMock(return_value=[ClubOption("C1", "Club One", "Club One")]),
    )
    monkeypatch.setattr(
        config_flow,
        "async_fetch_teams",
        AsyncMock(return_value=[TeamOption("T1", "Team One", "Team One")]),
    )

    menu_result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )
    browse_result = await _select_menu(hass, menu_result, "browse")
    team_result = await hass.config_entries.flow.async_configure(
        browse_result["flow_id"], {"club_code": "Club One"}
    )
    team_selector = next(iter(team_result["data_schema"].schema.values()))

    assert team_result["type"] is FlowResultType.FORM
    assert team_result["step_id"] == "team"
    assert team_result["errors"] == {}
    assert team_selector.config["options"] == [
        {"value": "T1", "label": "Team One"}
    ]

    settings_result = await hass.config_entries.flow.async_configure(
        team_result["flow_id"], {"team_code": "Team One"}
    )

    assert settings_result["type"] is FlowResultType.FORM
    assert settings_result["step_id"] == "settings"
    assert config_flow.async_fetch_teams.await_args.args[1] == "C1"


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_manual_flow_keeps_legacy_data_shape(hass) -> None:
    """Manual setup remains available and stores the legacy fields."""
    menu_result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )
    manual_result = await _select_menu(hass, menu_result, "manual")
    create_result = await hass.config_entries.flow.async_configure(
        manual_result["flow_id"],
        {
            "club_code": "C1",
            "team_code": "T1",
            "team_name": "",
            "sportlink_client_id": "CLIENT1",
        },
    )

    assert create_result["type"] is FlowResultType.CREATE_ENTRY
    assert create_result["data"] == {
        "club_code": "C1",
        "team_code": "T1",
        "team_name": "T1",
        "sportlink_client_id": "CLIENT1",
    }


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_browse_failure_explains_manual_fallback(hass, monkeypatch) -> None:
    """A discovery outage stays in the flow and exposes a translated error."""
    monkeypatch.setattr(
        config_flow,
        "async_fetch_clubs",
        AsyncMock(side_effect=DiscoveryNetworkError("offline")),
    )

    menu_result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )
    browse_result = await _select_menu(hass, menu_result, "browse")

    assert browse_result["type"] is FlowResultType.FORM
    assert browse_result["step_id"] == "browse"
    assert browse_result["errors"] == {"base": "cannot_connect"}


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_duplicate_manual_entry_aborts(hass) -> None:
    """The unique ID remains club-code plus team-code for compatibility."""
    existing = MockConfigEntry(
        domain=DOMAIN,
        unique_id="C1_T1",
        data={
            "club_code": "C1",
            "team_code": "T1",
            "team_name": "Existing",
            "sportlink_client_id": "",
        },
    )
    existing.add_to_hass(hass)

    menu_result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )
    manual_result = await _select_menu(hass, menu_result, "manual")
    abort_result = await hass.config_entries.flow.async_configure(
        manual_result["flow_id"],
        {"club_code": "C1", "team_code": "T1", "team_name": "Duplicate"},
    )

    assert abort_result["type"] is FlowResultType.ABORT
    assert abort_result["reason"] == "already_configured"