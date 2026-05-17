"""Builder for the ``InlineCitation`` block on every ``DAOPaper`` (Schema v2).

Pure function, no network I/O. Renders one canonical ``markdown`` field
plus tool-authoritative bibliography strings the agent copies verbatim.

Schema v2 collapses six prior ``markdown_*`` variants into a single
``markdown`` field. The cascade is fixed:

    - No URL                       → ``fallback_text``
    - ``audit.aggregator=True``    → Domain(-Title) form, ⚠️-prefixed
      (the visible label must surface the aggregator domain regardless
      of any author-year context, so the reader sees it's a secondary
      hit)
    - Authors + Year present       → Author-Year form (academic body text)
    - URL but no Author-Year ctx   → Domain-Title form (web reference)
    - Domain-only as last resort
    - ``warn_marker=True``         → prepend ⚠️ (link forms only)

Two tool-authoritative fields prevent DOI-consistent author-year
hallucinations observed empirically (same DOI rendered with different
authors in the same bibliography):

    - ``authoritative_authors_label``        — plain-text "Finkelstein 1999"
    - ``authoritative_bibliography_line``    — full reference line

Priority of ``url``:
    DOI > OpenAlex > Zenon > IAA > ADAJ > arXiv > Semantic Scholar >
    CORE > Europe PMC > open_access_url > landing_page_url > None
"""

from __future__ import annotations

import re
from typing import List, Optional
from urllib.parse import urlparse

from .models import Audit, Identifiers, InlineCitation, Venue


_FAMILY_NAME_RE = re.compile(r"^([^,]+),")  # "Cohen, R." -> "Cohen"
_TITLE_MAX_LEN = 60  # truncation threshold for the domain-title form (v2)

# Family-name particles that must travel with the family name.
# Lower-cased for matching; ordering does not matter.
_PARTICLES = frozenset(
    {"van", "von", "de", "der", "den", "del", "della", "di", "da", "le", "la", "el"}
)


def _family_name(author: str) -> str:
    """Family-name extraction with particle preservation.

    Handles three shapes:

    - ``"van der Plicht, J."``     → ``"van der Plicht"`` (default regex)
    - ``"Plicht, J. van der"``     → ``"van der Plicht"`` (suffix particles
      after given names are rejoined to the family name in their
      original order)
    - ``"J. van der Plicht"``      → ``"van der Plicht"`` (initial-first
      with leading particle run)
    - ``"Cohen"``                  → ``"Cohen"``

    Falls back to the whole string when unsure — over-quoting beats
    wrong-quoting in a citation fallback.
    """
    s = author.strip()
    m = _FAMILY_NAME_RE.match(s)
    if m:
        family = m.group(1).strip()
        # Comma-style: check for trailing particles in the given-name tail
        # ("Plicht, J. van der") and rejoin them as a prefix.
        tail = s[m.end():].strip()
        if tail:
            tail_tokens = tail.replace(".", " ").split()
            # Walk from the end collecting particles.
            particles_suffix: List[str] = []
            i = len(tail_tokens) - 1
            while i >= 0 and tail_tokens[i].lower() in _PARTICLES:
                particles_suffix.insert(0, tail_tokens[i])
                i -= 1
            if particles_suffix:
                return " ".join([*particles_suffix, family])
        return family
    # No comma: try initial-first with leading particle run.
    tokens = s.split()
    if len(tokens) >= 2:
        # First drop any leading initials/given-name initials (tokens that
        # are a single letter or end with "."). What remains after that
        # run is the family in initial-first style ("J. van der Plicht").
        i = 0
        while i < len(tokens) and (tokens[i].endswith(".") or len(tokens[i]) == 1):
            i += 1
        if i > 0 and i < len(tokens):
            return " ".join(tokens[i:])
        # No initials to drop — the input is "Given Family"-style with a
        # spelled-out given name (e.g. "Itzhaq Beit-Arieh"). Fall back to
        # the trailing word and any preceding particle run; that beats
        # the over-quoting-the-whole-string behaviour, which spoils the
        # inline label with given names.
        j = len(tokens) - 1
        while j > 0 and tokens[j - 1].lower() in _PARTICLES:
            j -= 1
        return " ".join(tokens[j:])
    return s


