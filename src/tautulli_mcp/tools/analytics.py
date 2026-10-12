"""Top content, user statistics, viewing patterns, and stream quality tools."""

import asyncio
from typing import Annotated, Any, Literal

from fastmcp import Context, FastMCP
from pydantic import Field

from tautulli_mcp.formatting import (
    clamp,
    enum_error,
    format_duration,
    format_timestamp,
    identifiers,
    media_title,
    number,
    rows,
    stream_details,
    text,
)
from tautulli_mcp.tools.common import client_from_context

TOP_CATEGORIES = ("tv", "movies", "music", "users")
STAT_TYPES = ("plays", "duration")
PATTERNS = ("date", "weekday", "hour")
QUALITY_VIEWS = ("transcode", "platforms", "resolution", "stream")
HOME_STATS_IDS = {
    "tv": "top_tv",
    "movies": "top_movies",
    "music": "top_music",
    "users": "top_users",
}


def _home_rows(data: Any, stat_id: str) -> list[dict[str, Any]]:
    entries = rows(data)
    if any("stat_id" in entry for entry in entries):
        return [
            row
            for entry in entries
            if entry.get("stat_id") == stat_id
            for row in rows(entry.get("rows", []))
        ]
    return entries


def _series_key(name: Any) -> str:
    return text(name).strip().lower().replace(" ", "_").replace("-", "_")


def _is_graph(data: Any) -> bool:
    return (
        isinstance(data, dict)
        and isinstance(data.get("categories"), list)
        and isinstance(data.get("series"), list)
    )


def _graph_rows(data: Any) -> list[dict[str, Any]]:
    if not _is_graph(data):
        return rows(data)
    series = [
        (_series_key(item.get("name")), item.get("data"))
        for item in data["series"]
        if isinstance(item, dict) and isinstance(item.get("data"), list)
    ]
    return [
        {
            "label": category,
            **{name: values[index] for name, values in series if index < len(values)},
        }
        for index, category in enumerate(data["categories"])
    ]


def _label(entry: dict[str, Any]) -> str:
    for key in (
        "label",
        "date",
        "day",
        "weekday",
        "dayofweek",
        "day_name",
        "hour",
        "hourofday",
        "platform",
        "source_resolution",
        "stream_resolution",
        "resolution",
        "stream_type",
    ):
        if entry.get(key) is not None:
            label = text(entry[key])
            if label:
                return label
    return "Unlabelled"


def _plays(entry: dict[str, Any]) -> float:
    return number(entry.get("total_plays", entry.get("plays", entry.get("count", 0))))


def _decisions(entry: dict[str, Any]) -> tuple[float, float, float]:
    return (
        number(entry.get("direct_play", entry.get("dp"))),
        number(entry.get("direct_stream", entry.get("ds"))),
        number(entry.get("transcode", entry.get("tc"))),
    )


def _amount(entry: dict[str, Any], graph: bool) -> float:
    if graph:
        return sum(number(value) for key, value in entry.items() if key != "label")
    return _plays(entry)


def _decision_counts(entry: dict[str, Any], *, full: bool = False) -> str:
    dp, ds, tc = _decisions(entry)
    if full:
        return f"direct play {dp:g} / direct stream {ds:g} / transcode {tc:g}"
    return f"DP {dp:g} / DS {ds:g} / TC {tc:g}"


def _media_counts(entry: dict[str, Any]) -> str:
    values = []
    for label, keys in (
        ("TV", ("tv", "television")),
        ("Movies", ("movies", "movie")),
        ("Music", ("music",)),
    ):
        if any(key in entry for key in keys):
            values.append(f"{label}: {sum(number(entry.get(key)) for key in keys):g}")
    return " / ".join(values)


def _watch_time(entry: dict[str, Any], fields: tuple[str, ...]) -> str:
    value = next((entry.get(key) for key in fields if text(entry.get(key))), None)
    return format_duration(value)


