"""
JSON:API response parsing utilities for the TIDAL v2 API.

The TIDAL v2 API follows the JSON:API specification (https://jsonapi.org/format/).
This module provides helpers to extract resources, resolve included sideloads,
handle cursor-based pagination, and format track/playlist data into a
standardized output format.
"""

import re
from urllib.parse import urlparse, parse_qs


def parse_duration(iso_duration: str) -> int:
    """Convert an ISO 8601 duration string to total seconds.

    Examples:
        "PT3M45S" -> 225
        "PT1H2M3S" -> 3723
        "PT30S" -> 30
        "PT5M" -> 300

    Args:
        iso_duration: ISO 8601 duration string (e.g., "PT3M45S").

    Returns:
        Total duration in seconds. Returns 0 if parsing fails.
    """
    if not iso_duration:
        return 0

    match = re.match(
        r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", str(iso_duration)
    )
    if not match:
        return 0

    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = int(match.group(3) or 0)
    return hours * 3600 + minutes * 60 + seconds


def build_include_index(included: list) -> dict:
    """Build a lookup index from the JSON:API `included` array.

    Args:
        included: The `included` array from a JSON:API response.

    Returns:
        A dict mapping (type, id) tuples to the full resource object.
    """
    index = {}
    for resource in included or []:
        key = (resource.get("type"), str(resource.get("id")))
        index[key] = resource
    return index


def resolve_relationship(resource: dict, rel_name: str, index: dict) -> list:
    """Resolve a named relationship to its included resource objects.

    Args:
        resource: A JSON:API resource object with a `relationships` dict.
        rel_name: The relationship key to resolve (e.g., "artists", "albums").
        index: The include index from `build_include_index`.

    Returns:
        A list of resolved resource objects from the index.
    """
    relationships = resource.get("relationships", {})
    rel_data = relationships.get(rel_name, {}).get("data")
    if not rel_data:
        return []

    # Normalize to list (handles to-one relationships)
    if isinstance(rel_data, dict):
        rel_data = [rel_data]

    resolved = []
    for ref in rel_data:
        key = (ref.get("type"), str(ref.get("id")))
        included_resource = index.get(key)
        if included_resource:
            resolved.append(included_resource)
    return resolved


def get_next_cursor(response_json: dict) -> str | None:
    """Extract the cursor for the next page from JSON:API `links.next`.

    Args:
        response_json: The full JSON:API response body.

    Returns:
        The cursor string, or None if there is no next page.
    """
    next_link = (response_json.get("links") or {}).get("next")
    if not next_link:
        return None

    parsed = urlparse(next_link)
    params = parse_qs(parsed.query)
    cursors = params.get("page[cursor]", [])
    return cursors[0] if cursors else None


def format_track(resource: dict, index: dict = None) -> dict:
    """Convert a JSON:API track resource into the standardized output format.

    Args:
        resource: A JSON:API resource object with type "tracks".
        index: Optional include index to resolve artist/album relationships.

    Returns:
        A dict with keys: id, title, artist, album, duration, url.
    """
    attrs = resource.get("attributes", {})
    track_id = str(resource.get("id", ""))

    # Resolve artist name from includes
    artist_name = "Unknown"
    if index:
        artists = resolve_relationship(resource, "artists", index)
        if artists:
            artist_name = artists[0].get("attributes", {}).get("name", "Unknown")

    # Resolve album name from includes
    album_name = "Unknown"
    if index:
        albums = resolve_relationship(resource, "albums", index)
        if albums:
            album_name = albums[0].get("attributes", {}).get("title", "Unknown")

    return {
        "id": track_id,
        "title": attrs.get("title", "Unknown"),
        "artist": artist_name,
        "album": album_name,
        "duration": parse_duration(attrs.get("duration", "")),
        "url": f"https://tidal.com/browse/track/{track_id}",
    }


def format_playlist(resource: dict) -> dict:
    """Convert a JSON:API playlist resource into the standardized output format.

    Args:
        resource: A JSON:API resource object with type "playlists".

    Returns:
        A dict with keys: id, title, description, created, last_updated,
        track_count, duration, url.
    """
    attrs = resource.get("attributes", {})
    playlist_id = str(resource.get("id", ""))

    return {
        "id": playlist_id,
        "title": attrs.get("name", "Unknown"),
        "description": attrs.get("description", ""),
        "created": attrs.get("createdAt"),
        "last_updated": attrs.get("lastModifiedAt"),
        "track_count": attrs.get("numberOfItems", 0),
        "duration": parse_duration(attrs.get("duration", "")),
        "url": f"https://tidal.com/playlist/{playlist_id}",
    }


def parse_collection_response(response_json: dict, resource_type: str = None) -> tuple[list[dict], dict]:
    """Parse a JSON:API response containing a collection of resources.

    Args:
        response_json: The full JSON:API response body.
        resource_type: If provided, only format resources of this type
            (e.g., "tracks", "playlists"). If None, returns raw parsed resources.

    Returns:
        A tuple of (formatted_items, include_index).
        formatted_items is a list of dicts in standardized format.
        include_index can be used for further relationship resolution.
    """
    data = response_json.get("data", [])
    included = response_json.get("included", [])
    index = build_include_index(included)

    # If data contains resource identifiers (just id+type, no attributes),
    # resolve them from the included array
    items = []
    for item in data:
        if "attributes" not in item and index:
            # This is a resource identifier — resolve from includes
            key = (item.get("type"), str(item.get("id")))
            resolved = index.get(key, item)
            items.append(resolved)
        else:
            items.append(item)

    # Format based on resource type
    formatted = []
    for item in items:
        item_type = item.get("type", resource_type)
        if item_type == "tracks" or resource_type == "tracks":
            formatted.append(format_track(item, index))
        elif item_type == "playlists" or resource_type == "playlists":
            formatted.append(format_playlist(item))
        else:
            # Generic: flatten attributes
            formatted.append({
                "id": str(item.get("id", "")),
                "type": item.get("type", ""),
                **item.get("attributes", {}),
            })

    return formatted, index


def parse_single_response(response_json: dict) -> tuple[dict, dict]:
    """Parse a JSON:API response containing a single resource.

    Args:
        response_json: The full JSON:API response body.

    Returns:
        A tuple of (resource_data, include_index).
        resource_data is the single resource object (with attributes).
        include_index can be used for relationship resolution.
    """
    data = response_json.get("data", {})
    included = response_json.get("included", [])
    index = build_include_index(included)

    # Resolve from includes if this is just an identifier
    if "attributes" not in data and index:
        key = (data.get("type"), str(data.get("id")))
        data = index.get(key, data)

    return data, index
