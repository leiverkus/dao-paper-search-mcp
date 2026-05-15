"""Tests for the bioRxiv (+ medRxiv) adapter.

The fixture below is shape-faithful to the real Europe PMC
``SRC:PPR`` response (probed 2026-05-15).
"""

from __future__ import annotations

import httpx
import pytest
import respx

from dao_paper_search_mcp.adapters.biorxiv import (
    EUROPEPMC_API,
    _build_params,
    _doi_from_record,
    _journal_matches,
    _open_access_url,
    _parse_author_string,
    _record_to_paper,
    _strip_markup,
    search_biorxiv_impl,
)
from dao_paper_search_mcp.models import DAOPaper, PublicationStatus


# A realistic bioRxiv preprint record from Europe PMC. Mirrors the
# Lazaridis-Levant-aDNA paper genre that's the canonical use case.
LAZARIDIS_BIORXIV = {
    "id": "PPR648654",
    "source": "PPR",
    "doi": "10.1101/2024.01.15.575707",
    "title": "Ancient DNA from the Iron Age Southern Levant",
    "authorString": "Lazaridis I, Patterson N, Reich D",
    "journalTitle": "bioRxiv",
    "pubYear": "2024",
    "firstPublicationDate": "2024-01-15",
    "abstractText": "We report <em>aDNA</em> from Iron Age contexts in the southern Levant.",
    "fullTextUrlList": {
        "fullTextUrl": [
            {
                "url": "https://www.biorxiv.org/content/10.1101/2024.01.15.575707v1.full.pdf",
                "documentStyle": "pdf",
            }
        ]
    },
}

# A medRxiv record — should be included unless include_medrxiv=False.
MEDRXIV_RECORD = {
    "id": "PPR111222",
    "doi": "10.1101/2024.02.20.999999",
    "title": "Paleopathology of Iron Age Skeletons",
    "authorString": "Müller A, Schmidt B",
    "journalTitle": "medRxiv",
    "pubYear": "2024",
    "fullTextUrlList": {
        "fullTextUrl": [
            {
                "url": "https://www.medrxiv.org/content/10.1101/2024.02.20.999999v1.full.pdf",
                "documentStyle": "pdf",
            }
        ]
    },
}

# A ResearchSquare preprint — SRC:PPR returns these too; should be
# filtered out client-side.
RESEARCHSQUARE_RECORD = {
    "id": "PPR333444",
    "doi": "10.21203/rs.3.rs-12345/v1",
    "title": "A ResearchSquare preprint that should be filtered out",
    "authorString": "Someone E",
    "journalTitle": "ResearchSquare",
    "pubYear": "2024",
}


def test_strip_markup_removes_html_and_collapses_whitespace() -> None:
    raw = "<p>An <em>aDNA</em> abstract.\n\nMore text.</p>"
    assert _strip_markup(raw) == "An aDNA abstract. More text."
    assert _strip_markup(None) is None
    assert _strip_markup("") is None


def test_parse_author_string_flips_last_to_family_initials() -> None:
    """Europe PMC's authorString is comma-separated "Family Initials"."""
    out = _parse_author_string("Lazaridis I, Patterson N, Reich D")
    assert out == ["Lazaridis, I", "Patterson, N", "Reich, D"]


def test_parse_author_string_handles_multi_initial() -> None:
    out = _parse_author_string("Smith JR, Doe AB")
    assert out == ["Smith, JR", "Doe, AB"]


def test_parse_author_string_preserves_single_token() -> None:
    out = _parse_author_string("Anonymous")
    assert out == ["Anonymous"]


def test_doi_from_record_top_level() -> None:
    assert _doi_from_record(LAZARIDIS_BIORXIV) == "10.1101/2024.01.15.575707"


def test_doi_from_record_via_fulltext_id_list() -> None:
    """Some records carry the DOI inside fullTextIdList instead of top-level."""
    record = {
        "doi": None,
        "fullTextIdList": {"fullTextId": ["PMC12345", "10.1101/xyz.2024.01"]},
    }
    assert _doi_from_record(record) == "10.1101/xyz.2024.01"


def test_doi_from_record_returns_none_when_absent() -> None:
    assert _doi_from_record({}) is None
    assert _doi_from_record({"doi": ""}) is None


