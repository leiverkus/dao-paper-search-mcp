"""Zenodo adapter.

Endpoint
--------
``https://zenodo.org/api/records`` returns JSON. Zenodo is the CERN-
operated open research repository. Every record receives a DOI on
deposit (form ``10.5281/zenodo.<int>``), so the inline-citation
builder always picks ``https://doi.org/{doi}`` as ``primary_url``.

Resource types
--------------
Zenodo indexes more than journal articles: datasets, software,
presentations, posters, theses, lessons. Non-article types are
flagged via ``audit.verification_note`` (``"resource_type=dataset"``)
so the agent can decide whether to treat the hit as a citable paper
or as supporting material.

No internal calls to other adapters. Architecture principle #2.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Mapping, Optional

import httpx
from mcp.server.fastmcp import FastMCP

from ..inline_citation import build_inline_citation
from ..models import Audit, DAOPaper, Identifiers, PublicationStatus, Venue
from ..utils.contact import CONTACT_EMAIL
from ..utils.doi import normalize_doi

log = logging.getLogger(__name__)

ZENODO_API = "https://zenodo.org/api/records"
HTTP_TIMEOUT = 30.0

_USER_AGENT = (
    "dao-paper-search-mcp/0.1 "
    "(+https://github.com/leiverkus/dao-paper-search-mcp; "
    f"mailto:{CONTACT_EMAIL})"
)

# Resource-type values that map cleanly to "journal article" semantics.
# Everything else gets a verification_note so the agent can weigh it.
_ARTICLE_TYPES = {
    "publication-article",
    "publication-journalarticle",
    "publication-conferencepaper",
    "publication-book",
    "publication-bookchapter",
}

# Resource-type values that look like preprints (Zenodo has separate
# subtypes for preprints and working papers).
_PREPRINT_TYPES = {
    "publication-preprint",
    "publication-workingpaper",
}

# Strip HTML tags from Zenodo descriptions before exposing them as the
# DAOPaper.abstract field. Zenodo stores rich-text in description but
# our schema wants plain text.
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_HTML_WS_RE = re.compile(r"\s+")


def _strip_html(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    stripped = _HTML_TAG_RE.sub("", text)
    return _HTML_WS_RE.sub(" ", stripped).strip() or None


def _format_authors(metadata: Mapping[str, Any]) -> list[str]:
    """Zenodo lists creators in ``"Family, Given"`` form already.

    Each creator is ``{"name": "Family, Given", "affiliation": "...",
    "orcid": "..."}``. We preserve the family-first convention because
    it's exactly what the Author-Year builder expects.
    """
    out: list[str] = []
    for c in metadata.get("creators") or []:
        if not isinstance(c, dict):
            continue
        name = (c.get("name") or "").strip()
        if name:
            out.append(name)
    return out


def _extract_year(metadata: Mapping[str, Any]) -> Optional[int]:
    """Zenodo's ``publication_date`` is ``YYYY-MM-DD`` or ``YYYY``."""
    date = (metadata.get("publication_date") or "").strip()
    if len(date) >= 4 and date[:4].isdigit():
        return int(date[:4])
    return None


def _resource_type_key(metadata: Mapping[str, Any]) -> Optional[str]:
    """Compose Zenodo's resource_type into a canonical key string.

    Zenodo serialises ``resource_type`` as ``{"type": "publication",
    "subtype": "article"}``. We join the two into ``"publication-article"``
    so the membership check against ``_ARTICLE_TYPES`` works directly.
    """
    rt = metadata.get("resource_type") or {}
    if not isinstance(rt, dict):
        return None
    top = (rt.get("type") or "").strip().lower()
    sub = (rt.get("subtype") or "").strip().lower()
    if top and sub:
        return f"{top}-{sub}"
    return top or None


def _open_access_url(record: Mapping[str, Any]) -> Optional[str]:
    """Pick the first file's download URL when access is open."""
    files = record.get("files") or []
    if not isinstance(files, list):
        return None
    for f in files:
        if not isinstance(f, dict):
            continue
        links = f.get("links") or {}
        url = (links.get("self") or "").strip() if isinstance(links, dict) else ""
        if url:
            return url
    return None


def _publication_status(rt_key: Optional[str]) -> PublicationStatus:
    if rt_key in _PREPRINT_TYPES:
        return PublicationStatus.PREPRINT
    return PublicationStatus.PUBLISHED


def _verification_note(rt_key: Optional[str]) -> Optional[str]:
    """Flag non-article resource types so the agent can rank them."""
    if rt_key and rt_key not in _ARTICLE_TYPES:
        return f"resource_type={rt_key}"
    return None


