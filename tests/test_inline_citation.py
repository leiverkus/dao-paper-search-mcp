"""Unit tests for the InlineCitation builder (Schema v2).

Covers the source-priority table, the single ``markdown`` field,
``authoritative_authors_label`` and ``authoritative_bibliography_line``
fields against DOI-consistent hallucination, plus the print-only,
aggregator, and warn-marker edge cases. Adds inline-form tests for the
explicit 3-author rule and particle-name preservation.
"""

from __future__ import annotations

from dao_paper_search_mcp.inline_citation import (
    _family_name,
    _format_authors_full_bibliography,
    build_bibliography_line,
    build_inline_citation,
)
from dao_paper_search_mcp.models import Audit, Identifiers, Venue


def _audit(warn: bool = False) -> Audit:
    return Audit(primary_source=True, aggregator=False, warn_marker=warn)


# ---------------------------------------------------------------------------
# Source-priority cascade
# ---------------------------------------------------------------------------


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
    assert str(ic.url) == "https://doi.org/10.2307/1356668"
    assert ic.fallback_text == "Cohen 1979: 61–79"


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
    assert str(ic.url) == "https://zenon.dainst.org/Record/123"
    assert ic.markdown == "[(Cohen 1979)](https://zenon.dainst.org/Record/123)"


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
    assert ic.markdown == "[(Tebes 2020)](https://propylaeum.de/Papers/some-paper.pdf)"


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
    assert str(ic.url) == (
        "https://publication.doa.gov.jo/Publications/ViewChapterPublic/212"
    )
    assert ic.markdown == (
        "[(Bienkowski 2013)]"
        "(https://publication.doa.gov.jo/Publications/ViewChapterPublic/212)"
    )


# ---------------------------------------------------------------------------
# Markdown cascade: Author-Year → Domain-Title → Domain → fallback
# ---------------------------------------------------------------------------


def test_markdown_authoryear_form_for_doi_hit() -> None:
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
    assert ic.markdown == "[(Cohen 1979)](https://doi.org/10.2307/1356668)"
    assert ic.authoritative_authors_label == "Cohen 1979"


def test_markdown_falls_back_to_domain_title_without_year() -> None:
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
    assert ic.markdown == (
        "[(publications.iaa.org.il — Some Excavation Report)]"
        "(https://publications.iaa.org.il/report/12)"
    )


def test_markdown_falls_back_to_domain_when_no_title_no_year() -> None:
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
    # www. stripped from the displayed domain.
    assert ic.markdown == "[(example.org)](https://www.example.org/x)"


def test_long_title_truncated_with_ellipsis() -> None:
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
    assert ic.markdown.endswith("…)](https://journals.uchicago.edu/x)")
    # No mid-word break — character before the ellipsis is part of a full
    # word (preceded by a non-space character).
    body = ic.markdown.split(" — ", 1)[1]
    label = body.rsplit("…", 1)[0]
    assert not label.endswith(" ")


# ---------------------------------------------------------------------------
# Inline author-label cascade (1 / 2 / 3 / ≥4)
# ---------------------------------------------------------------------------


def test_two_author_inline_form() -> None:
    ic = build_inline_citation(
        authors=["Carmi, I.", "Segal, D."],
        year=2007,
        pages=None,
        title="t",
        identifiers=Identifiers(iaa_pub_id="favissa/312"),
        landing_page_url=None,
        open_access_url=None,
        audit=_audit(),
    )
    assert ic.authoritative_authors_label == "Carmi & Segal 2007"
    assert ic.markdown == (
        "[(Carmi & Segal 2007)](https://publications.iaa.org.il/favissa/312)"
    )


