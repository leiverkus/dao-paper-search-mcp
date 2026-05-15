# IAA Publications — Sondierungsbericht (Phase 1 IAA-MVP-Auflösung)

**Datum:** 2026-05-15
**Vorbedingung:** v0.4.0 gerade getaggt; IAA-Adapter steht auf `MVP-incomplete` mit `IAAUnavailableError`-Tripwire
**Ziel:** Welcher Pfad (Solr-direct / OAI-PMH / per-Collection-Scrape / Playwright) ist gangbar?

---

## Befunde

### 1. Solr-Endpoint via JS-Bundle (Option B) — **nicht gangbar**

Reverse-Engineered aus `/assets/cgi/js/search/query-screen.js`:

```js
uri = Bepress.cgiUrlBase + '/do/search/results/json';
theRequest = Y.io(uri, { method: 'GET', form: { id: formObject, state: formState } });
```

Die Route heißt `/do/search/results/json`. Getestet mit allen plausiblen Varianten:

| Variante | Status |
|---|---|
| `GET ...?q=Negev+fortress` | **404** |
| Mit `Referer` + Browser-UA + `Accept: application/json` | **404** |
| Mit `bp_plack_session`-Cookie nach Init | **404** |
| `POST /do/search/results/json` | **404** |
| `GET /cgi/search.cgi?q=...` | **404** |

`solr.pack.js` ist auf `Last-Modified: 2019-09-09`. BePress hat in den 7 Jahren seitdem höchstwahrscheinlich die Route migriert, den statischen Bundle aber nicht aktualisiert. Toter Code, der nur deshalb noch ausgespielt wird, weil die HTML-Templates ihn referenzieren.

→ **Option B ist tot.** Keine weitere Investition lohnt sich.

### 2. OAI-PMH (Option D) — **funktioniert sauber, ist der richtige Pfad**

`https://publications.iaa.org.il/do/oai/` ist voll funktionsfähig:

```
verb=Identify    → 200, korrekter OAI-Header
verb=ListSets    → 200, alle Collections enumerierbar:
                     publication:atiqot
                     publication:ha-esi
                     publication:hadashot_gallery
                     publication:cornerstone
                     publication:esi_english_series
                     publication:favissa
                     publication:ha_hebrew_series
                     publication:ha_esi_bilingual_series
                     publication:iaabookseries
                     …
verb=ListMetadataFormats → oai_dc, qualified-dublin-core (qdc), oai_etdms
verb=ListRecords (atiqot, 2024) → 100 Records pro Seite, resumptionToken-paginiert
```

**Sample-Record-Inhalt (Auszug):**

```xml
<header>
  <identifier>oai:publications.iaa.org.il:atiqot-1041</identifier>
  <datestamp>2024-11-25T06:52:19Z</datestamp>
  <setSpec>publication:atiqot</setSpec>
</header>
<metadata>
  <oai_dc:dc>
    <dc:title>Front Matter &amp; Editorial</dc:title>
    <dc:creator>Board, The Editorial</dc:creator>
    <dc:description>Table of contents and Editorial</dc:description>
    <dc:date>2024-11-26T10:12:05Z</dc:date>
    <dc:identifier>https://publications.iaa.org.il/atiqot/vol112/iss1/1</dc:identifier>
    <dc:identifier>info:doi/10.70967/2948-040X.1041</dc:identifier>
    <dc:identifier>https://publications.iaa.org.il/context/atiqot/article/1041/viewcontent/en_open_112.pdf</dc:identifier>
    <dc:subject>front matter</dc:subject>
    <dc:subject>editorial</dc:subject>
    …
  </oai_dc:dc>
</metadata>
```

**Wichtige Erkenntnis: IAA registriert DOIs.** Prefix `10.70967/` (DataCite via BePress). DOI ist als `info:doi/...` in `dc:identifier` enthalten — direkt extrahierbar.

Earliest datestamp 2000-01-19 — 26 Jahre Coverage. `adminEmail: dc-support@elsevier.com` (BePress ist Elsevier-owned).

### 3. Per-Collection-Landings (Option G) — **funktioniert auch, OAI-PMH ist aber überlegen**

- `/atiqot/all_issues.html` → SSR, listet alle Volumes mit Jahr (vol1 bis vol120)
- `/atiqot/vol116/iss1/` → SSR, listet alle Artikel der Issue mit `<span class="auth">`-Autorenangabe
- `/atiqot/vol116/iss1/3/` → SSR mit vollen `<meta name="bepress_citation_*">`-Tags inklusive DOI, PDF-URL, Volume/Issue/Pages

OAI-PMH erspart aber das pro-Artikel-HTML-Scraping (eine OAI-Anfrage liefert 100 Records mit allen Metadata vs. 100 HTML-Fetches), und die DC-XML-Struktur ist stabiler als BePress-HTML-Layouts. → **Option G als Backup behalten**, falls OAI je ausfällt.

### 4. Playwright (Option A) — **nicht nötig**

Da OAI-PMH funktioniert, lohnt sich die ~300-MB-Browser-Dep + 5–15-s-Latenz nicht. Aus der Optionenliste streichen.

---

## Empfehlung

**Sprint 5 (Option D, ~1 Tag):** IAA-Adapter neu auf OAI-PMH umbauen.

### Implementationsskizze

