"""Tests for the IxTheo adapter (BSZ SRU / picaxml backend)."""

from __future__ import annotations

import pytest
import respx
import httpx

from dao_paper_search_mcp.adapters.ixtheo import (
    _clean_title,
    _extract_authors,
    _extract_doi,
    _extract_language,
    _extract_venue,
    _extract_year,
    _pica_subfield,
    _pica_all_subfields,
    _parse_page,
    _record_to_paper,
    search_ixtheo_impl,
    BSZ_SRU,
)

import xml.etree.ElementTree as ET

# ---------------------------------------------------------------------------
# Shared XML namespace declaration for PICA records in test fixtures
# ---------------------------------------------------------------------------

_PICA_XMLNS = 'xmlns="info:srw/schema/5/picaXML-v1.0"'
_PICA_NS = {"pica": "info:srw/schema/5/picaXML-v1.0"}

# ---------------------------------------------------------------------------
# Realistic fixture: SRU response with two records
#   1. English article with DOI, authors, venue, abstract
#   2. German monograph chapter without DOI
# ---------------------------------------------------------------------------

SAMPLE_SRU_RESPONSE = """\
<?xml version="1.0" encoding="UTF-8"?>
<zs:searchRetrieveResponse xmlns:zs="http://www.loc.gov/zing/srw/">
  <zs:version>1.1</zs:version>
  <zs:numberOfRecords>2</zs:numberOfRecords>
  <zs:records>

    <zs:record>
      <zs:recordSchema>picaxml</zs:recordSchema>
      <zs:recordData>
        <record xmlns="info:srw/schema/5/picaXML-v1.0">
          <datafield tag="003@">
            <subfield code="0">1234567890</subfield>
          </datafield>
          <datafield tag="004V">
            <subfield code="0">10.1093/jaos/141.2.345</subfield>
          </datafield>
          <datafield tag="010@">
            <subfield code="a">eng</subfield>
          </datafield>
          <datafield tag="011@">
            <subfield code="a">2021</subfield>
          </datafield>
          <datafield tag="021A">
            <subfield code="a">@Archaeology and the @Hebrew Bible: Iron Age sites in the Levant</subfield>
          </datafield>
          <datafield tag="028A">
            <subfield code="A">Maeir</subfield>
            <subfield code="D">Aren M.</subfield>
          </datafield>
          <datafield tag="028C">
            <subfield code="A">Asscher</subfield>
            <subfield code="D">Yotam</subfield>
          </datafield>
          <datafield tag="031A">
            <subfield code="d">141</subfield>
            <subfield code="e">2</subfield>
            <subfield code="h">345-360</subfield>
          </datafield>
          <datafield tag="039B">
            <subfield code="t">Journal of the American Oriental Society</subfield>
          </datafield>
          <datafield tag="044K">
            <subfield code="a">Levantine archaeology</subfield>
          </datafield>
          <datafield tag="044K">
            <subfield code="a">Iron Age</subfield>
          </datafield>
          <datafield tag="047I">
            <subfield code="a">This article surveys Iron Age sites in the southern Levant with focus on stratigraphic correlations.</subfield>
          </datafield>
        </record>
      </zs:recordData>
    </zs:record>

    <zs:record>
      <zs:recordSchema>picaxml</zs:recordSchema>
      <zs:recordData>
        <record xmlns="info:srw/schema/5/picaXML-v1.0">
          <datafield tag="003@">
            <subfield code="0">9876543210</subfield>
          </datafield>
          <datafield tag="010@">
            <subfield code="a">deu</subfield>
          </datafield>
          <datafield tag="011@">
            <subfield code="a">2018</subfield>
          </datafield>
          <datafield tag="021A">
            <subfield code="a">@Biblische @Archäologie und Altes Testament: Überblick</subfield>
          </datafield>
          <datafield tag="028A">
            <subfield code="A">Zwickel</subfield>
            <subfield code="D">Wolfgang</subfield>
          </datafield>
          <datafield tag="039B">
            <subfield code="t">Theologische Rundschau</subfield>
          </datafield>
          <datafield tag="044K">
            <subfield code="a">Biblische Archäologie</subfield>
          </datafield>
          <datafield tag="047I">
            <subfield code="a">Survey of German-language research on Levant archaeology and biblical studies.</subfield>
          </datafield>
        </record>
      </zs:recordData>
    </zs:record>

  </zs:records>
</zs:searchRetrieveResponse>
"""

