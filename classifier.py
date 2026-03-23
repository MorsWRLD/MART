"""Batch classification of tracks with genre/mood/era/language tags via LLM."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import httpx

from matcher import _get_provider, _call_openai_compatible, _call_anthropic

TAGS_FILE = Path("tags.json")
BATCH_SIZE = 50

_CLASSIFICATION_PROMPT = """You are classifying music tracks for a playlist curation system.

For each track below, assign structured tags. Use ONLY values from the allowed lists.

ALLOWED GENRES: hip-hop, rap, pop, k-pop, j-pop, rock, metal, punk,
alternative, indie, electronic, edm, house, techno, dnb, dubstep, trap,
r&b, soul, jazz, blues, classical, folk, country, reggae, latin, anime,
soundtrack, phonk, hyperpop, lo-fi, ambient, experimental, post-punk,
emo, hardcore, grunge, new-wave, synthpop, disco, funk, drill, grime,
afrobeat, world

ALLOWED MOODS: happy, sad, melancholic, aggressive, chill, dreamy,
dark, euphoric, nostalgic, romantic, angry, peaceful, anxious,
triumphant, playful, intense, mysterious, upbeat, bittersweet

ALLOWED ENERGY: low, medium, high

ALLOWED ERAS: pre-1970s, 1970s, 1980s, 1990s, 2000s, 2010s, 2020s

ALLOWED VIBES: party, workout, driving, study, sleep, romance,
cooking, gaming, morning, night, rainy-day, summer, winter,
roadtrip, focus, emotional, hype

TRACKS:
{track_list}

Respond with ONLY a JSON array, no markdown. Each element:
{{"n": <1-based number>, "genres": [...], "mood": [...], "era": "...", "language": "...", "energy": "...", "vibe": [...]}}

Pick 1-3 values for genres, mood, and vibe. Pick exactly 1 for era, language, and energy.
For language use the primary language of the lyrics (e.g. "english", "russian", "japanese", "korean", "spanish"). Use "instrumental" if no lyrics.
Base era on the track's original release date, not when it was saved."""


def load_tags() -> dict:
    """Load tags.json or return empty structure."""
    if TAGS_FILE.exists():
        with open(TAGS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"metadata": {}, "tracks": {}}


def save_tags(data: dict) -> None:
    """Write tags to disk."""
    with open(TAGS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()


def _build_batch_prompt(batch: list[tuple[str, str, str]]) -> str:
    """Build classification prompt for a batch of (key, artist, title)."""
    lines = []
    for i, (key, artist, title) in enumerate(batch, 1):
        lines.append(f"{i}. Artist: {artist} | Title: {title}")
    return _CLASSIFICATION_PROMPT.format(track_list="\n".join(lines))


def _parse_classification_response(raw: str) -> list[dict]:
    """Parse the LLM JSON array response."""
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        cleaned = "\n".join(lines).strip()
    return json.loads(cleaned)


def classify_batch(
    batch: list[tuple[str, str, str]], client: httpx.Client
) -> list[dict]:
    """Classify a batch of tracks. Returns list of tag dicts with 'n' field."""
    env_key, api_key, base_url, model = _get_provider()
    prompt = _build_batch_prompt(batch)

    for attempt in range(2):
        try:
            if env_key == "ANTHROPIC_API_KEY":
                raw, _, _ = _call_anthropic(api_key, model, prompt, client)
            else:
                raw, _, _ = _call_openai_compatible(
                    api_key, base_url, model, prompt, client
                )
            return _parse_classification_response(raw)
        except (json.JSONDecodeError, KeyError, ValueError):
            if attempt == 0:
                continue
            raise
        except httpx.HTTPError:
            raise


def classify_all(
    results_data: dict,
    client: httpx.Client,
    on_progress: Callable[[int, int, int], None] | None = None,
) -> dict:
    """Classify all matched tracks. Resume-safe — skips already tagged tracks.

    on_progress(batch_num, total_batches, newly_classified) is called after each batch.
    Returns the full tags dict.
    """
    tags_data = load_tags()

    # Collect matched tracks not yet classified
    to_classify: list[tuple[str, str, str]] = []
    for key, entry in results_data["tracks"].items():
        if entry["status"] != "matched":
            continue
        if key in tags_data.get("tracks", {}):
            continue
        src = entry["source"]
        to_classify.append((key, src["artist"], src["title"]))

    if not to_classify:
        return tags_data

    # Ensure tracks dict exists
    if "tracks" not in tags_data:
        tags_data["tracks"] = {}

    total_batches = (len(to_classify) + BATCH_SIZE - 1) // BATCH_SIZE
    classified = 0

    for batch_idx in range(total_batches):
        start = batch_idx * BATCH_SIZE
        batch = to_classify[start : start + BATCH_SIZE]

        try:
            results = classify_batch(batch, client)
        except Exception as e:
            # Skip failed batch, continue with next
            if on_progress:
                on_progress(batch_idx + 1, total_batches, classified)
            continue

        # Map results back to keys
        for item in results:
            n = item.get("n")
            if n is None or not (1 <= n <= len(batch)):
                continue
            key = batch[n - 1][0]
            tags_data["tracks"][key] = {
                "genres": item.get("genres", []),
                "mood": item.get("mood", []),
                "era": item.get("era", ""),
                "language": item.get("language", ""),
                "energy": item.get("energy", ""),
                "vibe": item.get("vibe", []),
            }
            classified += 1

        # Update metadata and save after each batch
        tags_data["metadata"] = {
            "classified_at": datetime.now(timezone.utc).isoformat(),
            "total": len(tags_data["tracks"]),
            "model": _get_provider()[3],
        }
        save_tags(tags_data)

        if on_progress:
            on_progress(batch_idx + 1, total_batches, classified)

    return tags_data
