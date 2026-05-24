"""OpenEdition adapter — OAI-PMH backend.

Source
------
OpenEdition is France's main SSH (social sciences and humanities) publishing
platform. It aggregates:
- **OpenEdition Journals** — ~600 peer-reviewed journals in humanities, social
  sciences, theology, Orientalism, archaeology, ancient history (incl.
  Revue des études anciennes, Syria, Semitica, Yod, …).
- **OpenEdition Books** — monographs from French university presses.
- **Hypotheses** — research blogs (``blogs`` set).
- **Calenda** — events calendar (``events`` set — not useful for literature
  search).

OAI-PMH endpoint
----------------
``https://metadata.openedition.org/oai``  (v2, live since late 2024; CC0
metadata licence)

Records use Dublin Core (``oai_dc``). Key observations from live probing
(2026-05-18):

- ``dc:identifier`` carries the DOI as a full ``https://doi.org/10.4000/…``
  URL, plus the Handle URL (``https://hdl.handle.net/20.500.13089/…``), plus
  the OpenEdition landing URL (``https://journals.openedition.org/…``).
- Header ``<identifier>`` is the Handle path (``20.500.13089/lr42``) without
  scheme prefix.
- ``dc:date`` may contain multiple values: a bare year (``"2019"``) and an
  ``info:eu-repo/date/publication/…`` URI — only bare-date values are parsed.
- ``dc:language`` uses ISO 639-1 two-letter codes (``fr``, ``en``) directly.
- ``dc:description`` and ``dc:subject`` carry ``xml:lang`` attributes; all
  text values are merged for keyword matching.
- ``dc:relation`` holds ISSN refs and related-record handles — not used for
  citation building.
- Full-text access is HTML (no PDF in dc:identifier); the landing URL is the
  primary access point.

Sets
----
- ``journals`` (default) — journal articles, most useful for academic search.
- ``books``               — book chapters and monographs.
- ``all``                 — both journals and books (no set filter).
- ``blogs``               — Hypotheses blog posts (avoid for literature search).

Pagination uses OAI resumption tokens (100 records/page). Three-page cap +
40-second wall-clock budget.

Client-side AND-of-tokens keyword filtering across title + abstract +
subjects + authors. The OAI-PMH protocol offers no server-side keyword search;
year range parameters (``from``/``until``) narrow the date window to reduce
the candidate set.
"""

from __future__ import annotations

import logging
import re
import time
import xml.etree.ElementTree as ET

import httpx
from mcp.server.fastmcp import FastMCP

from ..inline_citation import build_inline_citation
from ..models import Audit, DAOPaper, Identifiers, PublicationStatus
from ..utils import HttpxParams
from ..utils.contact import CONTACT_EMAIL
from ..utils.doi import normalize_doi

log = logging.getLogger(__name__)

OPENEDITION_OAI = "https://metadata.openedition.org/oai"
HTTP_TIMEOUT = 30.0

_USER_AGENT = f"dao-paper-search-mcp/0.1 (+https://github.com/leiverkus/dao-paper-search-mcp; mailto:{CONTACT_EMAIL})"

_MAX_PAGES = 3
_BUDGET_SECONDS = 40.0

_NS = {
    "oai": "http://www.openarchives.org/OAI/2.0/",
    "oai_dc": "http://www.openarchives.org/OAI/2.0/oai_dc/",
    "dc": "http://purl.org/dc/elements/1.1/",
}

# Friendly set names → OAI setSpec. ``None`` means no set filter.
_SETS: dict[str, str | None] = {
    "journals": "journals",
    "books": "books",
    "all": None,
    "blogs": "blogs",
    "events": "events",
}
_DEFAULT_SET = "journals"

# DOI URL patterns: OpenEdition always emits the full https://doi.org/ form.
_DOI_URL_RE = re.compile(r"https?://(?:dx\.)?doi\.org/(.+)", re.IGNORECASE)

# OpenEdition landing URL domains.
_OPENEDITION_DOMAINS = (
    "journals.openedition.org",
    "books.openedition.org",
    "hypotheses.org",
    "calenda.org",
    "openedition.org",
)


def _resolve_set_spec(collection: str | None) -> str | None:
    """Map user-facing collection name to OAI setSpec.

    ``None`` or empty → default (journals). ``"all"`` → no set filter.
    Unknown names are passed through unchanged.
    """
    if not collection or not collection.strip():
        return _SETS[_DEFAULT_SET]
    s = collection.strip().lower()
    if s in _SETS:
        return _SETS[s]
    return s  # pass-through for direct setSpec values