def _format_author_label(authors: List[str], year: Optional[int]) -> Optional[str]:
    """Inline Author-Year label for the ``markdown`` field.

    Schema v2 rules:

    - 0 authors           → ``None``
    - 1 author            → ``"Cohen 1979"``
    - 2 authors           → ``"Cohen & Yisrael 1995"``
    - 3 authors           → ``"Boaretto, Finkelstein & Shahack-Gross 2010"``
      (explicit, not et al. — three names still fit comfortably and
      the form reads cleanly in prose)
    - ≥4 authors          → ``"Boaretto et al. 2011"``

    If ``year`` is ``None`` the year suffix is omitted; if both
    ``authors`` and ``year`` are missing returns ``None``.
    """
    if not authors and year is None:
        return None
    if not authors:
        # Year-only labels are useless inline; treat as no label.
        return None

    n = len(authors)
    if n == 1:
        head = _family_name(authors[0])
    elif n == 2:
        head = f"{_family_name(authors[0])} & {_family_name(authors[1])}"
    elif n == 3:
        f1, f2, f3 = (_family_name(a) for a in authors[:3])
        head = f"{f1}, {f2} & {f3}"
    else:
        head = f"{_family_name(authors[0])} et al."

    if year is None:
        return head
    return f"{head} {year}"


def _split_family_given(author: str) -> tuple[str, Optional[str]]:
    """Split one author string into (family, given). ``given`` is ``None``
    if not parseable.

    Adapters are not consistent: Crossref delivers ``"Family, Given"``,
    arXiv ``"Given Family"``, OpenAlex pre-flipped to comma form.
    """
    s = author.strip()
    m = _FAMILY_NAME_RE.match(s)
    if m:
        family = m.group(1).strip()
        # Particle suffix already folded by _family_name; for bibliography
        # we need the given part separately. Strip trailing particle run.
        rest_raw = s[m.end():].strip()
        if rest_raw:
            tokens = rest_raw.replace(".", " . ").split()
            # Re-emit with original dots: split on whitespace, then re-add
            # dots that were stuck to tokens.
            tokens = [t for t in tokens if t]
            # Drop trailing particles
            while tokens and tokens[-1].lower() in _PARTICLES:
                family = f"{tokens[-1]} {family}"
                tokens.pop()
            given = " ".join(tokens).replace(" .", ".") if tokens else None
            return family, given
        return family, None
    tokens = s.split()
    if len(tokens) >= 2:
        # "J. van der Plicht" — given is leading initial run, family is rest
        i = 0
        while i < len(tokens) and (tokens[i].endswith(".") or len(tokens[i]) == 1):
            i += 1
        given = " ".join(tokens[:i]) if i > 0 else None
        family = " ".join(tokens[i:]) if i < len(tokens) else tokens[-1]
        return family, given
    return s, None


def _initial(given: str) -> str:
    """Return ``"R."`` for ``"Roger"``, ``"R. M."`` for ``"Roger M."``.

    Already-abbreviated forms (``"R."`` or ``"R. M."``) pass through.
    Used by the bibliography-line author formatter only.
    """
    out_parts: List[str] = []
    for tok in given.replace(".", " ").split():
        if not tok:
            continue
        out_parts.append(f"{tok[0].upper()}.")
    return " ".join(out_parts)


def _format_one_author_bib(author: str) -> str:
    """Bibliography form of one author: ``"Cohen, R."`` or bare
    ``"Cohen"`` if no given name is parseable."""
    family, given = _split_family_given(author)
    if given:
        return f"{family}, {_initial(given)}"
    return family


