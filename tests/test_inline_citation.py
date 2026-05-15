"""Unit tests for the InlineCitation builder.

Covers the source-priority table, the three Markdown variants
(Author-Year / Domain / Domain-Title), the ``markdown_recommended``
heuristic, and the print-only / aggregator edge cases from the
two inline-citation briefings (2026-05-15).
"""

from __future__ import annotations

from dao_paper_search_mcp.inline_citation import build_inline_citation
from dao_paper_search_mcp.models import Audit, Identifiers


def _audit(warn: bool = False) -> Audit:
    return Audit(primary_source=True, aggregator=False, warn_marker=warn)


def test_doi_wins_over_landing_url() -> None:
    ic = build_inline_citation(
        authors=["Cohen, R."],
        year=1979,
        pages="61–79",
        title="The Iron Age Fortresses in the Central Negev",
        identifiers=Identifiers(doi="10.2307/1356668", zenon_id="123"),
        landing_page_url="https://zenon.dainst.org/Record/123",
        open_access_url=None,
        audit=_audit(),
    )
    assert str(ic.primary_url) == "https://doi.org/10.2307/1356668"
    assert ic.display_domain == "doi.org"
    assert ic.fallback_text == "Cohen 1979: 61–79"


def test_recommended_uses_authoryear_for_doi_hit() -> None:
    ic = build_inline_citation(
        authors=["Cohen, R."],
        year=1979,
        pages=None,
        title="The Iron Age Fortresses in the Central Negev",
        identifiers=Identifiers(doi="10.2307/1356668"),
        landing_page_url=None,
        open_access_url=None,
        audit=_audit(),
    )
    assert ic.markdown_recommended == "[(Cohen 1979)](https://doi.org/10.2307/1356668)"
    assert ic.markdown_authoryear == "[(Cohen 1979)](https://doi.org/10.2307/1356668)"
    assert ic.markdown_domain == "[(doi.org)](https://doi.org/10.2307/1356668)"
    # DOI variant — visible label is the DOI string itself (for
    # bibliography entries where readers want to read/copy the DOI).
    assert ic.markdown_doi == "[(10.2307/1356668)](https://doi.org/10.2307/1356668)"
    assert ic.display_label_doi == "10.2307/1356668"
    # Title short enough to fit unchanged.
    assert ic.markdown_domain_title == (
        "[(doi.org — The Iron Age Fortresses in the Central Negev)]"
        "(https://doi.org/10.2307/1356668)"
    )
    assert ic.display_label_authoryear == "Cohen 1979"
    assert ic.display_label_domain == "doi.org"


def test_recommended_falls_back_to_domain_title_without_year() -> None:
    """No year context → no Author-Year variant → Domain-Title wins."""
    ic = build_inline_citation(
        authors=["Cohen, R."],
        year=None,
        pages=None,
        title="Some Excavation Report",
        identifiers=Identifiers(iaa_pub_id="report/12"),
        landing_page_url=None,
        open_access_url=None,
        audit=_audit(),
    )
    assert ic.markdown_authoryear is None
    assert ic.markdown_recommended == (
        "[(publications.iaa.org.il — Some Excavation Report)]"
        "(https://publications.iaa.org.il/report/12)"
    )


def test_recommended_falls_back_to_domain_when_no_title_and_no_year() -> None:
    ic = build_inline_citation(
        authors=[],
        year=None,
        pages=None,
        title=None,
        identifiers=Identifiers(),
        landing_page_url="https://www.example.org/x",
        open_access_url=None,
        audit=_audit(),
    )
    assert ic.markdown_authoryear is None
    assert ic.markdown_domain_title is None
    assert ic.markdown_recommended == "[(example.org)](https://www.example.org/x)"
    # www. stripped from the display domain.
    assert ic.display_domain == "example.org"


def test_zenon_when_no_doi() -> None:
    ic = build_inline_citation(
        authors=["Cohen, R."],
        year=1979,
        pages=None,
        title="t",
        identifiers=Identifiers(zenon_id="123"),
        landing_page_url=None,
        open_access_url=None,
        audit=_audit(),
    )
    assert str(ic.primary_url) == "https://zenon.dainst.org/Record/123"
    assert ic.markdown_domain == "[(zenon.dainst.org)](https://zenon.dainst.org/Record/123)"
    assert ic.markdown_recommended == "[(Cohen 1979)](https://zenon.dainst.org/Record/123)"