def _classify_identifiers(
    identifier_texts: list[str],
) -> tuple[str | None, str | None]:
    """Sort ``dc:identifier`` values into (doi, landing_url).

    OpenEdition records carry:
    - ``https://doi.org/10.4000/…``           — DOI URL (priority)
    - ``https://hdl.handle.net/20.500.13089/…``— Handle URL
    - ``https://journals.openedition.org/…``   — landing URL

    Info URIs (``info:eu-repo/…``) are skipped.
    DOI takes priority over landing; Handle is used as landing fallback.
    """
    doi: str | None = None
    openedition_landing: str | None = None
    handle_landing: str | None = None

    for text in identifier_texts:
        s = text.strip()
        if not s or s.startswith("info:") or s.startswith("urn:"):
            continue

        doi_m = _DOI_URL_RE.match(s)
        if doi_m:
            d = normalize_doi(doi_m.group(1))
            if d and doi is None:
                doi = d
            continue

        if s.startswith("https://hdl.handle.net/"):
            if handle_landing is None:
                handle_landing = s
            continue

        if s.startswith(("http://", "https://")):
            if any(d in s for d in _OPENEDITION_DOMAINS):
                if openedition_landing is None:
                    openedition_landing = s
                continue

    landing = openedition_landing or handle_landing
    return doi, landing


def _extract_year(date_texts: list[str]) -> int | None:
    """Extract the publication year from a list of ``dc:date`` values.

    OpenEdition emits both a bare year (``"2019"``) and an info URI
    (``"info:eu-repo/date/publication/2019-12-08"``). We prefer the bare
    form; info URIs are a fallback.
    """
    bare: int | None = None
    info_year: int | None = None
    for text in date_texts:
        s = text.strip()
        if not s:
            continue
        if s.startswith("info:eu-repo/date/"):
            # info:eu-repo/date/publication/YYYY-MM-DD
            m = re.search(r"/(\d{4})", s)
            if m and info_year is None:
                try:
                    info_year = int(m.group(1))
                except ValueError:
                    pass
        elif re.fullmatch(r"\d{4}", s):
            bare = int(s)
        elif len(s) >= 4 and s[:4].isdigit():
            try:
                y = int(s[:4])
                if bare is None:
                    bare = y
            except ValueError:
                pass
    return bare if bare is not None else info_year


def _extract_texts(elements: list[ET.Element]) -> list[str]:
    """Return non-empty text content from a list of elements."""
    return [(e.text or "").strip() for e in elements if e is not None and e.text and e.text.strip()]


def _query_tokens(query: str) -> list[str]:
    return [t for t in re.split(r"\s+", query.strip().lower()) if t]


def _record_matches(
    tokens: list[str],
    title: str,
    description: str,
    subjects: list[str],
    authors: list[str],
) -> bool:
    if not tokens:
        return True
    haystack = " ".join([title, description, " ".join(subjects), " ".join(authors)]).lower()
    return all(tok in haystack for tok in tokens)


def _record_to_paper(record: ET.Element, tokens: list[str]) -> DAOPaper | None:
    """Convert one OAI record to a ``DAOPaper`` if keyword-matched."""
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

    creators = _extract_texts(dc.findall("dc:creator", _NS))

    # Abstract: all dc:description values (multilingual); merge for matching,
    # keep only the first for the DAOPaper abstract field.
    description_texts = _extract_texts(dc.findall("dc:description", _NS))
    description_merged = " ".join(description_texts)
    abstract = description_texts[0] if description_texts else None

    subjects = _extract_texts(dc.findall("dc:subject", _NS))

    if not _record_matches(tokens, title, description_merged, subjects, creators):
        return None

    date_texts = _extract_texts(dc.findall("dc:date", _NS))
    year = _extract_year(date_texts)

    identifier_texts = _extract_texts(dc.findall("dc:identifier", _NS))
    doi, landing_url = _classify_identifiers(identifier_texts)

    # Build doi_or_id: DOI > header handle > fallback
    if doi:
        doi_or_id = doi
    else:
        header = record.find("oai:header/oai:identifier", _NS)
        handle = (header.text or "").strip() if header is not None and header.text else ""
        doi_or_id = f"openedition:{handle}" if handle else "openedition:(unknown)"

    if landing_url is None and doi is None:
        return None

    lang_el = dc.find("dc:language", _NS)
    language = (lang_el.text or "und").strip() if lang_el is not None and lang_el.text else "und"

    # Determine publication status from dc:type
    type_texts = _extract_texts(dc.findall("dc:type", _NS))
    status = PublicationStatus.PUBLISHED
    if any("preprint" in t.lower() for t in type_texts):
        status = PublicationStatus.PREPRINT

    identifiers = Identifiers(doi=doi)
    audit = Audit(primary_source=True, aggregator=False, warn_marker=False)
    inline_citation = build_inline_citation(
        authors=creators,
        year=year,
        pages=None,
        title=title,
        identifiers=identifiers,
        landing_page_url=landing_url,
        open_access_url=None,
        audit=audit,
        venue=None,
    )

    return DAOPaper(
        title=title,
        authors=creators,
        year=year,
        doi_or_id=doi_or_id,
        source="openedition",
        open_access_url=None,
        landing_page_url=landing_url,  # type: ignore[arg-type]
        language=language,
        abstract=abstract,
        publication_status=status,
        identifiers=identifiers,
        audit=audit,
        inline_citation=inline_citation,
    )


