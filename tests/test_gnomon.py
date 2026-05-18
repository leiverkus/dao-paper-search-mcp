"""Tests for the Gnomon adapter (K10plus SRU / picaxml backend)."""

from __future__ import annotations

import pytest
import respx
import httpx
import xml.etree.ElementTree as ET

from dao_paper_search_mcp.adapters.gnomon import (
    _extract_authors,
    _extract_doi,
    _extract_language,
    _extract_title,
    _extract_venue,
    _extract_year,
    _pica_author_from_datafield,
    _pica_subfield,
    _pica_all_subfields,
    _parse_page,
    _record_to_paper,
    search_gnomon_impl,
    K10PLUS_SRU,
)

_NS = "info:srw/schema/5/picaXML-v1.0"
_PICA_NS = {"pica": _NS}


# ---------------------------------------------------------------------------
# Helper: build a minimal PICA record element
# ---------------------------------------------------------------------------

def _make_record(fields: dict) -> ET.Element:
    """fields: {tag: [(code, text), ...]}"""
    record = ET.Element(f"{{{_NS}}}record")
    for tag, subcodes in fields.items():
        df = ET.SubElement(record, f"{{{_NS}}}datafield", {"tag": tag})
        for code, text in subcodes:
            sf = ET.SubElement(df, f"{{{_NS}}}subfield", {"code": code})
            sf.text = text
    return record


# ---------------------------------------------------------------------------
# K10plus SRU response fixtures
# ---------------------------------------------------------------------------

# Record 1: journal article with DOI, GND-linked author (uppercase A/D), abstract
# Record 2: monograph with series, simple author (lowercase a/d), no DOI
SAMPLE_SRU_RESPONSE = """\
<?xml version="1.0" encoding="UTF-8"?>
<zs:searchRetrieveResponse xmlns:zs="http://www.loc.gov/zing/srw/">
  <zs:numberOfRecords>2</zs:numberOfRecords>
  <zs:records>

    <zs:record>
      <zs:recordData>
        <record xmlns="info:srw/schema/5/picaXML-v1.0">
          <datafield tag="003@"><subfield code="0">1935302507</subfield></datafield>
          <datafield tag="004V"><subfield code="0">10.1086/736852</subfield></datafield>
          <datafield tag="010@"><subfield code="a">eng</subfield></datafield>
          <datafield tag="011@"><subfield code="a">2025</subfield></datafield>
          <datafield tag="021A">
            <subfield code="a">The @Contribution of the Megiddo Expedition to Classical Archaeology</subfield>
          </datafield>
          <datafield tag="028A">
            <subfield code="A">Finkelstein</subfield>
            <subfield code="D">Israel</subfield>
          </datafield>
          <datafield tag="028C">
            <subfield code="A">Adams</subfield>
            <subfield code="D">Matthew J.</subfield>
          </datafield>
          <datafield tag="031A">
            <subfield code="d">88</subfield>
            <subfield code="e">3</subfield>
            <subfield code="h">240-247</subfield>
          </datafield>
          <datafield tag="039B"><subfield code="t">Near Eastern Archaeology</subfield></datafield>
          <datafield tag="044K"><subfield code="a">Hellenistic period</subfield></datafield>
          <datafield tag="044K"><subfield code="a">Levant classical</subfield></datafield>
          <datafield tag="047I">
            <subfield code="a">Surveys three decades of excavations at Megiddo with focus on Hellenistic strata.</subfield>
          </datafield>
        </record>
      </zs:recordData>
    </zs:record>

    <zs:record>
      <zs:recordData>
        <record xmlns="info:srw/schema/5/picaXML-v1.0">
          <datafield tag="003@"><subfield code="0">1884411169</subfield></datafield>
          <datafield tag="010@"><subfield code="a">ger</subfield></datafield>
          <datafield tag="011@"><subfield code="a">2006</subfield></datafield>
          <datafield tag="021A">
            <subfield code="a">Gnomon und die klassische Altertumswissenschaft</subfield>
            <subfield code="d">Einführung und Überblick</subfield>
          </datafield>
          <datafield tag="028A">
            <subfield code="a">Heidenreich</subfield>
            <subfield code="d">Marianne</subfield>
          </datafield>
          <datafield tag="033A">
            <subfield code="p">München</subfield>
            <subfield code="n">K.G. Saur</subfield>
          </datafield>
          <datafield tag="036E"><subfield code="a">Beiträge zur Altertumskunde</subfield></datafield>
          <datafield tag="044K"><subfield code="a">Klassische Altertumswissenschaft</subfield></datafield>
          <datafield tag="044K"><subfield code="a">Levant classical</subfield></datafield>
        </record>
      </zs:recordData>
    </zs:record>

  </zs:records>
</zs:searchRetrieveResponse>
"""

