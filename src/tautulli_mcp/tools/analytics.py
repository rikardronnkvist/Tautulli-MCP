"""Top content, user statistics, viewing patterns, and stream quality tools."""

import asyncio
from typing import Any

from fastmcp import Context, FastMCP

from tautulli_mcp.formatting import clamp, compact_lines, enum_error, rows, text
from tautulli_mcp.tools.common import client_from_context

TOP_CATEGORIES = ("tv", "movies", "music", "users")
STAT_TYPES = ("plays", "duration")
PATTERNS = ("date", "weekday", "hour")
QUALITY_VIEWS = ("transcode", "platforms", "resolution", "stream")


def _chart(title: str, data: Any) -> str:
    entries = rows(data)
    if not entries:
        return f"{title}: no data."
    values = []
    for entry in entries:
        label = text(
            entry.get("date")
            or entry.get("day")
            or entry.get("hour")
            or entry.get("label")
            or entry.get("stream_type")
        )
        try:
            amount = float(
                entry.get("plays") or entry.get("count") or entry.get("total_plays") or 0
            )
        except (TypeError, ValueError):
            amount = 0
        values.append((label, amount))
    peak = max(values, key=lambda value: value[1])[1]
    maximum = max(peak, 1)
    lines = [title]
    for label, amount in values:
        bar = "█" * min(30, round(amount / maximum * 30))
        marker = " ← peak" if amount == peak else ""
        lines.append(f"{label}: {bar} {amount:g}{marker}")
    return "\n".join(lines)


def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0


def register_analytics_tools(mcp: FastMCP) -> None:
    @mcp.tool(
        annotations={"readOnlyHint": True},
        description=(
            "Show the top 10 TV, movie, music, or user stats by plays or duration over a "
            "clamped number of days."
        ),
    )
    async def top_content(
        ctx: Context, category: str = "movies", stat_type: str = "plays", days: int = 30
    ) -> str:
        if category not in TOP_CATEGORIES:
            return enum_error("category", category, TOP_CATEGORIES)
        if stat_type not in STAT_TYPES:
            return enum_error("stat_type", stat_type, STAT_TYPES)
        data = await client_from_context(ctx).call(
            "get_home_stats",
            stat_id=category,
            stats_type=stat_type,
            time_range=clamp(days, 1, 365),
        )
        return compact_lines(
            f"Top {category} by {stat_type}",
            rows(data)[:10],
            ("title", "friendly_name", "user", "total_plays", "plays", "duration"),
        )

    @mcp.tool(
        annotations={"readOnlyHint": True},
        description=(
            "Show Tautulli user playback totals, watch time, and last-seen time. "
            "Optionally filter by username."
        ),
    )
    async def user_stats(ctx: Context, user: str | None = None) -> str:
        data = await client_from_context(ctx).call(
            "get_users_table", **({"user": user} if user else {})
        )
        users = [
            entry
            for entry in rows(data)
            if _number(entry.get("plays") or entry.get("total_plays")) > 0
        ]
        return compact_lines(
            "User stats",
            users,
            ("friendly_name", "username", "plays", "total_plays", "total_time", "last_seen"),
        )

    @mcp.tool(
        annotations={"readOnlyHint": True},
        description=(
            "Visualize playback by date, weekday, or hour as a text bar chart and mark the peak."
        ),
    )
    async def viewing_patterns(ctx: Context, group_by: str = "date", days: int = 30) -> str:
        if group_by not in PATTERNS:
            return enum_error("group_by", group_by, PATTERNS)
        duration = clamp(days, 1, 365)
        commands = {
            "date": ("get_plays_by_date", {"time_range": duration}),
            "weekday": ("get_plays_by_dayofweek", {}),
            "hour": ("get_plays_by_hourofday", {}),
        }
        command, params = commands[group_by]
        data = await client_from_context(ctx).call(command, **params)
        return _chart(f"Viewing patterns by {group_by}", data)

    @mcp.tool(
        annotations={"readOnlyHint": True},
        description=(
            "Inspect transcoding, platform and resolution trends, or detailed stream data for "
            "one history row_id/session_key."
        ),
    )
    async def stream_quality(
        ctx: Context,
        view: str = "transcode",
        days: int = 30,
        row_id: str | None = None,
        session_key: str | None = None,
    ) -> str:
        if view not in QUALITY_VIEWS:
            return enum_error("view", view, QUALITY_VIEWS)
        client = client_from_context(ctx)
        duration = clamp(days, 1, 365)
        if view == "stream":
            if not row_id and not session_key:
                return "view=stream requires row_id or session_key."
            params = {"row_id": row_id} if row_id else {"session_key": session_key}
            data = await client.call("get_stream_data", **params)
            return compact_lines(
                "Stream details",
                rows(data),
                (
                    "row_id",
                    "session_key",
                    "platform",
                    "video_codec",
                    "audio_codec",
                    "stream_video_decision",
                    "transcode_decision",
                    "transcode_decision_string",
                    "bitrate",
                ),
            )
        if view == "transcode":
            results = await asyncio.gather(
                client.call("get_plays_by_source_resolution", time_range=duration),
                client.call("get_plays_by_stream_resolution", time_range=duration),
            )
            return (
                _chart("Plays by source resolution", results[0])
                + "\n\n"
                + _chart("Plays by stream resolution", results[1])
            )
        if view == "platforms":
            data = await client.call("get_stream_type_by_top_10_platforms", time_range=duration)
            return compact_lines(
                "Stream type by platform",
                rows(data),
                ("platform", "direct_play", "direct_stream", "transcode"),
            )
        data = await client.call("get_plays_by_stream_resolution", time_range=duration)
        return compact_lines(
            "Plays by stream resolution",
            rows(data),
            ("stream_resolution", "resolution", "plays", "count"),
        )
