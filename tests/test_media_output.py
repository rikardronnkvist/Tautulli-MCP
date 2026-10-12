"""Fixture-driven coverage of readable, privacy-preserving media output."""

import asyncio
import copy
import json
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import respx
from fastmcp import FastMCP

from tautulli_mcp.tautulli import TautulliClient
from tautulli_mcp.tools.media import register_media_tools

FIXTURE = Path(__file__).parent / "fixtures" / "media_output.json"


@pytest.fixture
def media_data() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@asynccontextmanager
async def media_tools(data: dict[str, Any], responder=None):
    requests = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"response": {"result": "success", "data": data.get(request.url.params["cmd"])}},
        )

    mcp = FastMCP("media-output-test")
    register_media_tools(mcp)
    async with httpx.AsyncClient() as http:
        with respx.mock(base_url="http://tautulli.test", assert_all_called=False) as router:
            router.get("/api/v2").mock(side_effect=responder or respond)
            ctx = SimpleNamespace(
                lifespan_context={
                    "tautulli": TautulliClient("http://tautulli.test", "fixture-key", http)
                }
            )
            yield mcp, ctx, requests


def assert_private_fields_absent(result: str) -> None:
    for forbidden in (
        "/private",
        "192.0.2.",
        "198.51.100.",
        "None",
        "unknown",
        "ip_address",
        "file_size=",
        "query_days=",
    ):
        assert forbidden not in result


@pytest.mark.asyncio
async def test_now_playing_two_sessions(media_data) -> None:
    async with media_tools(media_data) as (mcp, ctx, requests):
        result = await (await mcp.get_tool("now_playing")).fn(ctx=ctx)
    assert 'Space Show — S01E02 "The Arrival"' in result
    assert "The Film (2025)" in result
    for value in (
        "Alice",
        "Bob",
        "Living Room",
        "Roku",
        "Browser",
        "Chrome",
        "Original",
        "4 Mbps 720p",
        "25%",
        "50",
        "Bandwidth limit",
        "[row_id: 11]",
        "[rating_key: 201]",
        "[session_key: 701]",
        "[row_id: 12]",
        "[rating_key: 202]",
        "[session_key: 702]",
        "LAN: 8000 kbps",
        "WAN: 4000 kbps",
        "Total: 12000 kbps",
        "Progress: 25% | 15m / 1h",
        "Progress: 50.0% | 1h / 2h",
    ):
        assert value in result
    assert "direct play" in result.lower()
    assert "transcode" in result.lower()
    assert len([line for line in result.splitlines() if line.startswith(" • ")]) == 2
    assert result.count("[session_key: 701]") == 1
    assert result.count("[session_key: 702]") == 1
    assert len(result.splitlines()) == 4
    assert "Source:" not in result
    assert "Delivered:" not in result
    assert "Space Show - The Arrival" not in result
    assert requests[0].url.params["cmd"] == "get_activity"
    assert_private_fields_absent(result)


