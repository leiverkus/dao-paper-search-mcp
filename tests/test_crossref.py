"""Tests for the Crossref adapter.

Upstream HTTP is mocked with ``respx``. The shapes below match
real Crossref ``/works`` responses (probed 2026-05-15).
"""

from __future__ import annotations

import httpx
import pytest
import respx

from dao_paper_search_mcp.adapters.crossref import (
    CROSSREF_API,
    _build_params,
    _extract_year,
    _format_authors,
    _format_journal_or_volume,
    _full_title,
    _item_to_paper,
    _strip_jats,
    search_crossref_impl,
)
from dao_paper_search_mcp.models import DAOPaper, PublicationStatus

# Realistic Crossref item for Cohen 1979 BASOR — minimal viable shape.
COHEN_1979_ITEM = {
    "DOI": "10.2307/1356668",
    "type": "journal-article",
    "title": ["The Iron Age Fortresses in the Central Negev"],
    "container-title": ["Bulletin of the American Schools of Oriental Research"],
    "volume": "236",
    "page": "61-79",
    "author": [{"family": "Cohen", "given": "Rudolph"}],
    "published-print": {"date-parts": [[1979, 1, 1]]},
    "URL": "https://doi.org/10.2307/1356668",
    "language": "en",
}

# Multi-author Boaretto + Finkelstein + Shahack-Gross 2010 Radiocarbon
# — exercises the explicit 3-author inline form added in Schema v2.
BOARETTO_2010_ITEM = {
    "DOI": "10.1017/s0033822200044982",
    "type": "journal-article",
    "title": ["Radiocarbon Results from the Iron IIA Site of Atar Haroa in the Negev Highlands"],
    "container-title": ["Radiocarbon"],
    "volume": "52",
    "issue": "1",
    "page": "1-12",
    "author": [
        {"family": "Boaretto", "given": "Elisabetta"},
        {"family": "Finkelstein", "given": "Israel"},
        {"family": "Shahack-Gross", "given": "Ruth"},
    ],
    "published-print": {"date-parts": [[2010]]},
    "URL": "https://doi.org/10.1017/s0033822200044982",
    "language": "en",
    "abstract": "<jats:p>We report radiocarbon dates from the Iron IIA site…</jats:p>",
}


def test_first_or_none_via_full_title() -> None:
    assert _full_title({"title": ["A Title"]}) == "A Title"
    assert _full_title({"title": []}) == "(untitled)"
    assert _full_title({}) == "(untitled)"


def test_full_title_merges_subtitle() -> None:
    out = _full_title({"title": ["Main Title"], "subtitle": ["A Sub Story"]})
    assert out == "Main Title: A Sub Story"


def test_full_title_skips_redundant_subtitle() -> None:
    """If the subtitle is already contained in the title, don't double it."""
    out = _full_title({"title": ["Main Title and the sub story"], "subtitle": ["the sub story"]})
    assert out == "Main Title and the sub story"


def test_format_authors_skips_corporate() -> None:
    """Corporate authors carry ``name`` not ``family``/``given``; they
    distort the Author-Year citation form, so we drop them."""
    item = {
        "author": [
            {"family": "Cohen", "given": "Rudolph"},
            {"name": "The International Negev Survey Project"},
            {"family": "Yisrael", "given": "Yigal"},
        ]
    }
    assert _format_authors(item) == ["Cohen, Rudolph", "Yisrael, Yigal"]


def test_format_authors_handles_missing_given() -> None:
    item = {"author": [{"family": "Cohen"}]}
    assert _format_authors(item) == ["Cohen"]


def test_extract_year_prefers_published_print() -> None:
    assert _extract_year(COHEN_1979_ITEM) == 1979
    # Fall back to issued when print/online are absent.
    assert _extract_year({"issued": {"date-parts": [[2024]]}}) == 2024
    # Year as string still works (Crossref occasionally returns strings).
    assert _extract_year({"published": {"date-parts": [["1995"]]}}) == 1995
    # No date info → None, not a guess.
    assert _extract_year({}) is None


def test_format_journal_or_volume_combines() -> None:
    assert _format_journal_or_volume(COHEN_1979_ITEM) == ("Bulletin of the American Schools of Oriental Research 236")
    assert _format_journal_or_volume(BOARETTO_2010_ITEM) == "Radiocarbon 52(1)"
    assert _format_journal_or_volume({}) is None


def test_strip_jats_removes_tags_and_collapses_whitespace() -> None:
    raw = "<jats:p>Hello <jats:italic>world</jats:italic>\n\n!</jats:p>"
    assert _strip_jats(raw) == "Hello world !"
    assert _strip_jats(None) is None
    assert _strip_jats("") is None


def test_build_params_basic() -> None:
    params = _build_params("Negev fortresses", 5, None, None)
    assert ("query.bibliographic", "Negev fortresses") in params
    assert ("rows", "5") in params
    # No filter when no year bounds.
    assert all(p[0] != "filter" for p in params)


def test_build_params_year_filter() -> None:
    params = _build_params("x", 5, 1990, 2000)
    assert ("filter", "from-pub-date:1990,until-pub-date:2000") in params


def test_build_params_clamps_rows_high_and_low() -> None:
    high = _build_params("x", 9999, None, None)
    low = _build_params("x", 0, None, None)
    assert ("rows", "100") in high
    assert ("rows", "1") in low


