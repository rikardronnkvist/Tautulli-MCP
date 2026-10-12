"""Playback, history, search, metadata, and recently-added read-only tools."""

import asyncio
import re
from typing import Annotated, Any, Literal

from fastmcp import Context, FastMCP
from pydantic import Field

from tautulli_mcp.formatting import (
    clamp,
    enum_error,
    format_date,
    format_duration,
    format_size,
    format_timestamp,
    format_title,
    identifiers,
    number,
    playback_decision,
    rows,
    stream_details,
    text,
    transcode_reason,
)
from tautulli_mcp.tools.common import client_from_context

MEDIA_TYPES = ("movie", "show", "artist")
HISTORY_TYPES = ("movie", "episode", "track", "live")


def _first(record: dict[str, Any], *keys: str) -> Any:
    return next((record[key] for key in keys if text(record.get(key))), None)


def _field(label: str, value: Any) -> str:
    rendered = text(value)
    return f"{label}: {rendered}" if rendered else ""


def _line(*parts: str) -> str:
    return " | ".join(part for part in parts if part)


def _stream_record(record: dict[str, Any]) -> dict[str, Any]:
    merged = dict(record)
    for key in ("session_info", "media_info"):
        nested = record.get(key)
        if isinstance(nested, dict):
            merged.update(nested)
        elif isinstance(nested, list) and nested and isinstance(nested[0], dict):
            merged.update(nested[0])
    return merged


def _technical_details(record: dict[str, Any]) -> str:
    # Session headings and chaining IDs are already shown once on the surrounding line.
    excluded = {
        "title",
        "full_title",
        "grandparent_title",
        "media_type",
        "type",
        "year",
        "row_id",
        "rating_key",
        "section_id",
        "session_key",
        "session_info",
        "media_info",
    }
    technical = {key: value for key, value in _stream_record(record).items() if key not in excluded}
    return _line(*stream_details(technical).splitlines())


def _duration(value: Any, *, milliseconds: bool = False) -> str:
    if not text(value):
        return ""
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return text(value)
    return format_duration(seconds / 1000 if milliseconds else seconds)


def _names(value: Any, limit: int | None = None) -> str:
    values = value if isinstance(value, (list, tuple)) else [value]
    names = [
        text(_first(item, "name", "tag", "title")) if isinstance(item, dict) else text(item)
        for item in values
    ]
    return ", ".join([name for name in names if name][:limit])


def _watch_stats(data: Any) -> list[str]:
    output = []
    labels = {1: "24h", 7: "7d", 30: "30d", 0: "all time"}
    for record in rows(data):
        if "query_days" in record:
            try:
                days = int(record["query_days"])
            except (TypeError, ValueError, OverflowError):
                continue
            if days < 0:
                continue
            label = labels.get(days, f"{days}d")
            plays = _field("Plays", _first(record, "total_plays", "plays"))
            duration = _field("Watch time", _duration(_first(record, "total_time", "duration")))
            if plays or duration:
                output.append(_line(label, plays, duration))
        else:
            for suffix, label in (("_24", "24h"), ("_7", "7d"), ("_30", "30d"), ("", "all time")):
                plays = _first(record, f"total_plays{suffix}", f"plays{suffix}")
                duration = _first(record, f"total_time{suffix}", f"duration{suffix}")
                if plays is not None or duration is not None:
                    output.append(
                        _line(
                            label, _field("Plays", plays), _field("Watch time", _duration(duration))
                        )
                    )
    return output