@pytest.mark.asyncio
async def test_history_details_and_totals(media_data, monkeypatch) -> None:
    monkeypatch.setenv("TZ", "UTC")
    async with media_tools(media_data) as (mcp, ctx, requests):
        result = await (await mcp.get_tool("history")).fn(ctx=ctx, include_stream_details=True)
    assert requests[0].url.params["length"] == "10"
    assert sum(request.url.params["cmd"] == "get_stream_data" for request in requests) == 2
    for value in (
        'Space Show — S01E02 "The Arrival"',
        "The Film (2025)",
        "Alice",
        "Bob",
        "2024-01-01 00:00",
        "2024-01-01 01:00",
        "100% complete",
        "50% complete",
        "Living Room",
        "Roku",
        "Browser",
        "Chrome",
        "[row_id: 11]",
        "[rating_key: 201]",
        "[session_key: 701]",
        "[row_id: 12]",
        "[rating_key: 202]",
        "Total records: 120",
        "Matching records: 2",
        "Total watch time:",
        "Filtered watch time:",
        "Duration: 30m",
        "Duration: 1h",
        "Total watch time: 3h",
        "hevc",
        "h264",
        "Bandwidth limit",
        "720p",
    ):
        assert value in result
    assert "Duration: 1800" not in result
    assert "Total watch time: 10800" not in result
    assert_private_fields_absent(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("title", [None, "unknown"])
async def test_episode_full_title_fallback_does_not_repeat_show(media_data, title) -> None:
    media_data["get_activity"]["sessions"][0]["title"] = title
    media_data["get_history"]["data"][0]["title"] = title
    async with media_tools(media_data) as (mcp, ctx, _):
        for name in ("now_playing", "history"):
            result = await (await mcp.get_tool(name)).fn(ctx=ctx)
            assert 'Space Show — S01E02 "The Arrival"' in result
            assert "Space Show - The Arrival" not in result
            assert_private_fields_absent(result)


@pytest.mark.asyncio
async def test_history_filters_ip_and_limit(media_data) -> None:
    async with media_tools(media_data) as (mcp, ctx, requests):
        result = await (await mcp.get_tool("history")).fn(
            ctx=ctx,
            length=900,
            user="Alice",
            media_type="episode",
            search="Space",
            start_date="2024-01-01",
            include_ip=True,
        )
    params = requests[0].url.params
    assert params["length"] == "50"
    assert params["user"] == "Alice"
    assert params["media_type"] == "episode"
    assert params["search"] == "Space"
    assert params["start_date"] == "2024-01-01"
    assert "IP: 192.0.2.11" in result
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_history_enrichment_is_parallel_and_capped(media_data) -> None:
    media_data["get_history"]["data"] = [
        {**media_data["get_history"]["data"][0], "row_id": index} for index in range(12)
    ]
    active = maximum = 0
    calls = []

    async def respond(request):
        nonlocal active, maximum
        command = request.url.params["cmd"]
        calls.append(command)
        if command == "get_stream_data":
            active += 1
            maximum = max(maximum, active)
            await asyncio.sleep(0.01)
            active -= 1
        return httpx.Response(
            200, json={"response": {"result": "success", "data": media_data[command]}}
        )

    async with media_tools(media_data, respond) as (mcp, ctx, _):
        result = await (await mcp.get_tool("history")).fn(ctx=ctx, include_stream_details=True)
    assert calls.count("get_stream_data") == 10
    assert maximum == 10
    assert "[row_id: 0]" in result
    assert "[row_id: 11]" in result
    assert_private_fields_absent(result)


@pytest.mark.asyncio
async def test_history_nested_stream_payload_and_failed_enrichment(media_data) -> None:
    async def respond(request):
        command = request.url.params["cmd"]
        if command == "get_stream_data":
            if request.url.params["row_id"] == "12":
                return httpx.Response(
                    200, json={"response": {"result": "error", "message": "Unavailable"}}
                )
            data = {
                "session_info": {"session_key": "799"},
                "media_info": [
                    {
                        "video_codec": "hevc",
                        "video_full_resolution": "2160p",
                        "stream_video_codec": "h264",
                        "stream_video_full_resolution": "1080p",
                    }
                ],
            }
        else:
            data = copy.deepcopy(media_data[command])
            data["data"][0].pop("session_key")
        return httpx.Response(200, json={"response": {"result": "success", "data": data}})

    async with media_tools(media_data, respond) as (mcp, ctx, _):
        result = await (await mcp.get_tool("history")).fn(ctx=ctx, include_stream_details=True)
    assert "[session_key: 799]" in result
    assert result.count("[session_key: 799]") == 1
    assert "hevc" in result
    assert "h264" in result
    assert "2160p" in result
    assert "1080p" in result
    assert "The Film (2025)" in result
    assert "Unavailable" not in result
    assert_private_fields_absent(result)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("zone", "expected"),
    [("America/New_York", "2023-12-31 19:00"), ("Invalid/Timezone", "2024-01-01 00:00")],
)
async def test_local_timestamps_and_utc_fallback(media_data, monkeypatch, zone, expected) -> None:
    monkeypatch.setenv("TZ", zone)
    async with media_tools(media_data) as (mcp, ctx, _):
        history = await (await mcp.get_tool("history")).fn(ctx=ctx)
        recent = await (await mcp.get_tool("recently_added")).fn(ctx=ctx)
    assert f"Date: {expected}" in history
    assert f"Added: {expected}" in recent


@pytest.mark.asyncio
async def test_search_chaining_ids_and_titles(media_data) -> None:
    async with media_tools(media_data) as (mcp, ctx, requests):
        result = await (await mcp.get_tool("search")).fn(ctx=ctx, query="  Film  ", limit=900)
    assert requests[0].url.params["query"] == "Film"
    assert requests[0].url.params["limit"] == "50"
    for value in (
        "Movie: The Film (2025)",
        "Show: Space Show",
        'Episode: Space Show — S01E02 "The Arrival"',
        "[rating_key: 202]",
        "[rating_key: 200]",
        "[rating_key: 201]",
    ):
        assert value in result
    assert_private_fields_absent(result)