EMPTY_SRU_RESPONSE = """\
<?xml version="1.0" encoding="UTF-8"?>
<zs:searchRetrieveResponse xmlns:zs="http://www.loc.gov/zing/srw/">
  <zs:version>1.1</zs:version>
  <zs:numberOfRecords>0</zs:numberOfRecords>
  <zs:records/>
</zs:searchRetrieveResponse>
"""

# Two pages for pagination test. Page 1 has nextRecordPosition=21.
PAGE_ONE = """\
<?xml version="1.0" encoding="UTF-8"?>
<zs:searchRetrieveResponse xmlns:zs="http://www.loc.gov/zing/srw/">
  <zs:numberOfRecords>25</zs:numberOfRecords>
  <zs:records>
    <zs:record>
      <zs:recordData>
        <record xmlns="info:srw/schema/5/picaXML-v1.0">
          <datafield tag="003@"><subfield code="0">111</subfield></datafield>
          <datafield tag="004V"><subfield code="0">10.1000/page1-rec1</subfield></datafield>
          <datafield tag="011@"><subfield code="a">2020</subfield></datafield>
          <datafield tag="010@"><subfield code="a">eng</subfield></datafield>
          <datafield tag="021A"><subfield code="a">Page One Record One: Levant archaeology in the Iron Age</subfield></datafield>
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
  <zs:numberOfRecords>25</zs:numberOfRecords>
  <zs:records>
    <zs:record>
      <zs:recordData>
        <record xmlns="info:srw/schema/5/picaXML-v1.0">
          <datafield tag="003@"><subfield code="0">222</subfield></datafield>
          <datafield tag="004V"><subfield code="0">10.1000/page2-rec1</subfield></datafield>
          <datafield tag="011@"><subfield code="a">2021</subfield></datafield>
          <datafield tag="010@"><subfield code="a">eng</subfield></datafield>
          <datafield tag="021A"><subfield code="a">Page Two Record One: Levant archaeology and biblical texts</subfield></datafield>
        </record>
      </zs:recordData>
    </zs:record>
  </zs:records>
</zs:searchRetrieveResponse>
"""

SRU_ERROR_RESPONSE = """\
<?xml version="1.0" encoding="UTF-8"?>
<zs:searchRetrieveResponse xmlns:zs="http://www.loc.gov/zing/srw/">
  <zs:diagnostics>
    <diag:diagnostic xmlns:diag="http://www.loc.gov/zing/srw/diagnostic/">
      <diag:uri>info:srw/diagnostic/1/4</diag:uri>
      <diag:message>Unsupported Operation</diag:message>
    </diag:diagnostic>
  </zs:diagnostics>
</zs:searchRetrieveResponse>
"""


# ---------------------------------------------------------------------------
# Helper: build a minimal PICA record element from a dict of {tag: {code: text}}
# ---------------------------------------------------------------------------

def _make_pica_record(fields: dict) -> ET.Element:
    """Build a PICA record element for unit tests.

    fields: {tag: [(code, text), ...]} or {tag: {code: text}}
    """
    ns = "info:srw/schema/5/picaXML-v1.0"
    record = ET.Element(f"{{{ns}}}record")
    for tag, subcodes in fields.items():
        if isinstance(subcodes, dict):
            subcodes = list(subcodes.items())
        df = ET.SubElement(record, f"{{{ns}}}datafield", {"tag": tag})
        for code, text in subcodes:
            sf = ET.SubElement(df, f"{{{ns}}}subfield", {"code": code})
            sf.text = text
    return record


# ---------------------------------------------------------------------------
# Unit tests: _clean_title
# ---------------------------------------------------------------------------

def test_clean_title_strips_sort_marker():
    assert _clean_title("@Archaeology and the @Hebrew Bible") == "Archaeology and the Hebrew Bible"


def test_clean_title_no_marker():
    assert _clean_title("Iron Age Levant") == "Iron Age Levant"


def test_clean_title_empty():
    assert _clean_title("") == ""


# ---------------------------------------------------------------------------
# Unit tests: _pica_subfield / _pica_all_subfields
# ---------------------------------------------------------------------------

def test_pica_subfield_found():
    r = _make_pica_record({"003@": [("0", "PPN123")]})
    assert _pica_subfield(r, "003@", "0") == "PPN123"


