"""Tests for the IAA Publications adapter (OAI-PMH backend).

The MVP-incomplete HTML-scraping path is gone: ``IAAUnavailableError``
no longer exists, the ``#results-list`` tripwire is unnecessary. The
adapter now talks OAI-PMH to ``/do/oai/`` and parses Dublin Core XML.

The fixtures below are shape-faithful copies of real Europe-PMC-style
DC responses probed on 2026-05-15 — minimal enough to read, complete
enough to exercise the parser.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from dao_paper_search_mcp.adapters.iaa import (
    IAA_OAI,
    _classify_identifiers,
    _detect_language,
    _extract_year,
    _parse_page,
    _pub_id_from_landing,
    _query_tokens,
    _record_matches,
    _resolve_set_spec,
    _strip_doi_prefix,
    search_iaa_impl,
)
from dao_paper_search_mcp.models import DAOPaper


# A realistic OAI-PMH response: one English Atiqot article plus one
# Hebrew Hadashot record. Shape mirrors what /do/oai/?verb=ListRecords
# actually returns (probed 2026-05-15).
SAMPLE_LISTRECORDS = """<?xml version="1.0" encoding="UTF-8"?>
<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">
  <responseDate>2026-05-15T18:00:00Z</responseDate>
  <ListRecords>
    <record>
      <header>
        <identifier>oai:publications.iaa.org.il:atiqot-1124</identifier>
        <datestamp>2024-11-26T10:12:05Z</datestamp>
        <setSpec>publication:atiqot</setSpec>
      </header>
      <metadata>
        <oai_dc:dc xmlns:oai_dc="http://www.openarchives.org/OAI/2.0/oai_dc/"
                   xmlns:dc="http://purl.org/dc/elements/1.1/">
          <dc:title>Iron Age Fortresses in the Central Negev: A Reassessment</dc:title>
          <dc:creator>Cohen, Rudolph</dc:creator>
          <dc:creator>Yisrael, Yigal</dc:creator>
          <dc:description>We reassess radiocarbon dates from En Haseva and Atar Haroa.</dc:description>
          <dc:date>2024-11-26T10:12:05Z</dc:date>
          <dc:type>text</dc:type>
          <dc:identifier>https://publications.iaa.org.il/atiqot/vol116/iss1/3</dc:identifier>
          <dc:identifier>info:doi/10.70967/2948-040X.1124</dc:identifier>
          <dc:identifier>https://publications.iaa.org.il/context/atiqot/article/1124/viewcontent/en_116.pdf</dc:identifier>
          <dc:source>'Atiqot</dc:source>
          <dc:publisher>Israel Antiquities Authority Publications Portal</dc:publisher>
          <dc:subject>Iron Age</dc:subject>
          <dc:subject>Negev</dc:subject>
          <dc:subject>radiocarbon dating</dc:subject>
        </oai_dc:dc>
      </metadata>
    </record>
    <record>
      <header>
        <identifier>oai:publications.iaa.org.il:ha_hebrew_series-500</identifier>
        <datestamp>2012-06-01T00:00:00Z</datestamp>
        <setSpec>publication:ha_hebrew_series</setSpec>
      </header>
      <metadata>
        <oai_dc:dc xmlns:oai_dc="http://www.openarchives.org/OAI/2.0/oai_dc/"
                   xmlns:dc="http://purl.org/dc/elements/1.1/">
          <dc:title>חפירות בחורבת קיטמית</dc:title>
          <dc:creator>בית-אריה, יצחק</dc:creator>
          <dc:description>דיווח מקדים על חפירות בנגב</dc:description>
          <dc:date>2012</dc:date>
          <dc:identifier>https://publications.iaa.org.il/ha_hebrew_series/vol3/iss1/2</dc:identifier>
          <dc:subject>negev</dc:subject>
          <dc:subject>חפירות</dc:subject>
        </oai_dc:dc>
      </metadata>
    </record>
  </ListRecords>