def test_open_access_url_prefers_biorxiv_pdf() -> None:
    record = {
        "fullTextUrlList": {
            "fullTextUrl": [
                {"url": "https://europepmc.org/abstract/PPR/PPR648654", "documentStyle": "abstract"},
                {"url": "https://www.biorxiv.org/content/.../full.pdf", "documentStyle": "pdf"},
            ]
        }
    }
    assert _open_access_url(record) == "https://www.biorxiv.org/content/.../full.pdf"


def test_open_access_url_returns_none_when_no_pdf() -> None:
    assert _open_access_url({"fullTextUrlList": {"fullTextUrl": []}}) is None
    assert _open_access_url({}) is None


def test_journal_matches_biorxiv_always_included() -> None:
    assert _journal_matches("bioRxiv", include_medrxiv=False) is True
    assert _journal_matches("biorxiv", include_medrxiv=False) is True  # case-insens
    assert _journal_matches("bioRxiv", include_medrxiv=True) is True


def test_journal_matches_medrxiv_gated_by_flag() -> None:
    assert _journal_matches("medRxiv", include_medrxiv=True) is True
    assert _journal_matches("medRxiv", include_medrxiv=False) is False


def test_journal_matches_other_preprint_servers_excluded() -> None:
    assert _journal_matches("ResearchSquare", include_medrxiv=True) is False
    assert _journal_matches("SSRN", include_medrxiv=True) is False
    assert _journal_matches("OSF Preprints", include_medrxiv=True) is False


def test_build_params_basic() -> None:
    params = _build_params("aDNA Levant", 10, None, None)
    query = next(v for k, v in params if k == "query")
    assert "SRC:PPR" in query
    assert "(aDNA Levant)" in query
    assert ("resulttype", "core") in params
    assert ("format", "json") in params


def test_build_params_year_filter() -> None:
    query = next(v for k, v in _build_params("x", 5, 2020, 2024) if k == "query")
    assert "PUB_YEAR:[2020 TO 2024]" in query


def test_build_params_page_size_has_headroom_for_filtering() -> None:
    """The upstream page is doubled so we have headroom after the
    client-side bioRxiv/medRxiv filter trims ResearchSquare hits."""
    params = _build_params("x", 10, None, None)
    assert ("pageSize", "20") in params
    # And clamped at 100.
    params = _build_params("x", 60, None, None)
    assert ("pageSize", "100") in params


def test_record_to_paper_biorxiv_doi_form() -> None:
    p = _record_to_paper(LAZARIDIS_BIORXIV)
    assert p is not None
    assert p.source == "biorxiv"
    assert p.doi_or_id == "10.1101/2024.01.15.575707"
    assert p.identifiers is not None
    assert p.identifiers.doi == "10.1101/2024.01.15.575707"
    assert p.identifiers.europepmc_id == "PPR648654"
    assert p.publication_status is PublicationStatus.PREPRINT
    assert p.year == 2024
    assert p.authors == ["Lazaridis, I", "Patterson, N", "Reich, D"]
    # JATS-style markup in abstract is stripped.
    assert p.abstract is not None
    assert "<em>" not in p.abstract
    assert "aDNA" in p.abstract
    # Inline citation: 3 authors → et al. against DOI.
    assert p.inline_citation is not None
    assert p.inline_citation.markdown_recommended == (
        "[(Lazaridis et al. 2024)](https://doi.org/10.1101/2024.01.15.575707)"
    )


def test_record_to_paper_without_doi_uses_epmc_landing() -> None:
    """Very-recent preprints whose DOI hasn't reached Europe PMC yet."""
    record = dict(LAZARIDIS_BIORXIV)
    record["doi"] = None
    p = _record_to_paper(record)
    assert p is not None
    assert p.doi_or_id == "epmc:PPR648654"
    assert str(p.landing_page_url) == "https://europepmc.org/article/PPR/PPR648654"
    assert p.identifiers is not None
    assert p.identifiers.doi is None
    assert p.identifiers.europepmc_id == "PPR648654"
    assert p.inline_citation is not None
    assert p.inline_citation.markdown_recommended == (
        "[(Lazaridis et al. 2024)](https://europepmc.org/article/PPR/PPR648654)"
    )


def test_record_without_doi_or_epmc_id_is_dropped() -> None:
    record = dict(LAZARIDIS_BIORXIV)
    record["doi"] = None
    record["id"] = None  # type: ignore[assignment]
    assert _record_to_paper(record) is None


