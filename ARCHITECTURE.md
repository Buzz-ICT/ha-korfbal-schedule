# Architecture

## Overview

`korfbal_schedule` is a Home Assistant custom integration that exposes one
team's korfball schedule as a calendar and sensors. It uses the public Mijn
Korfbal API by default and can use the official Sportlink Club.Dataservice API
when a Sportlink Client ID is configured.

The integration is organized around one shared data coordinator. The
coordinator owns API access, response parsing, filtering, and the normalized
match model. The calendar and sensor platforms only read coordinator data.

## Components

| File | Responsibility |
| --- | --- |
| `__init__.py` | Defines the domain, creates a coordinator for each config entry, performs the first refresh, and forwards setup to the calendar and sensor platforms. |
| `api.py` | Fetches and parses clubs and teams for the config flow. It accepts direct, wrapped, and grouped discovery responses and keeps stable API identifiers separate from display labels. |
| `config_flow.py` | Provides browse and manual setup paths, loads teams after a club is selected, stores stable club/team/pool codes, and prevents duplicate entries. |
| `coordinator.py` | Selects the configured API strategy, fetches match data, parses responses into `KorfbalMatch`, filters to the selected team and pool, and reports update failures. |
| `calendar.py` | Creates the team calendar and converts normalized matches to Home Assistant calendar events. |
| `sensor.py` | Creates sensors for the next match, upcoming match count, and latest played result. |
| `strings.json` | Provides the translated config-flow labels and error messages. |
| `manifest.json` | Contains Home Assistant metadata and the integration version. |

## Configuration flow

1. The user chooses browse or manual setup.
2. Browse setup calls `GET /clubs`, parses the response, and displays friendly
   labels. The submitted values are the stable club codes.
3. After a club is selected, browse setup calls
   `GET /clubs/{clubCode}/teams` and displays the matching team options. The
   submitted value is the stable team code; the team's pool ID is also retained
   for schedule requests.
4. The settings step collects an optional display name and optional Sportlink
   Client ID.
5. Manual setup accepts the club and team codes copied from the Mijn Korfbal
   team URL, plus an optional pool ID, and remains available when discovery is
   unavailable.
6. The config entry uses `{club_code}_{team_code}` as its unique ID. Existing
   entries therefore remain compatible when labels or discovery behavior
   change.

Discovery errors are translated into specific config-flow messages for network
failures, HTTP failures, invalid responses, and empty club or team lists.

## Runtime data flow

When Home Assistant loads a config entry, `__init__.py` creates
`KorfbalScheduleCoordinator` and performs its first refresh. The coordinator is
stored in `hass.data[DOMAIN]` and shared with both platforms. It refreshes every
six hours.

The coordinator reads `club_code`, `team_code`, `team_name`, optional
`pool_code`, and optional `sportlink_client_id` from the config entry. It
chooses the data source as follows:

### Sportlink strategy

When a Client ID is present, the coordinator calls
`https://data.sportlink.com/programma` for the selected team and a 90-day
look-ahead. It parses Dutch date and time fields, scores, location, competition,
and cancellation status.

### Mijn Korfbal strategy

Without a Client ID, the coordinator calls both Mijn Korfbal website endpoints:

- `https://api-saas-site-prod-236.dotlab.net/general/api/competition/clubs/{clubCode}/program`
- `https://api-saas-site-prod-236.dotlab.net/general/api/competition/clubs/{clubCode}/results`

The coordinator passes the selected team code and, when available, its pool ID
to each request and reads all DataTables response pages. The program date range
covers the remainder of the current season; results cover the full history.
Matches are checked against the selected team and pool when those IDs are
present in the response, then deduplicated by match ID. Results add scores and
played status when available. A failure of one endpoint is logged while the
other endpoint can still provide data; both endpoints failing raises
`UpdateFailed`.

## Normalized match model

`KorfbalMatch` is the boundary between remote API formats and Home Assistant
entities. Each match contains:

- a stable match ID;
- home and away team names;
- timezone-aware start and end datetimes in `Europe/Amsterdam`;
- location and competition;
- `scheduled`, `cancelled`, or `played` status; and
- optional home and away scores.

The model also builds the calendar summary and description so both platforms
present the same match details.

## Home Assistant entities

- `KorfbalCalendar` exposes all matches that overlap a requested calendar date
  range and reports the next non-cancelled match as the current event.
- `KorfbalNextMatchSensor` reports the next match start as a timestamp and
  exposes the full match as attributes.
- `KorfbalMatchCountSensor` counts non-cancelled matches whose end is still in
  the future.
- `KorfbalLastResultSensor` reports the most recent played score and its match
  details.

All entities inherit from `CoordinatorEntity`, so a successful coordinator
refresh updates the calendar and sensors together.

## Design decisions

- The no-key Mijn Korfbal path is the default to keep initial setup simple.
- The Sportlink path is retained for installations with a Club.Dataservice
  subscription and is selected explicitly by entering a Client ID.
- Stable API codes are stored in config entries; display labels are only for
  the setup UI.
- API response parsing is isolated from entity code so endpoint shape changes
  can be tested without running a full Home Assistant instance.
- A full Home Assistant restart is required after replacing integration Python
  files because reloading a config entry does not reload Python modules.

## Testing

`tests/test_api.py` covers grouped discovery payloads, duplicate-name label
disambiguation, empty responses, and malformed responses. `tests/test_config_flow.py`
covers browse setup, label compatibility, manual setup compatibility, discovery
errors, and duplicate-entry protection.