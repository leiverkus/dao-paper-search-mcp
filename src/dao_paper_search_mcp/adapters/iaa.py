"""IAA Publications adapter — OAI-PMH backend.

Why OAI-PMH (rewrite of 2026-05-15)
-----------------------------------
The original MVP scraped ``/do/search/`` and detected JS-only rendering
via the empty-``#results-list`` tripwire. The reverse-engineered Solr
endpoint (``/do/search/results/json``) was found to be a stale 2019
artefact — BePress migrated the route without updating the JS bundle.

Sondierung 2026-05-15 (see ``docs/2026-05-15-iaa-solr-probe.md``)
discovered that **OAI-PMH at ``/do/oai/`` works perfectly**:
``Identify`` / ``ListSets`` / ``ListMetadataFormats`` / ``ListRecords``
all respond cleanly, 100 records per page with resumption tokens,
earliest datestamp 2000-01-19, full Dublin Core metadata. And IAA
**does register DOIs** (DataCite prefix ``10.70967/``) — they're
embedded in ``dc:identifier`` as ``info:doi/...``.

So this adapter:

1. Calls ``verb=ListRecords&metadataPrefix=oai_dc`` with optional
   ``set=`` (collection filter) and ``from=``/``until=`` (year range).
2. Paginates via ``resumptionToken`` (max 20 pages = ~2k records per
   query as a safety cap).
3. Parses Dublin Core XML into ``DAOPaper``.
4. Filters client-side: every query token must appear in title +
   description + subject. AND-of-tokens, case-insensitive.
5. Returns up to ``max_results`` matches.

No more ``IAAUnavailableError``; the MVP-incomplete asterisk is gone.

Stable, documented protocol (OAI-PMH 2.0, 2002); no HTML brittleness;
DOIs surface directly. The dedicated full-text Solr search would still
be nicer, but it's not on offer and OAI-PMH is a structurally cleaner
substitute.
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

IAA_OAI = "https://publications.iaa.org.il/do/oai/"
HTTP_TIMEOUT = 30.0

_USER_AGENT = (
    "dao-paper-search-mcp/0.1 "
    "(+https://github.com/leiverkus/dao-paper-search-mcp; "
    "mailto:patrick.leiverkus@uni-oldenburg.de)"
)

# Safety cap on pagination: 20 pages × 100 records = ~2000 records per
# query. A keyword filter typically narrows that to <50 hits. If a user
# hits the cap with no matches, the docstring tells them to narrow the
# year range.
_MAX_PAGES = 20

# Friendly collection names → OAI setSpec. Pass-through is also
# accepted for sets we haven't catalogued here yet.
_COLLECTIONS: dict[str, str] = {
    "atiqot": "publication:atiqot",
    "ha-esi": "publication:ha-esi",
    "ha-esi-bilingual": "publication:ha_esi_bilingual_series",
    "ha-hebrew": "publication:ha_hebrew_series",
    "esi-english": "publication:esi_english_series",
    "iaa-books": "publication:iaabookseries",
    "favissa": "publication:favissa",
    "cornerstone": "publication:cornerstone",
}

# Atom / Dublin Core namespaces. We pass these to findall().
_NS = {
    "oai": "http://www.openarchives.org/OAI/2.0/",
    "oai_dc": "http://www.openarchives.org/OAI/2.0/oai_dc/",
    "dc": "http://purl.org/dc/elements/1.1/",
}


def _resolve_set_spec(collection: Optional[str]) -> Optional[str]:
    """Translate a user-facing collection name to an OAI setSpec.

    ``None`` means "no set filter — query all collections". Friendly
    names from ``_COLLECTIONS`` are mapped; any other string is passed
    through, with ``publication:`` prepended if missing — so callers
    can use either ``"atiqot"`` or ``"publication:atiqot"``.
    """
    if collection is None:
        return None
    s = collection.strip()
    if not s:
        return None
    if s in _COLLECTIONS:
        return _COLLECTIONS[s]
    return s if s.startswith("publication:") else f"publication:{s}"


def _detect_language(text: str) -> str:
    """Hebrew if Hebrew code points present, English if Latin letters,
    'und' otherwise. Mirrors the old adapter's heuristic — IAA never
    surfaces a structured language field via OAI-DC."""
    if any("֐" <= ch <= "׿" for ch in text):
        return "he"
    if re.search(r"[A-Za-z]{4,}", text):
        return "en"
    return "und"


def _strip_doi_prefix(raw: str) -> Optional[str]:
    """``info:doi/10.70967/x.y`` → ``10.70967/x.y``."""
    s = raw.strip()
    for prefix in ("info:doi/", "doi:", "https://doi.org/", "http://doi.org/"):
        if s.lower().startswith(prefix):
            return s[len(prefix):]
    return None


def _classify_identifiers(identifier_texts: list[str]) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Sort a record's ``dc:identifier`` values into (doi, landing, pdf).

    BePress emits up to three identifier strings per record: a DC
    landing URL, a ``info:doi/...`` URI, and a viewable-content PDF
    URL. None are guaranteed.
    """
    doi: Optional[str] = None
    landing: Optional[str] = None
    pdf: Optional[str] = None
    for text in identifier_texts:
        s = text.strip()
        if not s:
            continue
        if s.startswith("info:doi/") or s.lower().startswith("doi:"):
            d = _strip_doi_prefix(s)
            if d:
                doi = d
        elif s.lower().endswith(".pdf") or "/viewcontent" in s.lower():
            pdf = s
        elif s.startswith(("http://", "https://")):
            # First non-PDF http URL wins as the landing page.
            if landing is None:
                landing = s
    return doi, landing, pdf