def _format_authors_full_bibliography(authors: List[str]) -> str:
    """Full author list for a bibliography entry.

    - 1 author          → ``"Cohen, R."``
    - 2 authors         → ``"Cohen, R., & Yisrael, Y."``
    - ≥3 authors        → ``"Boaretto, E., Finkelstein, I., & Shahack-Gross, R."``

    Oxford comma before the ampersand. No ``et al.`` — the bibliography
    section lists everyone; truncating belongs to the inline form.
    """
    if not authors:
        return "Anon."
    formatted = [_format_one_author_bib(a) for a in authors]
    if len(formatted) == 1:
        return formatted[0]
    if len(formatted) == 2:
        return f"{formatted[0]}, & {formatted[1]}"
    head = ", ".join(formatted[:-1])
    return f"{head}, & {formatted[-1]}"


def build_bibliography_line(
    authors: List[str],
    year: Optional[int],
    title: Optional[str],
    venue: Optional[Venue],
) -> Optional[str]:
    """Render the full bibliography-entry line.

    Format: ``"{Authors} ({Year}). {Title}. *{Venue}* {Vol}({Issue}), {Pages}."``

    Returns ``None`` when authors or year are missing (the bibliography
    needs both to be a stable reference) or when title is missing.
    Vol/Issue/Pages are folded in defensively — each is omitted with
    its surrounding punctuation when ``None``.
    """
    if not authors or year is None or not title:
        return None

    authors_str = _format_authors_full_bibliography(authors)
    line = f"{authors_str} ({year}). {title.strip().rstrip('.')}."

    if venue is None or not venue.name:
        return line

    line += f" *{venue.name}*"
    if venue.volume:
        line += f" {venue.volume}"
        if venue.issue:
            line += f"({venue.issue})"
    if venue.pages:
        line += f", {venue.pages}"
    line += "."
    return line


def _format_fallback(authors: List[str], year: Optional[int], pages: Optional[str]) -> str:
    """Author-Year-page form for print-only hits with no link target.

    Deliberately divergent from ``_format_author_label``: this is the
    string the agent prints bare in prose, so the compact ``"et al."``
    form is preferred over a three-name list — the reader does not have
    a hyperlink to pivot to and we are trying to keep the inline
    citation short.
    """
    if not authors:
        head = "Anon."
    elif len(authors) >= 3:
        head = f"{_family_name(authors[0])} et al."
    else:
        head = " & ".join(_family_name(a) for a in authors)

    parts = [head]
    if year is not None:
        parts.append(str(year))
    text = " ".join(parts)
    if pages:
        text = f"{text}: {pages}"
    return text


def _truncate_title(title: str) -> str:
    """Trim a title to fit inside an inline link label.

    Cut on a word boundary near the limit so we don't slice through a
    word, then append a single-character ellipsis to signal truncation.
    """
    title = title.strip()
    if len(title) <= _TITLE_MAX_LEN:
        return title
    cut = title[:_TITLE_MAX_LEN].rsplit(" ", 1)[0]
    return f"{cut}…"


def _display_domain(url: str) -> str:
    netloc = urlparse(url).netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return netloc