def test_three_author_inline_form() -> None:
    """Three authors: list explicitly, no et al. — three names still
    fit comfortably and the form reads cleanly in prose."""
    ic = build_inline_citation(
        authors=["Boaretto, E.", "Finkelstein, I.", "Shahack-Gross, R."],
        year=2010,
        pages="1–12",
        title="Radiocarbon-based chronology",
        identifiers=Identifiers(doi="10.1017/S0033822200044982"),
        landing_page_url=None,
        open_access_url=None,
        audit=_audit(),
    )
    assert ic.authoritative_authors_label == (
        "Boaretto, Finkelstein & Shahack-Gross 2010"
    )
    assert ic.markdown == (
        "[(Boaretto, Finkelstein & Shahack-Gross 2010)]"
        "(https://doi.org/10.1017/S0033822200044982)"
    )


def test_four_plus_author_et_al() -> None:
    ic = build_inline_citation(
        authors=[
            "Bruins, H. J.",
            "van der Plicht, J.",
            "Mazar, A.",
            "Manning, S. W.",
        ],
        year=2011,
        pages=None,
        title="t",
        identifiers=Identifiers(doi="10.1017/s0033822200034470"),
        landing_page_url=None,
        open_access_url=None,
        audit=_audit(),
    )
    assert ic.authoritative_authors_label == "Bruins et al. 2011"
    assert ic.markdown == (
        "[(Bruins et al. 2011)](https://doi.org/10.1017/s0033822200034470)"
    )


def test_particle_name_van_der_plicht_comma_first() -> None:
    """``"van der Plicht, J."`` — default regex grabs the whole prefix."""
    assert _family_name("van der Plicht, J.") == "van der Plicht"


def test_particle_name_van_der_plicht_suffix_form() -> None:
    """``"Plicht, J. van der"`` — particle suffix is rejoined to the
    family as a prefix in the original order."""
    assert _family_name("Plicht, J. van der") == "van der Plicht"


def test_particle_name_initial_first() -> None:
    """``"J. van der Plicht"`` — initial run is dropped, particle and
    family stay together."""
    assert _family_name("J. van der Plicht") == "van der Plicht"


def test_particle_name_in_inline_label() -> None:
    """End-to-end: particle names survive into the inline author label."""
    ic = build_inline_citation(
        authors=["van der Plicht, J.", "Bruins, H. J."],
        year=2009,
        pages=None,
        title="t",
        identifiers=Identifiers(doi="10.1017/s0033822200033786"),
        landing_page_url=None,
        open_access_url=None,
        audit=_audit(),
    )
    assert ic.authoritative_authors_label == "van der Plicht & Bruins 2009"
    assert "van der Plicht & Bruins 2009" in ic.markdown


# ---------------------------------------------------------------------------
# authoritative_authors_label edge cases
# ---------------------------------------------------------------------------


def test_authoritative_authors_label_no_year() -> None:
    """No year → label is the bare author head, no year suffix."""
    ic = build_inline_citation(
        authors=["Cohen, R."],
        year=None,
        pages=None,
        title="t",
        identifiers=Identifiers(doi="10.1/x"),
        landing_page_url=None,
        open_access_url=None,
        audit=_audit(),
    )
    assert ic.authoritative_authors_label == "Cohen"


def test_authoritative_authors_label_no_authors_no_year() -> None:
    ic = build_inline_citation(
        authors=[],
        year=None,
        pages=None,
        title="t",
        identifiers=Identifiers(),
        landing_page_url="https://example.org/x",
        open_access_url=None,
        audit=_audit(),
    )
    assert ic.authoritative_authors_label is None


# ---------------------------------------------------------------------------
# authoritative_bibliography_line
# ---------------------------------------------------------------------------


def test_bibliography_line_full_format() -> None:
    line = build_bibliography_line(
        authors=["Boaretto, E.", "Finkelstein, I.", "Shahack-Gross, R."],
        year=2010,
        title="Radiocarbon-based chronology of the Levant",
        venue=Venue(name="Radiocarbon", volume="52", issue="2", pages="1–12"),
    )
    assert line == (
        "Boaretto, E., Finkelstein, I., & Shahack-Gross, R. (2010). "
        "Radiocarbon-based chronology of the Levant. "
        "*Radiocarbon* 52(2), 1–12."
    )


