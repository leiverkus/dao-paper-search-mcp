# PR-Notes: `inline_citation` upstream in paper-search-mcp

**Status:** Vorbereitet, noch nicht eingereicht.
**Datum:** 2026-05-15
**Zielrepo:** `paper-search-mcp` (separater Repo, separater Maintainer-Kontakt)
**Anker:** Erster Briefing §VI ("Konsistenz mit paper-search-mcp"); zweiter Briefing §V.III ("Output-Shape determiniert Output-Format")

---

## Warum

Empirie vom 2026-05-15: Bei archäologischen Forschungsständen, deren primäre Evidenzanker in mainstream Crossref-Journals liegen (*Radiocarbon*, *Tel Aviv*, *Levant*, *BASOR*, IEJ), zitiert der Agent ausschließlich aus paper-search-mcp-Treffern, nicht aus dao-paper-search. Folge: das in dao-paper-search-mcp bereits gelandete `inline_citation`-Schema greift für diese Fragen **nicht**, weil keine der zitierten Refs durch dao-paper-search fließt.

Die dao-paper-search-Erweiterung ist korrekt und gehört dort hin (sie deckt Zenon/IAA/ADAJ). Aber **für die häufigste Recherche-Klasse — peer-reviewed Crossref-Journals — braucht paper-search-mcp das identische Schema**, sonst entsteht ein Format-Riss zwischen den zwei MCPs (Author-Year-Form für DAO-Treffer, blanke `[(doi.org)](...)`-Form für Crossref-Treffer in derselben Antwort).

## Was

Additive Schema-Erweiterung pro Adapter-Treffer: drei vor-gerenderte Markdown-Varianten (`markdown_authoryear` / `markdown_domain` / `markdown_domain_title`), eine `markdown_recommended` Erstwahl, ein `fallback_text` für print-only. Identisch zum dao-paper-search-Schema.

**Bricht nichts.** Bestehende Felder bleiben unangetastet, das `inline_citation`-Objekt ist optional, ältere Konsumenten ignorieren es.

## Schema (Pydantic-Skizze)

```python
from typing import List, Optional
from pydantic import BaseModel, HttpUrl

class Identifiers(BaseModel):
    doi: Optional[str] = None
    openalex_id: Optional[str] = None  # e.g. "W2741809807"
    semantic_scholar_id: Optional[str] = None
    arxiv_id: Optional[str] = None
    pubmed_id: Optional[str] = None
    corpus_id: Optional[str] = None

class Audit(BaseModel):
    primary_source: bool = True
    aggregator: bool = False          # Google Books, ResearchGate, Academia
    verification_note: Optional[str] = None
    warn_marker: bool = False

class InlineCitation(BaseModel):
    primary_url: Optional[HttpUrl] = None
    display_domain: Optional[str] = None
    display_label_authoryear: Optional[str] = None
    display_label_domain: Optional[str] = None
    display_label_domain_title: Optional[str] = None
    markdown_authoryear: Optional[str] = None
    markdown_domain: Optional[str] = None
    markdown_domain_title: Optional[str] = None
    markdown_recommended: str
    fallback_text: str
```

Wo paper-search-mcp aktuell ein TypedDict / Dataclass / dict pro Treffer baut, kommen drei neue optionale Felder dazu: `identifiers`, `audit`, `inline_citation`.

## Builder

Identisch zu `dao-paper-search-mcp/src/dao_paper_search_mcp/inline_citation.py`. Vollständig portierbar (pure function, keine I/O, keine Dependencies außer `urllib.parse` und `re`). Datei wörtlich kopierbar — nur den Import von `.models` auf den paper-search-mcp-Modul-Pfad anpassen.

**Quelle:** [src/dao_paper_search_mcp/inline_citation.py](../src/dao_paper_search_mcp/inline_citation.py) im aktuellen Repo.

## Source-Priority pro Adapter

| paper-search-mcp Adapter | Hauptidentifier → `primary_url` | Domain-Fallback |
|---|---|---|
| Crossref | DOI → `https://doi.org/{doi}` | n/a |
| OpenAlex | DOI > OpenAlex Work ID → `https://openalex.org/{id}` | `openalex.org` |
| Semantic Scholar | DOI > S2 paper ID → `https://www.semanticscholar.org/paper/{id}` | `semanticscholar.org` |
| arXiv | DOI > arXiv ID → `https://arxiv.org/abs/{id}` | `arxiv.org` |
| PubMed | DOI > PMID → `https://pubmed.ncbi.nlm.nih.gov/{pmid}/` | `pubmed.ncbi.nlm.nih.gov` |
| Google Scholar (falls vorhanden) | URL aus Treffer | `scholar.google.com` + `aggregator=True` |

