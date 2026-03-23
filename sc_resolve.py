"""Resolve SoundCloud URLs to track IDs. Saves progress to track_ids.json so it can resume."""

import json
import time
from pathlib import Path

import click
import httpx

from sc_search import get_client_id, RATE_LIMIT

PROGRESS_FILE = "track_ids.json"


def load_progress() -> dict:
    if Path(PROGRESS_FILE).exists():
        with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"resolved": {}, "dead": []}


def save_progress(data: dict) -> None:
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()


@click.command()
@click.option("--urls", default="soundcloud_urls.txt", type=click.Path(exists=True))
def main(urls: str):
    with open(urls, "r", encoding="utf-8") as f:
        url_list = [line.strip() for line in f if line.strip()]

    progress = load_progress()
    already = len(progress["resolved"])
    click.echo(f"Loaded {len(url_list)} URLs, {already} already resolved")

    with httpx.Client(timeout=httpx.Timeout(30, connect=10), follow_redirects=True) as client:
        client_id = get_client_id(client)
        click.echo(f"client_id: {client_id}\n")

        for i, url in enumerate(url_list):
            if url in progress["resolved"] or url in progress["dead"]:
                continue

            for attempt in range(3):
                time.sleep(RATE_LIMIT)
                try:
                    resp = client.get(
                        "https://api-v2.soundcloud.com/resolve",
                        params={"url": url, "client_id": client_id},
                    )

                    if resp.status_code == 429:
                        wait = 60 * (attempt + 1)
                        click.echo(f"  429 rate limited, waiting {wait}s...")
                        time.sleep(wait)
                        continue

                    if resp.status_code in (404, 403):
                        progress["dead"].append(url)
                        break

                    if resp.is_success:
                        data = resp.json()
                        track_id = data.get("id")
                        if track_id:
                            progress["resolved"][url] = track_id
                        break

                except httpx.HTTPError as e:
                    click.echo(f"  Error: {e}, retrying...")
                    time.sleep(5)

            # Save after every track
            save_progress(progress)

            total_done = len(progress["resolved"]) + len(progress["dead"])
            if total_done % 50 == 0:
                click.echo(f"[{total_done}/{len(url_list)}] resolved: {len(progress['resolved'])}, dead: {len(progress['dead'])}")

    click.echo(f"\nDone! {len(progress['resolved'])} resolved, {len(progress['dead'])} dead")
    click.echo(f"Saved to {PROGRESS_FILE}")

    # Generate browser script with batched playlist creation
    track_ids = list(progress["resolved"].values())
    batch_size = 100
    js = f"""// MART Playlist Creator — paste into soundcloud.com console
// {len(track_ids)} tracks, added in batches of {batch_size}
(async () => {{
    const IDS = {json.dumps(track_ids)};
    const BATCH = {batch_size};
    const token = document.cookie.match(/oauth_token=([^;]+)/)?.[1];
    if (!token) {{ console.error("Not logged in! Make sure you're on soundcloud.com and logged in."); return; }}

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
    if (!cid) {{ console.error("Could not find client_id"); return; }}

    const headers = {{
        "Authorization": `OAuth ${{token}}`,
        "Content-Type": "application/json"
    }};

    // Step 1: Create playlist with first batch
    const firstBatch = IDS.slice(0, BATCH);
    console.log(`Creating playlist with first ${{firstBatch.length}} tracks...`);
    const createResp = await fetch(
        `https://api-v2.soundcloud.com/playlists?client_id=${{cid}}`,
        {{
            method: "POST",
            headers,
            body: JSON.stringify({{
                playlist: {{
                    title: "MART Import {time.strftime('%Y-%m-%d')}",
                    sharing: "private",
                    tracks: firstBatch.map(id => ({{ id }}))
                }}
            }})
        }}
    );

    if (!createResp.ok) {{
        console.error("Failed to create playlist:", createResp.status, await createResp.text());
        return;
    }}

    const pl = await createResp.json();
    const playlistId = pl.id;
    console.log(`Playlist created (id: ${{playlistId}}). Adding remaining tracks...`);

    // Step 2: Add remaining tracks in batches via PUT
    let allAdded = firstBatch.slice();
    let failures = 0;

    for (let i = BATCH; i < IDS.length; i += BATCH) {{
        const batch = IDS.slice(i, i + BATCH);
        allAdded = allAdded.concat(batch);

        for (let attempt = 0; attempt < 3; attempt++) {{
            const putResp = await fetch(
                `https://api-v2.soundcloud.com/playlists/${{playlistId}}?client_id=${{cid}}`,
                {{
                    method: "PUT",
                    headers,
                    body: JSON.stringify({{
                        playlist: {{
                            tracks: allAdded.map(id => ({{ id }}))
                        }}
                    }})
                }}
            );

            if (putResp.ok) {{
                console.log(`[${{Math.min(i + BATCH, IDS.length)}}/${{IDS.length}}] added`);
                break;
            }} else if (putResp.status === 429) {{
                const wait = 30 * (attempt + 1);
                console.log(`Rate limited, waiting ${{wait}}s...`);
                await new Promise(r => setTimeout(r, wait * 1000));
            }} else {{
                console.error(`Batch failed: ${{putResp.status}}`, await putResp.text());
                failures++;
                break;
            }}
        }}

        // Small delay between batches
        await new Promise(r => setTimeout(r, 2000));
    }}

    console.log(`Done! ${{allAdded.length}} tracks in playlist. Failures: ${{failures}}`);
    console.log(`Playlist: ${{pl.permalink_url}}`);
}})();
"""
    with open("create_playlist.js", "w", encoding="utf-8") as f:
        f.write(js)
    click.echo(f"Generated create_playlist.js — paste into SoundCloud console to create playlist")


if __name__ == "__main__":
    main()
