# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.7.1] - 2026-05-18

Schema v2.1 — DOI/URL link appended to `authoritative_bibliography_line`.

**Why:** Live test (2026-05-18, Qwen 3.5 122B, Negev-Festungen-Frage) showed
that Schema v2's `authoritative_bibliography_line` was Plain-Text with no
clickable links. Because Qwen 3.5 122B copies the field verbatim (exactly the
intended Single-Source-of-Truth behaviour), bibliography entries appeared as
bare author-date strings. The fix: the tool now appends the link, so the
literal-copy model produces linked references without reconstruction.

### Fixed

- `build_bibliography_line()` appends a DOI Markdown link
  (`DOI: [10.xxx](https://doi.org/10.xxx)`) when a DOI is registered, or a
  shortened URL link (`URL: [domain/…](url)`) when only a `primary_url` is
  available. No link is appended when neither is present.

### Added

- `_shorten_url_for_label()` helper — truncates long paths to `"domain/…"` so
  non-DOI URLs don't overflow bibliography lines.

### Tests

274 passed (was 271). 3 new tests: `test_shorten_url_for_label`,
`test_bibliography_line_no_doi_no_url_no_append`,
`test_bibliography_line_url_fallback_when_no_doi`.

---

## [0.7.0] - 2026-05-18

Schema v2 — radical inline-citation simplification plus structural
hallucination protection.

**Why:** Four iterations of agent testing (2026-05-15) showed three
divergent model behaviours on the v0.6.x multi-variant schema. Qwen
3.5 122B copied fields verbatim and reflexively picked
`markdown_domain` → `[(doi.org)](url)` instead of Author-Year.
Ring 2.6 1T ignored the schema entirely and rendered Author-Year
itself. Qwen 3.6 Plus mixed and matched. A Mistral Medium 3.5 run
(2026-05-18) added a new failure mode: the agent assigned the same
DOI to two different authors in one bibliography (`Aharoni 1976` vs.
`Finkelstein 1999`, identical DOI/title/pages), reconstructing the
author-year from training knowledge instead of the tool output.

### Breaking — `InlineCitation` schema collapse

Twelve fields removed:

- `primary_url`, `display_domain`
- `display_label_authoryear`, `display_label_domain`,
  `display_label_domain_title`, `display_label_doi`
- `markdown_authoryear`, `markdown_domain`, `markdown_domain_title`,
  `markdown_doi`
- `markdown_recommended`, `markdown_bibliography`

Five fields remain:

- `url` — the canonical link (replaces `primary_url`).
- `markdown` — single context-sensitive Inline-Markdown link, cascade
  Author-Year → Domain-Title → Domain → `fallback_text`, with
  `⚠️`-prefix for aggregator and warn-flagged hits.
- `authoritative_authors_label` (new) — plain-text Author-Year string
  (`"Finkelstein 1999"`) so agents render their own form from
  tool-authoritative data instead of training knowledge.
- `authoritative_bibliography_line` (new) — full reference-list line
  (`"Finkelstein, I. (1999). Title. *BASOR* 314, 55–70."`) for
  verbatim copy into the bibliography section. `None` when venue
  metadata is incomplete; in that case the agent must fall back to
  Author-Year + URL/DOI rather than reconstructing.
- `fallback_text` — unchanged.

### Added — `Venue` sub-model

New `models.Venue` (`name`, `volume`, `issue`, `pages`). Adapters
populate it from their API response and `build_inline_citation()`
threads it into `build_bibliography_line()`. All 10 adapters
(crossref, openalex, semantic_scholar, arxiv, zenon, iaa, adaj, core,
zenodo, biorxiv) pass `venue=...` to the builder.

### Added — inline author-label rules

- 1 author → `"Cohen 1979"`
- 2 authors → `"Cohen & Yisrael 1995"`
- 3 authors → `"Boaretto, Finkelstein & Shahack-Gross 2010"` (explicit,
  no et al. — three names still fit comfortably and read cleanly in
  prose)
