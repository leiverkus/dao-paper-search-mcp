"""Crossref adapter.

Endpoint
--------
``https://api.crossref.org/works`` returns JSON with one DOI-bearing
record per ``message.items[]`` entry. Crossref is the canonical DOI
registry — every hit has a DOI, so the inline-citation builder always
picks ``https://doi.org/{doi}`` as ``primary_url``.

Polite pool
-----------
Including a ``mailto`` in the User-Agent (or as a query parameter)
moves us into Crossref's polite pool: higher rate limits and priority
during incidents. We use the User-Agent header form.

Query shape
-----------
- ``query.bibliographic`` — free-text keyword search across title,
  authors, container-title, ISSN, ISBN, DOI
- ``rows`` — page size, 1-100 (Crossref accepts up to 1000 but caps
  noisy queries)
- ``filter=from-pub-date:YYYY,until-pub-date:YYYY`` — year range

No internal calls to other adapters. Architecture principle #2.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Mapping, Optional

import httpx
from mcp.server.fastmcp import FastMCP

from ..inline_citation import build_inline_citation
from ..models import Audit, DAOPaper, Identifiers, PublicationStatus

log = logging.getLogger(__name__)

CROSSREF_API = "https://api.crossref.org/works"
HTTP_TIMEOUT = 30.0

# Polite-pool identification — bumps us out of the public bucket and
# into the higher-priority queue.
_USER_AGENT = (
    "dao-paper-search-mcp/0.1 "
    "(+https://github.com/leiverkus/dao-paper-search-mcp; "
    "mailto:patrick.leiverkus@uni-oldenburg.de)"
)

# Strip JATS markup such as ``<jats:p>…</jats:p>`` so the abstract is
# readable plain text. Crossref embeds JATS XML inside the abstract
# field on most journal articles.
_JATS_TAG_RE = re.compile(r"<[^>]+>")
_JATS_WS_RE = re.compile(r"\s+")


def _first_or_none(seq: Any) -> Optional[str]:
    """Return the first string in a list-like, or None."""
    if isinstance(seq, list) and seq:
        v = seq[0]
        return str(v).strip() or None
    return None


def _format_authors(item: Mapping[str, Any]) -> list[str]:
    """Render Crossref's structured author block as ``"Family, Given"``.

    Crossref distinguishes personal authors (``family``/``given``) from
    corporate authors (``name``). We surface personal authors only for
    the ``authors`` list; corporate authors would distort the
    Author-Year citation form, and agents can still fetch them from the
    raw record if needed.
    """
    out: list[str] = []
    for a in item.get("author") or []:
        if not isinstance(a, dict):
            continue
        family = (a.get("family") or "").strip()
        given = (a.get("given") or "").strip()
        if family and given:
            out.append(f"{family}, {given}")
        elif family:
            out.append(family)
        # If only ``name`` (corporate) is present, skip — see docstring.
    return out


def _extract_year(item: Mapping[str, Any]) -> Optional[int]:
    """Pull the first year from ``published`` / ``issued`` / ``created``.

    Crossref's date provenance is messy: ``published-print`` and
    ``published-online`` are preferred, ``issued`` is a fallback,
    ``created`` is the registration date (last resort). Each is a
    ``{"date-parts": [[year, month, day]]}`` structure.
    """
    for key in ("published-print", "published-online", "published", "issued", "created"):
        node = item.get(key)
        if isinstance(node, dict):
            parts = node.get("date-parts")
            if isinstance(parts, list) and parts and isinstance(parts[0], list) and parts[0]:
                year = parts[0][0]
                if isinstance(year, int):
                    return year
                try:
                    return int(year)
                except (TypeError, ValueError):
                    continue
    return None


def _format_journal_or_volume(item: Mapping[str, Any]) -> Optional[str]:
    """Render ``"Journal Name 12(3)"`` or ``"Journal Name 12"`` or None."""
    title = _first_or_none(item.get("container-title"))
    if not title:
        return None
    volume = (item.get("volume") or "").strip()
    issue = (item.get("issue") or "").strip()
    if volume and issue:
        return f"{title} {volume}({issue})"
    if volume:
        return f"{title} {volume}"
    return title


def _full_title(item: Mapping[str, Any]) -> str:
    title = (_first_or_none(item.get("title")) or "").rstrip(":/ ")
    subtitle = (_first_or_none(item.get("subtitle")) or "").rstrip(":/ ")
    if subtitle and subtitle.lower() not in title.lower():
        return f"{title}: {subtitle}" if title else subtitle
    return title or "(untitled)"


def _strip_jats(abstract: Optional[str]) -> Optional[str]:
    if not abstract:
        return None
    text = _JATS_TAG_RE.sub("", abstract)
    text = _JATS_WS_RE.sub(" ", text).strip()
    return text or None


def _publication_status_from_type(item_type: Optional[str]) -> PublicationStatus:
    """Map Crossref ``type`` to our enum.

    ``posted-content`` is Crossref's term for preprints; everything else
    we treat as published. We do not invent ``forthcoming`` here —
    Crossref only registers things that have a DOI, which implies
    public release.
    """
    if item_type == "posted-content":
        return PublicationStatus.PREPRINT
    return PublicationStatus.PUBLISHED


def _item_to_paper(item: Mapping[str, Any]) -> Optional[DAOPaper]:
    doi = (item.get("DOI") or "").strip()
    if not doi:
        # Without a DOI we have no stable identifier and no link target —
        # rather than fabricate a fallback, drop the hit. Hallucination
        # prevention beats coverage.
        return None

    title = _full_title(item)
    authors = _format_authors(item)
    year = _extract_year(item)
    pages = (item.get("page") or "").strip() or None
    journal = _format_journal_or_volume(item)
    landing_page_url = (item.get("URL") or "").strip() or f"https://doi.org/{doi}"
    language = (item.get("language") or "und").strip().lower() or "und"
    abstract = _strip_jats(item.get("abstract"))

    identifiers = Identifiers(doi=doi)
    audit = Audit(primary_source=True, aggregator=False, warn_marker=False)
    inline_citation = build_inline_citation(
        authors=authors,
        year=year,
        pages=pages,
        title=title,
        identifiers=identifiers,
        landing_page_url=landing_page_url,
        open_access_url=None,
        audit=audit,
    )

    return DAOPaper(
        title=title,
        authors=authors,
        year=year,
        journal_or_volume=journal,
        pages=pages,
        doi_or_id=doi,
        source="crossref",
        landing_page_url=landing_page_url,  # type: ignore[arg-type]
        language=language,
        abstract=abstract,
        publication_status=_publication_status_from_type(item.get("type")),
        identifiers=identifiers,
        audit=audit,
        inline_citation=inline_citation,
    )


def _build_params(
    query: str,
    max_results: int,
    year_from: Optional[int],
    year_to: Optional[int],
) -> list[tuple[str, str]]:
    """Build a Crossref ``/works`` parameter list.

    Uses ``query.bibliographic`` for free-text (broader than ``query``,
    which only searches titles). Year-range filters are encoded as a
    single comma-joined ``filter`` value because Crossref expects all
    filters in one parameter.
    """
    params: list[tuple[str, str]] = [
        ("query.bibliographic", query),
        ("rows", str(max(1, min(max_results, 100)))),
    ]
    filters: list[str] = []
    if year_from is not None:
        filters.append(f"from-pub-date:{year_from}")
    if year_to is not None:
        filters.append(f"until-pub-date:{year_to}")
    if filters:
        params.append(("filter", ",".join(filters)))
    return params


async def search_crossref_impl(
    query: str,
    max_results: int = 10,
    year_from: Optional[int] = None,
    year_to: Optional[int] = None,
    *,
    client: Optional[httpx.AsyncClient] = None,
) -> list[DAOPaper]:
    """Search Crossref. ``client`` is injectable for tests."""
    params = _build_params(query, max_results, year_from, year_to)
    log.info("crossref.search query=%r filters=%s", query, params[2:])

    async def _run(c: httpx.AsyncClient) -> list[DAOPaper]:
        headers = {"User-Agent": _USER_AGENT, "Accept": "application/json"}
        r = await c.get(CROSSREF_API, params=params, headers=headers, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        data = r.json()
        items = (data.get("message") or {}).get("items") or []
        log.info("crossref.search hits=%d total=%s", len(items),
                 (data.get("message") or {}).get("total-results"))
        papers = [_item_to_paper(item) for item in items]
        return [p for p in papers if p is not None]

    if client is not None:
        return await _run(client)
    async with httpx.AsyncClient() as c:
        return await _run(c)


def register(mcp: FastMCP) -> None:
    """Register the ``search_crossref`` tool."""

    @mcp.tool()
    async def search_crossref(
        query: str,
        max_results: int = 10,
        year_from: Optional[int] = None,
        year_to: Optional[int] = None,
    ) -> list[DAOPaper]:
        """Search Crossref — the canonical DOI registry, ~150M scholarly works.
        Use for verifying mainstream peer-reviewed Anglophone literature
        (Cambridge, Wiley, Elsevier, Tandfonline, etc.) by DOI or
        bibliographic query.

        Every hit has a DOI, so the inline-citation will always be in
        ``[(Author Year)](https://doi.org/...)`` form for academic
        body-text rendering.

        Citation rendering: each returned ``DAOPaper`` carries an
        ``inline_citation`` block with pre-rendered Markdown. Copy
        ``inline_citation.markdown_recommended`` verbatim when citing a
        hit — do not reformat to ``[(domain)](url)``. Every Crossref
        hit has a DOI, so for bibliography or reference-list entries
        use ``inline_citation.markdown_doi`` — the visible label is
        the actual DOI string, useful for cross-reference and BibTeX
        round-tripping.

        Args:
            query: free-text bibliographic query (matches title, authors,
                journal, ISSN, ISBN, DOI). Boolean operators are not
                supported by Crossref — just space-separate terms.
            max_results: 1-100.
            year_from: lower bound publication year, inclusive.
            year_to: upper bound publication year, inclusive.
        """
        return await search_crossref_impl(
            query=query,
            max_results=max_results,
            year_from=year_from,
            year_to=year_to,
        )