</OAI-PMH>
"""

# Empty result list — legitimate "no hits" response.
EMPTY_LISTRECORDS = """<?xml version="1.0" encoding="UTF-8"?>
<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">
  <responseDate>2026-05-15T18:00:00Z</responseDate>
  <ListRecords></ListRecords>
</OAI-PMH>
"""

# Two-page response: first page with a token, second page final.
PAGE_ONE = """<?xml version="1.0" encoding="UTF-8"?>
<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">
  <ListRecords>
    <record>
      <header>
        <identifier>oai:publications.iaa.org.il:atiqot-1</identifier>
        <datestamp>2024-01-01T00:00:00Z</datestamp>
      </header>
      <metadata>
        <oai_dc:dc xmlns:oai_dc="http://www.openarchives.org/OAI/2.0/oai_dc/"
                   xmlns:dc="http://purl.org/dc/elements/1.1/">
          <dc:title>Negev Iron Age article one</dc:title>
          <dc:creator>Author A</dc:creator>
          <dc:date>2024</dc:date>
          <dc:identifier>https://publications.iaa.org.il/atiqot/vol1/iss1/1</dc:identifier>
          <dc:identifier>info:doi/10.70967/test.1</dc:identifier>
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
        <identifier>oai:publications.iaa.org.il:atiqot-2</identifier>
        <datestamp>2024-01-02T00:00:00Z</datestamp>
      </header>
      <metadata>
        <oai_dc:dc xmlns:oai_dc="http://www.openarchives.org/OAI/2.0/oai_dc/"
                   xmlns:dc="http://purl.org/dc/elements/1.1/">
          <dc:title>Negev Iron Age article two</dc:title>
          <dc:creator>Author B</dc:creator>
          <dc:date>2024</dc:date>
          <dc:identifier>https://publications.iaa.org.il/atiqot/vol1/iss1/2</dc:identifier>
          <dc:identifier>info:doi/10.70967/test.2</dc:identifier>
        </oai_dc:dc>
      </metadata>
    </record>
    <resumptionToken></resumptionToken>
  </ListRecords>
