"""Yandex Music library importer — fetches liked tracks directly via API token."""

from __future__ import annotations

from vk_import import Track


def fetch_liked_tracks(token: str) -> list[Track]:
    """Fetch all liked tracks from Yandex Music using an API token.

    Args:
        token: Yandex Music API token (see UI for instructions on how to get it).

    Returns:
        List of Track objects.

    Raises:
        ImportError: if yandex-music library is not installed.
        RuntimeError: if the token is invalid or the request fails.
    """
    try:
        from yandex_music import Client
    except ImportError:
        raise ImportError(
            "yandex-music library not installed. Run: pip install yandex-music"
        )

    try:
        client = Client(token).init()
    except Exception as e:
        raise RuntimeError(f"Failed to connect to Yandex Music: {e}")

    try:
        tracks_list = client.users_likes_tracks()
    except Exception as e:
        raise RuntimeError(f"Failed to fetch liked tracks: {e}")

    if not tracks_list:
        return []

    try:
        raw_tracks = tracks_list.fetch_tracks()
    except Exception as e:
        raise RuntimeError(f"Failed to load track details: {e}")

    result: list[Track] = []
    for t in raw_tracks:
        if not t or not t.title:
            continue

        artist_names = t.artists_name() if callable(getattr(t, "artists_name", None)) else []
        if not artist_names and t.artists:
            artist_names = [a.name for a in t.artists if a and a.name]

        artist = ", ".join(n for n in artist_names if n).strip()
        title = (t.title or "").strip()

        if not artist or not title:
            continue

        duration = (t.duration_ms // 1000) if t.duration_ms else None
        result.append(Track(artist=artist, title=title, duration=duration))

    return result
