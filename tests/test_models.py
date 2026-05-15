"""Schema-validation tests for DAOPaper / ResolvedAuthor / PublicationStatus.

These tests pin the public shape of the output models. If any test here
fails, downstream consumers (the research agent) will likely also break.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from dao_paper_search_mcp.models import (
    DAOPaper,
    PublicationStatus,
    ResolvedAuthor,
)


def test_publication_status_enum_values() -> None:
    assert PublicationStatus.PUBLISHED == "published"
    assert PublicationStatus.FORTHCOMING == "forthcoming"
    assert PublicationStatus.PREPRINT == "preprint"
    assert PublicationStatus.UNKNOWN == "unknown"


def test_daopaper_minimal() -> None:
    p = DAOPaper(title="t", doi_or_id="zenon:42", source="zenon")
    assert p.title == "t"
    assert p.authors == []
    assert p.language == "und"
    assert p.publication_status is PublicationStatus.UNKNOWN
    assert p.site_ids == [] and p.periods == [] and p.regions == []
    assert p.authors_resolved is None


def test_daopaper_requires_title_and_id_and_source() -> None:
    with pytest.raises(ValidationError):
        DAOPaper()  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        DAOPaper(title="t")  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        DAOPaper(title="t", doi_or_id="x")  # type: ignore[call-arg]


def test_daopaper_json_roundtrip() -> None:
    p = DAOPaper(
        title="Standing at the Crossroads",
        authors=["Ben-Ami, D."],
        year=2024,
        journal_or_volume="Levant",
        pages="1-25",
        doi_or_id="10.1234/levant.2024.001",
        source="zenon",
        language="en",
        publication_status=PublicationStatus.PUBLISHED,
    )
    payload = p.model_dump_json()
    revived = DAOPaper.model_validate_json(payload)
    assert revived == p


def test_daopaper_url_validation() -> None:
    with pytest.raises(ValidationError):
        DAOPaper(
            title="t",
            doi_or_id="x",
            source="zenon",
            open_access_url="not-a-url",  # type: ignore[arg-type]
        )


def test_resolved_author_minimal() -> None:
    a = ResolvedAuthor(name_canonical="Steven A. Rosen", source="override")
    assert a.name_variants == []
    assert a.sites_associated == []
    assert a.q_id is None


def test_resolved_author_full() -> None:
    a = ResolvedAuthor(
        name_canonical="Steven A. Rosen",
        name_variants=["S.A. Rosen", "Rosen, S.A."],
        q_id="Q7613131",
        domain="Levant archaeology",
        affiliation_current="Ben Gurion University",
        sites_associated=["Negev Highlands"],
        source="override",
    )
    payload = a.model_dump_json()
    revived = ResolvedAuthor.model_validate_json(payload)
    assert revived == a


def test_daopaper_with_resolved_authors() -> None:
    a = ResolvedAuthor(name_canonical="Steven A. Rosen", source="wikidata")
    p = DAOPaper(
        title="t",
        doi_or_id="x",
        source="zenon",
        authors=["S.A. Rosen"],
        authors_resolved=[a],
    )
    assert p.authors_resolved is not None
    assert p.authors_resolved[0].name_canonical == "Steven A. Rosen"
