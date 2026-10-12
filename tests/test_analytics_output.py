import copy
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import respx
from fastmcp import FastMCP

from tautulli_mcp.tautulli import TautulliClient
from tautulli_mcp.tools.analytics import register_analytics_tools

FIXTURE = Path(__file__).parent / "fixtures" / "analytics_output.json"


@pytest.fixture
def payloads() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text())


async def invoke(
    name: str, payloads: dict[str, Any], **params: Any
) -> tuple[str, list[dict[str, str]]]:
    mcp = FastMCP("analytics output")
    register_analytics_tools(mcp)
    tool = await mcp.get_tool(name)
    requests = []

    def respond(request: httpx.Request) -> httpx.Response:
        query = dict(request.url.params)
        requests.append(query)
        command = query["cmd"]
        key = query["stat_id"] if command == "get_home_stats" else command
        return httpx.Response(200, json=payloads[key])

    with respx.mock(assert_all_called=False) as mock:
        mock.get("http://tautulli.test/api/v2").mock(side_effect=respond)
        async with httpx.AsyncClient() as http:
            client = TautulliClient("http://tautulli.test", "fixture-api-key", http)
            ctx = SimpleNamespace(lifespan_context={"tautulli": client})
            output = await tool.fn(ctx=ctx, **params)
    return output, requests


@pytest.mark.parametrize(
    ("category", "title", "plays", "key"),
    [
        ("tv", "The Example Show", "12 plays", "101"),
        ("movies", "Example Movie (2024)", "7 plays", "201"),
        ("music", "Example Artist", "18 plays", "301"),
        ("users", "Family Room", "11 plays", None),
    ],
)
async def test_top_content_unwraps_real_home_stats(
    payloads: dict[str, Any], category: str, title: str, plays: str, key: str | None
) -> None:
    output, requests = await invoke("top_content", payloads, category=category, days=999)
    assert f"1. {title} — {plays}," in output
    assert "Wrong category" not in output
    assert "total_duration=" not in output
    assert "unknown" not in output.lower()
    if key:
        assert f"[rating_key: {key}]" in output
    if category == "tv":
        assert "10h" in output
        assert "2. Second Show" in output
    assert requests[0]["stat_id"] == f"top_{category}"
    assert requests[0]["time_range"] == "365"
    assert requests[0]["stats_type"] == "plays"


async def test_defaults_and_duration_ranking_request(payloads: dict[str, Any]) -> None:
    output, requests = await invoke("top_content", payloads)
    assert "Top tv by plays" in output
    assert requests[0]["stat_id"] == "top_tv"
    _, requests = await invoke("top_content", payloads, stat_type="duration", days=0)
    assert requests[0]["stats_type"] == "duration"
    assert requests[0]["time_range"] == "1"


async def test_top_content_caps_rows_at_ten(payloads: dict[str, Any]) -> None:
    entries = payloads["top_tv"]["response"]["data"][1]["rows"]
    entries.extend(
        {"title": f"Extra show {index}", "total_plays": 1, "total_duration": 60}
        for index in range(12)
    )
    output, _ = await invoke("top_content", payloads)
    assert "10. Extra show 7" in output
    assert "11." not in output
    assert "Extra show 8" not in output


async def test_users_sorted_locally_with_readable_time_and_latest_title(
    payloads: dict[str, Any],
) -> None:
    output, requests = await invoke("user_stats", payloads, user="Family")
    assert requests[0]["order_column"] == "plays"
    assert requests[0]["order_dir"] == "desc"
    assert requests[0]["search"] == "Family"
    assert output.index("Family Room") < output.index("Guest")
    assert "Unused" not in output
    assert "10h" in output
    assert "Last seen: 2024-01-02" in output
    assert "Latest: Latest Movie" in output
    assert "Latest: Guest Movie" in output
    assert "Owner display title" not in output and "Guest display title" not in output
    assert "1704153600" not in output
    assert "unknown" not in output.lower()


