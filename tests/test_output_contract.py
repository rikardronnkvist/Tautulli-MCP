import copy
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import respx
from fastmcp import FastMCP

from tautulli_mcp.tautulli import TautulliClient
from tautulli_mcp.tools import register_tools

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        ("now_playing", {}),
        ("history", {"include_stream_details": True}),
        ("search", {"query": "Example"}),
        ("item_details", {"rating_key": 42}),
        ("recently_added", {}),
        ("library", {}),
        ("library", {"section_id": 1}),
        ("top_content", {}),
        ("user_stats", {}),
        ("viewing_patterns", {}),
        ("viewing_patterns", {"group_by": "weekday"}),
        ("viewing_patterns", {"group_by": "hour"}),
        ("stream_quality", {}),
        ("stream_quality", {"view": "platforms"}),
        ("stream_quality", {"view": "resolution"}),
        ("stream_quality", {"view": "stream", "row_id": 7}),
    ],
)
async def test_tools_omit_missing_noise_and_epoch_values(monkeypatch, tool_name, arguments):
    monkeypatch.setenv("TZ", "Europe/Stockholm")
    fixtures = json.loads((FIXTURES / "tautulli.json").read_text(encoding="utf-8"))

    def add_fields(data):
        if isinstance(data, dict):
            for key, value in list(data.items()):
                if isinstance(value, (dict, list)):
                    add_fields(value)
            if any(key in data for key in ("title", "full_title", "friendly_name", "section_name")):
                for key, value in {
                    "added_at": 1767225600,
                    "date": "1767225600",
                    "last_seen": 1767225600,
                    "last_played": 1767225600,
                    "started": 1767225600,
                    "stopped": 1767225600,
                    "platform": "unknown",
                    "player": None,
                    "container": "",
                    "video_codec": "unknown",
                }.items():
                    data.setdefault(key, value)
        elif isinstance(data, list):
            for item in data:
                add_fields(item)

    fixtures = copy.deepcopy(fixtures)
    add_fields(fixtures)

    def respond(request):
        return httpx.Response(
            200,
            json={"response": {"result": "success", "data": fixtures[request.url.params["cmd"]]}},
        )

    mcp = FastMCP("test")
    register_tools(mcp)
    async with httpx.AsyncClient() as http:
        with respx.mock(base_url="http://tautulli.test") as router:
            router.get("/api/v2").mock(side_effect=respond)
            client = TautulliClient("http://tautulli.test", "fixture-key", http)
            context = SimpleNamespace(lifespan_context={"tautulli": client})
            output = await (await mcp.get_tool(tool_name)).fn(ctx=context, **arguments)
    assert "unknown" not in output.lower()
    assert "1767225600" not in output
    assert "None" not in output
