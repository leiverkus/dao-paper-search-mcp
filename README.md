# dao-paper-search-mcp

Vertical MCP server for the **DAO** domain (Digital Antiquity Oldenburg) — Levant archaeology, biblical archaeology, Bronze/Iron Age. Complementary to [`paper-search-mcp`](https://github.com/yourusername/paper-search-mcp), not a replacement.

`paper-search-mcp` covers ~20 horizontal scholarly platforms (Crossref, OpenAlex, Semantic Scholar, arXiv, …). It does **not** cover Zenon DAI, IAA Publications, ADAJ, IxTheo, Propylaeum — the German/Hebrew/Arabic-language sources that are the DAO core domain. This server fills that gap.

## Sources

### Tier 1 (MVP)

| Tool | Source | Status |
|---|---|---|
| `search_zenon` | Zenon DAI (~1M records, multilingual DE/EN/FR/IT/HE/AR) | implemented |
| `search_iaa` | IAA Publications (Reports, ʿAtiqot, HA-ESI) | MVP-incomplete (see below) |
| `search_adaj` | DoA Publication Archive (ADAJ, SHAJ, Munjazat, JERD, Athar) | implemented |
| `resolve_author` | Wikidata SPARQL + local override list + GND fallback | implemented |

### Tier 2 (planned)

Propylaeum, IxTheo, Persée, OpenEdition, Gnomon Online, TOCS-IN — one adapter per follow-up PR.

## Install / Run

Locally via `uvx` from the working tree:

```bash
uvx --from git+file:///Users/patrick/Documents/Aktuell/dao-paper-search-mcp \
  python -m dao_paper_search_mcp.server
```

Or after a GitHub push:

```bash
uvx --from git+https://github.com/<owner>/dao-paper-search-mcp \
  python -m dao_paper_search_mcp.server
```

## OpenCode integration

Add to `~/.config/opencode/opencode.jsonc` under the `mcp` block:

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

Then update `~/.config/opencode/agent/research.md` to route Levant/IAA/DoA-Jordan queries to this server first.

## Architecture principles

- **Vertical scope.** Only sources `paper-search-mcp` does not cover.
- **Tool independence.** No internal calls to `paper-search-mcp`. Citation-graph delegation is the agent's responsibility. This preserves the cross-validation property of `research.md`.
- **Schema fidelity.** All search tools return the same Pydantic model (`DAOPaper`).
- **Structured verification notes.** When uncertain, the adapter sets `verification_note`, never guesses.
- **Stdio cleanliness.** MCP stdout is reserved for JSON-RPC; all logging goes to stderr.

## Authority overrides

`src/dao_paper_search_mcp/data/authority_overrides.yml` is the DAO-curated disambiguation list. To add an entry:

```yaml
- canonical: "Steven A. Rosen"
  variants: ["S.A. Rosen", "Rosen, S.A.", "Steven Rosen"]
  q_id: "Q7613131"
  domain: "Levant archaeology, lithics, Negev Highlands Survey"
  affiliation: "Ben Gurion University"
  sites: ["Negev Highlands", "Camel Site"]
```

`resolve_author` checks the override list **before** consulting Wikidata. Add an entry whenever you encounter a misattribution in real research output.

## Tests

```bash
uv sync --extra test
uv run pytest -v
```

The verification suite (`tests/test_verification_suite.py`) contains five **frozen reference fingerprints** drawn from the 2026-05-15 Negev-fortress test. One of them is a **negative test** — a hallucinated reference (Ben-Ami 2026 Levant 58(1):25–42) that converged across three LLM outputs. The suite asserts the server returns no result for this query; a false positive would mean the server is echoing the LLM hallucination.

## Known limitations

### `search_iaa` is MVP-incomplete

Probed 2026-05-15: ``publications.iaa.org.il`` runs Berkeley Electronic Press (Digital Commons). The search endpoint at ``/do/search/?q=<q>`` returns an HTML shell where ``<div id="results-list">`` is empty — actual hits arrive client-side via a Solr JS bundle. The backend also returns HTTP 504 intermittently on the search path.

Per the briefing (Abschnitt XIII.2), playwright/headless-browser fallback is out of scope for the MVP. The adapter therefore:

- Still issues the real GET against ``/do/search/?q=<q>``.
- Parses the BePress server-rendered markup as it *should* appear (so that the moment SSR is restored or a real API ships, results flow through transparently).
- Raises ``IAAUnavailableError`` with an actionable message when ``#results-list`` is empty or absent — the explicit anti-silent-failure tripwire from architecture principle #6.

Workaround until SSR returns: cross-check IAA queries via ``search_zenon`` — Zenon DAI partially indexes IAA publications.

## Disclaimer

MIT licensed. No cloud upload, runs entirely locally. DSGVO-konform.