def _pub_id_from_landing(landing_url: Optional[str]) -> Optional[str]:
    """``https://publications.iaa.org.il/atiqot/vol112/iss1/1`` →
    ``atiqot/vol112/iss1/1``. Anything off-domain returns None."""
    if not landing_url:
        return None
    prefix = "https://publications.iaa.org.il/"
    if landing_url.startswith(prefix):
        return landing_url[len(prefix):].rstrip("/") or None
    return None


def _extract_year(date_text: Optional[str]) -> Optional[int]:
    """``2024-11-26T10:12:05Z`` → 2024. Also tolerates bare YYYY."""
    if not date_text:
        return None
    s = date_text.strip()
    if len(s) >= 4 and s[:4].isdigit():
        try:
            return int(s[:4])
        except ValueError:
            return None
    return None


def _query_tokens(query: str) -> list[str]:
    """Split a search query into lowercase tokens for AND filtering."""
    return [t for t in re.split(r"\s+", query.strip().lower()) if t]


def _record_matches(
    tokens: list[str],
    title: str,
    description: str,
    subjects: list[str],
    authors: list[str],
) -> bool:
    """True when every token appears in title / description / subjects / authors.

    Authors are included to support author-name queries (``"Cohen
    Negev"`` should match a record where Cohen is an author and Negev
    is in the subject). Empty token list = match everything.
    """
    if not tokens:
        return True
    haystack = " ".join(
        [title, description, " ".join(subjects), " ".join(authors)]
    ).lower()
    return all(tok in haystack for tok in tokens)


