"""Tests for the PropylaeumDOK adapter (OAI-PMH / EPrints backend).

Fixtures are shape-faithful reproductions of EPrints 3.4 OAI-PMH Dublin Core
output from similar Heidelberg EPrints repositories. All network I/O is
mocked with respx.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from dao_paper_search_mcp.adapters.propylaeum import (
    PROPYLAEUM_OAI,
    _build_venue_from_source,
    _classify_identifiers,
    _detect_language_from_text,
    _extract_eprints_id,
    _extract_year,
    _parse_language,
    _parse_page,
    _query_tokens,
    _record_matches,
    search_propylaeum_impl,
)
from dao_paper_search_mcp.models import DAOPaper


# ---------------------------------------------------------------------------
# OAI-PMH fixtures — shape-faithful EPrints 3.4 Dublin Core records
# ---------------------------------------------------------------------------

# One German and one English record; the English one has a DOI, the German one
# has only a landing URL (no DOI registered).
SAMPLE_LISTRECORDS = """<?xml version="1.0" encoding="UTF-8"?>
<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">
  <responseDate>2026-05-15T12:00:00Z</responseDate>
  <ListRecords>
    <record>
      <header>
        <identifier>oai:archiv.ub.uni-heidelberg.de:propylaeumdok/7301</identifier>
        <datestamp>2022-03-10T08:15:00Z</datestamp>
      </header>
      <metadata>
        <oai_dc:dc xmlns:oai_dc="http://www.openarchives.org/OAI/2.0/oai_dc/"
                   xmlns:dc="http://purl.org/dc/elements/1.1/">
          <dc:title>Iron Age Settlements in the Northern Negev: New Evidence</dc:title>
          <dc:creator>Finkelstein, Israel</dc:creator>
          <dc:creator>Fantalkin, Alexander</dc:creator>
          <dc:description>A survey of Iron Age IIA settlements in the Beer-Sheba Valley.</dc:description>
          <dc:subject>Iron Age</dc:subject>
          <dc:subject>Negev</dc:subject>
          <dc:subject>Beer-Sheba Valley</dc:subject>
          <dc:date>2022-03-10</dc:date>
          <dc:type>article</dc:type>
          <dc:identifier>https://archiv.ub.uni-heidelberg.de/propylaeumdok/7301/</dc:identifier>
          <dc:identifier>https://doi.org/10.11588/propylaeumdok.7301.g5</dc:identifier>
          <dc:relation>https://archiv.ub.uni-heidelberg.de/propylaeumdok/7301/1/finkelstein_2022.pdf</dc:relation>
          <dc:language>eng</dc:language>
          <dc:source>Tel Aviv; 49; 45-72</dc:source>
        </oai_dc:dc>
      </metadata>
    </record>
    <record>
      <header>
        <identifier>oai:archiv.ub.uni-heidelberg.de:propylaeumdok/4512</identifier>
        <datestamp>2018-06-20T00:00:00Z</datestamp>
      </header>
      <metadata>
        <oai_dc:dc xmlns:oai_dc="http://www.openarchives.org/OAI/2.0/oai_dc/"
                   xmlns:dc="http://purl.org/dc/elements/1.1/">
          <dc:title>Die Bronzezeit in der südlichen Levante: Eine Übersicht</dc:title>
          <dc:creator>Mazar, Amihai</dc:creator>
          <dc:description>Überblick zur mittleren und späten Bronzezeit in der südlichen Levante.</dc:description>
          <dc:subject>Bronzezeit</dc:subject>
          <dc:subject>Levante</dc:subject>
          <dc:date>2018</dc:date>
          <dc:type>bookSection</dc:type>
          <dc:identifier>https://archiv.ub.uni-heidelberg.de/propylaeumdok/4512/</dc:identifier>
          <dc:language>deu</dc:language>
        </oai_dc:dc>
      </metadata>
    </record>
  </ListRecords>
</OAI-PMH>
"""

EMPTY_LISTRECORDS = """<?xml version="1.0" encoding="UTF-8"?>
<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">
  <responseDate>2026-05-15T12:00:00Z</responseDate>
  <ListRecords></ListRecords>
