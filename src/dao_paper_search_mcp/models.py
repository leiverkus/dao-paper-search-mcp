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


class DAOPaper(BaseModel):
    """Unified output schema for every search adapter.

    The ``source`` field identifies the adapter (``"zenon"`` | ``"iaa"``
    | ``"adaj"``). ``doi_or_id`` is a stable identifier that may be a
    DOI, a Zenon record-ID, an IAA report number, or an ADAJ
    volume/article reference.
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
