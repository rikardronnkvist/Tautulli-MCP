# Tautulli MCP

A read-only [Model Context Protocol](https://modelcontextprotocol.io/) server that lets Claude
query Tautulli, the Plex monitoring application. It serves MCP over Streamable HTTP and is
intended for remote use through Authentik OIDC, with Authentik using Plex as the login source.

## Tools

| Tool | Purpose |
|---|---|
| `now_playing` | Active sessions, progress, playback decisions, bandwidth, and session keys |
| `history` | Playback history, optionally enriched with stream data |
| `search` | Search library titles and return rating keys |
| `item_details` | Metadata and watch statistics by rating key; never returns file paths |
| `recently_added` | New library additions and rating keys |
| `library` | Library overview or items in a section |
| `top_content` | Top TV, movie, music, or user statistics |
| `user_stats` | User plays, watch time, and last-seen details |
| `viewing_patterns` | Plays by date, weekday, or hour |
| `stream_quality` | Transcoding, platforms, resolutions, or a single play's stream details |

All tools are read-only. Use rating keys returned by `search` or `recently_added` with
`item_details`. Use library section IDs with `library`; use history `row_id` or an active
`session_key` with `stream_quality`.

Outputs use compact readable lines, human-readable watch times, and bracketed chaining
IDs (for example, `[rating_key: 42]`). Missing fields are omitted. Timestamps are displayed
as `YYYY-MM-DD HH:MM` in `TZ`, falling back to UTC for an unset or invalid timezone.
Enum parameters advertise their allowed values in the MCP input schemas.

`history` defaults to 10 entries (maximum 50); optional stream enrichment is parallel and
limited to 10 entries, and IP addresses are hidden unless requested. `recently_added`
includes all media types unless filtered. `top_content` defaults to TV ranked by plays.
`stream_quality` defaults to direct-play/direct-stream/transcode counts per platform;
its `platforms`, `resolution`, and `stream` views show platform watch time, source versus
delivered resolutions, and detailed stream decisions respectively. The `stream` view
requires a history `row_id` or active `session_key`.

## Configuration

All settings are environment variables. Secret values support Docker's `*_FILE` convention;
the file value takes precedence when both forms are set.

| Variable | Required | Default | Description |
|---|---:|---|---|
| `TAUTULLI_URL` | yes | — | Tautulli base URL reachable from the container, e.g. `http://tautulli:8181` |
| `TAUTULLI_API_KEY` / `TAUTULLI_API_KEY_FILE` | yes | — | Tautulli API key; use a master key only on a trusted internal network |
| `TAUTULLI_TLS_VERIFY` | no | `true` | Verify the Tautulli HTTPS certificate |
| `PUBLIC_BASE_URL` | yes | — | Public service URL, e.g. `https://tautulli-mcp.example.com` |
| `OIDC_CONFIG_URL` | yes | — | Authentik OpenID discovery URL |
| `OIDC_CLIENT_ID` | yes | — | Confidential Authentik provider client ID |
| `OIDC_CLIENT_SECRET` / `OIDC_CLIENT_SECRET_FILE` | yes | — | Confidential Authentik provider client secret |
| `ALLOWED_SUBJECTS` | yes | — | Comma-separated OIDC `sub` or `preferred_username` values allowed to use MCP |
| `JWT_SIGNING_KEY` / `JWT_SIGNING_KEY_FILE` | yes | — | Stable high-entropy key for MCP tokens and encrypted proxy storage |
| `STATE_DIR` | no | `/data` | Persistent encrypted OAuth registration/token storage |
| `LOG_LEVEL` | no | `INFO` | Python logging level |
| `PORT` | no | `8000` | HTTP listen port |
| `TZ` | no | `UTC` | IANA timezone for displayed timestamps, e.g. `Europe/Stockholm` |

The service exits at startup with the names of all missing required settings. It never logs
secret values or complete Tautulli request URLs.

## Authentik setup

1. In **Directory → Federation & Social login**, create a Plex source. Restrict its **Allowed
   servers** to the owner's Plex server.
2. Create an OAuth2/OpenID Provider with a confidential client. Set its redirect URI to
   `https://<public-host>/auth/callback` (FastMCP's proxy callback path is `/auth/callback`)
   and scopes to `openid profile email`.
3. Create an application with slug `tautulli-mcp` and link it to the provider. Use its discovery
   URL for `OIDC_CONFIG_URL`, for example
   `https://authentik.example.com/application/o/tautulli-mcp/.well-known/openid-configuration`.
4. Bind the application to the owner's user only. Also set `ALLOWED_SUBJECTS` to the owner's
   OIDC `sub` or `preferred_username`; the server independently rejects other identities with
   HTTP 403.
5. Keep the provider client secret and signing key in Docker Swarm secrets. Plex credentials
   and tokens remain with Authentik and are never sent to this server.

## Docker Swarm deployment

The image is published to
[`ghcr.io/rikardronnkvist/tautulli-mcp`](https://github.com/rikardronnkvist/Tautulli-MCP/pkgs/container/tautulli-mcp)
from `main` and `vX.Y.Z` tags. The example in [`deploy/stack.yml`](deploy/stack.yml) expects
pre-created external overlay networks named `traefik-public` and `tautulli`, Swarm secrets
named `tautulli_api_key`, `oidc_client_secret`, and `jwt_signing_key`, and a persistent
GlusterFS bind mount for `/data` at `/mnt/gluster01/tautulli-mcp/data`. Ensure that directory
is writable by UID 10001.

Set the required application variables from the configuration table above, plus
`PUBLIC_HOST` (hostname used in the Traefik router rule), `TAUTULLI_NETWORK` (the overlay
network Tautulli is attached to), and `TRAEFIK_CERT_RESOLVER` (your existing Traefik
certificate resolver name). Adapt external network names, storage path, and Traefik labels
for your cluster before deploying:

```sh
docker stack deploy -c deploy/stack.yml tautulli-mcp
```

The service uses one replica because OAuth state is local to its persistent disk store. Do not
scale it without moving that state to a shared backend.

## Claude custom connector

After the container is available over HTTPS, add a custom connector in claude.ai using:

```text
https://<public-host>/mcp
```

The connector's OAuth flow redirects through the service to Authentik, where the user signs in
through the configured Plex source. OAuth client registrations and tokens persist in `STATE_DIR`,
so a container restart does not require re-adding the connector.

## Local development

Install [uv](https://docs.astral.sh/uv/) and configure the required environment variables. Then:

```sh
uv sync --all-groups
uv run tautulli-mcp
```

The MCP endpoint is `/mcp`; `/health` returns `{"status":"ok"}` without authentication.
`/health?deep=1` checks Tautulli reachability and reports only `ok` or `unavailable`.
FastMCP supplies the OAuth authorization/protected-resource metadata, registration,
authorization, token, and `/auth/callback` routes.
Run checks with `uv run ruff check src tests`, `uv run ruff format --check src tests`, and
`uv run pytest`.

## Security notes

- The server exposes only a fixed allow-list of Tautulli `get_*` and `search` commands. It has no
  generic Tautulli API passthrough, write tool, or direct Plex API integration.
- Treat the Tautulli master API key as a secret. Use Docker secrets, restrict network access to
  Tautulli, and never disable TLS verification except for controlled local development.
- This is a single-owner service, not a multi-user data-scoping gateway. Authentik policy and
  `ALLOWED_SUBJECTS` must both limit access to the owner.
- FastMCP is kept on the patched 3.x line: earlier OAuth proxy releases have callback
  vulnerabilities that are relevant to this authentication flow.
- Preserve `JWT_SIGNING_KEY` and `/data` across restarts. Losing either invalidates existing
  client registrations and tokens.