def _build_initial_params(
    set_spec: str | None,
    year_from: int | None,
    year_to: int | None,
) -> HttpxParams:
    params: HttpxParams = [
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
) -> tuple[list[DAOPaper], str | None]:
    """Parse one OAI-PMH response page; return (matches, resumption_token)."""
    root = ET.fromstring(xml_text)
    matches: list[DAOPaper] = []
    list_records = root.find("oai:ListRecords", _NS)
    if list_records is None:
        return matches, None
    for record in list_records.findall("oai:record", _NS):
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


async def search_openedition_impl(
    query: str,
    max_results: int = 10,
    collection: str | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    *,
    client: httpx.AsyncClient | None = None,
) -> list[DAOPaper]:
    """Search OpenEdition via OAI-PMH. Injectable ``client`` for tests."""
    set_spec = _resolve_set_spec(collection)
    tokens = _query_tokens(query)
    log.info(
        "openedition.search query=%r tokens=%s set=%s years=%s-%s",
        query,
        tokens,
        set_spec,
        year_from,
        year_to,
    )

    headers = {"User-Agent": _USER_AGENT, "Accept": "application/xml"}

    async def _run(c: httpx.AsyncClient) -> list[DAOPaper]:
        matches: list[DAOPaper] = []
        params = _build_initial_params(set_spec, year_from, year_to)
        deadline = time.monotonic() + _BUDGET_SECONDS
        for page in range(_MAX_PAGES):
            if time.monotonic() > deadline:
                log.warning(
                    "openedition.search budget %ss exceeded after %d pages; returning %d partial matches",
                    _BUDGET_SECONDS,
                    page,
                    len(matches),
                )
                break
            r = await c.get(OPENEDITION_OAI, params=params, headers=headers, timeout=HTTP_TIMEOUT)
            r.raise_for_status()
            page_matches, token = _parse_page(r.text, tokens)
            matches.extend(page_matches)
            log.info(
                "openedition.search page=%d new=%d total=%d token=%s",
                page,
                len(page_matches),
                len(matches),
                bool(token),
            )
            if len(matches) >= max_results:
                break
            if not token:
                break
            params = [("verb", "ListRecords"), ("resumptionToken", token)]
        return matches[:max_results]

    if client is not None:
        return await _run(client)
    async with httpx.AsyncClient() as c:
        return await _run(c)


def register(mcp: FastMCP) -> None:
    """Register the ``search_openedition`` tool."""

    @mcp.tool()
    async def search_openedition(
        query: str,
        max_results: int = 10,
        collection: str = "journals",
        year_from: int | None = None,
        year_to: int | None = None,
    ) -> list[DAOPaper]:
        """Search OpenEdition — France's main SSH (social sciences and
        humanities) open-access platform. Covers ~600 peer-reviewed journals
        and thousands of books, including key titles for ancient Near East,
        Levantine archaeology, biblical studies, Egyptology, and classical
        studies: *Syria*, *Semitica*, *Revue des études anciennes*, *Yod*,
        *Topoi*, *Revue de l'histoire des religions*, and many more.
        Primarily French- and English-language content.

        Backend: OAI-PMH 2.0 (``metadata.openedition.org/oai``, CC0 metadata).
        Server-side filtering by collection and year range; client-side
        AND-of-tokens keyword matching. A 40-second wall-clock budget caps
        each call; partial results are returned when exceeded.

        **Tip for sparse queries:** Combine with a year range (``year_from`` /
        ``year_to``) to keep the candidate set tractable — without a year
        filter the adapter scans up to 300 records from the most recently
        indexed content.

        Citation rendering (Schema v2): copy ``inline_citation.markdown``
        verbatim for in-text citations; copy
        ``inline_citation.authoritative_bibliography_line`` verbatim for the
        reference list.

        Args:
            query: free-text keywords, AND-matched against title + abstract +
                subjects + authors.
            max_results: 1–50.
            collection: ``"journals"`` (default) | ``"books"`` | ``"all"``
                (journals + books, no set filter). ``"blogs"``/``"events"``
                also accepted but rarely useful for academic literature.
            year_from: lower bound publication year, inclusive.
            year_to: upper bound publication year, inclusive.
        """
        return await search_openedition_impl(
            query=query,
            max_results=max_results,
            collection=collection,
            year_from=year_from,
            year_to=year_to,
        )
