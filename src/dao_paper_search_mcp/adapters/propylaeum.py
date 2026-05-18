"""PropylaeumDOK adapter — OAI-PMH backend.

Source
------
PropylaeumDOK is the Open Access repository of the FID Altertumswissenschaften
(Special Information Service for Classical Studies) hosted by UB Heidelberg.
It covers archaeology, ancient history, classical philology, and neighbouring
disciplines — with a strong focus on the Ancient Near East, Egypt, Greece, and
Rome. Relevant for DAO use cases involving Bronze/Iron Age Levant through the
Hellenistic and Roman periods.

OAI-PMH endpoint
----------------
``https://archiv.ub.uni-heidelberg.de/propylaeumdok/cgi/oai2``

The repository runs EPrints 3.4. Dublin Core (`oai_dc`) is the primary
metadata format. Records carry:

- ``dc:identifier`` — landing URL and possibly DOI as ``https://doi.org/…``
- ``dc:creator``    — authors in "Family, Given" format
- ``dc:date``       — publication date (YYYY or YYYY-MM-DD)
- ``dc:description``— abstract / summary
- ``dc:subject``    — subject headings / keywords
- ``dc:language``   — ISO 639-1 or 639-2 language code when present
- ``dc:type``       — publication type

No set filter is applied by default: the whole repository is specialized
enough that a full scan is practical within the wall-clock budget. Year
range parameters narrow the result set when provided.

Pagination uses OAI resumption tokens (100 records/page). Three-page cap
plus a 40-second wall-clock budget matches the IAA adapter strategy.

Client-side AND-of-tokens keyword filtering across title + abstract +
subjects + authors is applied before allocating DAOPaper objects.
"""

from __future__ import annotations

import logging
import re
import time
import xml.etree.ElementTree as ET
from typing import Optional

import httpx
from mcp.server.fastmcp import FastMCP

from ..inline_citation import build_inline_citation
from ..models import Audit, DAOPaper, Identifiers, PublicationStatus, Venue
from ..utils.contact import CONTACT_EMAIL
from ..utils.doi import normalize_doi

log = logging.getLogger(__name__)

PROPYLAEUM_OAI = "https://archiv.ub.uni-heidelberg.de/propylaeumdok/cgi/oai2"
HTTP_TIMEOUT = 30.0

_USER_AGENT = (
    "dao-paper-search-mcp/0.1 "
    "(+https://github.com/leiverkus/dao-paper-search-mcp; "
    f"mailto:{CONTACT_EMAIL})"
)

_MAX_PAGES = 3
_BUDGET_SECONDS = 40.0

_NS = {
    "oai": "http://www.openarchives.org/OAI/2.0/",
    "oai_dc": "http://www.openarchives.org/OAI/2.0/oai_dc/",
    "dc": "http://purl.org/dc/elements/1.1/",
}

# ISO 639-2/T → ISO 639-1 mapping for the languages most common in Propylaeum.
_ISO3_TO_2: dict[str, str] = {
    "deu": "de", "ger": "de",
    "eng": "en",
    "fra": "fr", "fre": "fr",
    "ita": "it",
    "lat": "la",
    "spa": "es",
    "por": "pt",
    "nld": "nl", "dut": "nl",
    "pol": "pl",
    "hun": "hu",
    "ces": "cs", "cze": "cs",
    "grc": "el",  # Ancient Greek
    "heb": "he",
    "ara": "ar",
    "tur": "tr",
}

_EPRINTS_ID_RE = re.compile(
    r"https?://archiv\.ub\.uni-heidelberg\.de/propylaeumdok/(\d+)"
)

_DOI_URL_RE = re.compile(
    r"https?://(?:dx\.)?doi\.org/(.+)", re.IGNORECASE
)


def _extract_eprints_id(landing_url: Optional[str]) -> Optional[str]:
    """``https://archiv.ub.uni-heidelberg.de/propylaeumdok/10234/`` → ``"10234"``."""
    if not landing_url:
        return None
    m = _EPRINTS_ID_RE.search(landing_url)
    return m.group(1) if m else None


