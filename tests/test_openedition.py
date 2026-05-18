"""Tests for the OpenEdition adapter (OAI-PMH backend).

Fixtures are shape-faithful reproductions of live OpenEdition OAI-PMH
responses probed 2026-05-18. All network I/O is mocked with respx.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from dao_paper_search_mcp.adapters.openedition import (
    OPENEDITION_OAI,
    _classify_identifiers,
    _extract_texts,
    _extract_year,
    _parse_page,
    _query_tokens,
    _record_matches,
    _resolve_set_spec,
    search_openedition_impl,
)
from dao_paper_search_mcp.models import DAOPaper


# ---------------------------------------------------------------------------
# Fixtures — shape-faithful OpenEdition oai_dc records (probed 2026-05-18)
# ---------------------------------------------------------------------------

# One French article with DOI + multilingual metadata; one English article
# with DOI. Both are journal records with the real identifier structure.
SAMPLE_LISTRECORDS = """<?xml version="1.0" encoding="UTF-8"?>
<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">
  <responseDate>2026-05-18T12:00:00Z</responseDate>
  <ListRecords>
    <record>
      <header>
        <identifier>20.500.13089/lr42</identifier>
        <datestamp>2020-01-14T22:39:10Z</datestamp>
        <setSpec>journals</setSpec>
        <setSpec>journals:vertigo</setSpec>
      </header>
      <metadata>
        <oai_dc:dc xmlns:oai_dc="http://www.openarchives.org/OAI/2.0/oai_dc/"
                   xmlns:dc="http://purl.org/dc/elements/1.1/">
          <dc:identifier>https://doi.org/10.4000/vertigo.26537</dc:identifier>
          <dc:identifier>https://hdl.handle.net/20.500.13089/lr42</dc:identifier>
          <dc:identifier>https://journals.openedition.org/vertigo/26537</dc:identifier>
          <dc:title>Bronze Age coastal settlements in the southern Levant</dc:title>
          <dc:creator>Rey-Valette, Hélène</dc:creator>
          <dc:creator>Rocle, Nicolas</dc:creator>
          <dc:description xml:lang="fr">Étude des établissements côtiers de l'âge du Bronze.</dc:description>
          <dc:description xml:lang="en">Study of Bronze Age coastal settlements in the southern Levant.</dc:description>
          <dc:subject xml:lang="en">Bronze Age</dc:subject>
          <dc:subject xml:lang="en">Levant</dc:subject>
          <dc:subject xml:lang="fr">Âge du Bronze</dc:subject>
          <dc:date>2019</dc:date>
          <dc:date>info:eu-repo/date/publication/2019-12-08</dc:date>
          <dc:type>article</dc:type>
          <dc:language>fr</dc:language>
        </oai_dc:dc>
      </metadata>
    </record>
    <record>
      <header>
        <identifier>20.500.13089/ab12</identifier>
        <datestamp>2021-06-01T00:00:00Z</datestamp>
        <setSpec>journals</setSpec>
        <setSpec>journals:syria</setSpec>
      </header>
      <metadata>
        <oai_dc:dc xmlns:oai_dc="http://www.openarchives.org/OAI/2.0/oai_dc/"
                   xmlns:dc="http://purl.org/dc/elements/1.1/">
          <dc:identifier>https://doi.org/10.4000/syria.12345</dc:identifier>
          <dc:identifier>https://hdl.handle.net/20.500.13089/ab12</dc:identifier>
          <dc:identifier>https://journals.openedition.org/syria/12345</dc:identifier>
          <dc:title>Iron Age pottery from Tell es-Safi</dc:title>
          <dc:creator>Maeir, Aren M.</dc:creator>
          <dc:description xml:lang="en">A typological analysis of Iron Age IIA pottery assemblages from Tell es-Safi/Gath.</dc:description>
          <dc:subject xml:lang="en">Iron Age</dc:subject>
          <dc:subject xml:lang="en">pottery</dc:subject>
          <dc:subject xml:lang="en">Tell es-Safi</dc:subject>
          <dc:subject xml:lang="en">Philistia</dc:subject>
          <dc:date>2021</dc:date>
          <dc:type>article</dc:type>
          <dc:language>en</dc:language>
        </oai_dc:dc>
      </metadata>
    </record>
  </ListRecords>
