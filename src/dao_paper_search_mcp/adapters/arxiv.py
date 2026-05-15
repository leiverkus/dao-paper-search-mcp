"""arXiv adapter.

Endpoint
--------
``http://export.arxiv.org/api/query`` returns an Atom XML feed. arXiv
is the canonical preprint server for physics, math, CS, and (relevant
here) the Digital-Humanities methods space — RAG / NLP / GIS / 3D
reconstruction papers that touch archaeology rarely show up in Zenon
or Crossref but appear here within days of upload.

Identifiers
-----------
Every entry has an arXiv ID (``2401.01234`` or older ``cs.AI/0102001``
form, both supported). About 30% of preprints later get a DOI when
the journal version is published; we route that DOI to
``Identifiers.doi`` and let the inline-citation builder prefer it.

Search query syntax
-------------------
arXiv uses a Lucene-style language with field prefixes:
``ti:`` (title), ``au:`` (author), ``abs:`` (abstract), ``all:``
(default), ``cat:`` (category, e.g. ``cs.AI``). We auto-prepend
``all:`` when no prefix is present so naïve queries work.

Year filtering uses ``submittedDate:[YYYYMMDDhhmm TO YYYYMMDDhhmm]``
ANDed into the search query — arXiv has no separate year filter.

XML parsing uses stdlib ``xml.etree.ElementTree`` because arXiv is a
trusted source and the Atom schema is simple. No new dependency.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from typing import Optional

import httpx
from mcp.server.fastmcp import FastMCP

from ..inline_citation import build_inline_citation
from ..models import Audit, DAOPaper, Identifiers, PublicationStatus

log = logging.getLogger(__name__)

ARXIV_API = "http://export.arxiv.org/api/query"
HTTP_TIMEOUT = 30.0

_USER_AGENT = (
    "dao-paper-search-mcp/0.1 "
    "(+https://github.com/leiverkus/dao-paper-search-mcp; "
    "mailto:patrick.leiverkus@uni-oldenburg.de)"
)

# Atom and arXiv namespace URIs. We pass these to ElementTree's findall
# so XPath expressions can resolve qualified tag names.
_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
}

# arXiv IDs come in two flavors. The new form ``YYMM.NNNNN`` has been
# used since 2007; the old form ``archive/YYMMNNN`` (e.g. ``cs.AI/0102001``)
# remains for legacy entries. Both can be followed by a ``vN`` version
# suffix which we strip — the bare ID is the citation-stable anchor.
_VERSION_SUFFIX_RE = re.compile(r"v\d+$")

# Acceptable field prefixes for arXiv's search_query. If none is
# detected, we wrap the user's query in ``all:`` to make naïve
# free-text queries work.
_QUERY_PREFIXES = ("all:", "ti:", "au:", "abs:", "cat:", "co:", "id:", "jr:")


def _extract_arxiv_id(entry_id: Optional[str]) -> Optional[str]:
    """``http://arxiv.org/abs/2401.01234v3`` → ``2401.01234``."""
    if not entry_id:
        return None
    s = entry_id.strip()
    # Drop scheme/host so the rsplit lands on the bare ID.
    for prefix in ("http://arxiv.org/abs/", "https://arxiv.org/abs/"):
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    s = _VERSION_SUFFIX_RE.sub("", s).strip("/")
    return s or None


def _pdf_link(entry: ET.Element) -> Optional[str]:
    """Find the ``link title="pdf"`` element on an Atom entry."""
    for link in entry.findall("atom:link", _NS):
        if (link.get("title") or "").lower() == "pdf":
            href = (link.get("href") or "").strip()
            return href or None
    return None


def _text(entry: ET.Element, tag: str) -> Optional[str]:
    """Return the stripped text of an atom-namespaced child tag, or None."""
    el = entry.find(f"atom:{tag}", _NS)
    if el is None or el.text is None:
        return None
    text = el.text.strip()
    return text or None


def _arxiv_text(entry: ET.Element, tag: str) -> Optional[str]:
    """Return the stripped text of an arxiv-namespaced child tag, or None."""
    el = entry.find(f"arxiv:{tag}", _NS)
    if el is None or el.text is None:
        return None
    text = el.text.strip()
    return text or None