EMPTY_SRU_RESPONSE = """\
<?xml version="1.0" encoding="UTF-8"?>
<zs:searchRetrieveResponse xmlns:zs="http://www.loc.gov/zing/srw/">
  <zs:numberOfRecords>0</zs:numberOfRecords>
  <zs:records/>
</zs:searchRetrieveResponse>
"""

PAGE_ONE = """\
<?xml version="1.0" encoding="UTF-8"?>
<zs:searchRetrieveResponse xmlns:zs="http://www.loc.gov/zing/srw/">
  <zs:numberOfRecords>30</zs:numberOfRecords>
  <zs:records>
    <zs:record>
      <zs:recordData>
        <record xmlns="info:srw/schema/5/picaXML-v1.0">
          <datafield tag="003@"><subfield code="0">111</subfield></datafield>
          <datafield tag="004V"><subfield code="0">10.1000/page1</subfield></datafield>
          <datafield tag="010@"><subfield code="a">eng</subfield></datafield>
          <datafield tag="011@"><subfield code="a">2020</subfield></datafield>
          <datafield tag="021A"><subfield code="a">Page One Hellenistic Levant classical studies</subfield></datafield>
        </record>
      </zs:recordData>
    </zs:record>
  </zs:records>
  <zs:nextRecordPosition>21</zs:nextRecordPosition>
</zs:searchRetrieveResponse>
"""

PAGE_TWO = """\
<?xml version="1.0" encoding="UTF-8"?>
<zs:searchRetrieveResponse xmlns:zs="http://www.loc.gov/zing/srw/">
  <zs:numberOfRecords>30</zs:numberOfRecords>
  <zs:records>
    <zs:record>
      <zs:recordData>
        <record xmlns="info:srw/schema/5/picaXML-v1.0">
          <datafield tag="003@"><subfield code="0">222</subfield></datafield>
          <datafield tag="004V"><subfield code="0">10.1000/page2</subfield></datafield>
          <datafield tag="010@"><subfield code="a">eng</subfield></datafield>
          <datafield tag="011@"><subfield code="a">2021</subfield></datafield>
          <datafield tag="021A"><subfield code="a">Page Two Hellenistic Levant classical studies</subfield></datafield>
        </record>
      </zs:recordData>
    </zs:record>
  </zs:records>
</zs:searchRetrieveResponse>
"""


# ---------------------------------------------------------------------------
# Unit tests: _pica_subfield / _pica_all_subfields
# ---------------------------------------------------------------------------

def test_pica_subfield_found():
    r = _make_record({"003@": [("0", "PPN999")]})
    assert _pica_subfield(r, "003@", "0") == "PPN999"


def test_pica_subfield_missing():
    r = _make_record({"003@": [("0", "PPN999")]})
    assert _pica_subfield(r, "021A", "a") is None


def test_pica_all_subfields_multiple():
    r = _make_record({"044K": [("a", "S1"), ("a", "S2"), ("a", "S3")]})
    assert _pica_all_subfields(r, "044K", "a") == ["S1", "S2", "S3"]


# ---------------------------------------------------------------------------
# Unit tests: _pica_author_from_datafield — uppercase and lowercase variants
# ---------------------------------------------------------------------------

def test_author_uppercase_gnd_linked():
    df = ET.Element(f"{{{_NS}}}datafield", {"tag": "028A"})
    for code, text in [("A", "Finkelstein"), ("D", "Israel")]:
        sf = ET.SubElement(df, f"{{{_NS}}}subfield", {"code": code})
        sf.text = text
    assert _pica_author_from_datafield(df) == "Finkelstein, Israel"


