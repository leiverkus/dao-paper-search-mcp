"""Tests for the Zenodo adapter."""

from __future__ import annotations

import httpx
import pytest
import respx

from dao_paper_search_mcp.adapters.zenodo import (
    ZENODO_API,
    _build_params,
    _extract_year,
    _format_authors,
    _open_access_url,
    _record_to_paper,
    _resource_type_key,
    _strip_html,
    _verification_note,
    search_zenodo_impl,
)
from dao_paper_search_mcp.models import DAOPaper, PublicationStatus


# Realistic Zenodo record — a DH software release (non-article type).
SOFTWARE_RECORD = {
    "id": 1234567,
    "doi": "10.5281/zenodo.1234567",
    "metadata": {
        "title": "iDAI.fieldRAG: Retrieval-augmented archaeology corpus tools",
        "creators": [
            {"name": "Schmidt, Anna", "affiliation": "DAI Berlin"},
            {"name": "Kohli, Ravi"},
        ],
        "publication_date": "2024-03-15",
        "resource_type": {"type": "software", "subtype": "main"},
        "description": "<p>A Python library for <strong>RAG</strong>-based corpus parsing.</p>",
        "language": "eng",
        "doi": "10.5281/zenodo.1234567",
    },
    "files": [
        {"links": {"self": "https://zenodo.org/api/records/1234567/files/v1.0.zip/content"}}
    ],
}

# Article-type record — should not carry a verification_note.
ARTICLE_RECORD = {
    "id": 7654321,
    "doi": "10.5281/zenodo.7654321",
    "metadata": {
        "title": "Iron Age Pottery from Tel Reḥov",
        "creators": [{"name": "Mazar, Amihai"}],
        "publication_date": "2018-06-01",
        "resource_type": {"type": "publication", "subtype": "article"},
        "description": "<p>Plain text abstract</p>",
        "language": "en",
    },
    "files": [],
}

# Preprint-type record.
PREPRINT_RECORD = {
    "id": 555,
    "doi": "10.5281/zenodo.555",
    "metadata": {
        "title": "A working paper on chronology debates",
        "creators": [{"name": "Doe, Jane"}],
        "publication_date": "2024",
        "resource_type": {"type": "publication", "subtype": "preprint"},
    },
    "files": [],
}


def test_strip_html_collapses_whitespace_and_removes_tags() -> None:
    raw = "<p>Hello <em>world</em>!\n\n  More text.</p>"
    assert _strip_html(raw) == "Hello world! More text."
    assert _strip_html(None) is None
    assert _strip_html("") is None


def test_format_authors_preserves_family_first() -> None:
    """Zenodo returns ``"Family, Given"`` — pass through unchanged."""
    md = {
        "creators": [
            {"name": "Schmidt, Anna"},
            {"name": "Kohli, Ravi"},
        ]
    }
    assert _format_authors(md) == ["Schmidt, Anna", "Kohli, Ravi"]


def test_extract_year_handles_full_date_or_year_only() -> None:
    assert _extract_year({"publication_date": "2024-03-15"}) == 2024
    assert _extract_year({"publication_date": "2024"}) == 2024
    assert _extract_year({"publication_date": ""}) is None
    assert _extract_year({}) is None


def test_resource_type_key_composition() -> None:
    assert (
        _resource_type_key({"resource_type": {"type": "software", "subtype": "main"}})
        == "software-main"
    )
    assert (
        _resource_type_key({"resource_type": {"type": "publication", "subtype": "article"}})
        == "publication-article"
    )
    # Top-level only when subtype is absent.
    assert _resource_type_key({"resource_type": {"type": "dataset"}}) == "dataset"
    assert _resource_type_key({}) is None


def test_verification_note_only_for_non_article_types() -> None:
    assert _verification_note("publication-article") is None
    assert _verification_note("publication-book") is None
    assert _verification_note("software-main") == "resource_type=software-main"
    assert _verification_note("dataset") == "resource_type=dataset"
    assert _verification_note(None) is None


def test_open_access_url_picks_first_file() -> None:
    assert _open_access_url(SOFTWARE_RECORD) == (
        "https://zenodo.org/api/records/1234567/files/v1.0.zip/content"
    )
    assert _open_access_url(ARTICLE_RECORD) is None
    assert _open_access_url({"files": []}) is None


def test_build_params_basic() -> None:
    params = _build_params("RAG archaeology", 5, None, None)
    assert ("q", "RAG archaeology") in params
    assert ("size", "5") in params
    assert ("page", "1") in params


def test_build_params_year_filter() -> None:
    params = _build_params("x", 5, 2010, 2020)
    q = next(v for k, v in params if k == "q")
    assert "AND year:[2010 TO 2020]" in q