</OAI-PMH>
"""

EMPTY_LISTRECORDS = """<?xml version="1.0" encoding="UTF-8"?>
<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">
  <responseDate>2026-05-18T12:00:00Z</responseDate>
  <ListRecords></ListRecords>
</OAI-PMH>
"""

PAGE_ONE = """<?xml version="1.0" encoding="UTF-8"?>
<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">
  <ListRecords>
    <record>
      <header>
        <identifier>20.500.13089/p001</identifier>
        <datestamp>2022-01-01T00:00:00Z</datestamp>
        <setSpec>journals</setSpec>
      </header>
      <metadata>
        <oai_dc:dc xmlns:oai_dc="http://www.openarchives.org/OAI/2.0/oai_dc/"
                   xmlns:dc="http://purl.org/dc/elements/1.1/">
          <dc:identifier>https://doi.org/10.4000/test.1001</dc:identifier>
          <dc:identifier>https://journals.openedition.org/test/1001</dc:identifier>
          <dc:title>Levant archaeology first article</dc:title>
          <dc:creator>Scholar, A.</dc:creator>
          <dc:date>2022</dc:date>
          <dc:language>en</dc:language>
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
        <identifier>20.500.13089/p002</identifier>
        <datestamp>2022-01-02T00:00:00Z</datestamp>
        <setSpec>journals</setSpec>
      </header>
      <metadata>
        <oai_dc:dc xmlns:oai_dc="http://www.openarchives.org/OAI/2.0/oai_dc/"
                   xmlns:dc="http://purl.org/dc/elements/1.1/">
          <dc:identifier>https://doi.org/10.4000/test.1002</dc:identifier>
          <dc:identifier>https://journals.openedition.org/test/1002</dc:identifier>
          <dc:title>Levant archaeology second article</dc:title>
          <dc:creator>Scholar, B.</dc:creator>
          <dc:date>2022</dc:date>
          <dc:language>en</dc:language>
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

def test_resolve_set_spec_defaults_to_journals() -> None:
    assert _resolve_set_spec(None) == "journals"
    assert _resolve_set_spec("") == "journals"
    assert _resolve_set_spec("journals") == "journals"


def test_resolve_set_spec_all_means_no_filter() -> None:
    assert _resolve_set_spec("all") is None


def test_resolve_set_spec_books_and_blogs() -> None:
    assert _resolve_set_spec("books") == "books"
    assert _resolve_set_spec("blogs") == "blogs"


def test_resolve_set_spec_passthrough_for_unknown() -> None:
    assert _resolve_set_spec("journals:syria") == "journals:syria"


def test_classify_identifiers_doi_url_form() -> None:
    ids = [
        "https://doi.org/10.4000/vertigo.26537",
        "https://hdl.handle.net/20.500.13089/lr42",
        "https://journals.openedition.org/vertigo/26537",
    ]
    doi, landing = _classify_identifiers(ids)
    assert doi == "10.4000/vertigo.26537"
    assert landing == "https://journals.openedition.org/vertigo/26537"


def test_classify_identifiers_handle_fallback_when_no_openedition_url() -> None:
    ids = [
        "https://doi.org/10.4000/test.1",
        "https://hdl.handle.net/20.500.13089/abc",
    ]
    doi, landing = _classify_identifiers(ids)
    assert doi == "10.4000/test.1"
    assert landing == "https://hdl.handle.net/20.500.13089/abc"


def test_classify_identifiers_info_uri_skipped() -> None:
    ids = [
        "info:eu-repo/semantics/openAccess",
        "urn:issn:1492-8442",
        "https://journals.openedition.org/test/42",
    ]
    doi, landing = _classify_identifiers(ids)
    assert doi is None
    assert landing == "https://journals.openedition.org/test/42"


def test_classify_identifiers_empty() -> None:
    assert _classify_identifiers([]) == (None, None)


def test_extract_year_bare_year() -> None:
    assert _extract_year(["2019"]) == 2019
    assert _extract_year(["2021"]) == 2021


def test_extract_year_prefers_bare_over_info_uri() -> None:
    assert _extract_year(["2019", "info:eu-repo/date/publication/2019-12-08"]) == 2019


def test_extract_year_falls_back_to_info_uri() -> None:
    assert _extract_year(["info:eu-repo/date/publication/2018-03-15"]) == 2018


def test_extract_year_empty() -> None:
    assert _extract_year([]) is None
    assert _extract_year(["info:eu-repo/semantics/article"]) is None


