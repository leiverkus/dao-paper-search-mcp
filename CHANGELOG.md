# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
