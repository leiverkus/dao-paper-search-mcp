# dao-paper-search-mcp

DAO-centred MCP server for academic research — Levant archaeology, biblical archaeology, Bronze/Iron Age, plus a curated cross-platform paper-search surface for the wider humanities. Started as a vertical complement to [`paper-search-mcp`](https://github.com/openags/paper-search-mcp); now also reimplements the cross-platform adapters that matter for DAO/DH workflows (Crossref, OpenAlex, Semantic Scholar, arXiv, CORE, Zenodo) so every hit carries the same pre-rendered `inline_citation` block.

The DAO-specific sources (Zenon DAI, IAA Publications, ADAJ) are the original raison d'être and remain the strongest reason to use this server for Levantine archaeology. The horizontal adapters reduce the need to run `paper-search-mcp` alongside.

## Sources

### DAO-specific (Tier 1, original scope)

| Tool | Source | Status |
|---|---|---|
| `search_zenon` | Zenon DAI (~1M records, multilingual DE/EN/FR/IT/HE/AR) | implemented |
| `search_iaa` | IAA Publications (ʿAtiqot, HA-ESI, IAA Book Series, Favissa, …) | implemented (OAI-PMH backend since v0.5.0) |
| `search_adaj` | DoA Publication Archive (ADAJ, SHAJ, Munjazat, JERD, Athar) | implemented |
| `resolve_author` | Wikidata SPARQL + local override list + GND fallback | implemented |
| `resolve_site` | iDAI.gazetteer (DAI's authoritative place register) | implemented |

### Cross-platform (Pfad II — selective `paper-search-mcp` substitution)

| Tool | Source | Status |
|---|---|---|
| `search_crossref` | Crossref (~150M DOI-bearing scholarly works) | implemented (Sprint 1) |
| `search_openalex` | OpenAlex (~250M works, broadest open scholarly graph) | implemented (Sprint 1) |
| `search_semantic_scholar` | Semantic Scholar (citation graph, recommendations) | implemented (Sprint 2) |
| `search_arxiv` | arXiv (preprints, esp. Digital Humanities methods) | implemented (Sprint 2) |
| `search_core` | CORE (open-access full-text aggregator) | implemented (Sprint 3) |
| `search_zenodo` | Zenodo (data, software, preprints, every record gets a DOI) | implemented (Sprint 3) |
| `search_biorxiv` | bioRxiv + medRxiv preprints (via Europe PMC) — aDNA / paleogenomic currency | implemented (Sprint 4) |

### Tier 2 (planned, DAO-specific)

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
- **Output-shape lock-in for citations.** Every hit carries an `inline_citation` block whose `markdown` field is a pre-rendered Markdown link (with `⚠️`-prefix when `audit.warn_marker` or `audit.aggregator` is set). The agent copies this verbatim instead of formatting citations itself — structural enforcement of the `AGENTS.md` inline-link rule. Two extra fields (`authoritative_authors_label`, `authoritative_bibliography_line`) carry the tool-authoritative Author-Year and reference-list strings to prevent DOI-consistent author-year hallucinations.
- **Centralised DOI normalisation.** All adapters run DOIs through `utils.doi.normalize_doi()` — stripping resolver prefixes (`https://doi.org/`, `info:doi/`, `doi:` …) and lower-casing (DOI Handbook §2.4). Produces bare, case-insensitive `10.<registrant>/…` strings for reliable deduplication. The render layer always reconstructs the full link from the bare string, so case drift in upstream APIs never leaks into bibliography output.

## Inline citations

Each `DAOPaper` carries three blocks the agent should consume directly:

- `identifiers`: structured DOI / Zenon / IAA / ADAJ IDs (coexists with the legacy `doi_or_id` string).
- `audit`: `primary_source`, `aggregator`, `verification_note`, `warn_marker` — flags that drive the citation renderer.
- `inline_citation`: pre-rendered Markdown plus the tool-authoritative bibliography strings.

### `inline_citation` fields (Schema v2.1, since v0.7.1)

| Field | Purpose |
|---|---|
| `url` | Canonical URL (priority: DOI > OpenAlex > Zenon > IAA > ADAJ > arXiv > Semantic Scholar > CORE > Europe PMC > open_access_url > landing_page_url). |
| `markdown` | Finished in-text Markdown link. Author-Year form for academic hits (`[(Cohen 1979)](https://doi.org/…)`), Domain-Title form for web hits (`[(example.org — Title…)](url)`), Domain-only as last resort, and `⚠️`-prefixed for aggregator/warn-flagged hits. Print-only hits (no URL) collapse to `fallback_text`. **Copy this verbatim** in prose. |
| `authoritative_authors_label` | Plain-text Author-Year string (`"Finkelstein 1999"`) — `None` when no author context exists. Use this when you want to render Author-Year yourself instead of copying `markdown`; do **not** reconstruct from `authors`/`year`. |
| `authoritative_bibliography_line` | Full reference-list line with a trailing clickable link: `"Finkelstein, I. (1999). Title. *BASOR* 314, 55–70. DOI: [10.2307/1357451](https://doi.org/10.2307/1357451)"`. DOI takes priority over `primary_url`; no suffix when neither is present. `None` when author/year/title metadata is incomplete. **Copy verbatim** in the references list. |
| `fallback_text` | `"Cohen 1979: 61–79"` — used when no `url` exists (print-only). |

**Author-label rules (inline):**

- 1 author → `"Cohen 1979"`.
- 2 authors → `"Cohen & Yisrael 1995"`.
- 3 authors → `"Boaretto, Finkelstein & Shahack-Gross 2010"` (explicit, no et al.).
- ≥4 authors → `"Bruins et al. 2011"`.
- Particle names (`van der Plicht`, `von Daniken`) stay intact in both inline and bibliography forms.

**Bibliography author-rules:** family + initials, Oxford comma before `&`, full list (no et al.). 1 author: `"Cohen, R."`; 2: `"Cohen, R., & Yisrael, Y."`; ≥3: `"Boaretto, E., Finkelstein, I., & Shahack-Gross, R."`.

**`markdown` cascade:** print-only `→ fallback_text`; aggregator `→ ⚠️[(domain — Title…)](url)`; Author + Year `→ [(Author-Label Year)](url)`; URL + Title without Author-Year `→ [(domain — Title…)](url)`; URL only `→ [(domain)](url)`; `audit.warn_marker` prepends `⚠️` to the link form.

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

### `search_iaa` has no full-text search

The IAA backend (BePress/Solr) does not expose a public free-text search API. The v0.5.0 OAI-PMH-backed adapter compensates by AND-matching query tokens against title + description + subject + author fields client-side — broad enough for most archaeology queries, but not as deep as a real Solr `q=` would be. Recommendation: always pass at least a 5-year `year_from`/`year_to` window so the OAI listing stays manageable.

The reverse-engineered `/do/search/results/json` endpoint pulled from the page's 2019 JS bundle has been retired — BePress migrated the route without updating the bundle. See [`docs/2026-05-15-iaa-solr-probe.md`](docs/2026-05-15-iaa-solr-probe.md) for the full sondierungsbericht.

## Disclaimer

MIT licensed. No cloud upload, runs entirely locally. DSGVO-konform.
