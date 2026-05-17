"""Author resolver — Wikidata SPARQL + DAO override YAML + GND fallback.

Resolution order (first match wins):
    1. ``data/authority_overrides.yml`` — DAO-curated disambiguation list.
       Overrides ALWAYS win because they encode local domain expertise that
       Wikidata or GND cannot represent (e.g. "Avraham Rosen" is a known
       LLM hallucination for "Steven A. Rosen" — no upstream catalog has
       that variant).
    2. Wikidata via the public SPARQL endpoint at
       ``https://query.wikidata.org/sparql``. Filters to archaeologists /
       professors / historians to suppress unrelated namesakes.
    3. GND (Deutsche Nationalbibliothek) via the lobid.org JSON API. Used
       only when Wikidata returns no candidates. Mostly relevant for
       German-language authors and historic scholars not in Wikidata.

When multiple candidates remain after filtering, all are returned with a
``verification_note`` so the agent can decide.
"""

from __future__ import annotations

import logging
import os
import re
from importlib import resources
from typing import Any, Optional

import httpx
import yaml
from mcp.server.fastmcp import FastMCP

from ..models import ResolvedAuthor
from ..utils.contact import CONTACT_EMAIL

log = logging.getLogger(__name__)

WIKIDATA_SPARQL = "https://query.wikidata.org/sparql"
GND_LOOKUP = "https://lobid.org/gnd/search"
HTTP_TIMEOUT = 20.0

_DEFAULT_UA = (
    "dao-paper-search-mcp/0.1 (https://github.com/leiverkus/dao-paper-search-mcp;"
    f" {CONTACT_EMAIL})"
)


def _user_agent() -> str:
    """Wikidata strongly recommends a descriptive User-Agent. We honour
    the WIKIDATA_USER_AGENT env var so deployments can supply a contact."""
    return os.environ.get("WIKIDATA_USER_AGENT") or _DEFAULT_UA


# --- override YAML --------------------------------------------------------


def _normalise(name: str) -> str:
    """Canonical form for matching: lowercase, no punctuation, single spaces."""
    s = re.sub(r"[.,;:]", " ", name).strip().lower()
    return re.sub(r"\s+", " ", s)


_OVERRIDES_CACHE: Optional[list[dict[str, Any]]] = None


