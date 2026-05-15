"""iDAI.gazetteer resolver.

The gazetteer at ``gazetteer.dainst.org`` is the DAI's authoritative
place register. It returns clean JSON, has hierarchical
parent/ancestor relationships, multilingual name variants, and
cross-references to Pleiades (ancient places) and GeoNames.

Zenon records already cross-link to the gazetteer via the
``DAILinks.gazetteer`` block — see ``adapters.zenon`` where those links
are read into ``DAOPaper.site_ids``. This module additionally exposes a
dedicated MCP tool ``resolve_site`` for ad-hoc place lookups.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

import httpx
from mcp.server.fastmcp import FastMCP

from ..models import ResolvedSite

log = logging.getLogger(__name__)

GAZETTEER_SEARCH = "https://gazetteer.dainst.org/search.json"
GAZETTEER_DOC = "https://gazetteer.dainst.org/doc/{gaz_id}.json"
GAZETTEER_PLACE_URL = "https://gazetteer.dainst.org/place/{gaz_id}"
HTTP_TIMEOUT = 15.0


def gaz_id_from_uri(uri: str) -> Optional[str]:
    """Extract the numeric gazId from a place URI.

    ``https://gazetteer.dainst.org/place/2043520`` -> ``"2043520"``
    """
    if not uri:
        return None
    m = re.search(r"/place/(\d+)", uri)
    return m.group(1) if m else None


def _site_id_token(gaz_id: str) -> str:
    """Format a gazetteer ID for DAOPaper.site_ids."""
    return f"gazetteer:{gaz_id}"


def site_id_tokens_from_zenon_record(record: dict[str, Any]) -> list[str]:
    """Pull `DAILinks.gazetteer[].uri` from a Zenon record and convert to
    ``gazetteer:<gazId>`` tokens. Used by the Zenon adapter to populate
    ``DAOPaper.site_ids`` automatically."""
    dailinks = record.get("DAILinks") or {}
    out: list[str] = []
    for entry in dailinks.get("gazetteer") or []:
        uri = entry.get("uri") if isinstance(entry, dict) else None
        gid = gaz_id_from_uri(uri or "")
        if gid:
            out.append(_site_id_token(gid))
    return out


def _coords(rec: dict[str, Any]) -> Optional[tuple[float, float]]:
    loc = (rec.get("prefLocation") or {}).get("coordinates")
    if isinstance(loc, list) and len(loc) >= 2:
        # gazetteer stores [lon, lat] (GeoJSON-style)
        try:
            lon, lat = float(loc[0]), float(loc[1])
        except (TypeError, ValueError):
            return None
        return (lat, lon)
    return None


def _identifier_value(rec: dict[str, Any], context: str) -> Optional[str]:
    """Pluck the value for an identifier matching ``context`` (e.g.
    ``"pleiades"`` or ``"geonames"``) out of the gazetteer's
    ``identifiers`` list."""
    for ident in rec.get("identifiers") or []:
        if not isinstance(ident, dict):
            continue
        ctx = (ident.get("context") or "").lower()
        if context in ctx:
            return ident.get("value")
    return None


def _record_to_site(rec: dict[str, Any]) -> ResolvedSite:
    gaz_id = str(rec.get("gazId") or "")
    pref = rec.get("prefName") or {}
    variants = [n.get("title") for n in (rec.get("names") or []) if isinstance(n, dict) and n.get("title")]

    ancestors_uri = rec.get("ancestors") or []
    ancestor_ids = [aid for aid in (gaz_id_from_uri(u) for u in ancestors_uri) if aid]

    parent_id = gaz_id_from_uri(rec.get("parent") or "") if isinstance(rec.get("parent"), str) else None

    return ResolvedSite(
        gaz_id=gaz_id,
        name_preferred=pref.get("title") or "(unknown)",
        name_language=pref.get("language"),
        name_variants=variants,
        types=list(rec.get("types") or []),
        coordinates=_coords(rec),
        parent_gaz_id=parent_id,
        ancestor_gaz_ids=ancestor_ids,
        pleiades_id=_identifier_value(rec, "pleiades"),
        geonames_id=_identifier_value(rec, "geonames"),
        landing_page_url=GAZETTEER_PLACE_URL.format(gaz_id=gaz_id) if gaz_id else None,  # type: ignore[arg-type]
    )


async def search_gazetteer_impl(
    query: str,
    max_results: int = 5,
    *,
    client: Optional[httpx.AsyncClient] = None,
) -> list[ResolvedSite]:
    """Free-text search of the iDAI.gazetteer."""
    headers = {
        "Accept": "application/json",
        "User-Agent": "dao-paper-search-mcp/0.1 (https://github.com/patrick-leiverkus/dao-paper-search-mcp)",
    }
    params = {"q": query, "limit": str(max(1, min(max_results, 25)))}

    async def _run(c: httpx.AsyncClient) -> list[ResolvedSite]:
        r = await c.get(GAZETTEER_SEARCH, params=params, headers=headers, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        data = r.json()
        records = data.get("result") or []
        return [_record_to_site(rec) for rec in records]

    if client is not None:
        return await _run(client)
    async with httpx.AsyncClient() as c:
        return await _run(c)


async def resolve_site_impl(
    query: str,
    *,
    client: Optional[httpx.AsyncClient] = None,
) -> ResolvedSite:
    """Resolve a single site name to its top gazetteer match.

    If multiple candidates are returned, the highest-ranked one is
    returned with a ``verification_note`` listing alternatives so the
    agent knows the resolution may be ambiguous.
    """
    hits = await search_gazetteer_impl(query, max_results=5, client=client)
    if not hits:
        return ResolvedSite(
            gaz_id="",
            name_preferred=query,
            verification_note=(
                "No iDAI.gazetteer match for this query. Site may be "
                "below gazetteer coverage or spelled differently — try a "
                "transliteration variant."
            ),
        )
    primary = hits[0]
    if len(hits) > 1:
        primary.name_variants = list(primary.name_variants) + [h.name_preferred for h in hits[1:]]
        primary.verification_note = (
            f"{len(hits)} gazetteer candidates returned; top hit by relevance. "
            "Verify against parent_gaz_id / coordinates."
        )
    return primary


def register(mcp: FastMCP) -> None:
    """Register the ``resolve_site`` MCP tool."""

    @mcp.tool()
    async def resolve_site(query: str) -> ResolvedSite:
        """Resolve an archaeological site name to its iDAI.gazetteer identity.

        Returns the canonical name, gazetteer ID (stable), preferred-name
        language, multilingual name variants, place types, coordinates,
        parent/ancestor hierarchy, and Pleiades + GeoNames cross-refs.

        Use to disambiguate site spellings (``Kadesh-Barnea`` vs
        ``Tell el-Qudeirat``), to anchor research output on stable IDs,
        or to discover related places in the same region.

        Args:
            query: free-text site name in any language transliteration.
        """
        return await resolve_site_impl(query=query)
