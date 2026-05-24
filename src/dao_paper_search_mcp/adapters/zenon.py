"""Zenon DAI adapter.

Endpoint
--------
The ``/SRU`` endpoint advertised on older DAI documentation pages returns
404 on the live instance (probed 2026-05-15). The current public API is
the VuFind REST API at ``https://zenon.dainst.org/api/v1/search``, which
returns JSON with rich bibliographic fields (authors, languages,
publication dates, gazetteer cross-links).

We use the REST API exclusively. There is no SRU fallback because SRU is
not deployed; falling back to a non-existent endpoint would only obscure
errors.

Query shape
-----------
Required:
    lookfor=<query>     keyword search

Optional filters (all repeatable via ``filter[]=...``):
    publishDate:[YYYY TO YYYY]
    language:"English"        # full language name, not ISO-639-1

We do NOT call paper-search internally — see architecture principle #2.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

from ..inline_citation import build_inline_citation
from ..models import Audit, DAOPaper, Identifiers, PublicationStatus, Venue
from ..resolvers.gazetteer import site_id_tokens_from_zenon_record
from ..utils import HttpxParams
from ..utils.doi import normalize_doi

log = logging.getLogger(__name__)

ZENON_API = "https://zenon.dainst.org/api/v1/search"
ZENON_RECORD_URL = "https://zenon.dainst.org/Record/{record_id}"
HTTP_TIMEOUT = 15.0

# Mapping ISO-639-1 -> Zenon language facet values.
# Zenon's facet uses English language names. We accept ISO-639-1 from
# callers so the tool signature is portable.
_LANG_FACET: dict[str, str] = {
    "de": "German",
    "en": "English",
    "fr": "French",
    "it": "Italian",
    "he": "Hebrew",
    "ar": "Arabic",
    "es": "Spanish",
    "tr": "Turkish",
    "el": "Greek",
}

# Reverse mapping for output: Zenon "English" -> ISO "en".
_LANG_ISO: dict[str, str] = {v.lower(): k for k, v in _LANG_FACET.items()}


def _first_int(values: Sequence[Any]) -> int | None:
    """Pick the first parseable year out of a list like ['1994', '1995?']."""
    for v in values:
        s = str(v).strip()
        # Tolerate things like "1994?" or "[1994]" by extracting 4 digits.
        digits = "".join(c for c in s if c.isdigit())[:4]
        if len(digits) == 4:
            try:
                return int(digits)
            except ValueError:
                continue
    return None


def _detect_language(record: Mapping[str, Any]) -> str:
    """Return an ISO-639-1 code or 'und'."""
    langs = record.get("languages") or []
    if not langs:
        return "und"
    first = str(langs[0]).strip().lower()
    return _LANG_ISO.get(first, "und")


def _flatten_authors(record: Mapping[str, Any]) -> list[str]:
    """Combine primary + secondary author names in original order, deduplicated.

    Corporate authors are intentionally omitted from the ``authors`` list
    to keep it human-author-only; agents can fetch them via the raw record
    if needed.
    """
    seen: set[str] = set()
    out: list[str] = []
    for key in ("primaryAuthorsNames", "secondaryAuthorsNames"):
        for name in record.get(key, []) or []:
            n = str(name).strip().rstrip(".,")
            if n and n not in seen:
                seen.add(n)
                out.append(n)
    return out


def _series_or_journal(record: Mapping[str, Any]) -> str | None:
    """Render the series / volume label, falling back to nothing.

    Zenon journal articles often arrive with ``series=[{name, number}]``
    where ``name`` is the journal title. For monographs the series block
    is the publisher series. Either is the closest thing to a
    ``journal_or_volume`` label downstream consumers expect.
    """
    series = record.get("series") or []
    if not series:
        return None
    s0 = series[0]
    name = (s0.get("name") or "").strip().rstrip(",;:")
    number = (s0.get("number") or "").strip()
    if name and number:
        return f"{name} {number}"
    return name or number or None


def _build_landing_url(record: Mapping[str, Any]) -> str | None:
    rid = record.get("id")
    if not rid:
        return None
    return ZENON_RECORD_URL.format(record_id=rid)


def _build_open_access_url(record: Mapping[str, Any]) -> str | None:
    """Pick the first record-supplied URL that looks externally resolvable."""
    for url in record.get("urls") or []:
        if isinstance(url, dict):
            href = url.get("url") or url.get("href")
        else:
            href = url
        if href and str(href).startswith(("http://", "https://")):
            return str(href)
    return None


_DOI_RE = re.compile(r"10\.\d{4,9}/[^\s\"<>]+")


def _extract_doi(record: Mapping[str, Any]) -> str | None:
    """Scan a Zenon record for an external DOI.

    Zenon's VuFind API surfaces DOIs in two places: occasionally as a
    top-level ``DOI``/``doi`` field, and more often embedded in the
    ``urls`` list (e.g. ``https://doi.org/10.1163/...``). We accept either.
    """
    for key in ("DOI", "doi"):
        val = record.get(key)
        if val:
            s = str(val).strip()
            s = s.removeprefix("https://doi.org/").removeprefix("http://doi.org/")
            m = _DOI_RE.search(s)
            if m:
                return normalize_doi(m.group(0).rstrip(".,;)"))
    for url in record.get("urls") or []:
        if isinstance(url, dict):
            href = url.get("url") or url.get("href") or ""
        else:
            href = str(url)
        m = _DOI_RE.search(href)
        if m:
            return normalize_doi(m.group(0).rstrip(".,;)"))
    return None


def _record_to_paper(record: Mapping[str, Any]) -> DAOPaper:
    title = (record.get("title") or "").strip().rstrip(":/ ")
    subtitle = (record.get("subTitle") or "").strip().rstrip(":/ ")
    if subtitle and subtitle.lower() not in title.lower():
        full_title = f"{title}: {subtitle}" if title else subtitle
    else:
        full_title = title or "(untitled)"

    authors = _flatten_authors(record)
    year = _first_int(record.get("publicationDates") or [])
    zenon_id = str(record.get("id")) if record.get("id") is not None else None
    open_access_url = _build_open_access_url(record)
    landing_page_url = _build_landing_url(record)
    identifiers = Identifiers(doi=_extract_doi(record), zenon_id=zenon_id)
    audit = Audit(primary_source=True, aggregator=False, warn_marker=False)
    # Zenon series block carries the closest thing to a journal name +
    # volume — surface it as Venue.name + Venue.volume. Issue and pages
    # aren't structurally exposed in the VuFind API response.
    series = record.get("series") or []
    venue: Venue | None = None
    if series:
        s0 = series[0]
        v_name = (s0.get("name") or "").strip().rstrip(",;:") or None
        v_volume = (s0.get("number") or "").strip() or None
        if v_name or v_volume:
            venue = Venue(name=v_name, volume=v_volume)
    inline_citation = build_inline_citation(
        authors=authors,
        year=year,
        pages=None,
        title=full_title,
        identifiers=identifiers,
        landing_page_url=landing_page_url,
        open_access_url=open_access_url,
        audit=audit,
        venue=venue,
    )

    return DAOPaper(
        title=full_title,
        authors=authors,
        year=year,
        journal_or_volume=_series_or_journal(record),
        doi_or_id=f"zenon:{record.get('id')}",
        source="zenon",
        open_access_url=open_access_url,  # type: ignore[arg-type]
        landing_page_url=landing_page_url,  # type: ignore[arg-type]
        language=_detect_language(record),
        publication_status=PublicationStatus.PUBLISHED,
        site_ids=site_id_tokens_from_zenon_record(record),
        identifiers=identifiers,
        audit=audit,
        inline_citation=inline_citation,
    )


def _build_params(
    query: str,
    max_results: int,
    language: str | None,
    year_from: int | None,
    year_to: int | None,
) -> HttpxParams:
    """Build an httpx params list. Tuples instead of dict because Zenon
    expects repeated ``filter[]=...`` keys."""
    params: HttpxParams = [
        ("lookfor", query),
        ("limit", str(max(1, min(max_results, 50)))),
    ]
    if language:
        lang_value = _LANG_FACET.get(language.lower())
        if lang_value:
            params.append(("filter[]", f'language:"{lang_value}"'))
    if year_from is not None or year_to is not None:
        lo = year_from if year_from is not None else 1500
        hi = year_to if year_to is not None else 2100
        params.append(("filter[]", f"publishDate:[{lo} TO {hi}]"))
    return params


async def search_zenon_impl(
    query: str,
    max_results: int = 10,
    language: str | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    *,
    client: httpx.AsyncClient | None = None,
) -> list[DAOPaper]:
    """Search Zenon DAI via the REST API.

    ``client`` is injectable for tests. Production callers should leave it
    None so each call gets a fresh client (Zenon is not high-volume in
    this use case).
    """
    params = _build_params(query, max_results, language, year_from, year_to)
    log.info("zenon.search query=%r filters=%s", query, params[2:])

    async def _run(c: httpx.AsyncClient) -> list[DAOPaper]:
        r = await c.get(ZENON_API, params=params, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        data = r.json()
        records = data.get("records") or []
        log.info("zenon.search hits=%d total=%s", len(records), data.get("resultCount"))
        return [_record_to_paper(rec) for rec in records]

    if client is not None:
        return await _run(client)
    async with httpx.AsyncClient() as c:
        return await _run(c)


def register(mcp: FastMCP) -> None:
    """Register the ``search_zenon`` tool on the given FastMCP server."""

    @mcp.tool()
    async def search_zenon(
        query: str,
        max_results: int = 10,
        language: str | None = None,
        year_from: int | None = None,
        year_to: int | None = None,
    ) -> list[DAOPaper]:
        """Search the Zenon DAI catalog — the German Archaeological Institute's
        bibliography (~1M records, multilingual). Best for German-language Levant
        archaeology, classical antiquity, and DAI publication series.

        Citation rendering (Schema v2): each returned ``DAOPaper``
        carries an ``inline_citation`` block. Copy
        ``inline_citation.markdown`` verbatim for in-text citations —
        do not reformat. For the bibliography / reference-list section
        copy ``inline_citation.authoritative_bibliography_line``
        verbatim; if it is ``None`` (venue metadata incomplete) fall
        back to Author-Year + URL/DOI rather than reconstructing the
        reference line from training knowledge.

        Args:
            query: free-text keyword query (CQL-style boolean operators
                ``AND``/``OR``/``NOT`` are honored by the upstream search).
            max_results: 1-50.
            language: ISO-639-1 code (``"de"`` | ``"en"`` | ``"fr"`` | ``"he"``
                | ``"ar"`` | ...). Filtered server-side.
            year_from: lower bound publication year, inclusive.
            year_to: upper bound publication year, inclusive.
        """
        return await search_zenon_impl(
            query=query,
            max_results=max_results,
            language=language,
            year_from=year_from,
            year_to=year_to,
        )