def test_bibliography_line_two_authors_with_oxford_comma() -> None:
    line = build_bibliography_line(
        authors=["Cohen, R.", "Yisrael, Y."],
        year=1995,
        title="The Iron Age in the Negev highlands",
        venue=Venue(name="Tel Aviv", volume="22", issue=None, pages="203–215"),
    )
    assert line == (
        "Cohen, R., & Yisrael, Y. (1995). The Iron Age in the Negev "
        "highlands. *Tel Aviv* 22, 203–215."
    )


def test_bibliography_line_single_author() -> None:
    line = build_bibliography_line(
        authors=["Cohen, R."],
        year=1979,
        title="The Iron Age Fortresses in the Central Negev",
        venue=Venue(name="BASOR", volume="236", pages="61–79"),
    )
    assert line == (
        "Cohen, R. (1979). The Iron Age Fortresses in the Central Negev. "
        "*BASOR* 236, 61–79."
    )


def test_bibliography_line_missing_venue() -> None:
    line = build_bibliography_line(
        authors=["Cohen, R."],
        year=1979,
        title="t",
        venue=None,
    )
    # Without venue we still get a usable Author-Year-Title line.
    assert line == "Cohen, R. (1979). t."


def test_bibliography_line_partial_venue_name_only() -> None:
    line = build_bibliography_line(
        authors=["Cohen, R."],
        year=1979,
        title="Some study",
        venue=Venue(name="Tel Aviv"),
    )
    assert line == "Cohen, R. (1979). Some study. *Tel Aviv*."


def test_bibliography_line_missing_year_returns_none() -> None:
    line = build_bibliography_line(
        authors=["Cohen, R."],
        year=None,
        title="t",
        venue=Venue(name="BASOR"),
    )
    assert line is None


def test_bibliography_line_missing_title_returns_none() -> None:
    line = build_bibliography_line(
        authors=["Cohen, R."],
        year=1979,
        title=None,
        venue=Venue(name="BASOR"),
    )
    assert line is None


def test_bibliography_line_particle_authors() -> None:
    """``"van der Plicht, J."`` round-trips: family = ``"van der Plicht"``,
    initial = ``"J."``."""
    line = build_bibliography_line(
        authors=["van der Plicht, J.", "Bruins, H. J."],
        year=2009,
        title="Tell es-Safi radiocarbon",
        venue=Venue(name="Radiocarbon", volume="51", pages="100–110"),
    )
    assert line == (
        "van der Plicht, J., & Bruins, H. J. (2009). "
        "Tell es-Safi radiocarbon. *Radiocarbon* 51, 100–110."
    )


def test_bibliography_author_initial_multi_given() -> None:
    """Multi-token given names get multi-letter initials."""
    assert (
        _format_authors_full_bibliography(["Bruins, Hendrik Jan"])
        == "Bruins, H. J."
    )


def test_bibliography_author_bare_family_no_initial() -> None:
    """When only the family name is parseable, the bibliography line
    surfaces it bare without inventing an initial."""
    assert _format_authors_full_bibliography(["Aristotle"]) == "Aristotle"


def test_bibliography_line_appears_on_full_citation() -> None:
    """End-to-end: ``build_inline_citation`` sets
    ``authoritative_bibliography_line`` when venue is supplied."""
    ic = build_inline_citation(
        authors=["Finkelstein, I."],
        year=1999,
        pages="55–70",
        title="Hazor and the North in the Iron Age: A Low Chronology Perspective",
        identifiers=Identifiers(doi="10.2307/1357451"),
        landing_page_url=None,
        open_access_url=None,
        audit=_audit(),
        venue=Venue(name="BASOR", volume="314", pages="55–70"),
    )
    assert ic.authoritative_bibliography_line == (
        "Finkelstein, I. (1999). "
        "Hazor and the North in the Iron Age: A Low Chronology Perspective. "
        "*BASOR* 314, 55–70."
    )