</OAI-PMH>
"""

PAGE_ONE = """<?xml version="1.0" encoding="UTF-8"?>
<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">
  <ListRecords>
    <record>
      <header>
        <identifier>oai:archiv.ub.uni-heidelberg.de:propylaeumdok/100</identifier>
        <datestamp>2020-01-01T00:00:00Z</datestamp>
      </header>
      <metadata>
        <oai_dc:dc xmlns:oai_dc="http://www.openarchives.org/OAI/2.0/oai_dc/"
                   xmlns:dc="http://purl.org/dc/elements/1.1/">
          <dc:title>Levant Bronze Age first article</dc:title>
          <dc:creator>Scholar, A.</dc:creator>
          <dc:date>2020</dc:date>
          <dc:identifier>https://archiv.ub.uni-heidelberg.de/propylaeumdok/100/</dc:identifier>
          <dc:identifier>https://doi.org/10.11588/propylaeumdok.100.x1</dc:identifier>
        </oai_dc:dc>
      </metadata>
    </record>
    <resumptionToken>page-2-token</resumptionToken>
  </ListRecords>
</OAI-PMH>
"""

PAGE_TWO = """<?xml version="1.0" encoding="UTF-8"?>
<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">
  <ListRecords>
    <record>
      <header>
        <identifier>oai:archiv.ub.uni-heidelberg.de:propylaeumdok/101</identifier>
        <datestamp>2020-01-02T00:00:00Z</datestamp>
      </header>
      <metadata>
        <oai_dc:dc xmlns:oai_dc="http://www.openarchives.org/OAI/2.0/oai_dc/"
                   xmlns:dc="http://purl.org/dc/elements/1.1/">
          <dc:title>Levant Bronze Age second article</dc:title>
          <dc:creator>Scholar, B.</dc:creator>
          <dc:date>2020</dc:date>
          <dc:identifier>https://archiv.ub.uni-heidelberg.de/propylaeumdok/101/</dc:identifier>
          <dc:identifier>https://doi.org/10.11588/propylaeumdok.101.x2</dc:identifier>
        </oai_dc:dc>
      </metadata>
    </record>
    <resumptionToken></resumptionToken>
  </ListRecords>