def _format_authors(entry: ET.Element) -> list[str]:
    """Read each ``atom:author/atom:name`` and flip to ``"Family, Given"``."""
    out: list[str] = []
    for author in entry.findall("atom:author", _NS):
        name_el = author.find("atom:name", _NS)
        if name_el is None or name_el.text is None:
            continue
        name = name_el.text.strip()
        if not name:
            continue
        # arXiv reports "Given Family" in display order. ``rsplit`` on
        # the last space gives us a family-first split that works for
        # most names; hyphenated and single-token names fall through.
        parts = name.rsplit(" ", 1)
        if len(parts) == 2:
            given, family = parts
            out.append(f"{family}, {given}")
        else:
            out.append(name)
    return out


def _extract_year(entry: ET.Element) -> Optional[int]:
    published = _text(entry, "published")
    if published and len(published) >= 4:
        try:
            return int(published[:4])
        except ValueError:
            return None
    return None


def _entry_to_paper(entry: ET.Element) -> Optional[DAOPaper]:
    arxiv_id = _extract_arxiv_id(_text(entry, "id"))
    if not arxiv_id:
        # Without an arXiv ID we have no canonical anchor — drop.
        return None

    title_raw = _text(entry, "title") or "(untitled)"
    # arXiv linebreaks inside <title> are cosmetic; collapse whitespace.
    title = re.sub(r"\s+", " ", title_raw)
    abstract_raw = _text(entry, "summary")
    abstract = re.sub(r"\s+", " ", abstract_raw) if abstract_raw else None
    authors = _format_authors(entry)
    year = _extract_year(entry)
    doi = _arxiv_text(entry, "doi") or None
    journal_ref = _arxiv_text(entry, "journal_ref") or None
    pdf_url = _pdf_link(entry)

    # When the preprint gained a journal DOI we use that as the canonical
    # anchor; otherwise the arXiv landing is canonical. ``doi_or_id``
    # carries the prefixed legacy convention used elsewhere.
    if doi:
        doi_or_id = doi
        landing_page_url = f"https://doi.org/{doi}"
        status = PublicationStatus.PUBLISHED
    else:
        doi_or_id = f"arxiv:{arxiv_id}"
        landing_page_url = f"https://arxiv.org/abs/{arxiv_id}"
        status = PublicationStatus.PREPRINT

    identifiers = Identifiers(doi=doi, arxiv_id=arxiv_id)
    audit = Audit(primary_source=True, aggregator=False, warn_marker=False)
    inline_citation = build_inline_citation(
        authors=authors,
        year=year,
        pages=None,  # arXiv journal_ref is unstructured; pages rarely exposed
        title=title,
        identifiers=identifiers,
        landing_page_url=landing_page_url,
        open_access_url=pdf_url,
        audit=audit,
    )

    return DAOPaper(
        title=title,
        authors=authors,
        year=year,
        journal_or_volume=journal_ref,
        pages=None,
        doi_or_id=doi_or_id,
        source="arxiv",
        open_access_url=pdf_url,  # type: ignore[arg-type]
        landing_page_url=landing_page_url,  # type: ignore[arg-type]
        language="en",  # arXiv is English-only in practice
        abstract=abstract,
        publication_status=status,
        identifiers=identifiers,
        audit=audit,
        inline_citation=inline_citation,
    )


def _normalize_query(query: str) -> str:
    """Wrap a naïve free-text query in ``all:`` so arXiv accepts it.

    Lucene-style field prefixes (``ti:`` / ``au:`` / ``cat:`` etc.)
    are detected by token and left alone — power users can pass the
    full search_query DSL.
    """
    stripped = query.strip()
    lowered = stripped.lower()
    if any(lowered.startswith(p) for p in _QUERY_PREFIXES):
        return stripped
    return f"all:{stripped}"


def _apply_year_filter(
    search_query: str,
    year_from: Optional[int],
    year_to: Optional[int],
) -> str:
    """ANDs ``submittedDate:[lo TO hi]`` into the search_query.

    arXiv accepts ``YYYYMMDDhhmm``-form bounds. Wide defaults (1900 /
    2999) cover the open-ended cases without complicating the parser.
    """
    if year_from is None and year_to is None:
        return search_query
    lo = f"{year_from:04d}01010000" if year_from is not None else "190001010000"
    hi = f"{year_to:04d}12312359" if year_to is not None else "299912312359"
    return f"({search_query}) AND submittedDate:[{lo} TO {hi}]"


