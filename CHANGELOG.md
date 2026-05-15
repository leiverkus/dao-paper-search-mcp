# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
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
