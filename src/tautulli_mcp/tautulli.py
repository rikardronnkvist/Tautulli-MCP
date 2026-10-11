"""Small asynchronous client for an explicit allow-list of Tautulli commands."""

from __future__ import annotations

from typing import Any

import httpx

ALLOWED_COMMANDS = frozenset(
    {
        "get_activity",
        "get_history",
        "get_stream_data",
        "search",
        "get_metadata",
        "get_item_watch_time_stats",
        "get_item_user_stats",
        "get_recently_added",
        "get_libraries_table",
        "get_library_media_info",
        "get_home_stats",
        "get_users_table",
        "get_plays_by_date",
        "get_plays_by_stream_type",
        "get_plays_by_dayofweek",
        "get_plays_by_hourofday",
        "get_stream_type_by_top_10_platforms",
        "get_plays_by_source_resolution",
        "get_plays_by_stream_resolution",
        "get_server_info",
    }
)


class TautulliClient:
    def __init__(self, base_url: str, api_key: str, client: httpx.AsyncClient):
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._client = client

    async def call(self, command: str, **params: Any) -> Any:
        if command not in ALLOWED_COMMANDS:
            raise ValueError(f"Tautulli command is not allowed: {command}")
        try:
            response = await self._client.get(
                f"{self._base_url}/api/v2",
                params={**params, "apikey": self._api_key, "cmd": command},
            )
            response.raise_for_status()
            payload = response.json()
            result = payload.get("response", {})
            if result.get("result") != "success":
                reason = str(result.get("message") or "unknown API error")
                reason = reason.replace(self._api_key, "[redacted]").replace(
                    self._base_url, "[redacted]"
                )
                raise RuntimeError(f"Tautulli {command} failed: {reason}")
            return result.get("data")
        except RuntimeError:
            raise
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            reason = (
                "invalid response"
                if isinstance(exc, (ValueError, TypeError))
                else type(exc).__name__
            )
            raise RuntimeError(f"Tautulli {command} failed: {reason}") from None