def test_pica_subfield_missing_tag():
    r = _make_pica_record({"003@": [("0", "PPN123")]})
    assert _pica_subfield(r, "021A", "a") is None


def test_pica_subfield_missing_code():
    r = _make_pica_record({"021A": [("a", "Title")]})
    assert _pica_subfield(r, "021A", "b") is None


def test_pica_all_subfields_multiple():
    r = _make_pica_record({"044K": [("a", "Subject1"), ("a", "Subject2")]})
    # Two subfields on the same datafield
    assert _pica_all_subfields(r, "044K", "a") == ["Subject1", "Subject2"]


# ---------------------------------------------------------------------------
# Unit tests: _extract_authors
# ---------------------------------------------------------------------------

def test_extract_authors_single():
    r = _make_pica_record({"028A": [("A", "Maeir"), ("D", "Aren M.")]})
    assert _extract_authors(r) == ["Maeir, Aren M."]


def test_extract_authors_two():
    ns = "info:srw/schema/5/picaXML-v1.0"
    record = ET.Element(f"{{{ns}}}record")
    for tag, family, given in [("028A", "Maeir", "Aren M."), ("028C", "Asscher", "Yotam")]:
        df = ET.SubElement(record, f"{{{ns}}}datafield", {"tag": tag})
        for code, text in [("A", family), ("D", given)]:
            sf = ET.SubElement(df, f"{{{ns}}}subfield", {"code": code})
            sf.text = text
    authors = _extract_authors(record)
    assert authors == ["Maeir, Aren M.", "Asscher, Yotam"]


def test_extract_authors_family_only():
    r = _make_pica_record({"028A": [("A", "Maeir")]})
    assert _extract_authors(r) == ["Maeir"]


def test_extract_authors_empty():
    r = _make_pica_record({"021A": [("a", "Title")]})
    assert _extract_authors(r) == []


# ---------------------------------------------------------------------------
# Unit tests: _extract_year
# ---------------------------------------------------------------------------

def test_extract_year_bare():
    r = _make_pica_record({"011@": [("a", "2021")]})
    assert _extract_year(r) == 2021


def test_extract_year_in_string():
    r = _make_pica_record({"011@": [("a", "2021-05")]})
    assert _extract_year(r) == 2021


def test_extract_year_missing():
    r = _make_pica_record({"021A": [("a", "Title")]})
    assert _extract_year(r) is None


# ---------------------------------------------------------------------------
# Unit tests: _extract_language
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("iso3,expected", [
    ("eng", "en"),
    ("deu", "de"),
    ("ger", "de"),
    ("fra", "fr"),
    ("lat", "la"),
    ("heb", "he"),
    ("ara", "ar"),
])
def test_extract_language_iso3(iso3, expected):
    r = _make_pica_record({"010@": [("a", iso3)]})
    assert _extract_language(r) == expected


def test_extract_language_missing():
    r = _make_pica_record({"021A": [("a", "Title")]})
    assert _extract_language(r) == "und"


def test_extract_language_unknown_iso3():
    r = _make_pica_record({"010@": [("a", "zxx")]})
    assert _extract_language(r) == "und"


# ---------------------------------------------------------------------------
# Unit tests: _extract_doi
# ---------------------------------------------------------------------------

def test_extract_doi_bare_004v():
    r = _make_pica_record({"004V": [("0", "10.1093/jaos/141.2.345")]})
    assert _extract_doi(r) == "10.1093/jaos/141.2.345"


def test_extract_doi_url_017c():
    r = _make_pica_record({"017C": [("u", "https://doi.org/10.1093/jaos/141.2.345")]})
    assert _extract_doi(r) == "10.1093/jaos/141.2.345"


def test_extract_doi_004v_priority_over_017c():
    ns = "info:srw/schema/5/picaXML-v1.0"
    record = ET.Element(f"{{{ns}}}record")
    for tag, code, text in [("004V", "0", "10.1000/bare"), ("017C", "u", "https://doi.org/10.1000/url")]:
        df = ET.SubElement(record, f"{{{ns}}}datafield", {"tag": tag})
        sf = ET.SubElement(df, f"{{{ns}}}subfield", {"code": code})
        sf.text = text
    assert _extract_doi(record) == "10.1000/bare"


def test_extract_doi_missing():
    r = _make_pica_record({"021A": [("a", "Title")]})
    assert _extract_doi(r) is None