def test_build_params_year_filter_open_ended() -> None:
    """Open-ended ranges use ``*`` per Elasticsearch syntax."""
    open_lo = next(v for k, v in _build_params("x", 5, None, 2020) if k == "q")
    open_hi = next(v for k, v in _build_params("x", 5, 2010, None) if k == "q")
    assert "AND year:[* TO 2020]" in open_lo
    assert "AND year:[2010 TO *]" in open_hi


def test_build_params_clamps_size() -> None:
    assert ("size", "100") in _build_params("x", 9999, None, None)
    assert ("size", "1") in _build_params("x", 0, None, None)


def test_record_to_paper_article_type() -> None:
    p = _record_to_paper(ARTICLE_RECORD)
    assert p is not None
    assert p.source == "zenodo"
    assert p.doi_or_id == "10.5281/zenodo.7654321"
    assert p.identifiers is not None
    assert p.identifiers.doi == "10.5281/zenodo.7654321"
    assert p.publication_status is PublicationStatus.PUBLISHED
    # No verification_note for article type.
    assert p.verification_note is None
    assert p.abstract == "Plain text abstract"
    # Inline citation: DOI present → Author-Year form.
    assert p.inline_citation is not None
    assert p.inline_citation.markdown == (
        "[(Mazar 2018)](https://doi.org/10.5281/zenodo.7654321)"
    )


def test_record_to_paper_software_flagged_in_verification_note() -> None:
    p = _record_to_paper(SOFTWARE_RECORD)
    assert p is not None
    assert p.verification_note == "resource_type=software-main"
    assert p.audit is not None
    assert p.audit.verification_note == "resource_type=software-main"
    # warn_marker stays False — software DOIs are legitimate citation targets.
    assert p.audit.warn_marker is False
    # Multi-author Author-Year against DOI.
    assert p.inline_citation is not None
    assert p.inline_citation.markdown == (
        "[(Schmidt & Kohli 2024)](https://doi.org/10.5281/zenodo.1234567)"
    )
    # File download URL surfaces as open_access_url.
    assert str(p.open_access_url) == (
        "https://zenodo.org/api/records/1234567/files/v1.0.zip/content"
    )


def test_record_to_paper_preprint_status() -> None:
    p = _record_to_paper(PREPRINT_RECORD)
    assert p is not None
    assert p.publication_status is PublicationStatus.PREPRINT
    assert p.verification_note == "resource_type=publication-preprint"


def test_record_without_doi_is_dropped() -> None:
    """Zenodo always assigns a DOI; a missing one means malformed/pre-deposit."""
    record = dict(ARTICLE_RECORD)
    record["doi"] = None
    record["metadata"] = dict(ARTICLE_RECORD["metadata"])
    record["metadata"]["doi"] = None  # type: ignore[index]
    assert _record_to_paper(record) is None


@pytest.mark.asyncio
@respx.mock
async def test_search_zenodo_impl_happy_path() -> None:
    respx.get(ZENODO_API).mock(
        return_value=httpx.Response(
            200,
            json={"hits": {"total": 1, "hits": [ARTICLE_RECORD]}},
        )
    )
    results = await search_zenodo_impl("Tel Rehov pottery", max_results=5)
    assert len(results) == 1
    p = results[0]
    assert isinstance(p, DAOPaper)
    assert p.source == "zenodo"
    assert p.inline_citation is not None
    assert p.inline_citation.markdown.startswith("[(Mazar 2018)]")


@pytest.mark.asyncio
@respx.mock
async def test_search_zenodo_impl_empty() -> None:
    respx.get(ZENODO_API).mock(
        return_value=httpx.Response(200, json={"hits": {"total": 0, "hits": []}})
    )
    assert await search_zenodo_impl("xyzzy") == []


@pytest.mark.asyncio
@respx.mock
async def test_search_zenodo_impl_http_error_propagates() -> None:
    respx.get(ZENODO_API).mock(return_value=httpx.Response(503))
    with pytest.raises(httpx.HTTPStatusError):
        await search_zenodo_impl("anything")


@pytest.mark.asyncio
@respx.mock
async def test_search_zenodo_impl_query_params_forwarded() -> None:
    route = respx.get(ZENODO_API).mock(
        return_value=httpx.Response(200, json={"hits": {"total": 0, "hits": []}})
    )
    await search_zenodo_impl(
        "RAG corpus tools",
        max_results=3,
        year_from=2020,
        year_to=2024,
    )
    assert route.called
    sent_url = str(route.calls.last.request.url)
    assert "q=" in sent_url
    assert "size=3" in sent_url
    # Year range is encoded into the q parameter.
    assert "2020" in sent_url and "2024" in sent_url