def _chart(title: str, data: Any, *, trim_dates: bool = False, streams: Any = None) -> str:
    entries = _graph_rows(data)
    values = [(entry, _amount(entry, _is_graph(data))) for entry in entries]
    if trim_dates:
        first = next((index for index, (_, amount) in enumerate(values) if amount > 0), len(values))
        if values and first == len(values):
            return f"{title}\nTotal: 0 plays | Average: 0.00 plays\nNo plays in this date range."
        values = values[first:]
    if not values:
        return f"{title}: no data."
    total = sum(amount for _, amount in values)
    peak_index, (_, peak) = max(enumerate(values), key=lambda item: item[1][1])
    lines = [title, f"Total: {total:g} plays | Average: {total / len(values):.2f} plays"]
    media_totals: dict[str, Any] = {}
    for entry, _ in values:
        for key in ("tv", "television", "movies", "movie", "music"):
            if key in entry:
                media_totals[key] = number(media_totals.get(key)) + number(entry[key])
    if breakdown := _media_counts(media_totals):
        lines.append(breakdown.replace(" / ", " | "))
    stream_map = {_label(entry): entry for entry in _graph_rows(streams)}
    for index, (entry, amount) in enumerate(values):
        label = _label(entry)
        bar = "█" * min(30, round(amount / max(peak, 1) * 30))
        counts = (
            _decision_counts(stream_map[label]) if label in stream_map else _media_counts(entry)
        )
        detail = f" | {counts}" if counts else ""
        marker = " ← peak" if index == peak_index and peak > 0 else ""
        display_label = (format_timestamp(label) or label) if trim_dates else label
        lines.append(f"{display_label}: {bar} {amount:g}{marker}{detail}")
    return "\n".join(lines)


def _ranked(title: str, entries: list[dict[str, Any]], category: str) -> str:
    if not entries:
        return f"{title}: no data."
    lines = [title]
    for index, entry in enumerate(entries[:10], 1):
        if category == "users":
            name = text(entry.get("friendly_name")) or text(entry.get("username")) or "User"
        elif category == "platforms":
            name = text(entry.get("platform")) or text(entry.get("title")) or "Platform"
        else:
            name = media_title(entry) or "Untitled"
            year = text(entry.get("year"))
            if category == "movies" and year and f"({year})" not in name:
                name += f" ({year})"
        duration = _watch_time(entry, ("total_duration", "duration", "total_time"))
        ids = identifiers(entry)
        lines.append(
            f"{index}. {name} — {_plays(entry):g} plays"
            + (f", {duration}" if duration else "")
            + (f" {ids}" if ids else "")
        )
    return "\n".join(lines)


def _quality_section(title: str, data: Any, *, overall: bool = False) -> str:
    entries = _graph_rows(data)
    if not entries:
        return f"{title}: no data."
    lines = [title]
    totals = [0.0, 0.0, 0.0]
    for entry in entries:
        decisions = _decisions(entry)
        total = sum(decisions)
        totals = [left + right for left, right in zip(totals, decisions, strict=True)]
        percent = decisions[2] / total * 100 if total else 0
        amount = total if _is_graph(data) else _plays(entry) or total
        lines.append(
            f"{_label(entry)} — {amount:g} plays"
            f" | {_decision_counts(entry, full=True)} | TC {percent:.1f}%"
        )
    if overall:
        total = sum(totals)
        percent = totals[2] / total * 100 if total else 0
        lines.append(
            f"Overall — {total:g} plays | direct play {totals[0]:g}"
            f" / direct stream {totals[1]:g} / transcode {totals[2]:g} | TC {percent:.1f}%"
        )
    return "\n".join(lines)


def _down_transcoded(source: Any, delivered: Any) -> bool:
    if not (_is_graph(source) and _is_graph(delivered)):
        return False
    sources, streams = _graph_rows(source), _graph_rows(delivered)
    four_k = {"4k", "2160", "2160p"}
    lower = {"1080", "1080p", "720", "720p", "576", "576p", "480", "480p", "sd"}
    source_tc = sum(_decisions(entry)[2] for entry in sources)
    source_4k = sum(_decisions(entry)[2] for entry in sources if _label(entry).lower() in four_k)
    delivered_tc = sum(_decisions(entry)[2] for entry in streams)
    lower_tc = sum(_decisions(entry)[2] for entry in streams if _label(entry).lower() in lower)
    return source_4k > 0 and source_tc == delivered_tc and delivered_tc == lower_tc