def _pick_primary_url(
    identifiers: Identifiers,
    landing_page_url: Optional[str],
    open_access_url: Optional[str],
) -> Optional[str]:
    """Apply the source-priority table to choose one canonical URL.

    DOI ranks highest because it survives publisher migrations; the
    repository-specific fallbacks (Zenon/IAA/ADAJ) only kick in when no
    DOI is present, so doi.org always wins when both exist.
    """
    if identifiers.doi:
        return f"https://doi.org/{identifiers.doi}"
    if identifiers.openalex_id:
        return f"https://openalex.org/{identifiers.openalex_id}"
    if identifiers.zenon_id:
        return f"https://zenon.dainst.org/Record/{identifiers.zenon_id}"
    if identifiers.iaa_pub_id:
        return f"https://publications.iaa.org.il/{identifiers.iaa_pub_id}"
    if identifiers.adaj_id:
        # ADAJ records live under the DoA Jordan publications portal.
        # ``adaj_id`` is stored as ``"chapter:N"`` or ``"publication:N"`` —
        # map to the matching DoA landing path.
        if identifiers.adaj_id.startswith("chapter:"):
            n = identifiers.adaj_id.split(":", 1)[1]
            return f"https://publication.doa.gov.jo/Publications/ViewChapterPublic/{n}"
        if identifiers.adaj_id.startswith("publication:"):
            n = identifiers.adaj_id.split(":", 1)[1]
            return f"https://publication.doa.gov.jo/Publications/ViewPublic/{n}"
    if identifiers.arxiv_id:
        # arXiv ranks above Semantic Scholar because arxiv.org is the
        # source repository for preprints, while S2 is a pure indexer.
        # When a paper has both, the canonical URL is the arXiv one.
        return f"https://arxiv.org/abs/{identifiers.arxiv_id}"
    if identifiers.semantic_scholar_id:
        return f"https://www.semanticscholar.org/paper/{identifiers.semantic_scholar_id}"
    if identifiers.core_id:
        # CORE aggregates open-access repository content. The CORE
        # landing is canonical when no DOI is registered, even though
        # the underlying source is some institutional repo.
        return f"https://core.ac.uk/works/{identifiers.core_id}"
    if identifiers.europepmc_id:
        # Europe PMC landing — used as last-resort anchor for preprints
        # that haven't been Crossref-registered yet (rare; most bioRxiv
        # preprints have a DOI from day one).
        return f"https://europepmc.org/article/PPR/{identifiers.europepmc_id}"
    if open_access_url:
        return str(open_access_url)
    if landing_page_url:
        return str(landing_page_url)
    return None


def render_markdown(
    *,
    authors: List[str],
    year: Optional[int],
    title: Optional[str],
    primary_url: Optional[str],
    audit: Audit,
    fallback_text: str,
) -> str:
    """Render the single ``markdown`` field per the v2 cascade.

    See module docstring for the full ordering. The ⚠️-prefix attaches
    only to link forms; print-only fallback prose stays unmarked.
    """
    if primary_url is None:
        return fallback_text

    domain = _display_domain(primary_url)
    author_label = _format_author_label(authors, year)
    trunc_title = _truncate_title(title) if title else None

    if audit.aggregator:
        # Aggregator hits surface the domain in the label so readers see
        # the hit is secondary, even when author-year is available.
        if trunc_title:
            body = f"[({domain} — {trunc_title})]({primary_url})"
        else:
            body = f"[({domain})]({primary_url})"
        return f"⚠️{body}"

    # Author-Year branch requires *both* authors and year. A bare
    # "[(Cohen)]" without a year is a worse inline citation than the
    # Domain-Title form, which at least tells the reader what the work
    # is. The ``authoritative_authors_label`` field still surfaces the
    # year-less label for callers that want it.
    if author_label is not None and year is not None and authors:
        body = f"[({author_label})]({primary_url})"
    elif trunc_title is not None:
        body = f"[({domain} — {trunc_title})]({primary_url})"
    else:
        body = f"[({domain})]({primary_url})"

    if audit.warn_marker:
        return f"⚠️{body}"
    return body


def build_inline_citation(
    *,
    authors: List[str],
    year: Optional[int],
    pages: Optional[str],
    title: Optional[str],
    identifiers: Identifiers,
    landing_page_url: Optional[str],
    open_access_url: Optional[str],
    audit: Audit,
    venue: Optional[Venue] = None,
) -> InlineCitation:
    fallback_text = _format_fallback(authors, year, pages)
    primary_url = _pick_primary_url(identifiers, landing_page_url, open_access_url)
    authoritative_authors_label = _format_author_label(authors, year)
    authoritative_bibliography_line = build_bibliography_line(authors, year, title, venue)
    markdown = render_markdown(
        authors=authors,
        year=year,
        title=title,
        primary_url=primary_url,
        audit=audit,
        fallback_text=fallback_text,
    )

    return InlineCitation(
        url=primary_url,  # type: ignore[arg-type]
        markdown=markdown,
        authoritative_authors_label=authoritative_authors_label,
        authoritative_bibliography_line=authoritative_bibliography_line,
        fallback_text=fallback_text,
    )
