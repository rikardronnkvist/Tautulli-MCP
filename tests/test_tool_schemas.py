from typing import Any

import pytest
from fastmcp import Context, FastMCP
from fastmcp.exceptions import ValidationError

from tautulli_mcp.tools import register_tools

ENUMS = {
    ("library", "order_by"): {
        "added_at",
        "sort_title",
        "file_size",
        "play_count",
        "last_played",
        "video_resolution",
        "bitrate",
        "video_codec",
        "audio_codec",
        "container",
    },
    ("library", "order_dir"): {"asc", "desc"},
    ("top_content", "category"): {"tv", "movies", "music", "users"},
    ("top_content", "stat_type"): {"plays", "duration"},
    ("viewing_patterns", "group_by"): {"date", "weekday", "hour"},
    ("stream_quality", "view"): {"transcode", "platforms", "resolution", "stream"},
    ("recently_added", "media_type"): {"movie", "show", "artist"},
    ("history", "media_type"): {"movie", "episode", "track", "live"},
}

DEFAULTS = {
    "library": {
        "section_id": None,
        "order_by": "added_at",
        "order_dir": "desc",
        "length": 25,
        "search": None,
    },
    "history": {
        "length": 10,
        "user": None,
        "media_type": None,
        "search": None,
        "start_date": None,
        "include_stream_details": False,
        "include_ip": False,
    },
    "top_content": {"category": "tv", "stat_type": "plays", "days": 30},
    "user_stats": {"user": None},
    "viewing_patterns": {"group_by": "date", "days": 30},
    "stream_quality": {"view": "transcode", "days": 30, "row_id": None, "session_key": None},
    "search": {"limit": 20},
    "recently_added": {"count": 10, "media_type": None},
}


@pytest.fixture
def mcp():
    server = FastMCP("schema-test")
    register_tools(server)
    return server


def enum_values(schema: dict[str, Any]) -> set[str]:
    if "enum" in schema:
        return set(schema["enum"])
    return set().union(*(enum_values(branch) for branch in schema.get("anyOf", [])))


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name, parameter", ENUMS)
async def test_enum_parameters_expose_every_choice(mcp, tool_name, parameter):
    tool = await mcp.get_tool(tool_name)
    schema = tool.parameters["properties"][parameter]
    assert enum_values(schema) == ENUMS[tool_name, parameter]


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ["history", "recently_added"])
async def test_optional_media_filters_allow_null_in_schema(mcp, tool_name):
    tool = await mcp.get_tool(tool_name)
    schema = tool.parameters["properties"]["media_type"]
    assert {"type": "null"} in schema["anyOf"]
    assert "media_type" not in tool.parameters.get("required", [])


@pytest.mark.asyncio
async def test_every_parameter_has_a_description_and_context_is_hidden(mcp):
    tools = await mcp.list_tools()
    assert len(tools) == 10
    for tool in tools:
        assert "ctx" not in tool.parameters.get("properties", {})
        for name, schema in tool.parameters.get("properties", {}).items():
            assert schema.get("description", "").strip(), f"{tool.name}.{name}"


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", DEFAULTS)
async def test_parameter_defaults_are_preserved(mcp, tool_name):
    tool = await mcp.get_tool(tool_name)
    properties = tool.parameters["properties"]
    for name, default in DEFAULTS[tool_name].items():
        assert "default" in properties[name]
        assert properties[name]["default"] == default


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool_name, parameter, optional",
    [
        ("library", "section_id", True),
        ("item_details", "rating_key", False),
        ("stream_quality", "row_id", True),
        ("stream_quality", "session_key", True),
    ],
)
async def test_ids_are_integers_not_strings(mcp, tool_name, parameter, optional):
    tool = await mcp.get_tool(tool_name)
    schema = tool.parameters["properties"][parameter]
    variants = schema.get("anyOf", [schema])
    assert {branch.get("type") for branch in variants} == (
        {"integer", "null"} if optional else {"integer"}
    )
    assert (parameter in tool.parameters.get("required", [])) is not optional


class RecordingClient:
    def __init__(self):
        self.calls = []

    async def call(self, command, **params):
        self.calls.append((command, params))
        return []


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name, parameter", ENUMS)
async def test_invalid_enum_is_rejected_before_api_call(mcp, monkeypatch, tool_name, parameter):
    client = RecordingClient()
    for module in ("library", "media", "analytics"):
        monkeypatch.setattr(f"tautulli_mcp.tools.{module}.client_from_context", lambda _: client)
    tool = await mcp.get_tool(tool_name)
    async with Context(mcp):
        with pytest.raises(ValidationError):
            await tool.run({parameter: "not-a-supported-value"})
    assert client.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool_name, module, arguments, command, parameter",
    [
        (
            "library",
            "library",
            {"section_id": "002"},
            "get_library_media_info",
            "section_id",
        ),
        (
            "item_details",
            "media",
            {"rating_key": "002"},
            "get_metadata",
            "rating_key",
        ),
        (
            "stream_quality",
            "analytics",
            {"view": "stream", "row_id": "002"},
            "get_stream_data",
            "row_id",
        ),
        (
            "stream_quality",
            "analytics",
            {"view": "stream", "session_key": "002"},
            "get_stream_data",
            "session_key",
        ),
    ],
)
async def test_numeric_string_ids_work_through_fastmcp_validation(
    mcp, monkeypatch, tool_name, module, arguments, command, parameter
):
    client = RecordingClient()
    monkeypatch.setattr(f"tautulli_mcp.tools.{module}.client_from_context", lambda _: client)
    tool = await mcp.get_tool(tool_name)
    async with Context(mcp):
        await tool.run(arguments)
    params = next(params for called, params in client.calls if called == command)
    assert params[parameter] == 2
