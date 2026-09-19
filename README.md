# TIDAL MCP: My Custom Picks 🌟🎧

![Demo: Music Recommendations in Action](./assets/tidal_mcp_demo.gif)

TIDAL MCP lets you manage your TIDAL music library through natural conversation with an LLM. Search the catalog, build playlists, and browse your favorites — all from chat.

With TIDAL MCP, you can ask for things like:
> *"Search for Radiohead tracks on TIDAL and create a playlist with the top results."*
>
> *"Find 'Bohemian Rhapsody' by Queen and add it to my playlist."*

The LLM searches the TIDAL catalog, collects track IDs, and creates playlists directly in your account.

<a href="https://glama.ai/mcp/servers/@yuhuacheng/tidal-mcp">
  <img width="400" height="200" src="https://glama.ai/mcp/servers/@yuhuacheng/tidal-mcp/badge" alt="TIDAL: My Custom Picks MCP server" />
</a>

## Features

- 🔍 **Track Search**: Search the TIDAL catalog by track name, artist, or any query — no login required
- ၊၊||၊ **Playlist Management**: Create, view, and manage your TIDAL playlists
- ⭐ **Favorites Access**: Browse your saved favorite tracks

## Quick Start

### Prerequisites

- Python 3.10+
- [uv](https://github.com/astral-sh/uv) (Python package manager)
- TIDAL subscription
- TIDAL developer credentials (Client ID + Client Secret from [developer.tidal.com](https://developer.tidal.com))

### Installation

1. Clone this repository:
   ```bash
   git clone https://github.com/yuhuacheng/tidal-mcp.git
   cd tidal-mcp
   ```

2. Create a virtual environment and install dependencies using uv:
   ```bash
   uv venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   ```

3. Install the package with all dependencies from the pyproject.toml file:
   ```bash
   uv pip install --editable .
   ```

### TIDAL Developer Setup

1. Go to [developer.tidal.com](https://developer.tidal.com) and log in
2. Create a new app in the Dashboard
3. Note your **Client ID** and **Client Secret**
4. Set the redirect URI to `http://localhost:18888/callback` (or your custom port)

## MCP Client Configuration

### Claude Desktop Configuration

To add this MCP server to Claude Desktop, update the MCP configuration file:

```json
{
  "mcpServers": {
    "TIDAL Integration": {
      "command": "/path/to/your/uv",
      "env": {
        "TIDAL_CLIENT_ID": "your-client-id",
        "TIDAL_CLIENT_SECRET": "your-client-secret"
      },
      "args": [
        "run",
        "--with",
        "requests",
        "--with",
        "mcp[cli]<2",
        "mcp",
        "run",
        "/path/to/your/project/tidal-mcp/mcp_server/server.py"
      ]
    }
  }
}
```

### Environment Variables

| Variable | Required | Default | Purpose |
|----------|----------|---------|---------|
| `TIDAL_CLIENT_ID` | Yes | — | From developer.tidal.com dashboard |
| `TIDAL_CLIENT_SECRET` | Yes | — | From developer.tidal.com dashboard |
| `TIDAL_REDIRECT_PORT` | No | `18888` | Port for OAuth PKCE callback server |
| `TIDAL_COUNTRY_CODE` | No | `US` | Country code for catalog queries |

### Steps to Install MCP Configuration

1. Open Claude Desktop
2. Go to Settings > Developer
3. Click on "Edit Config"
4. Paste the modified JSON configuration
5. Save the configuration
6. Restart Claude Desktop

## Suggested Prompt Starters
Once configured, you can interact with your TIDAL account through an LLM by asking questions like:

- *"Search for Radiohead tracks on TIDAL"*
- *"Find 'Bohemian Rhapsody' by Queen and create a playlist with it"*
- *"Show me my favorite tracks"*
- *"Create a playlist called 'Road Trip' with these songs: ..."*

## Available Tools

The TIDAL MCP integration provides the following tools:

- `tidal_login`: Authenticate with TIDAL through browser login flow (required for user operations)
- `search_tracks`: Search the TIDAL catalog by track name, artist, or query (no login needed)
- `get_favorite_tracks`: Retrieve your favorite tracks from TIDAL
- `create_tidal_playlist`: Create a new playlist in your TIDAL account
- `get_user_playlists`: List all your playlists on TIDAL
- `get_playlist_tracks`: Retrieve all tracks from a specific playlist
- `delete_tidal_playlist`: Delete a playlist from your TIDAL account

## License

[MIT License](LICENSE)

## Acknowledgements

- [Model Context Protocol (MCP)](https://github.com/modelcontextprotocol/python-sdk)
- [TIDAL Developer Platform](https://developer.tidal.com)
