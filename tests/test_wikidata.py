"""Tests for the author resolver.

Three layers, each tested in isolation:
1. Override YAML matching (most important — it eliminates the
   "Avraham Rosen" -> "Steven A. Rosen" hallucination class).
2. Wikidata SPARQL response parsing.
3. GND fallback when Wikidata is empty.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from dao_paper_search_mcp.adapters import (  # noqa: F401 - ensures package import works
    zenon,
)
from dao_paper_search_mcp.models import ResolvedAuthor
from dao_paper_search_mcp.resolvers.wikidata_author import (
    GND_LOOKUP,
    WIKIDATA_SPARQL,
    _binding_to_author,
    _gnd_query_with_hint,
    _gnd_record_passes_domain,
    _load_overrides,
    _normalise,
    _override_match,
    _q_id_from_uri,
    _score_candidate,
    _year_from_date,
    resolve_author_impl,
)

# Real lobid response observed 2026-05-15 for "Yisrael Cohen" — the
# rabbi Chafetz Chaim. Pinned here as a regression fixture so the
# domain filter is forever guarded against this specific leak.
CHAFETZ_CHAIM_GND_RECORD = {
    "preferredName": "Kahan, Israel M.",
    "gndIdentifier": "119150530",
    "variantName": ["Chafetz Chaim", "Hafets Hayim", "Hofets Hayim"],
    "professionOrOccupation": [
        {"id": "https://d-nb.info/gnd/4176751-2", "label": "Rabbiner"}
    ],
    "dateOfBirth": ["1838"],
    "dateOfDeath": ["1933"],
}


def test_normalise_strips_punctuation_and_case() -> None:
    assert _normalise("S.A. Rosen") == "s a rosen"
    assert _normalise("Rosen, Steven A.") == "rosen steven a"
    assert _normalise("  Cohen,  R.  ") == "cohen r"


def test_q_id_extraction() -> None:
    assert _q_id_from_uri("http://www.wikidata.org/entity/Q7613131") == "Q7613131"
    assert _q_id_from_uri("") is None
    assert _q_id_from_uri(None) is None  # type: ignore[arg-type]


def test_year_from_date_handles_iso_and_bce() -> None:
    assert _year_from_date("1955-03-14T00:00:00Z") == 1955
    assert _year_from_date("-0500-01-01T00:00:00Z") == -500
    assert _year_from_date("") is None
    assert _year_from_date(None) is None


def test_overrides_yaml_loads_with_canonical_entries() -> None:
    overrides = _load_overrides()
    names = {entry["canonical"] for entry in overrides}
    # Spot-check the seed entries from the briefing — these MUST be present
    # to keep the verification suite's halluzination-elimination guarantee.
    assert "Steven A. Rosen" in names
    assert "Arlene M. Rosen" in names
    assert "Rudolph Cohen" in names
    assert "Amihai Mazar" in names


def test_override_match_canonical_name() -> None:
    overrides = _load_overrides()
    hit = _override_match("Steven A. Rosen", overrides)
    assert hit is not None
    assert hit.name_canonical == "Steven A. Rosen"
    assert hit.source == "override"
    assert "Negev Highlands" in (hit.sites_associated or [])


def test_override_match_variant() -> None:
    overrides = _load_overrides()
    for variant in ("S.A. Rosen", "Rosen, S.A.", "Steven Rosen"):
        hit = _override_match(variant, overrides)
        assert hit is not None, f"variant {variant!r} should resolve"
        assert hit.name_canonical == "Steven A. Rosen"


def test_override_eliminates_avraham_rosen_hallucination() -> None:
    """Critical contract: 'Avraham Rosen' (LLM hallucination) MUST resolve
    to Steven A. Rosen via the override layer. If this test fails, the
    verification suite's halluzination-elimination guarantee is broken."""
    overrides = _load_overrides()
    hit = _override_match("Avraham Rosen", overrides)
    assert hit is not None
    assert hit.name_canonical == "Steven A. Rosen"
    assert hit.verification_note and "halluzination" in hit.verification_note.lower()


def test_override_disambiguates_two_rosens() -> None:
    """Steven A. Rosen and Arlene M. Rosen are both real archaeologists.
    The override must distinguish them via their canonical full names."""
    overrides = _load_overrides()
    assert _override_match("S.A. Rosen", overrides).name_canonical == "Steven A. Rosen"  # type: ignore[union-attr]
    assert _override_match("A.M. Rosen", overrides).name_canonical == "Arlene M. Rosen"  # type: ignore[union-attr]


def test_override_miss_returns_none() -> None:
    overrides = _load_overrides()
    assert _override_match("Unknown Scholar", overrides) is None


