# OpenCode integration

These snippets are not applied automatically — review and merge into
your OpenCode config yourself.

## 1. `~/.config/opencode/opencode.jsonc`

Add the following entry to the `mcp` block, alongside the existing
`paper-search` server. Three source options — pick one:

**(a) Pinned to a release tag (recommended for stable workflows):**

```jsonc
"dao-paper-search": {
  "type": "local",
  "command": [
    "/opt/homebrew/bin/uvx",
    "--from", "git+https://github.com/leiverkus/dao-paper-search-mcp@v0.6.4",
    "python", "-m", "dao_paper_search_mcp.server"
  ],
  "enabled": true,
  "environment": {
    "WIKIDATA_USER_AGENT": "dao-paper-search-mcp/0.6 (your-email@example.com)",
    "DAO_PAPER_SEARCH_RATE_LIMIT_MS": "1000",
    "CORE_API_KEY": "${CORE_API_KEY}",
    "SEMANTIC_SCHOLAR_API_KEY": "${SEMANTIC_SCHOLAR_API_KEY}"
  }
}
```

Bump the `@vX.Y.Z` segment when you want to pick up a newer release.
Inspect changes in CHANGELOG.md before upgrading.

**(b) Latest `main`** (no pinning — picks up changes immediately):

Replace the `--from` argument with `git+https://github.com/leiverkus/dao-paper-search-mcp`.

**(c) Local checkout** (for development while editing the source):

Replace the `--from` argument with `git+https://github.com/leiverkus/dao-paper-search-mcp`.

After saving, verify with:

```bash
opencode mcp list
```

You should see `dao-paper-search [connected]` and the following tools
listed: `ping`, `search_zenon`, `search_iaa`, `search_adaj`,
`resolve_author`, `resolve_site`.

## 2. `~/.config/opencode/agent/research.md`

In the `## Werkzeuge & wann du sie nutzt` section, replace the existing
`dao-paper-search-mcp` placeholder line with this routing block:

```markdown
- **dao-paper-search-mcp** — DAO-Spezialquellen (Zenon DAI, IAA Publications,
  ADAJ + SHAJ + Munjazat + JERD + Athar) + Wikidata-Author-Disambiguation
  + iDAI.gazetteer-Site-Resolver. **Erste Wahl** bei:
    - Levante-Archäologie (Iron Age, Bronze Age, biblische Archäologie)
    - Deutsch-/Hebräisch-/Arabisch-sprachige Forschung
    - IAA-, DoA-Jordan-, und DAI-Grabungsberichten
    - Author-Verifikation bei mehrdeutigen Namen (Cohen, Mazar, Rosen) → `resolve_author`
    - Site-Disambiguation und stabile Anker-IDs via iDAI.gazetteer → `resolve_site`
  Output enthält `verification_note` bei unsicheren Treffern. Zenon-Treffer haben
  `site_ids` mit `gazetteer:<gazId>`-Tokens, sobald DAI die Site cross-linkt.

  **Cross-DB-Routing (wichtig):**
    - Wenn `search_adaj` leer für eine Levante-Anfrage → **immer** `search_zenon` quer-checken.
      Beispiel: „Cohen Yisrael Edom" → ADAJ leer, Zenon hat den Israel-Museum-Katalog.
      Begründung: DoA-Jordan deckt nur die Jordan-Seite; Israel-Seite (Cohen/Yisrael
      via IAA-Excavations) ist in Zenon teilindexiert, aber nicht in ADAJ.
    - Wenn `search_iaa` mit `IAAUnavailableError` fehlschlägt → `search_zenon` als
      Cross-Check (IAA-Backend ist JS-only, MVP-incomplete).
    - Wenn `resolve_author` mit `source: "unresolved"` zurückkommt → das ist ein
      *positives* Signal: Wikidata/GND haben keinen passenden Archäologen-Namensvariant
      → wahrscheinlich falsche Schreibweise oder Halluzination. **Nicht** ignorieren.

  **Multi-Autor-Heuristik:**
    - „Cohen Yisrael" ist nicht eine Person „Yisrael Cohen", sondern wahrscheinlich
      Co-Autoren-Stil „R. Cohen and Y. Yisrael" (klassische Negev-Fortresses-Refs).
      `resolve_author` einzeln pro Nachname aufrufen, nicht über den ganzen String.
```

The cross-validation discipline in `## Verifikations-Disziplin` then
applies automatically: every reference needs at least one structured
tool to confirm it — a Zenon-DAI or ADAJ hit now counts as a valid
structured verification, complementary to `paper-search.search_crossref`.

## 3. Smoke test in OpenCode

After both edits, restart OpenCode and try this prompt:

> Suche in dao-paper-search nach „Cohen Yisrael Edom" und gib die
> Treffer als Bibliographie aus.

You should see at least the Israel-Museum catalog *On the road to Edom:
discoveries from 'En Ḥaẓeva* (Cohen, Rudolph, 1995) come back, and the
agent should follow the briefing's verification discipline.
