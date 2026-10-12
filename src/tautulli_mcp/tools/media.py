"""Playback, history, search, metadata, and recently-added tools."""

import asyncio
from typing import Any

from fastmcp import Context, FastMCP

from tautulli_mcp.formatting import clamp, compact_lines, enum_error, rows, text
from tautulli_mcp.tools.common import client_from_context

MEDIA_TYPES = ("movie", "show", "artist")


def _stats_text(data: Any) -> str:
    records = rows(data)
    if not records and isinstance(data, dict):
        records = [data]
    output = []
    for record in records:
        fields = []
        for key, value in record.items():
            normalized = key.lower()
            if (
                value is not None
                and isinstance(value, (str, int, float, list, tuple))
                and (
                    any(term in normalized for term in ("play", "time", "duration"))
                    or normalized in {"user", "friendly_name", "username"}
                )
            ):
                fields.append(f"{key}={text(value)}")
        if fields:
            output.append(" | ".join(fields))
    return "; ".join(output) or "none"


def register_media_tools(mcp: FastMCP) -> None:
    @mcp.tool(
        annotations={"readOnlyHint": True},
        description=(
            "Show active Plex streams, progress, player, direct-play/transcode details, "
            "bandwidth, and session keys."
        ),
    )
    async def now_playing(ctx: Context) -> str:
        data = await client_from_context(ctx).call("get_activity")
        active = rows(data.get("sessions", data) if isinstance(data, dict) else data)
        if not active:
            return "Now playing: no active streams."
        lines = ["Now playing"]
        for session in active:
            title = text(session.get("full_title") or session.get("title"))
            user = text(session.get("friendly_name") or session.get("user"))
            player = text(session.get("player") or session.get("product"))
            mode = text(
                session.get("stream_video_decision")
                or session.get("video_decision")
                or session.get("quality_profile")
            )
            reason = session.get("transcode_decision_string")
            progress = session.get("progress_percent")
            details = [
                f"{user}: {title}",
                f"progress={text(progress, '0')}%",
                f"player={player}",
                f"mode={mode}",
                f"session_key={text(session.get('session_key'))}",
            ]
            if reason:
                details.append(f"transcode_reason={text(reason)}")
            lines.append(" • " + " | ".join(details))
        if isinstance(data, dict):
            bandwidth_fields = [
                f"{key}={data[key]}"
                for key in ("lan_bandwidth", "wan_bandwidth", "total_bandwidth")
                if data.get(key) is not None
            ]
            if bandwidth_fields:
                lines.append("Bandwidth: " + " | ".join(bandwidth_fields))
        return "\n".join(lines)

    @mcp.tool(
        annotations={"readOnlyHint": True},
        description=(
            "Browse playback history and optionally enrich up to 10 entries with stream details. "
            "Use row_id from results with stream_quality."
        ),
    )
    async def history(
        ctx: Context,
        length: int = 25,
        user: str | None = None,
        media_type: str | None = None,
        search: str | None = None,
        start_date: str | None = None,
        include_stream_details: bool = False,
        include_ip: bool = False,
    ) -> str:
        params: dict[str, Any] = {"length": clamp(length, 1, 100)}
        for key, value in (
            ("user", user),
            ("media_type", media_type),
            ("search", search),
            ("start_date", start_date),
        ):
            if value:
                params[key] = value
        client = client_from_context(ctx)
        entries = rows(await client.call("get_history", **params))
        if include_stream_details:

            async def enrich(entry: dict[str, Any]) -> None:
                row_id = entry.get("row_id")
                if row_id is not None:
                    try:
                        entry["_stream"] = await client.call("get_stream_data", row_id=row_id)
                    except RuntimeError:
                        entry["_stream"] = None

            await asyncio.gather(*(enrich(entry) for entry in entries[:10]))

        fields = ["row_id", "date", "user", "full_title", "media_type", "platform"]
        if include_ip:
            fields.append("ip_address")
        lines = ["History"]
        for entry in entries:
            parts = [
                f"{key}={text(entry.get(key))}" for key in fields if entry.get(key) is not None
            ]
            if include_stream_details and entry.get("_stream") is not None:
                detail_rows = rows(entry["_stream"])
                detail = (
                    detail_rows[0]
                    if detail_rows
                    else (entry["_stream"] if isinstance(entry["_stream"], dict) else {})
                )
                for field in ("video_codec", "audio_codec", "bitrate", "transcode_decision"):
                    if detail.get(field) is not None:
                        parts.append(f"{field}={text(detail[field])}")
            lines.append(" • " + " | ".join(parts) if parts else " • (no details)")
        return "\n".join(lines)

    @mcp.tool(
        annotations={"readOnlyHint": True},
        description=(
            "Search the Tautulli library by title. Returned rating keys can be "
            "passed to item_details."
        ),
    )
    async def search(ctx: Context, query: str, limit: int = 20) -> str:
        if not query.strip():
            return "Search query must not be empty."
        data = await client_from_context(ctx).call(
            "search", query=query.strip(), limit=clamp(limit, 1, 50)
        )
        grouped_results = data.get("results_list") if isinstance(data, dict) else None
        if isinstance(grouped_results, dict):
            results = [
                {"media_type": media_type, **item}
                for media_type, items in grouped_results.items()
                if isinstance(items, list)
                for item in items
                if isinstance(item, dict)
            ]
        else:
            results = rows(data)
        if not results:
            return "Search: no matches."
        groups: dict[str, list[str]] = {}
        for item in results:
            media_type = text(item.get("media_type") or item.get("type"), "other")
            title = text(item.get("title") or item.get("full_title"))
            key = item.get("rating_key")
            groups.setdefault(media_type, []).append(f"{title} [key: {text(key)}]")
        return "\n".join(f"{kind.title()}: " + "; ".join(titles) for kind, titles in groups.items())

    @mcp.tool(
        annotations={"readOnlyHint": True},
        description=(
            "Get metadata and watch statistics for a rating key from search or recently_added. "
            "File paths are never included."
        ),
    )
    async def item_details(ctx: Context, rating_key: str) -> str:
        if not rating_key.strip():
            return "rating_key must not be empty."
        client = client_from_context(ctx)
        metadata, watch_stats, user_stats = await asyncio.gather(
            client.call("get_metadata", rating_key=rating_key.strip()),
            client.call("get_item_watch_time_stats", rating_key=rating_key.strip()),
            client.call("get_item_user_stats", rating_key=rating_key.strip()),
        )
        item = rows(metadata)
        record = item[0] if item else (metadata if isinstance(metadata, dict) else {})
        fields = (
            "title",
            "year",
            "media_type",
            "summary",
            "genres",
            "actors",
            "cast",
            "rating",
            "content_rating",
            "video_resolution",
            "source_resolution",
            "video_codec",
            "audio_codec",
            "video_dynamic_range",
            "container",
            "bitrate",
            "file_size",
        )
        lines = ["Item details"]
        for key in fields:
            if record.get(key) is not None:
                lines.append(f"{key}: {text(record[key])}")
        for title, stats in (("Plays", watch_stats), ("Watched by", user_stats)):
            if stats not in (None, [], {}):
                lines.append(f"{title}: {_stats_text(stats)}")
        if len(lines) == 1:
            return f"Item details for rating key {rating_key}: no metadata available."
        return "\n".join(lines)

    @mcp.tool(
        annotations={"readOnlyHint": True},
        description=(
            "List the newest library additions. Each rating key can be passed to item_details."
        ),
    )
    async def recently_added(ctx: Context, count: int = 10, media_type: str = "movie") -> str:
        if media_type not in MEDIA_TYPES:
            return enum_error("media_type", media_type, MEDIA_TYPES)
        data = await client_from_context(ctx).call(
            "get_recently_added", count=clamp(count, 1, 50), media_type=media_type
        )
        return compact_lines(
            "Recently added",
            rows(data),
            ("rating_key", "title", "year", "added_at"),
        )