def _record_to_paper(record: ET.Element, tokens: list[str]) -> Optional[DAOPaper]:
    """Convert one ``<record>`` to a ``DAOPaper`` if it passes the keyword filter."""
    metadata = record.find("oai:metadata", _NS)
    if metadata is None:
        return None
    dc = metadata.find("oai_dc:dc", _NS)
    if dc is None:
        return None

    title_el = dc.find("dc:title", _NS)
    title = (title_el.text or "").strip() if title_el is not None and title_el.text else ""
    if not title:
        title = "(untitled)"

    creators = [
        (e.text or "").strip()
        for e in dc.findall("dc:creator", _NS)
        if e is not None and e.text and e.text.strip()
    ]

    description_el = dc.find("dc:description", _NS)
    description = (
        (description_el.text or "").strip()
        if description_el is not None and description_el.text
        else ""
    )

    subjects = [
        (e.text or "").strip()
        for e in dc.findall("dc:subject", _NS)
        if e is not None and e.text and e.text.strip()
    ]

    # Keyword filter early — drop non-matching records before allocating.
    if not _record_matches(tokens, title, description, subjects, creators):
        return None

    date_el = dc.find("dc:date", _NS)
    year = _extract_year(date_el.text if date_el is not None else None)

    identifier_texts = [
        (e.text or "")
        for e in dc.findall("dc:identifier", _NS)
        if e is not None and e.text
    ]
    doi, landing_url, pdf_url = _classify_identifiers(identifier_texts)
    iaa_pub_id = _pub_id_from_landing(landing_url)

    # Pick the canonical legacy ``doi_or_id`` and landing for the
    # DAOPaper. DOI > IAA-path > OAI header identifier (last resort).
    if doi:
        doi_or_id = doi
        if not landing_url:
            landing_url = f"https://doi.org/{doi}"
    elif iaa_pub_id:
        doi_or_id = f"iaa:{iaa_pub_id}"
    else:
        # Fall back to the OAI record identifier (e.g. atiqot-1041)
        header = record.find("oai:header/oai:identifier", _NS)
        oai_id = (header.text or "").strip() if header is not None and header.text else ""
        doi_or_id = f"iaa:{oai_id}" if oai_id else "iaa:(unknown)"

    if landing_url is None:
        # No anchor at all — drop, same drop-not-fake policy as other
        # cross-platform adapters.
        return None

    language = _detect_language(f"{title} {description}")

    identifiers = Identifiers(doi=doi, iaa_pub_id=iaa_pub_id)
    audit = Audit(primary_source=True, aggregator=False, warn_marker=False)
    inline_citation = build_inline_citation(
        authors=creators,
        year=year,
        pages=None,
        title=title,
        identifiers=identifiers,
        landing_page_url=landing_url,
        open_access_url=pdf_url,
        audit=audit,
    )

    return DAOPaper(
        title=title,
        authors=creators,
        year=year,
        doi_or_id=doi_or_id,
        source="iaa",
        open_access_url=pdf_url,  # type: ignore[arg-type]
        landing_page_url=landing_url,  # type: ignore[arg-type]
        language=language,
        abstract=description or None,
        publication_status=PublicationStatus.PUBLISHED,
        identifiers=identifiers,
        audit=audit,
        inline_citation=inline_citation,
    )


def _build_initial_params(
    set_spec: Optional[str],
    year_from: Optional[int],
    year_to: Optional[int],
) -> list[tuple[str, str]]:
    """First-page OAI parameters. ``resumptionToken`` calls replace these."""
    params: list[tuple[str, str]] = [
        ("verb", "ListRecords"),
        ("metadataPrefix", "oai_dc"),
    ]
    if set_spec:
        params.append(("set", set_spec))
    if year_from is not None:
        params.append(("from", f"{year_from:04d}-01-01"))
    if year_to is not None:
        params.append(("until", f"{year_to:04d}-12-31"))
    return params


def _parse_page(
    xml_text: str,
    tokens: list[str],
) -> tuple[list[DAOPaper], Optional[str]]:
    """Parse one OAI-PMH response page.

    Returns ``(matches_on_this_page, resumption_token)``. ``token`` is
    ``None`` when the list is exhausted (or absent in the response).
    """
    root = ET.fromstring(xml_text)
    matches: list[DAOPaper] = []
    list_records = root.find("oai:ListRecords", _NS)
    if list_records is None:
        return matches, None
    for record in list_records.findall("oai:record", _NS):
        # Skip deleted records — header has status="deleted" attribute.
        header = record.find("oai:header", _NS)
        if header is not None and header.get("status") == "deleted":
            continue
        paper = _record_to_paper(record, tokens)
        if paper is not None:
            matches.append(paper)
    token_el = list_records.find("oai:resumptionToken", _NS)
    token = None
    if token_el is not None and token_el.text and token_el.text.strip():
        token = token_el.text.strip()
    return matches, token


