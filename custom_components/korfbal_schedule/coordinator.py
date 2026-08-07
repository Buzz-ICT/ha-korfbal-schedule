"""Data coordinator for Korfbal Schedule.

Two fetch strategies:
1. Sportlink Club.Dataservice API  — used when `sportlink_client_id` is set.
   Endpoint: https://data.sportlink.com/programma
   Docs: https://sportlinkservices.freshdesk.com/nl/support/solutions/articles/9000062942

2. Mijn Korfbal REST API (default, no API key required).
   Base URL: https://api-mijn.korfbal.nl/api/v2
   Program:  GET /clubs/{clubCode}/program?dateFrom=YYYY-MM-DD&dateTo=YYYY-MM-DD
   Results:  GET /clubs/{clubCode}/results?dateFrom=YYYY-MM-DD&dateTo=YYYY-MM-DD
   Response: list of week-objects [{year, week, matches: [...]}]
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import MIJN_KORFBAL_API

_LOGGER = logging.getLogger(__name__)

TZ_NL = ZoneInfo("Europe/Amsterdam")
SCAN_INTERVAL = timedelta(hours=6)

# Sportlink Club.Dataservice base URL
SPORTLINK_API_BASE = "https://data.sportlink.com"

@dataclass
class KorfbalMatch:
    """Represents a single korfball match."""

    match_id: str
    home_team: str
    away_team: str
    start: datetime
    end: datetime
    location: str
    competition: str
    status: str  # "scheduled", "cancelled", "played"
    home_score: int | None
    away_score: int | None

    @property
    def summary(self) -> str:
        """Calendar event summary."""
        return f"🏐 {self.home_team} – {self.away_team}"

    @property
    def description(self) -> str:
        """Calendar event description."""
        lines = [
            f"Competitie: {self.competition}",
            f"Locatie: {self.location}",
            f"Status: {self.status}",
        ]
        if self.home_score is not None:
            lines.append(f"Uitslag: {self.home_score}–{self.away_score}")
        return "\n".join(lines)


class KorfbalScheduleCoordinator(DataUpdateCoordinator[list[KorfbalMatch]]):
    """Coordinator that fetches match data."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="Korfbal Schedule",
            update_interval=SCAN_INTERVAL,
        )
        self.entry = entry
        self.team_code: str = entry.data["team_code"]
        self.club_code: str = entry.data["club_code"]
        self.client_id: str = entry.data.get("sportlink_client_id", "")
        self.team_name: str = entry.data.get("team_name", self.team_code)

    async def _async_update_data(self) -> list[KorfbalMatch]:
        """Fetch data from either Sportlink API or scraper fallback."""
        session = async_get_clientsession(self.hass)
        try:
            if self.client_id:
                return await self._fetch_sportlink_api(session)
            else:
                return await self._fetch_mijn_korfbal(session)
        except aiohttp.ClientError as err:
            raise UpdateFailed(f"Network error fetching korfbal schedule: {err}") from err

    # ------------------------------------------------------------------ #
    # Strategy 1: Official Sportlink Club.Dataservice API                 #
    # ------------------------------------------------------------------ #
    async def _fetch_sportlink_api(self, session: aiohttp.ClientSession) -> list[KorfbalMatch]:
        """Fetch schedule via the Sportlink Club.Dataservice API.

        Requires a valid clientId obtained from Sportlink Services.
        The 'programma' endpoint returns upcoming fixtures.
        """
        params = {
            "clientId": self.client_id,
            "teamcode": self.team_code,
            "aantaldagen": 90,          # look ahead 90 days
            "sorteervolgorde": "datum-tijd",
            "eigenwedstrijden": "ja",
            "gebruiklokaleteamnaam": "ja",
        }
        url = f"{SPORTLINK_API_BASE}/programma"
        _LOGGER.debug("Fetching Sportlink API: %s params=%s", url, params)

        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            resp.raise_for_status()
            data = await resp.json(content_type=None)

        matches: list[KorfbalMatch] = []
        for item in data if isinstance(data, list) else []:
            match = self._parse_sportlink_match(item)
            if match:
                matches.append(match)
        _LOGGER.info("Fetched %d matches via Sportlink API", len(matches))
        return matches

    def _parse_sportlink_match(self, item: dict[str, Any]) -> KorfbalMatch | None:
        """Parse one Sportlink API match object."""
        try:
            # Sportlink returns datum as "DD-MM-YYYY" and aanvangstijd as "HH:MM"
            raw_date = item.get("datum", "")
            raw_time = item.get("aanvangstijd", "00:00")
            if not raw_date:
                return None

            # Parse Dutch date format
            day, month, year = raw_date.split("-")
            hour, minute = raw_time.split(":")
            start = datetime(
                int(year), int(month), int(day),
                int(hour), int(minute),
                tzinfo=TZ_NL,
            )
            end = start + timedelta(hours=1, minutes=30)

            home_score = item.get("thuisscore")
            away_score = item.get("uitscore")

            return KorfbalMatch(
                match_id=str(item.get("wedstrijdcode", f"{raw_date}-{raw_time}")),
                home_team=item.get("thuisteam", "Thuis"),
                away_team=item.get("uitteam", "Uit"),
                start=start,
                end=end,
                location=item.get("accommodatie", item.get("plaats", "Onbekend")),
                competition=item.get("poule", item.get("competitie", "")),
                status="cancelled" if item.get("status", "").lower() == "afgelast" else "scheduled",
                home_score=int(home_score) if home_score not in (None, "", "-") else None,
                away_score=int(away_score) if away_score not in (None, "", "-") else None,
            )
        except (ValueError, KeyError, TypeError) as err:
            _LOGGER.warning("Could not parse Sportlink match item %s: %s", item, err)
            return None

    # ------------------------------------------------------------------ #
    # Strategy 2: Mijn Korfbal REST API (no API key needed)              #
    # Base: https://api-mijn.korfbal.nl/api/v2                           #
    # ------------------------------------------------------------------ #
    async def _fetch_mijn_korfbal(self, session: aiohttp.ClientSession) -> list[KorfbalMatch]:
        """Fetch schedule and results from the Mijn Korfbal REST API.

        Endpoints (discovered from SPA JS bundle, env.competitionApiV2):
          GET /clubs/{clubCode}/program?dateFrom=YYYY-MM-DD&dateTo=YYYY-MM-DD
          GET /clubs/{clubCode}/results?dateFrom=YYYY-MM-DD&dateTo=YYYY-MM-DD

        Both return a list of week-objects:
          [{year, week, matches: [{date, teams, ref_id, pool, facility, status, stats?}]}]

        Matches are filtered client-side by team_code (teams.home.ref_id or
        teams.away.ref_id).  The club endpoint returns all club teams so
        client-side filtering is required.
        """
        today = date.today()
        # Cover the full korfball season: Aug previous year → Jul current year
        date_from = date(today.year - 1, 8, 1).isoformat()
        date_to = date(today.year + 1, 7, 31).isoformat()
        params = {"dateFrom": date_from, "dateTo": date_to}

        headers = {
            "Accept": "application/json",
            "Referer": f"https://mijn.korfbal.nl/team/details/{self.club_code}/{self.team_code}/programma",
        }

        matches: list[KorfbalMatch] = []
        seen_ids: set[str] = set()
        fetch_errors = 0

        for endpoint in ("program", "results"):
            url = f"{MIJN_KORFBAL_API}/clubs/{self.club_code}/{endpoint}"
            _LOGGER.debug("Fetching Mijn Korfbal %s: %s params=%s", endpoint, url, params)
            try:
                async with session.get(
                    url,
                    headers=headers,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    resp.raise_for_status()
                    data = await resp.json(content_type=None)
            except Exception as err:
                _LOGGER.error(
                    "Failed to fetch korfbal %s for club %s: %s",
                    endpoint, self.club_code, err,
                )
                fetch_errors += 1
                continue

            is_results = endpoint == "results"
            week_items = data if isinstance(data, list) else []
            for week in week_items:
                for item in week.get("matches", []):
                    home = item.get("teams", {}).get("home", {})
                    away = item.get("teams", {}).get("away", {})
                    # Filter: only keep matches where our team participates
                    if home.get("ref_id") != self.team_code and away.get("ref_id") != self.team_code:
                        continue
                    match = self._parse_mijn_korfbal_match(item, is_results)
                    if match and match.match_id not in seen_ids:
                        matches.append(match)
                        seen_ids.add(match.match_id)

        if fetch_errors == 2:
            raise UpdateFailed(
                f"Both program and results endpoints failed for club {self.club_code}. "
                "Check HA logs for details."
            )

        _LOGGER.info(
            "Fetched %d matches for team %s via Mijn Korfbal API",
            len(matches), self.team_code,
        )
        return matches

    def _parse_mijn_korfbal_match(self, item: dict[str, Any], is_results: bool) -> KorfbalMatch | None:
        """Parse a single match from the Mijn Korfbal REST API response.

        Response shape:
          {
            "date": "2026-04-18T09:30:00+0200",
            "teams": {
              "home": {"name": "...", "ref_id": "T...", "clubRefId": "..."},
              "away": {"name": "...", "ref_id": "T...", "clubRefId": "..."}
            },
            "ref_id": 54009,
            "pool": {"name": "...", "ref_id": ...},
            "facility": {"name": "...", "address": {"city": "..."}},
            "status": {"game": "gepland", "status": "SCHEDULED"},
            "stats": {                          # present on results endpoint
              "home": {"score": 9},
              "away": {"score": 1}
            }
          }
        """
        try:
            raw_dt = item.get("date")
            if not raw_dt:
                return None

            start = datetime.fromisoformat(str(raw_dt)).astimezone(TZ_NL)
            end = start + timedelta(hours=1, minutes=30)

            teams = item.get("teams", {})
            home = teams.get("home", {})
            away = teams.get("away", {})

            facility = item.get("facility") or {}
            address = facility.get("address") or {}
            city = address.get("city", "")
            location = facility.get("name") or city or "Onbekend"
            if city and city not in location:
                location = f"{location}, {city}"

            pool = item.get("pool") or {}
            competition = pool.get("name", "")

            status_obj = item.get("status") or {}
            status_raw = str(status_obj.get("status", "")).upper()
            if status_raw in ("CANCELLED", "AFGELAST"):
                status = "cancelled"
            elif status_raw == "FINAL":
                status = "played"
            else:
                status = "scheduled"

            home_score: int | None = None
            away_score: int | None = None
            stats = item.get("stats") or {}
            if stats:
                hs = (stats.get("home") or {}).get("score")
                as_ = (stats.get("away") or {}).get("score")
                if hs is not None and as_ is not None:
                    try:
                        home_score = int(hs)
                        away_score = int(as_)
                        status = "played"
                    except (ValueError, TypeError):
                        pass

            return KorfbalMatch(
                match_id=str(item.get("ref_id", "")),
                home_team=home.get("name", "Thuis"),
                away_team=away.get("name", "Uit"),
                start=start,
                end=end,
                location=location,
                competition=competition,
                status=status,
                home_score=home_score,
                away_score=away_score,
            )
        except (ValueError, KeyError, TypeError, AttributeError) as err:
            _LOGGER.warning("Could not parse Mijn Korfbal match %s: %s", item, err)
            return None
