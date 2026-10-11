"""Compact human-readable formatting helpers for Tautulli data."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


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


def text(value: Any, default: str = "unknown") -> str:
    if value is None or value == "":
        return default
    if isinstance(value, (list, tuple, set)):
        return ", ".join(map(str, value)) or default
    return str(value).replace("\n", " ").strip()


def compact_lines(title: str, records: Iterable[Mapping[str, Any]], fields: Iterable[str]) -> str:
    lines = [title]
    fields = tuple(fields)
    for record in records:
        values = [
            f"{field}={text(record.get(field))}"
            for field in fields
            if record.get(field) is not None
        ]
        lines.append(" • " + " | ".join(values) if values else " • (no details)")
    return "\n".join(lines)


def clamp(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, int(value)))


def enum_error(name: str, value: str, options: Iterable[str]) -> str:
    return f"Invalid {name} '{value}'. Choose from: {', '.join(options)}."
