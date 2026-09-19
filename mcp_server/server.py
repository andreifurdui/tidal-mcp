import os
from mcp.server.mcpserver import MCPServer
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

mcp = MCPServer("TIDAL MCP", version="0.1.0")

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
        result = client.add_tracks_to_playlist(
            playlist_id=playlist["id"], track_ids=str_ids
        )

        return {
            "status": "success",
            "message": f"Successfully created playlist '{title}' with {len(result['added'])} tracks.",
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
    4. Each track carries its 1-based "position" in the playlist, useful for
       reorder_playlist_tracks() and remove_tracks_from_playlist()

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
def update_tidal_playlist(
    playlist_id: str,
    title: Optional[str] = None,
    description: Optional[str] = None,
    access_type: Optional[str] = None,
) -> dict:
    """
    Updates a playlist's title, description and/or visibility.

    USE THIS TOOL WHENEVER A USER ASKS FOR:
    - "Rename my playlist to ..."
    - "Change the description of my playlist"
    - "Make my playlist public / unlisted"

    Only the fields you pass are changed; the others are left as they are.

    Args:
        playlist_id: The TIDAL ID of the playlist to update (required)
        title: New playlist title (1-250 characters)
        description: New description (up to 500 characters; pass "" to clear it)
        access_type: Playlist visibility: "PUBLIC" or "UNLISTED"

    Returns:
        A dictionary containing the status and the updated playlist.
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

    if title is not None and not title.strip():
        return {"status": "error", "message": "Playlist title cannot be empty."}

    try:
        playlist = client.update_playlist(
            playlist_id=playlist_id,
            name=title,
            description=description,
            access_type=access_type,
        )
        return {
            "status": "success",
            "message": f"Playlist '{playlist['title']}' was updated.",
            "playlist": playlist,
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Failed to update playlist: {str(e)}",
        }


@mcp.tool()
def add_tracks_to_playlist(
    playlist_id: str,
    track_ids: list,
    skip_duplicates: bool = True,
    before_track_id: Optional[str] = None,
) -> dict:
    """
    Adds tracks to an existing TIDAL playlist.

    USE THIS TOOL WHENEVER A USER ASKS FOR:
    - "Add these songs to my playlist"
    - "Put this track in my [playlist name] playlist"
    - "Insert this song before ... in my playlist"

    Use search_tracks() to find track IDs and get_user_playlists() to find the
    playlist ID. Tracks are appended to the end unless before_track_id is given.

    Args:
        playlist_id: The TIDAL ID of the playlist (required)
        track_ids: List of TIDAL track IDs to add, in the desired order (required)
        skip_duplicates: Skip tracks that are already in the playlist (default: True)
        before_track_id: Insert the tracks right before this track instead of at the end

    Returns:
        A dictionary with the tracks added and any that were skipped as duplicates.
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

    if not track_ids or not isinstance(track_ids, list):
        return {"status": "error", "message": "You must provide at least one track ID."}

    try:
        result = client.add_tracks_to_playlist(
            playlist_id=playlist_id,
            track_ids=[str(tid) for tid in track_ids],
            skip_duplicates=skip_duplicates,
            before_track_id=before_track_id,
        )
        message = f"Added {len(result['added'])} track(s) to the playlist."
        if result["skipped"]:
            message += f" Skipped {len(result['skipped'])} already present."
        return {
            "status": "success",
            "message": message,
            "added": result["added"],
            "skipped": result["skipped"],
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Failed to add tracks: {str(e)}",
        }


@mcp.tool()
def remove_tracks_from_playlist(playlist_id: str, track_ids: list) -> dict:
    """
    Removes tracks from a TIDAL playlist.

    USE THIS TOOL WHENEVER A USER ASKS FOR:
    - "Remove this song from my playlist"
    - "Take these tracks out of my [playlist name] playlist"
    - "Delete the duplicates from my playlist"

    Use get_playlist_tracks() first to find the track IDs in the playlist.
    If a track appears more than once, every occurrence is removed.

    Args:
        playlist_id: The TIDAL ID of the playlist (required)
        track_ids: List of TIDAL track IDs to remove (required)

    Returns:
        A dictionary with how many entries were removed and which track IDs
        were not found in the playlist.
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

    if not track_ids or not isinstance(track_ids, list):
        return {"status": "error", "message": "You must provide at least one track ID."}

    try:
        result = client.remove_tracks_from_playlist(
            playlist_id=playlist_id, track_ids=[str(tid) for tid in track_ids]
        )
        message = f"Removed {result['removed']} track(s) from the playlist."
        if result["not_found"]:
            message += f" {len(result['not_found'])} track ID(s) were not in the playlist."
        return {
            "status": "success",
            "message": message,
            "removed": result["removed"],
            "not_found": result["not_found"],
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Failed to remove tracks: {str(e)}",
        }


@mcp.tool()
def reorder_playlist_tracks(
    playlist_id: str, track_ids: list, before_track_id: Optional[str] = None
) -> dict:
    """
    Moves tracks to a new position within a TIDAL playlist.

    USE THIS TOOL WHENEVER A USER ASKS FOR:
    - "Move this song to the top / end of my playlist"
    - "Put track X before track Y"
    - "Reorder my playlist so that ..."

    Use get_playlist_tracks() first to see the current order (each track has a
    "position"). The given tracks are moved, keeping the order you list them
    in, to sit right before before_track_id. Leave before_track_id empty to
    move them to the end of the playlist. To move tracks to the top, pass the
    current first track as before_track_id.

    Args:
        playlist_id: The TIDAL ID of the playlist (required)
        track_ids: TIDAL track IDs to move, in their desired relative order (required)
        before_track_id: The track the moved tracks should come before; omit to move to the end

    Returns:
        A dictionary with the moved track IDs and the playlist's new track order.
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

    if not track_ids or not isinstance(track_ids, list):
        return {"status": "error", "message": "You must provide at least one track ID to move."}

    try:
        result = client.move_tracks_in_playlist(
            playlist_id=playlist_id,
            track_ids=[str(tid) for tid in track_ids],
            before_track_id=before_track_id,
        )
        return {
            "status": "success",
            "message": f"Moved {len(result['moved'])} track(s).",
            "moved": result["moved"],
            "order": result["order"],
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Failed to reorder tracks: {str(e)}",
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
