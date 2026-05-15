# OpenCode integration

These snippets are not applied automatically — review and merge into
your OpenCode config yourself.

## 1. `~/.config/opencode/opencode.jsonc`

Add the following entry to the `mcp` block, alongside the existing
`paper-search` server:

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
  ADAJ + SHAJ + Munjazat + JERD + Athar) + Wikidata-Author-Disambiguation.
  **Erste Wahl** bei:
    - Levante-Archäologie (Iron Age, Bronze Age, biblische Archäologie)
    - Deutsch-/Hebräisch-/Arabisch-sprachige Forschung
    - IAA-, DoA-Jordan-, und DAI-Grabungsberichten
    - Author-Verifikation bei mehrdeutigen Namen (Cohen, Mazar, Rosen) → `resolve_author`
    - Site-Disambiguation und stabile Anker-IDs via iDAI.gazetteer → `resolve_site`
  Output enthält `verification_note` bei unsicheren Treffern. Zenon-Treffer haben
  `site_ids` mit `gazetteer:<gazId>`-Tokens, sobald DAI die Site cross-linkt.
  `search_iaa` ist aktuell **MVP-incomplete** (IAA-Backend ist JS-only) —
  bei `IAAUnavailableError` über `search_zenon` quer-checken.
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
