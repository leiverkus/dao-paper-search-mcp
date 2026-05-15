# Migration: `paper-search-mcp` → `dao-paper-search-mcp` cross-platform tools

**Stand:** 2026-05-15, nach Sprint 3
**Vorbedingung:** Sprints 1–3 abgeschlossen (Crossref, OpenAlex, Semantic Scholar, arXiv, CORE, Zenodo live in `dao-paper-search-mcp`)

---

## Warum migrieren

Nach Sprint 1–3 sind die sechs paper-search-mcp-Adapter, die du tatsächlich benutzt, in dao-paper-search-mcp reimplementiert. Jeder liefert das `inline_citation`-Schema mit `markdown_recommended`. Sobald paper-search-mcp aus dem OpenCode-Setup entfernt ist, rendert der Agent Inline-Citations strukturell konsistent in Author-Year-Form — über alle Treffer hinweg, nicht nur für die DAO-spezifischen Quellen.

paper-search-mcp bleibt nützlich, falls du je einen Adapter brauchst, den dao-paper-search-mcp nicht hat (PubMed, DBLP, bioRxiv, IEEE Xplore, ACM, Google Scholar). Für 95 %+ deiner aktuellen Recherchen reichen die sechs reimplementierten.

---

## Schritt 1 — Dual-Run (eine Woche)

**Bevor** du paper-search-mcp deaktivierst, fahre eine Woche im Dual-Mode: beide MCPs aktiv. Der Agent darf wählen.

Das gibt dir empirische Evidenz, ob:
- Die neuen Adapter die Trefferqualität der alten erreichen
- Inline-Citations konsistent in Author-Year-Form erscheinen (`[(Cohen 1979)](https://doi.org/...)`)
- Latenz im erwarteten Bereich liegt
- Keine Adapter-Bugs auftreten, die deine Recherche-Pipeline brechen

**Was du währenddessen monitort:** ob der Agent von selbst `dao-paper-search.search_crossref` statt `paper-search.search_crossref` aufruft. Wenn ja → Migration safe. Wenn der Agent paper-search-mcp bevorzugt → `research.md` schärfen (siehe Schritt 3).

---

## Schritt 2 — paper-search-mcp deaktivieren (nicht löschen)

In `~/.config/opencode/opencode.jsonc`:

```jsonc
"paper-search": {
  // unverändert lassen — nur enabled umschalten
  "enabled": false,
  // ...rest unverändert
}
```

OpenCode neu starten:

```bash
# OpenCode beenden + neu starten
opencode mcp list
# Erwartung: paper-search [disabled], dao-paper-search [connected]
```

**Begründung für `enabled: false` statt `rm`:** wenn du in drei Wochen einen exotischen Adapter brauchst (z. B. ACM Digital Library für einen CS-Aspekt der Forschung), kannst du paper-search-mcp punktuell wieder aktivieren. Block bleibt im Config liegen als Rollback-Pfad.

---

## Schritt 3 — `research.md` aktualisieren

In `~/.config/opencode/agent/research.md` (oder wo immer dein Werkzeug-Routing-Block steht), den `paper-search-mcp`-Block durch den `dao-paper-search-mcp`-Block ersetzen:

```markdown
- **dao-paper-search-mcp** — Vertikale DAO-Quellen (Zenon DAI, IAA Publications, ADAJ
  + SHAJ + Munjazat + JERD + Athar) **plus** kuratierte horizontale Quellen
  (Crossref, OpenAlex, Semantic Scholar, arXiv, CORE, Zenodo).

  **Erste Wahl bei:**
    - Levante-Archäologie → `search_zenon`, dann `search_iaa`, `search_adaj`
    - DOI-Verifikation eines Treffers → `search_crossref` (kanonische DOI-Quelle)
    - Cross-disziplinärer Discovery, Citation-Graph → `search_openalex`,
      `search_semantic_scholar`
    - Recent Preprints (DH-Methoden, NLP, RAG, GIS, 3D) → `search_arxiv`
    - OA-PDFs paywalled Journal-Artikel, Theses, Grey Literature → `search_core`
      (braucht `CORE_API_KEY` env var)
    - Research Data, Software, FundedMandates-Compliance-Versions → `search_zenodo`
    - Author-Verifikation bei mehrdeutigen Namen → `resolve_author`
    - Site-Disambiguation und stabile Anker-IDs → `resolve_site`

  **Citation-Render-Hinweis:** Jeder Treffer trägt ein `inline_citation`-Objekt.
  Bei Inline-Citations im Lesefluss das Feld `inline_citation.markdown_recommended`
  **wörtlich kopieren** — nicht aus `doi_or_id` oder `landing_page_url` selbst
  rekonstruieren. Aggregator-Treffer (CORE via ResearchGate/Academia.edu) tragen
  ein ⚠️-Präfix; das gehört in den finalen Text.
```

Den bestehenden `paper-search-mcp`-Routing-Block daneben durchstreichen oder einfach löschen — er ist deaktiviert.

---

## Schritt 4 — Smoke-Test