def test_author_lowercase_simple():
    df = ET.Element(f"{{{_NS}}}datafield", {"tag": "028A"})
    for code, text in [("a", "Heidenreich"), ("d", "Marianne")]:
        sf = ET.SubElement(df, f"{{{_NS}}}subfield", {"code": code})
        sf.text = text
    assert _pica_author_from_datafield(df) == "Heidenreich, Marianne"


def test_author_family_only():
    df = ET.Element(f"{{{_NS}}}datafield", {"tag": "028A"})
    sf = ET.SubElement(df, f"{{{_NS}}}subfield", {"code": "A"})
    sf.text = "Müller"
    assert _pica_author_from_datafield(df) == "Müller"


def test_author_empty():
    df = ET.Element(f"{{{_NS}}}datafield", {"tag": "028A"})
    assert _pica_author_from_datafield(df) is None


# ---------------------------------------------------------------------------
# Unit tests: _extract_title
# ---------------------------------------------------------------------------

def test_extract_title_main_only():
    r = _make_record({"021A": [("a", "Iron Age Levant")]})
    assert _extract_title(r) == "Iron Age Levant"


def test_extract_title_strips_sort_marker():
    r = _make_record({"021A": [("a", "The @Gnomon Database")]})
    assert _extract_title(r) == "The Gnomon Database"


def test_extract_title_with_subtitle():
    r = _make_record({"021A": [("a", "Gnomon"), ("d", "Einführung")]})
    assert _extract_title(r) == "Gnomon: Einführung"


def test_extract_title_missing():
    r = _make_record({"003@": [("0", "PPN")]})
    assert _extract_title(r) == "(untitled)"


# ---------------------------------------------------------------------------
# Unit tests: _extract_year / _extract_language / _extract_doi
# ---------------------------------------------------------------------------

def test_extract_year():
    r = _make_record({"011@": [("a", "2006")]})
    assert _extract_year(r) == 2006


def test_extract_year_missing():
    r = _make_record({"003@": [("0", "X")]})
    assert _extract_year(r) is None


@pytest.mark.parametrize("iso3,expected", [
    ("eng", "en"), ("ger", "de"), ("deu", "de"), ("fra", "fr"),
    ("lat", "la"), ("grc", "el"), ("heb", "he"),
])
def test_extract_language(iso3, expected):
    r = _make_record({"010@": [("a", iso3)]})
    assert _extract_language(r) == expected


def test_extract_language_missing():
    r = _make_record({})
    assert _extract_language(r) == "und"


def test_extract_doi_bare():
    r = _make_record({"004V": [("0", "10.1086/736852")]})
    assert _extract_doi(r) == "10.1086/736852"


def test_extract_doi_url_fallback():
    r = _make_record({"017C": [("u", "https://doi.org/10.1086/736852")]})
    assert _extract_doi(r) == "10.1086/736852"


def test_extract_doi_bare_priority():
    r = _make_record({
        "004V": [("0", "10.1000/bare")],
        "017C": [("u", "https://doi.org/10.1000/url")],
    })
    assert _extract_doi(r) == "10.1000/bare"


def test_extract_doi_missing():
    r = _make_record({"021A": [("a", "Title")]})
    assert _extract_doi(r) is None


def test_extract_doi_normalises_case():
    r = _make_record({"004V": [("0", "10.1086/UPPER")]})
    assert _extract_doi(r) == "10.1086/upper"


# ---------------------------------------------------------------------------
# Unit tests: _extract_venue
# ---------------------------------------------------------------------------

def test_extract_venue_journal():
    r = _make_record({
        "039B": [("t", "Near Eastern Archaeology")],
        "031A": [("d", "88"), ("e", "3"), ("h", "240-247")],
    })
    v = _extract_venue(r)
    assert v is not None
    assert v.name == "Near Eastern Archaeology"
    assert v.volume == "88"
    assert v.issue == "3"
    assert v.pages == "240-247"


def test_extract_venue_series_fallback():
    r = _make_record({
        "036E": [("a", "Beiträge zur Altertumskunde")],
    })
    v = _extract_venue(r)
    assert v is not None
    assert v.name == "Beiträge zur Altertumskunde"


def test_extract_venue_publisher_fallback():
    r = _make_record({
        "033A": [("p", "München"), ("n", "K.G. Saur")],
    })
    v = _extract_venue(r)
    assert v is not None
    assert v.name == "K.G. Saur"


