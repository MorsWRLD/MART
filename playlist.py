"""Playlist management tools: create multi-part playlists, generate swap scripts, export failed tracks."""

from __future__ import annotations

import json
from pathlib import Path

import click


def _load_results(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


@click.group()
def cli():
    """MART playlist tools."""
    pass


# ---------------------------------------------------------------------------
# 1. Failed tracks report
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--results", default="results.json", type=click.Path(exists=True))
@click.option("--output", default="failed_tracks.txt", type=click.Path())
def failed(results: str, output: str):
    """Export all unmatched/failed tracks grouped by reason."""
    data = _load_results(results)

    no_match: list[tuple[str, str, str]] = []  # (artist, title, reason)
    no_results: list[tuple[str, str]] = []
    errors: list[tuple[str, str, str]] = []

    for key, entry in data["tracks"].items():
        src = entry["source"]
        label = f"{src['artist']} — {src['title']}"

        if entry["status"] == "no_match":
            if entry["candidates_count"] == 0:
                no_results.append((label, entry.get("llm_reason", "")))
            else:
                no_match.append((label, f"{entry['candidates_count']} candidates", entry.get("llm_reason", "")))
        elif entry["status"] == "failed":
            errors.append((label, entry.get("llm_reason", ""), f"{entry['candidates_count']} candidates"))

    lines: list[str] = []
    lines.append(f"MART Failed Tracks Report")
    lines.append(f"Source: {data['metadata'].get('source_file', '?')}")
    lines.append(f"Total: {data['metadata']['total_tracks']} tracks processed")
    lines.append("")

    lines.append(f"=== NO SOUNDCLOUD RESULTS ({len(no_results)}) ===")
    lines.append("These tracks returned zero search results on SoundCloud.")
    lines.append("")
    for label, reason in no_results:
        lines.append(f"  {label}")
    lines.append("")

    lines.append(f"=== LLM REJECTED ALL CANDIDATES ({len(no_match)}) ===")
    lines.append("SoundCloud had results, but none matched the original track.")
    lines.append("")
    for label, cands, reason in no_match:
        lines.append(f"  {label}  [{cands}]")
        if reason:
            lines.append(f"    Reason: {reason}")
    lines.append("")

    if errors:
        lines.append(f"=== ERRORS ({len(errors)}) ===")
        lines.append("Processing failed due to search or LLM errors.")
        lines.append("")
        for label, reason, cands in errors:
            lines.append(f"  {label}  [{cands}]")
            if reason:
                lines.append(f"    Error: {reason}")
        lines.append("")

    lines.append(f"--- Total missing: {len(no_results) + len(no_match) + len(errors)} tracks ---")

    text = "\n".join(lines)
    with open(output, "w", encoding="utf-8") as f:
        f.write(text)

    click.echo(f"No SC results:    {len(no_results)}")
    click.echo(f"LLM rejected:     {len(no_match)}")
    click.echo(f"Errors:           {len(errors)}")
    click.echo(f"Written to {output}")


# ---------------------------------------------------------------------------
# 2. Generate multi-part playlist creation script
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--results", default="results.json", type=click.Path(exists=True))
@click.option("--ids", default="track_ids.json", type=click.Path(exists=True))
@click.option("--output", default="create_playlists.js", type=click.Path())
@click.option("--limit", default=500, help="Max tracks per playlist")
def create(results: str, ids: str, output: str, limit: int):
    """Generate JS script to create multi-part playlists (500 track limit)."""
    with open(ids, "r", encoding="utf-8") as f:
        id_data = json.load(f)

    all_ids = list(id_data["resolved"].values())
    num_parts = (len(all_ids) + limit - 1) // limit

    click.echo(f"{len(all_ids)} tracks -> {num_parts} playlist(s) of up to {limit}")

    # Build JS
    js_lines: list[str] = []
    js_lines.append("// MART Multi-Playlist Creator")
    js_lines.append(f"// {len(all_ids)} tracks in {num_parts} part(s)")
    js_lines.append("(async () => {")
    js_lines.append(f"    const ALL_IDS = {json.dumps(all_ids)};")
    js_lines.append(f"    const LIMIT = {limit};")
    js_lines.append(f"    const BATCH = 100;")
    js_lines.append('    const token = document.cookie.match(/oauth_token=([^;]+)/)?.[1];')
    js_lines.append('    if (!token) { console.error("Not logged in!"); return; }')
    js_lines.append("")
    js_lines.append("    // Get client_id")
    js_lines.append("    const scripts = document.querySelectorAll('script[src*=\"sndcdn.com\"]');")
    js_lines.append("    let cid = null;")
    js_lines.append("    for (const s of scripts) {")
    js_lines.append("        try {")
    js_lines.append("            const r = await fetch(s.src);")
    js_lines.append("            const t = await r.text();")
    js_lines.append('            const m = t.match(/client_id:"([a-zA-Z0-9]{32})"/);')
    js_lines.append("            if (m) { cid = m[1]; break; }")
    js_lines.append("        } catch(e) {}")
    js_lines.append("    }")
    js_lines.append('    if (!cid) { console.error("Could not find client_id"); return; }')
    js_lines.append("")
    js_lines.append('    const headers = { "Authorization": "OAuth " + token, "Content-Type": "application/json" };')
    js_lines.append("")
    js_lines.append(f"    const numParts = Math.ceil(ALL_IDS.length / LIMIT);")
    js_lines.append("    const playlists = [];")
    js_lines.append("")
    js_lines.append("    for (let part = 0; part < numParts; part++) {")
    js_lines.append("        const partIds = ALL_IDS.slice(part * LIMIT, (part + 1) * LIMIT);")
    js_lines.append('        const suffix = numParts > 1 ? " (Part " + (part + 1) + "/" + numParts + ")" : "";')
    js_lines.append('        const title = "MART Import 2026-03-19" + suffix;')
    js_lines.append("")
    js_lines.append("        // Create with first batch")
    js_lines.append("        const firstBatch = partIds.slice(0, BATCH);")
    js_lines.append('        console.log("Creating " + title + " with " + partIds.length + " tracks...");')
    js_lines.append('        const createResp = await fetch("https://api-v2.soundcloud.com/playlists?client_id=" + cid, {')
    js_lines.append('            method: "POST", headers,')
    js_lines.append("            body: JSON.stringify({ playlist: { title: title, sharing: \"private\", tracks: firstBatch } })")
    js_lines.append("        });")
    js_lines.append("")
    js_lines.append("        if (!createResp.ok) {")
    js_lines.append('            console.error("Failed to create " + title + ":", createResp.status, await createResp.text());')
    js_lines.append("            continue;")
    js_lines.append("        }")
    js_lines.append("")
    js_lines.append("        const pl = await createResp.json();")
    js_lines.append("        playlists.push({ id: pl.id, url: pl.permalink_url, title: title });")
    js_lines.append('        console.log("Created: " + pl.permalink_url);')
    js_lines.append("")
    js_lines.append("        // Add remaining in batches")
    js_lines.append("        let added = firstBatch.slice();")
    js_lines.append("        for (let i = BATCH; i < partIds.length; i += BATCH) {")
    js_lines.append("            added = added.concat(partIds.slice(i, i + BATCH));")
    js_lines.append("            for (let attempt = 0; attempt < 3; attempt++) {")
    js_lines.append('                const r = await fetch("https://api-v2.soundcloud.com/playlists/" + pl.id + "?client_id=" + cid, {')
    js_lines.append('                    method: "PUT", headers,')
    js_lines.append("                    body: JSON.stringify({ playlist: { tracks: added } })")
    js_lines.append("                });")
    js_lines.append("                if (r.ok) {")
    js_lines.append('                    console.log("  [" + Math.min(i + BATCH, partIds.length) + "/" + partIds.length + "]");')
    js_lines.append("                    break;")
    js_lines.append("                } else if (r.status === 429) {")
    js_lines.append("                    const w = 30 * (attempt + 1);")
    js_lines.append('                    console.log("  Rate limited, waiting " + w + "s...");')
    js_lines.append("                    await new Promise(r => setTimeout(r, w * 1000));")
    js_lines.append("                } else {")
    js_lines.append('                    console.error("  Batch failed:", r.status); break;')
    js_lines.append("                }")
    js_lines.append("            }")
    js_lines.append("            await new Promise(r => setTimeout(r, 2000));")
    js_lines.append("        }")
    js_lines.append("")
    js_lines.append("        // Pause between playlists")
    js_lines.append("        if (part < numParts - 1) await new Promise(r => setTimeout(r, 5000));")
    js_lines.append("    }")
    js_lines.append("")
    js_lines.append('    console.log("\\nDone! Created " + playlists.length + " playlist(s):");')
    js_lines.append("    playlists.forEach(p => console.log(\"  \" + p.title + \": \" + p.url));")
    js_lines.append("})();")

    with open(output, "w", encoding="utf-8") as f:
        f.write("\n".join(js_lines))

    click.echo(f"Written to {output}")


# ---------------------------------------------------------------------------
# 3. Swap tool — generate a JS snippet to replace a track in a playlist
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("playlist_id", type=int)
@click.argument("old_url")
@click.argument("new_url")
@click.option("--output", default="swap.js", type=click.Path())
def swap(playlist_id: int, old_url: str, new_url: str, output: str):
    """Generate JS to swap a track in a SoundCloud playlist.

    PLAYLIST_ID: numeric playlist ID (from the create output)
    OLD_URL: SoundCloud URL of the track to remove
    NEW_URL: SoundCloud URL of the replacement track
    """
    js = f"""// MART Track Swap — paste into soundcloud.com console
(async () => {{
    const PLAYLIST_ID = {playlist_id};
    const OLD_URL = "{old_url}";
    const NEW_URL = "{new_url}";

    const token = document.cookie.match(/oauth_token=([^;]+)/)?.[1];
    if (!token) {{ console.error("Not logged in!"); return; }}

    const scripts = document.querySelectorAll('script[src*="sndcdn.com"]');
    let cid = null;
    for (const s of scripts) {{
        try {{
            const r = await fetch(s.src);
            const t = await r.text();
            const m = t.match(/client_id:"([a-zA-Z0-9]{{32}})"/);
            if (m) {{ cid = m[1]; break; }}
        }} catch(e) {{}}
    }}

    const headers = {{ "Authorization": "OAuth " + token, "Content-Type": "application/json" }};

    // Resolve both URLs to track IDs
    console.log("Resolving track URLs...");
    const [oldResp, newResp] = await Promise.all([
        fetch("https://api-v2.soundcloud.com/resolve?url=" + encodeURIComponent(OLD_URL) + "&client_id=" + cid),
        fetch("https://api-v2.soundcloud.com/resolve?url=" + encodeURIComponent(NEW_URL) + "&client_id=" + cid)
    ]);

    if (!oldResp.ok) {{ console.error("Could not resolve old URL:", oldResp.status); return; }}
    if (!newResp.ok) {{ console.error("Could not resolve new URL:", newResp.status); return; }}

    const oldTrack = await oldResp.json();
    const newTrack = await newResp.json();
    console.log("Old: " + oldTrack.title + " by " + oldTrack.user.username + " (id: " + oldTrack.id + ")");
    console.log("New: " + newTrack.title + " by " + newTrack.user.username + " (id: " + newTrack.id + ")");

    // Get current playlist
    console.log("Fetching playlist...");
    const plResp = await fetch(
        "https://api-v2.soundcloud.com/playlists/" + PLAYLIST_ID + "?client_id=" + cid,
        {{ headers: {{ "Authorization": "OAuth " + token }} }}
    );
    if (!plResp.ok) {{ console.error("Could not fetch playlist:", plResp.status); return; }}

    const pl = await plResp.json();
    const trackIds = pl.tracks.map(t => t.id);
    console.log("Playlist has " + trackIds.length + " tracks");

    const idx = trackIds.indexOf(oldTrack.id);
    if (idx === -1) {{ console.error("Old track not found in playlist!"); return; }}

    // Swap
    trackIds[idx] = newTrack.id;

    console.log("Swapping track at position " + (idx + 1) + "...");
    const putResp = await fetch(
        "https://api-v2.soundcloud.com/playlists/" + PLAYLIST_ID + "?client_id=" + cid,
        {{
            method: "PUT",
            headers,
            body: JSON.stringify({{ playlist: {{ tracks: trackIds }} }})
        }}
    );

    if (putResp.ok) {{
        console.log("Swapped! " + oldTrack.title + " -> " + newTrack.title);
    }} else {{
        console.error("Swap failed:", putResp.status, await putResp.text());
    }}
}})();
"""
    with open(output, "w", encoding="utf-8") as f:
        f.write(js)

    click.echo(f"Swap script written to {output}")
    click.echo(f"Paste into SoundCloud console to execute.")


if __name__ == "__main__":
    cli()