Die gleiche Negev-Festungen-Anfrage wie am 2026-05-15-Abend nochmal stellen:

> Recherchiere den aktuellen Forschungsstand zur Datierung der Negev-Eisenzeit-Festungen — eine der kontroversesten Fragen in der südlevantinischen Archäologie. Strukturiere die Antwort nach den drei Positionsschulen (salomonisch, Low Chronology, Revisionismus), ihren Hauptargumenten, und der Rolle der neuen ¹⁴C-Daten.

**Erwartung:**

- ✅ Alle Inline-Refs in der Form `[(Author Year)](https://doi.org/...)`, **nicht** `[(doi.org)](...)`
- ✅ Latenz im 100–200-s-Bereich (vergleichbar mit dem 2026-05-15-Lauf, ggf. minimal besser durch Wegfall des paper-search-mcp-Round-Trips)
- ✅ Bibliographie am Ende unverändert; gleiche 9 Refs wie im Vor-Test
- ✅ Wenn `search_core` Treffer aus ResearchGate findet: ⚠️-Marker im Inline-Link sichtbar

**Wenn Inline-Refs weiterhin in Domain-Form rendern:** der Agent hat den `research.md`-Hinweis nicht aufgenommen. In dem Fall:
1. OpenCode komplett neu starten (Prompt-Reload)
2. Wenn Problem bleibt → die Tool-Docstrings in `search_crossref` etc. lesen das Modell durchaus, aber manche Modelle ignorieren sie. Prompt-Verschärfung in `research.md` oder Schema-Discovery prüfen (`opencode mcp inspect dao-paper-search`).

---

## Schritt 5 — Eine Woche laufen lassen, dann committen

Wenn nach einer Woche im Solo-Betrieb (paper-search-mcp deaktiviert) **keine** Lücke aufgefallen ist:

- `enabled: false` kann in `opencode.jsonc` bleiben (kostet nichts)
- Oder den ganzen `paper-search`-Block löschen + `paper-search-mcp` aus `uvx`-Cache entfernen:

```bash
uvx cache remove paper-search-mcp
```

**Falls eine Lücke auffällt** (z. B. du suchst etwas in PubMed, das via paper-search-mcp da war):
1. paper-search-mcp wieder via `enabled: true` aktivieren
2. Den fehlenden Adapter (PubMed o. ä.) auf die Pfad-II-Roadmap setzen
3. Sprint 4 planen — selektiv noch einen Adapter dazubauen

---

## Environment-Variables-Checkliste

Vor dem Migrationstart prüfen, dass die optionalen API-Keys gesetzt sind:

```bash
# Erforderlich für search_core
export CORE_API_KEY="<dein-token-von-core.ac.uk>"

# Optional für höhere S2-Quoten (sonst ~100 req/min)
export SEMANTIC_SCHOLAR_API_KEY="<dein-s2-token>"
```

In OpenCode-Config (`opencode.jsonc`) die `environment`-Sektion deines dao-paper-search-Blocks ergänzen, damit der MCP die Keys sieht:

```jsonc
"dao-paper-search": {
  "command": [ /* unverändert */ ],
  "enabled": true,
  "environment": {
    "WIKIDATA_USER_AGENT": "dao-paper-search-mcp/0.1 (patrick.leiverkus@uni-oldenburg.de)",
    "DAO_PAPER_SEARCH_RATE_LIMIT_MS": "1000",
    "CORE_API_KEY": "${CORE_API_KEY}",
    "SEMANTIC_SCHOLAR_API_KEY": "${SEMANTIC_SCHOLAR_API_KEY}"
  }
}
```

(OpenCode löst `${VAR}` aus deiner Shell-Umgebung auf.)

---

## Rollback (falls etwas hart bricht)

```bash
# In opencode.jsonc:
#   paper-search.enabled: true
#   dao-paper-search.enabled: false  (oder einfach beide aktiv lassen)
# OpenCode neu starten.
```

Kein Daten- oder Konfigurationsverlust — beide MCPs koexistieren problemlos. Der einzige Effekt: Citation-Format-Riss kehrt zurück.

---

## Was als nächstes (post-Migration)

Wenn nach einer Recherche-Woche alles stabil ist, sind die nächsten sinnvollen Schritte (optional, in der Reihenfolge):

1. **`paper-search-mcp` PR** ([docs/2026-05-15-paper-search-mcp-pr-notes.md](2026-05-15-paper-search-mcp-pr-notes.md)) — das `inline_citation`-Schema upstream einbringen, damit andere MCP-User auch davon profitieren. Reduziert auch die Notwendigkeit, dass *du* die sechs Adapter wartest.
2. **Tier-2-Adapter** für DAO-spezifische Quellen (Propylaeum, IxTheo, Persée, OpenEdition) — wenn die JS-Rendering-Hindernisse umgehbar werden.
3. **Verification-Suite erweitern** — pro neuem Adapter einen Acceptance-Fingerprint (analog `tests/test_verification_suite.py`) für Live-Tests gegen die echten APIs.