def _build_params(
    query: str,
    max_results: int,
    year_from: Optional[int],
    year_to: Optional[int],
) -> list[tuple[str, str]]:
    """Build an arXiv ``/query`` parameter list."""
    sq = _apply_year_filter(_normalize_query(query), year_from, year_to)
    return [
        ("search_query", sq),
        ("start", "0"),
        ("max_results", str(max(1, min(max_results, 100)))),
        ("sortBy", "relevance"),
        ("sortOrder", "descending"),
    ]


def _parse_atom(xml_text: str) -> list[DAOPaper]:
    """Walk every ``atom:entry`` in the response into ``DAOPaper`` objects.

    Malformed XML raises ``ET.ParseError`` to the caller — an empty
    feed is a legitimate "no results"; an unparseable feed is an
    upstream regression worth surfacing.
    """
    root = ET.fromstring(xml_text)
    papers: list[DAOPaper] = []
    for entry in root.findall("atom:entry", _NS):
        p = _entry_to_paper(entry)
        if p is not None:
            papers.append(p)
    return papers


async def search_arxiv_impl(
    query: str,
    max_results: int = 10,
    year_from: Optional[int] = None,
    year_to: Optional[int] = None,
    *,
    client: Optional[httpx.AsyncClient] = None,
) -> list[DAOPaper]:
    """Search arXiv. ``client`` is injectable for tests."""
    params = _build_params(query, max_results, year_from, year_to)
    log.info("arxiv.search query=%r", params[0][1])

    async def _run(c: httpx.AsyncClient) -> list[DAOPaper]:
        headers = {"User-Agent": _USER_AGENT, "Accept": "application/atom+xml"}
        r = await c.get(ARXIV_API, params=params, headers=headers, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        papers = _parse_atom(r.text)
        log.info("arxiv.search hits=%d", len(papers))
        return papers

    if client is not None:
        return await _run(client)
    async with httpx.AsyncClient() as c:
        return await _run(c)


def register(mcp: FastMCP) -> None:
    """Register the ``search_arxiv`` tool."""

    @mcp.tool()
    async def search_arxiv(
        query: str,
        max_results: int = 10,
        year_from: Optional[int] = None,
        year_to: Optional[int] = None,
    ) -> list[DAOPaper]:
        """Search arXiv — preprint server for physics, math, CS, and
        Digital-Humanities methods (NLP, RAG, GIS, 3D reconstruction).
        Strongest for: brand-new methodology papers that haven't reached
        Crossref yet, and for finding preprint versions of paywalled
        journal articles.

        Query syntax supports arXiv's Lucene-style prefixes (``ti:`` /
        ``au:`` / ``abs:`` / ``cat:`` / ``all:``). Naïve free-text queries
        are auto-wrapped in ``all:``. Examples:

            "negev iron age"          → all:negev iron age
            "cat:cs.AI RAG"           → unchanged
            "au:cohen ti:negev"       → unchanged

        Citation rendering: each returned ``DAOPaper`` carries an
        ``inline_citation`` block with pre-rendered Markdown. Copy
        ``inline_citation.markdown_recommended`` verbatim when citing
        a hit — do not reformat to ``[(domain)](url)``. For preprints
        that gained a journal DOI later, the recommended form points
        at ``doi.org``; for preprint-only hits, it points at
        ``arxiv.org``. For bibliography or reference-list entries,
        prefer ``inline_citation.markdown_doi`` when present — the
        visible label is the actual DOI string, useful for
        cross-reference and BibTeX round-tripping.

        Args:
            query: search_query in arXiv's Lucene syntax (or free text
                that gets auto-wrapped in ``all:``).
            max_results: 1-100.
            year_from: lower bound submission year, inclusive.
            year_to: upper bound submission year, inclusive.
        """
        return await search_arxiv_impl(
            query=query,
            max_results=max_results,
            year_from=year_from,
            year_to=year_to,
        )
