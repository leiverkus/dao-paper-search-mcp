"""IxTheo adapter — BSZ SRU backend (picaxml).

Source
------
Index Theologicus (IxTheo) is the leading international bibliography for
theology, biblical studies, church history, and religious studies, hosted
by the University of Tübingen. Directly relevant for DAO workflows:

- Biblical archaeology, textual criticism, Dead Sea Scrolls
- Jewish–Christian relations, ancient religion, Levantine cult sites
- Patristic sources, early church, Coptic studies
- Cross-disciplinary overlap with Levantine and Near Eastern archaeology

API
---
``ixtheo.de`` is protected by a JavaScript proof-of-work challenge that
blocks direct API access. IxTheo content is indexed in the BSZ SWB union
catalogue (Bibliotheksservice-Zentrum Baden-Württemberg), which provides
an open SRU (Search/Retrieve via URL) endpoint without authentication:

    https://sru.bsz-bw.de/swb

Records use the ``picaxml`` schema, a structured PICA+ XML format with
richer metadata than Dublin Core: bare DOIs, structured author fields,
venue metadata (journal, volume, issue, pages), and abstracts.

Query language: SRU CQL 1.1. Title-and-keyword search uses
``title all "terms"`` (all terms must appear in the title). Pagination
via ``startRecord`` (1-based); page size 20; three-page cap.

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
| 021A  | a       | title (may carry ``@`` sort marker)        |
| 028A  | A/D     | first author: family / given               |
| 028C  | A/D     | co-author: family / given (repeatable)     |
| 031A  | d/e/h   | venue: volume / issue / pages              |
| 039B  | t       | journal/series title                       |
| 044K  | a       | subject headings (repeatable)              |
| 047I  | a       | abstract                                   |
+-------+---------+--------------------------------------------+

Note: BSZ SWB is broader than IxTheo alone; theology and biblical-studies
queries return predominantly IxTheo-indexed records. The adapter does not
filter by institution code — all matching records from the SWB index are
returned.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from typing import Optional

import httpx
from mcp.server.fastmcp import FastMCP

from ..inline_citation import build_inline_citation
from ..models import Audit, DAOPaper, Identifiers, PublicationStatus, Venue
from ..utils.contact import CONTACT_EMAIL
from ..utils.doi import normalize_doi

log = logging.getLogger(__name__)

BSZ_SRU = "https://sru.bsz-bw.de/swb"
HTTP_TIMEOUT = 30.0

_USER_AGENT = (
    "dao-paper-search-mcp/0.1 "
    "(+https://github.com/leiverkus/dao-paper-search-mcp; "
    f"mailto:{CONTACT_EMAIL})"
)

_PAGE_SIZE = 20
_MAX_PAGES = 3

_SRU_NS = {"zs": "http://www.loc.gov/zing/srw/"}
_PICA_NS = {"pica": "info:srw/schema/5/picaXML-v1.0"}

# ISO 639-2/T → ISO 639-1 for languages common in the IxTheo index.
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
    "grc": "el",
    "heb": "he",
    "ara": "ar",
    "tur": "tr",
}

_SORT_MARKER_RE = re.compile(r"@")


def _pica_subfield(record: ET.Element, tag: str, code: str) -> Optional[str]:
    """Return the first non-empty subfield text for the given tag+code."""
    for df in record.findall(f"pica:datafield[@tag='{tag}']", _PICA_NS):
        for sf in df.findall(f"pica:subfield[@code='{code}']", _PICA_NS):
            text = (sf.text or "").strip()
            if text:
                return text
    return None


def _pica_all_subfields(record: ET.Element, tag: str, code: str) -> list[str]:
    """Return text of all subfields matching tag+code across all datafield occurrences."""
    results: list[str] = []
    for df in record.findall(f"pica:datafield[@tag='{tag}']", _PICA_NS):
        for sf in df.findall(f"pica:subfield[@code='{code}']", _PICA_NS):
            text = (sf.text or "").strip()
            if text:
                results.append(text)
    return results


def _pica_author_from_datafield(df: ET.Element) -> Optional[str]:
    """Extract "Family, Given" from a single 028A or 028C datafield element."""
    family = ""
    given = ""
    for sf in df.findall("pica:subfield", _PICA_NS):
        code = sf.get("code", "")
        text = (sf.text or "").strip()
        if code == "A":
            family = text
        elif code == "D":
            given = text
    if family and given:
        return f"{family}, {given}"
    return family or given or None


def _extract_authors(record: ET.Element) -> list[str]:
    """Extract all authors: 028A (first) then 028C (co-authors, repeatable)."""
    authors: list[str] = []
    for df in record.findall("pica:datafield[@tag='028A']", _PICA_NS):
        author = _pica_author_from_datafield(df)
        if author:
            authors.append(author)
    for df in record.findall("pica:datafield[@tag='028C']", _PICA_NS):
        author = _pica_author_from_datafield(df)
        if author:
            authors.append(author)
    return authors


def _clean_title(raw: str) -> str:
    """Remove PICA sort markers (``@``) from title strings."""
    return _SORT_MARKER_RE.sub("", raw).strip()


def _extract_year(record: ET.Element) -> Optional[int]:
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


def _extract_doi(record: ET.Element) -> Optional[str]:
    """Extract DOI from 004V/0 (bare) or fall back to 017C/u (full URL)."""
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


def _extract_venue(record: ET.Element) -> Optional[Venue]:
    """Build Venue from 039B (journal name) + 031A (volume/issue/pages)."""
    journal = _pica_subfield(record, "039B", "t")
    volume = _pica_subfield(record, "031A", "d")
    issue = _pica_subfield(record, "031A", "e")
    pages = _pica_subfield(record, "031A", "h")
    if journal or volume or pages:
        return Venue(name=journal, volume=volume, issue=issue, pages=pages)
    return None


def _record_to_paper(record: ET.Element, tokens: list[str]) -> Optional[DAOPaper]:
    """Convert one PICA record element to a ``DAOPaper`` if keyword-matched."""
    raw_title = _pica_subfield(record, "021A", "a") or ""
    title = _clean_title(raw_title) if raw_title else "(untitled)"

    authors = _extract_authors(record)
    subjects = _pica_all_subfields(record, "044K", "a")
    abstract_parts = _pica_all_subfields(record, "047I", "a")
    abstract = abstract_parts[0] if abstract_parts else None

    if tokens:
        haystack = " ".join(
            [title, abstract or "", " ".join(subjects), " ".join(authors)]
        ).lower()
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
        doi_or_id = f"ixtheo:{ppn}"
    else:
        return None

    landing_url: Optional[str] = f"https://doi.org/{doi}" if doi else None

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
        source="ixtheo",
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
) -> tuple[list[DAOPaper], Optional[int]]:
    """Parse one SRU response page.

    Returns ``(matches, next_start_record)`` where ``next_start_record``
    is the 1-based ``startRecord`` to use for the following page, or
    ``None`` when no further records exist.
    """
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
    next_pos: Optional[int] = None
    if next_pos_el is not None and next_pos_el.text and next_pos_el.text.strip().isdigit():
        next_pos = int(next_pos_el.text.strip())

    return matches, next_pos


async def search_ixtheo_impl(
    query: str,
    max_results: int = 10,
    year_from: Optional[int] = None,
    year_to: Optional[int] = None,
    *,
    client: Optional[httpx.AsyncClient] = None,
) -> list[DAOPaper]:
    """Search IxTheo content via BSZ SRU (picaxml). Injectable ``client`` for tests."""
    tokens = [t for t in re.split(r"\s+", query.strip().lower()) if t]

    # CQL query: title search + optional date range filter
    cql = f'title all "{query}"'
    if year_from is not None and year_to is not None:
        cql += f' and year within "{year_from} {year_to}"'
    elif year_from is not None:
        cql += f' and year >= "{year_from}"'
    elif year_to is not None:
        cql += f' and year <= "{year_to}"'

    log.info(
        "ixtheo.search query=%r cql=%r max_results=%d",
        query, cql, max_results,
    )

    headers = {"User-Agent": _USER_AGENT, "Accept": "application/xml"}

    async def _run(c: httpx.AsyncClient) -> list[DAOPaper]:
        matches: list[DAOPaper] = []
        start_record = 1
        for page in range(_MAX_PAGES):
            params = {
                "operation": "searchRetrieve",
                "version": "1.1",
                "query": cql,
                "maximumRecords": str(_PAGE_SIZE),
                "startRecord": str(start_record),
                "recordSchema": "picaxml",
            }
            r = await c.get(BSZ_SRU, params=params, headers=headers, timeout=HTTP_TIMEOUT)
            r.raise_for_status()
            page_matches, next_pos = _parse_page(r.text, tokens)
            matches.extend(page_matches)
            log.info(
                "ixtheo.search page=%d start=%d new=%d total=%d next=%s",
                page, start_record, len(page_matches), len(matches), next_pos,
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
    """Register the ``search_ixtheo`` tool."""

    @mcp.tool()
    async def search_ixtheo(
        query: str,
        max_results: int = 10,
        year_from: Optional[int] = None,
        year_to: Optional[int] = None,
    ) -> list[DAOPaper]:
        """Search IxTheo — Index Theologicus, the leading international
        bibliography for theology, biblical studies, church history, and
        religious studies (University of Tübingen). Directly relevant for
        DAO workflows: biblical archaeology, Dead Sea Scrolls, ancient
        Levantine religion, Septuagint and textual criticism, early Judaism
        and Christianity, Coptic studies, ancient Near East.

        Backend: BSZ SRU union catalogue (``sru.bsz-bw.de/swb``, picaxml
        schema). IxTheo content is indexed in the BSZ Südwestdeutscher
        Bibliotheksverbund; the SRU endpoint is used because ``ixtheo.de``
        itself runs a JavaScript proof-of-work challenge that blocks API
        access. Records carry structured PICA+ metadata including DOIs,
        volume/issue/page ranges, and abstracts where available.

        Use ``year_from`` / ``year_to`` to restrict by publication year.
        The SRU endpoint applies server-side date filtering before returning
        records; client-side AND-of-tokens matching is applied additionally
        against title + abstract + subjects + authors.

        Citation rendering (Schema v2): copy ``inline_citation.markdown``
        verbatim for in-text citations; copy
        ``inline_citation.authoritative_bibliography_line`` verbatim for
        the reference list.

        Args:
            query: free-text keywords (AND-matched against title and
                full-text index).
            max_results: 1–50.
            year_from: lower bound publication year, inclusive.
            year_to: upper bound publication year, inclusive.
        """
        return await search_ixtheo_impl(
            query=query,
            max_results=max_results,
            year_from=year_from,
            year_to=year_to,
        )
