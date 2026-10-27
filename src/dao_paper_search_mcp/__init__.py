"""dao-paper-search-mcp: vertical MCP server for the DAO domain."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("dao-paper-search-mcp")
except PackageNotFoundError:  # running from a source tree that was never installed
    __version__ = "0.0.0"
