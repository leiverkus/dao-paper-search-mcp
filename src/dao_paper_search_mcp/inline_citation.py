"""Builder for the ``InlineCitation`` block on every ``DAOPaper``.

Pure function, no network I/O. Pre-renders multiple Markdown variants
so the agent picks the form that fits its prose: Author-Year for body
text, domain-plus-title for web references, domain-only for footnotes,
DOI-string for bibliography entries. The agent copies the chosen
variant verbatim.

Priority of ``primary_url``:
    DOI > OpenAlex > Zenon > IAA > ADAJ > open_access_url > landing_page_url > None

``markdown_recommended`` heuristic (order matters):
    - No URL                            → ``fallback_text``
    - ``audit.aggregator=True``         → Domain(-Title) form, ⚠️-prefixed
      (Google Books / ResearchGate / Academia: the visible label must
      surface the aggregator domain so the reader sees it's a secondary
      hit, regardless of any author-year context that might exist.)
    - Authors + Year present            → Author-Year form (academic body text)
    - URL but no Author-Year context    → Domain-Title form (web reference)
    - Domain-only as last resort        → Domain form
    - ``warn_marker=True``              → prepend ⚠️ (link forms only)
"""

from __future__ import annotations

import re
from typing import List, Optional
from urllib.parse import urlparse

from .models import Audit, Identifiers, InlineCitation


_FAMILY_NAME_RE = re.compile(r"^([^,]+),")  # "Cohen, R." -> "Cohen"
_TITLE_MAX_LEN = 50  # truncation threshold for the domain-title variant


def _family_name(author: str) -> str:
    """Best-effort family-name extraction from one author string.

    Handles ``"Cohen, R."`` (comma-first), ``"R. Cohen"`` (initial-first),
    and bare ``"Cohen"``. Falls back to the whole string when unsure —
    over-quoting beats wrong-quoting in a citation fallback.
    """
    s = author.strip()
    m = _FAMILY_NAME_RE.match(s)
    if m:
        return m.group(1).strip()
    parts = s.split()
    if len(parts) >= 2:
        return parts[-1]
    return s


def _authoryear_label(authors: List[str], year: Optional[int]) -> Optional[str]:
    """Return ``"Cohen 1979"`` / ``"Cohen & Yisrael 1995"`` / ``"Cohen et al. 1979"``.

    Returns ``None`` when there's no usable author-year context — that
    signals to the caller that the Author-Year variant should be skipped
    and the domain-title form should win ``markdown_recommended`` instead.
    """
    if not authors or year is None:
        return None
    if len(authors) >= 3:
        head = f"{_family_name(authors[0])} et al."
    elif len(authors) == 2:
        head = f"{_family_name(authors[0])} & {_family_name(authors[1])}"
    else:
        head = _family_name(authors[0])
    return f"{head} {year}"


def _format_fallback(authors: List[str], year: Optional[int], pages: Optional[str]) -> str:
    """Author-Year form used when no link target exists.

    The fallback must read like a normal in-text citation so prose stays
    grammatical — this is the only string the agent prints when print-only
    grey literature has no digital anchor.
    """
    if not authors:
        head = "Anon."
    elif len(authors) >= 3:
        head = f"{_family_name(authors[0])} et al."
    else:
        head = " & ".join(_family_name(a) for a in authors)

    parts = [head]
    if year is not None:
        parts.append(str(year))
    text = " ".join(parts)
    if pages:
        text = f"{text}: {pages}"
    return text


def _truncate_title(title: str) -> str:
    """Trim a title to fit inside an inline link label.

    Cut on a word boundary near the limit so we don't slice through a
    word, then append a single-character ellipsis to signal truncation.
    """
    title = title.strip()
    if len(title) <= _TITLE_MAX_LEN:
        return title
    cut = title[:_TITLE_MAX_LEN].rsplit(" ", 1)[0]
    return f"{cut}…"


def _display_domain(url: str) -> str:
    netloc = urlparse(url).netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return netloc


