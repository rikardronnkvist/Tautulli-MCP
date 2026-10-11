"""FastMCP application, OIDC proxy, health endpoint, and process entrypoint."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import uvicorn
from fastmcp import FastMCP
from fastmcp import settings as fastmcp_settings
from fastmcp.server.auth.oidc_proxy import OIDCProxy
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp

from tautulli_mcp.authorization import AllowedSubjectsMiddleware
from tautulli_mcp.config import Settings
from tautulli_mcp.tautulli import TautulliClient
from tautulli_mcp.tools import register_tools

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> ASGIApp:
    config = settings or Settings.from_env()
    for logger_name in ("httpx", "httpcore"):
        logging.getLogger(logger_name).setLevel(logging.WARNING)
    config.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    config.state_dir.chmod(0o700)
    fastmcp_settings.home = config.state_dir

    auth = OIDCProxy(
        config_url=config.oidc_config_url,
        client_id=config.oidc_client_id,
        client_secret=config.oidc_client_secret,
        base_url=config.public_base_url,
        issuer_url=config.public_base_url,
        redirect_path="/auth/callback",
        jwt_signing_key=config.jwt_signing_key,
        required_scopes=["openid", "profile", "email"],
        verify_id_token=True,
    )

    health_client: TautulliClient | None = None

    @asynccontextmanager
    async def lifespan(_: FastMCP) -> AsyncIterator[dict[str, Any]]:
        nonlocal health_client
        timeout = httpx.Timeout(15.0, connect=10.0)
        async with httpx.AsyncClient(timeout=timeout, verify=config.tautulli_tls_verify) as client:
            health_client = TautulliClient(config.tautulli_url, config.tautulli_api_key, client)
            try:
                yield {"tautulli": health_client}
            finally:
                health_client = None

    mcp = FastMCP("Tautulli MCP", auth=auth, lifespan=lifespan)
    register_tools(mcp)

    @mcp.custom_route("/health", methods=["GET"], include_in_schema=False)
    async def health(request: Request) -> JSONResponse:
        if request.query_params.get("deep") == "1":
            if health_client is None:
                return JSONResponse({"status": "unavailable"}, status_code=503)
            try:
                await health_client.call("get_server_info")
            except RuntimeError:
                return JSONResponse({"status": "unavailable"}, status_code=503)
        return JSONResponse({"status": "ok"})

    app = mcp.http_app(path="/mcp", transport="streamable-http")
    app.add_middleware(
        AllowedSubjectsMiddleware,
        auth_provider=auth,
        allowed_subjects=config.allowed_subjects,
    )
    return app


def main() -> None:
    config = Settings.from_env()
    logging.basicConfig(
        level=getattr(logging, config.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    app = create_app(config)
    uvicorn.run(app, host="0.0.0.0", port=config.port, log_config=None)


if __name__ == "__main__":
    main()
