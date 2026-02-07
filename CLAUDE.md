# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

TIDAL MCP is a Model Context Protocol server that integrates TIDAL music streaming with Claude. It lets LLMs search the TIDAL catalog, manage playlists, and access user favorites via the official TIDAL v2 API.

## Development Setup

**Prerequisites:** Python 3.10+, [uv](https://github.com/astral-sh/uv), TIDAL subscription, TIDAL developer credentials (Client ID + Secret from [developer.tidal.com](https://developer.tidal.com))

```bash
uv venv
source .venv/bin/activate
uv pip install --editable .
```

**Required environment variables:**
```bash
export TIDAL_CLIENT_ID="your-client-id"
export TIDAL_CLIENT_SECRET="your-client-secret"
# Optional:
export TIDAL_REDIRECT_PORT="18888"   # Port for OAuth PKCE callback (default: 18888)
export TIDAL_COUNTRY_CODE="US"       # Country code for catalog queries (default: US)
```

## Running

The server runs via MCP (stdio transport). For local testing:

```bash
TIDAL_CLIENT_ID=xxx TIDAL_CLIENT_SECRET=yyy uv run mcp run mcp_server/server.py
```

The server will fail fast at startup if `TIDAL_CLIENT_ID` or `TIDAL_CLIENT_SECRET` are not set.

## Architecture

Single-process design. The MCP server calls the official TIDAL v2 API directly via HTTP:

```
Claude (MCP client) ──stdio──▶ MCP Server (mcp_server/server.py)
                                    │
                                    │ HTTPS (requests library)
                                    ▼
                              TIDAL v2 API (openapi.tidal.com/v2)
                              (JSON:API format, OAuth2)
```

**mcp_server/server.py** — FastMCP server that registers 7 tools and initializes `TidalAuth` + `TidalClient` at module level. Tools call `TidalClient` methods directly.

**mcp_server/auth.py** — `TidalAuth` class handling two OAuth2 flows: Client Credentials (for catalog/search, no user login) and Authorization Code PKCE (for user operations like playlists and favorites). Persists tokens to `{tempdir}/tidal-mcp-tokens.json`.

**mcp_server/tidal_client.py** — `TidalClient` class wrapping all TIDAL v2 API calls (search, favorites, playlist CRUD). Handles JSON:API request/response format, pagination, and batch track addition (max 20 per request).

**mcp_server/jsonapi.py** — Utilities for parsing JSON:API responses: resolving included sideloads, cursor-based pagination, ISO 8601 duration parsing, and formatting track/playlist data into standardized output dicts.

## Key Design Decisions

- Uses the official TIDAL v2 API (`openapi.tidal.com/v2`) with JSON:API format instead of the unofficial `tidalapi` library.
- Search uses Client Credentials auth (no user login required). All other user operations use PKCE.
- Tokens are persisted to a temp file so sessions survive MCP server restarts.
- Playlist track addition is batched in groups of 20 (the API limit).
- No test suite or CI/CD is currently configured.
