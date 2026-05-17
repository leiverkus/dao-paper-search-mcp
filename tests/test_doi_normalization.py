"""Tests for the centralised DOI normaliser."""

from __future__ import annotations

import pytest

from dao_paper_search_mcp.utils.doi import normalize_doi


@pytest.mark.parametrize(
    "raw,expected",
    [
        # bare DOI passes through, lowered
        ("10.1179/0334435515Z.00000000054", "10.1179/0334435515z.00000000054"),
        # OpenAlex-style resolver URL
        ("https://doi.org/10.1179/0334435515z.00000000054", "10.1179/0334435515z.00000000054"),
        # http variant
        ("http://doi.org/10.1000/abc", "10.1000/abc"),
        # dx.doi.org legacy
        ("https://dx.doi.org/10.1000/abc", "10.1000/abc"),
        ("http://dx.doi.org/10.1000/abc", "10.1000/abc"),
        # IAA's OAI-DC form
        ("info:doi/10.70967/x.y", "10.70967/x.y"),
        # doi: scheme prefix
        ("doi:10.1000/abc", "10.1000/abc"),
        # mixed case in scheme + host
        ("HTTPS://DOI.ORG/10.1000/ABC", "10.1000/abc"),
        # surrounding whitespace
        ("  10.1000/abc  ", "10.1000/abc"),
        # scheme-less doi.org/
        ("doi.org/10.1000/abc", "10.1000/abc"),
    ],
)
def test_normalize_doi_valid(raw: str, expected: str) -> None:
    assert normalize_doi(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "   ",
        "not-a-doi",
        "https://openalex.org/W2592690738",  # adapter Work-URL slipping in
        "doi:nonsense",  # prefix present but body invalid
    ],
)
def test_normalize_doi_invalid_returns_none(raw) -> None:
    assert normalize_doi(raw) is None


def test_normalize_doi_idempotent() -> None:
    """Normalising an already-normalised DOI is a no-op."""
    once = normalize_doi("https://doi.org/10.1179/0334435515Z.X")
    twice = normalize_doi(once)
    assert once == twice == "10.1179/0334435515z.x"