- ≥4 authors → `"Bruins et al. 2011"`
- Particle names (`van der Plicht`, `von Daniken`) stay intact in both
  inline and bibliography forms; new `_PARTICLES` whitelist preserves
  multi-token family names across "Family, Given Particle" and
  "Given Particle Family" author-string shapes.

### Added — bibliography author rules

Full family + initial form (no et al. in the bibliography). Oxford
comma before the final `&`. Example:
`"Boaretto, E., Finkelstein, I., & Shahack-Gross, R."`.

### Tests

268 passed (was 241). 36 new and migrated tests in
`test_inline_citation.py` (single-author / two-author / three-author
explicit / et al. / particle / aggregator / print-only / Test 5
hallucination protection / venue cascade / authoritative label
edge cases).

### Title truncation

`_TITLE_MAX_LEN` raised from 50 to 60 characters for the
Domain-Title form (Briefing §II.B).

## [0.6.4] - 2026-05-15

Total domain-field blackout for DOI hits. v0.6.3 hid
`markdown_domain` and `display_label_domain` when a DOI was
registered. The agent in the next daily-driver run fixed body-text
rendering (now correctly Author-Year form) but **still rendered
`[(doi.org)](url)` in every bibliography entry**. The agent was
constructing the label from the remaining domain-revealing fields:
`display_domain` (value `"doi.org"`) and `markdown_domain_title`
(label `"(doi.org — Title)"`).

v0.6.4 hides those too. For DOI-bearing hits the only
domain-revealing trace left is parseable from `primary_url` itself,
which is harder for the agent to reach reflexively when constructing
a templated bibliography entry from individual fields.

### Changed
- `InlineCitation.display_domain`, `markdown_domain_title`, and
  `display_label_domain_title` are now `None` when
  `identifiers.doi` is set. These joined `markdown_domain` and
  `display_label_domain` (already hidden since v0.6.3) in the
  DOI-hit blackout.
- For non-DOI sources (Zenon, IAA pre-DataCite, ADAJ chapters,
  OpenAlex records without DOI, etc.) all these fields keep their
  previous behaviour. `[(zenon.dainst.org)]` and friends remain
  legitimate source-hint labels.

### Tests
- 2 updated tests reflecting the v0.6.4 blackout assertions.
- 256 unit tests passing, 3 xfailed.

### Why this iteration
Four converging daily-driver runs (v0.6.0, v0.6.1, v0.6.2, v0.6.3)
proved that the agent has a strong prior for rendering
`[(doi.org)](url)` in bibliography entries — it will construct this
label from any domain-revealing field we expose. The final
schema-side intervention is total field removal.

If v0.6.4 *still* doesn't suppress the rendering, the agent is
parsing `primary_url` directly to extract the domain, and
schema-side levers are exhausted. The fallback would be prompt-side
reinforcement via `~/.config/opencode/agent/research.md` — see
`docs/2026-05-15-research-md-snippet.md` for the prepared snippet.

## [0.6.3] - 2026-05-15

Hide the bare-domain variant from public exposure when a DOI is
registered. Three converging daily-driver runs (v0.6.0, v0.6.1,
v0.6.2) showed the agent reflexively picking ``markdown_domain``
(``[(doi.org)](url)``) for both body-text and bibliography
rendering — ignoring the docstring guidance pointing at
``markdown_recommended`` (Author-Year form) and the context-named
pflichtfeld ``markdown_bibliography`` (DOI-string form).

When a DOI exists, the bare-domain label is structurally useless —
it tells the reader nothing about *which* DOI. The Author-Year and
DOI-string variants are both already exposed and both more
informative. Removing the noise variant for DOI hits forces the
agent into the useful subset.

### Changed
- ``InlineCitation.markdown_domain`` and
  ``InlineCitation.display_label_domain`` are now ``None`` when
  ``identifiers.doi`` is set. For non-DOI sources (Zenon, IAA records
  pre-DataCite, ADAJ chapters, etc.) these fields keep their previous
  behaviour — ``[(zenon.dainst.org)]`` etc. remain legitimate
  source-hint labels.