def test_extract_texts_filters_empty() -> None:
    import xml.etree.ElementTree as ET
    el1 = ET.fromstring("<dc:creator xmlns:dc='http://purl.org/dc/elements/1.1/'>Cohen, R.</dc:creator>")
    el2 = ET.fromstring("<dc:creator xmlns:dc='http://purl.org/dc/elements/1.1/'></dc:creator>")
    assert _extract_texts([el1, el2]) == ["Cohen, R."]


def test_query_tokens_normalises() -> None:
    assert _query_tokens("Iron Age Levant") == ["iron", "age", "levant"]
    assert _query_tokens("  ") == []


def test_record_matches_and_semantics() -> None:
    assert _record_matches(["bronze", "levant"], "Bronze Age Levant", "", [], [])
    assert not _record_matches(["byzantine"], "Bronze Age", "Levant", [], [])
    assert _record_matches(["maeir"], "Iron Age pottery", "", [], ["Maeir, Aren M."])
    assert _record_matches([], "anything", "", [], [])


# ---------------------------------------------------------------------------
# Parser tests
# ---------------------------------------------------------------------------

def test_parse_page_french_article_with_doi() -> None:
    tokens = _query_tokens("bronze age levant")
    matches, token = _parse_page(SAMPLE_LISTRECORDS, tokens)
    assert len(matches) == 1
    p = matches[0]
    assert isinstance(p, DAOPaper)
    assert p.source == "openedition"
    assert p.title == "Bronze Age coastal settlements in the southern Levant"
    assert p.authors == ["Rey-Valette, Hélène", "Rocle, Nicolas"]
    assert p.year == 2019
    assert p.language == "fr"
    assert p.identifiers is not None
    assert p.identifiers.doi == "10.4000/vertigo.26537"
    assert p.doi_or_id == "10.4000/vertigo.26537"
    # Landing URL: openedition.org takes priority over hdl.handle.net
    assert p.landing_page_url is not None
    assert "journals.openedition.org" in str(p.landing_page_url)
    # Abstract: first dc:description value (French)
    assert p.abstract is not None
    assert "Bronze" in p.abstract
    assert p.inline_citation is not None
    assert p.inline_citation.markdown == (
        "[(Rey-Valette & Rocle 2019)](https://doi.org/10.4000/vertigo.26537)"
    )
    assert token is None


def test_parse_page_english_article() -> None:
    tokens = _query_tokens("iron age pottery tell")
    matches, _ = _parse_page(SAMPLE_LISTRECORDS, tokens)
    assert len(matches) == 1
    p = matches[0]
    assert p.authors == ["Maeir, Aren M."]
    assert p.year == 2021
    assert p.language == "en"
    assert p.identifiers is not None
    assert p.identifiers.doi == "10.4000/syria.12345"


def test_parse_page_empty_token_list_returns_all() -> None:
    matches, _ = _parse_page(SAMPLE_LISTRECORDS, [])
    assert len(matches) == 2


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
        <identifier>20.500.13089/del1</identifier>
        <datestamp>2020-01-01T00:00:00Z</datestamp>
      </header>
    </record>
  </ListRecords>
</OAI-PMH>
"""
    matches, _ = _parse_page(xml, [])
    assert matches == []


def test_parse_page_record_no_url_dropped() -> None:
    """Records with no landing URL and no DOI are dropped."""
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">
  <ListRecords>
    <record>
      <header>
        <identifier>20.500.13089/nourl</identifier>
        <datestamp>2020-01-01T00:00:00Z</datestamp>
      </header>
      <metadata>
        <oai_dc:dc xmlns:oai_dc="http://www.openarchives.org/OAI/2.0/oai_dc/"
                   xmlns:dc="http://purl.org/dc/elements/1.1/">
          <dc:title>A record with no URL</dc:title>
          <dc:creator>Ghost, Author</dc:creator>
          <dc:date>2020</dc:date>
          <dc:identifier>urn:issn:1234-5678</dc:identifier>
        </oai_dc:dc>
      </metadata>
    </record>
  </ListRecords>
</OAI-PMH>
"""
    matches, _ = _parse_page(xml, [])
    assert matches == []


