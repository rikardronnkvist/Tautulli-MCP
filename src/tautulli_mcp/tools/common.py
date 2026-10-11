"""Shared helpers for MCP tool implementations."""

from typing import Any

from fastmcp import Context

from tautulli_mcp.tautulli import TautulliClient


def client_from_context(ctx: Context) -> TautulliClient:
    return ctx.lifespan_context["tautulli"]


def display_value(data: Any, keys: tuple[str, ...]) -> str:
    if isinstance(data, dict):
        values = [f"{key}={data[key]}" for key in keys if data.get(key) is not None]
        if values:
            return " | ".join(values)
        nested = next((value for value in data.values() if isinstance(value, list)), None)
        if nested is not None:
            return ", ".join(map(str, nested)) or "none"
    return str(data) if data not in (None, "", [], {}) else "none"