def test_item_without_doi_is_dropped() -> None:
    """No DOI = no stable identifier = drop. Hallucination prevention."""
    item = dict(COHEN_1979_ITEM)
    item.pop("DOI")
    assert _item_to_paper(item) is None


def test_item_to_paper_single_author_authoryear_form() -> None:
    p = _item_to_paper(COHEN_1979_ITEM)
    assert p is not None
    assert p.source == "crossref"
    assert p.doi_or_id == "10.2307/1356668"
    assert p.authors == ["Cohen, Rudolph"]
    assert p.year == 1979
    assert p.pages == "61-79"
    assert "Bulletin of the American Schools" in (p.journal_or_volume or "")
    assert p.identifiers is not None and p.identifiers.doi == "10.2307/1356668"
    # Inline citation — Author-Year form via DOI.
    assert p.inline_citation is not None
    assert p.inline_citation.markdown == ("[(Cohen 1979)](https://doi.org/10.2307/1356668)")


def test_item_to_paper_three_authors_explicit_inline_form() -> None:
    """Schema v2: three authors are listed explicitly (no et al.)."""
    p = _item_to_paper(BOARETTO_2010_ITEM)
    assert p is not None
    assert len(p.authors) == 3
    assert p.inline_citation is not None
    assert p.inline_citation.markdown == (
        "[(Boaretto, Finkelstein & Shahack-Gross 2010)](https://doi.org/10.1017/s0033822200044982)"
    )
    # Schema v2: structured bibliography line comes from Venue metadata.
    assert p.inline_citation.authoritative_bibliography_line == (
        "Boaretto, E., Finkelstein, I., & Shahack-Gross, R. (2010). "
        "Radiocarbon Results from the Iron IIA Site of Atar Haroa in "
        "the Negev Highlands. *Radiocarbon* 52(1), 1-12."
        " DOI: [10.1017/s0033822200044982](https://doi.org/10.1017/s0033822200044982)"
    )
    # JATS-stripped abstract.
    assert p.abstract is not None
    assert "<jats:p>" not in p.abstract
    assert "radiocarbon dates" in p.abstract


def test_item_to_paper_preprint_status() -> None:
    item = dict(COHEN_1979_ITEM)
    item["type"] = "posted-content"
    p = _item_to_paper(item)
    assert p is not None
    assert p.publication_status is PublicationStatus.PREPRINT


@pytest.mark.asyncio
@respx.mock
async def test_search_crossref_impl_happy_path() -> None:
    respx.get(CROSSREF_API).mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "ok",
                "message": {"total-results": 1, "items": [COHEN_1979_ITEM]},
            },
        )
    )
    results = await search_crossref_impl("Negev fortresses Cohen", max_results=5)
    assert len(results) == 1
    p = results[0]
    assert isinstance(p, DAOPaper)
    assert p.source == "crossref"
    assert p.inline_citation is not None
    assert p.inline_citation.markdown.startswith("[(Cohen 1979)]")


@pytest.mark.asyncio
@respx.mock
async def test_search_crossref_impl_empty_when_no_items() -> None:
    respx.get(CROSSREF_API).mock(
        return_value=httpx.Response(
            200,
            json={"status": "ok", "message": {"total-results": 0, "items": []}},
        )
    )
    assert await search_crossref_impl("xyzzy-no-such-query") == []


@pytest.mark.asyncio
@respx.mock
async def test_search_crossref_impl_filters_out_doi_less_items() -> None:
    """Crossref usually guarantees DOIs, but partial records do happen
    (rare). Verify those drop out rather than crash."""
    no_doi = dict(COHEN_1979_ITEM)
    no_doi.pop("DOI")
    respx.get(CROSSREF_API).mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "ok",
                "message": {
                    "total-results": 2,
                    "items": [COHEN_1979_ITEM, no_doi],
                },
            },
        )
    )
    results = await search_crossref_impl("anything")
    assert len(results) == 1
    assert results[0].doi_or_id == "10.2307/1356668"


@pytest.mark.asyncio
@respx.mock
async def test_search_crossref_impl_http_error_propagates() -> None:
    respx.get(CROSSREF_API).mock(return_value=httpx.Response(503))
    with pytest.raises(httpx.HTTPStatusError):
        await search_crossref_impl("anything")


@pytest.mark.asyncio
@respx.mock
async def test_search_crossref_impl_query_and_filter_forwarded() -> None:
    route = respx.get(CROSSREF_API).mock(
        return_value=httpx.Response(
            200,
            json={"status": "ok", "message": {"total-results": 0, "items": []}},
        )
    )
    await search_crossref_impl(
        "Boaretto Atar Haroa Iron Age",
        max_results=3,
        year_from=2005,
        year_to=2012,
    )
    assert route.called
    sent_url = str(route.calls.last.request.url)
    assert "query.bibliographic" in sent_url
    assert "from-pub-date" in sent_url and "2005" in sent_url
    assert "until-pub-date" in sent_url and "2012" in sent_url


@pytest.mark.asyncio
@respx.mock
async def test_search_crossref_impl_sends_polite_user_agent() -> None:
    """Polite-pool bucket requires a mailto in the User-Agent."""
    route = respx.get(CROSSREF_API).mock(
        return_value=httpx.Response(
            200,
            json={"status": "ok", "message": {"total-results": 0, "items": []}},
        )
    )
    await search_crossref_impl("anything")
    ua = route.calls.last.request.headers.get("User-Agent", "")
    assert "dao-paper-search-mcp" in ua
    assert "mailto:" in ua
