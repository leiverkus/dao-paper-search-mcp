"""Tests for the Semantic Scholar adapter."""

from __future__ import annotations

import httpx
import pytest
import respx

from dao_paper_search_mcp.adapters.semantic_scholar import (
    S2_API,
    _build_params,
    _format_authors,
    _format_journal_or_volume,
    _format_pages,
    _paper_to_paper,
    _publication_status,
    _verification_note,
    search_semantic_scholar_impl,
)
from dao_paper_search_mcp.models import DAOPaper, PublicationStatus


# Realistic S2 paper for Cohen 1979 BASOR (DOI present).
COHEN_1979_PAPER = {
    "paperId": "abc123def456",
    "externalIds": {"DOI": "10.2307/1356668", "CorpusId": "12345"},
    "title": "The Iron Age Fortresses in the Central Negev",
    "abstract": "An overview of Iron Age fortified sites in the central Negev.",
    "year": 1979,
    "authors": [{"authorId": "A1", "name": "Rudolph Cohen"}],
    "venue": "BASOR",
    "journal": {
        "name": "Bulletin of the American Schools of Oriental Research",
        "volume": "236",
        "pages": "61-79",
    },
    "publicationDate": "1979-01-01",
    "publicationTypes": ["JournalArticle"],
    "citationCount": 87,
    "openAccessPdf": None,
}

# ArXiv-only paper without a DOI (preprint case).
ARXIV_ONLY_PAPER = {
    "paperId": "xyz789",
    "externalIds": {"ArXiv": "2401.01234", "CorpusId": "99999"},
    "title": "A Digital-Humanities Method Paper",
    "year": 2024,
    "authors": [
        {"name": "Jane Doe"},
        {"name": "John Smith"},
    ],
    "publicationTypes": ["Preprint"],
    "citationCount": 0,
    "openAccessPdf": {"url": "https://arxiv.org/pdf/2401.01234"},
}


def test_format_authors_flips_to_family_first() -> None:
    paper = {"authors": [{"name": "Rudolph Cohen"}, {"name": "Yigal Yisrael"}]}
    assert _format_authors(paper) == ["Cohen, Rudolph", "Yisrael, Yigal"]


def test_format_authors_keeps_single_token() -> None:
    paper = {"authors": [{"name": "Madonna"}]}
    assert _format_authors(paper) == ["Madonna"]


def test_format_journal_prefers_structured_over_venue() -> None:
    assert _format_journal_or_volume(COHEN_1979_PAPER) == (
        "Bulletin of the American Schools of Oriental Research 236"
    )
    # No journal block → fall through to venue.
    assert _format_journal_or_volume({"venue": "ICCS Proceedings"}) == "ICCS Proceedings"
    # Nothing usable.
    assert _format_journal_or_volume({}) is None


def test_format_pages_from_journal_block() -> None:
    assert _format_pages(COHEN_1979_PAPER) == "61-79"
    assert _format_pages({"journal": {}}) is None
    assert _format_pages({}) is None


def test_publication_status_detects_preprint() -> None:
    assert _publication_status(ARXIV_ONLY_PAPER) is PublicationStatus.PREPRINT
    assert _publication_status(COHEN_1979_PAPER) is PublicationStatus.PUBLISHED
    assert _publication_status({"publicationTypes": []}) is PublicationStatus.PUBLISHED


def test_verification_note_surfaces_citation_count() -> None:
    assert _verification_note(COHEN_1979_PAPER) == "citation_count=87"
    # Zero counts are not surfaced — they're noise on new papers.
    assert _verification_note({"citationCount": 0}) is None
    assert _verification_note({}) is None


def test_build_params_basic() -> None:
    params = _build_params("Negev forts", 5, None, None)
    assert ("query", "Negev forts") in params
    assert ("limit", "5") in params
    assert any(k == "fields" for k, _ in params)
    assert all(k != "year" for k, _ in params)


def test_build_params_year_filter_open_ended() -> None:
    """S2 accepts ``2010-`` and ``-2010`` for open-ended ranges."""
    assert ("year", "2010-") in _build_params("x", 5, 2010, None)
    assert ("year", "-2010") in _build_params("x", 5, None, 2010)
    assert ("year", "2005-2012") in _build_params("x", 5, 2005, 2012)


def test_build_params_clamps_limit() -> None:
    high = _build_params("x", 9999, None, None)
    low = _build_params("x", 0, None, None)
    assert ("limit", "100") in high
    assert ("limit", "1") in low


def test_paper_to_paper_doi_form() -> None:
    p = _paper_to_paper(COHEN_1979_PAPER)
    assert p is not None
    assert p.source == "semantic_scholar"
    assert p.doi_or_id == "10.2307/1356668"
    assert p.identifiers is not None
    assert p.identifiers.doi == "10.2307/1356668"
    assert p.identifiers.semantic_scholar_id == "abc123def456"
    assert p.identifiers.arxiv_id is None
    assert p.pages == "61-79"
    assert p.audit is not None and p.audit.verification_note == "citation_count=87"
    # Inline citation: DOI present → Author-Year against doi.org.
    assert p.inline_citation is not None
    assert p.inline_citation.markdown == (
        "[(Cohen 1979)](https://doi.org/10.2307/1356668)"
    )


