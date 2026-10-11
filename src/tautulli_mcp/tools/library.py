"""Library overview and item browsing tool."""

from fastmcp import Context, FastMCP

from tautulli_mcp.formatting import clamp, compact_lines, enum_error, rows
from tautulli_mcp.tools.common import client_from_context

ORDER_BY = (
    "file_size",
    "play_count",
    "added_at",
    "video_resolution",
    "last_played",
    "title",
)
ORDER_DIRECTIONS = ("asc", "desc")


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
        section_id: str | None = None,
        order_by: str = "added_at",
        order_dir: str = "desc",
        length: int = 25,
        search: str | None = None,
    ) -> str:
        if order_by not in ORDER_BY:
            return enum_error("order_by", order_by, ORDER_BY)
        if order_dir not in ORDER_DIRECTIONS:
            return enum_error("order_dir", order_dir, ORDER_DIRECTIONS)
        client = client_from_context(ctx)
        if section_id is None:
            return compact_lines(
                "Libraries",
                rows(await client.call("get_libraries_table")),
                ("section_id", "section_name", "count", "parent_count"),
            )
        params = {
            "section_id": section_id,
            "order_by": order_by,
            "order_dir": order_dir,
            "length": clamp(length, 1, 100),
        }
        if search:
            params["search"] = search
        return compact_lines(
            f"Library section {section_id}",
            rows(await client.call("get_library_media_info", **params)),
            ("rating_key", "title", "year", "media_type", "file_size", "play_count", "last_played"),
        )