def test_score_candidate_prefers_affiliation_match() -> None:
    binding = {
        "affiliationLabel": {"value": "Ben Gurion University of the Negev"},
        "orcid": {"value": "0000-0001-2345-6789"},
    }
    score_match = _score_candidate(binding, domain_hint="Ben Gurion archaeology")
    score_nomatch = _score_candidate(binding, domain_hint="Tel Aviv classics")
    assert score_match > score_nomatch


def test_binding_to_author_complete_mapping() -> None:
    binding = {
        "person": {"value": "http://www.wikidata.org/entity/Q7613131"},
        "personLabel": {"value": "Steven A. Rosen"},
        "birth": {"value": "1955-03-14T00:00:00Z"},
        "affiliationLabel": {"value": "Ben Gurion University"},
        "orcid": {"value": "0000-0001-2345-6789"},
        "gnd": {"value": "123456789"},
    }
    a = _binding_to_author(binding)
    assert a.q_id == "Q7613131"
    assert a.name_canonical == "Steven A. Rosen"
    assert a.birth_year == 1955
    assert a.orcid == "0000-0001-2345-6789"
    assert a.affiliation_current == "Ben Gurion University"
    assert a.source == "wikidata"


# --- async / impl tests --------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_author_impl_short_circuits_on_override() -> None:
    """When the override matches, no upstream call is made.
    This proves the override layer wins and we don't waste a Wikidata
    request on names we already know how to handle locally."""

    # If anything tries to reach Wikidata, respx with no mock will raise.
    with respx.mock(assert_all_called=False) as router:
        router.get(WIKIDATA_SPARQL)
        router.get(GND_LOOKUP)
        result = await resolve_author_impl("S.A. Rosen")
        assert result.source == "override"
        assert result.name_canonical == "Steven A. Rosen"
        assert not router.routes[0].called
        assert not router.routes[1].called


@pytest.mark.asyncio
@respx.mock
async def test_resolve_author_impl_wikidata_single_hit() -> None:
    respx.get(WIKIDATA_SPARQL).mock(
        return_value=httpx.Response(
            200,
            json={
                "results": {
                    "bindings": [
                        {
                            "person": {"value": "http://www.wikidata.org/entity/Q12345"},
                            "personLabel": {"value": "Test Person"},
                            "orcid": {"value": "0000-0001-2222-3333"},
                        }
                    ]
                }
            },
        )
    )
    result = await resolve_author_impl("Test Person Not In Overrides")
    assert result.source == "wikidata"
    assert result.name_canonical == "Test Person"
    assert result.q_id == "Q12345"


def test_gnd_query_appends_german_archaeology_term() -> None:
    assert _gnd_query_with_hint("Yisrael Cohen", "archaeology") == "Yisrael Cohen Archäologie"
    assert _gnd_query_with_hint("Müller", "archaeology") == "Müller Archäologie"


def test_gnd_query_passes_other_hints_verbatim() -> None:
    assert _gnd_query_with_hint("Müller", "biblical studies") == "Müller biblical studies"
    assert _gnd_query_with_hint("Müller", None) == "Müller"
    assert _gnd_query_with_hint("Müller", "") == "Müller"


def test_gnd_record_passes_domain_drops_pure_rabbi() -> None:
    """Regression: the 2026-05-15 'Yisrael Cohen' query returned Chafetz
    Chaim (Rabbiner) from GND because the previous fallback ignored
    domain_hint. This must never happen again for archaeology queries."""
    assert _gnd_record_passes_domain(CHAFETZ_CHAIM_GND_RECORD, "archaeology") is False


def test_gnd_record_passes_domain_keeps_archaeologist() -> None:
    record = {"professionOrOccupation": [{"label": "Archäologin"}]}
    assert _gnd_record_passes_domain(record, "archaeology") is True


def test_gnd_record_passes_domain_keeps_mixed_profession() -> None:
    """If at least one profession is plausible, the record is kept —
    Cohen-the-rabbi-and-also-archaeologist is real enough to allow."""
    record = {"professionOrOccupation": [{"label": "Rabbiner"}, {"label": "Archäologe"}]}
    assert _gnd_record_passes_domain(record, "archaeology") is True


def test_gnd_record_passes_domain_keeps_unknown_profession_when_no_data() -> None:
    """Records without profession data are kept — we have no grounds to
    reject them, and over-aggressive filtering would drop real hits."""
    assert _gnd_record_passes_domain({}, "archaeology") is True
    assert _gnd_record_passes_domain({"professionOrOccupation": []}, "archaeology") is True


def test_gnd_record_passes_domain_no_filter_without_hint() -> None:
    """When no domain_hint is supplied, the filter is a no-op."""
    assert _gnd_record_passes_domain(CHAFETZ_CHAIM_GND_RECORD, None) is True
    assert _gnd_record_passes_domain(CHAFETZ_CHAIM_GND_RECORD, "") is True