def test_extract_venue_journal_preferred_over_series():
    r = _make_record({
        "039B": [("t", "Journal Name")],
        "036E": [("a", "Series Name")],
    })
    v = _extract_venue(r)
    assert v is not None
    assert v.name == "Journal Name"


def test_extract_venue_missing():
    r = _make_record({"021A": [("a", "Title")]})
    assert _extract_venue(r) is None


# ---------------------------------------------------------------------------
# Unit tests: _record_to_paper
# ---------------------------------------------------------------------------

def _sample_record_1() -> ET.Element:
    root = ET.fromstring(SAMPLE_SRU_RESPONSE)
    sru = {"zs": "http://www.loc.gov/zing/srw/"}
    pica = {"pica": _NS}
    rd = root.findall(".//zs:recordData", sru)[0]
    return rd.find("pica:record", pica)


def _sample_record_2() -> ET.Element:
    root = ET.fromstring(SAMPLE_SRU_RESPONSE)
    sru = {"zs": "http://www.loc.gov/zing/srw/"}
    pica = {"pica": _NS}
    rd = root.findall(".//zs:recordData", sru)[1]
    return rd.find("pica:record", pica)


def test_record_to_paper_journal_article():
    p = _record_to_paper(_sample_record_1(), [])
    assert p is not None
    assert p.title == "The Contribution of the Megiddo Expedition to Classical Archaeology"
    assert p.authors == ["Finkelstein, Israel", "Adams, Matthew J."]
    assert p.year == 2025
    assert p.doi_or_id == "10.1086/736852"
    assert p.language == "en"
    assert p.source == "gnomon"


def test_record_to_paper_monograph_with_subtitle():
    p = _record_to_paper(_sample_record_2(), [])
    assert p is not None
    assert p.title == "Gnomon und die klassische Altertumswissenschaft: Einführung und Überblick"
    assert p.authors == ["Heidenreich, Marianne"]
    assert p.year == 2006
    assert p.doi_or_id == "gnomon:1884411169"
    assert p.language == "de"


def test_record_to_paper_inline_citation_author_year():
    p = _record_to_paper(_sample_record_1(), [])
    assert p is not None
    ic = p.inline_citation
    assert ic is not None
    assert "Finkelstein & Adams 2025" in ic.markdown
    assert "10.1086/736852" in ic.markdown


def test_record_to_paper_venue_in_bibliography():
    p = _record_to_paper(_sample_record_1(), [])
    assert p is not None
    bib = p.inline_citation.authoritative_bibliography_line
    assert bib is not None
    assert "Near Eastern Archaeology" in bib
    assert "240-247" in bib


def test_record_to_paper_series_venue():
    p = _record_to_paper(_sample_record_2(), [])
    assert p is not None
    assert p.inline_citation is not None


def test_record_to_paper_abstract():
    p = _record_to_paper(_sample_record_1(), [])
    assert p is not None
    assert p.abstract is not None
    assert "Megiddo" in p.abstract


def test_record_to_paper_keyword_match():
    p = _record_to_paper(_sample_record_1(), ["hellenistic", "megiddo"])
    assert p is not None


def test_record_to_paper_keyword_no_match():
    p = _record_to_paper(_sample_record_1(), ["byzantine", "pottery"])
    assert p is None


def test_record_to_paper_no_doi_ppn_fallback():
    r = _make_record({
        "003@": [("0", "PPNXYZ")],
        "010@": [("a", "eng")],
        "011@": [("a", "2010")],
        "021A": [("a", "A classical Hellenistic Levant study")],
    })
    p = _record_to_paper(r, [])
    assert p is not None
    assert p.doi_or_id == "gnomon:PPNXYZ"
    assert p.landing_page_url is None


def test_record_to_paper_no_ppn_returns_none():
    r = _make_record({"021A": [("a", "Title")]})
    assert _record_to_paper(r, []) is None


def test_record_to_paper_landing_url_from_doi():
    p = _record_to_paper(_sample_record_1(), [])
    assert p is not None
    assert str(p.landing_page_url) == "https://doi.org/10.1086/736852"


# ---------------------------------------------------------------------------
# Unit tests: _parse_page
# ---------------------------------------------------------------------------

def test_parse_page_returns_both_records():
    papers, next_pos = _parse_page(SAMPLE_SRU_RESPONSE, [])
    assert len(papers) == 2
    assert next_pos is None


