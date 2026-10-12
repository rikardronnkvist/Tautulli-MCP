"""Defense-in-depth authorization for authenticated OIDC identities."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger(__name__)


def identity_values(token: Any) -> tuple[str | None, tuple[str, ...]]:
    claims = getattr(token, "claims", None) or {}
    upstream = claims.get("upstream_claims", {}) if isinstance(claims, dict) else {}
    if not isinstance(upstream, dict):
        upstream = {}
    combined = {**claims, **upstream}
    subject = getattr(token, "subject", None) or combined.get("sub")
    usernames = tuple(
        str(combined[key]) for key in ("preferred_username", "username") if combined.get(key)
    )
    return (str(subject) if subject else None, usernames)


def subject_is_allowed(token: Any, allowed_subjects: Iterable[str]) -> bool:
    subject, usernames = identity_values(token)
    return any(value in allowed_subjects for value in ((subject,) if subject else ()) + usernames)


class AllowedSubjectsMiddleware:
    """Return HTTP 403 for authenticated identities outside the configured allow-list."""

    def __init__(self, app: ASGIApp, *, auth_provider: Any, allowed_subjects: frozenset[str]):
        self.app = app
        self.auth_provider = auth_provider
        self.allowed_subjects = allowed_subjects

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope.get("path") == "/mcp":
            headers = dict(scope.get("headers", []))
            authorization = headers.get(b"authorization", b"").decode("latin-1")
            if authorization.lower().startswith("bearer "):
                token = await self.auth_provider.verify_token(authorization[7:])
                if token is not None and not subject_is_allowed(token, self.allowed_subjects):
                    subject, _ = identity_values(token)
                    logger.warning("Denied OIDC subject %s", subject or "<missing>")
                    response = JSONResponse({"detail": "Forbidden"}, status_code=403)
                    await response(scope, receive, send)
                    return
        await self.app(scope, receive, send)
