# MART — Future Plans

## Current State
- 1817/1996 tracks matched (91% match rate)
- Playlists created on SoundCloud via browser console script
- Multi-query search + LLM matching pipeline working
- Web UI with rescan, manual replacement, swap staging

---

## PRIORITY: AI DJ Chat

Chat interface in the UI for natural language playlist management from the user's library.

**Examples:**
- "pick all Russian trap songs"
- "old rock classics"
- "songs for a rainy drive"
- "everything by Japanese artists"
- "chill instrumental tracks"

**Approach:** Hybrid classification
1. Batch-classify all tracks into broad tags (genre, mood, era, language) on first use — ~20 LLM calls for 1800 tracks in batches of 100
2. Store tags in results.json (or separate tags.json)
3. Chat queries: send tags + user request to LLM, get filtered track list
4. On-demand flexibility: "songs that would play in a 90s movie" still works even without exact genre tags
5. Output: create SC playlist script from the filtered set

**UI:** Chat panel (side drawer or tab), text input, streaming responses, "Create Playlist" button on results.

---

## BACKLOG

### Free vs Go+ Track Tagging
- Tag each track as free or Go+ during resolve step (SC API returns `policy`/`access` field)
- Show "Go+" badge in UI on affected tracks
- Add toggle filter: "free only" when creating playlists
- Informational — don't try to find free alternatives (rabbit hole, bad reuploads)
- Helps user understand what they'd lose if Go+ subscription lapses

### Multi-Platform Import
- **Apple Music** — export via playlist file or scrape API
- **Spotify** — Spotify API with OAuth, export liked songs / playlists
- **Yandex Music** — scrape or API
- Dedup across platforms: same lowercase(artist + title) key, merge sources
- MART becomes a universal music migration tool, not just VK→SC
- Architecture: each platform gets its own `xx_import.py` module, same `Track` dataclass output

### Download Missing Tracks from VK
- For the ~179 unmatched tracks, download audio files locally as MP3 backup
- Requires VK audio access (user's own VK token + audio API or browser extension like VK Music Saver)
- VK's audio API is locked down — likely needs a browser extension or user-provided direct URLs
- VK Music Saver already handles bulk download — MART could export a list of missing track names for the user to select in the extension
- **Decision: don't re-upload to SoundCloud** — copyright takedowns + account ban risk

### Track Recommendations
- Given the user's library, suggest similar tracks on SoundCloud they might like
- Could use LLM to analyze library patterns and generate search queries
- Lower priority — focus on migration first

---

## Decisions Made
- No SoundCloud re-upload of missing tracks (copyright/account risk)
- Go+ handling: tag and filter, don't search for free alternatives
- Playlist creation uses browser console script (DataDome blocks server-side POST)
- LLM provider: OpenRouter (default model: gemini-2.0-flash-001)
- Serial processing, no async (fast enough for current scale)
</content>
</invoke>