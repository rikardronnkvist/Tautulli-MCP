import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import respx
from fastmcp import FastMCP

from tautulli_mcp.tautulli import TautulliClient
from tautulli_mcp.tools import register_tools

FIXTURES = Path(__file__).parent / "fixtures"


class FakeTautulli:
    def __init__(self, responses: dict[str, Any]):
        self.responses = responses
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call(self, command: str, **params: Any) -> Any:
        self.calls.append((command, params))
        return self.responses.get(command, [])


def tool_context(client: FakeTautulli) -> SimpleNamespace:
    return SimpleNamespace(lifespan_context={"tautulli": client})


def get_tool(mcp: FastMCP, name: str):
    return asyncio.run(mcp.get_tool(name))


def test_registers_ten_read_only_tools() -> None:
    mcp = FastMCP("test")
    register_tools(mcp)

    tools = asyncio.run(mcp.list_tools())
    assert len(tools) == 10
    assert all(tool.annotations and tool.annotations.readOnlyHint for tool in tools)


def test_history_clamps_length_caps_enrichment_and_hides_ip() -> None:
    entries = [
        {"row_id": str(index), "full_title": f"Title {index}", "ip_address": "192.0.2.1"}
        for index in range(11)
    ]
    fake = FakeTautulli({"get_history": entries, "get_stream_data": [{"bitrate": 4000}]})
    mcp = FastMCP("test")
    register_tools(mcp)

    result = asyncio.run(
        get_tool(mcp, "history").fn(ctx=tool_context(fake), length=0, include_stream_details=True)
    )

    assert fake.calls[0] == ("get_history", {"length": 1})
    assert sum(command == "get_stream_data" for command, _ in fake.calls) == 10
    assert "row_id=0" in result
    assert "ip_address" not in result


def test_rejects_invalid_enum_without_calling_tautulli() -> None:
    fake = FakeTautulli({})
    mcp = FastMCP("test")
    register_tools(mcp)

    result = asyncio.run(
        get_tool(mcp, "recently_added").fn(ctx=tool_context(fake), media_type="episode")
    )

    assert "movie, show, artist" in result
    assert fake.calls == []


def test_item_details_omits_file_paths() -> None:
    fake = FakeTautulli(
        {
            "get_metadata": {"title": "Example", "file": "/private/library/movie.mkv"},
            "get_item_watch_time_stats": {"total_plays": 4},
            "get_item_user_stats": [],
        }
    )
    mcp = FastMCP("test")
    register_tools(mcp)

    result = asyncio.run(get_tool(mcp, "item_details").fn(ctx=tool_context(fake), rating_key="42"))

    assert "Example" in result
    assert "/private/library" not in result


def test_search_clamps_limit_and_includes_chaining_rating_key() -> None:
    fake = FakeTautulli(
        {
            "search": {
                "results_count": 1,
                "results_list": {
                    "movie": [{"title": "Example", "rating_key": "42"}],
                    "show": [{"title": "Example Show", "rating_key": "43"}],
                },
            }
        }
    )
    mcp = FastMCP("test")
    register_tools(mcp)

    result = asyncio.run(
        get_tool(mcp, "search").fn(ctx=tool_context(fake), query="Example", limit=900)
    )

    assert fake.calls[0] == ("search", {"query": "Example", "limit": 50})
    assert "[key: 42]" in result
    assert "Movie: Example [key: 42]" in result
    assert "Show: Example Show [key: 43]" in result


def test_top_content_clamps_days_and_maps_category() -> None:
    fake = FakeTautulli({"get_home_stats": []})
    mcp = FastMCP("test")
    register_tools(mcp)

    asyncio.run(
        get_tool(mcp, "top_content").fn(
            ctx=tool_context(fake), category="tv", stat_type="plays", days=900
        )
    )

    assert fake.calls == [
        (
            "get_home_stats",
            {"stat_id": "top_tv", "stats_type": "plays", "time_range": 365},
        )
    ]


def test_user_stats_omits_zero_play_users() -> None:
    fake = FakeTautulli(
        {
            "get_users_table": [
                {"friendly_name": "Owner", "plays": 5},
                {"friendly_name": "Unused", "plays": 0},
            ]
        }
    )
    mcp = FastMCP("test")

    register_tools(mcp)
    result = asyncio.run(get_tool(mcp, "user_stats").fn(ctx=tool_context(fake)))

    assert "Owner" in result
    assert "Unused" not in result


def test_user_stats_passes_username_as_search() -> None:
    fake = FakeTautulli({"get_users_table": [{"friendly_name": "Owner", "plays": 5}]})
    mcp = FastMCP("test")
    register_tools(mcp)

    asyncio.run(get_tool(mcp, "user_stats").fn(ctx=tool_context(fake), user="Owner"))

    assert fake.calls == [("get_users_table", {"search": "Owner"})]