def test_iaa_when_no_doi() -> None:
    ic = build_inline_citation(
        authors=["Carmi, I.", "Segal, D."],
        year=2007,
        pages=None,
        title="C14 dates from the Negev",
        identifiers=Identifiers(iaa_pub_id="favissa/312"),
        landing_page_url=None,
        open_access_url=None,
        audit=_audit(),
    )
    assert ic.display_label_authoryear == "Carmi & Segal 2007"
    assert ic.markdown_recommended == (
        "[(Carmi & Segal 2007)](https://publications.iaa.org.il/favissa/312)"
    )


def test_adaj_chapter() -> None:
    ic = build_inline_citation(
        authors=["Bienkowski, P."],
        year=2013,
        pages=None,
        title="Edom and the Edomites",
        identifiers=Identifiers(adaj_id="chapter:212"),
        landing_page_url=None,
        open_access_url=None,
        audit=_audit(),
    )
    assert str(ic.primary_url) == (
        "https://publication.doa.gov.jo/Publications/ViewChapterPublic/212"
    )
    assert ic.markdown_recommended == (
        "[(Bienkowski 2013)]"
        "(https://publication.doa.gov.jo/Publications/ViewChapterPublic/212)"
    )


def test_open_access_fallback() -> None:
    ic = build_inline_citation(
        authors=["Tebes, J. M."],
        year=2020,
        pages=None,
        title="Edomite religion revisited",
        identifiers=Identifiers(),
        landing_page_url=None,
        open_access_url="https://propylaeum.de/Papers/some-paper.pdf",
        audit=_audit(),
    )
    assert ic.display_domain == "propylaeum.de"
    assert ic.markdown_recommended == (
        "[(Tebes 2020)](https://propylaeum.de/Papers/some-paper.pdf)"
    )


def test_print_only_fallback_text() -> None:
    ic = build_inline_citation(
        authors=["Aharoni, Y."],
        year=1962,
        pages="55–60",
        title="Some out-of-print study",
        identifiers=Identifiers(),
        landing_page_url=None,
        open_access_url=None,
        audit=_audit(),
    )
    assert ic.primary_url is None
    assert ic.display_domain is None
    assert ic.markdown_recommended == "Aharoni 1962: 55–60"
    assert ic.fallback_text == "Aharoni 1962: 55–60"


def test_warn_marker_prefix_only_on_link_form() -> None:
    ic = build_inline_citation(
        authors=["Cohen, R."],
        year=1979,
        pages=None,
        title="t",
        identifiers=Identifiers(doi="10.2307/1356668"),
        landing_page_url=None,
        open_access_url=None,
        audit=_audit(warn=True),
    )
    assert ic.markdown_recommended.startswith("⚠️[(")
    assert ic.markdown_recommended == "⚠️[(Cohen 1979)](https://doi.org/10.2307/1356668)"
    # Variants themselves stay clean — only ``markdown_recommended``
    # carries the prefix so the agent can still pick a variant freely.
    assert ic.markdown_authoryear == "[(Cohen 1979)](https://doi.org/10.2307/1356668)"


def test_warn_marker_with_no_url_does_not_prefix_fallback() -> None:
    """When there is no link target, the fallback prints bare — the
    ⚠️-prefix only attaches to link-form markdown, never to plain text."""
    ic = build_inline_citation(
        authors=["Aharoni, Y."],
        year=1962,
        pages=None,
        title=None,
        identifiers=Identifiers(),
        landing_page_url=None,
        open_access_url=None,
        audit=_audit(warn=True),
    )
    assert ic.markdown_recommended == "Aharoni 1962"


def test_three_or_more_authors_use_et_al() -> None:
    ic = build_inline_citation(
        authors=["Cohen, R.", "Yisrael, Y.", "Anbar, M."],
        year=1995,
        pages="223–235",
        title="t",
        identifiers=Identifiers(doi="10.1/x"),
        landing_page_url=None,
        open_access_url=None,
        audit=_audit(),
    )
    assert ic.display_label_authoryear == "Cohen et al. 1995"
    assert ic.markdown_recommended == "[(Cohen et al. 1995)](https://doi.org/10.1/x)"
    assert ic.fallback_text == "Cohen et al. 1995: 223–235"


