"""Discovery helpers for the public Mijn Korfbal API."""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

MIJN_KORFBAL_API = "https://api-saas-site-prod-236.dotlab.net/general/api/competition"
DISCOVERY_TIMEOUT_SECONDS = 15


class DiscoveryError(Exception):
    """Base error for club and team discovery."""


class DiscoveryNetworkError(DiscoveryError):
    """The discovery endpoint could not be reached."""


class DiscoveryHttpError(DiscoveryError):
    """The discovery endpoint returned an HTTP error."""

    def __init__(self, status: int) -> None:
        super().__init__(f"Discovery endpoint returned HTTP {status}")
        self.status = status


class DiscoveryResponseError(DiscoveryError):
    """The discovery endpoint returned an unusable response."""


class DiscoveryEmptyError(DiscoveryError):
    """The discovery endpoint returned no selectable items."""


@dataclass(frozen=True)
class ClubOption:
    """A club option with a stable API value and a display label."""

    code: str
    name: str
    label: str


@dataclass(frozen=True)
class TeamOption:
    """A team option with a stable API value and a display label."""

    code: str
    name: str
    label: str
    age_group: str = ""
    pool_code: str = ""
    pool_name: str = ""


def _first_value(item: Mapping[str, Any], keys: tuple[str, ...]) -> Any:
    """Return the first non-empty value for the supplied keys."""
    for key in keys:
        value = item.get(key)
        if value is not None and str(value).strip():
            return value
    return None


def _record_code(item: Mapping[str, Any]) -> str | None:
    """Extract an API identifier from a club or team object."""
    value = _first_value(
        item,
        ("ref_id", "refId", "reference_id", "referenceId", "id", "code"),
    )
    return str(value).strip() if value is not None else None


def _record_name(item: Mapping[str, Any]) -> str | None:
    """Extract a display name from a club or team object."""
    value = _first_value(
        item,
        ("name", "club_name", "clubName", "team_name", "teamName", "title"),
    )
    return str(value).strip() if value is not None else None


def _iter_grouped_records(
    payload: Any,
    *,
    collection_keys: tuple[str, ...],
    context_keys: tuple[str, ...],
    inherited_context: Mapping[str, str] | None = None,
) -> Iterator[tuple[Mapping[str, Any], Mapping[str, str]]]:
    """Yield records from direct, wrapped, or grouped API responses."""
    context = dict(inherited_context or {})

    if isinstance(payload, list):
        for item in payload:
            yield from _iter_grouped_records(
                item,
                collection_keys=collection_keys,
                context_keys=context_keys,
                inherited_context=context,
            )
        return

    if not isinstance(payload, Mapping):
        return

    if _record_code(payload) and _record_name(payload):
        yield payload, context
        return

    for key in context_keys:
        value = payload.get(key)
        if isinstance(value, Mapping):
            nested_name = _record_name(value)
            if nested_name:
                context[key] = nested_name
        elif value is not None and not isinstance(value, list):
            text = str(value).strip()
            if text:
                context[key] = text

    if "name" in payload and any(key in payload for key in collection_keys):
        group_name = payload.get("name")
        if group_name is not None and not isinstance(group_name, (list, Mapping)):
            context.setdefault("group", str(group_name).strip())

    for key, value in payload.items():
        if key in context_keys:
            continue
        if key in collection_keys or isinstance(value, (list, Mapping)):
            child_context = context
            if key not in collection_keys and isinstance(value, list):
                child_context = {**context, "group": str(key).strip()}
            yield from _iter_grouped_records(
                value,
                collection_keys=collection_keys,
                context_keys=context_keys,
                inherited_context=child_context,
            )


def _has_nested_collection(payload: list[Any] | Mapping[str, Any]) -> bool:
    """Return whether a response has a recognizable grouped shape."""
    if isinstance(payload, list):
        return any(isinstance(item, (list, Mapping)) for item in payload)
    return any(isinstance(value, (list, Mapping)) for value in payload.values())


def _validate_payload(payload: Any) -> list[Any] | Mapping[str, Any]:
    """Validate the outer response shape while allowing an empty response."""
    if not isinstance(payload, (list, Mapping)):
        raise DiscoveryResponseError("Discovery response must be a list or object")

    if isinstance(payload, list) and any(
        not isinstance(item, (list, Mapping)) for item in payload
    ):
        raise DiscoveryResponseError("Discovery response contains an invalid item")

    return payload


def _club_location(item: Mapping[str, Any]) -> str:
    """Extract a useful location suffix for duplicate club names."""
    address = item.get("address")
    values: list[str] = []
    if isinstance(address, Mapping):
        for key in ("city", "place", "street"):
            value = address.get(key)
            if value is not None and str(value).strip():
                values.append(str(value).strip())
    elif address is not None and str(address).strip():
        values.append(str(address).strip())

    for key in ("city", "place", "location"):
        value = item.get(key)
        if value is not None and str(value).strip():
            values.append(str(value).strip())

    unique_values = list(dict.fromkeys(values))
    return ", ".join(unique_values)


def _disambiguate_label(name: str, suffix: str, code: str) -> str:
    """Build a friendly label while retaining a useful final fallback."""
    return f"{name} ({suffix or code})"