def test_paper_to_paper_arxiv_only_falls_back_to_arxiv_landing() -> None:
    p = _paper_to_paper(ARXIV_ONLY_PAPER)
    assert p is not None
    assert p.doi_or_id == "arxiv:2401.01234"
    assert p.identifiers is not None
    assert p.identifiers.doi is None
    assert p.identifiers.arxiv_id == "2401.01234"
    assert p.identifiers.semantic_scholar_id == "xyz789"
    assert str(p.landing_page_url) == "https://arxiv.org/abs/2401.01234"
    assert p.publication_status is PublicationStatus.PREPRINT
    # OA URL is surfaced.
    assert str(p.open_access_url) == "https://arxiv.org/pdf/2401.01234"
    # Inline citation: no DOI → Author-Year against arxiv.org.
    assert p.inline_citation is not None
    assert p.inline_citation.markdown == (
        "[(Doe & Smith 2024)](https://arxiv.org/abs/2401.01234)"
    )


def test_paper_with_only_s2_id_uses_s2_landing() -> None:
    """Edge case: bibliographic record S2 indexed without any external ID."""
    minimal = {
        "paperId": "xx000",
        "externalIds": {},
        "title": "Untraceable working paper",
        "year": 2020,
        "authors": [{"name": "Anonymous Author"}],
    }
    p = _paper_to_paper(minimal)
    assert p is not None
    assert p.doi_or_id == "s2:xx000"
    assert str(p.landing_page_url) == "https://www.semanticscholar.org/paper/xx000"
    assert p.inline_citation is not None
    assert (
        p.inline_citation.markdown
        == "[(Author 2020)](https://www.semanticscholar.org/paper/xx000)"
    )


def test_paper_without_any_anchor_is_dropped() -> None:
    """No DOI, no ArXiv, no paperId → drop, do not fabricate."""
    paper = {
        "paperId": "",
        "externalIds": {},
        "title": "An untraceable record",
        "year": 2024,
        "authors": [{"name": "Someone"}],
    }
    assert _paper_to_paper(paper) is None


@pytest.mark.asyncio
@respx.mock
async def test_search_s2_impl_happy_path() -> None:
    respx.get(S2_API).mock(
        return_value=httpx.Response(
            200,
            json={"total": 1, "data": [COHEN_1979_PAPER]},
        )
    )
    results = await search_semantic_scholar_impl("Negev fortresses", max_results=5)
    assert len(results) == 1
    p = results[0]
    assert isinstance(p, DAOPaper)
    assert p.source == "semantic_scholar"
    assert p.inline_citation is not None
    assert p.inline_citation.markdown.startswith("[(Cohen 1979)]")


@pytest.mark.asyncio
@respx.mock
async def test_search_s2_impl_empty() -> None:
    respx.get(S2_API).mock(
        return_value=httpx.Response(200, json={"total": 0, "data": []})
    )
    assert await search_semantic_scholar_impl("xyzzy") == []


@pytest.mark.asyncio
@respx.mock
async def test_search_s2_impl_http_error_propagates() -> None:
    respx.get(S2_API).mock(return_value=httpx.Response(503))
    with pytest.raises(httpx.HTTPStatusError):
        await search_semantic_scholar_impl("anything")


@pytest.mark.asyncio
@respx.mock
async def test_search_s2_impl_query_params_forwarded() -> None:
    route = respx.get(S2_API).mock(
        return_value=httpx.Response(200, json={"total": 0, "data": []})
    )
    await search_semantic_scholar_impl(
        "Boaretto Atar Haroa",
        max_results=3,
        year_from=2005,
        year_to=2012,
    )
    assert route.called
    sent_url = str(route.calls.last.request.url)
    assert "query=" in sent_url
    assert "fields=" in sent_url
    assert "year=2005-2012" in sent_url


@pytest.mark.asyncio
@respx.mock
async def test_search_s2_impl_sends_api_key_when_env_set(monkeypatch) -> None:
    monkeypatch.setenv("SEMANTIC_SCHOLAR_API_KEY", "test-key-xyz")
    route = respx.get(S2_API).mock(
        return_value=httpx.Response(200, json={"total": 0, "data": []})
    )
    await search_semantic_scholar_impl("anything")
    assert route.calls.last.request.headers.get("x-api-key") == "test-key-xyz"


@pytest.mark.asyncio
@respx.mock
async def test_search_s2_impl_omits_api_key_when_env_unset(monkeypatch) -> None:
    monkeypatch.delenv("SEMANTIC_SCHOLAR_API_KEY", raising=False)
    route = respx.get(S2_API).mock(
        return_value=httpx.Response(200, json={"total": 0, "data": []})
    )
    await search_semantic_scholar_impl("anything")
    assert "x-api-key" not in route.calls.last.request.headers
