"""MCP server entry point for dao-paper-search-mcp.

Stdio cleanliness invariant: stdout is reserved for JSON-RPC. All logging
goes to stderr. Do not print() anywhere in this codebase.
"""

from __future__ import annotations

import logging
import sys

from mcp.server.fastmcp import FastMCP

from .adapters import zenon as _zenon

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


def main() -> None:
    """Entry point for `python -m dao_paper_search_mcp.server` and the
    `dao-paper-search-mcp` console script."""
    log.info("dao-paper-search-mcp starting on stdio")
    mcp.run()


if __name__ == "__main__":
    main()
