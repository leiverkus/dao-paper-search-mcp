"""Tests for the CORE adapter."""

from __future__ import annotations

import httpx
import pytest
import respx

from dao_paper_search_mcp.adapters.core import (
    CORE_API,
    CoreMissingApiKey,
    _build_params,
    _data_provider_name,
    _format_authors,
    _is_aggregator,
    _open_access_url,
    _verification_note,
    _work_to_paper,
    search_core_impl,
)
from dao_paper_search_mcp.models import DAOPaper, PublicationStatus


# Realistic CORE v3 work — DOI-bearing, primary institutional repo.
DOI_WORK = {
    "id": 1234567,
    "doi": "10.1080/00758914.2024.2379655",
    "title": "Standing at the crossroads—'En Ḥaẓeva in the Early Iron Age IIA",
    "abstract": "We report on excavations at 'En Ḥaẓeva…",
    "yearPublished": 2024,
    "authors": [
        {"name": "Adi Eliyahu-Behar"},
        {"name": "Liora Freud"},
    ],
    "documentType": "research",
    "publicationType": "journal",
    "language": {"code": "en", "name": "English"},
    "dataProvider": {"name": "University of Haifa Repository", "type": "repository"},
    "downloadUrl": "https://haifa.example.org/eprint/12345/1/Crossroads.pdf",
}

# Aggregator-sourced work — should be flagged warn_marker.
RESEARCHGATE_WORK = {
    "id": 9999000,
    "doi": None,
    "title": "An archaeological note",
    "yearPublished": 2020,
    "authors": [{"name": "Some Author"}],
    "documentType": "research",
    "dataProvider": {"name": "ResearchGate", "type": "aggregator"},
    "downloadUrl": "https://www.researchgate.net/publication/12345/file.pdf",
}

# Thesis with no DOI — should still land via core_id, document_type
# verification_note surfaced.
THESIS_WORK = {
    "id": 5555444,
    "doi": None,
    "title": "A doctoral dissertation on Iron Age fortifications",
    "yearPublished": 2018,
    "authors": [{"name": "Jane Researcher"}],
    "documentType": "thesis",
    "dataProvider": {"name": "Tel Aviv University Library", "type": "repository"},
    "downloadUrl": None,
    "sourceFulltextUrls": ["https://primage.tau.ac.il/libraries/theses/12345.pdf"],
}


def test_format_authors_flips_to_family_first() -> None:
    work = {"authors": [{"name": "Adi Eliyahu-Behar"}, {"name": "Liora Freud"}]}
    assert _format_authors(work) == ["Eliyahu-Behar, Adi", "Freud, Liora"]


def test_format_authors_preserves_family_first_when_already_structured() -> None:
    work = {"authors": [{"name": "Cohen, R."}, {"name": "Yisrael, Y."}]}
    assert _format_authors(work) == ["Cohen, R.", "Yisrael, Y."]


def test_data_provider_name_extraction() -> None:
    assert _data_provider_name(DOI_WORK) == "University of Haifa Repository"
    assert _data_provider_name({}) is None
    assert _data_provider_name({"dataProvider": {}}) is None


def test_is_aggregator_detection() -> None:
    assert _is_aggregator("ResearchGate") is True
    assert _is_aggregator("Academia.edu") is True
    assert _is_aggregator("Google Books Project") is True
    assert _is_aggregator("University of Haifa Repository") is False
    assert _is_aggregator(None) is False
    assert _is_aggregator("") is False


def test_open_access_url_prefers_download_then_fulltext() -> None:
    assert _open_access_url(DOI_WORK) == (
        "https://haifa.example.org/eprint/12345/1/Crossroads.pdf"
    )
    # Falls back to sourceFulltextUrls when downloadUrl is absent.
    assert _open_access_url(THESIS_WORK) == (
        "https://primage.tau.ac.il/libraries/theses/12345.pdf"
    )
    assert _open_access_url({}) is None


def test_verification_note_for_non_article_types() -> None:
    assert _verification_note(THESIS_WORK) == "document_type=thesis"
    # "research" is normalised to article-like; no note.
    assert _verification_note(DOI_WORK) is None
    assert _verification_note({}) is None


def test_build_params_basic() -> None:
    body = _build_params("Negev forts", 5, None, None)
    assert body["q"] == "Negev forts"
    assert body["limit"] == 5
    assert body["offset"] == 0


def test_build_params_clamps_limit() -> None:
    assert _build_params("x", 9999, None, None)["limit"] == 100
    assert _build_params("x", 0, None, None)["limit"] == 1


