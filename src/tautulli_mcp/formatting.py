"""Compact human-readable formatting helpers for Tautulli data."""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from math import isfinite
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def rows(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [dict(row) for row in data if isinstance(row, Mapping)]
    if isinstance(data, Mapping):
        for key in (
            "data",
            "rows",
            "records",
            "sessions",
            "history",
            "results",
            "recently_added",
            "libraries",
            "items",
            "users",
            "stats",
        ):
            if isinstance(data.get(key), list):
                return [dict(row) for row in data[key] if isinstance(row, Mapping)]
        return [dict(data)]
    return []


def text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, (list, tuple, set)):
        return ", ".join(part for item in value if (part := text(item))) or default
    result = str(value).replace("\n", " ").strip()
    return default if not result or result.lower() == "unknown" else result


def number(value: Any) -> float:
    try:
        result = float(value or 0)
        return result if isfinite(result) else 0
    except (TypeError, ValueError, OverflowError):
        return 0


def format_timestamp(value: Any) -> str:
    """Render an epoch or ISO date in the configured container timezone."""
    if not text(value):
        return ""
    try:
        local = ZoneInfo(os.environ.get("TZ") or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        local = UTC
    try:
        try:
            instant = datetime.fromtimestamp(float(value), tz=UTC)
        except (TypeError, ValueError):
            instant = datetime.fromisoformat(text(value).replace("Z", "+00:00"))
            if instant.tzinfo is None:
                instant = instant.replace(tzinfo=local)
        return instant.astimezone(local).strftime("%Y-%m-%d %H:%M")
    except (ValueError, OverflowError, OSError):
        return ""


def format_duration(seconds: Any) -> str:
    if not text(seconds):
        return ""
    try:
        amount = float(seconds)
    except (TypeError, ValueError):
        return text(seconds)
    if not isfinite(amount):
        return ""
    remaining = max(0, int(amount))
    if remaining < 60:
        return f"{remaining}s"
    days, remaining = divmod(remaining, 86400)
    hours, remaining = divmod(remaining, 3600)
    minutes = remaining // 60
    return " ".join(
        f"{amount}{unit}" for amount, unit in ((days, "d"), (hours, "h"), (minutes, "m")) if amount
    )


def format_size(value: Any) -> str:
    if not text(value):
        return ""
    try:
        float(value)
    except (TypeError, ValueError):
        return text(value) if re.fullmatch(r"\d+(?:\.\d+)?\s*[KMGT]?i?B", text(value)) else ""
    size = max(0, number(value))
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:g} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return ""


def identifiers(record: Mapping[str, Any]) -> str:
    return " ".join(
        f"[{key}: {value}]"
        for key in ("row_id", "rating_key", "section_id", "session_key")
        if (value := text(record.get(key)))
    )


def media_title(record: Mapping[str, Any]) -> str:
    title = text(record.get("title") or record.get("full_title"))
    kind = record.get("media_type") or record.get("type")
    if kind == "episode":
        show = text(record.get("grandparent_title"))
        season = record.get("parent_media_index")
        episode = record.get("media_index")
        index = ""
        if text(season) and text(episode):
            index = f"S{int(number(season)):02}E{int(number(episode)):02}"
        detail = " ".join(part for part in (index, f'"{title}"' if title else "") if part)
        return " — ".join(part for part in (show, detail) if part)
    if kind == "movie" and text(record.get("year")):
        return f"{title} ({text(record['year'])})"
    return text(record.get("full_title")) or title


def playback_decision(record: Mapping[str, Any]) -> str:
    decisions = [
        text(record.get(key)).lower().replace("_", " ")
        for key in (
            "transcode_decision",
            "stream_video_decision",
            "video_decision",
            "stream_audio_decision",
            "audio_decision",
        )
    ]
    for mode in ("transcode", "direct stream", "copy", "direct play"):
        if mode in decisions:
            return "direct stream" if mode == "copy" else mode
    return ""


def stream_details(record: Mapping[str, Any]) -> str:
    """Describe an allow-listed stream payload without exposing file paths or IPs."""
    merged = dict(record)
    for key in ("session_info", "media_info"):
        nested = record.get(key)
        if isinstance(nested, Mapping):
            merged.update(nested)
    record = merged

    def first(*keys: str) -> str:
        return next((value for key in keys if (value := text(record.get(key)))), "")

    def media(delivered: bool) -> str:
        prefix = "stream_" if delivered else ""
        parts = []
        if not delivered and (container := first("container", "source_container")):
            parts.append(container)
        for keys in (
            (prefix + "video_codec",) if delivered else ("video_codec", "source_video_codec"),
            (prefix + "video_resolution",)
            if delivered
            else ("video_resolution", "source_resolution"),
        ):
            if value := first(*keys):
                parts.append(value)
        if bitrate := first(prefix + "bitrate", prefix + "video_bitrate"):
            parts.append(f"{bitrate} kbps")
        audio = first(prefix + "audio_codec")
        channels = first(prefix + "audio_channel_layout", prefix + "audio_channels")
        if audio or channels:
            parts.append(
                " ".join(part for part in (audio, f"{channels} ch" if channels else "") if part)
            )
        return ", ".join(parts)

    lines = []
    heading = media_title(record)
    ids = identifiers(record)
    if heading or ids:
        lines.append(" ".join(part for part in (heading, ids) if part))
    for label, value in (
        ("Quality", first("quality_profile")),
        ("Source", media(False)),
        ("Delivered", media(True)),
        ("Video", first("stream_video_decision", "video_decision")),
        ("Audio", first("stream_audio_decision", "audio_decision")),
        ("Subtitles", first("stream_subtitle_decision", "subtitle_decision")),
        ("HW decode", first("transcode_hw_decoding", "transcode_hw_decode")),
        ("HW encode", first("transcode_hw_encoding", "transcode_hw_encode")),
        ("Transcode reason", first("transcode_reason", "transcode_decision_string")),
    ):
        if value:
            lines.append(f"{label}: {value}")
    return "\n".join(lines)


def compact_lines(title: str, records: Iterable[Mapping[str, Any]], fields: Iterable[str]) -> str:
    lines = [title]
    fields = tuple(fields)
    for record in records:
        values = []
        for field in fields:
            value = record.get(field)
            if field in {"date", "added_at", "last_seen", "started", "stopped", "last_played"}:
                rendered = format_timestamp(value)
            elif field in {"duration", "total_time"}:
                rendered = format_duration(value)
            else:
                rendered = text(value)
            if rendered and field not in {"row_id", "rating_key", "section_id", "session_key"}:
                values.append(f"{field.replace('_', ' ')}: {rendered}")
        detail = " — ".join(values)
        ids = identifiers(record)
        if detail or ids:
            lines.append(" • " + " ".join(part for part in (detail, ids) if part))
    return "\n".join(lines)


def clamp(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, int(value)))


def enum_error(name: str, value: str, options: Iterable[str]) -> str:
    return f"Invalid {name} '{value}'. Choose from: {', '.join(options)}."
