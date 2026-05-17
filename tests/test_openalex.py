"""Tests for the OpenAlex adapter."""

from __future__ import annotations

import httpx
import pytest
import respx

from dao_paper_search_mcp.adapters.openalex import (
    OPENALEX_API,
    _build_params,
    _format_authors,
    _format_journal_or_volume,
    _format_pages,
    _reconstruct_abstract,
    _strip_doi_prefix,
    _strip_openalex_id_prefix,
    _work_to_paper,
    search_openalex_impl,
)
from dao_paper_search_mcp.models import DAOPaper, PublicationStatus


# Realistic OpenAlex Work for Cohen 1979 BASOR.
COHEN_1979_WORK = {
    "id": "https://openalex.org/W2031234567",
    "doi": "https://doi.org/10.2307/1356668",
    "display_name": "The Iron Age Fortresses in the Central Negev",
    "title": "The Iron Age Fortresses in the Central Negev",
    "publication_year": 1979,
    "publication_date": "1979-01-01",
    "type": "journal-article",
    "language": "en",
    "authorships": [
        {"author": {"id": "A1", "display_name": "Rudolph Cohen"}}
    ],
    "primary_location": {
        "source": {
            "display_name": "Bulletin of the American Schools of Oriental Research"
        }
    },
    "biblio": {"volume": "236", "first_page": "61", "last_page": "79"},
    "open_access": {"is_oa": False, "oa_url": None},
    "abstract_inverted_index": {
        "Iron": [0],
        "Age": [1],
        "fortresses": [2],
        "in": [3],
        "the": [4],
        "Negev": [5],
    },
}

# Work without a DOI — only OpenAlex Work ID. Common for preprints,
# institutional reports, dissertations.
NO_DOI_WORK = {
    "id": "https://openalex.org/W9999000001",
    "doi": None,
    "display_name": "An unpublished excavation report",
    "publication_year": 2022,
    "type": "preprint",
    "language": "en",
    "authorships": [
        {"author": {"display_name": "Yael Yisrael"}},
        {"author": {"display_name": "Itzhaq Beit-Arieh"}},
    ],
    "primary_location": {"source": None},
    "biblio": {},
    "open_access": {"is_oa": True, "oa_url": "https://example.org/oa.pdf"},
}


def test_strip_doi_prefix_handles_variants() -> None:
    assert _strip_doi_prefix("https://doi.org/10.1/x") == "10.1/x"
    assert _strip_doi_prefix("http://dx.doi.org/10.1/x") == "10.1/x"
    assert _strip_doi_prefix("10.1/x") == "10.1/x"
    assert _strip_doi_prefix("doi:10.1/x") == "10.1/x"
    assert _strip_doi_prefix(None) is None
    assert _strip_doi_prefix("") is None
    # Non-DOI URL should not be silently mistaken for one.
    assert _strip_doi_prefix("https://example.org/some-page") is None


def test_strip_openalex_id_prefix() -> None:
    assert _strip_openalex_id_prefix("https://openalex.org/W12345") == "W12345"
    assert _strip_openalex_id_prefix("W12345") == "W12345"
    assert _strip_openalex_id_prefix(None) is None


def test_format_authors_flips_to_family_first() -> None:
    """OpenAlex display_name is 'Given Family'; flip so Author-Year extracts
    the family name correctly."""
    work = {
        "authorships": [
            {"author": {"display_name": "Rudolph Cohen"}},
            {"author": {"display_name": "Yigal Yisrael"}},
        ]
    }
    assert _format_authors(work) == ["Cohen, Rudolph", "Yisrael, Yigal"]


def test_format_authors_keeps_single_token_name_as_is() -> None:
    work = {"authorships": [{"author": {"display_name": "Anonymous"}}]}
    assert _format_authors(work) == ["Anonymous"]


def test_format_journal_or_volume_combines() -> None:
    assert _format_journal_or_volume(COHEN_1979_WORK) == (
        "Bulletin of the American Schools of Oriental Research 236"
    )
    # No source → None, not a guess.
    assert _format_journal_or_volume({"primary_location": {"source": None}}) is None


def test_format_pages_combines_first_and_last() -> None:
    assert _format_pages(COHEN_1979_WORK) == "61-79"
    assert _format_pages({"biblio": {"first_page": "5"}}) == "5"
    assert _format_pages({"biblio": {}}) is None


def test_reconstruct_abstract_orders_words_by_position() -> None:
    out = _reconstruct_abstract(COHEN_1979_WORK["abstract_inverted_index"])
    assert out == "Iron Age fortresses in the Negev"


def test_reconstruct_abstract_handles_empty() -> None:
    assert _reconstruct_abstract(None) is None
    assert _reconstruct_abstract({}) is None
    assert _reconstruct_abstract("not a dict") is None  # type: ignore[arg-type]


def test_build_params_basic() -> None:
    params = _build_params("Negev forts", 5, None, None, None)
    assert ("search", "Negev forts") in params
    assert ("per_page", "5") in params
    # Polite-pool mailto is always sent.
    assert any(k == "mailto" for k, _ in params)
    # No filter when no constraints.
    assert all(k != "filter" for k, _ in params)