</OAI-PMH>
"""


def test_detect_language_hebrew_english_und() -> None:
    assert _detect_language("חפירות בכדש ברנע") == "he"
    assert _detect_language("Excavations at En Haseva") == "en"
    assert _detect_language("123 456") == "und"


def test_strip_doi_prefix_handles_variants() -> None:
    assert _strip_doi_prefix("info:doi/10.70967/x.y") == "10.70967/x.y"
    assert _strip_doi_prefix("doi:10.70967/x.y") == "10.70967/x.y"
    assert _strip_doi_prefix("https://doi.org/10.1/z") == "10.1/z"
    assert _strip_doi_prefix("not-a-doi") is None


def test_classify_identifiers_routes_three_types() -> None:
    identifiers = [
        "https://publications.iaa.org.il/atiqot/vol116/iss1/3",
        "info:doi/10.70967/2948-040X.1124",
        "https://publications.iaa.org.il/context/atiqot/article/1124/viewcontent/en_116.pdf",
    ]
    doi, landing, pdf = _classify_identifiers(identifiers)
    assert doi == "10.70967/2948-040X.1124"
    assert landing == "https://publications.iaa.org.il/atiqot/vol116/iss1/3"
    assert pdf.endswith("en_116.pdf")


def test_classify_identifiers_handles_missing_pieces() -> None:
    # No DOI, no PDF — just the landing.
    doi, landing, pdf = _classify_identifiers(["https://publications.iaa.org.il/atiqot/x/y/z"])
    assert doi is None
    assert landing == "https://publications.iaa.org.il/atiqot/x/y/z"
    assert pdf is None
    # Nothing at all.
    assert _classify_identifiers([]) == (None, None, None)


def test_pub_id_from_landing_strips_domain() -> None:
    assert (
        _pub_id_from_landing("https://publications.iaa.org.il/atiqot/vol116/iss1/3")
        == "atiqot/vol116/iss1/3"
    )
    # Off-domain → None, not garbage.
    assert _pub_id_from_landing("https://doi.org/10.1/x") is None
    assert _pub_id_from_landing(None) is None


def test_extract_year_handles_iso_and_bare_year() -> None:
    assert _extract_year("2024-11-26T10:12:05Z") == 2024
    assert _extract_year("2024") == 2024
    assert _extract_year("") is None
    assert _extract_year(None) is None


def test_query_tokens_splits_and_lowercases() -> None:
    assert _query_tokens("Cohen Negev FORTRESS") == ["cohen", "negev", "fortress"]
    assert _query_tokens("   ") == []
    assert _query_tokens("") == []


def test_record_matches_and_of_tokens() -> None:
    # Every token must appear somewhere.
    assert _record_matches(["cohen", "negev"], "Cohen Survey", "Negev fortress study", [], [])
    # Missing token → no match.
    assert not _record_matches(
        ["cohen", "byzantine"], "Cohen Survey", "Negev fortress", [], []
    )
    # Token in subjects also counts.
    assert _record_matches(["radiocarbon"], "x", "y", ["radiocarbon dating"], [])
    # Token in authors also counts.
    assert _record_matches(["yisrael"], "x", "y", [], ["Yisrael, Yigal"])
    # Empty token list matches everything.
    assert _record_matches([], "x", "y", [], [])


def test_resolve_set_spec_friendly_names_and_passthrough() -> None:
    assert _resolve_set_spec("atiqot") == "publication:atiqot"
    assert _resolve_set_spec("ha-esi") == "publication:ha-esi"
    assert _resolve_set_spec("publication:foo") == "publication:foo"
    # Unknown friendly name gets ``publication:`` prepended (escape hatch
    # for sets we haven't catalogued).
    assert _resolve_set_spec("brand_new_set") == "publication:brand_new_set"
    assert _resolve_set_spec(None) is None
    assert _resolve_set_spec("") is None


def test_parse_page_extracts_matching_records() -> None:
    tokens = _query_tokens("negev iron age")
    matches, token = _parse_page(SAMPLE_LISTRECORDS, tokens)
    assert len(matches) == 1
    p = matches[0]
    assert isinstance(p, DAOPaper)
    assert p.source == "iaa"
    assert p.title.startswith("Iron Age Fortresses")
    assert p.authors == ["Cohen, Rudolph", "Yisrael, Yigal"]
    assert p.year == 2024
    assert p.identifiers is not None
    assert p.identifiers.doi == "10.70967/2948-040X.1124"
    assert p.identifiers.iaa_pub_id == "atiqot/vol116/iss1/3"
    # Inline citation: DOI present → Author-Year against doi.org.
    assert p.inline_citation is not None
    assert p.inline_citation.markdown_recommended == (
        "[(Cohen & Yisrael 2024)](https://doi.org/10.70967/2948-040X.1124)"
    )
    assert p.language == "en"
    assert p.open_access_url is not None
    assert str(p.open_access_url).endswith("en_116.pdf")
    # No resumption token on this sample.
    assert token is None


def test_parse_page_hebrew_record_detection() -> None:
    """Empty token list matches everything; verify Hebrew detection."""
    matches, _ = _parse_page(SAMPLE_LISTRECORDS, [])
    assert len(matches) == 2
    hebrew = matches[1]
    assert hebrew.language == "he"
    assert hebrew.year == 2012
    assert hebrew.authors == ["בית-אריה, יצחק"]


def test_parse_page_keyword_filter_excludes_non_matches() -> None:
    """Tokens that match neither record drop everything."""
    matches, _ = _parse_page(SAMPLE_LISTRECORDS, _query_tokens("byzantine pottery"))
    assert matches == []


def test_parse_page_empty_listrecords() -> None:
    matches, token = _parse_page(EMPTY_LISTRECORDS, [])
    assert matches == []
    assert token is None


def test_parse_page_resumption_token_surfaced() -> None:
    _, token = _parse_page(PAGE_ONE, [])
    assert token == "page-2-token"
    _, no_token = _parse_page(PAGE_TWO, [])
    assert no_token is None


def test_parse_page_skips_deleted_records() -> None:
    """OAI marks deleted records with status="deleted" on the header."""
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">
  <ListRecords>
    <record>
      <header status="deleted">
        <identifier>oai:publications.iaa.org.il:atiqot-deleted</identifier>
        <datestamp>2020-01-01T00:00:00Z</datestamp>
      </header>
    </record>
  </ListRecords>
</OAI-PMH>
"""
    matches, _ = _parse_page(xml, [])
    assert matches == []


