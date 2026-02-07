import os
from mcp.server.fastmcp import FastMCP
from typing import Optional, List

from auth import TidalAuth
from tidal_client import TidalClient

# ── Configuration ────────────────────────────────────────────────────

CLIENT_ID = os.environ.get("TIDAL_CLIENT_ID")
CLIENT_SECRET = os.environ.get("TIDAL_CLIENT_SECRET")
REDIRECT_PORT = int(os.environ.get("TIDAL_REDIRECT_PORT", "18888"))
COUNTRY_CODE = os.environ.get("TIDAL_COUNTRY_CODE", "US")

if not CLIENT_ID or not CLIENT_SECRET:
    raise ValueError(
        "TIDAL_CLIENT_ID and TIDAL_CLIENT_SECRET environment variables are required. "
        "Register an app at https://developer.tidal.com to get credentials."
    )

# ── Initialize ───────────────────────────────────────────────────────

mcp = FastMCP("TIDAL MCP")

auth = TidalAuth(
    client_id=CLIENT_ID,
    client_secret=CLIENT_SECRET,
    redirect_uri=f"http://localhost:{REDIRECT_PORT}/callback",
)

client = TidalClient(auth=auth, country_code=COUNTRY_CODE)

print(f"TIDAL MCP initialized (country: {COUNTRY_CODE})")

# ── MCP Tools ────────────────────────────────────────────────────────


@mcp.tool()
def tidal_login() -> dict:
    """
    Authenticate with TIDAL through browser login flow.
    This will open a browser window for the user to log in to their TIDAL account.

    Returns:
        A dictionary containing authentication status and user information if successful.
    """
    try:
        return client.login()
    except Exception as e:
        return {
            "status": "error",
            "message": f"Failed to authenticate with TIDAL: {str(e)}",
        }


@mcp.tool()
def search_tracks(query: str, limit: int = 10) -> dict:
    """
    Search for tracks on TIDAL by name, artist, or any search query.

    USE THIS TOOL WHENEVER A USER ASKS FOR:
    - "Search for [track name]"
    - "Find [artist] tracks on TIDAL"
    - "Look up [song] on TIDAL"
    - "Get the TIDAL ID for [track]"
    - Any request to find music or look up tracks on TIDAL

    This is the primary tool for finding track IDs needed to create playlists.
    Does NOT require user login — works with catalog access only.

    When processing the results of this tool:
    1. Present the search results in a clear format with track name, artist, album
    2. Always include the track ID and TIDAL URL for each result
    3. If the user is building a playlist, collect the track IDs from results
    4. If no results are found, suggest refining the search query

    Args:
        query: Search query (track name, artist name, or combination like "Bohemian Rhapsody Queen")
        limit: Maximum number of results to return (default: 10, max: 25)

    Returns:
        A dictionary containing matching tracks with their IDs, titles, artists, albums, and URLs.
    """
    if not query or not query.strip():
        return {
            "status": "error",
            "message": "Search query cannot be empty.",
        }

    try:
        tracks = client.search_tracks(query=query.strip(), limit=limit)
        return {
            "status": "success",
            "query": query.strip(),
            "tracks": tracks,
            "total_count": len(tracks),
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Search failed: {str(e)}",
        }


@mcp.tool()
def get_favorite_tracks(limit: int = 20) -> dict:
    """
    Retrieves tracks from the user's TIDAL account favorites.

    USE THIS TOOL WHENEVER A USER ASKS FOR:
    - "What are my favorite tracks?"
    - "Show me my TIDAL favorites"
    - "What music do I have saved?"
    - "Get my favorite songs"
    - Any request to view their saved/favorite tracks

    Args:
        limit: Maximum number of tracks to retrieve (default: 20, max: 50).

    Returns:
        A dictionary containing track information including track ID, title, artist, album, and duration.
    """
    if not auth.is_user_authenticated():
        return {
            "status": "error",
            "message": "You need to login to TIDAL first. Please use tidal_login().",
        }

    try:
        tracks = client.get_favorite_tracks(limit=limit)
        return {
            "status": "success",
            "tracks": tracks,
            "total_count": len(tracks),
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Failed to retrieve favorite tracks: {str(e)}",
        }


