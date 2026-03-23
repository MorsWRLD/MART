"""Generate a JS script to paste into SoundCloud's browser console for bulk-liking.

Strategy: resolve all URLs to track IDs first, then add them to a playlist in one
API call (avoids 1662 individual like requests and rate limiting).
Falls back to batch-liking with exponential backoff if playlist creation fails.
"""

import json


def main():
    with open("soundcloud_urls.txt", "r", encoding="utf-8") as f:
        urls = [line.strip() for line in f if line.strip()]

    js = f"""
// MART Bulk Importer v2 — paste into browser console on soundcloud.com
// Strategy: resolve URLs → create playlist with all tracks (1 API call)
// {len(urls)} tracks
(async () => {{
    const URLS = {json.dumps(urls)};
    const BATCH = 50;       // resolve 50, then pause
    const DELAY = 1200;     // ms between resolve requests
    const BACKOFF = 60000;  // ms to wait on 429

    const sleep = ms => new Promise(r => setTimeout(r, ms));

    // Get client_id
    const scripts = document.querySelectorAll('script[src*="sndcdn.com"]');
    let clientId = null;
    for (const s of scripts) {{
        try {{
            const resp = await fetch(s.src);
            const text = await resp.text();
            const m = text.match(/client_id:"([a-zA-Z0-9]{{32}})"/);
            if (m) {{ clientId = m[1]; break; }}
        }} catch (e) {{}}
    }}
    if (!clientId) {{ console.error("No client_id found"); return; }}
    console.log("client_id:", clientId);

    const token = document.cookie.match(/oauth_token=([^;]+)/)?.[1];
    if (!token) {{ console.error("Not logged in!"); return; }}
    console.log("oauth_token found");

    // Phase 1: Resolve all URLs to track IDs
    console.log("\\n=== PHASE 1: Resolving URLs to track IDs ===");
    const trackIds = [];
    const deadLinks = [];

    for (let i = 0; i < URLS.length; i++) {{
        const url = URLS[i];
        let resolved = false;

        for (let attempt = 0; attempt < 3; attempt++) {{
            try {{
                const resp = await fetch(
                    `https://api-v2.soundcloud.com/resolve?url=${{encodeURIComponent(url)}}&client_id=${{clientId}}`
                );

                if (resp.status === 429) {{
                    console.log(`  Rate limited at ${{i+1}}, waiting 60s...`);
                    await sleep(BACKOFF);
                    continue;
                }}

                if (resp.status === 404 || resp.status === 403) {{
                    deadLinks.push(url);
                    resolved = true;
                    break;
                }}

                if (resp.ok) {{
                    const data = await resp.json();
                    if (data.id) trackIds.push(data.id);
                    resolved = true;
                    break;
                }}
            }} catch (e) {{
                await sleep(5000);
            }}
        }}

        if (!resolved) deadLinks.push(url);

        if ((i + 1) % 50 === 0) {{
            console.log(`  Resolved ${{i+1}}/${{URLS.length}} (${{trackIds.length}} found, ${{deadLinks.length}} dead)`);
        }}

        await sleep(DELAY);

        // Extra pause every batch to be safe
        if ((i + 1) % BATCH === 0) {{
            console.log(`  Batch pause (10s)...`);
            await sleep(10000);
        }}
    }}

    console.log(`\\nResolved: ${{trackIds.length}} tracks, ${{deadLinks.length}} dead links`);

    if (trackIds.length === 0) {{
        console.error("No tracks resolved!");
        return;
    }}

    // Phase 2: Try creating a playlist with all tracks (1 API call!)
    console.log("\\n=== PHASE 2: Creating playlist ===");

    const playlistResp = await fetch(
        `https://api-v2.soundcloud.com/playlists?client_id=${{clientId}}`,
        {{
            method: "POST",
            headers: {{
                "Authorization": `OAuth ${{token}}`,
                "Content-Type": "application/json"
            }},
            body: JSON.stringify({{
                playlist: {{
                    title: "MART Import " + new Date().toISOString().slice(0, 10),
                    sharing: "private",
                    tracks: trackIds.map(id => ({{ id }}))
                }}
            }})
        }}
    );

    if (playlistResp.ok) {{
        const pl = await playlistResp.json();
        console.log(`\\nPlaylist created: ${{pl.permalink_url}}`);
        console.log(`${{trackIds.length}} tracks added!`);
        console.log(`${{deadLinks.length}} dead links skipped`);
        console.log("\\nDone! Check your Library > Playlists");
        return;
    }}

    console.log(`Playlist creation returned ${{playlistResp.status}}, falling back to batch likes...`);

    // Phase 3 (fallback): Like tracks in batches with backoff
    console.log("\\n=== PHASE 3 (fallback): Liking tracks individually ===");
    let liked = 0, fails = 0;

    for (let i = 0; i < trackIds.length; i++) {{
        let success = false;

        for (let attempt = 0; attempt < 3; attempt++) {{
            const resp = await fetch(
                `https://api-v2.soundcloud.com/users/726635713/track_likes/${{trackIds[i]}}?client_id=${{clientId}}`,
                {{
                    method: "PUT",
                    headers: {{ "Authorization": `OAuth ${{token}}` }}
                }}
            );

            if (resp.ok || resp.status === 422) {{
                liked++;
                success = true;
                break;
            }}

            if (resp.status === 429) {{
                const wait = BACKOFF * (attempt + 1);
                console.log(`  429 at track ${{i+1}}, waiting ${{wait/1000}}s...`);
                await sleep(wait);
                continue;
            }}

            break;
        }}

        if (!success) fails++;

        if ((i + 1) % 20 === 0) {{
            console.log(`  [${{i+1}}/${{trackIds.length}}] Liked: ${{liked}}, Failed: ${{fails}}`);
        }}

        await sleep(DELAY);

        if ((i + 1) % BATCH === 0) {{
            console.log(`  Batch pause (15s)...`);
            await sleep(15000);
        }}
    }}

    console.log("\\n=== DONE ===");
    console.log(`Liked: ${{liked}}`);
    console.log(`Failed: ${{fails}}`);
    console.log(`Dead links: ${{deadLinks.length}}`);
}})();
"""

    with open("like_script_v2.js", "w", encoding="utf-8") as f:
        f.write(js)

    print(f"Generated like_script_v2.js with {len(urls)} URLs")
    print(f"File size: {len(js) // 1024}KB")
    print()
    print("How it works:")
    print("  Phase 1: Resolves all URLs to track IDs (with backoff on rate limits)")
    print("  Phase 2: Creates a SINGLE playlist with all tracks (1 API call)")
    print("  Phase 3: Falls back to individual likes if playlist fails")
    print()
    print("Instructions:")
    print("  1. Refresh soundcloud.com (clear any rate limit state)")
    print("  2. F12 → Console → type 'allow pasting' → Enter")
    print("  3. Paste contents of like_script_v2.js → Enter")
    print("  4. Let it run (~40 min for resolving, then instant playlist creation)")


if __name__ == "__main__":
    main()