async def search_iaa_impl(
    query: str,
    max_results: int = 10,
    collection: Optional[str] = None,
    year_from: Optional[int] = None,
    year_to: Optional[int] = None,
    *,
    client: Optional[httpx.AsyncClient] = None,
) -> list[DAOPaper]:
    """Search IAA Publications via OAI-PMH.

    ``client`` is injectable for tests. Paginates via resumption tokens
    until ``max_results`` matches accumulated or the safety page cap
    is hit.
    """
    set_spec = _resolve_set_spec(collection)
    tokens = _query_tokens(query)
    log.info(
        "iaa.search query=%r tokens=%s set=%s years=%s-%s",
        query, tokens, set_spec, year_from, year_to,
    )

    headers = {"User-Agent": _USER_AGENT, "Accept": "application/xml"}

    async def _run(c: httpx.AsyncClient) -> list[DAOPaper]:
        matches: list[DAOPaper] = []
        params: list[tuple[str, str]] = _build_initial_params(set_spec, year_from, year_to)
        for page in range(_MAX_PAGES):
            r = await c.get(IAA_OAI, params=params, headers=headers, timeout=HTTP_TIMEOUT)
            r.raise_for_status()
            page_matches, token = _parse_page(r.text, tokens)
            matches.extend(page_matches)
            log.info("iaa.search page=%d new_matches=%d total=%d token=%s",
                     page, len(page_matches), len(matches), bool(token))
            if len(matches) >= max_results:
                break
            if not token:
                break
            # Subsequent pages use ONLY verb + resumptionToken.
            params = [("verb", "ListRecords"), ("resumptionToken", token)]
        return matches[:max_results]

    if client is not None:
        return await _run(client)
    async with httpx.AsyncClient() as c:
        return await _run(c)


def register(mcp: FastMCP) -> None:
    """Register the ``search_iaa`` tool."""

    @mcp.tool()
    async def search_iaa(
        query: str,
        max_results: int = 10,
        collection: Optional[str] = None,
        year_from: Optional[int] = None,
        year_to: Optional[int] = None,
    ) -> list[DAOPaper]:
        """Search IAA Publications — Israel Antiquities Authority's open
        access portal: ʿAtiqot, Hadashot Arkheologiyot (HA-ESI), IAA
        Reports, Cornerstone, ESI English Series, Favissa, IAA Book
        Series. Primary source for Israeli excavation grey literature
        in Hebrew + English, with DOIs (DataCite prefix 10.70967/) from
        2000 onwards.

        Backend: OAI-PMH (``/do/oai/``). Server-side filtering by
        collection and year range; client-side AND-of-tokens keyword
        matching against title + description + subject + authors. The
        underlying Solr full-text search is not exposed publicly, so
        very wide queries without ``year_from``/``year_to`` may need
        multiple pagination round-trips before finding matches.
        Recommendation: always pass at least a 5-year window.

        Citation rendering: every record carries an ``inline_citation``
        block with pre-rendered Markdown. Copy
        ``inline_citation.markdown_recommended`` verbatim — do not
        reformat to ``[(domain)](url)``. Almost every IAA record has a
        DOI, so the recommended form is ``[(Author Year)](doi.org/…)``.
        For bibliography or reference-list entries, prefer
        ``inline_citation.markdown_doi`` — the visible label is the
        actual DOI string, useful for cross-reference and BibTeX
        round-tripping.

        Args:
            query: free-text query. Tokens are AND-matched against
                title + description + subject + author fields. Use a
                handful of keywords; complex Lucene operators are not
                supported here.
            max_results: 1-50.
            collection: optional collection filter — friendly name
                (``"atiqot"`` | ``"ha-esi"`` | ``"ha-hebrew"`` |
                ``"esi-english"`` | ``"iaa-books"`` | ``"favissa"`` |
                ``"cornerstone"`` | ``"ha-esi-bilingual"``) or a raw
                OAI setSpec (``"publication:xxx"``).
            year_from: lower bound publication year, inclusive.
            year_to: upper bound publication year, inclusive.
        """
        return await search_iaa_impl(
            query=query,
            max_results=max_results,
            collection=collection,
            year_from=year_from,
            year_to=year_to,
        )