def test_parse_page_handle_only_doi_or_id() -> None:
    """When no DOI, doi_or_id uses the header handle."""
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">
  <ListRecords>
    <record>
      <header>
        <identifier>20.500.13089/xyz99</identifier>
        <datestamp>2020-01-01T00:00:00Z</datestamp>
      </header>
      <metadata>
        <oai_dc:dc xmlns:oai_dc="http://www.openarchives.org/OAI/2.0/oai_dc/"
                   xmlns:dc="http://purl.org/dc/elements/1.1/">
          <dc:title>A record without DOI</dc:title>
          <dc:creator>Doe, Jane</dc:creator>
          <dc:date>2020</dc:date>
          <dc:identifier>https://journals.openedition.org/test/999</dc:identifier>
        </oai_dc:dc>
      </metadata>
    </record>
  </ListRecords>
</OAI-PMH>
"""
    matches, _ = _parse_page(xml, [])
    assert len(matches) == 1
    p = matches[0]
    assert p.doi_or_id == "openedition:20.500.13089/xyz99"
    assert p.identifiers is not None
    assert p.identifiers.doi is None


# ---------------------------------------------------------------------------
# Integration tests (mocked HTTP)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_search_openedition_impl_happy_path() -> None:
    respx.get(OPENEDITION_OAI).mock(
        return_value=httpx.Response(200, text=SAMPLE_LISTRECORDS)
    )
    results = await search_openedition_impl("bronze age levant", max_results=5)
    assert len(results) == 1
    p = results[0]
    assert isinstance(p, DAOPaper)
    assert p.source == "openedition"
    assert p.inline_citation is not None
    assert "Rey-Valette" in p.inline_citation.markdown


@pytest.mark.asyncio
@respx.mock
async def test_search_openedition_impl_empty() -> None:
    respx.get(OPENEDITION_OAI).mock(
        return_value=httpx.Response(200, text=EMPTY_LISTRECORDS)
    )
    assert await search_openedition_impl("xyzzy") == []


@pytest.mark.asyncio
@respx.mock
async def test_search_openedition_impl_http_error_propagates() -> None:
    respx.get(OPENEDITION_OAI).mock(return_value=httpx.Response(503))
    with pytest.raises(httpx.HTTPStatusError):
        await search_openedition_impl("anything")


@pytest.mark.asyncio
@respx.mock
async def test_search_openedition_impl_paginates() -> None:
    call_count = {"n": 0}

    def respond(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        body = PAGE_ONE if call_count["n"] == 1 else PAGE_TWO
        return httpx.Response(200, text=body)

    respx.get(OPENEDITION_OAI).mock(side_effect=respond)
    results = await search_openedition_impl("levant archaeology", max_results=10)
    assert call_count["n"] == 2
    assert len(results) == 2
    dois = {p.identifiers.doi for p in results if p.identifiers}
    assert dois == {"10.4000/test.1001", "10.4000/test.1002"}


@pytest.mark.asyncio
@respx.mock
async def test_search_openedition_impl_stops_at_max_results() -> None:
    call_count = {"n": 0}

    def respond(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return httpx.Response(200, text=PAGE_ONE)

    respx.get(OPENEDITION_OAI).mock(side_effect=respond)
    results = await search_openedition_impl("levant archaeology", max_results=1)
    assert len(results) == 1
    assert call_count["n"] == 1


@pytest.mark.asyncio
@respx.mock
async def test_search_openedition_impl_defaults_to_journals_set() -> None:
    route = respx.get(OPENEDITION_OAI).mock(
        return_value=httpx.Response(200, text=EMPTY_LISTRECORDS)
    )
    await search_openedition_impl("anything")
    assert route.called
    sent_url = str(route.calls.last.request.url)
    assert "set=journals" in sent_url


@pytest.mark.asyncio
@respx.mock
async def test_search_openedition_impl_all_drops_set_filter() -> None:
    route = respx.get(OPENEDITION_OAI).mock(
        return_value=httpx.Response(200, text=EMPTY_LISTRECORDS)
    )
    await search_openedition_impl("anything", collection="all")
    assert route.called
    sent_url = str(route.calls.last.request.url)
    assert "set=" not in sent_url


@pytest.mark.asyncio
@respx.mock
async def test_search_openedition_impl_year_params_forwarded() -> None:
    route = respx.get(OPENEDITION_OAI).mock(
        return_value=httpx.Response(200, text=EMPTY_LISTRECORDS)
    )
    await search_openedition_impl("anything", year_from=2015, year_to=2020)
    assert route.called
    sent_url = str(route.calls.last.request.url)
    assert "from=2015-01-01" in sent_url
    assert "until=2020-12-31" in sent_url