async def test_user_last_played_is_a_title_not_a_timestamp(payloads: dict[str, Any]) -> None:
    users = payloads["get_users_table"]["response"]["data"]["data"]
    users[:] = [
        {"title": "User Display Title", "plays": 2, "last_played": "The Latest Media Title"},
        {
            "title": "Another User",
            "plays": 1,
            "last_seen": "2024-01-03",
            "full_title": "Fallback Media Title",
        },
    ]
    output, _ = await invoke("user_stats", payloads)
    assert "User Display Title — 2 plays" in output
    assert "Latest: The Latest Media Title" in output
    assert "Latest: Fallback Media Title" in output
    assert output.count("Last seen:") == 1


async def test_dates_trim_only_leading_zeros_and_align_decisions_by_date(
    payloads: dict[str, Any],
) -> None:
    output, requests = await invoke("viewing_patterns", payloads)
    assert [request["cmd"] for request in requests] == [
        "get_plays_by_date",
        "get_plays_by_stream_type",
    ]
    assert "2024-01-01" not in output
    assert "2024-01-03 00:00:" in output
    assert "2024-01-05 00:00:" in output
    assert "Total: 6 plays | Average: 1.50 plays" in output
    assert "TV: 3 | Movies: 2 | Music: 1" in output
    date_lines = [line for line in output.splitlines() if line.startswith("2024")]
    assert "DP 2 / DS 0 / TC 1" in date_lines[0]
    assert "DP 0 / DS 1 / TC 2" in date_lines[2]
    assert date_lines[0].index("█") < date_lines[0].index("DP")
    assert "← peak" in output
    assert "Plays by stream type" not in output


@pytest.mark.parametrize(
    ("group", "command", "first", "average"),
    [
        ("weekday", "get_plays_by_dayofweek", "Sunday", "2.00"),
        ("hour", "get_plays_by_hourofday", "00", "1.00"),
    ],
)
async def test_other_groupings_keep_empty_categories(
    payloads: dict[str, Any], group: str, command: str, first: str, average: str
) -> None:
    output, requests = await invoke("viewing_patterns", payloads, group_by=group)
    assert len(requests) == 1 and requests[0]["cmd"] == command
    assert f"{first}:" in output
    assert f"Average: {average} plays" in output
    assert "DP " not in output
    assert " / Movies:" in output
    bar_line = next(line for line in output.splitlines() if "█" in line)
    assert bar_line.index("█") < bar_line.index("TV:")


async def test_all_zero_dates_report_no_plays_without_noisy_rows(payloads: dict[str, Any]) -> None:
    data = payloads["get_plays_by_date"]["response"]["data"]
    for series in data["series"]:
        series["data"] = [0] * len(data["categories"])
    output, _ = await invoke("viewing_patterns", payloads)
    assert "2024-01-01" not in output
    assert "Total: 0 plays | Average: 0.00 plays" in output
    assert "No plays in this date range." in output
    assert "DP " not in output
    assert "← peak" not in output


@pytest.mark.parametrize(
    ("group", "command"),
    [("weekday", "get_plays_by_dayofweek"), ("hour", "get_plays_by_hourofday")],
)
async def test_all_zero_weekday_and_hour_keep_categories(
    payloads: dict[str, Any], group: str, command: str
) -> None:
    graph = payloads[command]["response"]["data"]
    for series in graph["series"]:
        series["data"] = [0] * len(graph["categories"])
    output, _ = await invoke("viewing_patterns", payloads, group_by=group)
    assert all(f"{category}:" in output for category in graph["categories"])
    assert "Total: 0 plays | Average: 0.00 plays" in output
    assert "← peak" not in output


async def test_chart_only_reports_media_series_present(payloads: dict[str, Any]) -> None:
    graph = payloads["get_plays_by_dayofweek"]["response"]["data"]
    graph["series"] = graph["series"][:1]
    output, _ = await invoke("viewing_patterns", payloads, group_by="weekday")
    assert "TV: 4" in output
    assert "Movies:" not in output and "Music:" not in output
    assert "Monday:" in output and "| TV: 4" in output


