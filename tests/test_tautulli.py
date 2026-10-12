import httpx
import pytest
import respx

from tautulli_mcp.tautulli import TautulliClient


@pytest.mark.asyncio
async def test_calls_allowlisted_command_and_returns_data() -> None:
    with respx.mock(base_url="http://tautulli:8181") as router:
        route = router.get("/api/v2").mock(
            return_value=httpx.Response(
                200,
                json={"response": {"result": "success", "data": {"sessions": []}}},
            )
        )
        async with httpx.AsyncClient() as http:
            result = await TautulliClient("http://tautulli:8181", "top-secret", http).call(
                "get_activity"
            )

    assert result == {"sessions": []}
    assert route.calls[0].request.url.params["apikey"] == "top-secret"


@pytest.mark.asyncio
async def test_rejects_commands_outside_allowlist() -> None:
    async with httpx.AsyncClient() as http:
        client = TautulliClient("http://tautulli:8181", "secret", http)
        with pytest.raises(ValueError, match="not allowed"):
            await client.call("delete_user")


@pytest.mark.asyncio
async def test_errors_and_logs_do_not_leak_api_key_or_url(caplog) -> None:
    with respx.mock(base_url="http://tautulli:8181") as router:
        router.get("/api/v2").mock(
            return_value=httpx.Response(
                200,
                json={
                    "response": {
                        "result": "error",
                        "message": "top-secret at http://tautulli:8181/api/v2?apikey=top-secret",
                    }
                },
            )
        )
        async with httpx.AsyncClient() as http:
            with pytest.raises(RuntimeError) as error:
                await TautulliClient("http://tautulli:8181", "top-secret", http).call(
                    "get_activity"
                )

    assert str(error.value).startswith("Tautulli get_activity failed:")
    assert "top-secret" not in str(error.value)
    assert "http://" not in str(error.value)
    assert "top-secret" not in caplog.text