- The cascade fallbacks inside ``markdown_recommended`` and
  ``markdown_bibliography`` still use the full domain form internally;
  only the public field is hidden.
- ``DAOPaper`` model docstring explains the new constraint and its
  rationale (three converging runs of reflexive-domain-preference
  by the agent).

### Tests
- 1 updated test (the previous ``markdown_domain`` assertion for a
  DOI hit now asserts ``None``).
- 1 new test ``test_markdown_domain_hidden_for_doi_hits_visible_otherwise``
  pinning the new behaviour and verifying non-DOI hits are unaffected.
- 256 unit tests passing, 3 xfailed.

### Non-breaking
``markdown_domain`` was always ``Optional[str]`` in the schema, so
existing consumers that read it ``is not None`` already handled the
None case. Callers that relied on a domain-form for DOI hits should
switch to ``markdown_doi`` (DOI-string label) or ``markdown_recommended``
(Author-Year label) — both produce more informative output.

## [0.6.2] - 2026-05-15

Structural fix for the bibliography-rendering issue. v0.6.0 added the
`markdown_doi` variant; v0.6.2 makes the agent actually use it.

Observed in two consecutive daily-driver runs (2026-05-15): the agent
correctly rendered Author-Year inline citations in the body text but
kept rendering bibliography entries as `[(doi.org)](url)`. The
`markdown_doi` field was present in the tool output but the agent
preferred the domain-form anyway. Docstring instruction
("prefer markdown_doi for bibliography") was not strong enough to
overcome the agent's default mental model for footnote-style links.

Output-shape lock-in (again): when prompt hints lose to default
patterns, the fix is to make the field structurally unambiguous.

### Added
- `InlineCitation.markdown_bibliography` — **always set**, never None.
  The bibliography counterpart to `markdown_recommended`. Cascade:
  DOI-form when DOI present → Author-Year link → Domain-Title link →
  Domain-only link → plain `fallback_text`. ⚠️-prefix pre-applied for
  aggregator / warn-flagged hits.
- 4 new unit tests in `tests/test_inline_citation.py` covering the
  cascade (DOI → Author-Year → Domain-Title → fallback) and the
  aggregator override.

### Changed
- All 10 search-tool docstrings updated: bibliography rendering
  guidance changed from *"prefer `markdown_doi` when present"* to
  *"copy `markdown_bibliography` verbatim"*. Stronger signal — one
  field, one purpose, no field-picking required.
- `DAOPaper` model docstring and README "Inline citations" section
  updated to document the new field and its semantics.

### Non-breaking
Schema extension is additive. `markdown_doi` remains for callers who
want the raw DOI variant. `markdown_recommended` is unchanged for
body-text rendering. Existing consumers are unaffected.

## [0.6.1] - 2026-05-15

Fixes a regression observed in the first v0.6.0 daily-driver run: the
`search_iaa` tool timed out at the MCP layer (error -32001) when
called without a `collection` filter. Root cause: the IAA OAI server
takes 30+ seconds per page when no `set=` filter is applied, exceeding
the 30 s per-request HTTP timeout. Probed: `ListRecords` with no
filter returns first 100 records in 34.5 s; with `set=publication:atiqot`
in 7.6 s; with set + 2-year window in 4.6 s.

### Fixed
- `search_iaa(collection=None)` now defaults to `collection="atiqot"`
  rather than the slow no-set path. To explicitly search the entire
  IAA archive (slow), pass `collection="all"` and always combine with
  a `year_from`/`year_to` window.
- Added a 40 s wall-clock budget to the pagination loop. When
  exhausted, returns whatever matches have accumulated with a
  warning logged — partial results beat timeout errors. Reduced
  page cap from 5 to 3 (matches the budget envelope).
- Tool docstring spells out the performance characteristics: typical
  page latency ranges, budget behaviour, when to use `collection="all"`.