@pytest.mark.asyncio
async def test_item_metadata_media_info_and_statistics(media_data, monkeypatch) -> None:
    monkeypatch.setenv("TZ", "UTC")
    async with media_tools(media_data) as (mcp, ctx, requests):
        result = await (await mcp.get_tool("item_details")).fn(ctx=ctx, rating_key="202")
    assert {request.url.params["cmd"] for request in requests} == {
        "get_metadata",
        "get_item_watch_time_stats",
        "get_item_user_stats",
    }
    assert all(request.url.params["rating_key"] == "202" for request in requests)
    for value in (
        "The Film (2025)",
        "[rating_key: 202]",
        "Cinema",
        "2025-03-04",
        "Duration: 2h",
        "Science Fiction",
        "Adventure",
        "Director One",
        "Actor Six",
        "Writer Five",
        "Resolution: 4k",
        "Video: hevc",
        "Audio: truehd",
        "Channels: 7.1",
        "Container: mkv",
        "Size: 1.0 GiB",
        "HDR10",
        "Dolby Vision",
        "IMDB: tt1234567",
        "TMDB: 12345",
        "TVDB: 67890",
        "24h",
        "7d",
        "30d",
        "all time",
        "Plays: 12",
        "Watch time:",
    ):
        assert value in result
    assert "Actor Seven" not in result
    assert "Writer Six" not in result
    assert "Duration: 7200000" not in result
    assert result.index("Bob") < result.index("Alice")
    assert_private_fields_absent(result)


@pytest.mark.asyncio
async def test_item_user_ranking_is_numeric_and_limited(media_data) -> None:
    media_data["get_item_user_stats"] = [
        {"friendly_name": f"Viewer {index:02}", "total_plays": str(index)} for index in range(18)
    ]
    async with media_tools(media_data) as (mcp, ctx, _):
        result = await (await mcp.get_tool("item_details")).fn(ctx=ctx, rating_key=202)
    assert result.index("Viewer 17") < result.index("Viewer 09") < result.index("Viewer 03")
    assert "Viewer 02" not in result
    assert "Viewer 01" not in result
    assert "Viewer 00" not in result
    assert sum(line.startswith(" • Viewer") for line in result.splitlines()) == 15


@pytest.mark.asyncio
async def test_item_reads_hdr_dv_from_nested_video_streams_only(media_data) -> None:
    media = media_data["get_metadata"]["media_info"][0]
    media["video_full_resolution"] = "2160p"
    media["parts"][0]["streams"].insert(
        0, {"type": "2", "video_dynamic_range": "Audio-only HDR", "video_dovi_present": "1"}
    )
    async with media_tools(media_data) as (mcp, ctx, _):
        result = await (await mcp.get_tool("item_details")).fn(ctx=ctx, rating_key=202)
    assert "Resolution: 2160p" in result
    assert "Dynamic range: HDR10" in result
    assert "Dolby Vision" in result
    assert "Audio-only HDR" not in result
    assert_private_fields_absent(result)