```python
# adapters/iaa.py (Reimplementation)

IAA_OAI_BASE = "https://publications.iaa.org.il/do/oai/"

# Set names cover IAA's whole catalogue; expose as enum so callers
# can scope to specific collections.
_SETS = {
    "atiqot": "publication:atiqot",
    "ha-esi": "publication:ha-esi",
    "ha-esi-bilingual": "publication:ha_esi_bilingual_series",
    "ha-hebrew": "publication:ha_hebrew_series",
    "esi-english": "publication:esi_english_series",
    "iaa-books": "publication:iaabookseries",
    "favissa": "publication:favissa",
    "cornerstone": "publication:cornerstone",
}

async def search_iaa_impl(query, max_results, collection=None, year_from=None, year_to=None):
    """
    Strategy: server-side year + set filtering via OAI verb=ListRecords;
    client-side keyword filtering against dc:title + dc:description +
    dc:subject. Paginate via resumptionToken until enough hits or end.
    """
    set_filter = _SETS.get(collection, None)  # None = all sets

    # Build base URL
    params = {
        "verb": "ListRecords",
        "metadataPrefix": "oai_dc",
    }
    if set_filter:
        params["set"] = set_filter
    if year_from:
        params["from"] = f"{year_from}-01-01"
    if year_to:
        params["until"] = f"{year_to}-12-31"

    # Paginate through resumption tokens
    matches: list[DAOPaper] = []
    resumption_token = None
    while True:
        if resumption_token:
            params = {"verb": "ListRecords", "resumptionToken": resumption_token}
        # ...httpx.get(IAA_OAI_BASE, params=params)
        # ...parse XML, iterate <record>, keyword-filter, append matches
        # ...stop when len(matches) >= max_results or no more pages
        if len(matches) >= max_results:
            break
    return matches[:max_results]


def _record_to_paper(record_xml) -> Optional[DAOPaper]:
    # Extract dc:title, dc:creator (multiple), dc:date, dc:identifier (multi),
    # dc:description, dc:subject (multi)
    # Split identifiers:
    #   - one is the landing URL (starts with https://publications.iaa.org.il/)
    #   - one carries the DOI (starts with "info:doi/")
    #   - sometimes a PDF URL (ends with .pdf)
    doi = ...  # strip "info:doi/" prefix
    landing_url = ...
    pdf_url = ...

    identifiers = Identifiers(doi=doi, iaa_pub_id=_extract_pub_id(landing_url))
    audit = Audit(primary_source=True, aggregator=False)
    inline_citation = build_inline_citation(
        authors=authors, year=year, pages=None, title=title,
        identifiers=identifiers, landing_page_url=landing_url,
        open_access_url=pdf_url, audit=audit,
    )
    return DAOPaper(...)
```

### Vor- und Nachteile

**Pro:**
- Stabile, dokumentierte Protokoll-Schicht (OAI-PMH 2.0 von 2002)
- Strukturiertes XML, keine HTML-Brüche zu fürchten
- DOIs sind drin (Author-Year-Inline-Form für jeden Treffer)
- Pagination via `resumptionToken` — explizit specifiziert
- 100 Records pro Round-Trip; eine 10-Jahre-Anfrage = 5–15 HTTP-Calls
- Erbt automatisch alle künftigen IAA-Collections, die als BePress-Sets registriert werden

**Contra:**
- Keine Volltextsuche — wir filtern client-seitig in dc:title / dc:description / dc:subject. Genauer als HTML-Listing-Match (Option G), aber kein echtes Phrase-Search. Bei typischen Levant-Anfragen ("Ḥaṣeva", "Cohen Negev", "Iron Age fortress") ausreichend, weil Titel + Subject die Schlüsselbegriffe meist enthalten.
- Eine breite Anfrage ohne Year-Filter würde theoretisch das ganze Archiv (~20k Records) abklappern → 200+ Round-Trips → ~40s. Mitigation: max-Pages-Limit (z. B. 20 Pages = 2000 Records) plus Empfehlung im Docstring, mindestens `year_from` zu setzen.
- Hebräisch-Volltextsuche kann durch dc:subject schwach abgebildet sein. Falls Bedarf konkret auftritt, später per-Article-Enrichment dazubauen.

### Tests

| Fall | Test |
|---|---|
| Happy path | OAI-Mock mit 1 Record, exakt-Match auf Title-Keyword |
| Mehrere Resumption-Tokens | OAI-Mocks für 2 Seiten, Pagination-Logic |
| Empty result list | OAI-Mock mit 0 Records |
| HTTP-Error | 5xx propagiert sauber |
| `collection`-Filter | Server-side `set=` Parameter forwarded |
| Year-Filter | `from=YYYY-01-01&until=YYYY-12-31` forwarded |
| DOI-Extraktion | `dc:identifier=info:doi/...` korrekt geparst |
| Multi-Author | mehrere `dc:creator`-Elemente werden in `authors`-Liste |
| `IAAUnavailableError` | wird **entfernt** — Tripwire entfällt mit OAI-PMH |

### Migration

- `IAAUnavailableError` entfernt (war Tripwire für JS-Rendering, jetzt obsolet)
- Frozen Verification-Fingerprint `iaa_carmi_segal_2007` von **xfail → expected pass** umstellen, falls Carmi/Segal 2007 via OAI auffindbar ist (vor Migration auf VCR-Cassette gegen Live-Endpoint prüfen)
- README `## Known limitations` Abschnitt entfernen (oder umformulieren auf "no full-text search yet")
- CHANGELOG-Eintrag in `[0.5.0]`-Block
- Release als `v0.5.0` mit GitHub-Release

Wenn das durchläuft, ist die einzige verbleibende Reibung im Repo `paper-search-mcp`-Migration auf User-Seite — der MVP-incomplete-Stempel ist weg, und eine `1.0.0` ist greifbar.

---

## Zusammenfassung in einem Satz

> **OAI-PMH läuft sauber, gibt uns 26 Jahre IAA-Coverage mit DOIs und allen Collections — Sprint 5 = ~1 Tag IAA-Adapter-Reimplementierung, dann ist `IAAUnavailableError` Geschichte und `v1.0.0` realistisch.**
