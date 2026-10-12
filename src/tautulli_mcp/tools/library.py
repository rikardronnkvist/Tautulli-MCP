"""Library overview and item browsing tool."""

import re
from typing import Annotated, Any, Literal

from fastmcp import Context, FastMCP
from pydantic import Field

from tautulli_mcp.formatting import (
    clamp,
    enum_error,
    format_size,
    format_timestamp,
    identifiers,
    media_title,
    number,
    rows,
    text,
)
from tautulli_mcp.tools.common import client_from_context

ORDER_BY = (
    "file_size",
    "play_count",
    "added_at",
    "video_resolution",
    "last_played",
    "sort_title",
    "bitrate",
    "video_codec",
    "audio_codec",
    "container",
)
ORDER_DIRECTIONS = ("asc", "desc")


def _library_size(value: Any) -> str:
    rendered = text(value)
    if not rendered:
        return ""
    if re.fullmatch(r"\d+(?:\.\d+)?\s*[KMGTPE]?I?B", rendered, re.IGNORECASE):
        return rendered
    try:
        float(rendered)
    except ValueError:
        return ""
    return format_size(value)


def _overview(item: dict[str, Any]) -> str:
    section = item.get("section_id", item.get("id"))
    kind = text(item.get("section_type") or item.get("type"))
    name = text(item.get("section_name") or item.get("name"), "Library")
    parts = [f"{name} {identifiers({'section_id': section})}".strip()]
    if kind:
        parts.append(kind.title())
    labels = {
        "movie": ("movies",),
        "show": ("shows", "seasons", "episodes"),
        "artist": ("artists", "albums", "tracks"),
    }.get(kind, ("items",))
    for key, label in zip(("count", "parent_count", "child_count"), labels, strict=False):
        if text(item.get(key)):
            parts.append(f"{number(item[key]):g} {label}")
    if text(item.get("plays", item.get("total_plays"))):
        parts.append(f"{number(item.get('plays', item.get('total_plays'))):g} total plays")
    last_value = item.get("last_played") or item.get("last_accessed")
    last_played = format_timestamp(last_value) if last_value not in (None, "", 0, "0") else ""
    if last_played:
        parts.append(f"Last played: {last_played}")
    return " • " + " | ".join(parts)


def _item_line(item: dict[str, Any]) -> str:
    parts = [f"{media_title(item)} {identifiers({'rating_key': item.get('rating_key')})}".strip()]
    for key in ("video_resolution", "video_codec", "audio_codec", "container"):
        value = text(item.get(key))
        if value:
            parts.append(value)
    size = _library_size(item.get("file_size"))
    if size:
        parts.append(size)
    if text(item.get("play_count")):
        parts.append(f"{number(item['play_count']):g} plays")
    last_value = item.get("last_played")
    last_played = format_timestamp(last_value) if last_value not in (None, "", 0, "0") else ""
    if last_played:
        parts.append(f"Last played: {last_played}")
    detail = " | ".join(part for part in parts if part)
    return " • " + detail if detail else ""


def register_library_tools(mcp: FastMCP) -> None:
    @mcp.tool(
        annotations={"readOnlyHint": True},
        description=(
            "Show library sections (use their IDs to browse items), or list items within a "
            "section. Use play_count ascending to find never-watched media."
        ),
    )
    async def library(
        ctx: Context,
        section_id: Annotated[
            int | None, Field(description="Library section ID; omit to list all libraries.")
        ] = None,
        order_by: Annotated[
            Literal[
                "added_at",
                "sort_title",
                "file_size",
                "play_count",
                "last_played",
                "video_resolution",
                "bitrate",
                "video_codec",
                "audio_codec",
                "container",
            ],
            Field(description="Field used to sort library items."),
        ] = "added_at",
        order_dir: Annotated[
            Literal["asc", "desc"], Field(description="Ascending or descending sort order.")
        ] = "desc",
        length: Annotated[
            int, Field(description="Number of items to return, clamped to 1–100.")
        ] = 25,
        search: Annotated[
            str | None, Field(description="Optional title filter for items within a section.")
        ] = None,
    ) -> str:
        if order_by not in ORDER_BY:
            return enum_error("order_by", order_by, ORDER_BY)
        if order_dir not in ORDER_DIRECTIONS:
            return enum_error("order_dir", order_dir, ORDER_DIRECTIONS)
        client = client_from_context(ctx)
        if section_id is None:
            lines = ["Libraries"]
            for item in rows(await client.call("get_libraries_table")):
                lines.append(_overview(item))
            return "\n".join(lines)
        try:
            section_id = int(section_id)
        except (TypeError, ValueError, OverflowError):
            return "section_id must be an integer."
        params = {
            "section_id": section_id,
            "order_column": order_by,
            "order_dir": order_dir,
            "length": clamp(length, 1, 100),
        }
        if search:
            params["search"] = search
        data = await client.call("get_library_media_info", **params)
        title = f"Library section {section_id}"
        total_size = _library_size(data.get("total_file_size")) if isinstance(data, dict) else ""
        if total_size:
            title += f" | Total library size: {total_size}"
        entries = rows(data)
        if isinstance(data, dict) and not any(
            key in data
            for key in ("data", "rows", "records", "items", "rating_key", "title", "full_title")
        ):
            entries = []
        return "\n".join([title, *(line for item in entries if (line := _item_line(item)))])
