"""Tests for the dedupe-key hook (Session 5, §III.C).

The hook itself is a pure function over ``DAOPaper``. These tests pin
the invariant that future merge logic in Session 6 will rely on:
identical DOIs in different casings collapse to the same key, and
records without a DOI return ``None`` so the caller can decide what to
do.
"""

from __future__ import annotations

from dao_paper_search_mcp.dedup import dedupe_key
from dao_paper_search_mcp.models import DAOPaper, Identifiers


def _paper(doi: str | None, source: str = "openalex") -> DAOPaper:
    return DAOPaper(
        title="t",
        doi_or_id=doi or f"{source}:nodoi",
        source=source,
        identifiers=Identifiers(doi=doi) if doi is not None else None,
    )


def test_dedupe_key_returns_normalised_doi() -> None:
    assert dedupe_key(_paper("10.1179/0334435515z.00000000054")) == "10.1179/0334435515z.00000000054"


def test_dedupe_key_collapses_casing() -> None:
    a = _paper("10.1179/0334435515z.00000000054", source="openalex")
    b = _paper("10.1179/0334435515Z.00000000054", source="crossref")
    # Adapters normalise to lower-case via normalize_doi(); dedupe_key
    # additionally lowercases defensively, so a key supplied verbatim
    # from upstream still collapses.
    assert dedupe_key(a) == dedupe_key(b)


def test_dedupe_key_returns_none_without_doi() -> None:
    assert dedupe_key(_paper(None)) is None


def test_dedupe_key_returns_none_when_doi_empty_string() -> None:
    paper = DAOPaper(
        title="t",
        doi_or_id="x",
        source="zenon",
        identifiers=Identifiers(doi=""),
    )
    assert dedupe_key(paper) is None
