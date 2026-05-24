"""Tests for the arXiv adapter.

The fixtures below are abbreviated but shape-faithful copies of real
arXiv Atom-API responses (probed 2026-05-15).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import httpx
import pytest
import respx

from dao_paper_search_mcp.adapters.arxiv import (
    ARXIV_API,
    _apply_year_filter,
    _build_params,
    _extract_arxiv_id,
    _format_authors,
    _normalize_query,
    _parse_atom,
    search_arxiv_impl,
)
from dao_paper_search_mcp.models import DAOPaper, PublicationStatus

# Realistic atom feed with two entries: one DOI-bearing (post-journal)
# and one preprint-only with an arxiv:doi missing.
SAMPLE_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2401.01234v2</id>
    <title>RAG for Archaeological Texts:
    A Multilingual Approach</title>
    <summary>We introduce a retrieval-augmented method for parsing
    German and Hebrew archaeology corpora.</summary>
    <published>2024-01-15T18:00:00Z</published>
    <updated>2024-02-01T12:00:00Z</updated>
    <author><name>Jane Doe</name></author>
    <author><name>John Smith</name></author>
    <author><name>Alice Müller</name></author>
    <arxiv:doi>10.1234/journal.2024.01</arxiv:doi>
    <arxiv:journal_ref>Journal of DH 12(3) 2024</arxiv:journal_ref>
    <arxiv:primary_category term="cs.CL" scheme="http://arxiv.org/schemas/atom"/>
    <link href="http://arxiv.org/abs/2401.01234v2" rel="alternate" type="text/html"/>
    <link title="pdf" href="http://arxiv.org/pdf/2401.01234v2" rel="related" type="application/pdf"/>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2403.99999v1</id>
    <title>A Pure Preprint</title>
    <summary>Methodology without a journal DOI yet.</summary>
    <published>2024-03-01T09:00:00Z</published>
    <author><name>Anonymous</name></author>
    <arxiv:primary_category term="cs.AI" scheme="http://arxiv.org/schemas/atom"/>
    <link title="pdf" href="http://arxiv.org/pdf/2403.99999v1" rel="related" type="application/pdf"/>
  </entry>
</feed>
"""

