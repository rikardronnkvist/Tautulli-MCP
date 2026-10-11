import logging
from pathlib import Path
from types import SimpleNamespace

import httpx
import respx
from fastmcp.server.auth import TokenVerifier
from starlette.testclient import TestClient

from tautulli_mcp.config import Settings
from tautulli_mcp.server import create_app


def settings_for_test(state_dir: Path) -> Settings:
    return Settings(
        tautulli_url="http://tautulli.test",
        tautulli_api_key="test-api-key",
        tautulli_tls_verify=True,
        public_base_url="https://mcp.test",
        oidc_config_url="https://auth.test/.well-known/openid-configuration",
        oidc_client_id="client",
        oidc_client_secret="client-secret",
        allowed_subjects=frozenset({"owner"}),
        jwt_signing_key="test-signing-key",
        state_dir=state_dir,
        log_level="INFO",
        port=8000,
    )


class FakeAuthProvider(TokenVerifier):
    async def verify_token(self, token: str):
        if token == "blocked":
            return SimpleNamespace(subject="intruder", claims={})
        return None


def test_health_routes_are_public_and_deep_check_hides_details(
    tmp_path: Path, monkeypatch, caplog
) -> None:
    caplog.set_level(logging.INFO)
    monkeypatch.setattr(
        "tautulli_mcp.server.OIDCProxy",
        lambda **kwargs: FakeAuthProvider(base_url=kwargs["base_url"]),
    )
    app = create_app(settings_for_test(tmp_path / "state"))
    with respx.mock(base_url="http://tautulli.test") as router:
        route = router.get("/api/v2").mock(
            return_value=httpx.Response(
                200,
                json={
                    "response": {
                        "result": "error",
                        "message": "internal server details",
                    }
                },
            )
        )
        with TestClient(app) as client:
            response = client.get("/health")
            deep = client.get("/health?deep=1")
            denied = client.post("/mcp", headers={"Authorization": "Bearer " + "blocked"})
            unauthorized = client.post("/mcp")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert deep.status_code == 503
    assert deep.json() == {"status": "unavailable"}
    assert "internal server details" not in deep.text
    assert route.calls[0].request.url.params["apikey"] == "test-api-key"
    assert "test-api-key" not in caplog.text
    assert "/api/v2" not in caplog.text
    assert denied.status_code == 403
    assert denied.text == '{"detail":"Forbidden"}'
    assert unauthorized.status_code == 401


def test_oidc_proxy_exposes_oauth_routes_and_metadata(tmp_path: Path) -> None:
    discovery = {
        "issuer": "https://auth.test/application/o/tautulli-mcp/",
        "authorization_endpoint": "https://auth.test/authorize/",
        "token_endpoint": "https://auth.test/token/",
        "jwks_uri": "https://auth.test/jwks/",
        "response_types_supported": ["code"],
        "subject_types_supported": ["public"],
        "id_token_signing_alg_values_supported": ["RS256"],
    }
    with respx.mock(base_url="https://auth.test") as router:
        router.get("/.well-known/openid-configuration").mock(
            return_value=httpx.Response(200, json=discovery)
        )
        app = create_app(settings_for_test(tmp_path / "state"))
        paths = {route.path for route in app.routes if hasattr(route, "path")}
        with TestClient(app) as client:
            metadata = client.get("/.well-known/oauth-authorization-server")

    assert metadata.status_code == 200
    assert metadata.json()["authorization_endpoint"] == "https://mcp.test/authorize"
    assert {"/register", "/authorize", "/token", "/auth/callback"} <= paths
