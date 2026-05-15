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
    semantic_scholar_id: Optional[str] = None
    arxiv_id: Optional[str] = None
    core_id: Optional[str] = None
    europepmc_id: Optional[str] = None


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
    because the agent picks the variant that fits its surrounding
    prose:

    - **Body text:** ``markdown_recommended`` (defaults to Author-Year
      form). Author-Year is empirically what the agent renders when
      URLs and metadata flow together (Qwen 3.6 Plus test, 2026-05-15).
    - **Web references / domain-anchored citations:**
      ``markdown_domain_title`` or ``markdown_domain``.
    - **Bibliography / reference-list entries:** ``markdown_doi`` when
      a DOI is present — the visible label is the actual DOI string,
      which is what readers want for cross-reference and BibTeX
      round-tripping. Falls back to ``markdown_domain`` or
      ``markdown_authoryear`` when no DOI.
    - **Aggregator / warn-flagged hits:** ``markdown_recommended``
      already carries the ⚠️ prefix; no manual re-application needed.
    """

    primary_url: Optional[HttpUrl] = None
    display_domain: Optional[str] = None

    display_label_authoryear: Optional[str] = None
    display_label_domain: Optional[str] = None
    display_label_domain_title: Optional[str] = None
    display_label_doi: Optional[str] = None

    markdown_authoryear: Optional[str] = None
    markdown_domain: Optional[str] = None
    markdown_domain_title: Optional[str] = None
    markdown_doi: Optional[str] = None

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

    For bibliography / reference-list entries (the numbered "Zitierte
    Quellen" section at the end of a research-stand document), prefer
    ``inline_citation.markdown_doi`` when present — it renders the DOI
    string itself in the visible label (e.g.
    ``[(10.1179/tav.1984.1984.2.189)](https://doi.org/10.1179/…)``)
    instead of just ``[(doi.org)](…)``. This is what scholarly readers
    expect for cross-reference and BibTeX round-tripping. Falls back
    to ``markdown_domain`` or ``markdown_authoryear`` when no DOI.
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
