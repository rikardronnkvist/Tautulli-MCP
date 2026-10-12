from types import SimpleNamespace

import pytest
from starlette.types import Receive, Scope, Send

from tautulli_mcp.authorization import AllowedSubjectsMiddleware, subject_is_allowed


def test_subject_and_username_allow_list() -> None:
    token = SimpleNamespace(
        subject="owner-sub",
        claims={"preferred_username": "owner"},
    )

    assert subject_is_allowed(token, {"owner-sub"})
    assert subject_is_allowed(token, {"owner"})
    assert not subject_is_allowed(token, {"other"})


@pytest.mark.asyncio
async def test_denied_identity_gets_http_403_and_logs_subject(caplog) -> None:
    token = SimpleNamespace(subject="intruder", claims={})

    class Provider:
        async def verify_token(self, value: str) -> object:
            assert value == "test-token"
            return token

    async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
        raise AssertionError("denied requests must not reach MCP")

    middleware = AllowedSubjectsMiddleware(
        downstream, auth_provider=Provider(), allowed_subjects=frozenset({"owner"})
    )
    sent = []

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, object]) -> None:
        sent.append(message)

    await middleware(
        {
            "type": "http",
            "path": "/mcp",
            "headers": [(b"authorization", b"Bearer " + b"test-token")],
            "method": "POST",
        },
        receive,
        send,
    )

    assert sent[0]["status"] == 403
    assert "intruder" in caplog.text
    assert "test-token" not in caplog.text
