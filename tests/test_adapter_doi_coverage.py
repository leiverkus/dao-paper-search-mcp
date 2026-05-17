"""Adapter-level DOI extraction coverage.

A regression sperre for the class of bug identified in the
2026-05-18 Negev-Festungen live test: a paper that *had* a DOI in its
upstream response surfaced with the aggregator Work-URL because the
adapter did not pull the DOI into ``Identifiers.doi``.

Briefing reference: ``2026-05-18-dao-paper-search-session5-...`` §III.B
(adapter audit) and §V Test 2 (per-adapter coverage matrix).

The matrix asserts two invariants per adapter:

1. The mapping function reaches ``Identifiers.doi`` for an upstream
   record that carries a DOI.
2. The stored DOI is the normalised, lower-cased, prefix-free form.

The OpenAlex case doubles as Test 1: the Yahalom-Mack et al. 2015
fixture (OpenAlex W2592690738) is the paper that surfaced with a Work-
URL in the live test; if this assertion ever fails, the original symptom
is back.

arXiv, IAA, and ADAJ are intentionally excluded:

- arXiv and IAA map XML elements rather than dicts; their fixtures live
  in their own test modules where the XML envelope is set up.
- ADAJ has no DOI source (chapter records only carry an internal id).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Mapping, Optional

import pytest

from dao_paper_search_mcp.adapters.biorxiv import _record_to_paper as biorxiv_map
from dao_paper_search_mcp.adapters.core import _work_to_paper as core_map
from dao_paper_search_mcp.adapters.crossref import _item_to_paper as crossref_map
from dao_paper_search_mcp.adapters.openalex import _work_to_paper as openalex_map
from dao_paper_search_mcp.adapters.semantic_scholar import (
    _paper_to_paper as s2_map,
)
from dao_paper_search_mcp.adapters.zenodo import _record_to_paper as zenodo_map
from dao_paper_search_mcp.adapters.zenon import _record_to_paper as zenon_map
from dao_paper_search_mcp.models import DAOPaper

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def _load_openalex_yahalom_mack() -> Mapping:
    return json.loads((FIXTURE_DIR / "openalex_yahalom_mack_2015.json").read_text())


# Minimal, DOI-carrying upstream records — one per JSON-based adapter.
# Just enough fields that the mapping function does not drop the record
# for unrelated reasons (missing title, no anchor, etc.). The point of
# each fixture is purely: "the DOI is present in the API envelope —
# does it land in identifiers.doi?"
CROSSREF_ITEM: Mapping = {
    "DOI": "10.1017/S0033822200044982",
    "title": ["A radiocarbon synthesis for the Iron Age Negev"],
    "author": [{"family": "Cohen", "given": "Rudolph"}],
    "issued": {"date-parts": [[1979]]},
    "URL": "https://doi.org/10.1017/S0033822200044982",
    "type": "journal-article",
}

S2_PAPER: Mapping = {
    "paperId": "deadbeef",
    "title": "Lead isotope analysis",
    "year": 2015,
    "authors": [{"name": "Yahalom-Mack, N."}],
    "externalIds": {"DOI": "10.1179/0334435515Z.00000000054"},
}

CORE_WORK: Mapping = {
    "id": "12345678",
    "doi": "https://doi.org/10.1234/CORE.example",
    "title": "A CORE-indexed Negev study",
    "authors": [{"name": "Example, A."}],
    "yearPublished": 2020,
}

BIORXIV_RECORD: Mapping = {
    "doi": "10.1101/2021.01.01.123456",
    "title": "A bioRxiv preprint on something",
    "authorList": "Doe J; Smith A",
    "pubYear": 2021,
}

ZENODO_RECORD: Mapping = {
    "doi": "10.5281/zenodo.999999",
    "metadata": {
        "title": "A Zenodo dataset",
        "creators": [{"name": "Example, A."}],
        "publication_date": "2024-01-01",
        "resource_type": {"type": "dataset"},
    },
}

ZENON_RECORD: Mapping = {
    "id": "001234567",
    "title": "A Zenon-indexed monograph",
    "authors": {"primary": {"Schmidt, K.": []}},
    "publicationDates": ["2010"],
    "urls": [{"url": "https://doi.org/10.1163/example.123"}],
    "formats": ["Book"],
}


def _doi_or_none(paper: Optional[DAOPaper]) -> Optional[str]:
    assert paper is not None, "adapter dropped a record that should have mapped"
    assert paper.identifiers is not None, "Identifiers must be attached"
    return paper.identifiers.doi


@pytest.mark.parametrize(
    "label,mapper,record,expected_doi",
    [
        (
            "openalex-yahalom-mack-2015",
            openalex_map,
            _load_openalex_yahalom_mack(),
            "10.1179/0334435515z.00000000054",
        ),
        ("crossref", crossref_map, CROSSREF_ITEM, "10.1017/s0033822200044982"),
        ("semantic_scholar", s2_map, S2_PAPER, "10.1179/0334435515z.00000000054"),
        ("core", core_map, CORE_WORK, "10.1234/core.example"),
        ("biorxiv", biorxiv_map, BIORXIV_RECORD, "10.1101/2021.01.01.123456"),
        ("zenodo", zenodo_map, ZENODO_RECORD, "10.5281/zenodo.999999"),
        ("zenon", zenon_map, ZENON_RECORD, "10.1163/example.123"),
    ],
)
def test_adapter_extracts_normalised_doi(
    label: str,
    mapper: Callable[[Mapping], Optional[DAOPaper]],
    record: Mapping,
    expected_doi: str,
) -> None:
    paper = mapper(record)
    assert _doi_or_none(paper) == expected_doi, (
        f"{label}: adapter must surface the upstream DOI in identifiers.doi, "
        f"normalised to bare lower-case form"
    )


def test_openalex_yahalom_mack_renders_doi_in_bibliography_line() -> None:
    """Test 1 from briefing §V: the live-test regress sperre.

    The bibliography line must include the DOI hyperlink, not the
    OpenAlex Work-URL fallback. If this fails, the symptom that
    motivated Session 5 is back.
    """
    paper = openalex_map(_load_openalex_yahalom_mack())
    assert paper is not None
    assert paper.inline_citation is not None
    line = paper.inline_citation.authoritative_bibliography_line
    assert line is not None
    assert "DOI: [10.1179/0334435515z.00000000054]" in line
    assert "openalex.org/W2592690738" not in line
