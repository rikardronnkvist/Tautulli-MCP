# Tautulli MCP

A read-only [Model Context Protocol](https://modelcontextprotocol.io/) server that lets Claude
query a Tautulli server. The service is designed for remote Streamable HTTP access with
Authentik OIDC authentication and Docker Swarm deployment.

## Development

Install [uv](https://docs.astral.sh/uv/) and run:

```sh
uv sync --all-groups
uv run tautulli-mcp
```

See the configuration, Authentik, deployment, and security guidance below before exposing the
service publicly.
