"""Tests for the iDAI.gazetteer resolver and the Zenon integration."""

from __future__ import annotations

import httpx
import pytest
import respx

from dao_paper_search_mcp.models import ResolvedSite
from dao_paper_search_mcp.resolvers.gazetteer import (
    GAZETTEER_SEARCH,
    _coords,
    _identifier_value,
    _record_to_site,
    gaz_id_from_uri,
    resolve_site_impl,
    search_gazetteer_impl,
    site_id_tokens_from_zenon_record,
)

# Realistic gazetteer search result for "Kadesh-Barnea"
SAMPLE_KADESH = {
    "@id": "https://gazetteer.dainst.org/place/2043605",
    "gazId": "2043605",
    "prefName": {"title": "Kadesh-barnea", "language": "eng"},
    "names": [
        {"title": "Tell el-Qudeirat", "language": "ara"},
        {"title": "Qadesch-Barnea", "language": "deu"},
        {"title": "Kadesch-Barnea", "language": "deu"},
    ],
    "types": ["archaeological-site"],
    "parent": "https://gazetteer.dainst.org/place/2290547",
    "ancestors": [
        "https://gazetteer.dainst.org/place/2290547",
        "https://gazetteer.dainst.org/place/2043065",
    ],
    "relatedPlaces": [],
    "identifiers": [
        {"value": "687867", "context": "Pleiades"},
        {"value": "294640", "context": "geonames"},
    ],
    "prefLocation": {"coordinates": [34.418, 30.683]},
}


def test_gaz_id_from_uri() -> None:
    assert gaz_id_from_uri("https://gazetteer.dainst.org/place/2043520") == "2043520"
    assert gaz_id_from_uri("https://gazetteer.dainst.org/place/2043605/?x=1") == "2043605"
    assert gaz_id_from_uri("") is None
    assert gaz_id_from_uri("https://example.com/foo") is None


def test_site_id_tokens_from_zenon_record_extracts_gazetteer_links() -> None:
    """Zenon records carry DAILinks.gazetteer — this is the cross-link
    we promote into DAOPaper.site_ids for free."""
    record = {
        "DAILinks": {
            "gazetteer": [
                {"label": "Sahraʾ an-Naqab", "uri": "https://gazetteer.dainst.org/place/2043520"},
                {"label": "Kadesh-barnea", "uri": "https://gazetteer.dainst.org/place/2043605"},
            ],
            "thesauri": [],
        }
    }
    assert site_id_tokens_from_zenon_record(record) == [
        "gazetteer:2043520",
        "gazetteer:2043605",
    ]


def test_site_id_tokens_returns_empty_when_no_dailinks() -> None:
    assert site_id_tokens_from_zenon_record({}) == []
    assert site_id_tokens_from_zenon_record({"DAILinks": {"gazetteer": []}}) == []


def test_site_id_tokens_skips_malformed_entries() -> None:
    record = {
        "DAILinks": {
            "gazetteer": [
                {"label": "no uri here"},
                {"uri": "https://example.com/not-a-place"},
                {"uri": "https://gazetteer.dainst.org/place/123"},
            ]
        }
    }
    assert site_id_tokens_from_zenon_record(record) == ["gazetteer:123"]


def test_coords_extraction_geojson_order() -> None:
    """Gazetteer stores coordinates as [lon, lat] (GeoJSON convention).
    Our model returns (lat, lon) so geographic libraries can consume it
    directly."""
    assert _coords({"prefLocation": {"coordinates": [34.418, 30.683]}}) == (30.683, 34.418)


def test_coords_missing_returns_none() -> None:
    assert _coords({}) is None
    assert _coords({"prefLocation": {}}) is None
    assert _coords({"prefLocation": {"coordinates": [34.418]}}) is None


def test_identifier_value_pleiades_and_geonames() -> None:
    rec = {
        "identifiers": [
            {"value": "687867", "context": "Pleiades"},
            {"value": "294640", "context": "geonames"},
        ]
    }
    assert _identifier_value(rec, "pleiades") == "687867"
    assert _identifier_value(rec, "geonames") == "294640"
    assert _identifier_value(rec, "unknown") is None


def test_record_to_site_full_mapping() -> None:
    site = _record_to_site(SAMPLE_KADESH)
    assert isinstance(site, ResolvedSite)
    assert site.gaz_id == "2043605"
    assert site.name_preferred == "Kadesh-barnea"
    assert site.name_language == "eng"
    assert "Tell el-Qudeirat" in site.name_variants
    assert "Qadesch-Barnea" in site.name_variants
    assert site.types == ["archaeological-site"]
    assert site.coordinates == (30.683, 34.418)
    assert site.parent_gaz_id == "2290547"
    assert site.ancestor_gaz_ids == ["2290547", "2043065"]
    assert site.pleiades_id == "687867"
    assert site.geonames_id == "294640"
    assert str(site.landing_page_url) == "https://gazetteer.dainst.org/place/2043605"


@pytest.mark.asyncio
@respx.mock
async def test_search_gazetteer_impl_happy_path() -> None:
    respx.get(GAZETTEER_SEARCH).mock(return_value=httpx.Response(200, json={"total": 1, "result": [SAMPLE_KADESH]}))
    sites = await search_gazetteer_impl("Kadesh", max_results=3)
    assert len(sites) == 1
    assert sites[0].gaz_id == "2043605"


@pytest.mark.asyncio
@respx.mock
async def test_resolve_site_impl_single_match_no_note() -> None:
    respx.get(GAZETTEER_SEARCH).mock(return_value=httpx.Response(200, json={"total": 1, "result": [SAMPLE_KADESH]}))
    site = await resolve_site_impl("Kadesh-Barnea")
    assert site.gaz_id == "2043605"
    assert site.verification_note is None


@pytest.mark.asyncio
@respx.mock
async def test_resolve_site_impl_multiple_packs_variants_and_note() -> None:
    second = {**SAMPLE_KADESH, "gazId": "9999", "prefName": {"title": "Other Place"}}
    respx.get(GAZETTEER_SEARCH).mock(
        return_value=httpx.Response(200, json={"total": 2, "result": [SAMPLE_KADESH, second]})
    )
    site = await resolve_site_impl("Kadesh")
    assert site.gaz_id == "2043605"  # top hit
    assert "Other Place" in site.name_variants
    assert site.verification_note is not None
    assert "candidates" in site.verification_note.lower()


@pytest.mark.asyncio
@respx.mock
async def test_resolve_site_impl_empty_returns_placeholder_with_note() -> None:
    respx.get(GAZETTEER_SEARCH).mock(return_value=httpx.Response(200, json={"total": 0, "result": []}))
    site = await resolve_site_impl("Nowhereville")
    assert site.gaz_id == ""
    assert site.verification_note is not None
    assert "no idai.gazetteer match" in site.verification_note.lower()
