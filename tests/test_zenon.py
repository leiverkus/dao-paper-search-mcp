"""Tests for the Zenon DAI adapter.

We mock the upstream REST API with ``respx`` so the unit suite runs
offline. The live verification suite (``test_verification_suite.py``)
exercises the real endpoint.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from dao_paper_search_mcp.adapters.zenon import (
    ZENON_API,
    _build_params,
    _detect_language,
    _first_int,
    _flatten_authors,
    _series_or_journal,
    search_zenon_impl,
)
from dao_paper_search_mcp.models import DAOPaper

# Fixture: one realistic Zenon record (shape matches live API).
SAMPLE_RECORD = {
    "id": "001388596",
    "title": "Horvat Qitmit :",
    "subTitle": "an Edomite shrine in the biblical Negev ",
    "primaryAuthorsNames": ["Beit-Arieh, Itzhaq."],
    "secondaryAuthorsNames": ["Beck, Pirhiya,"],
    "corporateAuthorsNames": [],
    "publicationDates": ["1995"],
    "publishers": ["Institute of Archaeology of Tel Aviv University,"],
    "series": [
        {
            "name": "Monograph series (Makhon le-arkheologyah 'a. sh. Sonyah u-Marko Nadler) ;",
            "number": "no. 11",
        }
    ],
    "languages": ["English"],
    "formats": ["Book"],
    "isbns": ["9654400049"],
    "urls": [],
    "DAILinks": {
        "gazetteer": [
            {
                "label": "Sahraʾ an-Naqab",
                "uri": "https://gazetteer.dainst.org/place/2043520",
            }
        ],
        "thesauri": [],
    },
}


def test_first_int_picks_valid_year() -> None:
    assert _first_int(["1995"]) == 1995
    assert _first_int(["[1994]"]) == 1994
    assert _first_int(["n.d.", "1980"]) == 1980
    assert _first_int(["n.d."]) is None
    assert _first_int([]) is None


def test_detect_language_known_and_unknown() -> None:
    assert _detect_language({"languages": ["English"]}) == "en"
    assert _detect_language({"languages": ["German"]}) == "de"
    assert _detect_language({"languages": ["Hebrew"]}) == "he"
    assert _detect_language({"languages": []}) == "und"
    assert _detect_language({"languages": ["Klingon"]}) == "und"


def test_flatten_authors_dedups_and_strips() -> None:
    rec = {
        "primaryAuthorsNames": ["Cohen, R.,", "Cohen, R."],
        "secondaryAuthorsNames": ["Yisrael, Y."],
    }
    assert _flatten_authors(rec) == ["Cohen, R", "Yisrael, Y"]


def test_series_or_journal_combines_name_and_number() -> None:
    label = _series_or_journal(SAMPLE_RECORD)
    assert label is not None
    assert "Monograph series" in label
    assert "no. 11" in label


def test_build_params_with_filters() -> None:
    params = _build_params(
        "Negev fortresses",
        max_results=5,
        language="en",
        year_from=1990,
        year_to=2000,
    )
    assert ("lookfor", "Negev fortresses") in params
    assert ("limit", "5") in params
    assert ('filter[]', 'language:"English"') in params
    assert ("filter[]", "publishDate:[1990 TO 2000]") in params


def test_build_params_clamps_limit_high() -> None:
    params = _build_params("x", max_results=999, language=None, year_from=None, year_to=None)
    assert ("limit", "50") in params


def test_build_params_clamps_limit_low() -> None:
    params = _build_params("x", max_results=0, language=None, year_from=None, year_to=None)
    assert ("limit", "1") in params


def test_build_params_drops_unknown_language() -> None:
    params = _build_params("x", max_results=5, language="xx", year_from=None, year_to=None)
    assert not any(p[0] == "filter[]" and "language:" in p[1] for p in params)


@pytest.mark.asyncio
@respx.mock
async def test_search_zenon_impl_happy_path() -> None:
    respx.get(ZENON_API).mock(
        return_value=httpx.Response(
            200,
            json={"resultCount": 1, "records": [SAMPLE_RECORD], "status": "OK"},
        )
    )
    results = await search_zenon_impl("Negev fortresses", max_results=5)
    assert len(results) == 1
    p = results[0]
    assert isinstance(p, DAOPaper)
    assert p.source == "zenon"
    assert p.doi_or_id == "zenon:001388596"
    assert "Horvat Qitmit" in p.title
    assert "Edomite shrine" in p.title  # subTitle merged
    assert p.year == 1995
    assert p.language == "en"
    assert p.authors == ["Beit-Arieh, Itzhaq", "Beck, Pirhiya"]
    assert str(p.landing_page_url) == "https://zenon.dainst.org/Record/001388596"
    # Briefing Iteration 2 / gazetteer integration: DAILinks.gazetteer
    # is automatically promoted into site_ids as ``gazetteer:<gazId>``.
    assert "gazetteer:2043520" in p.site_ids
    # Inline-citation integration: Schema v2 renders Author-Year form
    # via the canonical zenon URL when no DOI is available.
    assert p.inline_citation is not None
    assert p.inline_citation.markdown.startswith("[(Beit-Arieh & Beck 1995)]")
    assert str(p.inline_citation.url) == (
        "https://zenon.dainst.org/Record/001388596"
    )
    assert p.identifiers is not None
    assert p.identifiers.zenon_id == "001388596"


@pytest.mark.asyncio
@respx.mock
async def test_search_zenon_impl_empty() -> None:
    respx.get(ZENON_API).mock(
        return_value=httpx.Response(
            200, json={"resultCount": 0, "records": [], "status": "OK"}
        )
    )
    assert await search_zenon_impl("xyzzy-no-such-query") == []


@pytest.mark.asyncio
@respx.mock
async def test_search_zenon_impl_http_error_propagates() -> None:
    """If the upstream returns 5xx, callers must see the failure.
    Silent failure would let hallucinations pass through unverified."""
    respx.get(ZENON_API).mock(return_value=httpx.Response(503))
    with pytest.raises(httpx.HTTPStatusError):
        await search_zenon_impl("anything")


@pytest.mark.asyncio
@respx.mock
async def test_search_zenon_impl_query_params_forwarded() -> None:
    route = respx.get(ZENON_API).mock(
        return_value=httpx.Response(
            200, json={"resultCount": 0, "records": [], "status": "OK"}
        )
    )
    await search_zenon_impl(
        "Cohen Yisrael BASOR",
        max_results=3,
        language="en",
        year_from=1990,
        year_to=2000,
    )
    assert route.called
    sent_url = str(route.calls.last.request.url)
    assert "lookfor=Cohen+Yisrael+BASOR" in sent_url or "lookfor=Cohen%20Yisrael%20BASOR" in sent_url
    assert "publishDate" in sent_url
    assert "language" in sent_url
