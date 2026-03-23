"""Parse VK Music playlist exports (JSON, CSV, or TXT) into a list of Track objects."""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Track:
    artist: str
    title: str
    duration: int | None = None  # seconds


def load_tracks(path: str | Path) -> list[Track]:
    """Load tracks from a JSON or CSV file. Auto-detects format by extension."""
    path = Path(path)
    ext = path.suffix.lower()

    if ext == ".json":
        return _load_json(path)
    elif ext == ".csv":
        return _load_csv(path)
    elif ext == ".txt":
        return _load_txt(path)
    else:
        raise ValueError(f"Unsupported file format: {ext} (expected .json, .csv, or .txt)")


def _load_json(path: Path) -> list[Track]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise ValueError("JSON file must contain an array of track objects")

    tracks: list[Track] = []
    for item in data:
        artist = item.get("artist", "").strip()
        title = item.get("title", "").strip()
        if not artist or not title:
            continue
        duration = item.get("duration")
        if duration is not None:
            duration = int(duration)
        tracks.append(Track(artist=artist, title=title, duration=duration))

    return tracks


def _load_txt(path: Path) -> list[Track]:
    """Parse numbered TXT format: '1. Artist - Title' per line."""
    # Pattern: optional number + dot + spaces, then Artist - Title
    pattern = re.compile(r"^\d+\.\s+(.+?)\s+-\s+(.+)$")

    tracks: list[Track] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            match = pattern.match(line)
            if match:
                artist = match.group(1).strip()
                title = match.group(2).strip()
                if artist and title:
                    tracks.append(Track(artist=artist, title=title))
            # Skip lines that don't match the pattern

    return tracks


def _load_csv(path: Path) -> list[Track]:
    tracks: list[Track] = []
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            artist = row.get("artist", "").strip()
            title = row.get("title", "").strip()
            if not artist or not title:
                continue
            duration = row.get("duration")
            if duration is not None and duration.strip():
                duration = int(duration.strip())
            else:
                duration = None
            tracks.append(Track(artist=artist, title=title, duration=duration))

    return tracks