def _classify_identifiers(
    identifier_texts: list[str],
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Sort ``dc:identifier`` values into (doi, landing_url, pdf_url).

    EPrints outputs:
    - A landing URL: ``https://archiv.ub.uni-heidelberg.de/propylaeumdok/{ID}/``
    - A DOI URL: ``https://doi.org/10.…`` (when registered)
    - Possibly a PDF URL: ``https://archiv.…/propylaeumdok/{ID}/1/filename.pdf``
    """
    doi: Optional[str] = None
    landing: Optional[str] = None
    pdf: Optional[str] = None

    for text in identifier_texts:
        s = text.strip()
        if not s:
            continue
        doi_url_m = _DOI_URL_RE.match(s)
        if doi_url_m:
            d = normalize_doi(doi_url_m.group(1))
            if d:
                doi = d
            continue
        if s.startswith("info:doi/") or s.lower().startswith("doi:"):
            d = normalize_doi(s)
            if d:
                doi = d
            continue
        if s.lower().endswith(".pdf") or "/document/" in s.lower():
            if pdf is None:
                pdf = s
            continue
        if s.startswith(("http://", "https://")):
            if landing is None:
                landing = s

    return doi, landing, pdf


def _parse_language(dc_language: Optional[str]) -> str:
    """Map ISO 639-2/T or 639-1 code to a 2-letter tag; fall back to ``"und"``."""
    if not dc_language:
        return "und"
    raw = dc_language.strip().lower()
    if raw in _ISO3_TO_2:
        return _ISO3_TO_2[raw]
    if len(raw) == 2:
        return raw
    return "und"


def _detect_language_from_text(text: str) -> str:
    """Heuristic language detection from title/abstract text.

    Checked in order: Hebrew codepoints, German-specific characters, Latin
    script (→ English as best guess), undetermined.
    """
    if any("א" <= ch <= "ת" for ch in text):
        return "he"
    if re.search(r"[äöüÄÖÜß]", text):
        return "de"
    if re.search(r"[A-Za-z]{4,}", text):
        return "en"
    return "und"


def _extract_year(date_text: Optional[str]) -> Optional[int]:
    """``2024-11-26`` or ``2024`` → 2024; ``None`` on failure."""
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
    haystack = " ".join(
        [title, description, " ".join(subjects), " ".join(authors)]
    ).lower()
    return all(tok in haystack for tok in tokens)


def _build_venue_from_source(source_texts: list[str]) -> Optional[Venue]:
    """Parse venue information from ``dc:source`` fields.

    OJS/EPrints encodes journal metadata in source strings like:
    ``"Acta Praehistorica et Archaeologica; Bd. 21 1989(1990); 131-133"``
    or bare ISSN strings. We extract the journal name, volume, and pages
    when the semicolon-delimited pattern is present; bare ISSNs are ignored.
    """
    for s in source_texts:
        s = s.strip()
        # Skip bare ISSN/ISBN patterns (8+ digit strings with optional hyphen)
        if re.fullmatch(r"[\d\-X]{8,}", s):
            continue
        parts = [p.strip() for p in s.split(";")]
        if len(parts) < 2:
            continue
        name = parts[0] if parts[0] else None
        volume: Optional[str] = None
        pages: Optional[str] = None
        for part in parts[1:]:
            # Volume pattern: "Bd. 21", "Vol. 3", "21 (2024)", bare digits
            vol_m = re.search(r"(?:Bd\.|Vol\.?|Band|Volume)?\s*(\d+)", part, re.IGNORECASE)
            # Pages pattern: "131-133", "pp. 5-10" — must contain a hyphen/en-dash
            pages_m = re.search(r"(\d+\s*[-–]\s*\d+)", part)
            if vol_m and not volume:
                volume = vol_m.group(1)
            if pages_m and not pages:
                pages = pages_m.group(1).strip()
        if name:
            return Venue(name=name, volume=volume, pages=pages)
    return None


def _record_to_paper(record: ET.Element, tokens: list[str]) -> Optional[DAOPaper]:
    """Convert one EPrints ``<record>`` to a ``DAOPaper`` if keyword-filtered."""
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

    # PDF may also appear in dc:relation
    if pdf_url is None:
        for rel_el in dc.findall("dc:relation", _NS):
            rel = (rel_el.text or "").strip()
            if rel.lower().endswith(".pdf") or "/document/" in rel.lower():
                pdf_url = rel
                break

    eprints_id = _extract_eprints_id(landing_url)

    if doi:
        doi_or_id = doi
    elif eprints_id:
        doi_or_id = f"propylaeum:{eprints_id}"
    else:
        header = record.find("oai:header/oai:identifier", _NS)
        oai_id = (header.text or "").strip() if header is not None and header.text else ""
        doi_or_id = f"propylaeum:{oai_id}" if oai_id else "propylaeum:(unknown)"

    if landing_url is None and doi is None:
        return None

    # Language: structured dc:language takes priority over text heuristic.
    lang_el = dc.find("dc:language", _NS)
    raw_lang = lang_el.text if lang_el is not None else None
    language = _parse_language(raw_lang)
    if language == "und":
        language = _detect_language_from_text(f"{title} {description}")

    source_texts = [
        (e.text or "").strip()
        for e in dc.findall("dc:source", _NS)
        if e is not None and e.text and e.text.strip()
    ]
    venue = _build_venue_from_source(source_texts)

    identifiers = Identifiers(doi=doi, propylaeum_id=eprints_id)
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
        venue=venue,
    )

    return DAOPaper(
        title=title,
        authors=creators,
        year=year,
        doi_or_id=doi_or_id,
        source="propylaeum",
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
    year_from: Optional[int],
    year_to: Optional[int],
) -> list[tuple[str, str]]:
    params: list[tuple[str, str]] = [
        ("verb", "ListRecords"),
        ("metadataPrefix", "oai_dc"),
    ]
    if year_from is not None:
        params.append(("from", f"{year_from:04d}-01-01"))
    if year_to is not None:
        params.append(("until", f"{year_to:04d}-12-31"))
    return params


def _parse_page(
    xml_text: str,
    tokens: list[str],
) -> tuple[list[DAOPaper], Optional[str]]:
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


async def search_propylaeum_impl(
    query: str,
    max_results: int = 10,
    year_from: Optional[int] = None,
    year_to: Optional[int] = None,
    *,
    client: Optional[httpx.AsyncClient] = None,
) -> list[DAOPaper]:
    """Search PropylaeumDOK via OAI-PMH. Injectable ``client`` for tests."""
    tokens = _query_tokens(query)
    log.info(
        "propylaeum.search query=%r tokens=%s years=%s-%s",
        query, tokens, year_from, year_to,
    )

    headers = {"User-Agent": _USER_AGENT, "Accept": "application/xml"}

    async def _run(c: httpx.AsyncClient) -> list[DAOPaper]:
        matches: list[DAOPaper] = []
        params = _build_initial_params(year_from, year_to)
        deadline = time.monotonic() + _BUDGET_SECONDS
        for page in range(_MAX_PAGES):
            if time.monotonic() > deadline:
                log.warning(
                    "propylaeum.search budget %ss exceeded after %d pages; "
                    "returning %d partial matches",
                    _BUDGET_SECONDS, page, len(matches),
                )
                break
            r = await c.get(PROPYLAEUM_OAI, params=params, headers=headers, timeout=HTTP_TIMEOUT)
            r.raise_for_status()
            page_matches, token = _parse_page(r.text, tokens)
            matches.extend(page_matches)
            log.info(
                "propylaeum.search page=%d new=%d total=%d token=%s",
                page, len(page_matches), len(matches), bool(token),
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
    """Register the ``search_propylaeum`` tool."""

    @mcp.tool()
    async def search_propylaeum(
        query: str,
        max_results: int = 10,
        year_from: Optional[int] = None,
        year_to: Optional[int] = None,
    ) -> list[DAOPaper]:
        """Search PropylaeumDOK — the Open Access repository of the FID
        Altertumswissenschaften (UB Heidelberg). Covers classical archaeology,
        ancient history, classical philology, Egyptology, and neighbouring
        disciplines including Bronze/Iron Age Levant, Hellenistic and Roman
        Near East. Multilingual: German, English, Italian, French, Latin.
        Records carry DOIs (prefix ``10.11588/propylaeumdok``) when registered.

        Backend: OAI-PMH (EPrints 3.4). No set filter is applied — the
        repository is specialized enough for whole-archive keyword matching.
        A 40-second wall-clock budget caps each call; partial matches are
        returned when exceeded. Client-side AND-of-tokens filtering covers
        title + abstract + subjects + authors.

        Citation rendering (Schema v2): each returned ``DAOPaper`` carries an
        ``inline_citation`` block. Copy ``inline_citation.markdown`` verbatim
        for in-text citations. For the reference list copy
        ``inline_citation.authoritative_bibliography_line`` verbatim; fall
        back to Author-Year + DOI/URL when ``None``.

        Args:
            query: free-text keywords, AND-matched. Use a handful of terms;
                Boolean operators are not supported.
            max_results: 1–50.
            year_from: lower bound publication year, inclusive.
            year_to: upper bound publication year, inclusive.
        """
        return await search_propylaeum_impl(
            query=query,
            max_results=max_results,
            year_from=year_from,
            year_to=year_to,
        )