def _media_info(record: dict[str, Any]) -> list[str]:
    info = rows(record.get("media_info"))
    if not info:
        info = [record]
    output = []
    for media in info:
        # Parts can contain file sizes, but their file paths must never be displayed.
        parts = rows(media.get("parts"))
        for part in parts:
            for stream in rows(part.get("streams")):
                if text(_first(stream, "type", "stream_type")).lower() in {"1", "video"}:
                    for key, value in stream.items():
                        if not text(media.get(key)) and text(value):
                            media[key] = value
        size = _first(media, "file_size", "size")
        if size is None:
            sizes = [_first(part, "file_size", "size") for part in parts]
            if any(value is not None for value in sizes):
                size = sum(number(value) for value in sizes)
        fields = [
            _field(
                "Resolution",
                _first(
                    media,
                    "video_full_resolution",
                    "video_resolution",
                    "source_resolution",
                    "resolution",
                ),
            ),
            _field("Video", _first(media, "video_codec", "source_video_codec")),
            _field("Audio", _first(media, "audio_codec", "source_audio_codec")),
            _field("Channels", _first(media, "audio_channels", "source_audio_channels")),
            _field("Container", media.get("container")),
            _field("Size", format_size(number(size)) if size is not None else ""),
            _field("Dynamic range", _first(media, "video_dynamic_range", "dynamic_range")),
            _field("HDR", _first(media, "video_hdr", "hdr")),
        ]
        dv = _first(media, "video_dovi_present", "dovi_present", "dolby_vision")
        if text(dv) and text(dv).lower() not in {"0", "false", "no"}:
            fields.append("Dolby Vision")
        rendered = _line(*fields)
        if rendered:
            output.append(rendered)
    return output


def _public_ids(record: dict[str, Any]) -> str:
    guids = record.get("guids") or []
    if not isinstance(guids, list):
        guids = [guids]
    if record.get("guid"):
        guids = [*guids, record["guid"]]
    found = {
        provider: text(record.get(f"{provider}_id"))
        for provider in ("imdb", "tmdb", "tvdb")
        if text(record.get(f"{provider}_id"))
    }
    for guid in guids:
        value = text(guid.get("id")) if isinstance(guid, dict) else text(guid)
        match = re.search(r"(?:^|[./])(imdb|tmdb|tvdb)://([A-Za-z0-9]+)", value)
        if match:
            found[match[1]] = match[2]
    return _line(*(f"{provider.upper()}: {value}" for provider, value in found.items()))


