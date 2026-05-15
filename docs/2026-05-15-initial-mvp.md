# Briefing: dao-paper-search-mcp — Neuanlage MVP

**Stand:** 2026-05-15
**Zielgruppe:** Claude Code Session, die das Repo *neu anlegt* und den MVP baut
**Repo-Pfad (anzulegen):** `/Users/patrick/Documents/Aktuell/dao-paper-search-mcp/`

---

## I. Kontext & Architektur-Einordnung

Patrick Leiverkus (DAO, UB Oldenburg) arbeitet in der südlichen Levante (Bronze- bis Eisenzeit, biblische Archäologie, digitale Archäologie). Sein OpenCode-Setup an der GWDG hat heute zwei MCP-Tools für wissenschaftliche Recherche:

1. **`searxng-cited`** — selbst gehostete Websuche mit DOI-/Domain-Klassifikations-Erkennungs-Layer (siehe separates Briefing). Horizontal, generisch, schnell.
2. **`paper-search-mcp` (git-pinned auf v0.1.4)** — globale akademische Infrastruktur: Crossref, OpenAlex, Semantic Scholar, Unpaywall, arXiv, PubMed, dblp, BASE, HAL, Zenodo, IACR, ... — 20+ Plattformen, horizontale Coverage.

`dao-paper-search-mcp` ist das **dritte** Tool und **vertikal**: DAO-Spezialquellen, archäologische Metadaten-Anreicherung, Wikidata-Author-Disambiguation. Komplementär zu paper-search, **nicht ersetzend**.

### Warum nicht paper-search forken oder erweitern?
- paper-search ist horizontal/generisch (auch für STEM-Methoden-Fragen wie Bayesian ¹⁴C, RAG, GIS). Fork würde diese Universal-Funktion verwässern.
- paper-search ist Upstream-Live-Codebase — `git pull` bringt automatisch neue Plattformen.
- Cross-Validation in `research.md` hängt an Tool-Unabhängigkeit. Wenn dao-paper-search intern paper-search aufruft, kollabiert der zweite unabhängige Datenpunkt.

### Empirisches Fundament (Test 2026-05-15)
6 Modell-Vergleichsläufe zur Negev-Festungen-Frage. Nach Aktivierung von paper-search v0.1.4: DOIs vollständig, halluzinierte Autoren beseitigt, Verlags-Metadaten korrekt, 4–6 Schulen-Positionen statt 3, Reply/Rejoinder-Streits explizit, 25 % schnellere Synthese. **Aber:** Zenon DAI, IAA, ADAJ, IxTheo, Propylaeum bleiben außen vor — und das ist Patricks Kern-Domäne. Genau diese Lücke ist der Auftrag für dao-paper-search.

---

## II. Repository-Setup

- **Pfad:** `/Users/patrick/Documents/Aktuell/dao-paper-search-mcp/`
- **Sprache:** Python ≥ 3.11
  - Begründung: Konsistenz mit paper-search-mcp; gute SRU/OAI-PMH-Bibliotheks-Landschaft (`sruthi`, `pyoai`); Wikidata-Tooling (`SPARQLWrapper`, `wikidataintegrator`); strukturierte Output-Schemas via Pydantic.
- **MCP-Framework:** FastMCP (analog paper-search-mcp). Aktuelle Anthropic-MCP-Python-SDK.
- **Package-Management:** `uv` mit `pyproject.toml`. Konsumierbar via `uvx --from git+...`.
- **Lizenz:** MIT.
- **Git:** Initial-Commit, `main` als Default-Branch.

### Initial-Files

