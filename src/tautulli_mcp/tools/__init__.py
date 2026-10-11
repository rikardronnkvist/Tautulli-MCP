"""Registration helpers for Tautulli read-only tools."""

from fastmcp import FastMCP

from .analytics import register_analytics_tools
from .library import register_library_tools
from .media import register_media_tools


def register_tools(mcp: FastMCP) -> None:
    register_media_tools(mcp)
    register_library_tools(mcp)
    register_analytics_tools(mcp)
