"""ADAJ adapter — Department of Antiquities of Jordan publications portal.

The DoA Publication Archive at ``publication.doa.gov.jo`` indexes ADAJ
(Annual of the Department of Antiquities of Jordan), SHAJ (Studies in
the History and Archaeology of Jordan), Munjazat, JERD, Athar, and other
DoA series. Probed 2026-05-15: ``/Publications/Search?SearchTerm=<q>``
returns clean server-rendered HTML with ``.search-result`` blocks
carrying title, author, year, publication, and PDF download link.

Unlike IAA, this site is fully scrapeable today.

Year filtering: the upstream GET search ignores ``Year=`` params and the
``/Publications/AdvancedSearch`` endpoint is POST-only. We therefore
apply year-range filtering client-side after parsing. This is documented
in the tool docstring so the agent knows the filter is a post-filter.
"""

from __future__ import annotations

import logging
import re

import httpx
from bs4 import BeautifulSoup, Tag
from mcp.server.fastmcp import FastMCP

from ..inline_citation import build_inline_citation
from ..models import Audit, DAOPaper, Identifiers, PublicationStatus, Venue

log = logging.getLogger(__name__)

ADAJ_BASE = "https://publication.doa.gov.jo"
ADAJ_SEARCH = f"{ADAJ_BASE}/Publications/Search"
HTTP_TIMEOUT = 30.0


def _absolute_url(href: str) -> str | None:
    if not href:
        return None
    if href.startswith(("http://", "https://")):
        return href
    if href.startswith("/"):
        return ADAJ_BASE + href
    return None


def _extract_text(tag: Tag | None, default: str = "") -> str:
    return tag.get_text(" ", strip=True) if tag else default


def _parse_id_from_url(url: str) -> str | None:
    """Pull the numeric chapter/publication ID out of an ADAJ URL.

    ``/Publications/ViewChapterPublic/212`` -> ``"chapter:212"``
    ``/Publications/ViewPublic/25``         -> ``"publication:25"``
    """
    m = re.search(r"/ViewChapterPublic/(\d+)", url)
    if m:
        return f"chapter:{m.group(1)}"
    m = re.search(r"/ViewPublic/(\d+)", url)
    if m:
        return f"publication:{m.group(1)}"
    return None


def _parse_result_tag(tag: Tag) -> DAOPaper | None:
    title_anchor = tag.find("a", class_="search-result-title")
    if not isinstance(title_anchor, Tag):
        return None
    title = title_anchor.get_text(" ", strip=True)
    if not title:
        return None
    href = title_anchor.get("href")
    landing = _absolute_url(href) if isinstance(href, str) else None

    year_text = _extract_text(tag.find(class_="search-result-year"))
    year: int | None = None
    if year_text:
        m = re.search(r"\b(1[89]\d{2}|20\d{2}|21\d{2})\b", year_text)
        if m:
            year = int(m.group(1))

    # ADAJ can have multiple author anchors — collect them all.
    authors = [_extract_text(a) for a in tag.find_all("a", class_="search-result-author") if _extract_text(a)]

    journal_raw = _extract_text(tag.find(class_="search-result-publication"))
    # The publication anchor inlines an italic "(SHAJ)" or "(ADAJ)" suffix —
    # the text-stripping above already preserves it on one line.
    journal: str | None = re.sub(r"\s+", " ", journal_raw).strip("/ ").strip() or None

    # Download URL points at the chapter PDF (open access in DoA archive).
    download = tag.find("a", class_="search-result-download")
    pdf_url: str | None = None
    if isinstance(download, Tag):
        dl_href = download.get("href")
        if isinstance(dl_href, str):
            pdf_url = _absolute_url(dl_href)

    pages_text = _extract_text(tag.find(class_="search-result-pages-count"))
    m = re.search(r"(\d+)\s+page", pages_text)
    pages = m.group(1) if m else None

    pub_type = _extract_text(tag.find(class_="search-result-publication-type"))

    # Stable ID: prefer the chapter URL, fall back to the title.
    chapter_id = _parse_id_from_url(landing or "") if landing else None
    doi_or_id = f"adaj:{chapter_id}" if chapter_id else f"adaj:{title[:48]}"

    note: str | None = None
    if pub_type and pub_type.lower() not in ("article", "chapter", "paper"):
        note = f"publication_type={pub_type}"

    identifiers = Identifiers(adaj_id=chapter_id)
    audit = Audit(
        primary_source=True,
        aggregator=False,
        verification_note=note,
        warn_marker=bool(note),
    )
    # The DoA portal exposes the parent publication as a single string
    # ("Annual of the Department of Antiquities of Jordan, vol. 56" or
    # "SHAJ XIV"). Parse a trailing volume token if present; otherwise
    # leave volume unset and use the full string as Venue.name.
    venue: Venue | None = None
    if journal:
        v_name = journal
        v_volume: str | None = None
        m_vol = re.search(r"(?:vol\.?\s*|volume\s+)([A-Za-z0-9]+)$", journal, re.I)
        if m_vol:
            v_volume = m_vol.group(1)
            v_name = journal[: m_vol.start()].rstrip(" ,.")
        venue = Venue(name=v_name or None, volume=v_volume, pages=pages)
    inline_citation = build_inline_citation(
        authors=authors,
        year=year,
        pages=pages,
        title=title,
        identifiers=identifiers,
        landing_page_url=landing,
        open_access_url=pdf_url,
        audit=audit,
        venue=venue,
    )

    return DAOPaper(
        title=title,
        authors=authors,
        year=year,
        journal_or_volume=journal,
        pages=pages,
        doi_or_id=doi_or_id,
        source="adaj",
        open_access_url=pdf_url,  # type: ignore[arg-type]
        landing_page_url=landing,  # type: ignore[arg-type]
        language="en",  # DoA archive is English-only in this portal
        publication_status=PublicationStatus.PUBLISHED,
        verification_note=note,
        identifiers=identifiers,
        audit=audit,
        inline_citation=inline_citation,
    )