def _pick_primary_url(
    identifiers: Identifiers,
    landing_page_url: Optional[str],
    open_access_url: Optional[str],
) -> Optional[str]:
    """Apply the source-priority table to choose one canonical URL.

    DOI ranks highest because it survives publisher migrations; the
    repository-specific fallbacks (Zenon/IAA/ADAJ) only kick in when no
    DOI is present, so doi.org always wins when both exist.
    """
    if identifiers.doi:
        return f"https://doi.org/{identifiers.doi}"
    if identifiers.openalex_id:
        return f"https://openalex.org/{identifiers.openalex_id}"
    if identifiers.zenon_id:
        return f"https://zenon.dainst.org/Record/{identifiers.zenon_id}"
    if identifiers.iaa_pub_id:
        return f"https://publications.iaa.org.il/{identifiers.iaa_pub_id}"
    if identifiers.adaj_id:
        # ADAJ records live under the DoA Jordan publications portal.
        # ``adaj_id`` is stored as ``"chapter:N"`` or ``"publication:N"`` —
        # map to the matching DoA landing path.
        if identifiers.adaj_id.startswith("chapter:"):
            n = identifiers.adaj_id.split(":", 1)[1]
            return f"https://publication.doa.gov.jo/Publications/ViewChapterPublic/{n}"
        if identifiers.adaj_id.startswith("publication:"):
            n = identifiers.adaj_id.split(":", 1)[1]
            return f"https://publication.doa.gov.jo/Publications/ViewPublic/{n}"
    if identifiers.arxiv_id:
        # arXiv ranks above Semantic Scholar because arxiv.org is the
        # source repository for preprints, while S2 is a pure indexer.
        # When a paper has both, the canonical URL is the arXiv one.
        return f"https://arxiv.org/abs/{identifiers.arxiv_id}"
    if identifiers.semantic_scholar_id:
        return f"https://www.semanticscholar.org/paper/{identifiers.semantic_scholar_id}"
    if identifiers.core_id:
        # CORE aggregates open-access repository content. The CORE
        # landing is canonical when no DOI is registered, even though
        # the underlying source is some institutional repo.
        return f"https://core.ac.uk/works/{identifiers.core_id}"
    if identifiers.europepmc_id:
        # Europe PMC landing — used as last-resort anchor for preprints
        # that haven't been Crossref-registered yet (rare; most bioRxiv
        # preprints have a DOI from day one).
        return f"https://europepmc.org/article/PPR/{identifiers.europepmc_id}"
    if open_access_url:
        return str(open_access_url)
    if landing_page_url:
        return str(landing_page_url)
    return None


def build_inline_citation(
    *,
    authors: List[str],
    year: Optional[int],
    pages: Optional[str],
    title: Optional[str],
    identifiers: Identifiers,
    landing_page_url: Optional[str],
    open_access_url: Optional[str],
    audit: Audit,
) -> InlineCitation:
    fallback_text = _format_fallback(authors, year, pages)
    primary_url = _pick_primary_url(identifiers, landing_page_url, open_access_url)

    if primary_url is None:
        # No link target — the agent must print the Author-Year string
        # bare; ⚠️-prefix does not attach to non-link prose.
        return InlineCitation(
            primary_url=None,
            display_domain=None,
            markdown_recommended=fallback_text,
            fallback_text=fallback_text,
        )

    domain = _display_domain(primary_url)
    ay_label = _authoryear_label(authors, year)
    trunc_title = _truncate_title(title) if title else None

    # Build the three variants. Each is either a finished string or
    # ``None`` when the underlying labels aren't available.
    markdown_authoryear = (
        f"[({ay_label})]({primary_url})" if ay_label else None
    )
    markdown_domain = f"[({domain})]({primary_url})"
    markdown_domain_title = (
        f"[({domain} — {trunc_title})]({primary_url})" if trunc_title else None
    )
    # DOI variant — only when a DOI is present. The label shows the
    # actual DOI string so bibliography entries surface it directly,
    # which is what readers want for cross-reference and BibTeX
    # round-tripping. The URL still resolves via doi.org.
    markdown_doi = (
        f"[({identifiers.doi})]({primary_url})" if identifiers.doi else None
    )

    display_label_authoryear = ay_label
    display_label_domain = domain
    display_label_domain_title = (
        f"{domain} — {trunc_title}" if trunc_title else None
    )
    display_label_doi = identifiers.doi if identifiers.doi else None

    # Heuristic for the agent's first-choice variant.
    # Aggregator hits override author-year because the reader must see
    # the aggregator domain in the label; academic hits use Author-Year
    # body-text form; web hits without author-year fall back to
    # Domain-Title; domain-only is the last resort.
    if audit.aggregator:
        markdown_recommended = markdown_domain_title or markdown_domain
    elif markdown_authoryear is not None:
        markdown_recommended = markdown_authoryear
    elif markdown_domain_title is not None:
        markdown_recommended = markdown_domain_title
    else:
        markdown_recommended = markdown_domain

    if audit.warn_marker or audit.aggregator:
        markdown_recommended = f"⚠️{markdown_recommended}"

    return InlineCitation(
        primary_url=primary_url,  # type: ignore[arg-type]
        display_domain=domain,
        display_label_authoryear=display_label_authoryear,
        display_label_domain=display_label_domain,
        display_label_domain_title=display_label_domain_title,
        display_label_doi=display_label_doi,
        markdown_authoryear=markdown_authoryear,
        markdown_domain=markdown_domain,
        markdown_domain_title=markdown_domain_title,
        markdown_doi=markdown_doi,
        markdown_recommended=markdown_recommended,
        fallback_text=fallback_text,
    )
