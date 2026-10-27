"""Tests for the server's tool registry.

The tool names are the contract with every client (research-superpowers' skills
call them by name). An SDK upgrade that changes how tools register — as the
mcp 1.x → 2.x move from FastMCP to MCPServer could have — must not drop one
silently.
"""

from __future__ import annotations

from dao_paper_search_mcp import __version__
from dao_paper_search_mcp.server import mcp

EXPECTED_TOOLS = {
    "ping",
    "resolve_author",
    "resolve_site",
    "search_adaj",
    "search_arxiv",
    "search_biorxiv",
    "search_core",
    "search_crossref",
    "search_gnomon",
    "search_iaa",
    "search_ixtheo",
    "search_openalex",
    "search_openedition",
    "search_propylaeum",
    "search_semantic_scholar",
    "search_zenodo",
    "search_zenon",
}


async def test_every_tool_is_registered() -> None:
    names = {t.name for t in await mcp.list_tools()}
    assert names == EXPECTED_TOOLS


async def test_every_tool_has_a_description_and_input_schema() -> None:
    for tool in await mcp.list_tools():
        assert tool.description, tool.name
        assert tool.input_schema.get("type") == "object", tool.name


async def test_ping_answers_pong() -> None:
    result = await mcp.call_tool("ping", {})
    assert [c.text for c in result.content] == ["pong"]


def test_version_comes_from_the_installed_package() -> None:
    # mcp 2.x reports an empty serverInfo.version unless the server sets one;
    # it must be this package's version, not a hard-coded string.
    assert __version__ != "0.1.0"
    assert __version__.count(".") >= 2