def _parse_results(html: str) -> list[DAOPaper]:
    soup = BeautifulSoup(html, "lxml")
    blocks = soup.find_all(class_="search-result")
    out: list[DAOPaper] = []
    for b in blocks:
        if not isinstance(b, Tag):
            continue
        paper = _parse_result_tag(b)
        if paper is not None:
            out.append(paper)
    return out


def _filter_by_year(
    papers: list[DAOPaper],
    year_from: int | None,
    year_to: int | None,
) -> list[DAOPaper]:
    """Client-side year filter — see module docstring."""
    if year_from is None and year_to is None:
        return papers
    lo = year_from if year_from is not None else 0
    hi = year_to if year_to is not None else 9999
    return [p for p in papers if p.year is not None and lo <= p.year <= hi]


async def search_adaj_impl(
    query: str,
    max_results: int = 10,
    year_from: int | None = None,
    year_to: int | None = None,
    *,
    client: httpx.AsyncClient | None = None,
) -> list[DAOPaper]:
    """Search the DoA publication archive (ADAJ + SHAJ + others)."""
    log.info("adaj.search query=%r year_from=%s year_to=%s", query, year_from, year_to)

    params = {"SearchTerm": query}
    headers = {
        "User-Agent": "dao-paper-search-mcp/0.1 (https://github.com/patrick-leiverkus/dao-paper-search-mcp)",
        "Accept": "text/html",
    }

    async def _run(c: httpx.AsyncClient) -> list[DAOPaper]:
        r = await c.get(ADAJ_SEARCH, params=params, headers=headers, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        papers = _parse_results(r.text)
        papers = _filter_by_year(papers, year_from, year_to)
        log.info("adaj.search hits=%d (after year filter)", len(papers))
        return papers[:max_results]

    if client is not None:
        return await _run(client)
    async with httpx.AsyncClient() as c:
        return await _run(c)


def register(mcp: FastMCP) -> None:
    """Register the ``search_adaj`` tool."""

    @mcp.tool()
    async def search_adaj(
        query: str,
        max_results: int = 10,
        year_from: int | None = None,
        year_to: int | None = None,
    ) -> list[DAOPaper]:
        """Search the DoA Publication Archive — ADAJ (Annual of the Department
        of Antiquities of Jordan), SHAJ (Studies in the History and Archaeology
        of Jordan), Munjazat, JERD, Athar, and other DoA series. English-language,
        primary source for Jordanian excavation reports.

        Note: ``year_from``/``year_to`` are applied client-side after retrieval
        (the upstream GET search ignores year parameters). Narrow your query
        keywords if year filtering yields too few hits.

        Citation rendering (Schema v2): each returned ``DAOPaper``
        carries an ``inline_citation`` block. Copy
        ``inline_citation.markdown`` verbatim for in-text citations —
        do not reformat. For the bibliography / reference-list section
        copy ``inline_citation.authoritative_bibliography_line``
        verbatim; if it is ``None`` (venue metadata incomplete) fall
        back to Author-Year + URL/DOI rather than reconstructing the
        reference line from training knowledge.

        Args:
            query: free-text query (e.g. ``"Negev fortresses"``).
            max_results: 1-50.
            year_from: lower bound publication year, inclusive.
            year_to: upper bound publication year, inclusive.
        """
        return await search_adaj_impl(
            query=query,
            max_results=max_results,
            year_from=year_from,
            year_to=year_to,
        )
