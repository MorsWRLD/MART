"""Search SoundCloud for track candidates using the internal API."""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass

import httpx

from vk_import import Track


@dataclass
class Candidate:
    title: str
    uploader: str
    duration: int | None  # seconds
    url: str
    play_count: int | None


_client_id: str | None = None
_last_request_time: float = 0.0

RATE_LIMIT = float(os.environ.get("MART_SC_RATE_LIMIT", "0.6"))
MAX_RETRIES = 3
RETRY_WAIT = 30


def _rate_limit() -> None:
    """Enforce minimum delay between SoundCloud requests."""
    global _last_request_time
    elapsed = time.monotonic() - _last_request_time
    if elapsed < RATE_LIMIT:
        time.sleep(RATE_LIMIT - elapsed)
    _last_request_time = time.monotonic()


def _extract_client_id(client: httpx.Client) -> str:
    """Extract SoundCloud client_id from their JS bundles."""
    resp = client.get("https://soundcloud.com", follow_redirects=True)
    resp.raise_for_status()

    # Find JS bundle URLs
    script_urls = re.findall(
        r'<script[^>]+src="(https://a-v2\.sndcdn\.com/assets/[^"]+\.js)"',
        resp.text,
    )

    for script_url in script_urls:
        _rate_limit()
        try:
            js_resp = client.get(script_url)
            js_resp.raise_for_status()
        except httpx.HTTPError:
            continue

        match = re.search(r'client_id:"([a-zA-Z0-9]{32})"', js_resp.text)
        if match:
            return match.group(1)

    raise RuntimeError(
        "Could not extract SoundCloud client_id. "
        "SoundCloud may have changed their frontend."
    )


def get_client_id(client: httpx.Client) -> str:
    """Get cached client_id or extract a fresh one."""
    global _client_id
    if _client_id is None:
        _client_id = _extract_client_id(client)
    return _client_id


def _search_sc_raw(
    query: str, client: httpx.Client, limit: int = 10
) -> list[Candidate]:
    """Execute a single SoundCloud search query. Returns up to `limit` results."""
    client_id = get_client_id(client)

    for attempt in range(MAX_RETRIES):
        _rate_limit()
        try:
            resp = client.get(
                "https://api-v2.soundcloud.com/search/tracks",
                params={
                    "q": query,
                    "client_id": client_id,
                    "limit": limit,
                    "offset": 0,
                },
            )

            if resp.status_code == 401:
                # client_id expired, re-extract
                global _client_id
                _client_id = None
                client_id = get_client_id(client)
                continue

            if resp.status_code in (429, 500, 502, 503, 504):
                if attempt < MAX_RETRIES - 1:
                    time.sleep(RETRY_WAIT)
                    continue
                return []

            resp.raise_for_status()
            data = resp.json()
            break

        except httpx.HTTPError:
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_WAIT)
                continue
            return []
    else:
        return []

    candidates: list[Candidate] = []
    for item in data.get("collection", []):
        duration_ms = item.get("duration")
        duration_sec = round(duration_ms / 1000) if duration_ms else None

        candidates.append(
            Candidate(
                title=item.get("title", ""),
                uploader=item.get("user", {}).get("username", ""),
                duration=duration_sec,
                url=item.get("permalink_url", ""),
                play_count=item.get("playback_count"),
            )
        )

    return candidates


def search_soundcloud(
    track: Track, client: httpx.Client
) -> list[Candidate]:
    """Search SoundCloud for candidates matching a track. Returns up to 10 results."""
    query = f"{track.artist} {track.title}"
    return _search_sc_raw(query, client)


def search_soundcloud_multi(
    track: Track, client: httpx.Client, queries: list[str]
) -> list[Candidate]:
    """Try multiple search queries, collect unique candidates from all of them.

    Always tries every query variant to maximize candidate diversity.
    Returns up to 20 unique candidates (deduped by URL).
    """
    seen_urls: set[str] = set()
    all_candidates: list[Candidate] = []

    for query in queries:
        results = _search_sc_raw(query, client)

        for c in results:
            if c.url not in seen_urls:
                seen_urls.add(c.url)
                all_candidates.append(c)

    return all_candidates[:20]
