"""Config flow for Korfbal Schedule integration."""

from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from . import DOMAIN
from .api import (
    ClubOption,
    DiscoveryEmptyError,
    DiscoveryError,
    DiscoveryHttpError,
    DiscoveryNetworkError,
    DiscoveryResponseError,
    TeamOption,
    async_fetch_clubs,
    async_fetch_teams,
)

_LOGGER = logging.getLogger(__name__)

CONF_TEAM_CODE = "team_code"
CONF_CLUB_CODE = "club_code"
CONF_TEAM_NAME = "team_name"
CONF_SPORTLINK_CLIENT_ID = "sportlink_client_id"

EXAMPLE_TEAM_URL = (
    "https://mijn.korfbal.nl/team/details/NCX35C2/T1200100098/programma"
)


class KorfbalScheduleConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Korfbal Schedule."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize a config flow."""
        super().__init__()
        self._clubs: list[ClubOption] = []
        self._teams: list[TeamOption] = []
        self._selected_club_code = ""
        self._selected_team_code = ""
        self._selected_team_name = ""

    async def async_step_user(
        self, user_input: dict | None = None
    ) -> FlowResult:
        """Show the setup method choices."""
        return self.async_show_menu(
            step_id="user",
            menu_options=["browse", "manual"],
        )

    async def async_step_browse(
        self, user_input: dict | None = None
    ) -> FlowResult:
        """Select a club and load its teams."""
        if user_input is not None:
            club_code = str(user_input.get(CONF_CLUB_CODE, "")).strip()
            if not any(club.code == club_code for club in self._clubs):
                return self._show_browse_form("invalid_selection")

            self._selected_club_code = club_code
            try:
                self._teams = await async_fetch_teams(self.hass, club_code)
            except DiscoveryEmptyError:
                return self._show_browse_form("no_teams")
            except DiscoveryError as err:
                _LOGGER.warning(
                    "Could not discover teams for club %s: %s", club_code, err
                )
                return self._show_browse_form(_discovery_error_key(err))
            except Exception:
                _LOGGER.exception("Unexpected error discovering teams for club %s", club_code)
                return self._show_browse_form("unknown")

            return await self.async_step_team()

        if not self._clubs:
            try:
                self._clubs = await async_fetch_clubs(self.hass)
            except DiscoveryEmptyError:
                return self._show_browse_form("no_clubs")
            except DiscoveryError as err:
                _LOGGER.warning("Could not discover Korfbal clubs: %s", err)
                return self._show_browse_form(_discovery_error_key(err))
            except Exception:
                _LOGGER.exception("Unexpected error discovering Korfbal clubs")
                return self._show_browse_form("unknown")

        return self._show_browse_form()

    async def async_step_team(
        self, user_input: dict | None = None
    ) -> FlowResult:
        """Select a team from the selected club."""
        if not self._selected_club_code or not self._teams:
            return await self.async_step_browse()

        if user_input is not None:
            team_code = str(user_input.get(CONF_TEAM_CODE, "")).strip()
            selected_team = next(
                (team for team in self._teams if team.code == team_code), None
            )
            if selected_team is None:
                return self._show_team_form("invalid_selection")

            self._selected_team_code = selected_team.code
            self._selected_team_name = selected_team.name
            return await self.async_step_settings()

        return self._show_team_form()

    async def async_step_settings(
        self, user_input: dict | None = None
    ) -> FlowResult:
        """Collect the display name and optional Sportlink client ID."""
        if not self._selected_club_code or not self._selected_team_code:
            return await self.async_step_browse()

        if user_input is not None:
            entered_name = str(user_input.get(CONF_TEAM_NAME, "")).strip()
            team_name = entered_name or self._selected_team_name or self._selected_team_code
            data = {
                CONF_CLUB_CODE: self._selected_club_code,
                CONF_TEAM_CODE: self._selected_team_code,
                CONF_TEAM_NAME: team_name,
                CONF_SPORTLINK_CLIENT_ID: str(
                    user_input.get(CONF_SPORTLINK_CLIENT_ID, "")
                ).strip(),
            }
            return await self._async_create_entry(data)

        return self.async_show_form(
            step_id="settings",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_TEAM_NAME,
                        default=self._selected_team_name or self._selected_team_code,
                    ): str,
                    vol.Optional(CONF_SPORTLINK_CLIENT_ID, default=""): str,
                }
            ),
        )

    async def async_step_manual(
        self, user_input: dict | None = None
    ) -> FlowResult:
        """Configure a team using its codes from the team URL."""
        if user_input is not None:
            club_code = str(user_input[CONF_CLUB_CODE]).strip()
            team_code = str(user_input[CONF_TEAM_CODE]).strip()
            team_name = str(user_input.get(CONF_TEAM_NAME, "")).strip() or team_code
            data = {
                CONF_CLUB_CODE: club_code,
                CONF_TEAM_CODE: team_code,
                CONF_TEAM_NAME: team_name,
                CONF_SPORTLINK_CLIENT_ID: str(
                    user_input.get(CONF_SPORTLINK_CLIENT_ID, "")
                ).strip(),
            }
            return await self._async_create_entry(data)

        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_CLUB_CODE): str,
                    vol.Required(CONF_TEAM_CODE): str,
                    vol.Optional(CONF_TEAM_NAME, default=""): str,
                    vol.Optional(CONF_SPORTLINK_CLIENT_ID, default=""): str,
                }
            ),
            description_placeholders={"url": EXAMPLE_TEAM_URL},
        )

    async def _async_create_entry(self, data: dict[str, str]) -> FlowResult:
        """Create an entry after all required identity data is available."""
        await self.async_set_unique_id(
            f"{data[CONF_CLUB_CODE]}_{data[CONF_TEAM_CODE]}"
        )
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=data[CONF_TEAM_NAME] or data[CONF_TEAM_CODE],
            data=data,
        )

    def _show_browse_form(self, error: str | None = None) -> FlowResult:
        """Show the club selector or a discovery error with manual guidance."""
        schema = vol.Schema({})
        if self._clubs:
            schema = vol.Schema(
                {
                    vol.Required(CONF_CLUB_CODE): _select_selector(
                        [
                            {"value": club.code, "label": club.label}
                            for club in self._clubs
                        ]
                    )
                }
            )
        return self.async_show_form(
            step_id="browse",
            data_schema=schema,
            errors={"base": error} if error else {},
            description_placeholders={"url": EXAMPLE_TEAM_URL},
        )

    def _show_team_form(self, error: str | None = None) -> FlowResult:
        """Show the team selector for the selected club."""
        return self.async_show_form(
            step_id="team",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_TEAM_CODE): _select_selector(
                        [
                            {"value": team.code, "label": team.label}
                            for team in self._teams
                        ]
                    )
                }
            ),
            errors={"base": error} if error else {},
        )


def _select_selector(options: list[dict[str, str]]) -> SelectSelector:
    """Create a compact dropdown selector with stable submitted values."""
    return SelectSelector(
        SelectSelectorConfig(
            options=options,
            mode=SelectSelectorMode.DROPDOWN,
        )
    )


def _discovery_error_key(error: DiscoveryError) -> str:
    """Map API failures to translated config-flow error keys."""
    if isinstance(error, DiscoveryNetworkError):
        return "cannot_connect"
    if isinstance(error, DiscoveryHttpError):
        return "api_error"
    if isinstance(error, DiscoveryResponseError):
        return "invalid_response"
    return "unknown"