async def test_numeric_date_labels_are_readable(payloads: dict[str, Any]) -> None:
    for command in ("get_plays_by_date", "get_plays_by_stream_type"):
        data = payloads[command]["response"]["data"]
        data["categories"] = ["1704067200", "1704153600", "1704240000", "1704326400", "1704412800"]
    output, _ = await invoke("viewing_patterns", payloads)
    assert "2024-01-02 00:00:" in output
    assert "1704153600" not in output


async def test_transcode_uses_platform_graph_and_weighted_overall(
    payloads: dict[str, Any],
) -> None:
    output, requests = await invoke("stream_quality", payloads)
    assert len(requests) == 1
    assert requests[0]["cmd"] == "get_stream_type_by_top_10_platforms"
    assert "Roku — 10 plays | direct play 6 / direct stream 2 / transcode 2 | TC 20.0%" in output
    assert "Web — 5 plays | direct play 0 / direct stream 1 / transcode 4 | TC 80.0%" in output
    assert "Unused — 0 plays | direct play 0 / direct stream 0 / transcode 0 | TC 0.0%" in output
    assert "Overall — 15 plays | direct play 6 / direct stream 3 / transcode 6 | TC 40.0%" in output


async def test_platforms_only_requests_home_stats_with_watch_time(
    payloads: dict[str, Any],
) -> None:
    output, requests = await invoke("stream_quality", payloads, view="platforms")
    assert len(requests) == 1
    assert requests[0]["cmd"] == "get_home_stats"
    assert requests[0]["stat_id"] == "top_platforms"
    assert "1. Roku — 10 plays, 10h" in output
    assert "DP " not in output


async def test_resolution_sections_and_supported_down_transcoding(
    payloads: dict[str, Any],
) -> None:
    output, requests = await invoke("stream_quality", payloads, view="resolution")
    assert {request["cmd"] for request in requests} == {
        "get_plays_by_source_resolution",
        "get_plays_by_stream_resolution",
    }
    assert "Source resolutions" in output and "Delivered resolutions" in output
    assert "4k — 5 plays | direct play 2 / direct stream 0 / transcode 3" in output
    assert "4K sources were down-transcoded" in output


@pytest.mark.parametrize("ambiguous", ["4k-delivery", "missing-delivery", "no-transcodes"])
async def test_resolution_does_not_claim_down_transcoding_without_evidence(
    payloads: dict[str, Any], ambiguous: str
) -> None:
    source = payloads["get_plays_by_source_resolution"]["response"]["data"]
    delivered = payloads["get_plays_by_stream_resolution"]["response"]["data"]
    if ambiguous == "4k-delivery":
        delivered["series"][2]["data"] = [1, 1, 3]
    elif ambiguous == "missing-delivery":
        delivered["series"][2]["data"] = [0, 0, 0]
    else:
        source["series"][2]["data"] = [0, 2]
    output, _ = await invoke("stream_quality", payloads, view="resolution")
    assert "down-transcoded" not in output
    if ambiguous == "4k-delivery":
        assert "aggregate counts suggest" in output
        assert "do not establish individual stream paths" in output
    else:
        assert "down-transcoding" not in output


@pytest.mark.parametrize(
    ("params", "parameter", "value"),
    [
        ({"row_id": "42"}, "row_id", "42"),
        ({"row_id": 0}, "row_id", "0"),
        ({"session_key": " 7 "}, "session_key", "7"),
        ({"row_id": 42, "session_key": 7}, "row_id", "42"),
    ],
)
async def test_stream_numeric_ids_and_shared_details(
    payloads: dict[str, Any], params: dict[str, Any], parameter: str, value: str
) -> None:
    output, requests = await invoke("stream_quality", payloads, view="stream", **params)
    assert len(requests) == 1
    assert requests[0]["cmd"] == "get_stream_data"
    assert requests[0][parameter] == value
    assert ("session_key" if parameter == "row_id" else "row_id") not in requests[0]
    assert "Example Movie" in output
    assert "hevc" in output.lower() and "h264" in output.lower()
    assert "4K" in output and "1080p" in output
    assert "truehd" in output.lower() and "aac" in output.lower()
    assert "[rating_key: 201]" in output and "[row_id: 42]" in output
    assert output.count("Example Movie") == 1
    assert "192.0.2.55" not in output
    assert "/private/library" not in output
    assert "private metadata" not in output
    assert "unknown" not in output.lower()