</OAI-PMH>
"""


# ---------------------------------------------------------------------------
# Unit tests — pure functions
# ---------------------------------------------------------------------------

def test_extract_eprints_id_from_url() -> None:
    assert _extract_eprints_id(
        "https://archiv.ub.uni-heidelberg.de/propylaeumdok/7301/"
    ) == "7301"
    assert _extract_eprints_id(
        "https://archiv.ub.uni-heidelberg.de/propylaeumdok/7301/1/file.pdf"
    ) == "7301"
    assert _extract_eprints_id("https://doi.org/10.xxx") is None
    assert _extract_eprints_id(None) is None


def test_classify_identifiers_doi_url_form() -> None:
    ids = [
        "https://archiv.ub.uni-heidelberg.de/propylaeumdok/7301/",
        "https://doi.org/10.11588/propylaeumdok.7301.g5",
        "https://archiv.ub.uni-heidelberg.de/propylaeumdok/7301/1/paper.pdf",
    ]
    doi, landing, pdf = _classify_identifiers(ids)
    assert doi == "10.11588/propylaeumdok.7301.g5"
    assert landing == "https://archiv.ub.uni-heidelberg.de/propylaeumdok/7301/"
    assert pdf is not None and pdf.endswith("paper.pdf")


def test_classify_identifiers_info_doi_form() -> None:
    doi, landing, _ = _classify_identifiers([
        "https://archiv.ub.uni-heidelberg.de/propylaeumdok/42/",
        "info:doi/10.11588/propylaeumdok.42",
    ])
    assert doi == "10.11588/propylaeumdok.42"
    assert landing is not None


def test_classify_identifiers_no_doi() -> None:
    doi, landing, pdf = _classify_identifiers([
        "https://archiv.ub.uni-heidelberg.de/propylaeumdok/4512/"
    ])
    assert doi is None
    assert landing == "https://archiv.ub.uni-heidelberg.de/propylaeumdok/4512/"
    assert pdf is None


def test_classify_identifiers_empty() -> None:
    assert _classify_identifiers([]) == (None, None, None)


def test_parse_language_iso3_codes() -> None:
    assert _parse_language("deu") == "de"
    assert _parse_language("ger") == "de"
    assert _parse_language("eng") == "en"
    assert _parse_language("fra") == "fr"
    assert _parse_language("ita") == "it"
    assert _parse_language("lat") == "la"


def test_parse_language_iso1_passthrough() -> None:
    assert _parse_language("de") == "de"
    assert _parse_language("en") == "en"


def test_parse_language_unknown_returns_und() -> None:
    assert _parse_language(None) == "und"
    assert _parse_language("zzz") == "und"
    assert _parse_language("") == "und"


def test_detect_language_from_text() -> None:
    assert _detect_language_from_text("Bronzezeit in der südlichen Levante") == "de"
    assert _detect_language_from_text("Iron Age settlements") == "en"
    assert _detect_language_from_text("חפירות ארכיאולוגיות בנגב") == "he"
    assert _detect_language_from_text("123 456") == "und"


def test_extract_year_various_formats() -> None:
    assert _extract_year("2022-03-10") == 2022
    assert _extract_year("2018") == 2018
    assert _extract_year("2024-11-26T10:12:05Z") == 2024
    assert _extract_year(None) is None
    assert _extract_year("") is None


def test_query_tokens_normalises() -> None:
    assert _query_tokens("Iron Age Levant") == ["iron", "age", "levant"]
    assert _query_tokens("  ") == []


def test_record_matches_and_semantics() -> None:
    assert _record_matches(["iron", "negev"], "Iron Age site", "Negev survey", [], [])
    assert not _record_matches(["byzantine"], "Iron Age site", "Negev", [], [])
    assert _record_matches(["finkelstein"], "Iron Age", "", [], ["Finkelstein, Israel"])
    assert _record_matches([], "anything", "", [], [])


def test_build_venue_from_source_semicolon_format() -> None:
    v = _build_venue_from_source(["Tel Aviv; 49; 45-72"])
    assert v is not None
    assert v.name == "Tel Aviv"
    assert v.volume == "49"
    assert v.pages == "45-72"


def test_build_venue_from_source_ignores_bare_issn() -> None:
    v = _build_venue_from_source(["1234-5678", "0341-1184"])
    assert v is None


def test_build_venue_from_source_empty() -> None:
    assert _build_venue_from_source([]) is None


# ---------------------------------------------------------------------------
# Parser tests
# ---------------------------------------------------------------------------

def test_parse_page_english_record_with_doi() -> None:
    tokens = _query_tokens("iron age negev")
    matches, token = _parse_page(SAMPLE_LISTRECORDS, tokens)
    assert len(matches) == 1
    p = matches[0]
    assert isinstance(p, DAOPaper)
    assert p.source == "propylaeum"
    assert p.title.startswith("Iron Age Settlements")
    assert p.authors == ["Finkelstein, Israel", "Fantalkin, Alexander"]
    assert p.year == 2022
    assert p.language == "en"
    assert p.identifiers is not None
    assert p.identifiers.doi == "10.11588/propylaeumdok.7301.g5"
    assert p.identifiers.propylaeum_id == "7301"
    assert p.doi_or_id == "10.11588/propylaeumdok.7301.g5"
    assert p.inline_citation is not None
    assert p.inline_citation.markdown == (
        "[(Finkelstein & Fantalkin 2022)](https://doi.org/10.11588/propylaeumdok.7301.g5)"
    )
    assert p.open_access_url is not None
    assert str(p.open_access_url).endswith("finkelstein_2022.pdf")
    assert token is None


def test_parse_page_german_record_no_doi() -> None:
    matches, _ = _parse_page(SAMPLE_LISTRECORDS, [])
    assert len(matches) == 2
    german = matches[1]
    assert german.language == "de"
    assert german.year == 2018
    assert german.authors == ["Mazar, Amihai"]
    assert german.identifiers is not None
    assert german.identifiers.doi is None
    assert german.identifiers.propylaeum_id == "4512"
    assert german.doi_or_id == "propylaeum:4512"
    # No DOI → landing URL used
    assert german.inline_citation is not None
    assert "archiv.ub.uni-heidelberg.de" in german.inline_citation.markdown


def test_parse_page_keyword_filter_excludes_non_matching() -> None:
    matches, _ = _parse_page(SAMPLE_LISTRECORDS, _query_tokens("byzantine pottery"))
    assert matches == []


def test_parse_page_empty() -> None:
    matches, token = _parse_page(EMPTY_LISTRECORDS, [])
    assert matches == []
    assert token is None


def test_parse_page_resumption_token() -> None:
    _, token = _parse_page(PAGE_ONE, [])
    assert token == "page-2-token"
    _, no_token = _parse_page(PAGE_TWO, [])
    assert no_token is None


def test_parse_page_skips_deleted_records() -> None:
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">
  <ListRecords>
    <record>
      <header status="deleted">
        <identifier>oai:archiv.ub.uni-heidelberg.de:propylaeumdok/999</identifier>
        <datestamp>2020-01-01T00:00:00Z</datestamp>
      </header>
    </record>
  </ListRecords>
</OAI-PMH>
"""
    matches, _ = _parse_page(xml, [])
    assert matches == []