def test_extract_doi_normalises_case():
    r = _make_pica_record({"004V": [("0", "10.1093/JAOS/141.2.345")]})
    assert _extract_doi(r) == "10.1093/jaos/141.2.345"


# ---------------------------------------------------------------------------
# Unit tests: _extract_venue
# ---------------------------------------------------------------------------

def test_extract_venue_full():
    ns = "info:srw/schema/5/picaXML-v1.0"
    record = ET.Element(f"{{{ns}}}record")
    for tag, code, text in [
        ("039B", "t", "Journal of the American Oriental Society"),
        ("031A", "d", "141"),
        ("031A", "e", "2"),
        ("031A", "h", "345-360"),
    ]:
        df = ET.SubElement(record, f"{{{ns}}}datafield", {"tag": tag})
        sf = ET.SubElement(df, f"{{{ns}}}subfield", {"code": code})
        sf.text = text
    v = _extract_venue(record)
    assert v is not None
    assert v.name == "Journal of the American Oriental Society"
    assert v.volume == "141"
    assert v.issue == "2"
    assert v.pages == "345-360"


def test_extract_venue_journal_only():
    r = _make_pica_record({"039B": [("t", "Theologische Rundschau")]})
    v = _extract_venue(r)
    assert v is not None
    assert v.name == "Theologische Rundschau"
    assert v.volume is None
    assert v.pages is None


def test_extract_venue_missing():
    r = _make_pica_record({"021A": [("a", "Title")]})
    assert _extract_venue(r) is None


# ---------------------------------------------------------------------------
# Unit tests: _record_to_paper
# ---------------------------------------------------------------------------

def _sample_record_element() -> ET.Element:
    """Return the first pica:record element from SAMPLE_SRU_RESPONSE."""
    root = ET.fromstring(SAMPLE_SRU_RESPONSE)
    sru_ns = {"zs": "http://www.loc.gov/zing/srw/"}
    pica_ns = {"pica": "info:srw/schema/5/picaXML-v1.0"}
    rd = root.find(".//zs:recordData", sru_ns)
    return rd.find("pica:record", pica_ns)


def test_record_to_paper_basic():
    record = _sample_record_element()
    paper = _record_to_paper(record, [])
    assert paper is not None
    assert paper.title == "Archaeology and the Hebrew Bible: Iron Age sites in the Levant"
    assert paper.authors == ["Maeir, Aren M.", "Asscher, Yotam"]
    assert paper.year == 2021
    assert paper.doi_or_id == "10.1093/jaos/141.2.345"
    assert paper.source == "ixtheo"
    assert paper.language == "en"


def test_record_to_paper_inline_citation_markdown():
    record = _sample_record_element()
    paper = _record_to_paper(record, [])
    assert paper is not None
    assert paper.inline_citation is not None
    assert "Maeir & Asscher 2021" in paper.inline_citation.markdown
    assert "10.1093/jaos/141.2.345" in paper.inline_citation.markdown


def test_record_to_paper_venue_populated():
    record = _sample_record_element()
    paper = _record_to_paper(record, [])
    assert paper is not None
    assert paper.inline_citation is not None
    bib = paper.inline_citation.authoritative_bibliography_line
    assert bib is not None
    assert "345-360" in bib


def test_record_to_paper_abstract():
    record = _sample_record_element()
    paper = _record_to_paper(record, [])
    assert paper is not None
    assert paper.abstract is not None
    assert "Iron Age" in paper.abstract


def test_record_to_paper_keyword_filter_match():
    record = _sample_record_element()
    paper = _record_to_paper(record, ["iron", "age"])
    assert paper is not None


def test_record_to_paper_keyword_filter_no_match():
    record = _sample_record_element()
    paper = _record_to_paper(record, ["byzantine", "pottery"])
    assert paper is None


def test_record_to_paper_no_doi_uses_ppn():
    r = _make_pica_record({
        "003@": [("0", "PPNABC")],
        "021A": [("a", "Title without DOI about biblical studies")],
        "010@": [("a", "eng")],
        "011@": [("a", "2020")],
    })
    paper = _record_to_paper(r, [])
    assert paper is not None
    assert paper.doi_or_id == "ixtheo:PPNABC"
    assert paper.landing_page_url is None