@pytest.mark.parametrize(
    "params",
    [{}, {"row_id": "abc"}, {"session_key": 1.5}, {"row_id": -1}, {"row_id": True}],
)
async def test_stream_rejects_missing_or_invalid_ids_without_request(
    payloads: dict[str, Any], params: dict[str, Any]
) -> None:
    output, requests = await invoke("stream_quality", payloads, view="stream", **params)
    assert requests == []
    assert "requires" in output or "integer IDs" in output


async def test_stream_unwraps_real_session_and_media_info(payloads: dict[str, Any]) -> None:
    record = payloads["get_stream_data"]["response"]["data"]
    payloads["get_stream_data"]["response"]["data"] = {
        "session_info": {"row_id": 42, "full_title": "Example Movie", "rating_key": "201"},
        "media_info": record,
    }
    output, _ = await invoke("stream_quality", payloads, view="stream", row_id=42)
    assert "Source:" in output and "Delivered:" in output
    assert "Subtitles: burn" in output
    assert "HW decode:" in output and "HW encode:" in output
    assert "Transcode reason: Video codec unsupported" in output
    assert "/private/library" not in output


@pytest.mark.parametrize(
    ("name", "params"),
    [
        ("top_content", {"category": "podcasts"}),
        ("top_content", {"stat_type": "rating"}),
        ("viewing_patterns", {"group_by": "month"}),
        ("stream_quality", {"view": "files"}),
    ],
)
async def test_invalid_modes_do_not_request_data(
    payloads: dict[str, Any], name: str, params: dict[str, Any]
) -> None:
    output, requests = await invoke(name, payloads, **params)
    assert "Invalid" in output
    assert requests == []


async def test_empty_analytics_data_is_readable(payloads: dict[str, Any]) -> None:
    empty = copy.deepcopy(payloads)
    for payload in empty.values():
        payload["response"]["data"] = []
    for name, params in [
        ("top_content", {}),
        ("user_stats", {}),
        ("viewing_patterns", {}),
        ("stream_quality", {}),
        ("stream_quality", {"view": "stream", "row_id": 42}),
        ("stream_quality", {"view": "resolution"}),
    ]:
        output, _ = await invoke(name, empty, **params)
        assert "no data" in output or "no active users" in output
        assert "unknown" not in output.lower()


async def test_analytics_schemas_describe_every_parameter_and_enumerate_modes() -> None:
    mcp = FastMCP("analytics schemas")
    register_analytics_tools(mcp)
    expected = {
        "top_content": {
            "category": ["tv", "movies", "music", "users"],
            "stat_type": ["plays", "duration"],
        },
        "viewing_patterns": {"group_by": ["date", "weekday", "hour"]},
        "stream_quality": {"view": ["transcode", "platforms", "resolution", "stream"]},
    }
    for name in ("top_content", "user_stats", "viewing_patterns", "stream_quality"):
        tool = await mcp.get_tool(name)
        properties = tool.parameters["properties"]
        assert all(property.get("description") for property in properties.values())
        for parameter, options in expected.get(name, {}).items():
            assert properties[parameter]["enum"] == options
        if name == "stream_quality":
            for parameter in ("row_id", "session_key"):
                types = {branch["type"] for branch in properties[parameter]["anyOf"]}
                assert types == {"integer", "null"}
    assert (await mcp.get_tool("top_content")).parameters["properties"]["category"][
        "default"
    ] == "tv"
