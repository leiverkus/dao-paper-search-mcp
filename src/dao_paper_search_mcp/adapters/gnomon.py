"""Gnomon adapter — K10plus SRU backend (picaxml).

Source
------
Gnomon Bibliographische Datenbank is the leading international bibliography
for classical studies: ancient Greece and Rome, classical archaeology,
ancient history, classical philology, and neighbouring disciplines. Its
geographical scope overlaps strongly with DAO workflows: Hellenistic and
Roman Near East, Phoenician and Punic studies, Greek colonies in the
Levant, Byzantine archaeology.

The full Gnomon index is part of the K10plus union catalogue (Bavarian
Library Network + GBV, ~200M records). The K10plus SRU endpoint provides
unauthenticated structured search over this content:

    https://sru.k10plus.de/opac-de-627

Records use the ``picaxml`` schema — the same PICA+ XML format used by the
BSZ SRU (IxTheo adapter) but with a broader database scope covering all
K10plus member libraries.

API differences from the IxTheo (BSZ SRU) adapter
--------------------------------------------------
- CQL index: ``pica.all=`` (all indexed fields) instead of ``title all``.
  K10plus CQL indices use the ``pica.`` prefix (``pica.tit``, ``pica.per``,
  ``pica.jah`` for year, ``pica.all`` for all fields).
- Author subfield codes: K10plus records without a GND authority link use
  lowercase subfield codes ``a`` (family) / ``d`` (given) instead of the
  uppercase ``A`` / ``D`` used in GND-linked records. Both variants are
  handled by the parser.
- Subtitle: ``021A/d`` carries subtitle or qualifying phrase; combined with
  ``021A/a`` (main title) when present.
- Publisher (books): ``033A/p`` (place) + ``033A/n`` (publisher name).
- Series: ``036E/a`` (series title) used as venue name fallback for book
  records without a journal.

PICA+ field mapping
-------------------
+-------+---------+--------------------------------------------+
| tag   | subcode | content                                    |
+-------+---------+--------------------------------------------+
| 003@  | 0       | PPN (record identifier)                    |
| 004V  | 0       | bare DOI (no resolver prefix)              |
| 010@  | a       | language (ISO 639-2 three-letter)          |
| 011@  | a       | publication year                           |
| 017C  | u       | DOI as full URL (``https://doi.org/…``)    |
| 021A  | a/d     | main title / subtitle                      |
| 028A  | A or a  | first author: family name                  |
|       | D or d  | first author: given name                   |
| 028C  | A or a  | co-author family (repeatable)              |
|       | D or d  | co-author given                            |
| 031A  | d/e/h   | venue: volume / issue / pages              |
| 033A  | p/n     | book: place / publisher                    |
| 036E  | a       | series title (book venue fallback)         |
| 039B  | t       | journal/series title                       |
| 044K  | a       | subject headings (repeatable)              |
| 047I  | a       | abstract                                   |
+-------+---------+--------------------------------------------+
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET

import httpx
from mcp.server.fastmcp import FastMCP

from ..inline_citation import build_inline_citation
from ..models import Audit, DAOPaper, Identifiers, PublicationStatus, Venue
from ..utils.contact import CONTACT_EMAIL
from ..utils.doi import normalize_doi

log = logging.getLogger(__name__)

K10PLUS_SRU = "https://sru.k10plus.de/opac-de-627"
HTTP_TIMEOUT = 30.0

_USER_AGENT = f"dao-paper-search-mcp/0.1 (+https://github.com/leiverkus/dao-paper-search-mcp; mailto:{CONTACT_EMAIL})"

_PAGE_SIZE = 20
_MAX_PAGES = 3

_SRU_NS = {"zs": "http://www.loc.gov/zing/srw/"}
_PICA_NS = {"pica": "info:srw/schema/5/picaXML-v1.0"}

_ISO3_TO_2: dict[str, str] = {
    "deu": "de",
    "ger": "de",
    "eng": "en",
    "fra": "fr",
    "fre": "fr",
    "ita": "it",
    "lat": "la",
    "spa": "es",
    "por": "pt",
    "nld": "nl",
    "dut": "nl",
    "pol": "pl",
    "hun": "hu",
    "ces": "cs",
    "cze": "cs",
    "grc": "el",
    "heb": "he",
    "ara": "ar",
    "tur": "tr",
}

_SORT_MARKER_RE = re.compile(r"@")


def _pica_subfield(record: ET.Element, tag: str, code: str) -> str | None:
    for df in record.findall(f"pica:datafield[@tag='{tag}']", _PICA_NS):
        for sf in df.findall(f"pica:subfield[@code='{code}']", _PICA_NS):
            text = (sf.text or "").strip()
            if text:
                return text
    return None


def _pica_all_subfields(record: ET.Element, tag: str, code: str) -> list[str]:
    results: list[str] = []
    for df in record.findall(f"pica:datafield[@tag='{tag}']", _PICA_NS):
        for sf in df.findall(f"pica:subfield[@code='{code}']", _PICA_NS):
            text = (sf.text or "").strip()
            if text:
                results.append(text)
    return results


def _pica_author_from_datafield(df: ET.Element) -> str | None:
    """Extract "Family, Given" from a 028A / 028C datafield.

    K10plus uses uppercase subfield codes (``A`` / ``D``) for GND-linked
    authority records and lowercase (``a`` / ``d``) for simple string entries.
    Both variants are accepted.
    """
    family = ""
    given = ""
    for sf in df.findall("pica:subfield", _PICA_NS):
        code = sf.get("code", "")
        text = (sf.text or "").strip()
        if code in ("A", "a") and not family:
            family = text
        elif code in ("D", "d") and not given:
            given = text
    if family and given:
        return f"{family}, {given}"
    return family or given or None


def _extract_authors(record: ET.Element) -> list[str]:
    authors: list[str] = []
    for df in record.findall("pica:datafield[@tag='028A']", _PICA_NS):
        a = _pica_author_from_datafield(df)
        if a:
            authors.append(a)
    for df in record.findall("pica:datafield[@tag='028C']", _PICA_NS):
        a = _pica_author_from_datafield(df)
        if a:
            authors.append(a)
    return authors


def _extract_title(record: ET.Element) -> str:
    """Combine 021A/a (main title) and 021A/d (subtitle) into one string."""
    df = record.find("pica:datafield[@tag='021A']", _PICA_NS)
    if df is None:
        return "(untitled)"
    main = ""
    sub = ""
    for sf in df.findall("pica:subfield", _PICA_NS):
        code = sf.get("code", "")
        text = _SORT_MARKER_RE.sub("", (sf.text or "")).strip()
        if code == "a" and not main:
            main = text
        elif code == "d" and not sub:
            sub = text
    if not main:
        return "(untitled)"
    return f"{main}: {sub}" if sub else main


def _extract_year(record: ET.Element) -> int | None:
    raw = _pica_subfield(record, "011@", "a")
    if not raw:
        return None
    m = re.search(r"\d{4}", raw)
    if m:
        try:
            return int(m.group(0))
        except ValueError:
            pass
    return None


def _extract_language(record: ET.Element) -> str:
    raw = _pica_subfield(record, "010@", "a")
    if not raw:
        return "und"
    code = raw.strip().lower()
    return _ISO3_TO_2.get(code, code if len(code) == 2 else "und")


def _extract_doi(record: ET.Element) -> str | None:
    bare = _pica_subfield(record, "004V", "0")
    if bare:
        d = normalize_doi(bare)
        if d:
            return d
    url = _pica_subfield(record, "017C", "u")
    if url:
        d = normalize_doi(url)
        if d:
            return d
    return None


def _extract_venue(record: ET.Element) -> Venue | None:
    """Build Venue from journal (039B) or series/publisher (036E / 033A)."""
    # Journal article: 039B/t + 031A (vol/issue/pages)
    journal = _pica_subfield(record, "039B", "t")
    volume = _pica_subfield(record, "031A", "d")
    issue = _pica_subfield(record, "031A", "e")
    pages = _pica_subfield(record, "031A", "h")
    if journal:
        return Venue(name=journal, volume=volume, issue=issue, pages=pages)

    # Book / monograph: series title (036E/a) or publisher (033A/n)
    series = _pica_subfield(record, "036E", "a")
    publisher = _pica_subfield(record, "033A", "n")
    name = series or publisher
    if name:
        return Venue(name=name, volume=volume, issue=issue, pages=pages)

    return None


def _record_to_paper(record: ET.Element, tokens: list[str]) -> DAOPaper | None:
    title = _extract_title(record)
    authors = _extract_authors(record)
    subjects = _pica_all_subfields(record, "044K", "a")
    abstract_parts = _pica_all_subfields(record, "047I", "a")
    abstract = abstract_parts[0] if abstract_parts else None

    if tokens:
        haystack = " ".join([title, abstract or "", " ".join(subjects), " ".join(authors)]).lower()
        if not all(tok in haystack for tok in tokens):
            return None

    year = _extract_year(record)
    language = _extract_language(record)
    doi = _extract_doi(record)
    venue = _extract_venue(record)
    ppn = _pica_subfield(record, "003@", "0")

    if doi:
        doi_or_id = doi
    elif ppn:
        doi_or_id = f"gnomon:{ppn}"
    else:
        return None

    landing_url: str | None = f"https://doi.org/{doi}" if doi else None

    identifiers = Identifiers(doi=doi)
    audit = Audit(primary_source=True, aggregator=False, warn_marker=False)
    inline_citation = build_inline_citation(
        authors=authors,
        year=year,
        pages=venue.pages if venue else None,
        title=title,
        identifiers=identifiers,
        landing_page_url=landing_url,
        open_access_url=None,
        audit=audit,
        venue=venue,
    )

    return DAOPaper(
        title=title,
        authors=authors,
        year=year,
        doi_or_id=doi_or_id,
        source="gnomon",
        open_access_url=None,
        landing_page_url=landing_url,  # type: ignore[arg-type]
        language=language,
        abstract=abstract,
        publication_status=PublicationStatus.PUBLISHED,
        identifiers=identifiers,
        audit=audit,
        inline_citation=inline_citation,
    )


def _parse_page(
    xml_text: str,
    tokens: list[str],
) -> tuple[list[DAOPaper], int | None]:
    root = ET.fromstring(xml_text)
    matches: list[DAOPaper] = []

    records_el = root.find("zs:records", _SRU_NS)
    if records_el is None:
        return matches, None

    for zs_record in records_el.findall("zs:record", _SRU_NS):
        record_data = zs_record.find("zs:recordData", _SRU_NS)
        if record_data is None:
            continue
        pica_record = record_data.find("pica:record", _PICA_NS)
        if pica_record is None:
            continue
        paper = _record_to_paper(pica_record, tokens)
        if paper is not None:
            matches.append(paper)

    next_pos_el = root.find("zs:nextRecordPosition", _SRU_NS)
    next_pos: int | None = None
    if next_pos_el is not None and next_pos_el.text and next_pos_el.text.strip().isdigit():
        next_pos = int(next_pos_el.text.strip())

    return matches, next_pos


async def search_gnomon_impl(
    query: str,
    max_results: int = 10,
    year_from: int | None = None,
    year_to: int | None = None,
    *,
    client: httpx.AsyncClient | None = None,
) -> list[DAOPaper]:
    """Search Gnomon / K10plus via SRU (picaxml). Injectable ``client`` for tests."""
    tokens = [t for t in re.split(r"\s+", query.strip().lower()) if t]

    # K10plus CQL: pica.all searches all indexed fields; pica.jah for year range.
    cql = f'pica.all="{query}"'
    if year_from is not None and year_to is not None:
        cql += f" and pica.jah>={year_from} and pica.jah<={year_to}"
    elif year_from is not None:
        cql += f" and pica.jah>={year_from}"
    elif year_to is not None:
        cql += f" and pica.jah<={year_to}"

    log.info("gnomon.search query=%r cql=%r max_results=%d", query, cql, max_results)

    headers = {"User-Agent": _USER_AGENT, "Accept": "application/xml"}

    async def _run(c: httpx.AsyncClient) -> list[DAOPaper]:
        matches: list[DAOPaper] = []
        start_record = 1
        for page in range(_MAX_PAGES):
            params = {
                "version": "1.1",
                "operation": "searchRetrieve",
                "query": cql,
                "maximumRecords": str(_PAGE_SIZE),
                "startRecord": str(start_record),
                "recordSchema": "picaxml",
            }
            r = await c.get(K10PLUS_SRU, params=params, headers=headers, timeout=HTTP_TIMEOUT)
            r.raise_for_status()
            page_matches, next_pos = _parse_page(r.text, tokens)
            matches.extend(page_matches)
            log.info(
                "gnomon.search page=%d start=%d new=%d total=%d next=%s",
                page,
                start_record,
                len(page_matches),
                len(matches),
                next_pos,
            )
            if len(matches) >= max_results:
                break
            if next_pos is None:
                break
            start_record = next_pos
        return matches[:max_results]

    if client is not None:
        return await _run(client)
    async with httpx.AsyncClient() as c:
        return await _run(c)


def register(mcp: FastMCP) -> None:
    """Register the ``search_gnomon`` tool."""

    @mcp.tool()
    async def search_gnomon(
        query: str,
        max_results: int = 10,
        year_from: int | None = None,
        year_to: int | None = None,
    ) -> list[DAOPaper]:
        """Search the Gnomon Bibliographische Datenbank — the leading
        international bibliography for classical studies (ancient Greece and
        Rome, classical archaeology, ancient history, classical philology).
        Directly relevant for DAO workflows covering the Hellenistic and
        Roman Near East, Phoenician and Punic studies, Greek colonies in the
        Levant, Nabataean archaeology, Byzantine period.

        Backend: K10plus SRU (``sru.k10plus.de/opac-de-627``, ``picaxml``
        schema). Gnomon content is fully integrated into the K10plus union
        catalogue (~200M records, Bavarian Library Network + GBV). The SRU
        endpoint searches all indexed fields (``pica.all=``) with optional
        server-side year filtering (``pica.jah``). Client-side AND-of-tokens
        matching is applied additionally.

        Records carry structured PICA+ metadata: DOIs, authors with optional
        GND authority links, journal/series/publisher information, and
        abstracts where available. Book records include series title and
        publisher as venue information.

        Citation rendering (Schema v2): copy ``inline_citation.markdown``
        verbatim for in-text citations; copy
        ``inline_citation.authoritative_bibliography_line`` verbatim for the
        reference list.

        Args:
            query: free-text keywords (AND-matched across all indexed fields:
                title, author, subject, abstract, series).
            max_results: 1–50.
            year_from: lower bound publication year, inclusive.
            year_to: upper bound publication year, inclusive.
        """
        return await search_gnomon_impl(
            query=query,
            max_results=max_results,
            year_from=year_from,
            year_to=year_to,
        )
