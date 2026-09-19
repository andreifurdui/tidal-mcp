"""
TIDAL v2 API client.

Wraps all TIDAL API calls behind a clean interface used by the MCP tools.
Uses the official v2 API at https://openapi.tidal.com/v2 with JSON:API format.
"""

import requests
from typing import Optional

from auth import TidalAuth
from jsonapi import (
    build_include_index,
    format_playlist,
    format_track,
    get_next_cursor,
    parse_collection_response,
    parse_single_response,
)


BASE_URL = "https://openapi.tidal.com/v2"
JSONAPI_CONTENT_TYPE = "application/vnd.api+json"

# Maximum tracks per add-to-playlist request (API limit)
MAX_TRACKS_PER_BATCH = 20


class TidalClient:
    """Client for the official TIDAL v2 API."""

    def __init__(self, auth: TidalAuth, country_code: str = "US"):
        self.auth = auth
        self.country_code = country_code
        self._cached_user_id: Optional[str] = None

    # ── Headers ──────────────────────────────────────────────────────

    def _catalog_headers(self) -> dict:
        """Headers for catalog operations (Client Credentials)."""
        return {
            "Accept": JSONAPI_CONTENT_TYPE,
            "Authorization": f"Bearer {self.auth.get_client_token()}",
            "Content-Type": JSONAPI_CONTENT_TYPE,
        }

    def _user_headers(self) -> dict:
        """Headers for user operations (PKCE token)."""
        return {
            "Accept": JSONAPI_CONTENT_TYPE,
            "Authorization": f"Bearer {self.auth.get_user_token()}",
            "Content-Type": JSONAPI_CONTENT_TYPE,
        }

    # ── User Info ────────────────────────────────────────────────────

    def get_user_id(self) -> str:
        """Get the authenticated user's TIDAL user ID.

        Caches the result after the first call. Also tries the auth module's
        cached user_id first (set during login).

        Returns:
            The user ID as a string.

        Raises:
            RuntimeError: If user is not authenticated or request fails.
        """
        # Check cached values
        if self._cached_user_id:
            return self._cached_user_id
        if self.auth.user_id:
            self._cached_user_id = self.auth.user_id
            return self._cached_user_id

        # Fetch from API
        response = requests.get(
            f"{BASE_URL}/users/me",
            headers=self._user_headers(),
        )

        if response.status_code != 200:
            raise RuntimeError(
                f"Failed to get user info: {response.status_code} {response.text}"
            )

        data = response.json().get("data", {})
        user_id = str(data.get("id", ""))
        if not user_id:
            raise RuntimeError("Could not extract user ID from /users/me response")

        self._cached_user_id = user_id
        self.auth.user_id = user_id
        return user_id

    # ── Auth ─────────────────────────────────────────────────────────

    def login(self, fn_print=print) -> dict:
        """Authenticate with TIDAL via PKCE flow.

        Returns:
            A dict with status, message, and user_id on success.
        """
        success = self.auth.login_pkce(fn_print=fn_print)
        if not success:
            return {
                "status": "error",
                "message": "TIDAL authentication failed.",
            }

        # Fetch user ID if we don't have it yet
        try:
            user_id = self.get_user_id()
        except RuntimeError:
            user_id = self.auth.user_id or "unknown"

        return {
            "status": "success",
            "message": "Successfully authenticated with TIDAL.",
            "user_id": user_id,
        }

    def auth_status(self) -> dict:
        """Check if the user is authenticated.

        Returns:
            A dict with authenticated boolean and user info.
        """
        authenticated = self.auth.is_user_authenticated()
        result = {"authenticated": authenticated}

        if authenticated:
            result["user_id"] = self.auth.user_id or self._cached_user_id
            result["message"] = "Valid TIDAL session"
        else:
            result["message"] = "Not authenticated. Please login first."

        return result

    # ── Search ───────────────────────────────────────────────────────

    def search_tracks(self, query: str, limit: int = 10) -> list[dict]:
        """Search for tracks on TIDAL.

        Uses Client Credentials — no user login required.

        Args:
            query: Search query (track name, artist name, etc.)
            limit: Maximum number of results (clamped to 1-25).

        Returns:
            A list of track dicts with id, title, artist, album, duration, url.

        Raises:
            RuntimeError: If the search request fails.
        """
        limit = max(1, min(25, limit))

        # The search text is passed as filter[query]; the response is a single
        # searchResults resource whose "tracks" relationship lists track ids,
        # with full track/artist/album objects sideloaded in "included".
        response = requests.get(
            f"{BASE_URL}/searchResults",
            params={
                "filter[query]": query,
                "countryCode": self.country_code,
                "include": "tracks,tracks.artists,tracks.albums",
            },
            headers=self._catalog_headers(),
        )

        if response.status_code != 200:
            raise RuntimeError(
                f"Search failed: {response.status_code} {response.text}"
            )

        resp_json = response.json()
        results = resp_json.get("data", [])
        if not results:
            return []

        # Promote the track resource identifiers to the top-level "data" so
        # parse_collection_response can resolve them from "included".
        track_refs = (
            results[0].get("relationships", {}).get("tracks", {}).get("data", [])
        )
        resp_json["data"] = [d for d in track_refs if d.get("type") == "tracks"]
        tracks, _ = parse_collection_response(resp_json, resource_type="tracks")
        return tracks[:limit]

    # ── Favorites ────────────────────────────────────────────────────

    def get_favorite_tracks(self, limit: int = 20) -> list[dict]:
        """Get the user's favorite tracks from their collection.

        Args:
            limit: Maximum number of tracks to retrieve (clamped to 1-50).

        Returns:
            A list of track dicts.

        Raises:
            RuntimeError: If not authenticated or request fails.
        """
        limit = max(1, min(50, limit))
        all_tracks: list[dict] = []
        cursor = None

        # Favorites live under the userCollectionTracks resource; "me" is the
        # API's alias for the authenticated user. Pages are a fixed size
        # (page[limit] is ignored), so follow the cursor until we have enough.
        while len(all_tracks) < limit:
            params = {
                "countryCode": self.country_code,
                "include": "items.artists,items.albums",
            }
            if cursor:
                params["page[cursor]"] = cursor

            response = requests.get(
                f"{BASE_URL}/userCollectionTracks/me/relationships/items",
                params=params,
                headers=self._user_headers(),
            )

            if response.status_code != 200:
                raise RuntimeError(
                    f"Failed to get favorites: {response.status_code} {response.text}"
                )

            resp_json = response.json()
            tracks, _ = parse_collection_response(resp_json, resource_type="tracks")
            all_tracks.extend(tracks)

            cursor = get_next_cursor(resp_json)
            if not cursor or not tracks:
                break

        return all_tracks[:limit]

    # ── Playlists ────────────────────────────────────────────────────

    def create_playlist(self, name: str, description: str = "") -> dict:
        """Create a new playlist in the user's TIDAL account.

        Args:
            name: The playlist name.
            description: Optional playlist description.

        Returns:
            A formatted playlist dict with id, title, url, etc.

        Raises:
            RuntimeError: If not authenticated or request fails.
        """
        payload = {
            "data": {
                "type": "playlists",
                "attributes": {
                    "name": name,
                    "description": description,
                },
            }
        }

        response = requests.post(
            f"{BASE_URL}/playlists",
            json=payload,
            headers=self._user_headers(),
        )

        if response.status_code not in (200, 201):
            raise RuntimeError(
                f"Failed to create playlist: {response.status_code} {response.text}"
            )

        resource, _ = parse_single_response(response.json())
        return format_playlist(resource)

    def add_tracks_to_playlist(
        self, playlist_id: str, track_ids: list[str]
    ) -> bool:
        """Add tracks to an existing playlist.

        Automatically batches requests in groups of 20 (the API limit).

        Args:
            playlist_id: The TIDAL playlist ID.
            track_ids: List of track IDs to add.

        Returns:
            True if all tracks were added successfully.

        Raises:
            RuntimeError: If not authenticated or any batch request fails.
        """
        for i in range(0, len(track_ids), MAX_TRACKS_PER_BATCH):
            batch = track_ids[i : i + MAX_TRACKS_PER_BATCH]
            payload = {
                "data": [
                    {"id": str(tid), "type": "tracks"} for tid in batch
                ]
            }

            response = requests.post(
                f"{BASE_URL}/playlists/{playlist_id}/relationships/items",
                json=payload,
                headers=self._user_headers(),
            )

            if response.status_code not in (200, 201, 204):
                raise RuntimeError(
                    f"Failed to add tracks (batch {i // MAX_TRACKS_PER_BATCH + 1}): "
                    f"{response.status_code} {response.text}"
                )

        return True

    def get_user_playlists(self) -> list[dict]:
        """Get all playlists owned by the authenticated user.

        Returns:
            A list of formatted playlist dicts, sorted by last_updated (desc).

        Raises:
            RuntimeError: If not authenticated or request fails.
        """
        user_id = self.get_user_id()

        response = requests.get(
            f"{BASE_URL}/playlists",
            params={
                "countryCode": self.country_code,
                "filter[owners.id]": user_id,
            },
            headers=self._user_headers(),
        )

        if response.status_code != 200:
            raise RuntimeError(
                f"Failed to get playlists: {response.status_code} {response.text}"
            )

        playlists, _ = parse_collection_response(
            response.json(), resource_type="playlists"
        )

        # Sort by last_updated descending
        playlists.sort(key=lambda p: p.get("last_updated", ""), reverse=True)
        return playlists

    def get_playlist_tracks(
        self, playlist_id: str, limit: int = 100
    ) -> list[dict]:
        """Get tracks from a specific playlist.

        Args:
            playlist_id: The TIDAL playlist ID.
            limit: Maximum number of tracks to retrieve (clamped to 1-100).

        Returns:
            A list of track dicts.

        Raises:
            RuntimeError: If request fails.
        """
        limit = max(1, min(100, limit))
        all_tracks = []
        cursor = None

        while len(all_tracks) < limit:
            # Nested includes bring artist/album names along; page[limit]
            # is ignored by the API, so pages come back at a fixed size.
            params = {
                "countryCode": self.country_code,
                "include": "items.artists,items.albums",
            }
            if cursor:
                params["page[cursor]"] = cursor

            response = requests.get(
                f"{BASE_URL}/playlists/{playlist_id}/relationships/items",
                params=params,
                headers=self._user_headers(),
            )

            if response.status_code == 404:
                raise RuntimeError(
                    f"Playlist with ID {playlist_id} not found."
                )
            if response.status_code != 200:
                raise RuntimeError(
                    f"Failed to get playlist tracks: {response.status_code} {response.text}"
                )

            resp_json = response.json()
            tracks, _ = parse_collection_response(resp_json, resource_type="tracks")
            all_tracks.extend(tracks)

            cursor = get_next_cursor(resp_json)
            if not cursor:
                break

        return all_tracks[:limit]

    def delete_playlist(self, playlist_id: str) -> bool:
        """Delete a playlist from the user's TIDAL account.

        Args:
            playlist_id: The TIDAL playlist ID to delete.

        Returns:
            True if deletion succeeded.

        Raises:
            RuntimeError: If not authenticated or request fails.
        """
        response = requests.delete(
            f"{BASE_URL}/playlists/{playlist_id}",
            headers=self._user_headers(),
        )

        if response.status_code == 404:
            raise RuntimeError(
                f"Playlist with ID {playlist_id} not found."
            )
        if response.status_code not in (200, 204):
            raise RuntimeError(
                f"Failed to delete playlist: {response.status_code} {response.text}"
            )

        return True
