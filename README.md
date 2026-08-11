# Korfbal Schedule — Home Assistant Custom Integration

Haalt het wedstrijdprogramma en uitslagen op van **mijn.korfbal.nl** (KNKV/Sportlink)
en biedt dit aan als een **Kalender** en **Sensoren** in Home Assistant.

Zie [ARCHITECTURE.md](ARCHITECTURE.md) voor de moduleverdeling en gegevensstroom.
Pending en uitgebrachte wijzigingen staan in [CHANGES.md](CHANGES.md).

---

## Wat doet het?

| Entity | Beschrijving |
|--------|-------------|
| `calendar.korfbal_<team>_wedstrijden` | Kalender met alle wedstrijden (programma + uitslagen) — werkt in de HA Agenda-kaart en met Google Calendar sync |
| `sensor.korfbal_<team>_volgende_wedstrijd` | Datum/tijd van de volgende wedstrijd (+ alle details als attributen) |
| `sensor.korfbal_<team>_aantal_wedstrijden` | Aantal aankomende wedstrijden |

---

## Installatie

### Stap 1 — Kopieer de bestanden

Kopieer de map `korfbal_schedule` naar je Home Assistant configuratiemap:

```
/config/custom_components/korfbal_schedule/
├── __init__.py
├── calendar.py
├── api.py
├── config_flow.py
├── coordinator.py
├── manifest.json
├── sensor.py
└── strings.json
```

Met HACS (aanbevolen via "Custom repository") of handmatig via SSH/Samba.

### Stap 2 — Herstart Home Assistant

> ⚠️ Na het kopiëren van bestanden is een **volledige herstart** vereist.
> Een integratie-reload is **niet** voldoende — HA herlaadt Python-modules
> alleen bij een volledige herstart.

```
Instellingen → Systeem → Herstart
```

### Stap 3 — Integratie toevoegen

1. Ga naar **Instellingen → Apparaten & Diensten → Integratie toevoegen**
2. Zoek op **Korfbal Schedule**
3. Kies **Club en team zoeken** en selecteer eerst je club en daarna je team.
4. Controleer de teamnaam en vul eventueel een **Sportlink Client ID** in.

Werkt de openbare lijst niet, kies dan **Codes handmatig invoeren**. Vul de
clubcode en teamcode uit de URL van je teampagina in. De handmatige route blijft
ook beschikbaar voor teams die niet in de openbare lijst staan.

---

## Gegevensbronnen — twee strategieën

### Club- en teamselectie tijdens het instellen

De browsebare setup gebruikt twee openbare endpoints van de Mijn Korfbal-website:

```
GET https://api-mijn.korfbal.nl/api/v2/clubs
GET https://api-mijn.korfbal.nl/api/v2/clubs/{clubCode}/teams
```

De clublijst is alfabetisch gegroepeerd en teams zijn per leeftijdsgroep
gegroepeerd. De integratie toont leesbare labels, maar slaat de stabiele
`ref_id`-waarden op als `club_code` en `team_code`.

Deze endpoints zijn website-API's zonder gegarandeerde beschikbaarheid of
compatibiliteitsbelofte. Een wijziging, lege lijst of tijdelijke storing kan de
club- of teamkeuze verhinderen; gebruik dan de handmatige route. Bestaande
configuratie-items blijven werken omdat hun opgeslagen velden en unieke ID niet
veranderen.

### Strategie A: Mijn Korfbal REST API (standaard, geen API key nodig)

Laat het `sportlink_client_id` veld leeg. De integratie gebruikt dan de
officiële REST API die de mijn.korfbal.nl website zelf ook gebruikt:

```
GET https://api-mijn.korfbal.nl/api/v2/clubs/{clubCode}/program
    ?dateFrom=YYYY-MM-DD&dateTo=YYYY-MM-DD

GET https://api-mijn.korfbal.nl/api/v2/clubs/{clubCode}/results
    ?dateFrom=YYYY-MM-DD&dateTo=YYYY-MM-DD
```

Beide endpoints geven een lijst van weekobjecten terug met alle wedstrijden
van de hele club. De integratie filtert client-side op jouw teamcode.
Uitslagen (inclusief scores) komen via het results-endpoint.

### Strategie B: Officiële Sportlink Club.Dataservice API

Vul een **Client ID** in als jouw vereniging dat heeft. Vereist een
abonnement op **Club.Dataservice** bij Sportlink Services.

**API endpoint die gebruikt wordt:**
```
GET https://data.sportlink.com/programma
  ?clientId=<JOUW_CLIENT_ID>
  &teamcode=T1200100098
  &aantaldagen=90
  &eigenwedstrijden=ja
```

---

## Lovelace voorbeeldkaarten

### Agenda-kaart (ingebouwd)
```yaml
type: calendar
entities:
  - calendar.korfbal_heren_1_wedstrijden
initial_view: listWeek
```

### Volgende wedstrijd — markdown kaart
```yaml
type: markdown
title: Volgende Wedstrijd
content: |
  ## 🏐 {{ state_attr('sensor.korfbal_heren_1_volgende_wedstrijd', 'home_team') }}
     vs
  ## {{ state_attr('sensor.korfbal_heren_1_volgende_wedstrijd', 'away_team') }}

  📅 {{ states('sensor.korfbal_heren_1_volgende_wedstrijd') | as_datetime | as_local }}
  📍 {{ state_attr('sensor.korfbal_heren_1_volgende_wedstrijd', 'location') }}
  🏆 {{ state_attr('sensor.korfbal_heren_1_volgende_wedstrijd', 'competition') }}
```

