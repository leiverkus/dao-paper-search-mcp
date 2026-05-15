"""Tests for the IAA Publications adapter.

The adapter is MVP-incomplete because the live upstream renders results
client-side. These tests pin two contracts:

1. **Tripwire test:** when the upstream returns the JS-only HTML (empty
   ``#results-list``), we raise IAAUnavailableError with a clear note
   instead of returning silently empty. This is the explicit
   anti-silent-failure contract from the briefing.
2. **Parser-ready test:** when BePress (eventually) returns a
   server-rendered results page, the parser already produces correct
   DAOPaper objects. This way no code change is needed when the upstream
   recovers — only the xfail markers in the verification suite are flipped.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from dao_paper_search_mcp.adapters.iaa import (
    IAA_SEARCH,
    IAAUnavailableError,
    _build_params,
    _detect_language,
    _parse_results,
    search_iaa_impl,
)
from dao_paper_search_mcp.models import DAOPaper

FIXTURES = Path(__file__).parent / "fixtures"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_detect_language_hebrew() -> None:
    assert _detect_language("חפירות בכדש ברנע") == "he"


def test_detect_language_english() -> None:
    assert _detect_language("Excavations at En Haseva") == "en"


def test_detect_language_neither() -> None:
    assert _detect_language("123 456") == "und"


def test_build_params_basic() -> None:
    assert _build_params("Cohen", 10, None) == {"q": "Cohen"}


def test_build_params_with_report_type() -> None:
    assert _build_params("Cohen", 10, "report") == {"q": "Cohen", "context": "iaareports"}
    assert _build_params("Cohen", 10, "atiqot") == {"q": "Cohen", "context": "atiqot"}
    assert _build_params("Cohen", 10, "ha-esi") == {"q": "Cohen", "context": "hadashot"}


def test_build_params_drops_unknown_report_type() -> None:
    assert _build_params("Cohen", 10, "nonsense") == {"q": "Cohen"}


def test_parse_results_empty_raises_tripwire() -> None:
    """The real IAA HTML (results loaded via JS) MUST raise
    IAAUnavailableError. A silent empty return would let the calling
    agent assume "no hits", which would be a correctness bug."""
    html = _read("iaa_search_empty.html")
    with pytest.raises(IAAUnavailableError):
        _parse_results(html)


def test_parse_results_server_rendered() -> None:
    """When upstream returns server-rendered results, parsing produces
    correct DAOPaper objects with no code changes required."""
    html = _read("iaa_search_with_results.html")
    papers = _parse_results(html)
    assert len(papers) == 3
    assert all(isinstance(p, DAOPaper) for p in papers)
    assert all(p.source == "iaa" for p in papers)

    first = papers[0]
    assert "En Ḥaṣeva" in first.title or "En Haseva" in first.title or "ʿEn" in first.title
    assert first.year == 2019
    assert first.authors == ["Cohen, R.", "Yisrael, Y."]
    assert first.language == "en"
    assert str(first.landing_page_url) == "https://publications.iaa.org.il/atiqot/95/3"
    assert first.doi_or_id == "iaa:atiqot/95/3"

    hebrew = papers[2]
    assert hebrew.language == "he"
    assert hebrew.year == 2012


def test_parse_results_missing_container_raises() -> None:
    with pytest.raises(IAAUnavailableError):
        _parse_results("<html><body><p>nothing here</p></body></html>")


@pytest.mark.asyncio
@respx.mock
async def test_search_iaa_impl_empty_results_raises() -> None:
    """End-to-end: live-shape empty HTML response must surface as
    IAAUnavailableError to the MCP caller."""
    respx.get(IAA_SEARCH).mock(
        return_value=httpx.Response(200, text=_read("iaa_search_empty.html"))
    )
    with pytest.raises(IAAUnavailableError):
        await search_iaa_impl("Cohen Negev")


@pytest.mark.asyncio
@respx.mock
async def test_search_iaa_impl_server_rendered_happy_path() -> None:
    respx.get(IAA_SEARCH).mock(
        return_value=httpx.Response(200, text=_read("iaa_search_with_results.html"))
    )
    papers = await search_iaa_impl("Negev fortresses", max_results=10)
    assert len(papers) == 3
    assert papers[0].source == "iaa"


@pytest.mark.asyncio
@respx.mock
async def test_search_iaa_impl_truncates_to_max_results() -> None:
    respx.get(IAA_SEARCH).mock(
        return_value=httpx.Response(200, text=_read("iaa_search_with_results.html"))
    )
    papers = await search_iaa_impl("x", max_results=2)
    assert len(papers) == 2


@pytest.mark.asyncio
@respx.mock
async def test_search_iaa_impl_504_propagates() -> None:
    """The intermittent 504s seen on 2026-05-15 must surface as
    HTTPStatusError, not silent empty."""
    respx.get(IAA_SEARCH).mock(return_value=httpx.Response(504, text="<html>504</html>"))
    with pytest.raises(httpx.HTTPStatusError):
        await search_iaa_impl("Cohen")
