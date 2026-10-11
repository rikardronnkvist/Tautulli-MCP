"""Environment-based configuration with Docker secret file support."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


class ConfigurationError(ValueError):
    """Raised when required settings are missing or invalid."""


def _value(env: Mapping[str, str], name: str, *, secret: bool = False) -> str | None:
    if secret and env.get(f"{name}_FILE"):
        try:
            return Path(env[f"{name}_FILE"]).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ConfigurationError(f"Unable to read {name}_FILE") from exc
    return env.get(name)


@dataclass(frozen=True)
class Settings:
    tautulli_url: str
    tautulli_api_key: str
    tautulli_tls_verify: bool
    public_base_url: str
    oidc_config_url: str
    oidc_client_id: str
    oidc_client_secret: str
    allowed_subjects: frozenset[str]
    jwt_signing_key: str
    state_dir: Path
    log_level: str
    port: int

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        values = os.environ if env is None else env
        secret_names = {"TAUTULLI_API_KEY", "OIDC_CLIENT_SECRET", "JWT_SIGNING_KEY"}
        required = (
            "TAUTULLI_URL",
            "TAUTULLI_API_KEY",
            "PUBLIC_BASE_URL",
            "OIDC_CONFIG_URL",
            "OIDC_CLIENT_ID",
            "OIDC_CLIENT_SECRET",
            "ALLOWED_SUBJECTS",
            "JWT_SIGNING_KEY",
        )
        missing = [
            name for name in required if not _value(values, name, secret=name in secret_names)
        ]
        if missing:
            raise ConfigurationError(
                "Missing required environment variables: " + ", ".join(missing)
            )

        raw_verify = values.get("TAUTULLI_TLS_VERIFY", "true").strip().lower()
        if raw_verify not in {"true", "false", "1", "0", "yes", "no"}:
            raise ConfigurationError("TAUTULLI_TLS_VERIFY must be true or false")
        try:
            port = int(values.get("PORT", "8000"))
        except ValueError as exc:
            raise ConfigurationError("PORT must be an integer") from exc
        if not 1 <= port <= 65535:
            raise ConfigurationError("PORT must be between 1 and 65535")

        subjects = frozenset(
            subject.strip()
            for subject in (values.get("ALLOWED_SUBJECTS") or "").split(",")
            if subject.strip()
        )
        if not subjects:
            raise ConfigurationError("ALLOWED_SUBJECTS must contain at least one subject")

        return cls(
            tautulli_url=(values["TAUTULLI_URL"].strip().rstrip("/")),
            tautulli_api_key=_value(values, "TAUTULLI_API_KEY", secret=True) or "",
            tautulli_tls_verify=raw_verify in {"true", "1", "yes"},
            public_base_url=values["PUBLIC_BASE_URL"].strip().rstrip("/"),
            oidc_config_url=values["OIDC_CONFIG_URL"].strip(),
            oidc_client_id=values["OIDC_CLIENT_ID"].strip(),
            oidc_client_secret=_value(values, "OIDC_CLIENT_SECRET", secret=True) or "",
            allowed_subjects=subjects,
            jwt_signing_key=_value(values, "JWT_SIGNING_KEY", secret=True) or "",
            state_dir=Path(values.get("STATE_DIR", "/data")),
            log_level=values.get("LOG_LEVEL", "INFO").upper(),
            port=port,
        )