### Entities kaart
```yaml
type: entities
title: Korfbal
entities:
  - sensor.korfbal_heren_1_volgende_wedstrijd
  - sensor.korfbal_heren_1_aantal_wedstrijden
```

---

## Automatie voorbeelden

### Herinnering dag van tevoren
```yaml
automation:
  - alias: "Korfbal morgen reminder"
    trigger:
      - platform: time
        at: "20:00:00"
    condition:
      - condition: template
        value_template: >
          {% set next = states('sensor.korfbal_heren_1_volgende_wedstrijd') | as_datetime %}
          {% set tomorrow = now() + timedelta(days=1) %}
          {{ next.date() == tomorrow.date() }}
    action:
      - service: notify.mobile_app_jouw_telefoon
        data:
          title: "🏐 Korfbal morgen!"
          message: >
            {{ state_attr('sensor.korfbal_heren_1_volgende_wedstrijd', 'home_team') }}
            vs {{ state_attr('sensor.korfbal_heren_1_volgende_wedstrijd', 'away_team') }}
            om {{ states('sensor.korfbal_heren_1_volgende_wedstrijd') | as_datetime | as_local | strftime('%H:%M') }}
            bij {{ state_attr('sensor.korfbal_heren_1_volgende_wedstrijd', 'location') }}
```

---

## Google Calendar sync

Als je de HA **Google Calendar** integratie hebt:
1. Voeg de `calendar.korfbal_*` entiteit toe in de Google Calendar config
2. Wedstrijden verschijnen automatisch in je Google Agenda

Of gebruik de ingebouwde HA Kalender — die is al zichtbaar na installatie.

---

## Vernieuwen

Gegevens worden elke **6 uur** automatisch ververst.
Handmatig vernieuwen: **Instellingen → Apparaten & Diensten → Korfbal Schedule → Vernieuwen**.

> ⚠️ Na het bijwerken van de integratie-bestanden is altijd een **volledige herstart**
> van Home Assistant nodig. Een integratie-reload herlaadt geen Python-modules.

---

## Problemen oplossen

| Probleem | Oplossing |
|---------|-----------|
| Geen wedstrijden zichtbaar na reload | Doe een **volledige HA herstart** — reload herlaadt geen Python-bestanden. |
| Club- of teamlijst kan niet worden geladen | Controleer de verbinding met `api-mijn.korfbal.nl` of kies **Codes handmatig invoeren**. |
| Foutmelding in logs over club/team | Controleer clubcode en teamcode in de URL van jouw teampagina op mijn.korfbal.nl. |
| `UpdateFailed` in logs | Netwerk probleem of API wijziging. Controleer HA logs via `Instellingen → Systeem → Logboek`. |
| Kalender leeg, sensoren op `unknown` | Zie logs op `korfbal` — de integratie logt nu altijd een fout als ophalen mislukt. |

Log level verhogen voor debug:
```yaml
# configuration.yaml
logger:
  logs:
    custom_components.korfbal_schedule: debug
```

---

## URL-structuur mijn.korfbal.nl

```
https://mijn.korfbal.nl/team/details/{CLUB_CODE}/{TEAM_CODE}/programma
                                       ^^^^^^^^^  ^^^^^^^^^^^^
                                       NCX35C2    T1200100098
```

Beide codes zijn zichtbaar in de URL van je teampagina. 

## Changelog

### v1.1.2

- Architectuurdocumentatie toegevoegd.
- Changelog- en releaseverwijzingen gestandaardiseerd voor HACS-packaging.

### v1.1.1

- Club- en teamkeuze in de browsebare setup hersteld.
- Selectie accepteert zowel de stabiele codes als de zichtbare labels van clubs en teams.
- De stabiele club- en teamcodes blijven opgeslagen in configuratie-items.

### v1.1.0

- Browsebare club- en teamselectie toegevoegd aan de setup.
- Handmatige invoer behouden als fallback voor storingen en ontbrekende teams.
- Duidelijke foutafhandeling toegevoegd voor de openbare discovery-API.
- Sportlink Client ID, bestaande configuratievelden en unieke team-ID's behouden.
- GitHub Release-automatisering toegevoegd voor geslaagde `main`-updates en versie-tags.

## Releaseprocedure

De versie in `custom_components/korfbal_schedule/manifest.json` is de leidende
releaseversie. Zet gewone wijzigingen onder `[Unreleased]` in `CHANGES.md` en
verhoog de manifestversie pas bij een expliciete versie-bump. Voor een release:

1. Hernoem `[Unreleased]` naar `## [x.y.z] - JJJJ-MM-DD` en voeg daar de
  release notes aan toe.
2. Voeg daarboven een nieuwe lege `## [Unreleased]`-sectie toe.
3. Werk de manifestversie bij en voer de lokale validatie uit.
4. Push de bijbehorende tag `vX.Y.Z`.

Een push naar `main` voert de validatie uit. Alleen een expliciete versie-tag
die overeenkomt met de manifestversie haalt de bijbehorende sectie uit
`CHANGES.md` en maakt of actualiseert de GitHub Release. Tag en release-titel
zijn exact `vX.Y.Z`; de matching changelogsectie wordt als release notes
gebruikt. Als Actions niet beschikbaar is, gebruik je dezelfde tag, titel en
release notes handmatig.