def _suggests_down_transcoding(source: Any, delivered: Any) -> bool:
    if not (_is_graph(source) and _is_graph(delivered)):
        return False
    sources, streams = _graph_rows(source), _graph_rows(delivered)
    four_k = {"4k", "2160", "2160p"}
    lower = {"1080", "1080p", "720", "720p", "576", "576p", "480", "480p", "sd"}
    source_4k = [entry for entry in sources if _label(entry).lower() in four_k]
    delivered_4k = [entry for entry in streams if _label(entry).lower() in four_k]
    return (
        sum(_decisions(entry)[2] for entry in source_4k) > 0
        and sum(_amount(entry, True) for entry in source_4k)
        > sum(_amount(entry, True) for entry in delivered_4k)
        and sum(_decisions(entry)[2] for entry in streams if _label(entry).lower() in lower) > 0
        and sum(_amount(entry, True) for entry in sources)
        == sum(_amount(entry, True) for entry in streams)
    )


def _optional_id(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str):
        parsed = int(value.strip())
    else:
        raise ValueError
    if parsed < 0:
        raise ValueError
    return parsed


def register_analytics_tools(mcp: FastMCP) -> None:
    @mcp.tool(
        annotations={"readOnlyHint": True},
        description="Show the top 10 TV, movie, music, or user stats by plays or watch time.",
    )
    async def top_content(
        ctx: Context,
        category: Annotated[
            Literal["tv", "movies", "music", "users"], Field(description="Content category.")
        ] = "tv",
        stat_type: Annotated[
            Literal["plays", "duration"], Field(description="Rank by plays or watch time.")
        ] = "plays",
        days: Annotated[int, Field(description="Lookback days, clamped to 1–365.")] = 30,
    ) -> str:
        if category not in TOP_CATEGORIES:
            return enum_error("category", category, TOP_CATEGORIES)
        if stat_type not in STAT_TYPES:
            return enum_error("stat_type", stat_type, STAT_TYPES)
        stat_id = HOME_STATS_IDS[category]
        data = await client_from_context(ctx).call(
            "get_home_stats", stat_id=stat_id, stats_type=stat_type, time_range=clamp(days, 1, 365)
        )
        return _ranked(f"Top {category} by {stat_type}", _home_rows(data, stat_id), category)

    @mcp.tool(
        annotations={"readOnlyHint": True},
        description="Show active users ranked by plays with watch time and latest playback.",
    )
    async def user_stats(
        ctx: Context,
        user: Annotated[str | None, Field(description="Optional username search filter.")] = None,
    ) -> str:
        data = await client_from_context(ctx).call(
            "get_users_table",
            order_column="plays",
            order_dir="desc",
            **({"search": user} if user else {}),
        )
        users = sorted(
            (entry for entry in rows(data) if _plays(entry) > 0), key=_plays, reverse=True
        )
        lines = ["User stats"]
        for index, entry in enumerate(users, 1):
            name = (
                text(entry.get("friendly_name"))
                or text(entry.get("username"))
                or text(entry.get("title"))
                or "User"
            )
            details = [f"{index}. {name} — {_plays(entry):g} plays"]
            if duration := _watch_time(entry, ("total_time", "total_duration", "duration")):
                details.append(duration)
            last_seen = format_timestamp(entry.get("last_seen"))
            if last_seen:
                details.append(f"Last seen: {last_seen}")
            last_played = entry.get("last_played")
            latest_title = text(last_played) if isinstance(last_played, str) else ""
            if abs(number(latest_title)) >= 100_000_000:
                latest_title = ""
            latest_title = latest_title or text(entry.get("full_title"))
            latest = (
                media_title({**entry, "title": latest_title, "full_title": latest_title})
                if latest_title or text(entry.get("grandparent_title"))
                else ""
            )
            if latest:
                details.append(f"Latest: {latest}")
            ids = identifiers(entry)
            lines.append(" | ".join(details) + (f" {ids}" if ids else ""))
        return "\n".join(lines) if users else "User stats: no active users."

    @mcp.tool(
        annotations={"readOnlyHint": True},
        description="Chart plays by date, weekday or hour with totals, average and media mix.",
    )
    async def viewing_patterns(
        ctx: Context,
        group_by: Annotated[
            Literal["date", "weekday", "hour"], Field(description="Playback grouping.")
        ] = "date",
        days: Annotated[int, Field(description="Lookback days, clamped to 1–365.")] = 30,
    ) -> str:
        if group_by not in PATTERNS:
            return enum_error("group_by", group_by, PATTERNS)
        duration = clamp(days, 1, 365)
        command = {
            "date": "get_plays_by_date",
            "weekday": "get_plays_by_dayofweek",
            "hour": "get_plays_by_hourofday",
        }[group_by]
        client = client_from_context(ctx)
        data = await client.call(command, time_range=duration)
        streams = (
            await client.call("get_plays_by_stream_type", time_range=duration)
            if group_by == "date"
            else None
        )
        return _chart(
            f"Viewing patterns by {group_by}", data, trim_dates=group_by == "date", streams=streams
        )

    @mcp.tool(
        annotations={"readOnlyHint": True},
        description="Inspect transcoding, top platforms, resolutions, or a detailed stream.",
    )
    async def stream_quality(
        ctx: Context,
        view: Annotated[
            Literal["transcode", "platforms", "resolution", "stream"],
            Field(description="Quality report to display."),
        ] = "transcode",
        days: Annotated[int, Field(description="Lookback days, clamped to 1–365.")] = 30,
        row_id: Annotated[int | None, Field(description="History row ID for view=stream.")] = None,
        session_key: Annotated[
            int | None, Field(description="Active session key for view=stream.")
        ] = None,
    ) -> str:
        if view not in QUALITY_VIEWS:
            return enum_error("view", view, QUALITY_VIEWS)
        client = client_from_context(ctx)
        duration = clamp(days, 1, 365)
        if view == "stream":
            try:
                row_id, session_key = _optional_id(row_id), _optional_id(session_key)
            except (TypeError, ValueError, OverflowError):
                return "row_id and session_key must be non-negative integer IDs."
            if row_id is None and session_key is None:
                return "view=stream requires row_id or session_key."
            params = {"row_id": row_id} if row_id is not None else {"session_key": session_key}
            data = await client.call("get_stream_data", **params)
            entries = rows(data)
            if not entries:
                return "Stream details: no data."
            lines = ["Stream details"]
            for entry in entries:
                entry = {**params, **entry}
                detail = stream_details(entry)
                if detail:
                    lines.append(detail)
            return "\n".join(lines)
        if view == "transcode":
            data = await client.call("get_stream_type_by_top_10_platforms", time_range=duration)
            return _quality_section("Stream type by platform", data, overall=True)
        if view == "platforms":
            data = await client.call(
                "get_home_stats", stat_id="top_platforms", stats_type="plays", time_range=duration
            )
            return _ranked("Top platforms", _home_rows(data, "top_platforms"), "platforms")
        source, delivered = await asyncio.gather(
            client.call("get_plays_by_source_resolution", time_range=duration),
            client.call("get_plays_by_stream_resolution", time_range=duration),
        )
        result = (
            _quality_section("Source resolutions", source)
            + "\n\n"
            + _quality_section("Delivered resolutions", delivered)
        )
        if _down_transcoded(source, delivered):
            result += "\n4K sources were down-transcoded to lower delivered resolutions."
        elif _suggests_down_transcoding(source, delivered):
            result += (
                "\nFewer 4K plays were delivered than sourced; aggregate counts suggest"
                " some 4K down-transcoding, but do not establish individual stream paths."
            )
        return result
