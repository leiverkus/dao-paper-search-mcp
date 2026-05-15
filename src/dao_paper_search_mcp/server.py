"""MCP server entry point for dao-paper-search-mcp.

Stdio cleanliness invariant: stdout is reserved for JSON-RPC. All logging
goes to stderr. Do not print() anywhere in this codebase.
"""

from __future__ import annotations

import logging
import sys

from mcp.server.fastmcp import FastMCP

from .adapters import adaj as _adaj
from .adapters import arxiv as _arxiv
from .adapters import crossref as _crossref
from .adapters import iaa as _iaa
from .adapters import openalex as _openalex
from .adapters import semantic_scholar as _semantic_scholar
from .adapters import zenon as _zenon
from .resolvers import gazetteer as _gazetteer
from .resolvers import wikidata_author as _author

logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("dao_paper_search_mcp")

mcp = FastMCP("dao-paper-search-mcp")


@mcp.tool()
async def ping() -> str:
    """Health check. Returns "pong" if the server is alive.

    Use this to verify the server is loaded and responsive before issuing
    real search calls.
    """
    log.info("ping() called")
    return "pong"


_zenon.register(mcp)
_iaa.register(mcp)
_adaj.register(mcp)
_crossref.register(mcp)
_openalex.register(mcp)
_semantic_scholar.register(mcp)
_arxiv.register(mcp)
_author.register(mcp)
_gazetteer.register(mcp)


def main() -> None:
    """Entry point for `python -m dao_paper_search_mcp.server` and the
    `dao-paper-search-mcp` console script."""
    log.info("dao-paper-search-mcp starting on stdio")
    mcp.run()


if __name__ == "__main__":
    main()