@pytest.mark.asyncio
async def test_item_single_media_info_and_legacy_stats(media_data) -> None:
    media_data["get_metadata"]["media_info"] = {
        "video_resolution": "1080",
        "audio_codec": "aac",
        "file_size": "1024",
        "video_dynamic_range": "unknown",
        "file": "/private/single.mkv",
    }
    media_data["get_item_watch_time_stats"] = {
        "total_plays": 8,
        "total_plays_24": 1,
        "total_plays_7": 2,
        "total_plays_30": 4,
        "total_time": "3600",
    }
    async with media_tools(media_data) as (mcp, ctx, _):
        result = await (await mcp.get_tool("item_details")).fn(ctx=ctx, rating_key=202)
    assert "Resolution: 1080" in result
    assert "Audio: aac" in result
    assert "24h | Plays: 1" in result
    assert "7d | Plays: 2" in result
    assert "30d | Plays: 4" in result
    assert "all time | Plays: 8" in result
    assert_private_fields_absent(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("as_list", [False, True])
async def test_item_wrapped_metadata_and_invalid_stat_periods(media_data, as_list) -> None:
    item = media_data["get_metadata"]
    media_data["get_metadata"] = {"metadata": [item] if as_list else item}
    media_data["get_item_watch_time_stats"].extend(
        {"query_days": period, "total_plays": "99", "total_time": "1"}
        for period in (None, "unknown", "invalid", "-1")
    )
    async with media_tools(media_data) as (mcp, ctx, _):
        result = await (await mcp.get_tool("item_details")).fn(ctx=ctx, rating_key=202)
    assert "The Film (2025)" in result
    assert "Resolution: 4k" in result
    assert "Plays: 99" not in result
    assert "all time | Plays: 12" in result
    assert_private_fields_absent(result)


@pytest.mark.asyncio
async def test_recently_added_all_types_and_filter(media_data, monkeypatch) -> None:
    monkeypatch.setenv("TZ", "UTC")
    async with media_tools(media_data) as (mcp, ctx, requests):
        tool = await mcp.get_tool("recently_added")
        result = await tool.fn(ctx=ctx)
        await tool.fn(ctx=ctx, media_type="show", count=900)
    assert "media_type" not in requests[0].url.params
    assert requests[0].url.params["count"] == "10"
    assert requests[1].url.params["media_type"] == "show"
    assert requests[1].url.params["count"] == "50"
    for value in (
        "The Film (2025)",
        "Space Show",
        "The Artist",
        "[rating_key: 202]",
        "[rating_key: 200]",
        "[rating_key: 300]",
        "Added: 2024-01-01 00:00",
        "Library: Cinema",
        "Library: Television",
        "Library: Music",
    ):
        assert value in result
    artist_line = next(line for line in result.splitlines() if "The Artist" in line)
    assert "Added:" not in artist_line
    assert_private_fields_absent(result)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("name", "params"),
    [
        ("history", {"media_type": "show"}),
        ("recently_added", {"media_type": "episode"}),
        ("item_details", {"rating_key": "not-an-id"}),
        ("search", {"query": " "}),
    ],
)
async def test_invalid_direct_calls_do_not_reach_api(media_data, name, params) -> None:
    async with media_tools(media_data) as (mcp, ctx, requests):
        result = await (await mcp.get_tool(name)).fn(ctx=ctx, **params)
    assert result
    assert not requests


@pytest.mark.asyncio
async def test_media_schemas_are_described_and_read_only(media_data) -> None:
    async with media_tools(media_data) as (mcp, _, _):
        tools = {tool.name: tool for tool in await mcp.list_tools()}
    for tool in tools.values():
        assert tool.annotations.readOnlyHint is True
        for name, schema in tool.parameters.get("properties", {}).items():
            assert schema.get("description"), (tool.name, name)
    history = tools["history"].parameters["properties"]
    assert history["length"]["default"] == 10
    assert history["length"]["maximum"] == 50
    assert history["media_type"]["anyOf"][0]["enum"] == ["movie", "episode", "track", "live"]
    recent = tools["recently_added"].parameters["properties"]["media_type"]
    assert recent["default"] is None
    assert recent["anyOf"][0]["enum"] == ["movie", "show", "artist"]
    assert tools["item_details"].parameters["properties"]["rating_key"]["type"] == "integer"


@pytest.mark.asyncio
async def test_missing_and_unknown_fields_are_omitted(media_data) -> None:
    data = copy.deepcopy(media_data)
    data["get_activity"] = {
        "sessions": [{"title": "Minimal", "player": None, "platform": "unknown"}]
    }
    data["get_history"] = {"data": [{"title": "Minimal", "duration": None, "date": "unknown"}]}
    data["get_metadata"] = {"title": "Minimal", "actors": ["unknown", None], "rating": "unknown"}
    data["get_item_watch_time_stats"] = []
    data["get_item_user_stats"] = []
    data["get_recently_added"] = [{"title": "Minimal", "rating_key": None, "added_at": None}]
    async with media_tools(data) as (mcp, ctx, _):
        for name in ("now_playing", "history", "item_details", "recently_added"):
            params = {"rating_key": 202} if name == "item_details" else {}
            result = await (await mcp.get_tool(name)).fn(ctx=ctx, **params)
            assert "Minimal" in result
            assert_private_fields_absent(result)
            for label in ("Player:", "Platform:", "Actors:", "Rating:", "Date:", "Added:"):
                assert label not in result
