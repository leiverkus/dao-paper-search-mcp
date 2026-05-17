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


class Venue(BaseModel):
    """Bibliographic venue metadata for assembling reference-list entries.

    Fields are strings because real-world values are messy: volumes like
    ``"12A"`` or ``"XXIV"``, page ranges with en-dashes, issue numbers
    that include letter suffixes. Adapters pass what they have; missing
    fields stay ``None`` and the bibliography-line renderer omits them
    defensively.
    """

    name: Optional[str] = None
    volume: Optional[str] = None
    issue: Optional[str] = None
    pages: Optional[str] = None


class InlineCitation(BaseModel):
    """Tool-authoritative citation strings the agent copies verbatim.

    Schema v2 (v0.7.0) collapses six prior ``markdown_*`` variants into
    one ``markdown`` field plus two tool-authoritative bibliographic
    fields. Rationale:

    1. **Format convergence across models.** Literal models (Qwen
       3.5 122B) copied multi-variant schemas verbatim and reflexively
       picked low-information variants (``[(doi.org)](url)``).
       Synthesising models (Ring 2.6 1T) ignored the schema entirely
       and rendered their own Author-Year form. A single ``markdown``
       field makes both pathways converge on the same author-year
       Inline-Citation form.
    2. **Hallucination protection.** Empirically observed
       DOI-consistent author-year hallucinations (same DOI rendered as
       two different authors in the same bibliography) are eliminated
       by exposing ``authoritative_authors_label`` and
       ``authoritative_bibliography_line`` — tool-rendered strings that
       the agent is instructed to copy literally instead of
       reconstructing from training knowledge.

    Field roles:

    - ``url`` — the canonical URL (DOI > OpenAlex > Zenon > IAA >
      ADAJ > arXiv > Semantic Scholar > CORE > Europe PMC > OA >
      Landing).
    - ``markdown`` — finished Inline-Markdown link. Author-Year form
      for academic hits, Domain-Title form for web references,
      Domain-only as last resort. Aggregator and warn-flagged hits get
      the ⚠️-prefix automatically.
    - ``authoritative_authors_label`` — plain-text Author-Year string
      ("Finkelstein 1999"). For agents that prefer to render their own
      Inline-Citation form instead of copying ``markdown``.
    - ``authoritative_bibliography_line`` — the full bibliography-entry
      string ("Finkelstein, I. (1999). Title. *BASOR* 314, 55–70.").
      Copied verbatim into the "Zitierte Quellen" section. ``None``
      when venue metadata is incomplete — in that case the agent must
      fall back to URL/DOI form without reconstructing author or
      journal from training data.
    - ``fallback_text`` — Author-Year-page form for print-only hits
      with no URL anchor.
    """

    url: Optional[HttpUrl] = None
    markdown: str
    authoritative_authors_label: Optional[str] = None
    authoritative_bibliography_line: Optional[str] = None
    fallback_text: str


class DAOPaper(BaseModel):
    """Unified output schema for every search adapter.

    The ``source`` field identifies the adapter (``"zenon"`` | ``"iaa"``
    | ``"adaj"``). ``doi_or_id`` is a stable identifier that may be a
    DOI, a Zenon record-ID, an IAA report number, or an ADAJ
    volume/article reference.

    Citation rendering (Schema v2, v0.7.0) — for in-text citations, copy
    ``inline_citation.markdown`` verbatim. It is the pre-rendered
    Markdown link in Author-Year form for academic hits, Domain-Title
    form for web hits, and ⚠️-prefixed for aggregator or warn-flagged
    hits. Do not reconstruct citations from ``doi_or_id`` /
    ``landing_page_url`` / ``authors`` — the builder has already chosen
    the format that fits the source. Only ``inline_citation.url`` is
    ``None`` (print-only), the agent prints ``fallback_text``.

    For the bibliography section, copy
    ``inline_citation.authoritative_bibliography_line`` verbatim — it
    is the canonical full reference line ("Finkelstein, I. (1999).
    Title. *BASOR* 314, 55–70."). If the field is ``None``, venue
    metadata is incomplete; in that case fall back to Author-Year
    plus URL/DOI without reconstructing author or journal from
    training data. ``authoritative_authors_label`` is the plain-text
    Author-Year string for prose ("Finkelstein 1999") — copy that
    instead of reconstructing one yourself.
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