def test_build_params_year_filter_appended_to_q() -> None:
    body = _build_params("x", 5, 2010, 2020)
    assert "yearPublished>=2010" in body["q"]
    assert "yearPublished<=2020" in body["q"]


def test_work_to_paper_doi_form() -> None:
    p = _work_to_paper(DOI_WORK)
    assert p is not None
    assert p.source == "core"
    assert p.doi_or_id == "10.1080/00758914.2024.2379655"
    assert p.identifiers is not None
    assert p.identifiers.doi == "10.1080/00758914.2024.2379655"
    assert p.identifiers.core_id == "1234567"
    assert p.audit is not None
    assert p.audit.aggregator is False
    assert p.audit.warn_marker is False
    # Inline citation: DOI present → Author-Year against doi.org, no ⚠️.
    assert p.inline_citation is not None
    assert p.inline_citation.markdown_recommended == (
        "[(Eliyahu-Behar & Freud 2024)](https://doi.org/10.1080/00758914.2024.2379655)"
    )


def test_work_to_paper_aggregator_flagged_with_warn() -> None:
    p = _work_to_paper(RESEARCHGATE_WORK)
    assert p is not None
    assert p.audit is not None
    assert p.audit.aggregator is True
    assert p.audit.warn_marker is True
    assert p.audit.primary_source is False
    assert p.audit.verification_note is not None
    assert "aggregator=ResearchGate" in p.audit.verification_note
    # Inline citation: aggregator → ⚠️ + domain-title form (not Author-Year).
    assert p.inline_citation is not None
    assert p.inline_citation.markdown_recommended.startswith("⚠️[(core.ac.uk")


def test_work_to_paper_thesis_no_doi_uses_core_landing() -> None:
    p = _work_to_paper(THESIS_WORK)
    assert p is not None
    assert p.doi_or_id == "core:5555444"
    assert p.identifiers is not None
    assert p.identifiers.doi is None
    assert p.identifiers.core_id == "5555444"
    assert str(p.landing_page_url) == "https://core.ac.uk/works/5555444"
    assert p.verification_note == "document_type=thesis"
    # Author-Year form against CORE landing.
    assert p.inline_citation is not None
    assert p.inline_citation.markdown_recommended == (
        "[(Researcher 2018)](https://core.ac.uk/works/5555444)"
    )


def test_work_without_doi_or_core_id_is_dropped() -> None:
    work = dict(DOI_WORK)
    work["id"] = None
    work["doi"] = None
    assert _work_to_paper(work) is None


@pytest.mark.asyncio
@respx.mock
async def test_search_core_impl_raises_when_api_key_missing(monkeypatch) -> None:
    monkeypatch.delenv("CORE_API_KEY", raising=False)
    with pytest.raises(CoreMissingApiKey):
        await search_core_impl("anything")


@pytest.mark.asyncio
@respx.mock
async def test_search_core_impl_happy_path(monkeypatch) -> None:
    monkeypatch.setenv("CORE_API_KEY", "test-key-xyz")
    respx.post(CORE_API).mock(
        return_value=httpx.Response(
            200,
            json={"totalHits": 1, "results": [DOI_WORK]},
        )
    )
    results = await search_core_impl("'En Ḥaẓeva crossroads", max_results=5)
    assert len(results) == 1
    p = results[0]
    assert isinstance(p, DAOPaper)
    assert p.source == "core"
    assert p.inline_citation is not None
    assert p.inline_citation.markdown_recommended.startswith("[(Eliyahu-Behar")


@pytest.mark.asyncio
@respx.mock
async def test_search_core_impl_sends_bearer_token(monkeypatch) -> None:
    monkeypatch.setenv("CORE_API_KEY", "test-bearer-xyz")
    route = respx.post(CORE_API).mock(
        return_value=httpx.Response(200, json={"totalHits": 0, "results": []})
    )
    await search_core_impl("anything")
    assert route.calls.last.request.headers.get("Authorization") == "Bearer test-bearer-xyz"


@pytest.mark.asyncio
@respx.mock
async def test_search_core_impl_empty(monkeypatch) -> None:
    monkeypatch.setenv("CORE_API_KEY", "test-key")
    respx.post(CORE_API).mock(
        return_value=httpx.Response(200, json={"totalHits": 0, "results": []})
    )
    assert await search_core_impl("xyzzy") == []


@pytest.mark.asyncio
@respx.mock
async def test_search_core_impl_http_error_propagates(monkeypatch) -> None:
    monkeypatch.setenv("CORE_API_KEY", "test-key")
    respx.post(CORE_API).mock(return_value=httpx.Response(503))
    with pytest.raises(httpx.HTTPStatusError):
        await search_core_impl("anything")