### Tests
- 4 new unit tests in `tests/test_iaa.py` covering the new default
  (`collection=None` → atiqot) and the `collection="all"` escape
  hatch. 251 unit tests passing, 3 xfailed.

## [0.6.0] - 2026-05-15

Bibliography-friendly DOI rendering. Observed live: at the end of a
research-stand document the agent rendered every reference's link as
`[(doi.org)](https://doi.org/...)` instead of showing the DOI string
in the visible label. The schema didn't expose a DOI-string variant —
it only had Author-Year, domain, and domain-title. Sprint 6 adds that
variant and tells the agent (via tool docstrings + model docstring)
when to pick it over the body-text default.

### Added
- `InlineCitation.display_label_doi` — the bare DOI string (e.g.
  `"10.1179/tav.1984.1984.2.189"`) when a DOI is registered; `None`
  otherwise.
- `InlineCitation.markdown_doi` — pre-rendered Markdown link with the
  DOI string in the visible label, e.g.
  `[(10.1179/tav.1984.1984.2.189)](https://doi.org/10.1179/...)`. The
  URL still resolves via `doi.org`; only the label changes from
  `doi.org` to the DOI itself, which is what scholarly readers expect
  in bibliography / reference-list entries.
- Two new unit tests in `tests/test_inline_citation.py` covering
  `markdown_doi` set / unset behaviour.

### Changed
- Tool docstrings on all 10 search adapters now explicitly hint:
  *"For bibliography or reference-list entries, prefer
  `inline_citation.markdown_doi` when present."*
- `DAOPaper` model docstring documents the per-context variant
  selection (body-text → recommended; bibliography → DOI; web-hit
  → domain-title; print-only → fallback).
- README "Inline citations" section now lists `markdown_doi` /
  `display_label_doi` and explains per-context variant selection.

### Non-breaking
Schema extension is additive. Existing consumers reading
`markdown_recommended` for body text are unaffected.

## [0.5.0] - 2026-05-15

The IAA-MVP-incomplete asterisk is gone. `search_iaa` now talks
OAI-PMH to `publications.iaa.org.il/do/oai/` instead of trying to
scrape a JS-rendered search HTML, and every IAA record carries a
DataCite DOI (prefix `10.70967/`) so inline citations get the
Author-Year form against `doi.org` for free.

### Changed
- **`search_iaa` reimplemented on OAI-PMH.** Server-side filtering by
  collection (`atiqot` / `ha-esi` / `ha-hebrew` / `esi-english` /
  `iaa-books` / `favissa` / `cornerstone` / `ha-esi-bilingual`) and
  year range; client-side AND-of-tokens keyword matching against
  title + description + subject + authors. Paginates via OAI
  `resumptionToken` up to a 20-page safety cap (~2000 records).
  21 unit tests in `tests/test_iaa.py`. See
  `docs/2026-05-15-iaa-solr-probe.md` for the full sondierungsbericht.
- **`IAAUnavailableError` removed.** The HTML-empty-`#results-list`
  tripwire is obsolete now that OAI-PMH provides a clean structured
  endpoint. Callers that handled the exception type can drop it.
- **`search_iaa` argument `report_type` renamed to `collection`** with
  expanded vocabulary covering all eight IAA-Publications collections
  (was three: `report` / `atiqot` / `ha-esi`). Raw OAI setSpec
  passthrough also supported.
- Verification suite's `iaa_carmi_segal_2007` fingerprint now uses
  the new `collection`/`year_from`/`year_to` signature; xfail still
  honoured pending live-probe confirmation, will flip to expected
  pass after the daily-driver run.
- Removed the obsolete HTML fixtures
  `tests/fixtures/iaa_search_empty.html` and
  `tests/fixtures/iaa_search_with_results.html`.

### Added
- `docs/2026-05-15-iaa-solr-probe.md` — sondierungsbericht documenting
  the dead `/do/search/results/json` route (stale 2019 JS bundle),
  the OAI-PMH endpoint discovery, and the implementation plan that
  drove this release.