def test_parse_page_record_without_url_dropped() -> None:
    """Records with no landing URL and no DOI are silently dropped."""
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">
  <ListRecords>
    <record>
      <header>
        <identifier>oai:archiv.ub.uni-heidelberg.de:propylaeumdok/999</identifier>
        <datestamp>2020-01-01T00:00:00Z</datestamp>
      </header>
      <metadata>
        <oai_dc:dc xmlns:oai_dc="http://www.openarchives.org/OAI/2.0/oai_dc/"
                   xmlns:dc="http://purl.org/dc/elements/1.1/">
          <dc:title>A record with no URL at all</dc:title>
          <dc:creator>Ghost, Author</dc:creator>
          <dc:date>2020</dc:date>
        </oai_dc:dc>
      </metadata>
    </record>
  </ListRecords>
</OAI-PMH>
"""
    matches, _ = _parse_page(xml, [])
    assert matches == []


# ---------------------------------------------------------------------------
# Integration tests (mocked HTTP)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_search_propylaeum_impl_happy_path() -> None:
    respx.get(PROPYLAEUM_OAI).mock(
        return_value=httpx.Response(200, text=SAMPLE_LISTRECORDS)
    )
    results = await search_propylaeum_impl("iron age negev", max_results=5)
    assert len(results) == 1
    p = results[0]
    assert isinstance(p, DAOPaper)
    assert p.source == "propylaeum"
    assert p.inline_citation is not None
    assert "Finkelstein" in p.inline_citation.markdown


@pytest.mark.asyncio
@respx.mock
async def test_search_propylaeum_impl_empty() -> None:
    respx.get(PROPYLAEUM_OAI).mock(
        return_value=httpx.Response(200, text=EMPTY_LISTRECORDS)
    )
    assert await search_propylaeum_impl("xyzzy") == []


@pytest.mark.asyncio
@respx.mock
async def test_search_propylaeum_impl_http_error_propagates() -> None:
    respx.get(PROPYLAEUM_OAI).mock(return_value=httpx.Response(503))
    with pytest.raises(httpx.HTTPStatusError):
        await search_propylaeum_impl("anything")


@pytest.mark.asyncio
@respx.mock
async def test_search_propylaeum_impl_paginates() -> None:
    call_count = {"n": 0}

    def respond(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        body = PAGE_ONE if call_count["n"] == 1 else PAGE_TWO
        return httpx.Response(200, text=body)

    respx.get(PROPYLAEUM_OAI).mock(side_effect=respond)
    results = await search_propylaeum_impl("levant bronze age", max_results=10)
    assert call_count["n"] == 2
    assert len(results) == 2
    dois = {p.identifiers.doi for p in results if p.identifiers}
    assert dois == {"10.11588/propylaeumdok.100.x1", "10.11588/propylaeumdok.101.x2"}


@pytest.mark.asyncio
@respx.mock
async def test_search_propylaeum_impl_stops_at_max_results() -> None:
    call_count = {"n": 0}

    def respond(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return httpx.Response(200, text=PAGE_ONE)

    respx.get(PROPYLAEUM_OAI).mock(side_effect=respond)
    results = await search_propylaeum_impl("levant bronze age", max_results=1)
    assert len(results) == 1
    assert call_count["n"] == 1


@pytest.mark.asyncio
@respx.mock
async def test_search_propylaeum_impl_year_params_forwarded() -> None:
    route = respx.get(PROPYLAEUM_OAI).mock(
        return_value=httpx.Response(200, text=EMPTY_LISTRECORDS)
    )
    await search_propylaeum_impl("anything", max_results=5, year_from=2010, year_to=2020)
    assert route.called
    sent_url = str(route.calls.last.request.url)
    assert "from=2010-01-01" in sent_url
    assert "until=2020-12-31" in sent_url


@pytest.mark.asyncio
@respx.mock
async def test_search_propylaeum_impl_no_set_filter() -> None:
    """PropylaeumDOK is queried without a set filter (no set= param)."""
    route = respx.get(PROPYLAEUM_OAI).mock(
        return_value=httpx.Response(200, text=EMPTY_LISTRECORDS)
    )
    await search_propylaeum_impl("bronze age")
    assert route.called
    sent_url = str(route.calls.last.request.url)
    assert "set=" not in sent_url