@mcp.tool()
def create_tidal_playlist(
    title: str, track_ids: list, description: str = ""
) -> dict:
    """
    Creates a new TIDAL playlist with the specified tracks.

    USE THIS TOOL WHENEVER A USER ASKS FOR:
    - "Create a playlist with these songs"
    - "Make a TIDAL playlist"
    - "Save these tracks to a playlist"
    - Any request to create a new playlist in their TIDAL account

    NAMING CONVENTION GUIDANCE:
    When suggesting or creating a playlist, first check the user's existing playlists
    using get_user_playlists() to understand their naming preferences.

    When processing the results of this tool:
    1. Confirm the playlist was created successfully
    2. Provide the playlist title, number of tracks added, and URL
    3. Always include the direct TIDAL URL
    4. Suggest that the user can now access this playlist in their TIDAL account

    Args:
        title: The name of the playlist to create
        track_ids: List of TIDAL track IDs to add to the playlist
        description: Optional description for the playlist

    Returns:
        A dictionary containing the status and details of the created playlist.
    """
    if not auth.is_user_authenticated():
        return {
            "status": "error",
            "message": "You need to login to TIDAL first. Please use tidal_login().",
        }

    if not title:
        return {"status": "error", "message": "Playlist title cannot be empty."}

    if not track_ids or not isinstance(track_ids, list) or len(track_ids) == 0:
        return {
            "status": "error",
            "message": "You must provide at least one track ID.",
        }

    try:
        # Create the playlist
        playlist = client.create_playlist(name=title, description=description)

        # Add tracks
        str_ids = [str(tid) for tid in track_ids]
        client.add_tracks_to_playlist(
            playlist_id=playlist["id"], track_ids=str_ids
        )

        return {
            "status": "success",
            "message": f"Successfully created playlist '{title}' with {len(track_ids)} tracks.",
            "playlist": playlist,
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Failed to create playlist: {str(e)}",
        }


@mcp.tool()
def get_user_playlists() -> dict:
    """
    Fetches the user's playlists from their TIDAL account.

    USE THIS TOOL WHENEVER A USER ASKS FOR:
    - "Show me my playlists"
    - "List my TIDAL playlists"
    - "What playlists do I have?"
    - Any request to view or list their TIDAL playlists

    When processing the results of this tool:
    1. Present the playlists in a clear, organized format
    2. Include key information like title, track count, and the TIDAL URL
    3. Mention when each playlist was last updated if available

    Returns:
        A dictionary containing the user's playlists sorted by last updated date.
    """
    if not auth.is_user_authenticated():
        return {
            "status": "error",
            "message": "You need to login to TIDAL first. Please use tidal_login().",
        }

    try:
        playlists = client.get_user_playlists()
        return {
            "status": "success",
            "playlists": playlists,
            "playlist_count": len(playlists),
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Failed to retrieve playlists: {str(e)}",
        }


@mcp.tool()
def get_playlist_tracks(playlist_id: str, limit: int = 100) -> dict:
    """
    Retrieves tracks from a specified TIDAL playlist.

    USE THIS TOOL WHENEVER A USER ASKS FOR:
    - "Show me the songs in my playlist"
    - "What tracks are in my [playlist name] playlist?"
    - "List the songs from my playlist"
    - Any request to see what songs/tracks are in a specific playlist

    When processing the results of this tool:
    1. Present the tracks in a clear, organized format
    2. Include track name, artist, album, and TIDAL URL
    3. Mention the total number of tracks

    Args:
        playlist_id: The TIDAL ID of the playlist to retrieve (required)
        limit: Maximum number of tracks to retrieve (default: 100)

    Returns:
        A dictionary containing the tracks in the playlist.
    """
    if not auth.is_user_authenticated():
        return {
            "status": "error",
            "message": "You need to login to TIDAL first. Please use tidal_login().",
        }

    if not playlist_id:
        return {
            "status": "error",
            "message": "A playlist ID is required. Use get_user_playlists() to find playlist IDs.",
        }

    try:
        tracks = client.get_playlist_tracks(
            playlist_id=playlist_id, limit=limit
        )
        return {
            "status": "success",
            "tracks": tracks,
            "track_count": len(tracks),
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Failed to retrieve playlist tracks: {str(e)}",
        }


@mcp.tool()
def delete_tidal_playlist(playlist_id: str) -> dict:
    """
    Deletes a TIDAL playlist by its ID.

    USE THIS TOOL WHENEVER A USER ASKS FOR:
    - "Delete my playlist"
    - "Remove a playlist from my TIDAL account"
    - Any request to delete or remove a TIDAL playlist

    Args:
        playlist_id: The TIDAL ID of the playlist to delete (required)

    Returns:
        A dictionary containing the status of the playlist deletion.
    """
    if not auth.is_user_authenticated():
        return {
            "status": "error",
            "message": "You need to login to TIDAL first. Please use tidal_login().",
        }

    if not playlist_id:
        return {
            "status": "error",
            "message": "A playlist ID is required. Use get_user_playlists() to find playlist IDs.",
        }

    try:
        client.delete_playlist(playlist_id=playlist_id)
        return {
            "status": "success",
            "message": f"Playlist {playlist_id} was successfully deleted.",
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Failed to delete playlist: {str(e)}",
        }
