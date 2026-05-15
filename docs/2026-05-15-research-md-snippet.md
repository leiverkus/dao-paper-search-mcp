# `research.md` Prompt-Snippet: Inline-Citation-Disziplin

**Status:** Bereit zum Einfügen in `~/.config/opencode/agent/research.md`.
**Datum:** 2026-05-15
**Ziel:** Schwächere Schicht (Prompt-Regel) als Belt-and-Suspenders neben den strukturellen Schema-Lösungen.

---

## Warum überhaupt Prompt-seitig?

Empirie vom 2026-05-15: Die strukturelle Lösung (Schema mit `inline_citation.markdown_recommended`) wirkt **nur** dort, wo der Treffer durch ein Tool mit dem Schema fließt. Solange `paper-search-mcp` das Schema nicht hat (siehe `2026-05-15-paper-search-mcp-pr-notes.md`), gibt es eine Lücke: Crossref-/OpenAlex-/Semantic-Scholar-Treffer kommen ohne `markdown_recommended` zurück und der Agent rendert sein Default-Format `[(domain)](url)`.

Diese Prompt-Regel kompensiert für genau diese Lücke — und nichts darüber hinaus.

**Caveat:** Im 2026-05-15-Test war die Prompt-Regel allein zu schwach gegenüber dem Output-Shape-Effekt. Sie wirkt nur in Kombination mit klarer Tool-Disziplin (welcher Treffer hat welche Identifier).

## Wo einfügen

In `research.md` unter `## Verifikations-Disziplin` (oder einem äquivalenten Abschnitt zu Output-Format-Regeln), als neuer Unter-Block.

## Snippet

```markdown
### Inline-Citation-Format (strikt)

**Regel:** Jede Inline-Citation im Lesefluss MUSS die Form
`[(Author Year)](url)` haben, sobald Author und Year bekannt sind und
eine URL existiert. **Niemals** die Form `[(domain)](url)` für
DOI-Treffer mit bekanntem Author-Year.

Beispiele (korrekt):

- `[(Cohen 1979)](https://doi.org/10.2307/1356668)`
- `[(Boaretto et al. 2010)](https://doi.org/10.1017/S0033822200044982)`
- `[(Bruins & van der Plicht 2009)](https://www.tandfonline.com/doi/abs/10.1179/204047809x439460)`

Beispiele (falsch — nicht so rendern):

- ❌ `[(doi.org)](https://doi.org/10.2307/1356668)` — Author-Year fehlt
- ❌ `[(tandfonline.com)](https://www.tandfonline.com/doi/abs/...)` — Author-Year fehlt
- ❌ `(Cohen 1979: 61–79)` — kein Link, obwohl URL vorhanden

**Ausnahmen** (Domain-Form ist erlaubt):

- Web-Treffer ohne klare Author-Year-Metadaten (Blog-Posts, Institutional
  Pages) → `[(domain — Titel-Fragment)](url)`
- Aggregator-Treffer (Google Books, ResearchGate, Academia.edu) →
  immer mit `⚠️`-Präfix: `⚠️[(researchgate.net)](url)` oder
  `⚠️[(academia.edu — Title)](url)`
- Print-only (keine URL, kein digitaler Anker) → bare Form
  `Cohen 1979: 61–79` ohne Link-Wrapper. Keine erfundene URL.

**Wenn ein Tool das Feld `inline_citation.markdown_recommended` liefert
(dao-paper-search aktuell, paper-search-mcp künftig): den String dort
wörtlich kopieren. Nicht reformatieren.** Das Tool hat die Form-Wahl
bereits getroffen und ⚠️-Präfixe wo nötig schon gesetzt.

**Warum diese Regel:**
Author-Year direkt im Linktext erlaubt dem Leser, Inline-Citation und
Bibliographie zu verknüpfen, ohne Maus-Hover. Reine Domain-Form
(`[(doi.org)]`) ist informationsfrei — sie sagt nur "Link existiert",
nicht "wer/wann". Empirie 2026-05-15 (Qwen 3.6 Plus mit dao-searxng
aktiv) zeigt: der Agent rendert Author-Year-Form natürlich, wenn URLs
und Metadaten parallel im Kontext sind. Diese Regel formalisiert das.
```

## Optional: Erweiterung in `## Werkzeuge & wann du sie nutzt`

Beim `dao-paper-search-mcp`-Block (siehe `opencode-integration.md`)
einen Hinweis ergänzen:

```markdown
**Citation-Render-Hinweis:** Jeder `DAOPaper` aus diesem Tool trägt
ein `inline_citation`-Objekt. Bei Inline-Citations das Feld
`inline_citation.markdown_recommended` wörtlich kopieren — es enthält
bereits die korrekte Form (Author-Year für akademische Treffer,
⚠️-Präfix wo nötig). Nicht aus `doi_or_id` und `landing_page_url`
selbst rekonstruieren.
```

Analog beim `paper-search-mcp`-Block, **sobald** dort das gleiche
Schema verfügbar ist:

```markdown
**Citation-Render-Hinweis (ab paper-search-mcp v?.?):** Treffer tragen
ein `inline_citation.markdown_recommended`-Feld. Wörtlich kopieren.
```

## Anti-Patterns, die man vorsorglich abfangen sollte

Im Snippet könnten optional zusätzlich konkrete Anti-Patterns benannt
werden, die der Agent in der Vergangenheit gezeigt hat:

- ❌ Bibliographie-Stil im Fließtext: `(Cohen 1979: 61–79)` neben einem
  Fakt, obwohl eine DOI verfügbar ist.
- ❌ Doppel-Citation: `[(Cohen 1979)](url) (Cohen 1979)` — Linktext
  und Author-Year-Form gleichzeitig.
- ❌ Mehrere Domains pro Link: `[(doi.org / jstor.org)](url)` —
  immer nur eine kanonische Domain.

## Test nach Einbau

Negev-Festungen-Frage erneut an Qwen 3.5 122B:

- Erwartet: Inline-Refs in Author-Year-Form auch ohne dao-searxng-Beimischung
- Falls Inline-Refs weiterhin in Domain-Form: die Regel ist zu schwach,
  Output-Shape-Lock-In gewinnt → A (PR an paper-search-mcp) ist die
  einzige verbleibende Hebelung
