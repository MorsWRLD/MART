# FUTURE.md — From Recovery Script to AI Music Agent

## Two Different Things That Share a Foundation

**MART today** is a batch pipeline: scrape → search → match → done.
It produces a static output file (results.json) and exits.

**An AI DJ / music agent** is a conversational tool: "Make a playlist of my sad Russian rap from 2016–2019" or "Find everything in my library that sounds like Radiohead."
It needs persistent access to your library and the ability to reason about it on demand.

These are different products. But they share the same hard problem: **getting a structured, normalized version of the user's music library into a format an LLM can work with.**

## What MART Builds That an Agent Would Reuse

The scraping, normalization, and dedup work is the same regardless of what you do with the data afterward:

```
MART pipeline (keep as-is):
  VK Export → parse → normalize → dedup → structured track list

MART today:          structured list → SC search → LLM match → results.json
AI DJ tomorrow:      structured list → agent memory → conversational queries
```

The structured track list is the shared asset. If you build MART's import/dedup cleanly (which CLAUDE.md already specifies), you can load that same data into an agent's context or memory system.

## How the AI DJ Skill Would Work

### As an OpenClaw / MCP Skill

If Rose or another agent has access to your normalized library (e.g. loaded from results.json or a similar export), the skill is essentially:

```
Tool: music_library_query
Description: Search and filter the user's music library by artist, genre,
             mood, era, language, or any natural language description.
Input: { "query": "heartbreak rock songs from 2014-2018" }
Output: { "tracks": [...matching tracks with metadata...] }
```

The agent calls this tool when the user asks anything about "my music" or "my library." The tool implementation is just filtering/searching the normalized track list — potentially with an LLM to handle fuzzy queries like "songs that sound like summer."

### What's Different from MART

| Aspect | MART (batch recovery) | AI DJ (conversational) |
|--------|----------------------|----------------------|
| Trigger | User runs a command | User asks in conversation |
| Scope | Process entire library once | Answer one question at a time |
| Data access | Reads export file, writes results | Needs persistent loaded library |
| LLM usage | Matching (cheap, per-track) | Query understanding + generation |
| Output | JSON file with URLs | Playlist, recommendations, conversation |
| State | Stateless (resume via file) | Stateful (remembers preferences) |

### What You'd Need to Add

1. **Genre/mood tagging** — MART doesn't tag tracks by genre or mood. An AI DJ needs this. Options:
   - Use the LLM during import to tag each track (adds ~$0.001/track)
   - Use SoundCloud/Spotify metadata if available
   - Let the agent infer from artist + title at query time (cheaper, less accurate)

2. **Library persistence** — Instead of just results.json, store the library in a format the agent can load quickly. Could be the same JSON, a SQLite db, or embedded in the agent's memory files (SOUL.md / MEMORY.md pattern from OpenClaw).

3. **Query interface** — The MCP tool or skill function that the agent calls. Thin wrapper: load library, filter/search, return results.

4. **Playlist generation** — Given a filtered set of tracks, format them as a playlist (M3U, SoundCloud playlist, or just a list in chat).

## Porting Path (Minimal Work)

The realistic path is:

**Phase 1 (now):** Build MART as specified. Get the batch recovery working.

**Phase 2 (days later):** Add a `--tag` flag that enriches results.json with genre/mood tags via LLM during the matching pass. Marginal extra cost, big value for agent use.

**Phase 3 (when agent infra is ready):** Write a thin skill/tool that:
- Loads results.json (or a tagged version)
- Exposes a `search_library(query)` function
- Returns matching tracks with metadata

That's maybe 50–100 lines of code on top of what MART already produces.

## Why Build MART First

- It solves a real, immediate problem (your library is broken right now)
- It forces you to solve the hard parts (VK scraping, SC search, LLM matching)
- The output (normalized, matched library) is exactly what an agent needs as input
- You validate the data quality before building a conversational layer on top

Building the agent first without the recovery pipeline would mean the agent has no data to work with. MART creates the data.

## Architecture Sketch (Future State)

```
┌─────────────────────────────────────────────┐
│ User's Music Library (results.json + tags)   │
│  - 3000 tracks, normalized, with SC URLs     │
│  - genre/mood tags from LLM                  │
│  - original VK metadata preserved            │
└──────────────┬──────────────────────────┬────┘
               │                          │
        ┌──────▼──────┐          ┌────────▼────────┐
        │  MART CLI    │          │  AI DJ Skill     │
        │  (batch)     │          │  (conversational)│
        │              │          │                  │
        │  Recovery:   │          │  "Find my 2016   │
        │  VK → SC     │          │   sad rap"       │
        │  matching    │          │                  │
        └──────────────┘          │  Runs as MCP     │
                                  │  tool / OpenClaw │
                                  │  skill for Rose  │
                                  └──────────────────┘
```

Both consume the same data. MART creates and updates it. The AI DJ reads and queries it.