@pytest.mark.asyncio
@respx.mock
async def test_resolve_author_impl_does_not_leak_chafetz_chaim_for_archaeology() -> None:
    """End-to-end regression: 'Yisrael Cohen' with default domain_hint
    must NOT resolve to Chafetz Chaim. Either no result or a flagged
    one is acceptable; the rabbi himself is not."""
    respx.get(WIKIDATA_SPARQL).mock(
        return_value=httpx.Response(200, json={"results": {"bindings": []}})
    )
    respx.get(GND_LOOKUP).mock(
        return_value=httpx.Response(200, json={"member": [CHAFETZ_CHAIM_GND_RECORD]})
    )
    result = await resolve_author_impl("Yisrael Cohen", domain_hint="archaeology")
    assert result.gnd_id != "119150530"
    assert result.source == "unresolved"
    assert result.verification_note is not None


@pytest.mark.asyncio
@respx.mock
async def test_resolve_author_impl_returns_gnd_with_profession_in_note() -> None:
    """When a non-blacklisted GND hit comes through, the note should
    surface the profession label so the agent can audit the match."""
    respx.get(WIKIDATA_SPARQL).mock(
        return_value=httpx.Response(200, json={"results": {"bindings": []}})
    )
    archaeologist_record = {
        "preferredName": "Müller, Hans",
        "gndIdentifier": "123456789",
        "professionOrOccupation": [{"label": "Archäologe"}],
    }
    respx.get(GND_LOOKUP).mock(
        return_value=httpx.Response(200, json={"member": [archaeologist_record]})
    )
    result = await resolve_author_impl("Hans Müller", domain_hint="archaeology")
    assert result.source == "gnd"
    assert result.gnd_id == "123456789"
    assert result.verification_note is not None
    assert "Archäologe" in result.verification_note


@pytest.mark.asyncio
@respx.mock
async def test_resolve_author_impl_wikidata_empty_falls_through_to_gnd() -> None:
    respx.get(WIKIDATA_SPARQL).mock(
        return_value=httpx.Response(200, json={"results": {"bindings": []}})
    )
    respx.get(GND_LOOKUP).mock(
        return_value=httpx.Response(
            200,
            json={
                "member": [
                    {
                        "preferredName": "Some German Scholar",
                        "gndIdentifier": "987654321",
                    }
                ]
            },
        )
    )
    result = await resolve_author_impl("Some German Scholar")
    assert result.source == "gnd"
    assert result.gnd_id == "987654321"


@pytest.mark.asyncio
@respx.mock
async def test_resolve_author_impl_unresolved_when_everything_empty() -> None:
    respx.get(WIKIDATA_SPARQL).mock(
        return_value=httpx.Response(200, json={"results": {"bindings": []}})
    )
    respx.get(GND_LOOKUP).mock(return_value=httpx.Response(200, json={"member": []}))
    result = await resolve_author_impl("Definitely Not Real Scholar")
    assert result.source == "unresolved"
    assert result.verification_note is not None
    assert "no match" in result.verification_note.lower()


@pytest.mark.asyncio
@respx.mock
async def test_resolve_author_impl_handles_wikidata_error_gracefully() -> None:
    """Wikidata is occasionally slow / 503. The resolver must still fall
    through to GND instead of crashing."""
    respx.get(WIKIDATA_SPARQL).mock(return_value=httpx.Response(503))
    respx.get(GND_LOOKUP).mock(
        return_value=httpx.Response(
            200,
            json={"member": [{"preferredName": "Backup Hit", "gndIdentifier": "111"}]},
        )
    )
    result = await resolve_author_impl("Backup Hit")
    assert result.source == "gnd"


@pytest.mark.asyncio
@respx.mock
async def test_resolve_author_impl_multiple_top_candidates_packs_variants() -> None:
    """When Wikidata returns several equally-scored matches, the resolver
    returns the first one with the other names packed into
    ``name_variants`` so the agent can see the ambiguity."""
    respx.get(WIKIDATA_SPARQL).mock(
        return_value=httpx.Response(
            200,
            json={
                "results": {
                    "bindings": [
                        {
                            "person": {"value": "http://www.wikidata.org/entity/Q1"},
                            "personLabel": {"value": "Cohen, Mark"},
                        },
                        {
                            "person": {"value": "http://www.wikidata.org/entity/Q2"},
                            "personLabel": {"value": "Cohen, Andrew"},
                        },
                    ]
                }
            },
        )
    )
    result = await resolve_author_impl("Cohen")
    assert result.source == "wikidata"
    assert result.verification_note is not None
    assert "verify" in result.verification_note.lower()
    assert "Cohen, Andrew" in result.name_variants