def _record_to_paper(record: Mapping[str, Any]) -> Optional[DAOPaper]:
    metadata = record.get("metadata") or {}
    if not isinstance(metadata, dict):
        return None

    doi = normalize_doi(record.get("doi") or metadata.get("doi"))
    if not doi:
        # Zenodo always assigns a DOI; missing one means the record is
        # malformed or pre-deposit. Drop — no fabricated anchor.
        return None

    title = (metadata.get("title") or "").strip() or "(untitled)"
    authors = _format_authors(metadata)
    year = _extract_year(metadata)
    abstract = _strip_html(metadata.get("description"))
    rt_key = _resource_type_key(metadata)
    open_access_url = _open_access_url(record)
    language = (metadata.get("language") or "und").strip().lower() or "und"

    verification_note = _verification_note(rt_key)
    landing_page_url = f"https://doi.org/{doi}"

    identifiers = Identifiers(doi=doi)
    audit = Audit(
        primary_source=True,
        aggregator=False,
        verification_note=verification_note,
        # Non-article resource types are surfaced but not warn-marked —
        # a dataset DOI is a legitimate citation target, just not a
        # journal article. The verification_note is the hint.
        warn_marker=False,
    )
    # Zenodo exposes journal metadata under ``metadata.journal``.
    journal_meta = metadata.get("journal") or {}
    venue: Optional[Venue] = None
    if isinstance(journal_meta, dict):
        v_name = (journal_meta.get("title") or "").strip() or None
        v_volume = (journal_meta.get("volume") or "").strip() or None
        v_issue = (journal_meta.get("issue") or "").strip() or None
        v_pages = (journal_meta.get("pages") or "").strip() or None
        if any((v_name, v_volume, v_issue, v_pages)):
            venue = Venue(name=v_name, volume=v_volume, issue=v_issue, pages=v_pages)
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
        doi_or_id=doi,
        source="zenodo",
        open_access_url=open_access_url,  # type: ignore[arg-type]
        landing_page_url=landing_page_url,  # type: ignore[arg-type]
        language=language,
        abstract=abstract,
        publication_status=_publication_status(rt_key),
        verification_note=verification_note,
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
    """Build a Zenodo ``/records`` parameter list.

    Year filtering uses Elasticsearch range syntax in the ``q``
    parameter because Zenodo doesn't expose a dedicated year filter:
    ``q="my query" AND year:[2010 TO 2020]``.
    """
    q = query
    if year_from is not None or year_to is not None:
        lo = str(year_from) if year_from is not None else "*"
        hi = str(year_to) if year_to is not None else "*"
        q = f"{q} AND year:[{lo} TO {hi}]"
    return [
        ("q", q),
        ("size", str(max(1, min(max_results, 100)))),
        ("page", "1"),
    ]


async def search_zenodo_impl(
    query: str,
    max_results: int = 10,
    year_from: Optional[int] = None,
    year_to: Optional[int] = None,
    *,
    client: Optional[httpx.AsyncClient] = None,
) -> list[DAOPaper]:
    """Search Zenodo. ``client`` is injectable for tests."""
    params = _build_params(query, max_results, year_from, year_to)
    log.info("zenodo.search query=%r", params[0][1])

    async def _run(c: httpx.AsyncClient) -> list[DAOPaper]:
        headers = {"User-Agent": _USER_AGENT, "Accept": "application/json"}
        r = await c.get(ZENODO_API, params=params, headers=headers, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        data = r.json()
        # Zenodo wraps results in {"hits": {"total": N, "hits": [...]}}.
        hits = ((data.get("hits") or {}).get("hits")) or []
        log.info("zenodo.search hits=%d total=%s", len(hits),
                 (data.get("hits") or {}).get("total"))
        papers = [_record_to_paper(rec) for rec in hits]
        return [p for p in papers if p is not None]

    if client is not None:
        return await _run(client)
    async with httpx.AsyncClient() as c:
        return await _run(c)


def register(mcp: FastMCP) -> None:
    """Register the ``search_zenodo`` tool."""

    @mcp.tool()
    async def search_zenodo(
        query: str,
        max_results: int = 10,
        year_from: Optional[int] = None,
        year_to: Optional[int] = None,
    ) -> list[DAOPaper]:
        """Search Zenodo — the CERN-operated open research repository.
        Strongest for: research data, software releases, preprints,
        Digital-Humanities toolchains, and OA versions of papers that
        the authors uploaded for compliance with funder mandates.

        Every Zenodo record carries a DOI (form
        ``10.5281/zenodo.<int>``), so inline citations always use the
        Author-Year form against ``doi.org``.

        Non-article resource types (dataset, software, presentation,
        poster, thesis) are surfaced via
        ``audit.verification_note=resource_type=…`` so the agent can
        decide whether to treat the hit as a citable paper or as
        supporting material.

        Citation rendering (Schema v2): each returned ``DAOPaper``
        carries an ``inline_citation`` block. Copy
        ``inline_citation.markdown`` verbatim for in-text citations —
        do not reformat. For the bibliography / reference-list section
        copy ``inline_citation.authoritative_bibliography_line``
        verbatim; if it is ``None`` (venue metadata incomplete) fall
        back to Author-Year + URL/DOI rather than reconstructing the
        reference line from training knowledge.

        Args:
            query: free-text search.
            max_results: 1-100.
            year_from: lower bound publication year, inclusive.
            year_to: upper bound publication year, inclusive.
        """
        return await search_zenodo_impl(
            query=query,
            max_results=max_results,
            year_from=year_from,
            year_to=year_to,
        )