# ---------------------------------------------------------------------------
# DOI-consistent author-year hallucination protection (Briefing §IX.E, Test 5)
# ---------------------------------------------------------------------------


def test_doi_hallucination_protection_test5() -> None:
    """Builder must surface the canonical Finkelstein 1999 strings for
    DOI 10.2307/1357451 — no "Aharoni 1976" phantom in any field. The
    structural fix is that the agent now has tool-authoritative
    strings to copy verbatim instead of reconstructing from the DOI URL.
    """
    ic = build_inline_citation(
        authors=["Finkelstein, I."],
        year=1999,
        pages="55–70",
        title="Hazor and the North in the Iron Age: A Low Chronology Perspective",
        identifiers=Identifiers(doi="10.2307/1357451"),
        landing_page_url=None,
        open_access_url=None,
        audit=_audit(),
        venue=Venue(name="BASOR", volume="314", pages="55–70"),
    )
    assert ic.markdown == (
        "[(Finkelstein 1999)](https://doi.org/10.2307/1357451)"
    )
    assert ic.authoritative_authors_label == "Finkelstein 1999"
    assert "Finkelstein" in (ic.authoritative_bibliography_line or "")
    # Phantom Aharoni 1976 name must not appear in any tool-emitted field.
    for field in (
        ic.markdown,
        ic.authoritative_authors_label or "",
        ic.authoritative_bibliography_line or "",
        ic.fallback_text,
    ):
        assert "Aharoni" not in field
        assert "1976" not in field


# ---------------------------------------------------------------------------
# Aggregator and warn-marker behavior
# ---------------------------------------------------------------------------


def test_aggregator_picks_domain_title_form_and_warns() -> None:
    """Aggregator hits (ResearchGate / Google Books / Academia) surface
    the domain in the label and get an automatic ⚠️-prefix, even when
    authors+year are available."""
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
    assert ic.markdown.startswith("⚠️[(researchgate.net — ")
    assert ic.markdown.endswith(
        "](https://www.researchgate.net/publication/12345)"
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
    assert ic.markdown == (
        "⚠️[(books.google.com)](https://books.google.com/books?id=xyz)"
    )


def test_warn_marker_prefixes_link_form_only() -> None:
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
    assert ic.markdown == "⚠️[(Cohen 1979)](https://doi.org/10.2307/1356668)"


def test_warn_marker_with_no_url_does_not_prefix_fallback() -> None:
    """No link target → the fallback prose stays unmarked; the ⚠️-prefix
    only attaches to link-form markdown."""
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
    assert ic.markdown == "Aharoni 1962"


# ---------------------------------------------------------------------------
# Print-only behaviour (no URL → fallback prose)
# ---------------------------------------------------------------------------


def test_print_only_no_url() -> None:
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
    assert ic.url is None
    assert ic.markdown == "Aharoni 1962: 55–60"
    assert ic.fallback_text == "Aharoni 1962: 55–60"


def test_print_only_fallback_keeps_et_al_for_compactness() -> None:
    """The print-only fallback prose uses ``et al.`` for ≥3 authors even
    though the inline label form lists three explicitly — the reader has
    no hyperlink to pivot to so we prefer the compact form."""
    ic = build_inline_citation(
        authors=["Boaretto, E.", "Finkelstein, I.", "Shahack-Gross, R."],
        year=2010,
        pages="1–12",
        title="t",
        identifiers=Identifiers(),
        landing_page_url=None,
        open_access_url=None,
        audit=_audit(),
    )
    assert ic.fallback_text == "Boaretto et al. 2010: 1–12"


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
    assert ic.authoritative_authors_label is None
    assert ic.markdown == (
        "[(example.org — Anonymous note)](https://example.org/x)"
    )
    assert ic.fallback_text == "Anon. 2024"
