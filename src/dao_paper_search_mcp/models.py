"""Pydantic output schemas for dao-paper-search-mcp.

The DAOPaper model is the single output type for every search tool. It is
intentionally frozen across adapters so downstream consumers (the
``research`` agent in OpenCode) can rely on a stable shape.

Fields ``site_ids``, ``periods``, ``regions`` are reserved for Iteration 2
(gazetteer + periodisation resolvers). They are present in the MVP schema
so consumer logic does not need to change when those resolvers ship.
"""

from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field, HttpUrl


class PublicationStatus(str, Enum):
    PUBLISHED = "published"
    FORTHCOMING = "forthcoming"
    PREPRINT = "preprint"
    UNKNOWN = "unknown"


class ResolvedSite(BaseModel):
    """A disambiguated archaeological place, resolved via iDAI.gazetteer.

    ``gaz_id`` is the stable iDAI identifier. ``pleiades_id`` and
    ``geonames_id`` are surfaced from the gazetteer's ``identifiers``
    block so consumers can cross-link to those other authoritative
    sources without a second lookup.
    """

    gaz_id: str
    name_preferred: str
    name_language: Optional[str] = None
    name_variants: List[str] = Field(default_factory=list)
    types: List[str] = Field(default_factory=list)
    coordinates: Optional[tuple[float, float]] = None
    parent_gaz_id: Optional[str] = None
    ancestor_gaz_ids: List[str] = Field(default_factory=list)
    pleiades_id: Optional[str] = None
    geonames_id: Optional[str] = None
    landing_page_url: Optional[HttpUrl] = None
    verification_note: Optional[str] = None


class ResolvedAuthor(BaseModel):
    """A disambiguated author identity, produced by ``resolve_author``.

    ``source`` records where the identity came from so the agent can
    weight its trust: ``"override"`` (DAO-curated YAML) ranks highest,
    then ``"wikidata"``, then ``"gnd"``.
    """

    name_canonical: str
    name_variants: List[str] = Field(default_factory=list)
    q_id: Optional[str] = None
    gnd_id: Optional[str] = None
    orcid: Optional[str] = None
    viaf_id: Optional[str] = None
    domain: Optional[str] = None
    affiliation_current: Optional[str] = None
    birth_year: Optional[int] = None
    death_year: Optional[int] = None
    sites_associated: List[str] = Field(default_factory=list)
    source: str
    verification_note: Optional[str] = None


class Identifiers(BaseModel):
    """Structured identifier block, broken out so the agent can route to
    the right downstream tool without parsing ``doi_or_id`` prefixes.

    Coexists with the legacy ``DAOPaper.doi_or_id`` string for backward
    compatibility — the frozen verification fingerprints still rely on
    that field's prefix conventions.
    """

    doi: Optional[str] = None
    openalex_id: Optional[str] = None
    zenon_id: Optional[str] = None
    iaa_pub_id: Optional[str] = None
    adaj_id: Optional[str] = None


class Audit(BaseModel):
    """Provenance flags consumed by the agent's citation renderer.

    ``warn_marker`` is the structural switch for the ⚠️-prefix in
    ``InlineCitation.markdown``: set it whenever the agent should
    surface uncertainty (aggregator hit, fuzzy match, forthcoming).
    """

    primary_source: bool = True
    aggregator: bool = False
    verification_note: Optional[str] = None
    warn_marker: bool = False


class InlineCitation(BaseModel):
    """Pre-rendered citation strings the agent copies verbatim.

    Output-shape lock-in: by handing the agent ready Markdown, we
    structurally enforce AGENTS.md's inline-link rule instead of
    relying on prompt-side guidance. Multiple variants are exposed
    because empirically (Qwen 3.6 Plus test, 2026-05-15) the agent
    picks the variant that fits its surrounding prose: Author-Year
    inside body text, domain-plus-title for web-style references,
    domain-only for footnotes.

    ``markdown_recommended`` is the first-choice render — already
    prefixed with ⚠️ when ``audit.warn_marker`` is set, so the agent
    can copy it verbatim without re-applying the warning rule.
    """

    primary_url: Optional[HttpUrl] = None
    display_domain: Optional[str] = None

    display_label_authoryear: Optional[str] = None
    display_label_domain: Optional[str] = None
    display_label_domain_title: Optional[str] = None

    markdown_authoryear: Optional[str] = None
    markdown_domain: Optional[str] = None
    markdown_domain_title: Optional[str] = None

    markdown_recommended: str
    fallback_text: str


class DAOPaper(BaseModel):
    """Unified output schema for every search adapter.

    The ``source`` field identifies the adapter (``"zenon"`` | ``"iaa"``
    | ``"adaj"``). ``doi_or_id`` is a stable identifier that may be a
    DOI, a Zenon record-ID, an IAA report number, or an ADAJ
    volume/article reference.

    Citation rendering — when citing a hit in body text, copy
    ``inline_citation.markdown_recommended`` verbatim. It is the
    pre-rendered Markdown link (Author-Year form for academic hits,
    Domain-Title form for web hits, ⚠️-prefixed for aggregators or
    flagged hits). Do not reconstruct citations from ``doi_or_id`` /
    ``landing_page_url`` / ``authors`` — the builder has already chosen
    the format that fits the source. Only fall back to
    ``inline_citation.fallback_text`` when ``primary_url`` is ``null``.
    """

    title: str
    authors: List[str] = Field(default_factory=list)
    authors_resolved: Optional[List[ResolvedAuthor]] = None
    year: Optional[int] = None
    journal_or_volume: Optional[str] = None
    pages: Optional[str] = None
    doi_or_id: str
    source: str
    open_access_url: Optional[HttpUrl] = None
    landing_page_url: Optional[HttpUrl] = None

    language: str = "und"
    abstract: Optional[str] = None

    site_ids: List[str] = Field(default_factory=list)
    periods: List[str] = Field(default_factory=list)
    regions: List[str] = Field(default_factory=list)

    publication_status: PublicationStatus = PublicationStatus.UNKNOWN
    verification_note: Optional[str] = None

    identifiers: Optional[Identifiers] = None
    audit: Optional[Audit] = None
    inline_citation: Optional[InlineCitation] = None
