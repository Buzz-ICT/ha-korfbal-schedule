"""Data coordinator for Korfbal Schedule.

Two fetch strategies:
1. Sportlink Club.Dataservice API  — used when `sportlink_client_id` is set.
   Endpoint: https://data.sportlink.com/programma
   Docs: https://sportlinkservices.freshdesk.com/nl/support/solutions/articles/9000062942

2. Mijn Korfbal competition API (default).
   Base URL: https://api-saas-site-prod-236.dotlab.net/general/api/competition
   Program:  GET /clubs/{clubCode}/program?team={teamCode}&pool={poolCode}
   Results:  GET /clubs/{clubCode}/results?team={teamCode}&pool={poolCode}
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import quote, urlencode
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
        self.pool_code: str = entry.data.get("pool_code", "")
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
    # Strategy 2: Mijn Korfbal competition API                           #
    # Base: https://api-saas-site-prod-236.dotlab.net/general/api/competition
    # ------------------------------------------------------------------ #
    async def _fetch_mijn_korfbal(self, session: aiohttp.ClientSession) -> list[KorfbalMatch]:
        """Fetch the selected team's program and results from Mijn Korfbal."""
        today = date.today()
        # Keep the program limited to the remainder of the current season.
        program_end = date(today.year + 1, 7, 31).isoformat()
        pool_params = {"pool": self.pool_code} if self.pool_code else {}
        common_params = {
            "dt": "1",
            "teamDetails": "true",
            "facility": "true",
            "officials": "true",
            "team": self.team_code,
            **pool_params,
        }
        referer_params = {
            "club": self.club_code,
            "team": self.team_code,
            **pool_params,
        }
        endpoint_params = {
            "program": {
                **common_params,
                "filters[date]": f"{today.isoformat()}><{program_end}",
            },
            "results": {
                **common_params,
                "_dateFrom": "2000-01-01",
                "filters[date]": f"2000-01-01><{today.isoformat()}",
            },
        }

        headers = {
            "Accept": "application/json",
            "Referer": (
                "https://mijn.korfbal.nl/competitie-vereniging/?"
                f"{urlencode(referer_params)}"
            ),
        }

        matches: list[KorfbalMatch] = []
        seen_ids: set[str] = set()
        fetch_errors = 0

        for endpoint, params in endpoint_params.items():
            try:
                rows = await self._fetch_mijn_korfbal_endpoint(
                    session, endpoint, params, headers
                )
            except (
                aiohttp.ClientError,
                asyncio.TimeoutError,
                UpdateFailed,
                ValueError,
                TypeError,
            ) as err:
                _LOGGER.error(
                    "Failed to fetch korfbal %s for club %s: %s",
                    endpoint, self.club_code, err,
                )
                fetch_errors += 1
                continue

            is_results = endpoint == "results"
            for row in rows:
                items = row.get("matches", []) if isinstance(row, Mapping) else []
                if not isinstance(items, list) or not items:
                    items = [row]
                for item in items:
                    if not isinstance(item, Mapping):
                        raise UpdateFailed(
                            f"Mijn Korfbal {endpoint} returned an invalid match"
                        )

                    team_ids = self._match_team_ids(item)
                    if team_ids and self.team_code not in team_ids:
                        continue
                    match_pool = self._match_pool_id(item)
                    if self.pool_code and match_pool and match_pool != self.pool_code:
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

    async def _fetch_mijn_korfbal_endpoint(
        self,
        session: aiohttp.ClientSession,
        endpoint: str,
        params: dict[str, str],
        headers: dict[str, str],
    ) -> list[Any]:
        """Fetch all DataTables pages returned by a Mijn Korfbal endpoint."""
        url = (
            f"{MIJN_KORFBAL_API}/clubs/"
            f"{quote(self.club_code, safe='')}/{endpoint}"
        )
        page_size = 100
        start = 0
        rows: list[Any] = []

        for draw in range(1, 101):
            request_params = {
                **params,
                "draw": str(draw),
                "start": str(start),
                "length": str(page_size),
            }
            _LOGGER.debug(
                "Fetching Mijn Korfbal %s: %s params=%s",
                endpoint,
                url,
                request_params,
            )
            async with session.get(
                url,
                headers=headers,
                params=request_params,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as response:
                response.raise_for_status()
                data = await response.json(content_type=None)

            if isinstance(data, list):
                return data
            if not isinstance(data, Mapping):
                raise UpdateFailed(
                    f"Mijn Korfbal {endpoint} returned an invalid response"
                )
            if data.get("success") is False:
                raise UpdateFailed(
                    f"Mijn Korfbal {endpoint} failed: "
                    f"{data.get('message', 'unknown API error')}"
                )

            page_rows = data.get("data")
            if not isinstance(page_rows, list):
                raise UpdateFailed(
                    f"Mijn Korfbal {endpoint} response has no match list"
                )
            rows.extend(page_rows)

            total_value = data.get("recordsFiltered", data.get("recordsTotal"))
            if total_value is None:
                return rows
            try:
                total = int(total_value)
            except (TypeError, ValueError) as err:
                raise UpdateFailed(
                    f"Mijn Korfbal {endpoint} returned an invalid row count"
                ) from err
            if not page_rows or start + len(page_rows) >= total:
                return rows
            start += len(page_rows)

        raise UpdateFailed(
            f"Mijn Korfbal {endpoint} exceeded the maximum page count"
        )

    @staticmethod
    def _match_team_ids(item: Mapping[str, Any]) -> set[str]:
        """Collect team IDs when the API includes them in a match row."""
        teams = item.get("teams")
        teams = teams if isinstance(teams, Mapping) else {}
        ids: set[str] = set()
        for team in (
            item.get("homeTeam") or teams.get("home"),
            item.get("awayTeam") or teams.get("away"),
        ):
            if not isinstance(team, Mapping):
                continue
            for key in ("ref_id", "refId", "teamRefId", "teamId", "id"):
                value = team.get(key)
                if value is not None and str(value).strip():
                    ids.add(str(value).strip())
                    break
        return ids

    @staticmethod
    def _match_pool_id(item: Mapping[str, Any]) -> str:
        """Return a match's pool ID when it is part of the API row."""
        pool = item.get("pool")
        if isinstance(pool, Mapping):
            value = pool.get("ref_id") or pool.get("refId") or pool.get("id")
        else:
            value = pool or item.get("pool_id") or item.get("poolId")
        return str(value).strip() if value is not None else ""

    def _parse_mijn_korfbal_match(
        self, item: Mapping[str, Any], is_results: bool
    ) -> KorfbalMatch | None:
        """Parse a match row from the current website API or legacy response."""
        try:
            raw_dt = (
                item.get("date")
                or item.get("datetime")
                or item.get("start")
                or item.get("date_display")
            )
            if not raw_dt:
                return None

            raw_dt_text = str(raw_dt).strip()
            raw_time = item.get("time_display") or item.get("time")
            try:
                start = datetime.fromisoformat(raw_dt_text.replace("Z", "+00:00"))
            except ValueError:
                start = None

            if start is None:
                start_date = None
                for date_format in ("%d-%m-%Y", "%Y-%m-%d"):
                    try:
                        start_date = datetime.strptime(
                            raw_dt_text, date_format
                        ).date()
                        break
                    except ValueError:
                        continue
                if start_date is None:
                    return None
                parsed_time = datetime.strptime(
                    str(raw_time or "00:00").strip()[:5], "%H:%M"
                ).time()
                start = datetime.combine(start_date, parsed_time, tzinfo=TZ_NL)
            else:
                if (
                    start.time().replace(tzinfo=None) == datetime.min.time()
                    and raw_time
                ):
                    parsed_time = datetime.strptime(
                        str(raw_time).strip()[:5], "%H:%M"
                    ).time()
                    start = start.replace(
                        hour=parsed_time.hour,
                        minute=parsed_time.minute,
                    )
                if start.tzinfo is None:
                    start = start.replace(tzinfo=TZ_NL)
                start = start.astimezone(TZ_NL)
            end = start + timedelta(hours=1, minutes=30)

            teams = item.get("teams")
            teams = teams if isinstance(teams, Mapping) else {}
            home = item.get("homeTeam") or teams.get("home") or {}
            away = item.get("awayTeam") or teams.get("away") or {}
            if not isinstance(home, Mapping) or not isinstance(away, Mapping):
                return None

            facility = item.get("facility") or {}
            facility = facility if isinstance(facility, Mapping) else {}
            address = facility.get("address") or {}
            address = address if isinstance(address, Mapping) else {}
            city = address.get("city", "")
            location = (
                item.get("location")
                or facility.get("name")
                or item.get("facilityName")
                or city
                or "Onbekend"
            )
            location = str(location)
            if city and city not in location:
                location = f"{location}, {city}"

            pool = item.get("pool")
            competition = (
                pool.get("name", "")
                if isinstance(pool, Mapping)
                else item.get("competition", "")
            )
            competition = competition or item.get("competition", "")

            status_obj = item.get("status") or {}
            if isinstance(status_obj, Mapping):
                status_raw = str(
                    status_obj.get("status") or status_obj.get("game") or ""
                ).upper()
            else:
                status_raw = str(status_obj).upper()

            home_score = self._score_value(
                home.get("score")
                if home.get("score") is not None
                else item.get("home_score")
            )
            away_score = self._score_value(
                away.get("score")
                if away.get("score") is not None
                else item.get("away_score")
            )
            stats = item.get("stats") or {}
            if isinstance(stats, Mapping):
                stat_home = stats.get("home") or {}
                stat_away = stats.get("away") or {}
                if isinstance(stat_home, Mapping) and home_score is None:
                    home_score = self._score_value(stat_home.get("score"))
                if isinstance(stat_away, Mapping) and away_score is None:
                    away_score = self._score_value(stat_away.get("score"))

            raw_score = item.get("score")
            if isinstance(raw_score, str):
                score_match = re.search(r"(\d+)\s*[-–:]\s*(\d+)", raw_score)
                if score_match:
                    home_score = int(score_match.group(1))
                    away_score = int(score_match.group(2))

            if status_raw in ("CANCELLED", "CANCELED", "AFGELAST"):
                status = "cancelled"
            elif is_results or status_raw in ("FINAL", "PLAYED", "GESPEELD") or (
                home_score is not None and away_score is not None
            ):
                status = "played"
            else:
                status = "scheduled"

            home_name = home.get("teamName") or home.get("name", "Thuis")
            away_name = away.get("teamName") or away.get("name", "Uit")
            match_id = (
                item.get("ref_id")
                or item.get("match_id")
                or item.get("id")
                or f"{start.isoformat()}-{home_name}-{away_name}"
            )
            return KorfbalMatch(
                match_id=str(match_id),
                home_team=str(home_name),
                away_team=str(away_name),
                start=start,
                end=end,
                location=location,
                competition=str(competition),
                status=status,
                home_score=home_score,
                away_score=away_score,
            )
        except (ValueError, KeyError, TypeError, AttributeError) as err:
            _LOGGER.warning("Could not parse Mijn Korfbal match %s: %s", item, err)
            return None

    @staticmethod
    def _score_value(value: Any) -> int | None:
        """Parse an optional API score as an integer."""
        if value is None or value == "":
            return None
        try:
            return int(value)
        except (ValueError, TypeError):
            return None
