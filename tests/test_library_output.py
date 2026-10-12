import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import respx
from fastmcp import FastMCP

from tautulli_mcp.tautulli import TautulliClient
from tautulli_mcp.tools.library import ORDER_BY, register_library_tools

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "library_output.json").read_text())


async def invoke(arguments, data=None):
    mcp = FastMCP("library-test")
    register_library_tools(mcp)
    tool = await mcp.get_tool("library")
    with respx.mock(base_url="http://tautulli.test", assert_all_called=False) as router:
        route = router.get("/api/v2").mock(
            side_effect=lambda request: httpx.Response(
                200,
                json={
                    "response": {
                        "result": "success",
                        "data": (data if data is not None else FIXTURE[request.url.params["cmd"]]),
                    }
                },
            )
        )
        async with httpx.AsyncClient() as http:
            client = TautulliClient("http://tautulli.test", "fixture-key", http)
            output = await tool.fn(
                ctx=SimpleNamespace(lifespan_context={"tautulli": client}), **arguments
            )
        requests = [call.request for call in route.calls]
    return output, requests


@pytest.mark.asyncio
async def test_overview_has_section_ids_type_counts_plays_and_timestamp():
    output, requests = await invoke({})
    assert requests[0].url.params["cmd"] == "get_libraries_table"
    assert "Movies [section_id: 1]" in output
    assert "12 movies" in output
    assert "30 total plays" in output
    assert "Television [section_id: 2]" in output
    assert "4 shows | 9 seasons | 80 episodes" in output
    assert "Music [section_id: 3]" in output
    assert "5 artists | 20 albums | 300 tracks" in output
    assert "2024-01-01" in output
    assert "0 total plays" in output
    assert "unknown" not in output.lower()
    assert "/private" not in output
    assert "None" not in output


@pytest.mark.asyncio
async def test_overview_uses_last_accessed_not_last_played_media_title():
    output, _ = await invoke({})
    movie_line = next(line for line in output.splitlines() if "Movies [section_id: 1]" in line)
    assert "Last played: 2024-01-01" in movie_line
    assert "Film" not in movie_line


@pytest.mark.asyncio
@pytest.mark.parametrize("last_played", [1704067200, "1704067200"])
async def test_overview_accepts_legacy_numeric_last_played(last_played):
    output, _ = await invoke({}, {"data": [{"section_name": "Legacy", "last_played": last_played}]})
    assert "Last played: 2024-01-01" in output


@pytest.mark.asyncio
@pytest.mark.parametrize("title", ["2024-01-01", "1917", "1984"])
async def test_overview_does_not_treat_last_played_title_as_timestamp(title):
    output, _ = await invoke({}, {"data": [{"section_name": "Titles", "last_played": title}]})
    assert output == "Libraries\n • Titles"


@pytest.mark.asyncio
async def test_items_have_total_size_metadata_ids_and_no_paths():
    output, requests = await invoke({"section_id": "1"})
    assert requests[0].url.params["section_id"] == "1"
    header, *lines = output.splitlines()
    assert "[section_id: 1]" in header
    assert "Total library size: 3" in header
    assert "GB" in header or "GiB" in header
    assert "Example Film" in lines[0]
    assert "[rating_key: 42]" in lines[0]
    assert "1080" in lines[0]
    assert "h264" in lines[0]
    assert "aac" in lines[0]
    assert "mkv" in lines[0]
    assert "1" in lines[0] and ("GB" in lines[0] or "GiB" in lines[0])
    assert "3 plays" in lines[0]
    assert "2024-01-01" in lines[0]
    assert "Unwatched [rating_key: 43]" in lines[1]
    assert "0 plays" in lines[1]
    assert "1970" not in output
    assert "unknown" not in output.lower()
    assert "None" not in output
    assert "/private" not in output
    assert "file_size=" not in output


@pytest.mark.asyncio
@pytest.mark.parametrize("order_by", ORDER_BY)
async def test_all_sort_fields_pass_correct_api_parameters(order_by):
    _, requests = await invoke(
        {
            "section_id": "002",
            "order_by": order_by,
            "order_dir": "asc",
            "length": 900,
            "search": "Example",
        }
    )
    params = requests[0].url.params
    assert params["section_id"] == "2"
    assert params["order_column"] == order_by
    assert params["order_dir"] == "asc"
    assert params["length"] == "100"
    assert params["search"] == "Example"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments, error",
    [
        ({"order_by": "path"}, "Invalid order_by"),
        ({"order_dir": "sideways"}, "Invalid order_dir"),
        ({"section_id": "not-an-id"}, "section_id must be an integer"),
    ],
)
async def test_invalid_arguments_do_not_call_api(arguments, error):
    output, requests = await invoke(arguments)
    assert error in output
    assert requests == []


@pytest.mark.asyncio
async def test_empty_items_do_not_render_response_metadata_as_an_item():
    output, _ = await invoke({"section_id": 1, "length": 0}, {"total_file_size": None})
    assert output == "Library [section_id: 1]"


@pytest.mark.asyncio
async def test_missing_overview_fields_are_omitted():
    output, _ = await invoke({}, {"data": [{"section_name": "Sparse"}]})
    assert output == "Libraries\n • Sparse"


@pytest.mark.asyncio
@pytest.mark.parametrize("total_size", ["1.00 TB", "10.12 GB", "2.5 GiB"])
async def test_already_humanized_library_sizes_are_preserved(total_size):
    output, _ = await invoke(
        {"section_id": 1},
        {
            "total_file_size": total_size,
            "data": [{"title": "Humanized", "rating_key": 42, "file_size": "1.25 GB"}],
        },
    )
    assert f"Total library size: {total_size}" in output
    assert "Humanized [rating_key: 42] | 1.25 GB" in output
    assert "0 B" not in output


@pytest.mark.asyncio
async def test_unknown_counts_and_sizes_are_omitted_not_fabricated():
    overview, _ = await invoke(
        {},
        {
            "data": [
                {
                    "section_name": "Sparse",
                    "section_type": "show",
                    "count": "unknown",
                    "parent_count": "Unknown",
                    "child_count": None,
                    "plays": "UNKNOWN",
                }
            ]
        },
    )
    assert overview == "Libraries\n • Sparse | Show"
    output, _ = await invoke(
        {"section_id": 1},
        {
            "total_file_size": "unknown",
            "data": [{"title": "Sparse", "file_size": "unknown", "play_count": "UNKNOWN"}],
        },
    )
    assert output == "Library [section_id: 1]\n • Sparse"


@pytest.mark.asyncio
async def test_numeric_total_library_size_is_still_formatted():
    output, _ = await invoke({"section_id": 1}, {"total_file_size": "3221225472", "data": []})
    assert output == "Library [section_id: 1] | Total library size: 3.0 GiB"
