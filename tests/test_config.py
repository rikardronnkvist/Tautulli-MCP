from pathlib import Path

import pytest

from tautulli_mcp.config import ConfigurationError, Settings


def config_env(**overrides: str) -> dict[str, str]:
    values = {
        "TAUTULLI_URL": "http://tautulli:8181/",
        "TAUTULLI_API_KEY": "master-key",
        "PUBLIC_BASE_URL": "https://example.test",
        "OIDC_CONFIG_URL": "https://auth.example.test/.well-known/openid-configuration",
        "OIDC_CLIENT_ID": "client-id",
        "OIDC_CLIENT_SECRET": "client-secret",
        "ALLOWED_SUBJECTS": "owner-sub,owner-name",
        "JWT_SIGNING_KEY": "stable-signing-secret",
    }
    values.update(overrides)
    return values


def test_loads_defaults_and_normalizes_values() -> None:
    settings = Settings.from_env(config_env())

    assert settings.tautulli_url == "http://tautulli:8181"
    assert settings.allowed_subjects == {"owner-sub", "owner-name"}
    assert settings.tautulli_tls_verify is True
    assert settings.port == 8000
    assert settings.state_dir == Path("/data")


def test_secret_file_takes_precedence(tmp_path: Path) -> None:
    secret_file = tmp_path / "api-key"
    secret_file.write_text("secret-from-file\n", encoding="utf-8")

    settings = Settings.from_env(
        config_env(TAUTULLI_API_KEY="environment-secret", TAUTULLI_API_KEY_FILE=str(secret_file))
    )

    assert settings.tautulli_api_key == "secret-from-file"


def test_missing_required_values_are_reported_together() -> None:
    with pytest.raises(ConfigurationError, match="TAUTULLI_URL.*PUBLIC_BASE_URL"):
        Settings.from_env({})


def test_rejects_invalid_port_and_empty_subjects() -> None:
    with pytest.raises(ConfigurationError, match="PORT"):
        Settings.from_env(config_env(PORT="not-a-port"))
    with pytest.raises(ConfigurationError, match="ALLOWED_SUBJECTS"):
        Settings.from_env(config_env(ALLOWED_SUBJECTS=" , "))
