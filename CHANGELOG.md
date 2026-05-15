# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
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