```
dao-paper-search-mcp/
├── .gitignore                     # Python-Standard + .venv/ + .uv-cache
├── LICENSE                        # MIT
├── README.md                      # siehe Abschnitt VIII
├── CHANGELOG.md                   # leer, mit "## [Unreleased]" Header
├── pyproject.toml                 # uv + FastMCP + Dependencies
├── src/
│   └── dao_paper_search_mcp/
│       ├── __init__.py
│       ├── server.py              # MCP-Server-Entry, @mcp.tool()-Decorators
│       ├── models.py              # Pydantic-Output-Schemas
│       ├── adapters/
│       │   ├── __init__.py
│       │   ├── zenon.py           # Zenon DAI SRU-Client
│       │   ├── iaa.py             # IAA Publications HTML-Scraper
│       │   └── adaj.py            # ADAJ HTML-Scraper
│       ├── resolvers/
│       │   ├── __init__.py
│       │   ├── wikidata_author.py # Wikidata SPARQL + lokale Overrides
│       │   └── (gazetteer.py — Stretch, Iteration 2)
│       └── data/
│           └── authority_overrides.yml  # DAO-Override-Liste
└── tests/
    ├── __init__.py
    ├── test_zenon.py
    ├── test_iaa.py
    ├── test_adaj.py
    ├── test_wikidata.py
    ├── test_models.py
    └── test_verification_suite.py  # Akzeptanz-Tests, siehe Abschnitt VI
```

### Dependencies (pyproject.toml-Snippet)

```toml
[project]
name = "dao-paper-search-mcp"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "mcp>=1.0",
    "fastmcp>=0.4",
    "httpx>=0.27",
    "pydantic>=2.0",
    "beautifulsoup4>=4.12",
    "lxml>=5.0",
    "pyyaml>=6.0",
    "SPARQLWrapper>=2.0",
    "sruthi>=2.0",  # optional, sonst manueller SRU-Client via httpx
]

[project.optional-dependencies]
test = ["pytest>=8.0", "pytest-asyncio>=0.23", "pytest-vcr>=1.0", "respx>=0.21"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

---

## III. Architektur-Prinzipien

1. **Vertical Scope.** Nur Quellen und Capabilities, die paper-search nicht hat. Keine Reimplementierung von Crossref/OpenAlex.
2. **Tool-Unabhängigkeit.** Keine internen Calls zu paper-search. Wenn ein Treffer eine DOI trägt und der Konsument Citation-Graph braucht, soll der **Agent** `paper-search.search_crossref` aufrufen.
3. **Output-Schema-Treue.** Alle Such-Tools liefern dasselbe Pydantic-Output-Modell. Eingefroren, versioniert.
4. **Strukturierte Verifikations-Hinweise.** Wenn dao-paper-search etwas nicht eindeutig findet, im Output `verification_note: "..."` setzen, statt zu raten.
5. **Stdio-Sauberkeit.** MCP-Server-Stdout muss reines JSON-RPC bleiben. Alle Logging über stderr (Python `logging.basicConfig(stream=sys.stderr)`).
6. **Kein "Magic"** — wenn ein Adapter scheitert, wirft er einen sauberen Error, der vom Tool-Handler in eine strukturierte Fehlermeldung übersetzt wird.

---

## IV. MVP — Such-Adapter (Tier 1)

### Adapter 1: `search_zenon`

**Quelle:** Zenon DAI (https://zenon.dainst.org)
- Zentralkatalog des Deutschen Archäologischen Instituts, ~1 Mio. Records, mehrsprachig (DE/EN/FR/IT/HE/AR).
- **Endpoint:** SRU bei `https://zenon.dainst.org/SRU` (zu verifizieren in der Build-Session — falls SRU nicht aktiv: REST-API-Fallback bei `https://zenon.dainst.org/api/`).
- **Dokumentation:** https://github.com/dainst/zenon, ggf. Kontakt zur DAI-IT.

**Tool-Signatur:**
```python
@mcp.tool()
async def search_zenon(
    query: str,
    max_results: int = 10,
    language: Optional[str] = None,  # "de" | "en" | "fr" | "he" | "ar"
    year_from: Optional[int] = None,
    year_to: Optional[int] = None,
) -> List[DAOPaper]:
    """Search the Zenon DAI catalog — the German Archaeological Institute's
    bibliography (~1M records, multilingual). Best for German-language Levant
    archaeology, classical antiquity, and DAI publication series."""
```

**Output:** Pydantic-Modell `DAOPaper` (siehe Abschnitt V), `source: "zenon"`.

