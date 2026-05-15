"""IAA Publications adapter — MVP-incomplete (see docs/2026-05-15-initial-mvp.md Abschnitt XIII.2).

Status (probed 2026-05-15)
--------------------------
``publications.iaa.org.il`` runs Berkeley Electronic Press (Digital
Commons). The search endpoint at ``/do/search/?q=<q>`` returns an HTML
shell where ``<div id="results-list">`` is **empty** — actual hits are
fetched client-side via a Solr bundle (``/assets/cgi/js/search/solr.pack.js``).
The backend additionally responds intermittently with HTTP 504 Gateway
Timeout on the search path.

Per the briefing (Abschnitt XIII.2 and the closing maintenance note),
playwright/headless-browser fallback is **out of scope for the MVP**.
This adapter therefore:

1. Issues the real HTTPS GET against ``/do/search/?q=<q>``.
2. Parses the BePress server-rendered result markup as it *should*
   appear (so that, the moment BePress restores server-side rendering or
   the site exposes a real API, results flow through transparently).
3. If ``#results-list`` is present but empty, raises a structured
   ``IAAUnavailableError`` carrying a clear note for the calling agent.
4. If the upstream returns 5xx, lets ``httpx.HTTPStatusError`` propagate.

The tool is still registered so the MCP surface matches the brief and so
that the moment the upstream becomes scrapable, no client-side change is
needed.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

import httpx
from bs4 import BeautifulSoup, Tag
from mcp.server.fastmcp import FastMCP

from ..models import DAOPaper, PublicationStatus

log = logging.getLogger(__name__)

IAA_SEARCH = "https://publications.iaa.org.il/do/search/"
IAA_BASE = "https://publications.iaa.org.il"
HTTP_TIMEOUT = 30.0

_REPORT_TYPE_CONTEXTS: dict[str, str] = {
    # BePress ``context`` filter values for IAA Publications.
    "report": "iaareports",
    "atiqot": "atiqot",
    "ha-esi": "hadashot",
}

# Permissive year extractor: pulls the first 4-digit year >= 1800 from a
# free-text blob (BePress citations are notoriously unstructured).
_YEAR_RE = re.compile(r"\b(1[89]\d{2}|20\d{2}|21\d{2})\b")


class IAAUnavailableError(RuntimeError):
    """Raised when the IAA backend returned a 200 but its server-rendered
    result list is empty — the canonical "JS-rendered or down" tripwire.
    Distinct from no-hits, which is a legitimate empty result set."""


def _detect_language(text: str) -> str:
    """Hebrew if it contains Hebrew code-points, English otherwise.
    Adapter principle: never silently guess — if neither matches we mark
    it as ``und`` so the agent knows to verify."""
    if any("֐" <= ch <= "׿" for ch in text):
        return "he"
    if re.search(r"[A-Za-z]{4,}", text):
        return "en"
    return "und"


def _absolute_url(href: str) -> Optional[str]:
    if not href:
        return None
    if href.startswith(("http://", "https://")):
        return href
    if href.startswith("/"):
        return IAA_BASE + href
    return None


def _parse_result_tag(tag: Tag) -> Optional[DAOPaper]:
    """Map one BePress result block to a DAOPaper.

    BePress server-rendered results vary slightly across instances, so
    this is deliberately defensive: it looks for the first anchor under
    the block as the title link, and falls back through several class
    names for the author/year metadata.
    """
    link = tag.find("a", href=True)
    if not link:
        return None
    title = link.get_text(strip=True)
    if not title:
        return None

    href = _absolute_url(link["href"])
    if not href:
        return None

    # Author candidates, in priority order
    author_text = ""
    for cls in ("dc-result-creator", "result-creator", "creator", "author", "authors"):
        node = tag.find(class_=cls)
        if node:
            author_text = node.get_text(" ", strip=True)
            break
    # Authors are separated by ``;`` or `` and ``; commas are part of
    # the last-name-first format (``Cohen, R.``), not author separators.
    authors = [a.strip() for a in re.split(r"\s*;\s*|\s+and\s+|\s+&\s+", author_text) if a.strip()] if author_text else []

    # Year — search the whole block text
    year_match = _YEAR_RE.search(tag.get_text(" ", strip=True))
    year = int(year_match.group(1)) if year_match else None

    # Stable ID derived from the URL path (e.g. ``/favissa/312`` -> ``iaa:favissa/312``)
    path_id = href.replace(IAA_BASE, "").strip("/")
    doi_or_id = f"iaa:{path_id}" if path_id else f"iaa:{title[:48]}"

    return DAOPaper(
        title=title,
        authors=authors,
        year=year,
        doi_or_id=doi_or_id,
        source="iaa",
        landing_page_url=href,  # type: ignore[arg-type]
        language=_detect_language(f"{title} {author_text}"),
        publication_status=PublicationStatus.PUBLISHED,
    )


def _parse_results(html: str) -> list[DAOPaper]:
    """Parse a BePress search page.

    Raises IAAUnavailableError when the results-list is present but empty
    — the canonical signal that results are JS-rendered or the backend
    skipped the server-render pass.
    """
    soup = BeautifulSoup(html, "lxml")
    results_list = soup.find(id="results-list")
    if results_list is None:
        # Old layouts (or other BePress instances) may use a different
        # container. Fall back to ``#search-results`` or ``#query-results``.
        results_list = soup.find(id="search-results") or soup.find(id="query-results")
    if results_list is None:
        raise IAAUnavailableError(
            "IAA search page returned HTML but no recognised results container."
            " The site layout may have changed."
        )

    candidates: list[Tag] = []
    # BePress server-rendered classes seen in the wild, in priority order.
    for selector in (
        "div.dc-result",
        "li.dc-result",
        "article.dc-result",
        "div.search-result",
        "li.search-result",
        "article.search-result",
        "div.result",
    ):
        tag, _, cls = selector.partition(".")
        found = results_list.find_all(tag, class_=cls)
        if found:
            candidates = found
            break

    if not candidates:
        raise IAAUnavailableError(
            "IAA Publications returned an empty server-rendered result list. "
            "The site currently delivers results via client-side JavaScript "
            "(Solr bundle). See docs/2026-05-15-initial-mvp.md Abschnitt XIII.2. "
            "Cross-check this query via search_zenon for IAA-indexed records."
        )

    out: list[DAOPaper] = []
    for c in candidates:
        paper = _parse_result_tag(c)
        if paper is not None:
            out.append(paper)
    return out


def _build_params(
    query: str,
    max_results: int,
    report_type: Optional[str],
) -> dict[str, str]:
    params: dict[str, str] = {"q": query}
    if report_type:
        ctx = _REPORT_TYPE_CONTEXTS.get(report_type.lower())
        if ctx:
            params["context"] = ctx
    # BePress uses ``start`` for offset; we always start at 0 and clamp results below.
    _ = max_results  # used by caller to truncate (BePress page-size is fixed at ~20)
    return params


async def search_iaa_impl(
    query: str,
    max_results: int = 10,
    report_type: Optional[str] = None,
    *,
    client: Optional[httpx.AsyncClient] = None,
) -> list[DAOPaper]:
    """Search IAA Publications. May raise ``IAAUnavailableError`` if the
    upstream is currently JS-rendered (see module docstring)."""
    params = _build_params(query, max_results, report_type)
    log.info("iaa.search query=%r context=%s", query, params.get("context"))

    async def _run(c: httpx.AsyncClient) -> list[DAOPaper]:
        headers = {
            # Be polite, identify ourselves; IAA has no explicit User-Agent policy
            # but the briefing flags rate-limit considerations (1 req/s ceiling).
            "User-Agent": "dao-paper-search-mcp/0.1 (https://github.com/patrick-leiverkus/dao-paper-search-mcp)",
            "Accept": "text/html,application/xhtml+xml",
        }
        r = await c.get(IAA_SEARCH, params=params, headers=headers, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        results = _parse_results(r.text)
        return results[:max_results]

    if client is not None:
        return await _run(client)
    async with httpx.AsyncClient() as c:
        return await _run(c)


def register(mcp: FastMCP) -> None:
    """Register the ``search_iaa`` tool.

    Currently marked MVP-incomplete: the IAA backend serves results via
    client-side JavaScript. When invoked, the tool either returns real
    hits (if BePress restores server-side rendering) or raises
    ``IAAUnavailableError`` with a clear note for the calling agent.
    """

    @mcp.tool()
    async def search_iaa(
        query: str,
        max_results: int = 10,
        report_type: Optional[str] = None,
    ) -> list[DAOPaper]:
        """Search IAA Publications — Israel Antiquities Authority Reports, ʿAtiqot,
        HA-ESI (Hadashot Arkheologiyot). Hebrew + English, primary source for
        Israeli excavation grey literature.

        STATUS: MVP-incomplete. The IAA backend currently renders search
        results client-side via JavaScript; this tool returns
        IAAUnavailableError until the upstream restores server-side
        rendering or a playwright fallback is added post-MVP. Use
        search_zenon as a cross-check — Zenon DAI partially indexes IAA
        publications.

        Args:
            query: free-text query.
            max_results: 1-50.
            report_type: optional collection filter — ``"report"``,
                ``"atiqot"``, or ``"ha-esi"``.
        """
        return await search_iaa_impl(
            query=query,
            max_results=max_results,
            report_type=report_type,
        )
