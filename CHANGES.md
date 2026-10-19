# Changelog

## [Unreleased]

## [1.1.3] - 2026-10-19

### Changed

- Migrate club, team, program, and results requests to the current Mijn Korfbal
  competition API.
- Store the selected pool ID and use it when requesting fixtures and results.
- Parse the current API response format, including paginated match data.

### Added

- Add HACS metadata for the Netherlands integration repository.

## [1.1.2] - 2026-08-11

### Changed

- Added architecture documentation and standardized changelog and release
  references for HACS packaging.

## v1.1.1 - 2026-08-07

- Herstel de overgang van clubkeuze naar de teamkeuze in de browsebare setup.
- Accepteer zowel stabiele codes als zichtbare labels bij club- en teamselectie.
- Behoud de stabiele club- en teamcodes in nieuwe configuratie-items.

## v1.1.0 - 2026-08-07

- Voeg browsebare club- en teamselectie toe tijdens het instellen van de integratie.
- Behoud handmatige invoer als fallback wanneer de openbare Mijn Korfbal API niet beschikbaar is of geen team toont.
- Voeg foutafhandeling toe voor netwerkproblemen, HTTP-fouten, lege lijsten en ongeldige API-antwoorden.
- Behoud Sportlink Client ID-ondersteuning, bestaande configuratievelden en unieke team-ID's.
- Voeg geautomatiseerde GitHub Releases toe na geslaagde `main`-updates en voor matching `vX.Y.Z` tags.