def test_build_params_with_language_and_year_range() -> None:
    params = _build_params("x", 5, "en", 1990, 2000)
    filter_val = next(v for k, v in params if k == "filter")
    assert "language:en" in filter_val
    assert "from_publication_date:1990-01-01" in filter_val
    assert "to_publication_date:2000-12-31" in filter_val


def test_build_params_clamps_per_page() -> None:
    high = _build_params("x", 9999, None, None, None)
    low = _build_params("x", 0, None, None, None)
    assert ("per_page", "100") in high
    assert ("per_page", "1") in low


def test_work_to_paper_doi_form() -> None:
    p = _work_to_paper(COHEN_1979_WORK)
    assert p is not None
    assert p.source == "openalex"
    assert p.doi_or_id == "10.2307/1356668"
    assert p.identifiers is not None
    assert p.identifiers.doi == "10.2307/1356668"
    assert p.identifiers.openalex_id == "W2031234567"
    assert p.authors == ["Cohen, Rudolph"]
    assert p.year == 1979
    assert p.pages == "61-79"
    assert p.abstract == "Iron Age fortresses in the Negev"
    # Inline citation: DOI present → Author-Year against doi.org.
    assert p.inline_citation is not None
    assert p.inline_citation.markdown == (
        "[(Cohen 1979)](https://doi.org/10.2307/1356668)"
    )


def test_work_to_paper_no_doi_falls_back_to_openalex_landing() -> None:
    p = _work_to_paper(NO_DOI_WORK)
    assert p is not None
    assert p.doi_or_id == "openalex:W9999000001"
    assert p.identifiers is not None
    assert p.identifiers.doi is None
    assert p.identifiers.openalex_id == "W9999000001"
    assert str(p.landing_page_url) == "https://openalex.org/W9999000001"
    assert p.publication_status is PublicationStatus.PREPRINT
    # OA URL surfaces independently.
    assert str(p.open_access_url) == "https://example.org/oa.pdf"
    # Inline citation: no DOI → Author-Year against openalex.org URL.
    assert p.inline_citation is not None
    assert p.inline_citation.markdown == (
        "[(Yisrael & Beit-Arieh 2022)](https://openalex.org/W9999000001)"
    )


def test_work_without_doi_or_openalex_id_is_dropped() -> None:
    """Without either canonical anchor we'd be inventing identifiers.
    Drop the hit rather than fabricate."""
    work = dict(COHEN_1979_WORK)
    work["doi"] = None
    work["id"] = None  # type: ignore[assignment]
    assert _work_to_paper(work) is None


@pytest.mark.asyncio
@respx.mock
async def test_search_openalex_impl_happy_path() -> None:
    respx.get(OPENALEX_API).mock(
        return_value=httpx.Response(
            200,
            json={"meta": {"count": 1}, "results": [COHEN_1979_WORK]},
        )
    )
    results = await search_openalex_impl("Negev fortresses Cohen", max_results=5)
    assert len(results) == 1
    p = results[0]
    assert isinstance(p, DAOPaper)
    assert p.source == "openalex"
    assert p.inline_citation is not None
    assert p.inline_citation.markdown.startswith("[(Cohen 1979)]")


@pytest.mark.asyncio
@respx.mock
async def test_search_openalex_impl_empty() -> None:
    respx.get(OPENALEX_API).mock(
        return_value=httpx.Response(
            200,
            json={"meta": {"count": 0}, "results": []},
        )
    )
    assert await search_openalex_impl("xyzzy") == []


@pytest.mark.asyncio
@respx.mock
async def test_search_openalex_impl_http_error_propagates() -> None:
    respx.get(OPENALEX_API).mock(return_value=httpx.Response(503))
    with pytest.raises(httpx.HTTPStatusError):
        await search_openalex_impl("anything")


@pytest.mark.asyncio
@respx.mock
async def test_search_openalex_impl_query_params_forwarded() -> None:
    route = respx.get(OPENALEX_API).mock(
        return_value=httpx.Response(
            200,
            json={"meta": {"count": 0}, "results": []},
        )
    )
    await search_openalex_impl(
        "Boaretto Atar Haroa",
        max_results=3,
        language="en",
        year_from=2005,
        year_to=2012,
    )
    assert route.called
    sent_url = str(route.calls.last.request.url)
    assert "search=" in sent_url
    assert "mailto=" in sent_url
    assert "language%3Aen" in sent_url or "language:en" in sent_url
    assert "from_publication_date" in sent_url
    assert "to_publication_date" in sent_url


@pytest.mark.asyncio
@respx.mock
async def test_search_openalex_impl_filters_out_anchorless_hits() -> None:
    """A hit without DOI or OpenAlex ID is dropped, not crashed."""
    bad = dict(COHEN_1979_WORK)
    bad["doi"] = None
    bad["id"] = None  # type: ignore[assignment]
    respx.get(OPENALEX_API).mock(
        return_value=httpx.Response(
            200,
            json={"meta": {"count": 2}, "results": [COHEN_1979_WORK, bad]},
        )
    )
    results = await search_openalex_impl("anything")
    assert len(results) == 1
    assert results[0].doi_or_id == "10.2307/1356668"
