"""Tests for the ADAJ adapter.

Uses a real fixture HTML captured 2026-05-15 from
``https://publication.doa.gov.jo/Publications/Search?SearchTerm=Negev``
so the parser is pinned against actual upstream output.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from dao_paper_search_mcp.adapters.adaj import (
    ADAJ_SEARCH,
    _filter_by_year,
    _parse_id_from_url,
    _parse_results,
    search_adaj_impl,
)
from dao_paper_search_mcp.models import DAOPaper

FIXTURES = Path(__file__).parent / "fixtures"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_parse_id_chapter() -> None:
    assert _parse_id_from_url("/Publications/ViewChapterPublic/212") == "chapter:212"


def test_parse_id_publication() -> None:
    assert _parse_id_from_url("/Publications/ViewPublic/25") == "publication:25"


def test_parse_id_none() -> None:
    assert _parse_id_from_url("/Other/url") is None


def test_parse_results_real_fixture() -> None:
    """The captured 'Negev' search returned 10 server-rendered results."""
    papers = _parse_results(_read("adaj_search_negev.html"))
    assert len(papers) == 10
    assert all(isinstance(p, DAOPaper) for p in papers)
    assert all(p.source == "adaj" for p in papers)
    assert all(p.language == "en" for p in papers)


def test_parse_results_first_hit_shape() -> None:
    papers = _parse_results(_read("adaj_search_negev.html"))
    p = papers[0]
    assert "Judah Versus Edom" in p.title
    assert p.year == 2009
    assert p.authors == ["Itzhaq Beit-Arieh"]
    assert p.pages == "6"
    assert p.doi_or_id == "adaj:chapter:212"
    assert str(p.landing_page_url) == "https://publication.doa.gov.jo/Publications/ViewChapterPublic/212"
    assert p.open_access_url is not None
    assert str(p.open_access_url).endswith(".pdf")
    assert "SHAJ" in (p.journal_or_volume or "") or "Studies" in (p.journal_or_volume or "")
    # Inline-citation integration: Author-Year recommended form.
    assert p.inline_citation is not None
    assert p.inline_citation.markdown.startswith("[(Beit-Arieh 2009)]")
    assert p.identifiers is not None
    assert p.identifiers.adaj_id == "chapter:212"


def test_filter_by_year_inclusive_bounds() -> None:
    papers = [DAOPaper(title=f"p{y}", doi_or_id=str(y), source="adaj", year=y) for y in (2005, 2009, 2012, 2020)]
    assert [p.year for p in _filter_by_year(papers, 2009, 2012)] == [2009, 2012]
    assert [p.year for p in _filter_by_year(papers, None, 2010)] == [2005, 2009]
    assert [p.year for p in _filter_by_year(papers, 2010, None)] == [2012, 2020]
    assert [p.year for p in _filter_by_year(papers, None, None)] == [2005, 2009, 2012, 2020]


def test_filter_by_year_drops_unknown_year() -> None:
    """Papers with year=None must drop out when a filter is active —
    otherwise the year filter would silently pass un-dateable hits."""
    papers = [
        DAOPaper(title="dated", doi_or_id="a", source="adaj", year=2010),
        DAOPaper(title="undated", doi_or_id="b", source="adaj", year=None),
    ]
    out = _filter_by_year(papers, 2000, 2020)
    assert len(out) == 1
    assert out[0].title == "dated"


@pytest.mark.asyncio
@respx.mock
async def test_search_adaj_impl_happy_path() -> None:
    respx.get(ADAJ_SEARCH).mock(return_value=httpx.Response(200, text=_read("adaj_search_negev.html")))
    papers = await search_adaj_impl("Negev", max_results=5)
    assert len(papers) == 5
    assert papers[0].title.startswith("Judah Versus Edom")


@pytest.mark.asyncio
@respx.mock
async def test_search_adaj_impl_year_filter() -> None:
    respx.get(ADAJ_SEARCH).mock(return_value=httpx.Response(200, text=_read("adaj_search_negev.html")))
    papers = await search_adaj_impl("Negev", max_results=10, year_from=2008, year_to=2010)
    assert all(p.year is not None and 2008 <= p.year <= 2010 for p in papers)


@pytest.mark.asyncio
@respx.mock
async def test_search_adaj_impl_http_error_propagates() -> None:
    respx.get(ADAJ_SEARCH).mock(return_value=httpx.Response(500))
    with pytest.raises(httpx.HTTPStatusError):
        await search_adaj_impl("anything")


@pytest.mark.asyncio
@respx.mock
async def test_search_adaj_impl_forwards_query_param() -> None:
    route = respx.get(ADAJ_SEARCH).mock(return_value=httpx.Response(200, text="<html><body></body></html>"))
    await search_adaj_impl("Cohen Bernick-Greenberg Kadesh-Barnea")
    assert route.called
    sent_url = str(route.calls.last.request.url)
    assert "SearchTerm=" in sent_url
    assert "Cohen" in sent_url
