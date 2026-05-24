"""CORE adapter.

Endpoint
--------
``https://api.core.ac.uk/v3/search/works`` returns JSON. CORE
aggregates open-access full-text content from ~10k institutional
repositories — the strongest free OA-PDF discovery surface for
grey literature and dissertations.

API key
-------
The CORE v3 API requires a Bearer token. Register at
https://core.ac.uk/services/api → free tier. We read it from the
``CORE_API_KEY`` environment variable; without it the adapter raises
``CoreMissingApiKey`` rather than firing an unauthenticated request
(which would just return 401).

Audit semantics
---------------
Some ``dataProvider`` entries are themselves aggregators (Google
Books, ResearchGate, Academia.edu). When that happens we set
``audit.aggregator=True`` and ``audit.warn_marker=True`` so the
inline-citation builder prepends ⚠️ and chooses the domain-title
variant — making the secondary-source nature visible to the reader.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

from ..inline_citation import build_inline_citation
from ..models import Audit, DAOPaper, Identifiers, PublicationStatus, Venue
from ..utils.contact import CONTACT_EMAIL
from ..utils.doi import normalize_doi

log = logging.getLogger(__name__)

CORE_API = "https://api.core.ac.uk/v3/search/works"
HTTP_TIMEOUT = 30.0

_USER_AGENT = f"dao-paper-search-mcp/0.1 (+https://github.com/leiverkus/dao-paper-search-mcp; mailto:{CONTACT_EMAIL})"

# Substrings in dataProvider.name that mark a hit as a secondary
# aggregator rather than a primary repository. Lowercase comparison.
_AGGREGATOR_SUBSTRINGS = (
    "researchgate",
    "academia.edu",
    "google books",
    "google scholar",
    "citeseerx",
)


class CoreMissingApiKey(RuntimeError):
    """Raised when ``CORE_API_KEY`` is unset.

    Distinct from upstream errors so the agent can surface a clear
    "configuration missing" instead of "unauthenticated".
    """


def _format_authors(work: Mapping[str, Any]) -> list[str]:
    """Flip CORE display names to ``"Family, Given"``.

    CORE returns authors as ``[{name: "Given Family", ...}, ...]``.
    Same flip heuristic as OpenAlex/S2.
    """
    out: list[str] = []
    for a in work.get("authors") or []:
        if not isinstance(a, dict):
            continue
        name = (a.get("name") or "").strip()
        if not name:
            continue
        # Some CORE records already arrive in "Family, Given" form
        # (institutional repos with structured metadata) — preserve.
        if "," in name:
            out.append(name)
            continue
        parts = name.rsplit(" ", 1)
        if len(parts) == 2:
            given, family = parts
            out.append(f"{family}, {given}")
        else:
            out.append(name)
    return out


def _data_provider_name(work: Mapping[str, Any]) -> str | None:
    """Extract the human-readable name of the source repository."""
    dp = work.get("dataProvider") or {}
    if isinstance(dp, dict):
        name = (dp.get("name") or "").strip()
        return name or None
    return None


def _is_aggregator(provider_name: str | None) -> bool:
    if not provider_name:
        return False
    lowered = provider_name.lower()
    return any(needle in lowered for needle in _AGGREGATOR_SUBSTRINGS)


def _open_access_url(work: Mapping[str, Any]) -> str | None:
    """Pick the best OA URL: explicit downloadUrl, else first fulltext URL."""
    download = (work.get("downloadUrl") or "").strip()
    if download:
        return download
    fulltexts = work.get("sourceFulltextUrls") or []
    if isinstance(fulltexts, list):
        for url in fulltexts:
            s = str(url or "").strip()
            if s.startswith(("http://", "https://")):
                return s
    return None


def _publication_status(work: Mapping[str, Any]) -> PublicationStatus:
    doc_type = (work.get("documentType") or "").strip().lower()
    if doc_type == "preprint":
        return PublicationStatus.PREPRINT
    return PublicationStatus.PUBLISHED


def _verification_note(work: Mapping[str, Any]) -> str | None:
    """Surface non-article document types as a warning hint.

    CORE indexes theses, working papers, technical reports — useful
    grey literature but not always citable as journal articles. The
    agent uses this string to decide whether to weight the hit lower.
    """
    doc_type = (work.get("documentType") or "").strip()
    if doc_type and doc_type.lower() not in ("article", "research", "research_paper"):
        return f"document_type={doc_type}"
    return None


def _work_to_paper(work: Mapping[str, Any]) -> DAOPaper | None:
    core_id_raw = work.get("id")
    core_id = str(core_id_raw).strip() if core_id_raw is not None else None
    doi = normalize_doi(work.get("doi"))
    if not core_id and not doi:
        # Neither anchor → drop. Same policy as Crossref/OpenAlex/S2.
        return None

    title = (work.get("title") or "").strip() or "(untitled)"
    authors = _format_authors(work)
    year_raw = work.get("yearPublished")
    year = year_raw if isinstance(year_raw, int) else None
    abstract = (work.get("abstract") or "").strip() or None
    open_access_url = _open_access_url(work)
    language = ((work.get("language") or {}).get("code") if isinstance(work.get("language"), dict) else None) or "und"
    language = str(language).strip().lower() or "und"

    provider = _data_provider_name(work)
    aggregator = _is_aggregator(provider)
    verification_note = _verification_note(work)
    if aggregator and provider:
        # Stack the provider name into the verification_note so the
        # agent can surface "via ResearchGate" alongside the ⚠️ marker.
        suffix = f"aggregator={provider}"
        verification_note = f"{verification_note}; {suffix}" if verification_note else suffix

    if doi:
        doi_or_id = doi
        landing_page_url = f"https://doi.org/{doi}"
    elif core_id:
        doi_or_id = f"core:{core_id}"
        landing_page_url = f"https://core.ac.uk/works/{core_id}"
    else:
        # Unreachable given the earlier guard, but keeps mypy happy.
        return None

    identifiers = Identifiers(doi=doi, core_id=core_id)
    audit = Audit(
        primary_source=not aggregator,
        aggregator=aggregator,
        verification_note=verification_note,
        warn_marker=aggregator,
    )
    # CORE aggregates repository content; only ``journals[0].title`` is
    # ever structurally exposed. Volume/issue/pages are not reliably
    # carried, so we set name-only Venue when a journal is present.
    journals = work.get("journals") or []
    venue: Venue | None = None
    if isinstance(journals, list) and journals:
        j0 = journals[0]
        if isinstance(j0, dict):
            jt = (j0.get("title") or "").strip() or None
            if jt:
                venue = Venue(name=jt)
    inline_citation = build_inline_citation(
        authors=authors,
        year=year,
        pages=None,
        title=title,
        identifiers=identifiers,
        landing_page_url=landing_page_url,
        open_access_url=open_access_url,
        audit=audit,
        venue=venue,
    )

    return DAOPaper(
        title=title,
        authors=authors,
        year=year,
        doi_or_id=doi_or_id,
        source="core",
        open_access_url=open_access_url,  # type: ignore[arg-type]
        landing_page_url=landing_page_url,  # type: ignore[arg-type]
        language=language,
        abstract=abstract,
        publication_status=_publication_status(work),
        verification_note=verification_note,
        identifiers=identifiers,
        audit=audit,
        inline_citation=inline_citation,
    )


def _build_params(
    query: str,
    max_results: int,
    year_from: int | None,
    year_to: int | None,
) -> dict[str, Any]:
    """Build a CORE v3 JSON request body.

    CORE v3 accepts the search query as a JSON body with ``q`` and
    optional Lucene-style filters concatenated into the same string.
    """
    q = query
    if year_from is not None:
        q = f"{q} AND yearPublished>={year_from}"
    if year_to is not None:
        q = f"{q} AND yearPublished<={year_to}"
    return {
        "q": q,
        "limit": max(1, min(max_results, 100)),
        "offset": 0,
    }


async def search_core_impl(
    query: str,
    max_results: int = 10,
    year_from: int | None = None,
    year_to: int | None = None,
    *,
    client: httpx.AsyncClient | None = None,
) -> list[DAOPaper]:
    """Search CORE. ``client`` is injectable for tests."""
    api_key = os.getenv("CORE_API_KEY", "").strip()
    if not api_key:
        raise CoreMissingApiKey(
            "CORE_API_KEY environment variable is not set. "
            "Register a free token at https://core.ac.uk/services/api "
            "and export it as CORE_API_KEY."
        )

    body = _build_params(query, max_results, year_from, year_to)
    log.info("core.search query=%r limit=%d", body["q"], body["limit"])

    headers = {
        "User-Agent": _USER_AGENT,
        "Accept": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    async def _run(c: httpx.AsyncClient) -> list[DAOPaper]:
        r = await c.post(CORE_API, json=body, headers=headers, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        data = r.json()
        works = data.get("results") or []
        log.info("core.search hits=%d total=%s", len(works), data.get("totalHits"))
        papers = [_work_to_paper(w) for w in works]
        return [p for p in papers if p is not None]

    if client is not None:
        return await _run(client)
    async with httpx.AsyncClient() as c:
        return await _run(c)


def register(mcp: FastMCP) -> None:
    """Register the ``search_core`` tool."""

    @mcp.tool()
    async def search_core(
        query: str,
        max_results: int = 10,
        year_from: int | None = None,
        year_to: int | None = None,
    ) -> list[DAOPaper]:
        """Search CORE — the largest open-access full-text aggregator
        (~250M papers from ~10k institutional repositories). Strongest
        for: finding free OA-PDF versions of paywalled journal articles,
        retrieving theses and dissertations, surfacing institutional
        grey literature that paper-search-mcp's other adapters miss.

        Requires a free API key in the ``CORE_API_KEY`` environment
        variable; register at https://core.ac.uk/services/api.

        Citation rendering (Schema v2): each returned ``DAOPaper``
        carries an ``inline_citation`` block. Copy
        ``inline_citation.markdown`` verbatim for in-text citations —
        do not reformat. CORE hits sourced from aggregators
        (ResearchGate, Academia.edu, Google Books) are flagged with
        ``audit.aggregator=True`` and their ``markdown`` already carries
        a ⚠️ prefix. For bibliography / reference-list entries copy
        ``inline_citation.authoritative_bibliography_line`` verbatim —
        if it is ``None`` (incomplete venue metadata), fall back to
        Author-Year + URL/DOI rather than reconstructing the line from
        training knowledge.

        Args:
            query: free-text search query.
            max_results: 1-100.
            year_from: lower bound publication year, inclusive.
            year_to: upper bound publication year, inclusive.
        """
        return await search_core_impl(
            query=query,
            max_results=max_results,
            year_from=year_from,
            year_to=year_to,
        )