def test_library_uses_tautulli_order_column_and_sort_title() -> None:
    fake = FakeTautulli({})
    mcp = FastMCP("test")
    register_tools(mcp)

    result = asyncio.run(
        get_tool(mcp, "library").fn(ctx=tool_context(fake), section_id="1", order_by="sort_title")
    )

    assert result == "Library section 1"
    assert fake.calls == [
        (
            "get_library_media_info",
            {"section_id": "1", "order_column": "sort_title", "order_dir": "desc", "length": 25},
        )
    ]


def test_stream_quality_requires_identity_and_valid_view() -> None:
    fake = FakeTautulli({})
    mcp = FastMCP("test")
    register_tools(mcp)

    tool = get_tool(mcp, "stream_quality")
    missing_id = asyncio.run(tool.fn(ctx=tool_context(fake), view="stream"))
    bad_view = asyncio.run(tool.fn(ctx=tool_context(fake), view="unknown"))

    assert "requires row_id or session_key" in missing_id
    assert "transcode, platforms, resolution, stream" in bad_view
    assert fake.calls == []


@pytest.mark.asyncio
async def test_all_tools_with_respx_responses_from_json_fixture() -> None:
    fixtures = json.loads((FIXTURES / "tautulli.json").read_text(encoding="utf-8"))
    calls: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        command = request.url.params["cmd"]
        return httpx.Response(
            200,
            json={
                "response": {
                    "result": "success",
                    "data": fixtures.get(command, []),
                }
            },
        )

    mcp = FastMCP("test")
    register_tools(mcp)
    async with httpx.AsyncClient() as http:
        with respx.mock(base_url="http://tautulli.test") as router:
            router.get("/api/v2").mock(side_effect=respond)
            client = TautulliClient("http://tautulli.test", "fixture-key", http)
            ctx = tool_context(client)
            results = {
                "now_playing": await (await mcp.get_tool("now_playing")).fn(ctx=ctx),
                "history": await (await mcp.get_tool("history")).fn(ctx=ctx),
                "search": await (await mcp.get_tool("search")).fn(ctx=ctx, query="Example"),
                "item_details": await (await mcp.get_tool("item_details")).fn(
                    ctx=ctx, rating_key="42"
                ),
                "recently_added": await (await mcp.get_tool("recently_added")).fn(ctx=ctx),
                "library": await (await mcp.get_tool("library")).fn(ctx=ctx),
                "top_content": await (await mcp.get_tool("top_content")).fn(ctx=ctx),
                "user_stats": await (await mcp.get_tool("user_stats")).fn(ctx=ctx),
                "viewing_patterns": await (await mcp.get_tool("viewing_patterns")).fn(ctx=ctx),
                "weekday_patterns": await (await mcp.get_tool("viewing_patterns")).fn(
                    ctx=ctx, group_by="weekday"
                ),
                "hour_patterns": await (await mcp.get_tool("viewing_patterns")).fn(
                    ctx=ctx, group_by="hour"
                ),
                "stream_quality": await (await mcp.get_tool("stream_quality")).fn(ctx=ctx),
                "platform_quality": await (await mcp.get_tool("stream_quality")).fn(
                    ctx=ctx, view="platforms"
                ),
                "resolution_quality": await (await mcp.get_tool("stream_quality")).fn(
                    ctx=ctx, view="resolution"
                ),
            }

    assert all(isinstance(result, str) for result in results.values())
    assert "session_key=session-1" in results["now_playing"]
    assert "row_id=7" in results["history"]
    assert "[key: 42]" in results["search"]
    assert "/private/library" not in results["item_details"]
    assert "total_plays_24=1" in results["item_details"]
    assert "total_plays_7=2" in results["item_details"]
    assert "total_plays_30=4" in results["item_details"]
    assert "[id: 1]" in results["library"]
    assert "peak" in results["viewing_patterns"]
    assert "2026-01-01:" in results["viewing_patterns"]
    assert "5 ← peak" in results["viewing_patterns"]
    assert "Direct Play" in results["viewing_patterns"]
    assert "7 ← peak" in results["weekday_patterns"]
    assert "10:00:" in results["hour_patterns"]
    assert "plays=5" in results["stream_quality"]
    assert "direct_play=5" in results["platform_quality"]
    assert "source_resolution=1080" in results["resolution_quality"]
    assert "stream_resolution=720" in results["resolution_quality"]
    assert all(request.url.params["apikey"] == "fixture-key" for request in calls)