def test_anonymous_skips_authoryear_variant() -> None:
    ic = build_inline_citation(
        authors=[],
        year=2024,
        pages=None,
        title="Anonymous note",
        identifiers=Identifiers(),
        landing_page_url="https://example.org/x",
        open_access_url=None,
        audit=_audit(),
    )
    assert ic.markdown_authoryear is None
    assert ic.display_label_authoryear is None
    assert ic.markdown_recommended == (
        "[(example.org — Anonymous note)](https://example.org/x)"
    )
    assert ic.fallback_text == "Anon. 2024"


def test_markdown_bibliography_prefers_doi_form_when_doi_present() -> None:
    """v0.6.2: bibliography variant exists as a context-named pflichtfeld
    so the agent doesn't have to interpret docstring hints. With a DOI
    registered it renders the DOI string in the visible label."""
    ic = build_inline_citation(
        authors=["Cohen, R."],
        year=1979,
        pages=None,
        title="t",
        identifiers=Identifiers(doi="10.2307/1356668"),
        landing_page_url=None,
        open_access_url=None,
        audit=_audit(),
    )
    assert ic.markdown_bibliography == (
        "[(10.2307/1356668)](https://doi.org/10.2307/1356668)"
    )
    # The body-text recommended stays Author-Year-form.
    assert ic.markdown_recommended == "[(Cohen 1979)](https://doi.org/10.2307/1356668)"


def test_markdown_bibliography_falls_back_through_authoryear_then_domain() -> None:
    """No DOI → Author-Year link; no Author-Year → Domain-Title; etc."""
    # No DOI but has author+year — author-year link form.
    ic = build_inline_citation(
        authors=["Cohen, R."],
        year=1979,
        pages=None,
        title="t",
        identifiers=Identifiers(zenon_id="123"),
        landing_page_url=None,
        open_access_url=None,
        audit=_audit(),
    )
    assert ic.markdown_bibliography == (
        "[(Cohen 1979)](https://zenon.dainst.org/Record/123)"
    )
    # No DOI, no author-year — domain-title form when title given.
    ic = build_inline_citation(
        authors=[],
        year=None,
        pages=None,
        title="An anonymous work",
        identifiers=Identifiers(),
        landing_page_url="https://example.org/x",
        open_access_url=None,
        audit=_audit(),
    )
    assert ic.markdown_bibliography == (
        "[(example.org — An anonymous work)](https://example.org/x)"
    )


def test_markdown_bibliography_falls_back_to_fallback_text_when_no_url() -> None:
    """Print-only literature has no link target; both context fields
    collapse to the plain Author-Year string."""
    ic = build_inline_citation(
        authors=["Aharoni, Y."],
        year=1962,
        pages="55–60",
        title=None,
        identifiers=Identifiers(),
        landing_page_url=None,
        open_access_url=None,
        audit=_audit(),
    )
    assert ic.markdown_bibliography == "Aharoni 1962: 55–60"
    assert ic.markdown_recommended == "Aharoni 1962: 55–60"


def test_markdown_bibliography_aggregator_overrides_doi_preference() -> None:
    """For aggregator hits surfacing the aggregator domain is more
    important than the DOI, so the cascade switches to Domain-Title.
    ⚠️-prefix applied automatically."""
    ic = build_inline_citation(
        authors=["Cohen, R."],
        year=1979,
        pages=None,
        title="The Iron Age fortresses",
        identifiers=Identifiers(doi="10.2307/1356668"),
        landing_page_url="https://www.researchgate.net/publication/123",
        open_access_url=None,
        audit=Audit(primary_source=False, aggregator=True, warn_marker=True),
    )
    # DOI is still in markdown_doi for callers who want it.
    assert ic.markdown_doi is not None
    # But bibliography surfaces the aggregator domain with ⚠️.
    assert ic.markdown_bibliography.startswith("⚠️[(doi.org — ")