## [0.4.0] - 2026-05-15

First tagged release. Captures the initial MVP (Zenon/IAA/ADAJ +
resolvers), the inline_citation schema rollout, and the four Pfad II
sprints that selectively reimplement the paper-search-mcp adapters
most relevant to DAO/Digital-Humanities research.

### Added (Pfad II Sprint 4 — preprint currency)
- `search_biorxiv` MCP tool for bioRxiv + medRxiv preprints. Backend
  is Europe PMC's `SRC:PPR`-filtered search (bioRxiv's native
  `api.biorxiv.org` doesn't support free-text queries); the adapter
  filters Europe PMC results to bioRxiv/medRxiv content client-side
  via `journalTitle` so callers don't pick up ResearchSquare / OSF /
  SSRN preprints they didn't ask for. `include_medrxiv` toggle
  (default True) gates medRxiv content. Primary use case: Levant
  aDNA / paleogenomic preprints (Lazaridis, Feldman, Harney, Reich
  Lab) that sit on bioRxiv 6–12 months before journal publication.
  25 unit tests in `tests/test_biorxiv.py`.
- `Identifiers.europepmc_id` field. Inline-citation builder gains a
  `europepmc.org/article/PPR/{id}` fallback below CORE for very-recent
  preprints whose DOI hasn't reached Europe PMC's index yet. Most
  records still resolve via `10.1101/...` DOI in practice.

