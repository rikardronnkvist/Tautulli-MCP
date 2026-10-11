"""Shared helpers for MCP tool implementations."""

from fastmcp import Context

from tautulli_mcp.tautulli import TautulliClient


def client_from_context(ctx: Context) -> TautulliClient:
    return ctx.lifespan_context["tautulli"]