def test_record_to_paper_no_ppn_no_doi_returns_none():
    r = _make_pica_record({"021A": [("a", "Title")]})
    assert _record_to_paper(r, []) is None


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
    assert en.doi_or_id == "10.1093/jaos/141.2.345"
    assert en.inline_citation is not None
    assert "Maeir & Asscher 2021" in en.inline_citation.markdown


def test_parse_page_german_record():
    papers, _ = _parse_page(SAMPLE_SRU_RESPONSE, [])
    de = next(p for p in papers if p.language == "de")
    assert de.doi_or_id == "ixtheo:9876543210"
    assert de.year == 2018


def test_parse_page_empty_response():
    papers, next_pos = _parse_page(EMPTY_SRU_RESPONSE, [])
    assert papers == []
    assert next_pos is None


def test_parse_page_returns_next_position():
    papers, next_pos = _parse_page(PAGE_ONE, [])
    assert next_pos == 21
    assert len(papers) == 1


def test_parse_page_no_next_when_absent():
    _, next_pos = _parse_page(PAGE_TWO, [])
    assert next_pos is None


# ---------------------------------------------------------------------------
# Integration tests: search_ixtheo_impl (mocked HTTP)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_search_returns_papers():
    async with respx.mock(base_url=BSZ_SRU) as mock:
        mock.get("").respond(200, text=SAMPLE_SRU_RESPONSE)
        papers = await search_ixtheo_impl("levant archaeology", client=httpx.AsyncClient())
    assert len(papers) == 2


@pytest.mark.asyncio
async def test_search_empty_result():
    async with respx.mock(base_url=BSZ_SRU) as mock:
        mock.get("").respond(200, text=EMPTY_SRU_RESPONSE)
        papers = await search_ixtheo_impl("xyzzy unknown query", client=httpx.AsyncClient())
    assert papers == []


@pytest.mark.asyncio
async def test_search_http_error_raises():
    async with respx.mock(base_url=BSZ_SRU) as mock:
        mock.get("").respond(503)
        with pytest.raises(httpx.HTTPStatusError):
            await search_ixtheo_impl("levant archaeology", client=httpx.AsyncClient())


@pytest.mark.asyncio
async def test_search_pagination():
    call_count = 0

    async def _handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if "startRecord=1" in str(request.url) or "startRecord" not in str(request.url):
            return httpx.Response(200, text=PAGE_ONE)
        return httpx.Response(200, text=PAGE_TWO)

    async with respx.mock(base_url=BSZ_SRU) as mock:
        mock.get("").mock(side_effect=_handler)
        papers = await search_ixtheo_impl(
            "levant archaeology", max_results=10, client=httpx.AsyncClient()
        )
    assert call_count == 2
    assert len(papers) == 2


@pytest.mark.asyncio
async def test_search_max_results_respected():
    async with respx.mock(base_url=BSZ_SRU) as mock:
        mock.get("").respond(200, text=SAMPLE_SRU_RESPONSE)
        papers = await search_ixtheo_impl("levant archaeology", max_results=1, client=httpx.AsyncClient())
    assert len(papers) == 1


@pytest.mark.asyncio
async def test_search_year_range_included_in_cql():
    captured: list[str] = []

    async def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(str(request.url))
        return httpx.Response(200, text=EMPTY_SRU_RESPONSE)

    async with respx.mock(base_url=BSZ_SRU) as mock:
        mock.get("").mock(side_effect=_handler)
        await search_ixtheo_impl(
            "Levant", year_from=2000, year_to=2010, client=httpx.AsyncClient()
        )
    assert captured
    # CQL year filter should appear URL-encoded in the query param
    assert "2000" in captured[0]
    assert "2010" in captured[0]


@pytest.mark.asyncio
async def test_search_source_field():
    async with respx.mock(base_url=BSZ_SRU) as mock:
        mock.get("").respond(200, text=SAMPLE_SRU_RESPONSE)
        papers = await search_ixtheo_impl("levant archaeology", client=httpx.AsyncClient())
    assert all(p.source == "ixtheo" for p in papers)


@pytest.mark.asyncio
async def test_search_audit_primary_source():
    async with respx.mock(base_url=BSZ_SRU) as mock:
        mock.get("").respond(200, text=SAMPLE_SRU_RESPONSE)
        papers = await search_ixtheo_impl("levant archaeology", client=httpx.AsyncClient())
    english = next(p for p in papers if p.language == "en")
    assert english.audit is not None
    assert english.audit.primary_source is True
    assert english.audit.aggregator is False