def _load_overrides() -> list[dict[str, Any]]:
    global _OVERRIDES_CACHE
    if _OVERRIDES_CACHE is not None:
        return _OVERRIDES_CACHE
    try:
        ref = resources.files("dao_paper_search_mcp.data").joinpath("authority_overrides.yml")
        data = yaml.safe_load(ref.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        log.warning("authority_overrides.yml not found; override layer disabled")
        _OVERRIDES_CACHE = []
        return _OVERRIDES_CACHE
    _OVERRIDES_CACHE = list(data.get("authors") or [])
    return _OVERRIDES_CACHE


def _override_match(name: str, overrides: list[dict[str, Any]]) -> Optional[ResolvedAuthor]:
    target = _normalise(name)
    for entry in overrides:
        canonical = entry.get("canonical", "")
        candidates = [canonical, *(entry.get("variants") or [])]
        if any(_normalise(c) == target for c in candidates if c):
            return ResolvedAuthor(
                name_canonical=canonical,
                name_variants=list(entry.get("variants") or []),
                q_id=entry.get("q_id"),
                gnd_id=entry.get("gnd_id"),
                orcid=entry.get("orcid"),
                domain=entry.get("domain"),
                affiliation_current=entry.get("affiliation"),
                death_year=entry.get("death_year"),
                sites_associated=list(entry.get("sites") or []),
                source="override",
                verification_note=entry.get("note"),
            )
    return None


# --- Wikidata -------------------------------------------------------------

# Archaeologist (Q3621491), professor (Q1622272), historian (Q201788),
# academic (Q3400985), researcher (Q1650915) — the cast of occupations
# that cover the Levant-archaeology research community.
_OCCUPATION_FILTER = "wd:Q3621491, wd:Q1622272, wd:Q201788, wd:Q3400985, wd:Q1650915"


def _build_sparql(name: str) -> str:
    # Escape double quotes to keep SPARQL valid.
    safe = name.replace('"', '\\"')
    return f"""
SELECT DISTINCT ?person ?personLabel ?birth ?death ?affiliationLabel ?orcid ?gnd WHERE {{
  ?person rdfs:label ?label .
  FILTER(LANG(?label) = "en" || LANG(?label) = "de")
  FILTER(CONTAINS(LCASE(STR(?label)), LCASE("{safe}"))) .
  ?person wdt:P106 ?occupation .
  FILTER(?occupation IN ({_OCCUPATION_FILTER}))
  OPTIONAL {{ ?person wdt:P569 ?birth }}
  OPTIONAL {{ ?person wdt:P570 ?death }}
  OPTIONAL {{ ?person wdt:P108 ?affiliation }}
  OPTIONAL {{ ?person wdt:P496 ?orcid }}
  OPTIONAL {{ ?person wdt:P227 ?gnd }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en" }}
}}
LIMIT 10
""".strip()


def _q_id_from_uri(uri: str) -> Optional[str]:
    m = re.search(r"(Q\d+)$", uri or "")
    return m.group(1) if m else None


def _year_from_date(s: Optional[str]) -> Optional[int]:
    if not s:
        return None
    m = re.match(r"(-?\d{4})", s)
    return int(m.group(1)) if m else None


def _score_candidate(
    binding: dict[str, dict[str, str]],
    domain_hint: Optional[str],
) -> int:
    """Rough scoring — affiliation match > ORCID present > GND present."""
    score = 0
    affiliation = (binding.get("affiliationLabel") or {}).get("value", "")
    if domain_hint and affiliation:
        domain_lc = domain_hint.lower()
        if any(tok in affiliation.lower() for tok in domain_lc.split()):
            score += 3
    if binding.get("orcid"):
        score += 2
    if binding.get("gnd"):
        score += 1
    return score


def _binding_to_author(
    binding: dict[str, dict[str, str]],
    note: Optional[str] = None,
) -> ResolvedAuthor:
    return ResolvedAuthor(
        name_canonical=(binding.get("personLabel") or {}).get("value", "(unknown)"),
        q_id=_q_id_from_uri((binding.get("person") or {}).get("value", "")),
        gnd_id=(binding.get("gnd") or {}).get("value"),
        orcid=(binding.get("orcid") or {}).get("value"),
        affiliation_current=(binding.get("affiliationLabel") or {}).get("value"),
        birth_year=_year_from_date((binding.get("birth") or {}).get("value")),
        death_year=_year_from_date((binding.get("death") or {}).get("value")),
        source="wikidata",
        verification_note=note,
    )


async def _query_wikidata(
    name: str,
    domain_hint: Optional[str],
    client: httpx.AsyncClient,
) -> list[ResolvedAuthor]:
    headers = {
        "Accept": "application/sparql-results+json",
        "User-Agent": _user_agent(),
    }
    r = await client.get(
        WIKIDATA_SPARQL,
        params={"query": _build_sparql(name), "format": "json"},
        headers=headers,
        timeout=HTTP_TIMEOUT,
    )
    r.raise_for_status()
    bindings = (r.json().get("results") or {}).get("bindings") or []
    if not bindings:
        return []

    # Rank by score; if multiple share the top score, return all with a note.
    scored = [(_score_candidate(b, domain_hint), b) for b in bindings]
    scored.sort(key=lambda x: x[0], reverse=True)
    top_score = scored[0][0]
    top = [b for s, b in scored if s == top_score]

    note = (
        f"Multiple Wikidata candidates with score {top_score}; agent must verify."
        if len(top) > 1
        else None
    )
    return [_binding_to_author(b, note=note) for b in top]


# --- GND fallback ---------------------------------------------------------

# When domain_hint is set, we drop GND hits whose entire profession list
# is on this blacklist. Saves us from returning a 19th-century rabbi
# (Chafetz Chaim, GND 119150530) for "Yisrael Cohen, archaeology" —
# observed regression 2026-05-15.
_OFF_DOMAIN_PROFESSIONS_BY_HINT: dict[str, frozenset[str]] = {
    "archaeology": frozenset(
        {
            "rabbiner", "rabbi",
            "theologe", "theologin", "theologian",
            "pfarrer", "pastor",
            "mediziner", "arzt", "physician",
            "jurist", "rechtsanwalt", "lawyer",
            "komponist", "musiker", "musician",
            "schauspieler", "actor",
            "politiker", "politician",
            "kaufmann", "merchant",
        }
    ),
}


def _gnd_query_with_hint(name: str, domain_hint: Optional[str]) -> str:
    """Bias lobid's relevance ranking by appending a German domain keyword.
    ``archaeology`` → ``Archäologie``; other hints pass through verbatim."""
    if not domain_hint:
        return name
    canonical = {"archaeology": "Archäologie"}.get(domain_hint.lower(), domain_hint)
    return f"{name} {canonical}"


def _gnd_record_passes_domain(record: dict[str, Any], domain_hint: Optional[str]) -> bool:
    """Drop GND hits whose only profession is on the off-domain blacklist
    for this hint. Records with no profession data are kept (we have no
    grounds to reject them)."""
    if not domain_hint:
        return True
    blacklist = _OFF_DOMAIN_PROFESSIONS_BY_HINT.get(domain_hint.lower())
    if not blacklist:
        return True
    professions = record.get("professionOrOccupation") or []
    if not professions:
        return True
    labels = {str(p.get("label", "")).lower().strip() for p in professions if isinstance(p, dict)}
    labels.discard("")
    if not labels:
        return True
    # Pass if any label is NOT on the blacklist (= at least one plausible
    # profession). Drop only when every label is off-topic.
    return not labels.issubset(blacklist)


async def _query_gnd(
    name: str,
    domain_hint: Optional[str],
    client: httpx.AsyncClient,
) -> list[ResolvedAuthor]:
    """Lobid.org wraps the GND with a clean JSON API. Restricted to
    persons (type=Person), biased by ``domain_hint``, and post-filtered
    so off-topic professions don't leak through.

    Returns an empty list when no surviving candidate matches the hint;
    the caller must then decide between escalation and giving up."""
    r = await client.get(
        GND_LOOKUP,
        params={
            "q": _gnd_query_with_hint(name, domain_hint),
            "filter": "type:Person",
            "format": "json",
            "size": 5,
        },
        headers={"User-Agent": _user_agent()},
        timeout=HTTP_TIMEOUT,
    )
    r.raise_for_status()
    payload = r.json()
    members = payload.get("member") or []
    filtered = [m for m in members if _gnd_record_passes_domain(m, domain_hint)]
    if not filtered:
        return []
    top = filtered[0]
    professions = [str(p.get("label", "")) for p in (top.get("professionOrOccupation") or []) if isinstance(p, dict)]
    note = "Resolved via GND fallback; verify domain match."
    if professions:
        note += f" GND profession: {', '.join(professions)}."
    return [
        ResolvedAuthor(
            name_canonical=top.get("preferredName") or name,
            name_variants=list(top.get("variantName") or [])[:10],
            gnd_id=top.get("gndIdentifier"),
            source="gnd",
            verification_note=note,
        )
    ]


# --- public entry point ---------------------------------------------------


async def resolve_author_impl(
    name_string: str,
    domain_hint: str = "archaeology",
    *,
    client: Optional[httpx.AsyncClient] = None,
) -> ResolvedAuthor:
    """Resolve an author name. See module docstring for resolution order."""
    log.info("resolve_author name=%r domain_hint=%r", name_string, domain_hint)

    overrides = _load_overrides()
    hit = _override_match(name_string, overrides)
    if hit is not None:
        log.info("resolve_author override match: %s", hit.name_canonical)
        return hit

    async def _run(c: httpx.AsyncClient) -> ResolvedAuthor:
        try:
            wd = await _query_wikidata(name_string, domain_hint, c)
        except httpx.HTTPError as e:
            log.warning("wikidata error: %s", e)
            wd = []
        if wd:
            # If only one candidate: return it. If multiple: pack the
            # variants into name_variants so the agent sees the alternatives.
            primary = wd[0]
            if len(wd) > 1:
                primary.name_variants = [a.name_canonical for a in wd[1:]]
            return primary
        # Wikidata empty -> try GND.
        try:
            gnd = await _query_gnd(name_string, domain_hint, c)
        except httpx.HTTPError as e:
            log.warning("gnd error: %s", e)
            gnd = []
        if gnd:
            return gnd[0]
        return ResolvedAuthor(
            name_canonical=name_string,
            source="unresolved",
            verification_note=(
                "No match in override list, Wikidata, or GND. "
                "Author name may be misspelled, hallucinated, or below the "
                "coverage threshold of these catalogs."
            ),
        )

    if client is not None:
        return await _run(client)
    async with httpx.AsyncClient() as c:
        return await _run(c)


def register(mcp: FastMCP) -> None:
    """Register the ``resolve_author`` tool."""

    @mcp.tool()
    async def resolve_author(
        name_string: str,
        domain_hint: str = "archaeology",
    ) -> ResolvedAuthor:
        """Resolve an author name to canonical identity via DAO override list,
        Wikidata SPARQL, and GND fallback. Returns canonical name, Wikidata
        Q-ID, GND-ID, ORCID, affiliation, life dates, and a verification note
        when the resolution is uncertain.

        Use when an LLM might hallucinate name variants — for example
        "A. Rosen" could mean Steven A. Rosen (Negev lithics) or Arlene M.
        Rosen (geoarchaeology); "Cohen" might be Rudolph (Negev fortresses)
        or one of dozens of other archaeologists.

        Args:
            name_string: the surface form to resolve (any common variant).
            domain_hint: scoring hint, default ``"archaeology"``. Use
                ``"biblical studies"``, ``"classics"``, etc. to bias the
                Wikidata candidate ranking.
        """
        return await resolve_author_impl(
            name_string=name_string,
            domain_hint=domain_hint,
        )
