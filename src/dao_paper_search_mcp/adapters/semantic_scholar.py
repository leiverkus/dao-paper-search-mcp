"""Semantic Scholar adapter.

Endpoint
--------
``https://api.semanticscholar.org/graph/v1/paper/search`` returns JSON.
Semantic Scholar's strength is the citation graph and cross-source
identifier coverage: about 70% of hits carry a DOI, and the rest
typically have an arXiv ID or a stable S2 paperId.

API key
-------
Operates without a key on the public bucket (~100 req/min). With an
``x-api-key`` header (env var ``SEMANTIC_SCHOLAR_API_KEY``) the limits
rise substantially. We send the header when the env var is present;
otherwise we run unauthenticated.

Quirks
------
- ``externalIds`` packs DOI, ArXiv, CorpusId, DBLP, PubMed and more in
  one object — we route DOI/ArXiv to our ``Identifiers`` block and use
  paperId as the S2-specific anchor.
- ``citationCount`` is surfaced via ``verification_note`` because it's
  a useful proxy for impact when ranking candidate citations.
- Author names come back in display order ("Given Family"); we flip to
  ``"Family, Given"`` for Author-Year extraction (same as OpenAlex).

No internal calls to other adapters. Architecture principle #2.
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
from ..utils import HttpxParams
from ..utils.contact import CONTACT_EMAIL
from ..utils.doi import normalize_doi

log = logging.getLogger(__name__)

S2_API = "https://api.semanticscholar.org/graph/v1/paper/search"
HTTP_TIMEOUT = 30.0

_USER_AGENT = f"dao-paper-search-mcp/0.1 (+https://github.com/leiverkus/dao-paper-search-mcp; mailto:{CONTACT_EMAIL})"

# Fields we request from the S2 graph API. Restricting fields shrinks
# response payloads and is faster than the default fat record.
_S2_FIELDS = ",".join(
    [
        "paperId",
        "externalIds",
        "title",
        "abstract",
        "year",
        "authors",
        "venue",
        "journal",
        "publicationDate",
        "publicationTypes",
        "citationCount",
        "openAccessPdf",
    ]
)


def _format_authors(paper: Mapping[str, Any]) -> list[str]:
    """Flip S2 ``"Given Family"`` display names to ``"Family, Given"``.

    Mirrors the OpenAlex adapter's handling because both APIs return
    display-form names rather than the structured family/given split
    that Crossref provides.
    """
    out: list[str] = []
    for a in paper.get("authors") or []:
        if not isinstance(a, dict):
            continue
        name = (a.get("name") or "").strip()
        if not name:
            continue
        parts = name.rsplit(" ", 1)
        if len(parts) == 2:
            given, family = parts
            out.append(f"{family}, {given}")
        else:
            out.append(name)
    return out


def _format_journal_or_volume(paper: Mapping[str, Any]) -> str | None:
    """Render ``"Journal Name 12(3)"`` from ``journal`` (preferred) or
    fall back to the free-text ``venue`` field.

    S2's ``journal`` object is structured (``name``/``volume``/``pages``)
    when available; ``venue`` is a single string used for conference
    proceedings and grey literature where structure is unreliable.
    """
    journal = paper.get("journal") or {}
    if isinstance(journal, dict):
        name = (journal.get("name") or "").strip()
        if name:
            volume = (journal.get("volume") or "").strip()
            if volume:
                return f"{name} {volume}"
            return name
    venue = (paper.get("venue") or "").strip()
    return venue or None


def _format_pages(paper: Mapping[str, Any]) -> str | None:
    journal = paper.get("journal") or {}
    if isinstance(journal, dict):
        pages = (journal.get("pages") or "").strip()
        return pages or None
    return None


def _build_venue(paper: Mapping[str, Any]) -> Venue | None:
    """Map S2 ``journal`` / ``venue`` into structured Venue.

    Issue is not exposed in S2's payload — only name/volume/pages. The
    free-text ``venue`` (conferences, grey literature) becomes the name
    when no structured ``journal`` is present.
    """
    journal = paper.get("journal") or {}
    name: str | None = None
    volume: str | None = None
    pages: str | None = None
    if isinstance(journal, dict):
        name = (journal.get("name") or "").strip() or None
        volume = (journal.get("volume") or "").strip() or None
        pages = (journal.get("pages") or "").strip() or None
    if not name:
        name = (paper.get("venue") or "").strip() or None
    if not any((name, volume, pages)):
        return None
    return Venue(name=name, volume=volume, issue=None, pages=pages)


def _publication_status(paper: Mapping[str, Any]) -> PublicationStatus:
    """Use ``publicationTypes`` to detect preprints; default to published.

    S2's vocabulary lists ``"JournalArticle"``, ``"Conference"``,
    ``"Review"``, ``"Preprint"`` (rarely), and bibliographic types like
    ``"Book"``. ArXiv-sourced papers are flagged as ``"Preprint"`` when
    no journal venue is attached.
    """
    types = paper.get("publicationTypes") or []
    if isinstance(types, list) and any(isinstance(t, str) and t.lower() == "preprint" for t in types):
        return PublicationStatus.PREPRINT
    return PublicationStatus.PUBLISHED


def _open_access_url(paper: Mapping[str, Any]) -> str | None:
    oa = paper.get("openAccessPdf") or {}
    if isinstance(oa, dict):
        url = (oa.get("url") or "").strip()
        return url or None
    return None


def _verification_note(paper: Mapping[str, Any]) -> str | None:
    """Surface the citation count so the agent can rank candidates.

    ``citationCount`` is a soft signal — high counts indicate
    well-known works, low counts on recent papers mean nothing. We
    expose it instead of hiding it; the agent decides what to do with
    the number.
    """
    count = paper.get("citationCount")
    if isinstance(count, int) and count > 0:
        return f"citation_count={count}"
    return None


def _paper_to_paper(paper: Mapping[str, Any]) -> DAOPaper | None:
    external = paper.get("externalIds") or {}
    if not isinstance(external, dict):
        external = {}

    doi = normalize_doi(external.get("DOI"))
    arxiv_id = (external.get("ArXiv") or "").strip() or None
    s2_id = (paper.get("paperId") or "").strip() or None

    if not (doi or arxiv_id or s2_id):
        # No canonical anchor — drop, same policy as Crossref/OpenAlex.
        return None

    title = (paper.get("title") or "").strip() or "(untitled)"
    authors = _format_authors(paper)
    year = paper.get("year")
    if not isinstance(year, int):
        year = None
    pages = _format_pages(paper)
    journal = _format_journal_or_volume(paper)
    open_access_url = _open_access_url(paper)
    abstract = (paper.get("abstract") or "").strip() or None
    verification_note = _verification_note(paper)

    # Pick the legacy ``doi_or_id`` and ``landing_page_url`` in source
    # priority — DOI > ArXiv > S2 — so existing tooling that reads the
    # flat string still routes correctly.
    if doi:
        doi_or_id = doi
        landing_page_url = f"https://doi.org/{doi}"
    elif arxiv_id:
        doi_or_id = f"arxiv:{arxiv_id}"
        landing_page_url = f"https://arxiv.org/abs/{arxiv_id}"
    else:
        doi_or_id = f"s2:{s2_id}"
        landing_page_url = f"https://www.semanticscholar.org/paper/{s2_id}"

    identifiers = Identifiers(
        doi=doi,
        arxiv_id=arxiv_id,
        semantic_scholar_id=s2_id,
    )
    audit = Audit(
        primary_source=True,
        aggregator=False,
        verification_note=verification_note,
        warn_marker=False,
    )
    inline_citation = build_inline_citation(
        authors=authors,
        year=year,
        pages=pages,
        title=title,
        identifiers=identifiers,
        landing_page_url=landing_page_url,
        open_access_url=open_access_url,
        audit=audit,
        venue=_build_venue(paper),
    )

    return DAOPaper(
        title=title,
        authors=authors,
        year=year,
        journal_or_volume=journal,
        pages=pages,
        doi_or_id=doi_or_id,
        source="semantic_scholar",
        open_access_url=open_access_url,  # type: ignore[arg-type]
        landing_page_url=landing_page_url,  # type: ignore[arg-type]
        language="und",  # S2 does not surface a language field reliably
        abstract=abstract,
        publication_status=_publication_status(paper),
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
) -> HttpxParams:
    """Build an S2 ``/paper/search`` parameter list.

    Year filter is a single ``year=YYYY-YYYY`` parameter (open-ended on
    either side: ``2010-`` for "2010 or later", ``-2010`` for "up to
    2010"). We always send ``fields`` to keep payloads small.
    """
    params: HttpxParams = [
        ("query", query),
        ("limit", str(max(1, min(max_results, 100)))),
        ("fields", _S2_FIELDS),
    ]
    if year_from is not None or year_to is not None:
        lo = str(year_from) if year_from is not None else ""
        hi = str(year_to) if year_to is not None else ""
        params.append(("year", f"{lo}-{hi}"))
    return params


async def search_semantic_scholar_impl(
    query: str,
    max_results: int = 10,
    year_from: int | None = None,
    year_to: int | None = None,
    *,
    client: httpx.AsyncClient | None = None,
) -> list[DAOPaper]:
    """Search Semantic Scholar. ``client`` is injectable for tests."""
    params = _build_params(query, max_results, year_from, year_to)
    log.info("s2.search query=%r filters=%s", query, [p for p in params if p[0] == "year"])

    headers: dict[str, str] = {
        "User-Agent": _USER_AGENT,
        "Accept": "application/json",
    }
    api_key = os.getenv("SEMANTIC_SCHOLAR_API_KEY", "").strip()
    if api_key:
        headers["x-api-key"] = api_key

    async def _run(c: httpx.AsyncClient) -> list[DAOPaper]:
        r = await c.get(S2_API, params=params, headers=headers, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        data = r.json()
        items = data.get("data") or []
        log.info("s2.search hits=%d total=%s", len(items), data.get("total"))
        papers = [_paper_to_paper(p) for p in items]
        return [p for p in papers if p is not None]

    if client is not None:
        return await _run(client)
    async with httpx.AsyncClient() as c:
        return await _run(c)


def register(mcp: FastMCP) -> None:
    """Register the ``search_semantic_scholar`` tool."""

    @mcp.tool()
    async def search_semantic_scholar(
        query: str,
        max_results: int = 10,
        year_from: int | None = None,
        year_to: int | None = None,
    ) -> list[DAOPaper]:
        """Search Semantic Scholar — citation graph + cross-source IDs.
        Strongest for: discovering recently-cited variants of a known
        paper, finding the canonical DOI for a fuzzily-remembered title,
        and surfacing ArXiv versions of journal-published works.
        Citation counts are exposed in ``verification_note`` (string
        ``"citation_count=N"``) as a soft ranking signal.

        Citation rendering (Schema v2): each returned ``DAOPaper``
        carries an ``inline_citation`` block. Copy
        ``inline_citation.markdown`` verbatim for in-text citations —
        do not reformat. For the bibliography / reference-list section
        copy ``inline_citation.authoritative_bibliography_line``
        verbatim; if it is ``None`` (venue metadata incomplete) fall
        back to Author-Year + URL/DOI rather than reconstructing the
        reference line from training knowledge.

        Set ``SEMANTIC_SCHOLAR_API_KEY`` in the environment for higher
        rate limits — the tool works without one but is throttled to
        the public bucket (~100 req/min).

        Args:
            query: free-text search (relevance-ranked by S2).
            max_results: 1-100.
            year_from: lower bound publication year, inclusive.
            year_to: upper bound publication year, inclusive.
        """
        return await search_semantic_scholar_impl(
            query=query,
            max_results=max_results,
            year_from=year_from,
            year_to=year_to,
        )