**SRU-Details:** Query in CQL (Contextual Query Language), z.B. `dc.title=Negev AND dc.subject=Festung`. Bibliothek `sruthi` macht das halbwegs sauber. Bei freier Stichwortsuche: `cql.serverChoice=<query>`.

### Adapter 2: `search_iaa`

**Quelle:** Israel Antiquities Authority Publications (https://publications.iaa.org.il)
- Inhalt: IAA Reports, ʿAtiqot, HA-ESI (Hadashot Arkheologiyot — Excavations and Surveys in Israel).
- **API-Status:** Keine offizielle API bekannt. HTML-Scraping nötig.
- **Vorgehen:** Build-Session muss die Such-URL-Struktur reverse-engineeren. Vermutete URL: `https://publications.iaa.org.il/search.aspx?query=...`. Mit `httpx` GET, `beautifulsoup4` parsen.

**Tool-Signatur:**
```python
@mcp.tool()
async def search_iaa(
    query: str,
    max_results: int = 10,
    report_type: Optional[str] = None,  # "report" | "atiqot" | "ha-esi"
) -> List[DAOPaper]:
    """Search IAA Publications — Israel Antiquities Authority Reports, ʿAtiqot,
    HA-ESI (Hadashot Arkheologiyot). Hebrew + English, primary source for
    Israeli excavation grey literature."""
```

**Robustheits-Hinweise:**
- Rate-Limit: höchstens 1 Request/Sekunde (kleines Site, kein Lastschutz erwartbar — wir sind höflich).
- Bei HTML-Layout-Änderung: klare Fehlermeldung, nicht silent failure.
- Sprachfeld setzen: HE wenn nur hebräischer Titel, EN wenn auch englischer Titel vorhanden.

### Adapter 3: `search_adaj`

**Quelle:** ADAJ (Annual of the Department of Antiquities of Jordan)
- **Endpoint:** http://publication.doa.gov.jo (Department of Antiquities, Jordan)
- **API-Status:** Wie IAA: HTML-Scraping.
- Inhalt: Jahresbände seit 1951, Grabungsberichte aus Jordanien.

**Tool-Signatur:**
```python
@mcp.tool()
async def search_adaj(
    query: str,
    max_results: int = 10,
    year_from: Optional[int] = None,
    year_to: Optional[int] = None,
) -> List[DAOPaper]:
    """Search ADAJ — Annual of the Department of Antiquities of Jordan.
    Primary source for Jordanian excavation reports. English-language."""
```

**Stretch-Goal:** Wenn `publication.doa.gov.jo` schwer scraping-bar ist (alte Site-Struktur, Session-Cookies, JavaScript-Pflicht): Fallback auf Trefferliste via Google-Site-Search oder Zenon-DAI-Filter (ADAJ ist in Zenon teilindexiert) — in der Build-Session entscheiden.

---

## V. MVP — Augmentation-Layer

### Resolver: `resolve_author`

**Zweck:** Author-Disambiguation für Levante-Archäologen. Eliminiert Halluzinationen wie "Avraham Rosen" → real "Steven A. Rosen".

**Datenquellen (in dieser Reihenfolge):**
1. **Lokale Override-Liste** `data/authority_overrides.yml` — für DAO-Spezifika, Kollegium-interne Disambiguierungen.
2. **Wikidata SPARQL** — `https://query.wikidata.org/sparql`.
3. **GND (DNB)** — Fallback, wenn Wikidata leer (über `lobid.org/gnd` API).

**Tool-Signatur:**
```python
@mcp.tool()
async def resolve_author(
    name_string: str,
    domain_hint: str = "archaeology",
) -> ResolvedAuthor:
    """Resolve an author name to canonical identity via Wikidata + local
    override list. Returns canonical name, Wikidata Q-ID, GND-ID, ORCID,
    name variants, and primary research domain. Use when LLM might hallucinate
    name variants (e.g. 'A. Rosen' → 'Steven A. Rosen' vs 'Arlene M. Rosen')."""
```

**Output-Modell:**
```python
class ResolvedAuthor(BaseModel):
    name_canonical: str
    name_variants: List[str]
    q_id: Optional[str] = None  # Wikidata
    gnd_id: Optional[str] = None
    orcid: Optional[str] = None
    viaf_id: Optional[str] = None
    domain: Optional[str] = None  # "Levant archaeology" / "biblical studies" / ...
    affiliation_current: Optional[str] = None
    birth_year: Optional[int] = None
    death_year: Optional[int] = None
    sites_associated: List[str] = []  # falls aus Override-Liste
    source: str  # "wikidata" | "gnd" | "override"
```

**SPARQL-Beispiel (Steven A. Rosen):**
```sparql
SELECT ?person ?personLabel ?birth ?affiliation ?orcid WHERE {
  ?person rdfs:label ?label .
  FILTER(CONTAINS(LCASE(?label), "rosen")) .
  ?person wdt:P106 ?occupation .
  FILTER(?occupation IN (wd:Q3621491, wd:Q1622272))  # archaeologist, professor
  OPTIONAL { ?person wdt:P569 ?birth }
  OPTIONAL { ?person wdt:P108 ?affiliation }
  OPTIONAL { ?person wdt:P496 ?orcid }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en" }
}
LIMIT 10
```

Bei mehreren Kandidaten: Score nach (a) Domain-Match, (b) Affiliation-Match (Ben Gurion University für Steven A. Rosen, falls bekannt), (c) Geburtsjahr-Plausibilität. Bei Score-Gleichstand: alle Kandidaten zurückgeben mit `verification_note`.

### Lokale Override-Liste `data/authority_overrides.yml`

Vorgeschlagenes Schema und Initial-Bestand:

```yaml
authors:
  - canonical: "Steven A. Rosen"
    variants: ["S.A. Rosen", "Rosen, S.A.", "Steven Rosen"]
    q_id: "Q7613131"  # falls vorhanden, sonst weglassen
    domain: "Levant archaeology, lithics, Negev Highlands Survey"
    affiliation: "Ben Gurion University"
    sites: ["Negev Highlands", "Camel Site"]

  - canonical: "Arlene M. Rosen"
    variants: ["A.M. Rosen", "Rosen, A.M."]
    domain: "Paleoenvironment, geoarchaeology, Levant"
    affiliation: "University of Texas at Austin"

  - canonical: "Rudolph Cohen"
    variants: ["R. Cohen", "Cohen, R.", "Rudolph Cohen (IAA)"]
    domain: "Negev fortresses, IAA excavations 1970s-90s"
    affiliation: "Israel Antiquities Authority (deceased 2007)"
    sites: ["Kadesh-Barnea", "ʿEn Ḥaṣeva", "Negev fortresses"]
    note: "Häufig verwechselt mit Mark Cohen (Assyriologe) oder Andrew Cohen."

  - canonical: "Amihai Mazar"
    variants: ["A. Mazar", "Mazar, A.", "Ami Mazar"]
    q_id: "Q3266226"
    domain: "Iron Age Levant, Tel Rehov"
    affiliation: "Hebrew University of Jerusalem"
    note: "Nicht zu verwechseln mit Eilat Mazar (Jerusalem-Ausgrabungen) oder Benjamin Mazar."

  - canonical: "Eilat Mazar"
    variants: ["E. Mazar", "Mazar, E."]
    domain: "Jerusalem archaeology, City of David"
    affiliation: "Hebrew University of Jerusalem"

  - canonical: "Israel Finkelstein"
    variants: ["I. Finkelstein", "Finkelstein, I."]
    q_id: "Q717237"
    domain: "Iron Age Levant, Low Chronology"
    affiliation: "Tel Aviv University / University of Haifa"

  - canonical: "Yohanan Aharoni"
    variants: ["Y. Aharoni", "Aharoni, Y."]
    domain: "Biblical archaeology, Beersheba, Tel Arad"
    affiliation: "Tel Aviv University (deceased 1976)"
```

Diese Liste wird in der ersten Build-Session mit den Belegfällen aus Patricks 2026-05-15-Test seed-befüllt, danach von Patrick organisch erweitert (z.B. wenn er auf eine neue Mehrdeutigkeit stößt).

---

## VI. Output-Schema

Zentrales Pydantic-Modell für **alle** Such-Tools:

```python
from pydantic import BaseModel, Field, HttpUrl
from typing import Optional, List
from enum import Enum

class PublicationStatus(str, Enum):
    PUBLISHED = "published"
    FORTHCOMING = "forthcoming"
    PREPRINT = "preprint"
    UNKNOWN = "unknown"

class DAOPaper(BaseModel):
    # Grundmetadaten
    title: str
    authors: List[str]  # Free-Text wie im Adapter geliefert
    authors_resolved: Optional[List[ResolvedAuthor]] = None  # populated wenn resolve_author aufgerufen
    year: Optional[int] = None
    journal_or_volume: Optional[str] = None
    pages: Optional[str] = None
    doi_or_id: str  # DOI / Zenon-ID / IAA-Report-Nr / ADAJ-Band-Nr
    source: str  # "zenon" | "iaa" | "adaj"
    open_access_url: Optional[HttpUrl] = None
    landing_page_url: Optional[HttpUrl] = None

    # Sprache + Kontext
    language: str = "und"  # ISO-639-1, "und" für undetermined
    abstract: Optional[str] = None

    # Archäologische Metadaten (MVP: leer, populated wenn site-/period-resolver verfügbar)
    site_ids: List[str] = []
    periods: List[str] = []
    regions: List[str] = []

    # Verifikations-Status
    publication_status: PublicationStatus = PublicationStatus.UNKNOWN
    verification_note: Optional[str] = None  # ⚠️-Hinweise: "Site-ID nicht aufgelöst", "Sprache geschätzt", etc.
```

**Hinweis:** `site_ids`, `periods`, `regions` bleiben im MVP **leer** — sie werden erst in Iteration 2 mit Site-Resolver / Periodisierungs-Expansion populated. Die Felder im Schema vorhalten, damit Konsumenten ihre Logik nicht später ändern müssen.

---

## VII. Test-Suite — Akzeptanzkriterium

### Eingefrorene Verifikations-Refs aus Patricks 2026-05-15-Test

In `tests/test_verification_suite.py`:

```python
VERIFICATION_REFS = [
    {
        "id": "ben-ami-2024-levant",
        "expected_status": "found",
        "query": "Ben-Ami Standing at the crossroads ʿEn Ḥaṣeva Iron Age IIA",
        "via_tool": "search_zenon",  # primärer Treffer-Pfad
        "expected_year": 2024,
        "expected_journal": "Levant",
        "note": "Echte Publikation, sollte über Zenon und IAA auffindbar sein."
    },
    {
        "id": "ben-ami-2026-levant-58-1",
        "expected_status": "NOT_FOUND",
        "query": "Ben-Ami Weiss Erickson-Gini Boaretto Fortresses frontiers Levant 2026",
        "via_tool": "any",
        "note": "VERDACHT GETEILTE HALLUZINATION — konvergent in 3 LLM-Outputs. "
                "Tool muss leer zurückgeben. Falsch-positiv wäre Bug — würde "
                "bedeuten, dao-paper-search echotnt die LLM-Halluzination."
    },
    {
        "id": "bienkowski-tebes-2024-peq",
        "expected_status": "found",
        "query": "Bienkowski Tebes PEQ 2024",
        "via_tool": "search_zenon",
        "expected_year": 2024,
        "expected_journal": "PEQ",
        "note": "PEQ ist im DAI-Bestand. Plausible Co-Autoren-Kombination."
    },
    {
        "id": "cohen-yisrael-1995-basor-298",
        "expected_status": "found",
        "query": "Cohen Yisrael BASOR 298 1995",
        "via_tool": ["search_zenon", "search_iaa"],
        "expected_year": 1995,
        "expected_journal": "BASOR",
        "expected_volume": "298",
        "note": "Klassische Negev-Festungen-Ref, IAA-Bestand."
    },
    {
        "id": "carmi-segal-2007-iaa-c14",
        "expected_status": "found",
        "query": "Carmi Segal radiocarbon Cohen Bernick-Greenberg IAA",
        "via_tool": "search_iaa",
        "note": "¹⁴C-Beitrag in IAA-Bericht Cohen/Bernick-Greenberg. "
                "Via publications.iaa.org.il zu verifizieren."
    },
]
```

**Akzeptanz:**
- 1, 3, 4, 5 müssen gefunden werden mit korrekter Bibliographie (Year/Journal/Volume).
- **2 muss leer zurückkommen** — der Test prüft explizit, dass dao-paper-search keine Halluzinations-Echos liefert.
- Tests laufen mit `pytest -v tests/test_verification_suite.py`.
- VCR-Cassettes für reproduzierbare CI (`pytest-vcr` oder `respx`).

### Weitere Tests

- `test_zenon.py`: Mock SRU-Response, Adapter-Parsing, Pydantic-Validation.
- `test_iaa.py`: Mock HTML-Response (fixiertes Snapshot von Live-Seite), Scraper-Robustheit gegen Layout-Drift.
- `test_adaj.py`: analog.
- `test_wikidata.py`: SPARQL-Mock, Disambiguations-Scoring, Override-Listen-Vorrang.
- `test_models.py`: Pydantic-Schema-Validation, Enum-Werte, Default-Felder.

---

## VIII. Nicht-Anforderungen (MVP-Scope-Schutz)

- ❌ **Citation-Graph** (`cited_by`, `references`) — Konsument delegiert an `paper-search.search_semantic` / `search_crossref` für DOI-Treffer.
- ❌ **GROBID/AnyStyle.io** Free-Text-Reference-Parsing — Iteration 2/3.
- ❌ **Tier-2-Quellen** (Propylaeum, JSTOR, Persée, IxTheo, OpenEdition, Gnomon Online, TOCS-IN) — separate PRs nach MVP, jeweils ein Adapter pro PR.
- ❌ **Site-ID-Resolver** via iDAI.gazetteer / Pleiades — Iteration 2 (Felder bereits im Schema vorhalten).
- ❌ **Periodisierungs-Expansion** (Iron IIA ↔ Salomonisch ↔ 10. Jh.) — Iteration 2.
- ❌ **Mehrsprachige Query-Translation** (DE→HE/AR) — Iteration 3.
- ❌ **OCR-Pipeline** für gescannte PDFs (alte ZDPV/ADAJ-Bände) — separater MCP, falls überhaupt.
- ❌ **Année Philologique-Adapter** — ToS-Risiko (Brepols-Lizenz verbietet typischerweise automated access), Park-Liste.
- ❌ **Eigenes Cloud-Hosting** — bleibt lokal via uvx, DSGVO-konform.

---

## IX. Integration in OpenCode (nach Build)

### `~/.config/opencode/opencode.jsonc` ergänzen

Neuer Eintrag im `mcp`-Block, parallel zu `paper-search`:

```jsonc
"dao-paper-search": {
  "type": "local",
  "command": [
    "/opt/homebrew/bin/uvx",
    "--from", "git+file:///Users/patrick/Documents/Aktuell/dao-paper-search-mcp",
    "python", "-m", "dao_paper_search_mcp.server"
  ],
  "enabled": true,
  "environment": {
    "WIKIDATA_USER_AGENT": "dao-paper-search-mcp/0.1 (patrick.leiverkus@uni-oldenburg.de)",
    "DAO_PAPER_SEARCH_RATE_LIMIT_MS": "1000"
  }
}
```

### `~/.config/opencode/agent/research.md` ergänzen

In der Werkzeuge-Sektion (`## Werkzeuge & wann du sie nutzt`): die bereits vorhandene Zeile zu `dao-paper-search` (aktuell als "sobald verfügbar" markiert) durch eine echte Routing-Empfehlung ersetzen:

```markdown
- **dao-paper-search-mcp** — DAO-Spezialquellen (Zenon DAI, IAA Publications, ADAJ) + Wikidata-Author-Disambiguation. **Erste Wahl** bei:
    - Levante-Archäologie (Iron Age, Bronze Age, biblische Archäologie)
    - Deutsch-/Hebräisch-/Arabisch-sprachige Forschung
    - IAA- und DoA-Jordan-Grabungsberichte
    - Author-Verifikation bei mehrdeutigen Namen (Cohen, Mazar, Rosen)
  Output enthält `verification_note` bei unsicheren Treffern.
```

Routing-Heuristik in `## Verifikations-Disziplin` automatisch mit-wirksam: jede Ref braucht mindestens ein strukturiertes Tool zur Bestätigung — Zenon-DAI-Treffer zählt jetzt als gültige strukturierte Verifikation.

---

## X. Implementation-Reihenfolge (für die Build-Session)

1. **Repo scaffolden** — `pyproject.toml`, `LICENSE`, `README.md`, `.gitignore`, Initial-Commit, `main`-Branch.
2. **FastMCP-Server-Skelett** mit Health-Check-Tool (`@mcp.tool() async def ping() -> str`). `opencode mcp list` muss "connected" zeigen.
3. **Pydantic-Output-Schema** (`models.py`) festziehen — `DAOPaper`, `ResolvedAuthor`, `PublicationStatus`.
4. **`search_zenon`-Adapter** mit SRU-Client. Tests gegen Mock + Live. Verifikations-Ref 1, 3 müssen gefunden werden.
5. **`search_iaa`-Adapter** (HTML-Scraping mit BeautifulSoup, vorsichtige Rate-Limits). Verifikations-Ref 5 muss gefunden werden.
6. **`search_adaj`-Adapter** (analog IAA).
7. **`resolve_author`-Tool** mit Wikidata-SPARQL + Override-YAML. `authority_overrides.yml` seed-befüllen.
8. **Verifikations-Test-Suite** (`test_verification_suite.py`) — Ref 2 muss leer zurückkommen, 1/3/4/5 mit korrekten Metadaten.
9. **opencode.jsonc + research.md** Update lokal anwenden (Patrick reviewt vor commit).
10. **End-to-End-Test:** Patrick stellt die Negev-Festungs-Frage erneut in OpenCode. Erwartet: `dao-paper-search.search_zenon` und `search_iaa` werden aufgerufen, Output enthält strukturierte Refs, halluzinierte 2026-Levant-Ref fehlt korrekt, korrekte Cohen-BASOR-Bandzahl (236, nicht 235) konvergent von paper-search + dao-paper-search bestätigt.

Pro Schritt: kleiner Commit. Auf `main` direkt arbeiten (kein PR-Workflow nötig für Single-Maintainer-Repo im MVP).

---

## XI. Verifikation nach Build

1. ✅ `opencode mcp list` zeigt `dao-paper-search [connected]`.
2. ✅ `pytest` läuft grün, insbesondere `test_verification_suite.py`.
3. ✅ End-to-End-Test in OpenCode-Session:
   - research-Agent ruft `dao-paper-search.search_zenon` und `paper-search.search_crossref` parallel auf.
   - Output enthält Cohen 1979 BASOR **236** (nicht 235) — konvergent von zwei unabhängigen Tools.
   - Steven A. Rosen erscheint korrekt aufgelöst, nicht "Avraham Rosen".
   - Ben-Ami 2026 Levant 58(1):25–42 ist im finalen Forschungsstand **nicht enthalten**, oder explizit mit `[VERIFIZIEREN]` markiert.
4. ✅ Latenz: dao-paper-search-Calls < 5 s pro Such-Operation (sonst Rate-Limit-Probleme oder Scraper-Optimierung nötig).

---

## XII. README.md — Mindestinhalt

- **Purpose-Statement** (1 Absatz): "Vertikaler MCP-Server für die DAO-Domäne (Levante-Archäologie, biblische Archäologie). Komplementär zu paper-search-mcp."
- **Quellen-Liste** mit Status (Tier 1: Zenon, IAA, ADAJ; Tier 2: TBD).
- **Install:** `uvx --from git+https://github.com/<owner>/dao-paper-search-mcp python -m dao_paper_search_mcp.server` (Repo-Pfad nach Push anpassen).
- **OpenCode-Konfig-Beispiel** (Snippet aus Abschnitt IX).
- **Architekturhinweis:** "Ruft nicht intern paper-search auf — Cross-Validation via Tool-Unabhängigkeit."
- **Authority-Overrides:** wie man die Liste erweitert.
- **Test-Suite:** `pytest` + Hinweis auf eingefrorene Verifikations-Refs.
- **Lizenz + Disclaimer:** MIT, kein Cloud-Upload, DSGVO-konform.

---

## XIII. Bekannte offene Fragen für die Build-Session

1. **Zenon-DAI-Endpoint:** SRU bestätigen vs REST-API. Falls SRU defekt, REST-API verwenden. Notfalls Kontakt zu DAI-IT (Reinhard Förtsch / Marcel Riedel).
2. **IAA-Such-URL-Struktur:** Reverse-Engineering nötig. Falls Site gegen Scraping abgesichert ist (Cloudflare, JS-Pflicht): playwright-Headless-Fallback erwägen, aber für MVP zurückstellen.
3. **ADAJ-Verfügbarkeit:** `publication.doa.gov.jo` ist historisch instabil. Fallback auf Zenon-DAI-Filter (ADAJ teilindexiert) akzeptabel für MVP.
4. **Wikidata-Rate-Limits:** öffentliche SPARQL-Endpoint ist großzügig, aber bei aggressivem Resolver-Einsatz throttling möglich. Pro Recherche-Session typisch < 20 resolve-Calls — sollte ok sein.
5. **Repo-Hosting:** zunächst lokal `git+file://`-Source. Push auf GitHub später, sobald Patrick entscheidet, ob privat oder öffentlich.

---

## XIV. Längerfristige Architektur-Vision (post-MVP)

In Reihenfolge der Priorität:

1. **Tier-2-Quellen** (Propylaeum, IxTheo, Persée, OpenEdition, Gnomon Online, TOCS-IN) — je ein Adapter pro PR.
2. **Site-ID-Resolver** via iDAI.gazetteer + Pleiades — populiert `site_ids`-Felder.
3. **Periodisierungs-Expansion** — Query "Iron IIA" trifft auch "10. Jh. v.Chr.", "Salomonisch", regionale Schulen (Finkelstein-LC / Mazar-MCC / Aharoni-HC).
4. **Mehrsprachige Query-Translation** — DE→HE/AR via Patricks `@hebrew`/`@arabic`-Subagenten, dann Suche in lokalen Datenbanken.
5. **GROBID-Reference-Parser** für PDF-Volltexte aus IAA/ADAJ — Free-Text-Reference-Extraction.
6. **Zotero-Push-Tool** — Trefferlisten direkt in Zotero-Collection schreiben.
7. **OCR-Pipeline** für gescannte alte Bände (ZDPV vor 1995, ADAJ vor 2000) — vermutlich separater MCP.
8. **AnPh-Adapter** (Tier 4, parkt) — falls UB-Lizenz-Team explizites OK gibt und ein Reverse-SSH-Tunnel zu UB-Netz steht.

Diese Roadmap ist **nicht** Teil des MVP. Nur erwähnt, damit die Build-Session keine Architektur-Entscheidungen trifft, die spätere Erweiterungen verbauen.

---

## Kontakt / Rückfragen

Patrick Leiverkus (UB Oldenburg / DAO). Bei Detailfragen zu Quellen-APIs, Schema-Erweiterungen, Override-Listen-Befüllung, oder Integration mit OpenCode: in der CC-Session direkt klären. Bei wiederholten Fehlern beim Scrapen einer Quelle: nicht stundenlang debuggen — Adapter als "MVP-incomplete" markieren und in die offenen-Fragen-Liste hochziehen.
