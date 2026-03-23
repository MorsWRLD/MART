"""Universal music library importer — handles exports from Spotify, Apple Music,
Yandex Music, VK, and generic CSV/JSON/TXT formats.

Auto-detects platform and column names. Always outputs List[Track].
"""

from __future__ import annotations

import csv
import json
import re
import io
from dataclasses import dataclass
from pathlib import Path

from vk_import import Track


# Column name mappings — maps various export formats to our fields
_ARTIST_COLUMNS = [
    "artist", "artists", "artist name", "artist name(s)", "artist_name",
    "исполнитель", "артист",
]
_TITLE_COLUMNS = [
    "title", "track name", "track_name", "name", "song", "song name",
    "track", "название", "трек",
]
_DURATION_COLUMNS = [
    "duration", "duration (ms)", "duration_ms", "durationms",
    "time", "length", "продолжительность",
]


@dataclass
class ImportResult:
    tracks: list[Track]
    platform: str  # detected platform name
    raw_count: int  # total rows before filtering


def detect_platform(headers: list[str] | None, data: any) -> str:
    """Guess the source platform from column names or data structure."""
    if headers:
        h = set(h.lower() for h in headers)
        if "track name" in h or "artist name(s)" in h:
            return "Spotify"
        if "content type" in h and ("apple" in str(headers).lower() or "name" in h):
            return "Apple Music"
        if "исполнитель" in h or "название" in h:
            return "Yandex Music"
        if "artist" in h and "title" in h:
            return "Generic"

    if isinstance(data, list) and data:
        item = data[0]
        if isinstance(item, dict):
            if "track" in item and isinstance(item["track"], dict):
                return "Spotify API"
            if "artists" in item and isinstance(item["artists"], list):
                if any(k in item for k in ("durationMs", "duration_ms")):
                    return "Yandex Music"
                return "Spotify API"
            if "artist" in item and "title" in item:
                if "duration" in item:
                    return "VK"
                return "Generic"

    return "Unknown"


def _find_column(headers: list[str], candidates: list[str]) -> str | None:
    """Find the first matching column name (case-insensitive)."""
    lower_map = {h.lower().strip(): h for h in headers}
    for c in candidates:
        if c in lower_map:
            return lower_map[c]
    return None


def _parse_duration(val: str | int | float | None) -> int | None:
    """Parse a duration value. Handles seconds, milliseconds, and mm:ss format."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        # If > 10000, assume milliseconds
        v = int(val)
        return v // 1000 if v > 10000 else v
    val = str(val).strip()
    if not val:
        return None
    # mm:ss format
    if ":" in val:
        parts = val.split(":")
        try:
            if len(parts) == 2:
                return int(parts[0]) * 60 + int(parts[1])
            elif len(parts) == 3:
                return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        except ValueError:
            return None
    # Plain number
    try:
        v = int(float(val))
        return v // 1000 if v > 10000 else v
    except ValueError:
        return None


def import_csv(text: str) -> ImportResult:
    """Parse a CSV string with auto-detected columns."""
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return ImportResult(tracks=[], platform="Unknown", raw_count=0)

    headers = list(reader.fieldnames)
    platform = detect_platform(headers, None)

    artist_col = _find_column(headers, _ARTIST_COLUMNS)
    title_col = _find_column(headers, _TITLE_COLUMNS)
    duration_col = _find_column(headers, _DURATION_COLUMNS)

    if not artist_col or not title_col:
        # Try to detect from first few column names
        raise ValueError(
            f"Could not find artist/title columns. "
            f"Found columns: {headers}. "
            f"Expected artist column (one of: {_ARTIST_COLUMNS}) "
            f"and title column (one of: {_TITLE_COLUMNS})."
        )

    tracks = []
    raw_count = 0
    for row in reader:
        raw_count += 1
        artist = row.get(artist_col, "").strip()
        title = row.get(title_col, "").strip()
        if not artist or not title:
            continue

        # Handle multi-artist (Spotify uses ", " separator)
        # Keep as-is for dedup, the query_clean module handles splitting
        duration = _parse_duration(row.get(duration_col)) if duration_col else None
        tracks.append(Track(artist=artist, title=title, duration=duration))

    return ImportResult(tracks=tracks, platform=platform, raw_count=raw_count)


def import_json(text: str) -> ImportResult:
    """Parse a JSON string — handles various API export formats."""
    data = json.loads(text)

    # Normalize to a list of items
    items = []
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        # Spotify API wraps in {"items": [...]}
        if "items" in data:
            items = data["items"]
        # Some exports use {"tracks": [...]}
        elif "tracks" in data:
            items = data["tracks"]
        else:
            raise ValueError("JSON must be an array or have an 'items'/'tracks' key")

    if not items:
        return ImportResult(tracks=[], platform="Unknown", raw_count=0)

    platform = detect_platform(None, items)
    tracks = []

    for item in items:
        artist, title, duration = _extract_from_json_item(item)
        if artist and title:
            tracks.append(Track(artist=artist, title=title, duration=duration))

    return ImportResult(tracks=tracks, platform=platform, raw_count=len(items))


def _extract_from_json_item(item: dict) -> tuple[str, str, int | None]:
    """Extract artist, title, duration from a single JSON track object."""
    # Spotify API format: {"track": {"name": "...", "artists": [{"name": "..."}], "duration_ms": ...}}
    if "track" in item and isinstance(item["track"], dict):
        item = item["track"]

    # Artist
    artist = ""
    if "artists" in item and isinstance(item["artists"], list):
        names = []
        for a in item["artists"]:
            if isinstance(a, dict):
                names.append(a.get("name", ""))
            elif isinstance(a, str):
                names.append(a)
        artist = ", ".join(n for n in names if n)
    elif "artist" in item:
        artist = str(item["artist"]).strip()

    # Title
    title = ""
    for key in ("title", "name", "track_name"):
        if key in item:
            title = str(item[key]).strip()
            break

    # Duration
    duration = None
    for key in ("duration", "duration_ms", "durationMs", "duration_s"):
        if key in item and item[key] is not None:
            duration = _parse_duration(item[key])
            break

    return artist, title, duration


def import_txt(text: str) -> ImportResult:
    """Parse numbered text format: '1. Artist - Title' per line."""
    pattern = re.compile(r"^\d*\.?\s*(.+?)\s+-\s+(.+)$")
    tracks = []
    lines = text.strip().splitlines()
    for line in lines:
        line = line.strip()
        if not line:
            continue
        match = pattern.match(line)
        if match:
            artist = match.group(1).strip()
            title = match.group(2).strip()
            if artist and title:
                tracks.append(Track(artist=artist, title=title))

    return ImportResult(tracks=tracks, platform="Text List", raw_count=len(lines))


def import_file(filename: str, content: str) -> ImportResult:
    """Auto-detect format from filename and parse."""
    ext = Path(filename).suffix.lower()

    if ext == ".json":
        return import_json(content)
    elif ext == ".csv":
        return import_csv(content)
    elif ext in (".txt", ".text"):
        return import_txt(content)
    else:
        # Try JSON first, then CSV, then TXT
        try:
            return import_json(content)
        except (json.JSONDecodeError, ValueError):
            pass
        try:
            return import_csv(content)
        except (ValueError, csv.Error):
            pass
        return import_txt(content)