DOI hat in jedem Adapter Priorität — paper-search-mcp-Treffer haben in 95+ % der Fälle eine DOI, also wird `markdown_recommended` fast immer die Form `[(Author Year)](https://doi.org/...)` annehmen. Das ist genau die Form, die der Agent im 2026-05-15-Test (Qwen 3.6 Plus) bereits selbständig zu rendern versuchte — nur nicht zuverlässig modellübergreifend.

## Heuristik für `markdown_recommended`

In Reihenfolge (erste passende gewinnt):

1. `primary_url` fehlt → `fallback_text`
2. `audit.aggregator=True` → `markdown_domain_title or markdown_domain`, immer ⚠️-Prefix
3. Autoren + Jahr vorhanden → `markdown_authoryear`
4. Sonst (URL + Title, kein klares Author-Year) → `markdown_domain_title`
5. Letzte Reserve → `markdown_domain`
6. `audit.warn_marker=True` → ⚠️-Prefix anhängen (außer bei reinem `fallback_text`)

## Edge Cases (mit dao-paper-search-Tests verifiziert)

- **Mehrere Autoren:** 2 Autoren → `"A & B 2020"`, ≥3 → `"A et al. 2020"`
- **Kein Jahr:** Author-Year-Variante wird `None`, Recommended fällt auf Domain-Title
- **Lange Titel:** Truncation auf 50 Zeichen, Wortgrenze, Single-Char-Ellipsis `…`
- **Aggregator ohne Title:** Domain-only Form
- **Print-only:** `markdown_recommended == fallback_text`, kein ⚠️-Prefix auf Plain-Text

## Test-Coverage zum Mitnehmen

[tests/test_inline_citation.py](../tests/test_inline_citation.py) hat 17 Unit-Tests gegen den Builder, alle pure (keine VCR-Cassetten nötig). Wörtlich portierbar nach paper-search-mcp — nur Imports anpassen.

## Tool-Docstring-Hinweis (separater PR-Punkt)

Pro Tool-Docstring eine Zeile einfügen, die dem Agent sagt: *"Copy `inline_citation.markdown_recommended` verbatim — do not reformat."* Sonst ist die Discovery der neuen Felder vom Modell-Verhalten abhängig.

Beispiel-Snippet (aus [search_zenon](../src/dao_paper_search_mcp/adapters/zenon.py) im aktuellen Repo, sinngemäß übernehmen):

> Citation rendering: each returned record carries an `inline_citation` block with pre-rendered Markdown. When citing a hit in body text, copy `inline_citation.markdown_recommended` verbatim — it already encodes the canonical URL, the Author-Year label, and any ⚠️ warning prefix. Do not reformat to `[(domain)](url)` or any other shape. Use `inline_citation.fallback_text` only when `primary_url` is `null`.

## Maintainer-Kontext

Die Schema-Erweiterung ist **additiv und non-breaking**. Bestehende Konsumenten sehen das Feld `inline_citation: null` (oder ignorieren ein neues optionales Key). Test-Aufwand: ~150 Zeilen Unit-Tests + 1 Assertion pro Adapter-Test.

Falls der Maintainer skeptisch ist gegenüber dem fest verdrahteten Markdown-Format: das `inline_citation`-Objekt enthält **alle Bestandteile separat** (`primary_url`, `display_label_authoryear`, etc.), sodass Konsumenten beliebige eigene Formate komponieren können. `markdown_recommended` ist nur die vorberechnete Erstwahl.

## Open Questions vor PR-Einreichung

1. **Lizenz-Kompatibilität.** dao-paper-search-mcp ist MIT — paper-search-mcp-Lizenz prüfen, bevor Code wörtlich übernommen wird.
2. **Builder-Position.** Als Modul auf Top-Level (`paper_search_mcp/inline_citation.py`) oder pro-Adapter? Top-Level ist sauberer (Single Source of Truth).
3. **Audit-Flags für Aggregator-Adapter.** Welche paper-search-mcp-Adapter sind Aggregatoren? Google Scholar (wenn aktiv) und ResearchGate (falls integriert) — die müssten `audit.aggregator=True` setzen.
4. **Backward-Compat-Tests.** Vor Merge sicherstellen, dass kein bestehender paper-search-mcp-Test bricht.