def test_parse_page_english_record():
    papers, _ = _parse_page(SAMPLE_SRU_RESPONSE, [])
    en = next(p for p in papers if p.language == "en")
    assert en.doi_or_id == "10.1086/736852"


def test_parse_page_german_record_subtitle():
    papers, _ = _parse_page(SAMPLE_SRU_RESPONSE, [])
    de = next(p for p in papers if p.language == "de")
    assert "Einführung und Überblick" in de.title


def test_parse_page_empty():
    papers, next_pos = _parse_page(EMPTY_SRU_RESPONSE, [])
    assert papers == []
    assert next_pos is None


def test_parse_page_next_position():
    _, next_pos = _parse_page(PAGE_ONE, [])
    assert next_pos == 21


def test_parse_page_no_next_position():
    _, next_pos = _parse_page(PAGE_TWO, [])
    assert next_pos is None


# ---------------------------------------------------------------------------
# Integration tests: search_gnomon_impl (mocked HTTP)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_search_returns_papers():
    async with respx.mock(base_url=K10PLUS_SRU) as mock:
        mock.get("").respond(200, text=SAMPLE_SRU_RESPONSE)
        papers = await search_gnomon_impl("levant classical", client=httpx.AsyncClient())
    assert len(papers) == 2


@pytest.mark.asyncio
async def test_search_empty():
    async with respx.mock(base_url=K10PLUS_SRU) as mock:
        mock.get("").respond(200, text=EMPTY_SRU_RESPONSE)
        papers = await search_gnomon_impl("xyzzy nonexistent", client=httpx.AsyncClient())
    assert papers == []


@pytest.mark.asyncio
async def test_search_http_error():
    async with respx.mock(base_url=K10PLUS_SRU) as mock:
        mock.get("").respond(503)
        with pytest.raises(httpx.HTTPStatusError):
            await search_gnomon_impl("classical archaeology", client=httpx.AsyncClient())


@pytest.mark.asyncio
async def test_search_pagination():
    call_count = 0

    async def _handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if "startRecord=1" in str(request.url) or "startRecord" not in str(request.url):
            return httpx.Response(200, text=PAGE_ONE)
        return httpx.Response(200, text=PAGE_TWO)

    async with respx.mock(base_url=K10PLUS_SRU) as mock:
        mock.get("").mock(side_effect=_handler)
        papers = await search_gnomon_impl(
            "hellenistic levant classical studies", max_results=10, client=httpx.AsyncClient()
        )
    assert call_count == 2
    assert len(papers) == 2


@pytest.mark.asyncio
async def test_search_max_results():
    async with respx.mock(base_url=K10PLUS_SRU) as mock:
        mock.get("").respond(200, text=SAMPLE_SRU_RESPONSE)
        papers = await search_gnomon_impl("levant classical", max_results=1, client=httpx.AsyncClient())
    assert len(papers) == 1


@pytest.mark.asyncio
async def test_search_year_range_in_cql():
    captured: list[str] = []

    async def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(str(request.url))
        return httpx.Response(200, text=EMPTY_SRU_RESPONSE)

    async with respx.mock(base_url=K10PLUS_SRU) as mock:
        mock.get("").mock(side_effect=_handler)
        await search_gnomon_impl("Levant", year_from=1990, year_to=2010, client=httpx.AsyncClient())
    assert captured
    assert "1990" in captured[0]
    assert "2010" in captured[0]


@pytest.mark.asyncio
async def test_search_source_is_gnomon():
    async with respx.mock(base_url=K10PLUS_SRU) as mock:
        mock.get("").respond(200, text=SAMPLE_SRU_RESPONSE)
        papers = await search_gnomon_impl("levant classical", client=httpx.AsyncClient())
    assert all(p.source == "gnomon" for p in papers)


@pytest.mark.asyncio
async def test_search_audit_flags():
    async with respx.mock(base_url=K10PLUS_SRU) as mock:
        mock.get("").respond(200, text=SAMPLE_SRU_RESPONSE)
        papers = await search_gnomon_impl("levant classical", client=httpx.AsyncClient())
    for p in papers:
        assert p.audit is not None
        assert p.audit.primary_source is True
        assert p.audit.aggregator is False