@pytest.mark.asyncio
@respx.mock
async def test_search_biorxiv_impl_happy_path() -> None:
    respx.get(EUROPEPMC_API).mock(
        return_value=httpx.Response(
            200,
            json={
                "hitCount": 1,
                "resultList": {"result": [LAZARIDIS_BIORXIV]},
            },
        )
    )
    results = await search_biorxiv_impl("Levant aDNA", max_results=5)
    assert len(results) == 1
    p = results[0]
    assert isinstance(p, DAOPaper)
    assert p.source == "biorxiv"
    assert p.inline_citation is not None
    assert p.inline_citation.markdown_recommended.startswith("[(Lazaridis et al. 2024)]")


@pytest.mark.asyncio
@respx.mock
async def test_search_biorxiv_impl_filters_out_non_target_servers() -> None:
    """Europe PMC's SRC:PPR also returns ResearchSquare etc. — must be filtered."""
    respx.get(EUROPEPMC_API).mock(
        return_value=httpx.Response(
            200,
            json={
                "hitCount": 3,
                "resultList": {
                    "result": [LAZARIDIS_BIORXIV, MEDRXIV_RECORD, RESEARCHSQUARE_RECORD]
                },
            },
        )
    )
    results = await search_biorxiv_impl("anything", max_results=10)
    sources = {p.journal_or_volume for p in results}
    assert sources == {"bioRxiv", "medRxiv"}
    # ResearchSquare is filtered out.
    assert all(p.journal_or_volume != "ResearchSquare" for p in results)


@pytest.mark.asyncio
@respx.mock
async def test_search_biorxiv_impl_include_medrxiv_false_drops_medrxiv() -> None:
    respx.get(EUROPEPMC_API).mock(
        return_value=httpx.Response(
            200,
            json={
                "hitCount": 2,
                "resultList": {"result": [LAZARIDIS_BIORXIV, MEDRXIV_RECORD]},
            },
        )
    )
    results = await search_biorxiv_impl("anything", max_results=10, include_medrxiv=False)
    assert len(results) == 1
    assert results[0].journal_or_volume == "bioRxiv"


@pytest.mark.asyncio
@respx.mock
async def test_search_biorxiv_impl_respects_max_results_after_filter() -> None:
    """max_results applies AFTER the journalTitle filter, not before."""
    respx.get(EUROPEPMC_API).mock(
        return_value=httpx.Response(
            200,
            json={
                "hitCount": 4,
                "resultList": {
                    "result": [
                        LAZARIDIS_BIORXIV,
                        MEDRXIV_RECORD,
                        RESEARCHSQUARE_RECORD,
                        dict(LAZARIDIS_BIORXIV),
                    ]
                },
            },
        )
    )
    results = await search_biorxiv_impl("anything", max_results=2)
    assert len(results) == 2
    # All passing items are bioRxiv/medRxiv (ResearchSquare gets dropped first).
    assert all(p.journal_or_volume in {"bioRxiv", "medRxiv"} for p in results)


@pytest.mark.asyncio
@respx.mock
async def test_search_biorxiv_impl_empty() -> None:
    respx.get(EUROPEPMC_API).mock(
        return_value=httpx.Response(
            200,
            json={"hitCount": 0, "resultList": {"result": []}},
        )
    )
    assert await search_biorxiv_impl("xyzzy") == []


@pytest.mark.asyncio
@respx.mock
async def test_search_biorxiv_impl_http_error_propagates() -> None:
    respx.get(EUROPEPMC_API).mock(return_value=httpx.Response(503))
    with pytest.raises(httpx.HTTPStatusError):
        await search_biorxiv_impl("anything")


@pytest.mark.asyncio
@respx.mock
async def test_search_biorxiv_impl_query_params_forwarded() -> None:
    route = respx.get(EUROPEPMC_API).mock(
        return_value=httpx.Response(
            200,
            json={"hitCount": 0, "resultList": {"result": []}},
        )
    )
    await search_biorxiv_impl(
        "aDNA Iron Age",
        max_results=5,
        year_from=2020,
        year_to=2024,
    )
    assert route.called
    sent_url = str(route.calls.last.request.url)
    assert "SRC%3APPR" in sent_url or "SRC:PPR" in sent_url
    assert "PUB_YEAR" in sent_url
    assert "2020" in sent_url and "2024" in sent_url
