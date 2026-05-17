"""bioRxiv (+ medRxiv) preprint adapter.

Backend choice
--------------
bioRxiv's native ``api.biorxiv.org`` only supports DOI-lookup and
date-range queries — there is no free-text search endpoint. The
de-facto keyword-search surface for bioRxiv and medRxiv preprints is
**Europe PMC** (``SRC:PPR`` filter), which indexes both servers
within hours of upload and offers a real Lucene query interface.

We therefore present this adapter as ``search_biorxiv`` (the source
the agent thinks about) but call Europe PMC under the hood. The
adapter filters results to bioRxiv/medRxiv content client-side via
``journalTitle`` so callers don't pick up ResearchSquare / OSF /
SSRN preprints they didn't ask for.

Relevance
---------
Strongest for ancient-DNA / paleogenomic / bioarchaeology preprints
that sit on bioRxiv 6–12 months before journal publication. The
Levant aDNA literature (Lazaridis, Feldman, Harney, Reich Lab) is
the canonical use case — most of these papers appear on bioRxiv long
before they reach Crossref/OpenAlex.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Mapping, Optional

import httpx
from mcp.server.fastmcp import FastMCP

from ..inline_citation import build_inline_citation
from ..models import Audit, DAOPaper, Identifiers, PublicationStatus, Venue

log = logging.getLogger(__name__)

EUROPEPMC_API = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
HTTP_TIMEOUT = 30.0

_USER_AGENT = (
    "dao-paper-search-mcp/0.1 "
    "(+https://github.com/leiverkus/dao-paper-search-mcp; "
    "mailto:patrick.leiverkus@uni-oldenburg.de)"
)

# Europe PMC normalises preprint server names in ``journalTitle``. We
# match case-insensitively to be defensive against capitalisation drift.
_BIORXIV_NAMES = {"biorxiv"}
_MEDRXIV_NAMES = {"medrxiv"}

# Strip HTML/JATS markup from abstracts — Europe PMC sometimes returns
# minimal HTML tags inside ``abstractText``.
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_HTML_WS_RE = re.compile(r"\s+")


def _strip_markup(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    stripped = _HTML_TAG_RE.sub("", text)
    return _HTML_WS_RE.sub(" ", stripped).strip() or None


def _parse_author_string(author_string: str) -> list[str]:
    """Split Europe PMC's ``"Smith J, Doe J, Müller A"`` style.

    Each comma-separated token is ``"Family Initials"``. We flip to
    ``"Family, Initials"`` so the Author-Year builder extracts the
    family name (which it does by splitting on the comma).
    """
    out: list[str] = []
    for token in author_string.split(","):
        s = token.strip()
        if not s:
            continue
        # If a comma is already present in this token, preserve as-is.
        # Otherwise split on the last space to find the initials.
        parts = s.rsplit(" ", 1)
        if len(parts) == 2:
            family, initials = parts
            out.append(f"{family}, {initials}")
        else:
            out.append(s)
    return out


def _doi_from_record(record: Mapping[str, Any]) -> Optional[str]:
    """Pull the DOI from the top-level ``doi`` field or the fulltext id list."""
    doi = (record.get("doi") or "").strip()
    if doi:
        return doi
    # Europe PMC sometimes ships the DOI inside ``fullTextIdList.fullTextId``.
    ft_list = record.get("fullTextIdList") or {}
    if isinstance(ft_list, dict):
        ids = ft_list.get("fullTextId") or []
        if isinstance(ids, list):
            for entry in ids:
                s = str(entry or "").strip()
                if s.startswith("10."):
                    return s
    return None


def _open_access_url(record: Mapping[str, Any]) -> Optional[str]:
    """Pick the bioRxiv/medRxiv PDF URL when Europe PMC surfaces one."""
    ft = record.get("fullTextUrlList") or {}
    if not isinstance(ft, dict):
        return None
    urls = ft.get("fullTextUrl") or []
    if not isinstance(urls, list):
        return None
    # Prefer the bioRxiv/medRxiv-hosted PDF; fall back to any PDF.
    pdf_url: Optional[str] = None
    for entry in urls:
        if not isinstance(entry, dict):
            continue
        doc_style = (entry.get("documentStyle") or "").lower()
        url = (entry.get("url") or "").strip()
        if not url:
            continue
        if doc_style == "pdf" and ("biorxiv.org" in url or "medrxiv.org" in url):
            return url
        if doc_style == "pdf" and pdf_url is None:
            pdf_url = url
    return pdf_url


def _journal_matches(journal: str, include_medrxiv: bool) -> bool:
    """True when ``journal`` is bioRxiv (always) or medRxiv (if requested).

    Client-side filter: Europe PMC's ``SRC:PPR`` includes other preprint
    servers (ResearchSquare, OSF, SSRN). We narrow to just the two the
    caller asked for so the surface stays predictable.
    """
    j = journal.strip().lower()
    if j in _BIORXIV_NAMES:
        return True
    if include_medrxiv and j in _MEDRXIV_NAMES:
        return True
    return False


def _record_to_paper(record: Mapping[str, Any]) -> Optional[DAOPaper]:
    epmc_id_raw = record.get("id")
    epmc_id = str(epmc_id_raw).strip() if epmc_id_raw is not None else None
    doi = _doi_from_record(record)
    if not doi and not epmc_id:
        # No anchor — drop.
        return None

    title = _strip_markup(record.get("title")) or "(untitled)"
    abstract = _strip_markup(record.get("abstractText"))
    journal = (record.get("journalTitle") or "").strip()
    author_string = (record.get("authorString") or "").strip()
    authors = _parse_author_string(author_string) if author_string else []

    year_raw = record.get("pubYear")
    try:
        year: Optional[int] = int(year_raw) if year_raw is not None else None
    except (TypeError, ValueError):
        year = None

    open_access_url = _open_access_url(record)

    if doi:
        doi_or_id = doi
        landing_page_url = f"https://doi.org/{doi}"
    else:
        doi_or_id = f"epmc:{epmc_id}"
        landing_page_url = f"https://europepmc.org/article/PPR/{epmc_id}"

    # bioRxiv content is always a preprint by definition; medRxiv too.
    # Even if a later journal version exists, the record we're handling
    # is the preprint, so flag accordingly.
    status = PublicationStatus.PREPRINT

    identifiers = Identifiers(doi=doi, europepmc_id=epmc_id)
    audit = Audit(primary_source=True, aggregator=False, warn_marker=False)
    # Preprint records have a journal name (always "bioRxiv" or
    # "medRxiv") and a pubYear, but no formal volume/issue/page numbers.
    # Surface the name only.
    venue: Optional[Venue] = None
    if journal:
        venue = Venue(name=journal)
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
        journal_or_volume=journal or None,
        doi_or_id=doi_or_id,
        source="biorxiv",
        open_access_url=open_access_url,  # type: ignore[arg-type]
        landing_page_url=landing_page_url,  # type: ignore[arg-type]
        language="en",  # bioRxiv/medRxiv are English-only in practice
        abstract=abstract,
        publication_status=status,
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
    """Build an Europe PMC search-parameter list.

    ``SRC:PPR`` restricts to preprints; we then filter to bioRxiv +
    medRxiv client-side via ``journalTitle``. Year filtering uses
    ``PUB_YEAR:[lo TO hi]`` in Lucene-style.
    """
    parts = [f"SRC:PPR", f"({query})"]
    if year_from is not None or year_to is not None:
        lo = str(year_from) if year_from is not None else "1900"
        hi = str(year_to) if year_to is not None else "2099"
        parts.append(f"PUB_YEAR:[{lo} TO {hi}]")
    epmc_query = " AND ".join(parts)
    return [
        ("query", epmc_query),
        ("resulttype", "core"),
        ("format", "json"),
        # ``pageSize`` is clamped 1-100 by Europe PMC; we send a bit
        # extra to leave headroom for client-side journalTitle filtering.
        ("pageSize", str(max(1, min(max_results * 2, 100)))),
    ]


async def search_biorxiv_impl(
    query: str,
    max_results: int = 10,
    include_medrxiv: bool = True,
    year_from: Optional[int] = None,
    year_to: Optional[int] = None,
    *,
    client: Optional[httpx.AsyncClient] = None,
) -> list[DAOPaper]:
    """Search bioRxiv (+ optionally medRxiv) preprints via Europe PMC.

    ``client`` is injectable for tests.
    """
    params = _build_params(query, max_results, year_from, year_to)
    log.info("biorxiv.search query=%r include_medrxiv=%s", params[0][1], include_medrxiv)

    async def _run(c: httpx.AsyncClient) -> list[DAOPaper]:
        headers = {"User-Agent": _USER_AGENT, "Accept": "application/json"}
        r = await c.get(EUROPEPMC_API, params=params, headers=headers, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        data = r.json()
        # Europe PMC nests results under ``resultList.result``.
        result_list = (data.get("resultList") or {}).get("result") or []
        log.info("biorxiv.search raw_hits=%d total=%s",
                 len(result_list), data.get("hitCount"))

        papers: list[DAOPaper] = []
        for record in result_list:
            if not isinstance(record, dict):
                continue
            journal = record.get("journalTitle") or ""
            if not _journal_matches(journal, include_medrxiv):
                continue
            paper = _record_to_paper(record)
            if paper is not None:
                papers.append(paper)
            if len(papers) >= max_results:
                break
        log.info("biorxiv.search filtered_hits=%d", len(papers))
        return papers

    if client is not None:
        return await _run(client)
    async with httpx.AsyncClient() as c:
        return await _run(c)


def register(mcp: FastMCP) -> None:
    """Register the ``search_biorxiv`` tool."""

    @mcp.tool()
    async def search_biorxiv(
        query: str,
        max_results: int = 10,
        include_medrxiv: bool = True,
        year_from: Optional[int] = None,
        year_to: Optional[int] = None,
    ) -> list[DAOPaper]:
        """Search bioRxiv (and optionally medRxiv) preprints. Strongest
        for ancient-DNA / paleogenomic / bioarchaeology preprints that
        sit on the preprint server for 6–12 months before journal
        publication — the Levant aDNA literature (Lazaridis, Feldman,
        Harney, Reich Lab) is the canonical use case.

        Backend note: bioRxiv has no native free-text search API; this
        tool queries Europe PMC (``SRC:PPR``) and filters results to
        bioRxiv + medRxiv content client-side via ``journalTitle``.

        Every bioRxiv/medRxiv record carries a DOI (form
        ``10.1101/...``) from day one of submission, so inline citations
        almost always use the Author-Year form against ``doi.org``.
        ``publication_status`` is always ``PREPRINT``.

        Citation rendering (Schema v2): each returned ``DAOPaper``
        carries an ``inline_citation`` block. Copy
        ``inline_citation.markdown`` verbatim for in-text citations —
        do not reformat. For the bibliography / reference-list section
        copy ``inline_citation.authoritative_bibliography_line``
        verbatim; if it is ``None`` (venue metadata incomplete) fall
        back to Author-Year + URL/DOI rather than reconstructing the
        reference line from training knowledge.

        Args:
            query: free-text search (Lucene-style operators ``AND``/
                ``OR``/``NOT`` work via Europe PMC).
            max_results: 1-50 (the upstream page is doubled internally
                to leave headroom for the bioRxiv/medRxiv filter).
            include_medrxiv: include medRxiv preprints (default True).
                Set False to restrict to bioRxiv only.
            year_from: lower bound publication year, inclusive.
            year_to: upper bound publication year, inclusive.
        """
        return await search_biorxiv_impl(
            query=query,
            max_results=max_results,
            include_medrxiv=include_medrxiv,
            year_from=year_from,
            year_to=year_to,
        )