### Added (Pfad II Sprint 3 — OA aggregator + research repository)
- `search_core` MCP tool against `api.core.ac.uk/v3/search/works`.
  Requires `CORE_API_KEY` (free tier registerable at
  https://core.ac.uk/services/api); without it the adapter raises
  `CoreMissingApiKey` rather than firing an unauthenticated 401 —
  explicit config error beats silent failure. Aggregator detection:
  `dataProvider` entries matching ResearchGate / Academia.edu /
  Google Books / CiteSeerX flip `audit.aggregator=True` and
  `audit.warn_marker=True`, so the inline-citation builder prepends
  ⚠️ and picks the domain-title variant — the secondary-source nature
  is structurally visible. Document-type verification_note surfaces
  theses / working papers / reports so the agent can weight them.
  18 unit tests in `tests/test_core.py`.
- `search_zenodo` MCP tool against `zenodo.org/api/records`. No API
  key. Every Zenodo record has a DOI (`10.5281/zenodo.<int>`) so
  Author-Year form is guaranteed. Non-article resource types
  (dataset, software, presentation, poster, thesis) get a
  `audit.verification_note=resource_type=<type>` hint without setting
  warn_marker — software DOIs are legitimate citation targets, just
  not journal articles. HTML in descriptions is stripped to plain
  text for `DAOPaper.abstract`. 18 unit tests in `tests/test_zenodo.py`.
- `Identifiers.core_id` field. Inline-citation builder gains a CORE
  landing fallback (`https://core.ac.uk/works/{id}`) below
  Semantic Scholar, because CORE is itself an aggregator surface for
  institutional repos.

### Migration documentation
- `docs/2026-05-15-paper-search-mcp-migration.md` — step-by-step
  walkthrough for switching off `paper-search-mcp` in OpenCode and
  routing the six cross-platform queries through this server instead.
  Includes dual-run period, smoke test, rollback path, env-var
  checklist.

### Added (Pfad II Sprint 2 — citation graph + preprints)
- `search_semantic_scholar` MCP tool against
  `api.semanticscholar.org/graph/v1/paper/search`. Optional API key
  via `SEMANTIC_SCHOLAR_API_KEY` env var lifts the public bucket's
  ~100-req/min ceiling. About 70% of hits carry a DOI; the rest are
  identified via the ArXiv ID (routed to the new `Identifiers.arxiv_id`
  field) or the S2 paperId. `citationCount` is surfaced via
  `audit.verification_note` as a soft ranking signal. 19 unit tests
  in `tests/test_semantic_scholar.py`.
- `search_arxiv` MCP tool against `export.arxiv.org/api/query`.
  Atom XML parsed with stdlib ElementTree (no new dependency). Naïve
  free-text queries are auto-wrapped in `all:` so the agent doesn't
  need to know arXiv's Lucene-style prefixes; power users can still
  pass the full DSL. Year filtering is encoded as
  `submittedDate:[…]` ANDed into search_query because arXiv has no
  separate year parameter. Version suffixes (`v1`, `v2`) are stripped
  from the canonical arXiv ID. When a preprint gained a journal DOI
  later (`arxiv:doi`), the DOI wins primary_url. 16 unit tests in
  `tests/test_arxiv.py`.
- `Identifiers.semantic_scholar_id` and `Identifiers.arxiv_id` fields
  on the schema. Inline-citation builder gains URL fallbacks for both,
  with arXiv ranked above S2 because arxiv.org is the source
  repository for preprints while S2 is a pure indexer — when a paper
  has both, arxiv.org is the canonical anchor.

### Added (Pfad II Sprint 1 — cross-platform adapters)
- `search_crossref` MCP tool against `api.crossref.org/works`. Polite-pool
  User-Agent with `mailto:` for higher rate limits. Every hit carries a DOI
  so `inline_citation.markdown_recommended` is always `[(Author Year)](https://doi.org/…)`.
  Personal authors only — corporate authors are dropped from the authors
  list because they distort the Author-Year citation form. Items without
  a DOI are dropped entirely. JATS markup in abstracts is stripped to
  plain text. 21 unit tests in `tests/test_crossref.py`.
- `search_openalex` MCP tool against `api.openalex.org/works`. Polite-pool
  via `mailto=` query parameter. DOI URLs (`https://doi.org/10.x/y`) are
  normalised to bare DOI form; OpenAlex Work IDs are stripped to the
  `W<digits>` form. Abstracts are reconstructed from OpenAlex's inverted
  index (`{word: [positions]}`) into plain text. Authorships are flipped
  from "Given Family" to "Family, Given" so the Author-Year builder picks
  the correct family name. Hits without either DOI or OpenAlex ID are
  dropped — no fabricated anchors. 19 unit tests in `tests/test_openalex.py`.

### Added
- `inline_citation` block on every `DAOPaper`: pre-rendered Markdown with
  three variants (`markdown_authoryear` / `markdown_domain` /
  `markdown_domain_title`) plus a `markdown_recommended` first-choice
  string. Builder picks the recommended variant heuristically and
  pre-applies the ⚠️-prefix when `audit.warn_marker` or
  `audit.aggregator` is set. Agent copies the string verbatim — output
  shape now structurally enforces the AGENTS.md inline-link rule
  instead of relying on prompt-side guidance.
- `Identifiers` and `Audit` Pydantic models on `DAOPaper` (additive,
  coexist with the legacy `doi_or_id` prefixed string). `Identifiers`
  exposes structured `doi` / `openalex_id` / `zenon_id` / `iaa_pub_id`
  / `adaj_id`; `Audit` exposes `primary_source` / `aggregator` /
  `verification_note` / `warn_marker`.
- Zenon adapter extracts external DOIs from `record["urls"]` and
  `record["DOI"]`; IAA adapter scans block text for DOI patterns;
  ADAJ adapter sets `audit.warn_marker` when `verification_note` fires
  (e.g. `publication_type` mismatch).
- 17 new unit tests in `tests/test_inline_citation.py` covering the
  source-priority table, three Markdown variants, aggregator handling,
  ≥3-author et-al form, ≤50-char title truncation with single-character
  ellipsis, and the ⚠️-prefix gating rules.
- Tool docstrings on `search_zenon` / `search_iaa` / `search_adaj` and
  the `DAOPaper` model now explicitly instruct the agent to copy
  `inline_citation.markdown_recommended` verbatim — discoverability
  fix so the new field actually gets used.

### Added (earlier in this Unreleased block)
- `resolve_site` MCP tool against `iDAI.gazetteer`
  (`gazetteer.dainst.org`). Returns canonical name, gazId, multilingual
  name variants, types, coordinates, parent/ancestor hierarchy, and
  Pleiades + GeoNames cross-refs.
- Zenon search results now auto-populate `DAOPaper.site_ids` from the
  upstream `DAILinks.gazetteer` cross-links. Each link becomes a
  `gazetteer:<gazId>` token in the same record, so agents get
  authoritative place anchoring with zero extra calls.
- `ResolvedSite` Pydantic model for the new resolver output.

### Note (Tier-2 bibliography portals)
- Investigated Propylaeum (BSB Munich) and IxTheo (Tübingen): both
  serve bot-protection JS challenges. Persée and OpenEdition both
  ship React frontends with no SSR results. Tier-2 bibliography
  coverage therefore requires Playwright (post-MVP) or stays
  uncovered. We pivoted to the gazetteer integration instead — same
  Iteration-2 scope, but with a clean JSON API and direct synergy
  with Zenon's existing DAILinks.

### Added (earlier in this changelog block)
- Acceptance test suite (`tests/test_verification_suite.py`) with the
  five frozen reference fingerprints from the briefing (Abschnitt VII).
  The non-negotiable contract — the Ben-Ami 2026 Levant 58(1)
  hallucination must return no match via Zenon — passes. Three
  references are xfail until upstream coverage expands:
  - Ben-Ami 2024 *Levant*: Zenon does not yet index 2024 *Levant* issues
  - Bienkowski/Tebes 2024 *PEQ*: same for 2024 *PEQ* issues
  - Carmi/Segal 2007 IAA ¹⁴C: IAA backend is JS-only (see `search_iaa`)
  The Cohen/Yisrael 1995 reference resolves via the companion Israel
  Museum catalog *On the road to Edom: discoveries from 'En Ḥaẓeva*.
- `resolve_author` MCP tool with three-layer resolution: DAO override
  YAML > Wikidata SPARQL > GND (lobid.org) fallback. Seeded with 7 DAO
  override entries including the "Avraham Rosen" → "Steven A. Rosen"
  hallucination correction. Overrides short-circuit before any upstream
  call so we never waste Wikidata budget on names we already know.
- `search_adaj` MCP tool for the DoA Publication Archive
  (publication.doa.gov.jo). Covers ADAJ, SHAJ, Munjazat, JERD, and
  Athar — broader than the briefing assumed. Year filtering is applied
  client-side because the upstream GET search ignores year parameters.
- `search_iaa` MCP tool for IAA Publications. **MVP-incomplete**: the
  IAA backend currently renders search results client-side via
  JavaScript, so the tool raises `IAAUnavailableError` rather than
  returning silently empty (anti-silent-failure tripwire). The parser is
  ready for server-rendered results so no code change is needed when
  upstream SSR is restored or a playwright fallback is added post-MVP.
  See README "Known limitations".

[Unreleased]: https://github.com/leiverkus/dao-paper-search-mcp/compare/v0.6.4...HEAD
[0.6.4]: https://github.com/leiverkus/dao-paper-search-mcp/releases/tag/v0.6.4
[0.6.3]: https://github.com/leiverkus/dao-paper-search-mcp/releases/tag/v0.6.3
[0.6.2]: https://github.com/leiverkus/dao-paper-search-mcp/releases/tag/v0.6.2
[0.6.1]: https://github.com/leiverkus/dao-paper-search-mcp/releases/tag/v0.6.1
[0.6.0]: https://github.com/leiverkus/dao-paper-search-mcp/releases/tag/v0.6.0
[0.5.0]: https://github.com/leiverkus/dao-paper-search-mcp/releases/tag/v0.5.0
[0.4.0]: https://github.com/leiverkus/dao-paper-search-mcp/releases/tag/v0.4.0