def test_markdown_doi_is_none_when_no_doi_present() -> None:
    """Without a DOI there's nothing to put in the DOI label.
    The agent falls back to other variants for bibliography entries."""
    ic = build_inline_citation(
        authors=["Cohen, R."],
        year=1979,
        pages=None,
        title="t",
        identifiers=Identifiers(zenon_id="123"),
        landing_page_url=None,
        open_access_url=None,
        audit=_audit(),
    )
    assert ic.markdown_doi is None
    assert ic.display_label_doi is None
    # Other variants still present.
    assert ic.markdown_authoryear is not None
    assert ic.markdown_domain == "[(zenon.dainst.org)](https://zenon.dainst.org/Record/123)"


def test_markdown_doi_uses_doi_org_url_regardless_of_landing() -> None:
    """If a hit has both a DOI and a landing URL, the DOI variant
    still points at doi.org/{doi} — never the landing URL."""
    ic = build_inline_citation(
        authors=["Author, A."],
        year=2024,
        pages=None,
        title="t",
        identifiers=Identifiers(doi="10.1/x", zenon_id="999"),
        landing_page_url="https://zenon.dainst.org/Record/999",
        open_access_url=None,
        audit=_audit(),
    )
    assert ic.markdown_doi == "[(10.1/x)](https://doi.org/10.1/x)"


def test_aggregator_picks_domain_title_form_and_warns() -> None:
    """Aggregator hits (ResearchGate / Google Books / Academia) must
    surface the domain in the label — even when authors+year are
    available — so the reader sees the hit is secondary."""
    ic = build_inline_citation(
        authors=["Cohen, R."],
        year=1979,
        pages=None,
        title="The Iron Age Fortresses in the Central Negev",
        identifiers=Identifiers(),
        landing_page_url="https://www.researchgate.net/publication/12345",
        open_access_url=None,
        audit=Audit(primary_source=False, aggregator=True, warn_marker=True),
    )
    assert ic.markdown_recommended.startswith("⚠️[(researchgate.net — ")
    assert ic.markdown_recommended.endswith(
        "](https://www.researchgate.net/publication/12345)"
    )
    # Author-Year variant still gets populated (the agent may want it
    # for the bibliography section even though it isn't the recommended
    # inline form).
    assert ic.markdown_authoryear == (
        "[(Cohen 1979)](https://www.researchgate.net/publication/12345)"
    )


def test_aggregator_without_title_falls_back_to_domain_only() -> None:
    ic = build_inline_citation(
        authors=["Cohen, R."],
        year=1979,
        pages=None,
        title=None,
        identifiers=Identifiers(),
        landing_page_url="https://books.google.com/books?id=xyz",
        open_access_url=None,
        audit=Audit(primary_source=False, aggregator=True, warn_marker=True),
    )
    assert ic.markdown_recommended == (
        "⚠️[(books.google.com)](https://books.google.com/books?id=xyz)"
    )


def test_aggregator_forces_warn_even_when_flag_not_set() -> None:
    """An aggregator hit must always be flagged, even if the adapter
    forgot to set ``warn_marker``."""
    ic = build_inline_citation(
        authors=[],
        year=None,
        pages=None,
        title="Some chapter",
        identifiers=Identifiers(),
        landing_page_url="https://www.academia.edu/12345",
        open_access_url=None,
        audit=Audit(primary_source=False, aggregator=True, warn_marker=False),
    )
    assert ic.markdown_recommended.startswith("⚠️[(academia.edu — ")


def test_long_title_is_truncated_with_ellipsis() -> None:
    long_title = (
        "The Iron Age fortresses in the central Negev highlands: "
        "a re-examination of the Cohen survey and its implications"
    )
    ic = build_inline_citation(
        authors=[],
        year=None,
        pages=None,
        title=long_title,
        identifiers=Identifiers(),
        landing_page_url="https://journals.uchicago.edu/x",
        open_access_url=None,
        audit=_audit(),
    )
    # The label is truncated and ends in the single-character ellipsis.
    assert ic.display_label_domain_title is not None
    assert ic.display_label_domain_title.endswith("…")
    assert "journals.uchicago.edu — " in ic.display_label_domain_title
    # No mid-word breaks — the cut lands on a space (i.e. previous char
    # before the ellipsis is a real word, not a fragment).
    body = ic.display_label_domain_title.rsplit(" — ", 1)[1][:-1]
    assert " " in body  # at least one word boundary preserved