@pytest.mark.asyncio
@respx.mock
async def test_search_iaa_impl_happy_path() -> None:
    respx.get(IAA_OAI).mock(
        return_value=httpx.Response(200, text=SAMPLE_LISTRECORDS)
    )
    results = await search_iaa_impl("Negev Iron Age", max_results=5)
    assert len(results) == 1
    p = results[0]
    assert isinstance(p, DAOPaper)
    assert p.source == "iaa"
    assert p.inline_citation is not None
    assert p.inline_citation.markdown_recommended.startswith("[(Cohen & Yisrael 2024)]")


@pytest.mark.asyncio
@respx.mock
async def test_search_iaa_impl_empty() -> None:
    respx.get(IAA_OAI).mock(
        return_value=httpx.Response(200, text=EMPTY_LISTRECORDS)
    )
    assert await search_iaa_impl("xyzzy") == []


@pytest.mark.asyncio
@respx.mock
async def test_search_iaa_impl_http_error_propagates() -> None:
    respx.get(IAA_OAI).mock(return_value=httpx.Response(503))
    with pytest.raises(httpx.HTTPStatusError):
        await search_iaa_impl("anything")


@pytest.mark.asyncio
@respx.mock
async def test_search_iaa_impl_paginates_via_resumption_token() -> None:
    """Two-page response: adapter must follow the token to find the
    second record after the first page returns one match."""
    call_count = {"n": 0}

    def respond(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        body = PAGE_ONE if call_count["n"] == 1 else PAGE_TWO
        return httpx.Response(200, text=body)

    respx.get(IAA_OAI).mock(side_effect=respond)

    results = await search_iaa_impl("negev iron age", max_results=10)
    assert call_count["n"] == 2
    assert len(results) == 2
    assert {p.identifiers.doi for p in results if p.identifiers} == {
        "10.70967/test.1", "10.70967/test.2",
    }


@pytest.mark.asyncio
@respx.mock
async def test_search_iaa_impl_stops_when_max_results_reached() -> None:
    """Even with a resumption token, stop paginating once we have enough."""
    call_count = {"n": 0}

    def respond(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return httpx.Response(200, text=PAGE_ONE)

    respx.get(IAA_OAI).mock(side_effect=respond)
    results = await search_iaa_impl("negev iron age", max_results=1)
    assert len(results) == 1
    # Only one round-trip: max_results reached on the first page.
    assert call_count["n"] == 1


@pytest.mark.asyncio
@respx.mock
async def test_search_iaa_impl_forwards_collection_and_year() -> None:
    route = respx.get(IAA_OAI).mock(
        return_value=httpx.Response(200, text=EMPTY_LISTRECORDS)
    )
    await search_iaa_impl(
        "carbon dating",
        max_results=5,
        collection="atiqot",
        year_from=2005,
        year_to=2010,
    )
    assert route.called
    sent_url = str(route.calls.last.request.url)
    assert "verb=ListRecords" in sent_url
    assert "metadataPrefix=oai_dc" in sent_url
    assert "set=publication%3Aatiqot" in sent_url or "set=publication:atiqot" in sent_url
    assert "from=2005-01-01" in sent_url
    assert "until=2010-12-31" in sent_url
