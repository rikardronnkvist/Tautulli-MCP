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


def format_date(value: Any) -> str:
    """Render calendar dates without timezone shifts; date epochs use UTC."""
    if not text(value):
        return ""
    try:
        try:
            date = datetime.fromtimestamp(float(value), tz=UTC)
        except (TypeError, ValueError):
            date = datetime.fromisoformat(text(value).replace("Z", "+00:00"))
        return date.strftime("%Y-%m-%d")
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


def format_title(record: Mapping[str, Any]) -> str:
    """Render a consistent movie or episode title across tool payloads."""
    title = text(record.get("title")) or text(record.get("full_title"))
    kind = text(record.get("media_type") or record.get("type")).lower()
    if kind == "episode":
        show = text(record.get("grandparent_title"))
        if not show:
            for candidate in (record.get("full_title"), title, record.get("last_played")):
                combined = text(candidate)
                for separator in (" — ", " - "):
                    if separator in combined:
                        show, fallback = combined.split(separator, 1)
                        if not title or title == combined:
                            title = fallback.strip()
                        break
                if show:
                    break
        if show:
            for separator in (" — ", " - "):
                prefix = f"{show}{separator}"
                if title.startswith(prefix):
                    title = title[len(prefix) :].strip()
                    break
        season = record.get("parent_media_index")
        episode = record.get("media_index")
        index = ""
        if text(season) and text(episode):
            index = f"S{int(number(season)):02}E{int(number(episode)):02}"
        detail = " ".join(part for part in (index, f'"{title}"' if title else "") if part)
        return " — ".join(part for part in (show, detail) if part)
    if kind == "movie" and title and text(record.get("year")):
        if title.endswith(f" ({text(record['year'])})"):
            return title
        return f"{title} ({text(record['year'])})"
    return text(record.get("full_title")) or title


media_title = format_title


def _decision(value: Any) -> str:
    value = text(value).lower().replace("_", " ").replace("-", " ")
    value = " ".join(value.split())
    aliases = {
        "copy": "direct stream",
        "directplay": "direct play",
        "directstream": "direct stream",
    }
    return aliases.get(value, value)


def playback_decision(record: Mapping[str, Any]) -> str:
    decisions = [
        _decision(record.get(key))
        for key in (
            "transcode_decision",
            "stream_video_decision",
            "video_decision",
            "stream_audio_decision",
            "audio_decision",
        )
    ]
    for mode in ("transcode", "direct stream", "direct play"):
        if mode in decisions:
            return mode
    return ""


def _stream_record(record: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(record)
    for key in ("session_info", "media_info"):
        nested = record.get(key)
        if isinstance(nested, list):
            nested = next((item for item in nested if isinstance(item, Mapping)), None)
        if isinstance(nested, Mapping):
            merged.update(nested)
    return merged


def _first(record: Mapping[str, Any], *keys: str) -> str:
    return next(
        (
            value
            for key in keys
            if isinstance(record.get(key), (str, int, float, bool)) and (value := text(record[key]))
        ),
        "",
    )


def _stream_changes(record: Mapping[str, Any], kind: str) -> list[str]:
    changes = []
    source = _first(record, f"{kind}_codec", f"source_{kind}_codec")
    delivered = _first(record, f"stream_{kind}_codec")
    if source and delivered and source.lower() != delivered.lower():
        codecs = {"dca": "DTS", "dts": "DTS", "aac": "AAC"} if kind == "audio" else {}
        changes.append(
            " → ".join(codecs.get(value.lower(), value) for value in (source, delivered))
        )
    if kind == "video":
        source = _first(record, "video_full_resolution", "video_resolution", "source_resolution")
        delivered = _first(record, "stream_video_full_resolution", "stream_video_resolution")
        if source and delivered and source.lower() != delivered.lower():
            changes.append(f"{source} → {delivered}")
    return changes


def transcode_reason(record: Mapping[str, Any]) -> str:
    """Use only known reason fields or observed stream changes, never dump the payload."""
    record = _stream_record(record)
    reasons = []
    keys = ["transcode_reason", "transcode_decision_string"]
    for kind in ("video", "audio", "subtitle"):
        keys.extend(
            (
                f"{kind}_reason",
                f"stream_{kind}_reason",
                f"{kind}_transcode_reason",
                f"{kind}_decision_reason",
                f"stream_{kind}_decision_reason",
            )
        )
    for key in keys:
        raw = record.get(key)
        if isinstance(raw, str) and (value := text(raw)) and value not in reasons:
            reasons.append(value)
    if reasons:
        return "; ".join(reasons)
    for kind in ("video", "audio"):
        reasons.extend(f"{kind}: {change}" for change in _stream_changes(record, kind))
    subtitle = _decision(_first(record, "stream_subtitle_decision", "subtitle_decision"))
    subtitle_changes = _stream_changes(record, "subtitle")
    if subtitle in {"burn", "transcode"}:
        codec = _first(record, "subtitle_codec", "source_subtitle_codec", "stream_subtitle_codec")
        detail = ", ".join(subtitle_changes) or codec
        reasons.append(f"subtitles: {subtitle}" + (f" ({detail})" if detail else ""))
    elif subtitle_changes:
        reasons.extend(f"subtitles: {change}" for change in subtitle_changes)
    return "; ".join(reasons)


def stream_details(record: Mapping[str, Any]) -> str:
    """Describe an allow-listed stream payload without exposing file paths or IPs."""
    record = _stream_record(record)

    def first(*keys: str) -> str:
        return _first(record, *keys)

    def hardware(*keys: str) -> str:
        value = first(*keys)
        if value.lower() in {"1", "1.0", "true", "yes"}:
            return "yes"
        if value.lower() in {"0", "0.0", "false", "no"}:
            return "no"
        return value

    def decision(kind: str) -> str:
        value = _decision(first(f"stream_{kind}_decision", f"{kind}_decision"))
        changes = _stream_changes(record, kind)
        if not value and changes:
            value = "transcode"
        if kind == "subtitle":
            codec = ", ".join(changes) or first(
                "subtitle_codec", "source_subtitle_codec", "stream_subtitle_codec"
            )
            if codec:
                value = f"{value} ({codec})" if value else codec
        elif value and changes:
            value += f" ({', '.join(changes)})"
        return value

    def media(delivered: bool) -> str:
        prefix = "stream_" if delivered else ""
        parts = []
        if not delivered and (container := first("container", "source_container")):
            parts.append(container)
        for keys in (
            (prefix + "video_codec",) if delivered else ("video_codec", "source_video_codec"),
            (prefix + "video_full_resolution", prefix + "video_resolution")
            if delivered
            else ("video_full_resolution", "video_resolution", "source_resolution"),
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
    heading = format_title(record)
    ids = identifiers(record)
    if heading or ids:
        lines.append(" ".join(part for part in (heading, ids) if part))
    for label, value in (
        ("Quality", first("quality_profile")),
        ("Source", media(False)),
        ("Delivered", media(True)),
        ("Video", decision("video")),
        ("Audio", decision("audio")),
        ("Subtitles", decision("subtitle")),
        ("HW decode", hardware("transcode_hw_decoding", "transcode_hw_decode")),
        ("HW encode", hardware("transcode_hw_encoding", "transcode_hw_encode")),
        ("Transcode reason", transcode_reason(record)),
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
