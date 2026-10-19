"""Korfbal Schedule integration for Home Assistant.

Scrapes match schedule from mijn.korfbal.nl (Sportlink) and exposes
upcoming matches as calendar events and sensor attributes.

URL pattern: https://mijn.korfbal.nl/competitie-vereniging/?club={CLUB_CODE}&team={TEAM_CODE}&pool={POOL_ID}
Example: https://mijn.korfbal.nl/competitie-vereniging/?club=NCX35C2&team=T385260525&pool=104892
"""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .coordinator import KorfbalScheduleCoordinator

_LOGGER = logging.getLogger(__name__)

DOMAIN = "korfbal_schedule"
PLATFORMS = [Platform.CALENDAR, Platform.SENSOR]
SCAN_INTERVAL = timedelta(hours=6)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Korfbal Schedule from a config entry."""
    coordinator = KorfbalScheduleCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
