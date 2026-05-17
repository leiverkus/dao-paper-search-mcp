"""OpenAlex adapter.

Endpoint
--------
``https://api.openalex.org/works`` returns JSON. OpenAlex is the
broadest open scholarly graph (~250M works) with rich relational
metadata: institutions, concepts, citation links. About 85% of hits
carry a DOI; the rest are identified only by OpenAlex Work ID
(``W<digits>``).

Polite pool
-----------
Including a ``mailto`` query parameter moves us into the polite pool
with higher rate limits. We pass it as a query param (OpenAlex's
preferred style) rather than the User-Agent header.

Quirks
------
- DOIs come back as full URLs (``https://doi.org/10.x/y``); we strip
  to the bare DOI form for ``Identifiers.doi``.
- OpenAlex Work IDs come back as full URLs
  (``https://openalex.org/W123``); we strip to the bare ``W123`` form.
- Abstracts are stored as an inverted index
  (``{"word": [pos0, pos1, …]}``); we reconstruct word-by-position.
- ``display_name`` is more reliable than ``title`` for the title field.

No internal calls to other adapters. Architecture principle #2.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping, Optional

import httpx
from mcp.server.fastmcp import FastMCP

from ..inline_citation import build_inline_citation
from ..models import Audit, DAOPaper, Identifiers, PublicationStatus, Venue
from ..utils.contact import CONTACT_EMAIL
from ..utils.doi import normalize_doi

log = logging.getLogger(__name__)

OPENALEX_API = "https://api.openalex.org/works"
HTTP_TIMEOUT = 30.0

# Polite-pool identifier — OpenAlex docs prefer this as a query
# parameter, not a User-Agent suffix. Set DAO_PAPER_SEARCH_CONTACT_EMAIL
# to improve rate-limit priority.
_POLITE_MAILTO = CONTACT_EMAIL
_USER_AGENT = "dao-paper-search-mcp/0.1 (+https://github.com/leiverkus/dao-paper-search-mcp)"


def _strip_openalex_id_prefix(work_id_url: Optional[str]) -> Optional[str]:
    """``https://openalex.org/W123`` → ``W123``."""
    if not work_id_url:
        return None
    s = work_id_url.strip()
    if "/" in s:
        s = s.rsplit("/", 1)[-1]
    return s or None


def _format_authors(work: Mapping[str, Any]) -> list[str]:
    """Render OpenAlex authorships as ``"Family, Given"``.

    OpenAlex normalizes ``display_name`` to ``"Given Family"``. We
    reverse to last-name-first so the Author-Year citation builder
    extracts the family name correctly.
    """
    out: list[str] = []
    for ship in work.get("authorships") or []:
        if not isinstance(ship, dict):
            continue
        author = ship.get("author") or {}
        name = (author.get("display_name") or "").strip()
        if not name:
            continue
        # OpenAlex display_name is "Given Family"; flip to "Family, Given".
        parts = name.rsplit(" ", 1)
        if len(parts) == 2:
            given, family = parts
            out.append(f"{family}, {given}")
        else:
            out.append(name)
    return out


def _format_journal_or_volume(work: Mapping[str, Any]) -> Optional[str]:
    """Render ``"Journal Name 12(3)"`` from ``primary_location.source`` + biblio."""
    primary = work.get("primary_location") or {}
    source = primary.get("source") if isinstance(primary, dict) else None
    if not isinstance(source, dict):
        return None
    name = (source.get("display_name") or "").strip()
    if not name:
        return None
    biblio = work.get("biblio") or {}
    volume = (biblio.get("volume") or "").strip()
    issue = (biblio.get("issue") or "").strip()
    if volume and issue:
        return f"{name} {volume}({issue})"
    if volume:
        return f"{name} {volume}"
    return name


def _format_pages(work: Mapping[str, Any]) -> Optional[str]:
    biblio = work.get("biblio") or {}
    first = (biblio.get("first_page") or "").strip()
    last = (biblio.get("last_page") or "").strip()
    if first and last:
        return f"{first}-{last}"
    return first or None


def _build_venue(work: Mapping[str, Any]) -> Optional[Venue]:
    primary = work.get("primary_location") or {}
    source = primary.get("source") if isinstance(primary, dict) else None
    name: Optional[str] = None
    if isinstance(source, dict):
        name = (source.get("display_name") or "").strip() or None
    biblio = work.get("biblio") or {}
    volume = (biblio.get("volume") or "").strip() or None
    issue = (biblio.get("issue") or "").strip() or None
    pages = _format_pages(work)
    if not any((name, volume, issue, pages)):
        return None
    return Venue(name=name, volume=volume, issue=issue, pages=pages)


def _reconstruct_abstract(inv: Optional[Mapping[str, Any]]) -> Optional[str]:
    """Rebuild a readable abstract from OpenAlex's inverted index.

    OpenAlex stores abstracts as ``{word: [position, …]}`` to keep the
    data redistributable under their license. We restore word order by
    sorting (position, word) pairs.
    """
    if not isinstance(inv, dict) or not inv:
        return None
    positions: list[tuple[int, str]] = []
    for word, idxs in inv.items():
        if not isinstance(idxs, list):
            continue
        for i in idxs:
            if isinstance(i, int):
                positions.append((i, str(word)))
    if not positions:
        return None
    positions.sort()
    return " ".join(w for _, w in positions)


def _publication_status_from_type(work_type: Optional[str]) -> PublicationStatus:
    if work_type == "preprint":
        return PublicationStatus.PREPRINT
    return PublicationStatus.PUBLISHED


def _open_access_url(work: Mapping[str, Any]) -> Optional[str]:
    oa = work.get("open_access") or {}
    if not isinstance(oa, dict):
        return None
    url = (oa.get("oa_url") or "").strip()
    return url or None


