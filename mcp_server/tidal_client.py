"""
TIDAL v2 API client.

Wraps all TIDAL API calls behind a clean interface used by the MCP tools.
Uses the official v2 API at https://openapi.tidal.com/v2 with JSON:API format.
"""

import time

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

# Maximum items per add/remove-from-playlist request (API limit is 50)
MAX_TRACKS_PER_BATCH = 20
# Maximum items per reorder request (API limit)
MAX_REORDER_PER_BATCH = 20
# How many times to retry a request that was rate limited (HTTP 429)
MAX_RATE_LIMIT_RETRIES = 5


def _request(method: str, url: str, **kwargs) -> requests.Response:
    """Issue an HTTP request, retrying on 429 using the Retry-After header.

    Multi-step playlist edits issue several requests in quick succession,
    which the TIDAL API rate limits aggressively.
    """
    for _ in range(MAX_RATE_LIMIT_RETRIES):
        response = requests.request(method, url, **kwargs)
        if response.status_code != 429:
            return response
        try:
            wait = float(response.headers.get("Retry-After", "1"))
        except ValueError:
            wait = 1.0
        time.sleep(min(wait, 10.0) + 0.25)
    return response


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
        self,
        playlist_id: str,
        track_ids: list[str],
        skip_duplicates: bool = False,
        before_track_id: Optional[str] = None,
    ) -> dict:
        """Add tracks to an existing playlist.

        Automatically batches requests in groups of MAX_TRACKS_PER_BATCH.

        Args:
            playlist_id: The TIDAL playlist ID.
            track_ids: List of track IDs to add, in the order they should appear.
            skip_duplicates: If True, tracks already in the playlist are skipped
                instead of being added a second time.
            before_track_id: If given, insert the tracks immediately before the
                first occurrence of this track; otherwise append to the end.

        Returns:
            A dict with "added" (list of track ids) and "skipped" (list of
            track ids already present, when skip_duplicates is True).

        Raises:
            RuntimeError: If not authenticated, the anchor track is not in the
                playlist, or any batch request fails.
        """
        meta: dict = {}
        if skip_duplicates:
            meta["onDuplicates"] = "SKIP"
        if before_track_id is not None:
            anchor = self._find_item(playlist_id, before_track_id)
            meta["positionBefore"] = anchor["item_id"]

        added: list[str] = []
        skipped: list[str] = []
        for i in range(0, len(track_ids), MAX_TRACKS_PER_BATCH):
            batch = [str(tid) for tid in track_ids[i : i + MAX_TRACKS_PER_BATCH]]
            payload: dict = {"data": [{"id": tid, "type": "tracks"} for tid in batch]}
            if meta:
                payload["meta"] = meta

            response = _request(
                "POST",
                f"{BASE_URL}/playlists/{playlist_id}/relationships/items",
                json=payload,
                headers=self._user_headers(),
            )

            if response.status_code not in (200, 201, 204):
                raise RuntimeError(
                    f"Failed to add tracks (batch {i // MAX_TRACKS_PER_BATCH + 1}): "
                    f"{response.status_code} {response.text}"
                )

            body = response.json() if response.text else {}
            batch_skipped = [
                str(item.get("id"))
                for item in (body.get("meta") or {}).get("skipped", [])
                if item.get("id") is not None
            ]
            skipped.extend(batch_skipped)
            added.extend(tid for tid in batch if tid not in batch_skipped)

        return {"added": added, "skipped": skipped}

    # ── Playlist editing ─────────────────────────────────────────────

    def _get_playlist_items(self, playlist_id: str) -> list[dict]:
        """Fetch every item in a playlist as {track_id, item_id, position}.

        The API identifies playlist entries by a per-entry item id (a track
        can appear more than once), which remove/reorder operations require.
        """
        items: list[dict] = []
        cursor = None
        while True:
            params = {"countryCode": self.country_code}
            if cursor:
                params["page[cursor]"] = cursor

            response = _request(
                "GET",
                f"{BASE_URL}/playlists/{playlist_id}/relationships/items",
                params=params,
                headers=self._user_headers(),
            )
            if response.status_code == 404:
                raise RuntimeError(f"Playlist with ID {playlist_id} not found.")
            if response.status_code != 200:
                raise RuntimeError(
                    f"Failed to get playlist items: {response.status_code} {response.text}"
                )

            resp_json = response.json()
            for entry in resp_json.get("data", []):
                if entry.get("type") != "tracks":
                    continue
                items.append({
                    "track_id": str(entry.get("id", "")),
                    "item_id": (entry.get("meta") or {}).get("itemId"),
                    "position": len(items) + 1,
                })

            cursor = get_next_cursor(resp_json)
            if not cursor or not resp_json.get("data"):
                break
        return items

    def _find_item(self, playlist_id: str, track_id: str) -> dict:
        """Return the first playlist entry for track_id, or raise."""
        for item in self._get_playlist_items(playlist_id):
            if item["track_id"] == str(track_id):
                return item
        raise RuntimeError(
            f"Track {track_id} is not in playlist {playlist_id}."
        )

    def update_playlist(
        self,
        playlist_id: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        access_type: Optional[str] = None,
    ) -> dict:
        """Update a playlist's name, description and/or visibility.

        Only the attributes given are changed.

        Args:
            playlist_id: The TIDAL playlist ID.
            name: New playlist name (1-250 chars).
            description: New description (max 500 chars, may be empty).
            access_type: "PUBLIC" or "UNLISTED".

        Returns:
            The updated playlist as a formatted dict.

        Raises:
            ValueError: If nothing to update or access_type is invalid.
            RuntimeError: If not authenticated or the request fails.
        """
        attributes: dict = {}
        if name is not None:
            attributes["name"] = name
        if description is not None:
            attributes["description"] = description
        if access_type is not None:
            access_type = access_type.upper()
            if access_type not in ("PUBLIC", "UNLISTED"):
                raise ValueError("access_type must be 'PUBLIC' or 'UNLISTED'.")
            attributes["accessType"] = access_type
        if not attributes:
            raise ValueError("Nothing to update: provide a name, description or access_type.")

        response = _request(
            "PATCH",
            f"{BASE_URL}/playlists/{playlist_id}",
            json={"data": {"id": playlist_id, "type": "playlists", "attributes": attributes}},
            headers=self._user_headers(),
        )
        if response.status_code == 404:
            raise RuntimeError(f"Playlist with ID {playlist_id} not found.")
        if response.status_code != 200:
            raise RuntimeError(
                f"Failed to update playlist: {response.status_code} {response.text}"
            )

        resource, _ = parse_single_response(response.json())
        return format_playlist(resource)

    def remove_tracks_from_playlist(
        self, playlist_id: str, track_ids: list[str]
    ) -> dict:
        """Remove tracks from a playlist.

        Every occurrence of each given track is removed. Track ids that are
        not in the playlist are reported rather than treated as errors.

        Args:
            playlist_id: The TIDAL playlist ID.
            track_ids: Track IDs to remove.

        Returns:
            A dict with "removed" (number of entries removed) and
            "not_found" (track ids that were not in the playlist).

        Raises:
            RuntimeError: If not authenticated or a request fails.
        """
        wanted = {str(tid) for tid in track_ids}
        items = self._get_playlist_items(playlist_id)
        targets = [it for it in items if it["track_id"] in wanted and it["item_id"]]
        not_found = sorted(wanted - {it["track_id"] for it in targets})

        for i in range(0, len(targets), MAX_TRACKS_PER_BATCH):
            batch = targets[i : i + MAX_TRACKS_PER_BATCH]
            payload = {
                "data": [
                    {"id": it["track_id"], "type": "tracks", "meta": {"itemId": it["item_id"]}}
                    for it in batch
                ]
            }
            response = _request(
                "DELETE",
                f"{BASE_URL}/playlists/{playlist_id}/relationships/items",
                json=payload,
                headers=self._user_headers(),
            )
            if response.status_code not in (200, 204):
                raise RuntimeError(
                    f"Failed to remove tracks (batch {i // MAX_TRACKS_PER_BATCH + 1}): "
                    f"{response.status_code} {response.text}"
                )

        return {"removed": len(targets), "not_found": not_found}

    def _reorder_items(self, playlist_id: str, items: list[dict], before_item_id: str) -> None:
        """Move the given entries (keeping their order) before before_item_id."""
        for i in range(0, len(items), MAX_REORDER_PER_BATCH):
            batch = items[i : i + MAX_REORDER_PER_BATCH]
            payload = {
                "data": [
                    {"id": it["track_id"], "type": "tracks", "meta": {"itemId": it["item_id"]}}
                    for it in batch
                ],
                "meta": {"positionBefore": before_item_id},
            }
            response = _request(
                "PATCH",
                f"{BASE_URL}/playlists/{playlist_id}/relationships/items",
                json=payload,
                headers=self._user_headers(),
            )
            if response.status_code not in (200, 204):
                raise RuntimeError(
                    f"Failed to reorder tracks (batch {i // MAX_REORDER_PER_BATCH + 1}): "
                    f"{response.status_code} {response.text}"
                )

    def move_tracks_in_playlist(
        self,
        playlist_id: str,
        track_ids: list[str],
        before_track_id: Optional[str] = None,
    ) -> dict:
        """Move tracks within a playlist.

        The given tracks are moved, in the order given, to sit immediately
        before ``before_track_id``; when that is None they are moved to the
        end of the playlist. Only the first occurrence of each track is moved.

        Args:
            playlist_id: The TIDAL playlist ID.
            track_ids: Track IDs to move, in their desired relative order.
            before_track_id: Track that the moved tracks should precede, or
                None to move them to the end.

        Returns:
            A dict with "moved" (track ids moved) and "order" (the playlist's
            track ids after the move).

        Raises:
            ValueError: If the anchor track is also one of the tracks to move.
            RuntimeError: If a track is not in the playlist or a request fails.
        """
        items = self._get_playlist_items(playlist_id)
        by_track: dict[str, dict] = {}
        for it in items:
            by_track.setdefault(it["track_id"], it)

        moving: list[dict] = []
        for tid in track_ids:
            tid = str(tid)
            if tid not in by_track:
                raise RuntimeError(f"Track {tid} is not in playlist {playlist_id}.")
            if by_track[tid] not in moving:
                moving.append(by_track[tid])
        if not moving:
            raise ValueError("No tracks to move.")
        moving_ids = {it["item_id"] for it in moving}

        if before_track_id is not None:
            before_track_id = str(before_track_id)
            if before_track_id not in by_track:
                raise RuntimeError(
                    f"Track {before_track_id} is not in playlist {playlist_id}."
                )
            anchor = by_track[before_track_id]
            if anchor["item_id"] in moving_ids:
                raise ValueError(
                    "before_track_id cannot be one of the tracks being moved."
                )
            self._reorder_items(playlist_id, moving, anchor["item_id"])
        else:
            # The API only supports "position before X", so to move tracks to
            # the end we instead move every *other* entry that follows the
            # first moved track to just before it. Result: the rest of the
            # playlist keeps its order and the moved tracks end up last.
            first_pos = min(it["position"] for it in moving)
            others_after = [
                it for it in items
                if it["position"] > first_pos and it["item_id"] not in moving_ids
            ]
            first_item = min(moving, key=lambda it: it["position"])
            if others_after:
                self._reorder_items(playlist_id, others_after, first_item["item_id"])
            # Now the moved tracks are contiguous at the end; fix their
            # relative order if the caller asked for a specific one.
            if len(moving) > 1:
                self._reorder_items(playlist_id, moving[:-1], moving[-1]["item_id"])

        order = [it["track_id"] for it in self._get_playlist_items(playlist_id)]
        return {"moved": [it["track_id"] for it in moving], "order": order}

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
            for track in tracks:
                track["position"] = len(all_tracks) + 1
                all_tracks.append(track)

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
