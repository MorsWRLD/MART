# MART — Music Archive Rescue Tool

## What This Is

A Python CLI tool that recovers a user's VK Music library on SoundCloud.
Users have thousands of tracks on VK — some censored, some removed, some duplicated.
MART finds the original uncensored versions on SoundCloud automatically using LLM matching.

## Architecture

4 modules, 1 orchestrator, 1 JSON state file. That's it.

```
mart.py            → CLI entry point + orchestrator loop
vk_import.py       → Parse VK playlist export (JSON/CSV) into track list
sc_search.py       → Search SoundCloud for candidates per track
matcher.py         → Send candidates to LLM, get pick or "none"
```

### Data Flow

```
VK Export File (JSON/CSV)
  → vk_import.py: parse into List[Track]
  → dedup by lowercase(artist + title)
  → for each track:
      → sc_search.py: query SoundCloud API → top 10 candidates
      → matcher.py: LLM picks best original uncensored match
      → write result to results.json (append + flush, crash-safe)
  → final summary printed to terminal
```

### State File: results.json

This is both the output AND the resume checkpoint. Structure:

```json
{
  "metadata": {
    "started_at": "ISO datetime",
    "source_file": "path",
    "total_tracks": 3000,
    "processed": 2150,
    "matched": 1894,
    "no_match": 201,
    "failed": 55
  },
  "tracks": {
    "artist::title": {
      "source": {"artist": "...", "title": "...", "duration": 245},
      "status": "matched|no_match|failed",
      "match": {"title": "...", "uploader": "...", "url": "...", "duration": 243},
      "candidates_count": 8,
      "llm_reason": "..."
    }
  }
}
```

Resume logic: on each track, check if `artist::title` key exists in results. If yes, skip.
Use `tracks` as a dict (not array) for O(1) lookup.

## Module Specs

### vk_import.py

Input: path to a JSON or CSV file exported from VK Music.

Supported formats:
- JSON array of objects with `artist`, `title`, and optional `duration` fields
- CSV with `artist`, `title`, and optional `duration` columns
- Auto-detect format from file extension

Output: `List[Track]` where Track is a simple dataclass:
```python
@dataclass
class Track:
    artist: str
    title: str
    duration: int | None = None  # seconds
```

Dedup: done in mart.py after import. Key = `f"{artist.lower().strip()}::{title.lower().strip()}"`.
Keep first occurrence, count dupes in metadata.

### sc_search.py

Uses SoundCloud's internal API (`api-v2.soundcloud.com`).

**Client ID extraction:**
- Fetch `https://soundcloud.com`
- Find JS bundle URLs in the HTML
- Fetch the bundles, regex for `client_id:"[a-zA-Z0-9]{32}"`
- Cache the client_id for the session

**Search:**
```
GET https://api-v2.soundcloud.com/search/tracks
  ?q={artist}+{title}
  &client_id={client_id}
  &limit=10
  &offset=0
```

Response fields we care about: `title`, `user.username`, `duration` (ms), `permalink_url`, `playback_count`, `created_at`.

**Rate limiting:** `time.sleep(0.6)` between requests. ~100 req/min. Conservative enough to avoid blocks.

**Error handling:** On 429 or 5xx, wait 30 seconds and retry up to 3 times. On persistent failure, mark track as `failed` and continue.

Return: `List[Candidate]`:
```python
@dataclass
class Candidate:
    title: str
    uploader: str
    duration: int | None  # seconds (convert from ms)
    url: str
    play_count: int | None
```

### matcher.py

Takes a source Track and List[Candidate], calls the LLM, returns the pick.

**Supported providers (via env vars):**
- `OPENROUTER_API_KEY` → OpenRouter (any model, default: `google/gemini-2.0-flash-001`)
- `OPENAI_API_KEY` → OpenAI (default: `gpt-4o-mini`)
- `ANTHROPIC_API_KEY` → Anthropic (default: `claude-haiku-4-5-20251001`)

Check env vars in that order. First one found wins.

**The Prompt** (this is the core of the whole project — get it right):

```
You are matching music tracks from a VK Music library to SoundCloud search results.

TASK: Given the source track and candidates below, pick the candidate that is the
ORIGINAL UNCENSORED version of the track. If no candidate is a good match, say null.

RULES:
- Artist must match (account for Cyrillic/Latin transliteration, e.g. Кино = Kino)
- Title must match the same song (not a different song by the same artist)
- REJECT these variants: slowed, reverb, nightcore, sped up, 8D, bass boosted,
  karaoke, tribute, cover, mashup, midi
- REJECT "clean" or "radio edit" — these are censored versions
- PREFER "explicit" or unmarked versions — these are likely uncensored originals
- "Remastered" is acceptable
- Duration should be within ~15 seconds if known (but don't reject solely on duration)
- If multiple candidates are equally good, prefer the one with higher play count

SOURCE TRACK:
Artist: {artist}
Title: {title}
Duration: {duration_str}

CANDIDATES:
{formatted_candidates}

Respond with ONLY valid JSON, no markdown:
{{"pick": <1-based candidate number or null>, "reason": "<brief explanation>"}}
```

**Response parsing:** Parse JSON from response. If parsing fails, retry once. If still fails, mark as `failed`.

**Cost tracking:** Count input/output tokens from API response (most providers return this). Accumulate for the summary.

### mart.py (orchestrator)

CLI using `click` (or `argparse` if keeping deps minimal).

```
python mart.py --input playlist.json [--output results.json] [--model google/gemini-2.0-flash-001]
```

Loop:
```python
tracks = load_and_dedup(input_file)
results = load_results(output_file)  # resume support

for i, track in enumerate(tracks):
    key = dedup_key(track)
    if key in results["tracks"]:
        continue  # already processed

    print(f"[{i+1}/{len(tracks)}] {track.artist} — {track.title}")

    candidates = search_soundcloud(track)
    if not candidates:
        save_result(results, key, track, status="no_match", ...)
        continue

    match = llm_match(track, candidates)
    save_result(results, key, track, match=match, ...)

print_summary(results)
```

Progress: use `rich` for a progress bar if installed, fall back to plain print statements.

## Coding Standards

- Python 3.11+
- Type hints everywhere
- Dataclasses for data structures (not dicts)
- No classes where a function will do
- httpx for HTTP (sync is fine for MVP)
- No database, no ORM, no heavy frameworks
- Error handling: retry transient failures, skip and log permanent ones, never crash the loop

## Environment Variables

```
# LLM provider (checked in order, first found wins)
OPENROUTER_API_KEY=sk-or-...
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...

# Optional
MART_MODEL=google/gemini-2.0-flash-001   # override default model
MART_SC_RATE_LIMIT=0.6                    # seconds between SC requests
```

## Dependencies

```
httpx>=0.27
click>=8.0
rich>=13.0          # optional, for pretty progress
```

That's it. Three dependencies.

## Testing Strategy

Don't write unit tests for everything. Focus on:
1. **VK import parsing** — test with sample exports (3-4 fixture files)
2. **LLM prompt quality** — manual test with 50 real tracks, verify accuracy
3. **Resume logic** — run 10 tracks, kill, run again, verify it picks up at track 11
4. **End-to-end** — run against a real 100-track playlist, spot-check results

## What NOT to Build

- No GUI
- No plugin system
- No config files (env vars only)
- No database
- No async/parallel (serial is fast enough)
- No artist alias tables
- No audio fingerprinting
- No downloading audio files
- No multi-platform import (VK only for now)
- No calibration workflow
- No scoring system (LLM decides, not a point system)
