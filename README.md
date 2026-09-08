# pinradar-motor

Oogstmotor voor publieke Pinterest-pins. Zoekt op opgegeven termen, klikt door
naar verwante pins, groepeert de vondsten per product, meet het aantal saves en
controleert of de winkel erachter nog bereikbaar is. Levert ruwe JSON.

De motor beslist niets. Hij weet niet waar hij naar zoekt tot je hem een
configbestand geeft, en hij heeft geen enkele voorkeur ingebouwd.

## Draaien

```bash
CONFIG=config.json RUWDIR=./ruw python motor.py
```

| omgevingsvariabele | betekenis | standaard |
|---|---|---|
| `CONFIG`  | pad naar het configbestand (verplicht) | `./config.json` |
| `RUWDIR`  | waar de ruwe JSON landt | `./ruw` |
| `GEZIEN`  | JSON met productsleutels die je wilt overslaan | leeg |
| `HOEK`    | dwing een specifieke hoek af | eerstvolgende zonder bestand |
| `MINUTEN` | tijdbudget voor deze ronde | `40` |
| `PAGINAS` | zoekpagina's per term | `6` |
| `LUIDOP`  | `1` = volledige voortgang naar stdout | uit |

Exitcodes: `0` klaar · `1` bootstrap mislukt · `2` config onleesbaar ·
`3` alle hoeken van vandaag hebben al een bestand.

## Configbestand

```json
{
  "ruislat": 150,
  "volgorde": ["hoek-a", "hoek-b"],
  "hoeken": {
    "hoek-a": ["zoekterm een", "zoekterm twee"],
    "hoek-b": ["nog een term"]
  }
}
```

`ruislat` is het minimum aantal saves waaronder een product niet wordt
opgeschreven. `volgorde` bepaalt welke hoek een ronde pakt: de eerstvolgende die
vandaag nog geen bestand heeft in `RUWDIR`.

## Waarom het zo gebouwd is

Een paar dingen die uit meten zijn gekomen en die je niet uit de API-documentatie
haalt:

- **Saves staan in `aggregated_stats`.** Het veld `repin_count` bestaat nog maar
  geeft nul terug. Meten gaat via de pidgets-endpoint, 40 pin-ids per verzoek.
- **De related-feed geeft vanaf datacenter-IP's een geldig antwoord met een lege
  lijst.** Geen foutmelding, gewoon niets. Daarom is zoeken de hoofdmotor en is
  doorklikken de bonus: de motor stopt met klikken zodra acht kliks niets nieuws
  opleveren, in plaats van het tijdbudget op te maken aan lege calls.
- **Een gesloten winkel herken je aan de eind-URL** (`/password`,
  `/opening-soon`), niet aan de tekst op de pagina. "Notify me when available" is
  standaardtekst en staat ook op winkels die gewoon open zijn.
- **Groepeer op domein plus producthandle, niet op pin.** Eén product heeft vaak
  tien pins; op pin-niveau tellen betekent hetzelfde product tien keer wegen.
- **Neem het hoogste gemeten getal van de pins van een product, niet de som.**
  `aggregated_stats` telt het hele beeld al mee; optellen telt dubbel.

## Stil op publieke logs

`log()` verzamelt alles maar print standaard niets. Workflow-logs van een
publieke repo zijn voor iedereen leesbaar, en de zoektermen en de oogst horen
daar niet in te staan. Het volledige log komt naast de ruwe uitvoer te staan, in
`RUWDIR`. Zet `LUIDOP=1` als je lokaal wilt meekijken.

## Tests

```bash
python -m unittest discover -s tests -v
```