async def _resolve_user_filter(client: Any, user: str) -> dict[str, Any]:
    """Map a display name, username, email or numeric ID to Tautulli history params.

    Tautulli's ``user`` parameter only matches the Plex username column, so names as shown
    by other tools (friendly names) never match. Resolve to ``user_id`` whenever possible.
    """
    value = user.strip()
    if value.isdigit():
        return {"user_id": value}
    try:
        data = await client.call("get_users_table", length=1000)
    except RuntimeError:
        data = None
    needle = value.casefold()
    matches = {
        text(entry.get("user_id"))
        for entry in rows(data)
        if text(entry.get("user_id"))
        and any(
            text(entry.get(key)).casefold() == needle
            for key in ("friendly_name", "username", "email")
        )
    }
    if len(matches) == 1:
        return {"user_id": matches.pop()}
    return {"user": value}


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
        active = rows(data.get("sessions", []) if isinstance(data, dict) else data)
        if not active:
            return "Now playing: no active streams."
        lines = ["Now playing"]
        for session in active:
            user = _first(session, "friendly_name", "user", "username")
            player = _first(session, "player", "product")
            progress = _first(session, "progress_percent", "percent_complete")
            offset = session.get("view_offset")
            duration = session.get("duration")
            if progress is None and text(offset) and number(duration) > 0:
                progress = round(number(offset) / number(duration) * 100, 1)
            position = _line(
                f"{text(progress)}%" if progress is not None else "",
                (
                    f"{_duration(offset, milliseconds=True)} / "
                    f"{_duration(duration, milliseconds=True)}"
                    if text(offset) and text(duration)
                    else ""
                ),
            )
            lines.append(
                " • "
                + _line(
                    format_title(session),
                    _field("User", user),
                    _field("State", session.get("state")),
                    _field("Progress", position),
                    _field("Player", player),
                    _field("Platform", session.get("platform")),
                    _field("Quality", session.get("quality_profile")),
                    _field("Mode", playback_decision(session)),
                    _field(
                        "Transcode reason",
                        transcode_reason(session),
                    ),
                    identifiers(session),
                )
            )
        if isinstance(data, dict):
            bandwidth = [
                _field(label, f"{text(data[key])} kbps")
                for key, label in (
                    ("lan_bandwidth", "LAN"),
                    ("wan_bandwidth", "WAN"),
                    ("total_bandwidth", "Total"),
                )
                if text(data.get(key))
            ]
            if bandwidth:
                lines.append("Bandwidth: " + _line(*bandwidth))
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
        length: Annotated[
            int, Field(description="Number of history entries (1–50).", ge=1, le=50)
        ] = 10,
        user: Annotated[str | None, Field(description="Filter by user name or user ID.")] = None,
        media_type: Annotated[
            Literal["movie", "episode", "track", "live"] | None,
            Field(description="Optional media type filter: movie, episode, track, or live."),
        ] = None,
        search: Annotated[str | None, Field(description="Optional title search filter.")] = None,
        start_date: Annotated[
            str | None,
            Field(description="Only history on or after this date (YYYY-MM-DD)."),
        ] = None,
        end_date: Annotated[
            str | None,
            Field(description="Only history on or before this date (YYYY-MM-DD)."),
        ] = None,
        include_stream_details: Annotated[
            bool, Field(description="Fetch stream details in parallel for at most 10 entries.")
        ] = False,
        include_ip: Annotated[
            bool, Field(description="Include client IP addresses; omitted by default for privacy.")
        ] = False,
    ) -> str:
        if media_type is not None and media_type not in HISTORY_TYPES:
            return enum_error("media_type", media_type, HISTORY_TYPES)
        params: dict[str, Any] = {"length": clamp(length, 1, 50)}
        # Tautulli's start_date means "exactly this day"; after/before are inclusive bounds.
        for key, value in (
            ("media_type", media_type),
            ("search", search),
            ("after", start_date),
            ("before", end_date),
        ):
            if text(value):
                params[key] = value
        client = client_from_context(ctx)
        if text(user):
            params.update(await _resolve_user_filter(client, user))
        data = await client.call("get_history", **params)
        entries = rows(data)
        if include_stream_details:

            async def enrich(entry: dict[str, Any]) -> None:
                row_id = entry.get("row_id")
                if text(row_id):
                    try:
                        entry["_stream"] = await client.call("get_stream_data", row_id=row_id)
                    except RuntimeError:
                        pass

            await asyncio.gather(*(enrich(entry) for entry in entries[:10]))
        lines = ["History"]
        for entry in entries:
            stream = rows(entry.get("_stream"))
            detail = _stream_record(stream[0]) if stream else {}
            progress = _first(entry, "percent_complete", "progress_percent")
            complete = ""
            if progress is not None:
                complete = f"{text(progress)}% complete"
            elif text(entry.get("watched_status")) in {"1", "true", "True"}:
                complete = "Completed"
            lines.append(
                " • "
                + _line(
                    format_title(entry),
                    _field("User", _first(entry, "friendly_name", "user", "username")),
                    _field("Date", format_timestamp(_first(entry, "date", "started"))),
                    _field("Duration", _duration(_first(entry, "duration", "play_duration"))),
                    _field("Player", _first(entry, "player", "product")),
                    _field("Platform", entry.get("platform")),
                    _field("Mode", playback_decision(entry)),
                    complete,
                    _technical_details(detail) if detail else "",
                    _field("IP", entry.get("ip_address")) if include_ip else "",
                    identifiers({**detail, **entry}),
                )
            )
        if not entries:
            lines.append("No history entries.")
        if isinstance(data, dict):
            footer = _line(
                _field("Total records", _first(data, "recordsTotal", "total_records")),
                _field("Matching records", _first(data, "recordsFiltered", "filtered_records")),
                _field("Total watch time", _duration(data.get("total_duration"))),
                _field("Filtered watch time", _duration(data.get("filter_duration"))),
            )
            if footer:
                lines.append(footer)
        return "\n".join(lines)

    @mcp.tool(
        annotations={"readOnlyHint": True},
        description=(
            "Search the Tautulli library by title. Returned rating keys can be "
            "passed to item_details."
        ),
    )
    async def search(
        ctx: Context,
        query: Annotated[str, Field(description="Title or title fragment to search for.")],
        limit: Annotated[
            int, Field(description="Maximum search results (1–50).", ge=1, le=50)
        ] = 20,
    ) -> str:
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
        lines = ["Search results"]
        for item in results:
            kind = text(_first(item, "media_type", "type"))
            title = format_title(item)
            lines.append(
                " • " + _line(f"{kind.title()}: {title}" if kind else title, identifiers(item))
            )
        return "\n".join(lines)

    @mcp.tool(
        annotations={"readOnlyHint": True},
        description=(
            "Get metadata and watch statistics for a rating key from search or recently_added. "
            "File paths are never included."
        ),
    )
    async def item_details(
        ctx: Context,
        rating_key: Annotated[
            int,
            Field(description="Plex rating key from another tool; numeric strings are accepted."),
        ],
    ) -> str:
        try:
            key = int(rating_key)
        except (TypeError, ValueError):
            return "rating_key must be an integer."
        client = client_from_context(ctx)
        metadata, watch_stats, user_stats = await asyncio.gather(
            client.call("get_metadata", rating_key=key),
            client.call("get_item_watch_time_stats", rating_key=key),
            client.call("get_item_user_stats", rating_key=key),
        )
        if isinstance(metadata, dict) and isinstance(metadata.get("metadata"), (dict, list)):
            metadata = metadata["metadata"]
        items = rows(metadata)
        record = items[0] if items else {}
        lines = [
            _line("Item details", format_title(record), identifiers({**record, "rating_key": key}))
        ]
        fields = [
            _field("Type", _first(record, "media_type", "type")),
            _field("Library", _first(record, "library_name", "section_name")),
            _field("Originally available", format_date(record.get("originally_available_at"))),
            _field("Duration", _duration(record.get("duration"), milliseconds=True)),
            _field("Rating", record.get("rating")),
            _field("Content rating", record.get("content_rating")),
            _field("Genres", _names(record.get("genres"))),
            _field("Directors", _names(record.get("directors"))),
            _field("Actors", _names(_first(record, "actors", "cast"), 6)),
            _field("Writers", _names(record.get("writers"), 5)),
            _field("Summary", record.get("summary")),
            _public_ids(record),
        ]
        lines.extend(field for field in fields if field)
        lines.extend(_media_info(record))
        stats = _watch_stats(watch_stats)
        if stats:
            lines.append("Watch statistics")
            lines.extend(" • " + stat for stat in stats)
        users = sorted(
            rows(user_stats),
            key=lambda user: number(_first(user, "total_plays", "plays")),
            reverse=True,
        )
        user_lines = [
            _line(
                text(_first(user, "friendly_name", "user", "username")),
                _field("Plays", _first(user, "total_plays", "plays")),
                _field("Watch time", _duration(_first(user, "total_time", "duration"))),
            )
            for user in users[:15]
        ]
        if any(user_lines):
            lines.append("Watched by (top 15 by plays)")
            lines.extend(" • " + line for line in user_lines if line)
        if len(lines) == 1 and not record:
            lines.append("No metadata available.")
        return "\n".join(lines)

    @mcp.tool(
        annotations={"readOnlyHint": True},
        description=(
            "List the newest library additions. Each rating key can be passed to item_details."
        ),
    )
    async def recently_added(
        ctx: Context,
        count: Annotated[
            int, Field(description="Number of recent additions (1–50).", ge=1, le=50)
        ] = 10,
        media_type: Annotated[
            Literal["movie", "show", "artist"] | None,
            Field(description="Optional movie, show, or artist filter; omit to include all types."),
        ] = None,
    ) -> str:
        if media_type is not None and media_type not in MEDIA_TYPES:
            return enum_error("media_type", media_type, MEDIA_TYPES)
        params: dict[str, Any] = {"count": clamp(count, 1, 50)}
        if media_type is not None:
            params["media_type"] = media_type
        data = await client_from_context(ctx).call("get_recently_added", **params)
        entries = rows(data)
        if not entries:
            return "Recently added: no items."
        lines = ["Recently added"]
        for item in entries:
            lines.append(
                " • "
                + _line(
                    format_title(item),
                    _field("Type", _first(item, "media_type", "type")),
                    _field("Library", _first(item, "library_name", "section_name")),
                    _field("Added", format_timestamp(item.get("added_at"))),
                    identifiers(item),
                )
            )
        return "\n".join(lines)