def parse_clubs(payload: Any) -> list[ClubOption]:
    """Parse direct or alphabetically grouped club data."""
    validated = _validate_payload(payload)
    records = list(
        _iter_grouped_records(
            validated,
            collection_keys=(
                "clubs",
                "list",
                "items",
                "data",
                "results",
                "values",
            ),
            context_keys=(),
        )
    )
    if not records and validated and not _has_nested_collection(validated):
        raise DiscoveryResponseError("Discovery response contains no club records")

    raw_options: list[tuple[str, str, str]] = []
    seen_codes: set[str] = set()
    for item, _context in records:
        code = _record_code(item)
        name = _record_name(item)
        if not code or not name or code in seen_codes:
            continue
        seen_codes.add(code)
        raw_options.append((code, name, _club_location(item)))

    name_counts = Counter(name.casefold() for _code, name, _suffix in raw_options)
    options = [
        ClubOption(
            code=code,
            name=name,
            label=(
                _disambiguate_label(name, suffix, code)
                if name_counts[name.casefold()] > 1
                else name
            ),
        )
        for code, name, suffix in raw_options
    ]
    options.sort(key=lambda option: option.label.casefold())

    labels = Counter(option.label.casefold() for option in options)
    return [
        ClubOption(
            code=option.code,
            name=option.name,
            label=(
                option.label
                if labels[option.label.casefold()] == 1
                else _disambiguate_label(option.name, "", option.code)
            ),
        )
        for option in options
    ]


def _team_age_group(
    item: Mapping[str, Any], context: Mapping[str, str]
) -> str:
    """Extract an age/category label for duplicate team names."""
    value = _first_value(
        item,
        ("age_group", "ageGroup", "age", "category", "level", "gender"),
    )
    if value is not None:
        return str(value).strip()
    return context.get("age_group") or context.get("ageGroup") or context.get(
        "age"
    ) or context.get("category") or context.get("group", "")


def _team_pool(item: Mapping[str, Any]) -> tuple[str, str]:
    """Extract the selected team's pool identifier and name."""
    pool = item.get("pool")
    if isinstance(pool, Mapping):
        return _record_code(pool) or "", _record_name(pool) or ""

    code = _first_value(item, ("pool_ref_id", "poolRefId", "pool_code", "poolCode"))
    name = _first_value(item, ("pool_name", "poolName"))
    return (
        str(code).strip() if code is not None else "",
        str(name).strip() if name is not None else "",
    )


def parse_teams(payload: Any) -> list[TeamOption]:
    """Parse direct or age-grouped team data."""
    validated = _validate_payload(payload)
    records = list(
        _iter_grouped_records(
            validated,
            collection_keys=("teams", "items", "data", "results", "values"),
            context_keys=("age_group", "ageGroup", "age", "category", "level", "gender"),
        )
    )
    if not records and validated and not _has_nested_collection(validated):
        raise DiscoveryResponseError("Discovery response contains no team records")

    raw_options: list[tuple[str, str, str, str, str]] = []
    seen_codes: set[str] = set()
    for item, context in records:
        code = _record_code(item)
        name = _record_name(item)
        if not code or not name or code in seen_codes:
            continue
        seen_codes.add(code)
        age_group = _team_age_group(item, context)
        pool_code, pool_name = _team_pool(item)
        raw_options.append((code, name, age_group, pool_code, pool_name))

    name_counts = Counter(name.casefold() for _code, name, *_rest in raw_options)
    options = [
        TeamOption(
            code=code,
            name=name,
            label=(
                _disambiguate_label(name, age_group or pool_name, code)
                if name_counts[name.casefold()] > 1
                else name
            ),
            age_group=age_group,
            pool_code=pool_code,
            pool_name=pool_name,
        )
        for code, name, age_group, pool_code, pool_name in raw_options
    ]
    options.sort(key=lambda option: option.label.casefold())

    labels = Counter(option.label.casefold() for option in options)
    return [
        TeamOption(
            code=option.code,
            name=option.name,
            label=(
                option.label
                if labels[option.label.casefold()] == 1
                else _disambiguate_label(option.name, "", option.code)
            ),
            age_group=option.age_group,
            pool_code=option.pool_code,
            pool_name=option.pool_name,
        )
        for option in options
    ]


async def _async_get_json(hass: HomeAssistant, url: str) -> Any:
    """Fetch JSON from a discovery endpoint and normalize transport errors."""
    import aiohttp

    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    session = async_get_clientsession(hass)
    try:
        async with session.get(
            url,
            headers={"Accept": "application/json"},
            timeout=aiohttp.ClientTimeout(total=DISCOVERY_TIMEOUT_SECONDS),
        ) as response:
            if response.status >= 400:
                raise DiscoveryHttpError(response.status)
            try:
                return await response.json(content_type=None)
            except (TypeError, ValueError, aiohttp.ContentTypeError) as err:
                raise DiscoveryResponseError("Discovery response was not valid JSON") from err
    except DiscoveryError:
        raise
    except asyncio.TimeoutError as err:
        raise DiscoveryNetworkError("Discovery request timed out") from err
    except aiohttp.ClientError as err:
        raise DiscoveryNetworkError("Discovery request failed") from err


async def async_fetch_clubs(hass: HomeAssistant) -> list[ClubOption]:
    """Fetch and parse the public club list."""
    data = await _async_get_json(hass, f"{MIJN_KORFBAL_API}/clubs")
    clubs = parse_clubs(data)
    if not clubs:
        raise DiscoveryEmptyError("No clubs were returned")
    return clubs


async def async_fetch_teams(hass: HomeAssistant, club_code: str) -> list[TeamOption]:
    """Fetch and parse the teams for one club."""
    encoded_club_code = quote(club_code, safe="")
    data = await _async_get_json(
        hass,
        f"{MIJN_KORFBAL_API}/clubs/{encoded_club_code}/teams",
    )
    teams = parse_teams(data)
    if not teams:
        raise DiscoveryEmptyError("No teams were returned")
    return teams