def _work_to_paper(work: Mapping[str, Any]) -> Optional[DAOPaper]:
    doi = normalize_doi(work.get("doi"))
    openalex_id = _strip_openalex_id_prefix(work.get("id"))
    if not doi and not openalex_id:
        # Neither identifier present — drop. We need at least one
        # canonical anchor for the inline-citation builder.
        return None

    title = (work.get("display_name") or work.get("title") or "").strip() or "(untitled)"
    authors = _format_authors(work)
    year = work.get("publication_year")
    if not isinstance(year, int):
        year = None
    pages = _format_pages(work)
    journal = _format_journal_or_volume(work)
    open_access_url = _open_access_url(work)
    language = (work.get("language") or "und").strip().lower() or "und"
    abstract = _reconstruct_abstract(work.get("abstract_inverted_index"))

    # ``doi_or_id`` keeps the prefixed-string convention used elsewhere
    # in this codebase: ``"openalex:W123"`` when no DOI, else the raw DOI.
    if doi:
        doi_or_id = doi
        landing_page_url = f"https://doi.org/{doi}"
    else:
        doi_or_id = f"openalex:{openalex_id}"
        landing_page_url = f"https://openalex.org/{openalex_id}"

    identifiers = Identifiers(doi=doi, openalex_id=openalex_id)
    audit = Audit(primary_source=True, aggregator=False, warn_marker=False)
    inline_citation = build_inline_citation(
        authors=authors,
        year=year,
        pages=pages,
        title=title,
        identifiers=identifiers,
        landing_page_url=landing_page_url,
        open_access_url=open_access_url,
        audit=audit,
        venue=_build_venue(work),
    )

    return DAOPaper(
        title=title,
        authors=authors,
        year=year,
        journal_or_volume=journal,
        pages=pages,
        doi_or_id=doi_or_id,
        source="openalex",
        open_access_url=open_access_url,  # type: ignore[arg-type]
        landing_page_url=landing_page_url,  # type: ignore[arg-type]
        language=language,
        abstract=abstract,
        publication_status=_publication_status_from_type(work.get("type")),
        identifiers=identifiers,
        audit=audit,
        inline_citation=inline_citation,
    )


def _build_params(
    query: str,
    max_results: int,
    language: Optional[str],
    year_from: Optional[int],
    year_to: Optional[int],
) -> list[tuple[str, str]]:
    """Build an OpenAlex ``/works`` parameter list.

    Filters are comma-joined into a single ``filter`` value because
    OpenAlex expects all filters in one parameter slot.
    """
    params: list[tuple[str, str]] = [
        ("search", query),
        ("per_page", str(max(1, min(max_results, 100)))),
        ("mailto", _POLITE_MAILTO),
    ]
    filters: list[str] = []
    if language:
        # OpenAlex language uses ISO-639-1; pass-through.
        filters.append(f"language:{language.lower()}")
    if year_from is not None:
        filters.append(f"from_publication_date:{year_from}-01-01")
    if year_to is not None:
        filters.append(f"to_publication_date:{year_to}-12-31")
    if filters:
        params.append(("filter", ",".join(filters)))
    return params


async def search_openalex_impl(
    query: str,
    max_results: int = 10,
    language: Optional[str] = None,
    year_from: Optional[int] = None,
    year_to: Optional[int] = None,
    *,
    client: Optional[httpx.AsyncClient] = None,
) -> list[DAOPaper]:
    """Search OpenAlex. ``client`` is injectable for tests."""
    params = _build_params(query, max_results, language, year_from, year_to)
    log.info("openalex.search query=%r filters=%s", query,
             [p for p in params if p[0] == "filter"])

    async def _run(c: httpx.AsyncClient) -> list[DAOPaper]:
        headers = {"User-Agent": _USER_AGENT, "Accept": "application/json"}
        r = await c.get(OPENALEX_API, params=params, headers=headers, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        data = r.json()
        works = data.get("results") or []
        log.info("openalex.search hits=%d total=%s", len(works),
                 (data.get("meta") or {}).get("count"))
        papers = [_work_to_paper(w) for w in works]
        return [p for p in papers if p is not None]

    if client is not None:
        return await _run(client)
    async with httpx.AsyncClient() as c:
        return await _run(c)


def register(mcp: FastMCP) -> None:
    """Register the ``search_openalex`` tool."""

    @mcp.tool()
    async def search_openalex(
        query: str,
        max_results: int = 10,
        language: Optional[str] = None,
        year_from: Optional[int] = None,
        year_to: Optional[int] = None,
    ) -> list[DAOPaper]:
        """Search OpenAlex — the broadest open scholarly graph (~250M works).
        Strongest for cross-disciplinary discovery, citation networks,
        institutional metadata, and works without DOIs (preprints,
        institutional reports). About 85% of hits carry a DOI.

        Citation rendering (Schema v2): each returned ``DAOPaper``
        carries an ``inline_citation`` block. Copy
        ``inline_citation.markdown`` verbatim for in-text citations —
        do not reformat. For the bibliography / reference-list section
        copy ``inline_citation.authoritative_bibliography_line``
        verbatim; if it is ``None`` (venue metadata incomplete) fall
        back to Author-Year + URL/DOI rather than reconstructing the
        reference line from training knowledge.

        Args:
            query: free-text search (relevance-ranked).
            max_results: 1-100.
            language: ISO-639-1 code (``"en"`` | ``"de"`` | ``"fr"`` | …).
                Server-side filter.
            year_from: lower bound publication year, inclusive.
            year_to: upper bound publication year, inclusive.
        """
        return await search_openalex_impl(
            query=query,
            max_results=max_results,
            language=language,
            year_from=year_from,
            year_to=year_to,
        )