# An empty feed — legitimate "no hits" response from arXiv.
EMPTY_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:arxiv="http://arxiv.org/schemas/atom">
</feed>
"""


def test_extract_arxiv_id_new_form_strips_version() -> None:
    assert _extract_arxiv_id("http://arxiv.org/abs/2401.01234v2") == "2401.01234"
    assert _extract_arxiv_id("https://arxiv.org/abs/2401.01234") == "2401.01234"


def test_extract_arxiv_id_old_form() -> None:
    """Pre-2007 preprints use ``archive/YYMMNNN`` IDs."""
    out = _extract_arxiv_id("http://arxiv.org/abs/cs.AI/0102001v1")
    assert out == "cs.AI/0102001"


def test_extract_arxiv_id_handles_empty() -> None:
    assert _extract_arxiv_id(None) is None
    assert _extract_arxiv_id("") is None


def test_normalize_query_wraps_naive_in_all() -> None:
    assert _normalize_query("negev iron age") == "all:negev iron age"
    # Lucene-style prefixes are left alone.
    assert _normalize_query("ti:negev au:cohen") == "ti:negev au:cohen"
    assert _normalize_query("cat:cs.AI") == "cat:cs.AI"
    assert _normalize_query("all:foo") == "all:foo"


def test_apply_year_filter_open_ended() -> None:
    assert _apply_year_filter("all:x", None, None) == "all:x"
    out = _apply_year_filter("all:x", 2010, None)
    assert "submittedDate:[201001010000 TO 299912312359]" in out
    out = _apply_year_filter("all:x", None, 2020)
    assert "submittedDate:[190001010000 TO 202012312359]" in out
    out = _apply_year_filter("all:x", 2005, 2012)
    assert "submittedDate:[200501010000 TO 201212312359]" in out


def test_build_params_basic_shape() -> None:
    params = _build_params("RAG archaeology", 5, None, None)
    keys = [k for k, _ in params]
    assert keys == ["search_query", "start", "max_results", "sortBy", "sortOrder"]
    sq = next(v for k, v in params if k == "search_query")
    assert sq.startswith("all:RAG archaeology")
    assert ("max_results", "5") in params


def test_build_params_clamps_max_results() -> None:
    assert ("max_results", "100") in _build_params("x", 9999, None, None)
    assert ("max_results", "1") in _build_params("x", 0, None, None)


def test_format_authors_via_xml() -> None:
    feed = ET.fromstring(SAMPLE_FEED)
    entry = feed.find("{http://www.w3.org/2005/Atom}entry")
    assert entry is not None
    out = _format_authors(entry)
    assert out == ["Doe, Jane", "Smith, John", "Müller, Alice"]


def test_entry_to_paper_with_doi() -> None:
    papers = _parse_atom(SAMPLE_FEED)
    assert len(papers) == 2
    p = papers[0]
    assert isinstance(p, DAOPaper)
    assert p.source == "arxiv"
    assert p.doi_or_id == "10.1234/journal.2024.01"
    assert p.identifiers is not None
    assert p.identifiers.doi == "10.1234/journal.2024.01"
    assert p.identifiers.arxiv_id == "2401.01234"
    assert p.publication_status is PublicationStatus.PUBLISHED
    assert "RAG for Archaeological Texts" in p.title
    # Whitespace inside <title> is collapsed.
    assert "\n" not in p.title
    assert p.year == 2024
    # Inline citation: DOI wins.
    assert p.inline_citation is not None
    assert p.inline_citation.markdown == ("[(Doe, Smith & Müller 2024)](https://doi.org/10.1234/journal.2024.01)")
    # PDF link surfaced as open_access_url.
    assert str(p.open_access_url) == "http://arxiv.org/pdf/2401.01234v2"


def test_entry_to_paper_preprint_only_falls_back_to_arxiv_landing() -> None:
    papers = _parse_atom(SAMPLE_FEED)
    p = papers[1]
    assert p.doi_or_id == "arxiv:2403.99999"
    assert p.identifiers is not None
    assert p.identifiers.doi is None
    assert p.identifiers.arxiv_id == "2403.99999"
    assert p.publication_status is PublicationStatus.PREPRINT
    assert str(p.landing_page_url) == "https://arxiv.org/abs/2403.99999"
    # Inline citation: arxiv.org as primary_url, Author-Year form.
    assert p.inline_citation is not None
    assert p.inline_citation.markdown == ("[(Anonymous 2024)](https://arxiv.org/abs/2403.99999)")


def test_parse_atom_empty_feed() -> None:
    assert _parse_atom(EMPTY_FEED) == []


def test_parse_atom_drops_entry_without_arxiv_id() -> None:
    """A malformed entry with no ``<id>`` is dropped, not crashed."""
    feed = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <title>No identifier here</title>
    <published>2024-01-01T00:00:00Z</published>
    <author><name>Nobody</name></author>
  </entry>
</feed>
"""
    assert _parse_atom(feed) == []


@pytest.mark.asyncio
@respx.mock
async def test_search_arxiv_impl_happy_path() -> None:
    respx.get(ARXIV_API).mock(return_value=httpx.Response(200, text=SAMPLE_FEED))
    results = await search_arxiv_impl("RAG archaeology", max_results=5)
    assert len(results) == 2
    assert all(isinstance(p, DAOPaper) for p in results)
    assert results[0].inline_citation is not None
    assert results[0].inline_citation.markdown.startswith("[(Doe, Smith & Müller 2024)]")


@pytest.mark.asyncio
@respx.mock
async def test_search_arxiv_impl_empty() -> None:
    respx.get(ARXIV_API).mock(return_value=httpx.Response(200, text=EMPTY_FEED))
    assert await search_arxiv_impl("xyzzy") == []


@pytest.mark.asyncio
@respx.mock
async def test_search_arxiv_impl_http_error_propagates() -> None:
    respx.get(ARXIV_API).mock(return_value=httpx.Response(503))
    with pytest.raises(httpx.HTTPStatusError):
        await search_arxiv_impl("anything")


@pytest.mark.asyncio
@respx.mock
async def test_search_arxiv_impl_query_and_year_forwarded() -> None:
    route = respx.get(ARXIV_API).mock(return_value=httpx.Response(200, text=EMPTY_FEED))
    await search_arxiv_impl(
        "RAG humanities",
        max_results=3,
        year_from=2020,
        year_to=2024,
    )
    assert route.called
    sent_url = str(route.calls.last.request.url)
    assert "search_query=" in sent_url
    # The URL encoding of '[' and ' ' will vary, but the date bounds
    # should land somewhere in the encoded query string.
    assert "submittedDate" in sent_url
    assert "2020" in sent_url
    assert "2024" in sent_url
