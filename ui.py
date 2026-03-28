"""MART Web UI — local Flask server for playlist management."""

from __future__ import annotations

import json
import time
import re
from pathlib import Path

import httpx
from flask import Flask, jsonify, request, send_from_directory

from sc_search import get_client_id, RATE_LIMIT, _rate_limit, search_soundcloud_multi
from query_clean import generate_queries
from matcher import llm_match, _get_provider, _call_openai_compatible, _call_anthropic
from classifier import classify_all, load_tags, TAGS_FILE
from importer import import_file
from vk_import import Track

app = Flask(__name__, static_folder="static")


@app.errorhandler(Exception)
def handle_exception(e):
    """Return JSON for all errors, never HTML."""
    import traceback
    traceback.print_exc()
    return jsonify({"ok": False, "error": str(e)}), 500


@app.errorhandler(404)
def handle_404(e):
    return jsonify({"ok": False, "error": "Not found"}), 404


@app.errorhandler(405)
def handle_405(e):
    return jsonify({"ok": False, "error": "Method not allowed"}), 405

# Session state — persisted parts saved to SESSION_FILE
_state = {
    "oauth_token": None,
    "llm_api_key": None,
    "sc_cookies": None,  # full cookie string from SoundCloud browser session
    "client_id": None,
    "http_client": None,
    "playlists": [],  # [{id, url, title, track_ids}]
    "staged_swaps": {},  # key -> {old_url, new_url, new_title, new_uploader}
}

RESULTS_FILE = Path("results.json")
TRACK_IDS_FILE = Path("track_ids.json")
SESSION_FILE = Path("session.json")


def _save_session() -> None:
    """Persist token, playlists, staged swaps, and LLM key to disk."""
    data = {
        "oauth_token": _state["oauth_token"],
        "llm_api_key": _state.get("llm_api_key"),
        "sc_cookies": _state.get("sc_cookies"),
        "playlists": [{"id": p["id"], "url": p["url"], "title": p["title"]} for p in _state["playlists"]],
        "staged_swaps": _state["staged_swaps"],
    }
    with open(SESSION_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()


def _detect_llm_env_var(key: str) -> str:
    """Detect which env var to set based on key prefix."""
    if key.startswith("sk-or-"):
        return "OPENROUTER_API_KEY"
    if key.startswith("sk-ant-"):
        return "ANTHROPIC_API_KEY"
    if key.startswith("sk-"):
        return "OPENAI_API_KEY"
    # Google Gemini keys are typically 39-char alphanumeric starting with "AIza"
    if key.startswith("AIza"):
        return "GEMINI_API_KEY"
    # Default: try as Gemini (most likely for new users with free key)
    return "GEMINI_API_KEY"


def _apply_llm_key() -> None:
    """Set the LLM API key as env var so matcher.py picks it up."""
    import os
    key = _state.get("llm_api_key")
    if key:
        env_var = _detect_llm_env_var(key)
        # Clear all provider env vars first to avoid conflicts
        for var in ("GEMINI_API_KEY", "OPENROUTER_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
            os.environ.pop(var, None)
        os.environ[env_var] = key


def _load_session() -> None:
    """Restore session from disk on startup."""
    if not SESSION_FILE.exists():
        return
    try:
        with open(SESSION_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        _state["oauth_token"] = data.get("oauth_token")
        _state["llm_api_key"] = data.get("llm_api_key")
        _state["sc_cookies"] = data.get("sc_cookies")
        _state["playlists"] = data.get("playlists", [])
        _state["staged_swaps"] = data.get("staged_swaps", {})
        _apply_llm_key()
    except (json.JSONDecodeError, KeyError):
        pass  # corrupted file, start fresh


def _get_client() -> httpx.Client:
    if _state["http_client"] is None:
        _state["http_client"] = httpx.Client(
            timeout=httpx.Timeout(30, connect=10),
            follow_redirects=True,
        )
    return _state["http_client"]


def _get_client_id() -> str:
    if _state["client_id"] is None:
        _state["client_id"] = get_client_id(_get_client())
    return _state["client_id"]


def _sc_api(method: str, path: str, **kwargs) -> httpx.Response:
    """Make an authenticated SC API call."""
    client = _get_client()
    cid = _get_client_id()
    token = _state["oauth_token"]

    url = f"https://api-v2.soundcloud.com{path}"
    params = kwargs.pop("params", {})
    params["client_id"] = cid

    headers = kwargs.pop("headers", {})
    # Browser-like headers to avoid DataDome captcha on write operations
    headers.setdefault("Origin", "https://soundcloud.com")
    headers.setdefault("Referer", "https://soundcloud.com/")
    headers.setdefault("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
    headers.setdefault("Accept", "application/json, text/javascript, */*; q=0.01")
    if token:
        headers["Authorization"] = f"OAuth {token}"

    # For write operations, include the user's browser cookies (needed for DataDome)
    sc_cookies = _state.get("sc_cookies")
    if sc_cookies and method.upper() in ("POST", "PUT", "PATCH", "DELETE"):
        headers["Cookie"] = sc_cookies

    return client.request(method, url, params=params, headers=headers, **kwargs)


def _load_results() -> dict:
    if RESULTS_FILE.exists():
        with open(RESULTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"metadata": {}, "tracks": {}}


def _load_track_ids() -> dict:
    if TRACK_IDS_FILE.exists():
        with open(TRACK_IDS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"resolved": {}, "dead": []}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.after_request
def add_no_cache(response):
    """Prevent browser from caching HTML/JS so updates always take effect."""
    if response.content_type and ("html" in response.content_type or "javascript" in response.content_type):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return response


@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/api/tracks")
def api_tracks():
    """Return all tracks from results.json."""
    data = _load_results()
    tracks = []
    for key, entry in data["tracks"].items():
        tracks.append({
            "key": key,
            "artist": entry["source"]["artist"],
            "title": entry["source"]["title"],
            "duration": entry["source"].get("duration"),
            "status": entry["status"],
            "match": entry.get("match"),
            "candidates_count": entry.get("candidates_count", 0),
            "reason": entry.get("llm_reason", ""),
        })
    return jsonify({
        "metadata": data["metadata"],
        "tracks": tracks,
    })


@app.route("/api/connect", methods=["POST"])
def api_connect():
    """Store OAuth token and cookies, then validate."""
    body = request.get_json()
    token = body.get("token", "").strip()
    if not token:
        return jsonify({"ok": False, "error": "No token provided"}), 400

    _state["oauth_token"] = token

    # Store browser cookies (needed for playlist write operations)
    cookies = body.get("cookies", "").strip()
    if cookies:
        _state["sc_cookies"] = cookies

    # Validate by fetching user profile
    try:
        resp = _sc_api("GET", "/me")
        if resp.status_code == 401:
            _state["oauth_token"] = None
            return jsonify({"ok": False, "error": "Invalid token"}), 401
        resp.raise_for_status()
        user = resp.json()
        _save_session()
        return jsonify({
            "ok": True,
            "user": {
                "username": user.get("username", ""),
                "id": user.get("id"),
                "avatar": user.get("avatar_url", ""),
            },
        })
    except Exception as e:
        _state["oauth_token"] = None
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/session")
def api_session():
    """Return current session state (for auto-restore on page load)."""
    tags = load_tags()
    tags_count = len(tags.get("tracks", {}))
    result = {
        "connected": _state["oauth_token"] is not None,
        "has_llm_key": bool(_state.get("llm_api_key")),
        "has_sc_cookies": bool(_state.get("sc_cookies")),
        "has_tags": tags_count > 0,
        "tags_count": tags_count,
        "playlists": [{"id": p["id"], "url": p["url"], "title": p["title"]} for p in _state["playlists"]],
        "staged_swaps": _state["staged_swaps"],
    }

    # If we have a token, validate and return user info
    if _state["oauth_token"]:
        try:
            resp = _sc_api("GET", "/me")
            if resp.is_success:
                user = resp.json()
                result["user"] = {
                    "username": user.get("username", ""),
                    "id": user.get("id"),
                    "avatar": user.get("avatar_url", ""),
                }
            else:
                # Token expired
                result["connected"] = False
                result["token_expired"] = True
        except Exception:
            result["connected"] = False

    return jsonify(result)


@app.route("/api/settings", methods=["POST"])
def api_settings():
    """Save app settings (LLM provider key, SC cookies, etc)."""
    try:
        body = request.get_json()
        changed = False

        llm = body.get("llm", "").strip()
        if llm:
            _state["llm_api_key"] = llm
            _apply_llm_key()
            changed = True

        cookies = body.get("sc_cookies", "").strip()
        if cookies:
            _state["sc_cookies"] = cookies
            changed = True

        if changed:
            _save_session()
        return jsonify({"ok": True})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/staged", methods=["GET"])
def api_staged_get():
    """Return all staged swaps."""
    return jsonify({"staged_swaps": _state["staged_swaps"]})


@app.route("/api/staged", methods=["POST"])
def api_staged_save():
    """Save staged swaps (called on every stage/unstage)."""
    body = request.get_json()
    _state["staged_swaps"] = body.get("staged_swaps", {})
    _save_session()
    return jsonify({"ok": True})


@app.route("/api/search")
def api_search():
    """Search SoundCloud for tracks."""
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"results": []})

    _rate_limit()
    try:
        resp = _sc_api("GET", "/search/tracks", params={"q": q, "limit": 15, "offset": 0})
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    results = []
    for item in data.get("collection", []):
        dur_ms = item.get("duration")
        results.append({
            "id": item.get("id"),
            "title": item.get("title", ""),
            "uploader": item.get("user", {}).get("username", ""),
            "duration": round(dur_ms / 1000) if dur_ms else None,
            "url": item.get("permalink_url", ""),
            "play_count": item.get("playback_count"),
            "artwork": item.get("artwork_url", ""),
        })

    return jsonify({"results": results})


@app.route("/api/resolve")
def api_resolve():
    """Resolve a SoundCloud URL to track info."""
    url = request.args.get("url", "").strip()
    if not url:
        return jsonify({"error": "No URL"}), 400

    _rate_limit()
    try:
        resp = _sc_api("GET", "/resolve", params={"url": url})
        if not resp.is_success:
            return jsonify({"error": f"Status {resp.status_code}"}), resp.status_code
        data = resp.json()
        dur_ms = data.get("duration")
        return jsonify({
            "id": data.get("id"),
            "title": data.get("title", ""),
            "uploader": data.get("user", {}).get("username", ""),
            "duration": round(dur_ms / 1000) if dur_ms else None,
            "url": data.get("permalink_url", ""),
            "play_count": data.get("playback_count"),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _resolve_url_to_id(url: str) -> int | None:
    """Resolve a SoundCloud URL to a track ID via the API."""
    _rate_limit()
    try:
        resp = _sc_api("GET", "/resolve", params={"url": url})
        if resp.is_success:
            return resp.json().get("id")
    except Exception:
        pass
    return None


@app.route("/api/playlists/create", methods=["POST"])
def api_playlists_create():
    """Generate a JS script for the user to run on soundcloud.com console.

    SoundCloud's DataDome anti-bot blocks server-side POST requests regardless
    of cookies/headers. The only way to create playlists is from the user's
    actual browser session on soundcloud.com.
    """
    if not _state["oauth_token"]:
        return jsonify({"error": "Not connected"}), 401

    # Build complete ID list from results.json + track_ids.json
    results = _load_results()
    id_data = _load_track_ids()
    resolved = dict(id_data.get("resolved", {}))

    # Collect all matched URLs
    matched_urls = []
    for entry in results["tracks"].values():
        if entry["status"] == "matched" and entry.get("match", {}).get("url"):
            matched_urls.append(entry["match"]["url"])

    if not matched_urls:
        return jsonify({"error": "No matched tracks found"}), 400

    # Resolve any missing URLs (GET requests work fine server-side)
    unresolved = [u for u in matched_urls if u not in resolved]
    newly_resolved = 0
    for url in unresolved:
        tid = _resolve_url_to_id(url)
        if tid:
            resolved[url] = tid
            newly_resolved += 1

    if newly_resolved:
        id_data["resolved"] = resolved
        with open(TRACK_IDS_FILE, "w", encoding="utf-8") as f:
            json.dump(id_data, f, ensure_ascii=False, indent=2)

    # Build final deduplicated ID list
    all_ids = []
    for url in matched_urls:
        tid = resolved.get(url)
        if tid and tid not in all_ids:
            all_ids.append(tid)

    if not all_ids:
        return jsonify({"error": "No track IDs could be resolved"}), 400

    cid = _get_client_id()

    # Generate the JS script
    script = _generate_playlist_script(all_ids, cid)

    return jsonify({
        "ok": True,
        "total_tracks": len(all_ids),
        "newly_resolved": newly_resolved,
        "script": script,
    })


def _generate_playlist_script(all_ids: list[int], client_id: str, playlist_title: str = "MART Import") -> str:
    """Generate a JavaScript snippet that creates playlists when run on soundcloud.com."""
    ids_json = json.dumps(all_ids)
    return f"""(async () => {{
  const ALL_IDS = {ids_json};
  const PLAYLIST_TITLE = "{playlist_title}";
  const CLIENT_ID = "{client_id}";
  const LIMIT = 500;
  const BATCH = 100;
  const DELAY = ms => new Promise(r => setTimeout(r, ms));

  // Extract OAuth token from browser cookies
  const TOKEN = document.cookie.match(/oauth_token=([^;]+)/)?.[1];
  if (!TOKEN) {{
    console.error('MART: No oauth_token found in cookies! Make sure you are logged in to SoundCloud.');
    return;
  }}
  const HEADERS = {{'Content-Type': 'application/json', 'Authorization': `OAuth ${{TOKEN}}`}};

  const numParts = Math.ceil(ALL_IDS.length / LIMIT);
  console.log(`MART: Creating ${{numParts}} playlist(s) for ${{ALL_IDS.length}} tracks...`);

  const results = [];

  for (let part = 0; part < numParts; part++) {{
    const partIds = ALL_IDS.slice(part * LIMIT, (part + 1) * LIMIT);
    const suffix = numParts > 1 ? ` (Part ${{part + 1}}/${{numParts}})` : '';
    const title = `${{PLAYLIST_TITLE}}${{suffix}}`;

    // Create with first batch
    const firstBatch = partIds.slice(0, BATCH);
    console.log(`MART: Creating "${{title}}" with ${{partIds.length}} tracks...`);

    let resp;
    try {{
      resp = await fetch(`https://api-v2.soundcloud.com/playlists?client_id=${{CLIENT_ID}}`, {{
        method: 'POST',
        headers: HEADERS,
        credentials: 'include',
        body: JSON.stringify({{playlist: {{title, sharing: 'private', tracks: firstBatch}}}})
      }});
    }} catch (e) {{
      console.error(`MART: Network error creating ${{title}}:`, e);
      continue;
    }}

    if (!resp.ok) {{
      const txt = await resp.text();
      console.error(`MART: Failed to create ${{title}}: ${{resp.status}}`, txt.slice(0, 200));
      continue;
    }}

    const pl = await resp.json();
    const plId = pl.id;
    const plUrl = pl.permalink_url || '';
    console.log(`MART: Created "${{title}}" (id: ${{plId}}), adding remaining tracks...`);
    results.push({{id: plId, url: plUrl, title}});

    // Add remaining in batches
    let added = [...firstBatch];
    for (let i = BATCH; i < partIds.length; i += BATCH) {{
      const batch = partIds.slice(i, i + BATCH);
      added = added.concat(batch);
      await DELAY(2000);

      for (let attempt = 0; attempt < 3; attempt++) {{
        try {{
          const putResp = await fetch(
            `https://api-v2.soundcloud.com/playlists/${{plId}}?client_id=${{CLIENT_ID}}`,
            {{
              method: 'PUT',
              headers: HEADERS,
              credentials: 'include',
              body: JSON.stringify({{playlist: {{tracks: added}}}})
            }}
          );
          if (putResp.ok) break;
          if (putResp.status === 429) {{ await DELAY(30000 * (attempt + 1)); continue; }}
          break;
        }} catch (e) {{ await DELAY(5000); }}
      }}
      console.log(`MART: ${{title}} — ${{added.length}}/${{partIds.length}} tracks added`);
    }}

    if (part < numParts - 1) await DELAY(5000);
  }}

  console.log('\\n===== MART DONE =====');
  console.log(`Created ${{results.length}} playlist(s):`);
  results.forEach(p => console.log(`  ${{p.title}}: ${{p.url}}`));

  // Report back to MART server
  try {{
    await fetch('http://127.0.0.1:{_get_server_port()}/api/playlists/report', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{playlists: results}})
    }});
    console.log('MART: Results saved to server.');
  }} catch (e) {{
    console.log('MART: Could not report back to server (not critical). Playlists are created.');
  }}
}})();"""


_server_port = 8000  # updated by run_ui()


def _get_server_port() -> int:
    return _server_port


@app.route("/api/playlists/report", methods=["POST", "OPTIONS"])
def api_playlists_report():
    """Callback from the browser script after playlists are created.

    CORS-enabled so soundcloud.com can POST here.
    """
    if request.method == "OPTIONS":
        resp = app.make_default_options_response()
        resp.headers["Access-Control-Allow-Origin"] = "https://soundcloud.com"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        resp.headers["Access-Control-Allow-Methods"] = "POST"
        return resp

    body = request.get_json(silent=True) or {}
    playlists = body.get("playlists", [])
    _state["playlists"] = [{"id": p["id"], "url": p["url"], "title": p["title"]} for p in playlists]
    _save_session()

    resp = jsonify({"ok": True, "saved": len(playlists)})
    resp.headers["Access-Control-Allow-Origin"] = "https://soundcloud.com"
    return resp


@app.route("/api/playlists/swap", methods=["POST"])
def api_playlists_swap():
    """Apply bulk track swaps to playlists.

    Body: {"swaps": [{"old_url": "...", "new_url": "..."}, ...]}
    """
    if not _state["oauth_token"]:
        return jsonify({"error": "Not connected"}), 401

    body = request.get_json()
    swaps = body.get("swaps", [])
    if not swaps:
        return jsonify({"error": "No swaps provided"}), 400

    # Resolve all URLs to IDs
    resolved = {}
    for s in swaps:
        for url_key in ("old_url", "new_url"):
            url = s.get(url_key)
            if url and url not in resolved:
                _rate_limit()
                try:
                    resp = _sc_api("GET", "/resolve", params={"url": url})
                    if resp.is_success:
                        resolved[url] = resp.json().get("id")
                except Exception:
                    pass

    # Separate swaps (have old_url) from additions (old_url is null)
    replacements = [s for s in swaps if s.get("old_url")]
    additions = [s for s in swaps if not s.get("old_url")]

    playlist_ids = [p["id"] for p in _state.get("playlists", [])]
    if not playlist_ids:
        return jsonify({"error": "No playlists tracked. Create playlists first."}), 400

    results = []
    for pl_info in _state["playlists"]:
        pl_id = pl_info["id"]

        # Fetch current playlist
        _rate_limit()
        try:
            resp = _sc_api("GET", f"/playlists/{pl_id}")
            if not resp.is_success:
                results.append({"playlist_id": pl_id, "error": f"Fetch failed: {resp.status_code}"})
                continue
            pl = resp.json()
        except Exception as e:
            results.append({"playlist_id": pl_id, "error": str(e)})
            continue

        track_ids = [t["id"] for t in pl.get("tracks", [])]
        changed = False

        # Apply replacements
        for s in replacements:
            old_id = resolved.get(s["old_url"])
            new_id = resolved.get(s["new_url"])
            if old_id and new_id and old_id in track_ids:
                idx = track_ids.index(old_id)
                track_ids[idx] = new_id
                changed = True

        # Apply additions (add to first playlist only, or whichever has room)
        if pl_info == _state["playlists"][0]:
            for s in additions:
                new_id = resolved.get(s["new_url"])
                if new_id and new_id not in track_ids:
                    track_ids.append(new_id)
                    changed = True

        if changed:
            _rate_limit()
            try:
                put_resp = _sc_api("PUT", f"/playlists/{pl_id}", json={
                    "playlist": {"tracks": track_ids}
                })
                results.append({
                    "playlist_id": pl_id,
                    "ok": put_resp.is_success,
                    "status": put_resp.status_code,
                })
            except Exception as e:
                results.append({"playlist_id": pl_id, "error": str(e)})
        else:
            results.append({"playlist_id": pl_id, "ok": True, "no_changes": True})

    # Clear staged swaps that were applied
    if any(r.get("ok") for r in results):
        _state["staged_swaps"] = {}
        _save_session()

    return jsonify({"results": results})


@app.route("/api/playlists", methods=["GET"])
def api_playlists():
    """Return currently tracked playlists."""
    return jsonify({
        "playlists": [{"id": p["id"], "url": p["url"], "title": p["title"]} for p in _state.get("playlists", [])]
    })


@app.route("/api/playlists/connect", methods=["POST"])
def api_playlists_connect():
    """Connect to existing playlists by ID (for resuming sessions)."""
    body = request.get_json()
    playlist_id = body.get("id")
    if not playlist_id:
        return jsonify({"error": "No playlist ID"}), 400

    _rate_limit()
    try:
        resp = _sc_api("GET", f"/playlists/{playlist_id}")
        if not resp.is_success:
            return jsonify({"error": f"Status {resp.status_code}"}), resp.status_code
        pl = resp.json()
        track_ids = [t["id"] for t in pl.get("tracks", [])]
        entry = {
            "id": pl["id"],
            "url": pl.get("permalink_url", ""),
            "title": pl.get("title", ""),
            "track_ids": track_ids,
        }
        # Don't duplicate
        if not any(p["id"] == pl["id"] for p in _state["playlists"]):
            _state["playlists"].append(entry)
            _save_session()
        return jsonify({"ok": True, "playlist": {"id": pl["id"], "url": entry["url"], "title": entry["title"], "track_count": len(track_ids)}})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _save_results(data: dict) -> None:
    """Write results to disk."""
    with open(RESULTS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()


@app.route("/api/retry-failed", methods=["POST"])
def api_retry_failed():
    """Re-process all no_match tracks with the improved query pipeline.

    Streams progress via NDJSON.
    """
    import os
    from matcher import _get_provider

    # Fail fast if no LLM key is configured
    try:
        _get_provider()
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 400

    data = _load_results()
    if not data["tracks"]:
        return jsonify({"error": "No results file found"}), 400

    # Collect no_match tracks
    failed_keys = []
    for key, entry in data["tracks"].items():
        if entry["status"] == "no_match":
            failed_keys.append((key, entry))

    if not failed_keys:
        return jsonify({"error": "No failed tracks to retry"}), 400

    client = _get_client()

    def generate():
        recovered = 0
        still_failed = 0

        yield json.dumps({
            "type": "start",
            "total": len(failed_keys),
        }) + "\n"

        for i, (key, entry) in enumerate(failed_keys):
            src = entry["source"]
            original_reason = entry.get("llm_reason", "")
            original_candidates = entry.get("candidates_count", 0)
            track = Track(
                artist=src["artist"],
                title=src["title"],
                duration=src.get("duration"),
            )

            yield json.dumps({
                "type": "progress",
                "current": i + 1,
                "total": len(failed_keys),
                "artist": src["artist"],
                "title": src["title"],
            }) + "\n"

            # Search with improved queries
            try:
                queries = generate_queries(track)
                candidates = search_soundcloud_multi(track, client, queries)
            except Exception as e:
                # Don't overwrite original entry on search failure
                still_failed += 1
                yield json.dumps({
                    "type": "error",
                    "message": f"Search error for {src['artist']} - {src['title']}: {e}",
                }) + "\n"
                continue

            if not candidates:
                still_failed += 1
                continue  # still no results, keep original entry untouched

            # Run LLM matching
            try:
                result = llm_match(track, candidates, client)
            except Exception as e:
                # Don't overwrite original entry on LLM failure
                still_failed += 1
                yield json.dumps({
                    "type": "error",
                    "message": f"LLM error for {src['artist']} - {src['title']}: {e}",
                }) + "\n"
                continue

            if result.candidate:
                data["tracks"][key] = {
                    "source": src,
                    "status": "matched",
                    "match": {
                        "title": result.candidate.title,
                        "uploader": result.candidate.uploader,
                        "url": result.candidate.url,
                        "duration": result.candidate.duration,
                    },
                    "candidates_count": len(candidates),
                    "llm_reason": result.reason,
                }
                data["metadata"]["no_match"] = data["metadata"].get("no_match", 0) - 1
                data["metadata"]["matched"] = data["metadata"].get("matched", 0) + 1
                recovered += 1

                yield json.dumps({
                    "type": "match_found",
                    "key": key,
                    "artist": src["artist"],
                    "title": src["title"],
                    "match_title": result.candidate.title,
                    "match_uploader": result.candidate.uploader,
                    "match_url": result.candidate.url,
                }) + "\n"

                _save_results(data)
            else:
                # Only update if we got MORE candidates than before
                if len(candidates) > original_candidates:
                    data["tracks"][key]["candidates_count"] = len(candidates)
                    data["tracks"][key]["llm_reason"] = result.reason
                    _save_results(data)
                still_failed += 1

        yield json.dumps({
            "type": "done",
            "recovered": recovered,
            "still_failed": still_failed,
            "total": len(failed_keys),
        }) + "\n"

    return app.response_class(generate(), mimetype="application/x-ndjson")


# ---------------------------------------------------------------------------
# AI DJ endpoints
# ---------------------------------------------------------------------------

@app.route("/api/classify", methods=["POST"])
def api_classify():
    """Batch-classify all matched tracks with genre/mood/era tags. Streams NDJSON."""
    from matcher import _get_provider as check_provider

    try:
        check_provider()
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 400

    data = _load_results()
    matched = sum(1 for e in data["tracks"].values() if e["status"] == "matched")
    if not matched:
        return jsonify({"error": "No matched tracks to classify"}), 400

    client = _get_client()

    def generate():
        yield json.dumps({"type": "start", "total_matched": matched}) + "\n"

        def on_progress(batch_num, total_batches, classified):
            # This runs synchronously inside classify_all
            pass

        tags = classify_all(data, client, on_progress=None)

        # Stream with simpler approach: classify_all saves after each batch,
        # but we need to stream progress. Let's do it manually.
        yield json.dumps({
            "type": "done",
            "classified": len(tags.get("tracks", {})),
        }) + "\n"

    # Actually, let's do proper streaming by inlining the classify loop
    def generate_with_progress():
        tags_data = load_tags()
        if "tracks" not in tags_data:
            tags_data["tracks"] = {}

        already = len(tags_data["tracks"])

        to_classify = []
        for key, entry in data["tracks"].items():
            if entry["status"] != "matched":
                continue
            if key in tags_data["tracks"]:
                continue
            src = entry["source"]
            to_classify.append((key, src["artist"], src["title"]))

        total = len(to_classify)
        yield json.dumps({
            "type": "start",
            "total": total,
            "already_classified": already,
        }) + "\n"

        if total == 0:
            yield json.dumps({"type": "done", "classified": already, "new": 0}) + "\n"
            return

        from classifier import classify_batch, save_tags, BATCH_SIZE
        from datetime import datetime, timezone

        batch_size = BATCH_SIZE
        total_batches = (total + batch_size - 1) // batch_size
        classified = 0
        errors = 0

        for batch_idx in range(total_batches):
            start = batch_idx * batch_size
            batch = to_classify[start : start + batch_size]

            try:
                results = classify_batch(batch, client)
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
            except Exception as e:
                errors += 1
                yield json.dumps({
                    "type": "error",
                    "message": f"Batch {batch_idx + 1} failed: {e}",
                }) + "\n"
                continue

            tags_data["metadata"] = {
                "classified_at": datetime.now(timezone.utc).isoformat(),
                "total": len(tags_data["tracks"]),
                "model": check_provider()[3],
            }
            save_tags(tags_data)

            yield json.dumps({
                "type": "progress",
                "batch": batch_idx + 1,
                "total_batches": total_batches,
                "classified": classified,
                "sample": f"{batch[0][1]} - {batch[0][2]}",
            }) + "\n"

        yield json.dumps({
            "type": "done",
            "classified": len(tags_data["tracks"]),
            "new": classified,
            "errors": errors,
        }) + "\n"

    return app.response_class(generate_with_progress(), mimetype="application/x-ndjson")


@app.route("/api/tags")
def api_tags():
    """Return the full tag index."""
    tags = load_tags()
    return jsonify({
        "total": len(tags.get("tracks", {})),
        "tags": tags.get("tracks", {}),
    })


@app.route("/api/dj/chat", methods=["POST"])
def api_dj_chat():
    """AI DJ: process a chat message and return track selections."""
    try:
        _get_provider()
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 400

    body = request.get_json()
    message = body.get("message", "").strip()
    history = body.get("history", [])
    if not message:
        return jsonify({"error": "No message"}), 400

    tags_data = load_tags()
    all_tags = tags_data.get("tracks", {})
    if not all_tags:
        return jsonify({"error": "Library not classified yet. Click 'Classify Library' in Settings first."}), 400

    results_data = _load_results()

    # Stage 1: Pre-filter tracks by keyword matching
    candidates = _prefilter_tracks(message, all_tags, results_data)

    # If too few matches, include more tracks
    if len(candidates) < 30:
        # Add random tracks to give the LLM more to work with
        all_keys = list(all_tags.keys())
        import random
        random.shuffle(all_keys)
        existing_keys = {c[0] for c in candidates}
        for k in all_keys:
            if k not in existing_keys and len(candidates) < 300:
                entry = results_data["tracks"].get(k, {})
                if entry.get("status") == "matched":
                    candidates.append((k, 0, all_tags[k]))

    # Stage 2: Build prompt and call LLM
    prompt = _build_dj_prompt(message, history, candidates, results_data, all_tags)
    client = _get_client()
    env_key, api_key, base_url, model = _get_provider()

    try:
        if env_key == "ANTHROPIC_API_KEY":
            raw, _, _ = _call_anthropic(api_key, model, prompt, client)
        else:
            raw, _, _ = _call_openai_compatible(api_key, base_url, model, prompt, client)
    except Exception as e:
        return jsonify({"error": f"LLM error: {e}"}), 500

    # Parse response
    try:
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            cleaned = "\n".join(lines).strip()
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        return jsonify({"error": f"Failed to parse LLM response", "raw": raw[:500]}), 500

    reply = parsed.get("reply", "")
    track_numbers = parsed.get("track_numbers", [])

    # Map track numbers back to actual track data
    selected_tracks = []
    for num in track_numbers:
        if isinstance(num, int) and 1 <= num <= len(candidates):
            key = candidates[num - 1][0]
            entry = results_data["tracks"].get(key, {})
            src = entry.get("source", {})
            match = entry.get("match", {})
            tag = all_tags.get(key, {})
            selected_tracks.append({
                "key": key,
                "artist": src.get("artist", ""),
                "title": src.get("title", ""),
                "url": match.get("url", ""),
                "genres": tag.get("genres", []),
                "mood": tag.get("mood", []),
                "era": tag.get("era", ""),
            })

    return jsonify({
        "reply": reply,
        "tracks": selected_tracks,
    })


def _prefilter_tracks(
    query: str, tags: dict, results_data: dict
) -> list[tuple[str, int, dict]]:
    """Keyword-match user query against tags. Returns [(key, score, tag_dict)]."""
    query_lower = query.lower()
    query_words = set(query_lower.split())

    # Common synonyms
    synonyms = {
        "chill": ["chill", "peaceful", "lo-fi", "ambient"],
        "sad": ["sad", "melancholic", "bittersweet", "emotional"],
        "hype": ["hype", "aggressive", "intense", "party"],
        "workout": ["workout", "hype", "high"],
        "study": ["study", "focus", "lo-fi", "ambient"],
        "coding": ["study", "focus", "lo-fi", "ambient"],
        "night": ["night", "dark", "mysterious"],
        "morning": ["morning", "upbeat", "happy"],
        "summer": ["summer", "happy", "upbeat", "party"],
        "rain": ["rainy-day", "melancholic", "chill"],
        "drive": ["driving", "roadtrip"],
        "sleep": ["sleep", "ambient", "peaceful", "low"],
        "dance": ["party", "dance", "edm", "house"],
        "russian": ["russian"],
        "japanese": ["japanese", "j-pop", "anime"],
        "korean": ["korean", "k-pop"],
        "rap": ["rap", "hip-hop", "trap"],
        "rock": ["rock", "alternative", "indie", "grunge"],
        "classic": ["pre-1970s", "1970s", "1980s"],
        "old": ["pre-1970s", "1970s", "1980s"],
        "new": ["2020s", "2010s"],
        "modern": ["2020s", "2010s"],
        "retro": ["1980s", "1970s", "synthpop", "disco", "new-wave"],
    }

    # Expand query words with synonyms
    expanded = set()
    for word in query_words:
        expanded.add(word)
        if word in synonyms:
            expanded.update(synonyms[word])

    scored = []
    for key, tag_data in tags.items():
        entry = results_data["tracks"].get(key, {})
        if entry.get("status") != "matched":
            continue

        score = 0
        all_tag_values = (
            tag_data.get("genres", [])
            + tag_data.get("mood", [])
            + tag_data.get("vibe", [])
            + [tag_data.get("era", ""), tag_data.get("language", ""), tag_data.get("energy", "")]
        )
        tag_text = " ".join(str(v) for v in all_tag_values).lower()

        for word in expanded:
            if word in tag_text:
                score += 2

        # Also check artist/title
        artist = entry.get("source", {}).get("artist", "").lower()
        title = entry.get("source", {}).get("title", "").lower()
        for word in query_words:
            if word in artist or word in title:
                score += 3

        if score > 0:
            scored.append((key, score, tag_data))

    scored.sort(key=lambda x: -x[1])
    return scored[:500]


def _build_dj_prompt(
    message: str,
    history: list[dict],
    candidates: list[tuple[str, int, dict]],
    results_data: dict,
    all_tags: dict,
) -> str:
    """Build the DJ chat prompt."""
    # Format candidates
    track_lines = []
    for i, (key, score, tag_data) in enumerate(candidates, 1):
        entry = results_data["tracks"].get(key, {})
        src = entry.get("source", {})
        artist = src.get("artist", "")
        title = src.get("title", "")
        genres = ", ".join(tag_data.get("genres", []))
        mood = ", ".join(tag_data.get("mood", []))
        era = tag_data.get("era", "")
        lang = tag_data.get("language", "")
        energy = tag_data.get("energy", "")
        vibe = ", ".join(tag_data.get("vibe", []))
        track_lines.append(
            f'{i}. "{artist} - {title}" [{genres}] [{mood}] [{era}] [{lang}] [{energy}] [{vibe}]'
        )

    # Format history (last 6 messages)
    history_text = ""
    for msg in history[-6:]:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        history_text += f"{role.upper()}: {content}\n"

    return f"""You are an AI DJ curating playlists from a user's personal music library.

The user has {len(all_tags)} tracks total. Below are {len(candidates)} pre-filtered candidates.

USER'S REQUEST: {message}

{f"CONVERSATION HISTORY:{chr(10)}{history_text}" if history_text else ""}
AVAILABLE TRACKS:
{chr(10).join(track_lines)}

INSTRUCTIONS:
- Select tracks that best match the user's request
- You can select 0 to 100 tracks
- Consider genre, mood, era, language, vibe, and energy tags
- Be creative with vague requests ("songs for a rainy drive" = melancholic/chill + driving)
- If the request is about a specific artist, select all their tracks
- If no tracks match, say so honestly
- Give a brief, friendly reply

Respond with ONLY valid JSON, no markdown:
{{"reply": "<1-3 sentence response>", "track_numbers": [<1-based numbers from AVAILABLE TRACKS>]}}"""


@app.route("/api/dj/playlist-script", methods=["POST"])
def api_dj_playlist_script():
    """Generate SC playlist creation script from selected track keys."""
    body = request.get_json()
    track_keys = body.get("track_keys", [])
    title = body.get("title", "AI DJ Playlist")
    if not track_keys:
        return jsonify({"error": "No tracks selected"}), 400

    results_data = _load_results()
    id_data = _load_track_ids()
    resolved = dict(id_data.get("resolved", {}))

    # Collect IDs for the selected tracks
    all_ids = []
    unresolved_urls = []
    for key in track_keys:
        entry = results_data["tracks"].get(key, {})
        url = entry.get("match", {}).get("url", "")
        if not url:
            continue
        tid = resolved.get(url)
        if tid:
            if tid not in all_ids:
                all_ids.append(tid)
        else:
            unresolved_urls.append(url)

    # Resolve any missing
    for url in unresolved_urls:
        tid = _resolve_url_to_id(url)
        if tid:
            resolved[url] = tid
            if tid not in all_ids:
                all_ids.append(tid)

    if unresolved_urls:
        id_data["resolved"] = resolved
        with open(TRACK_IDS_FILE, "w", encoding="utf-8") as f:
            json.dump(id_data, f, ensure_ascii=False, indent=2)

    if not all_ids:
        return jsonify({"error": "No track IDs could be resolved"}), 400

    cid = _get_client_id()
    script = _generate_playlist_script(all_ids, cid, title)

    return jsonify({
        "ok": True,
        "total_tracks": len(all_ids),
        "script": script,
    })


# ---------------------------------------------------------------------------
# Import endpoints
# ---------------------------------------------------------------------------

@app.route("/api/import", methods=["POST"])
def api_import():
    """Import tracks from an uploaded file (Spotify, Apple Music, Yandex, VK, generic).

    Deduplicates against existing results.json. New tracks are added as 'no_match'
    so they can be matched via Rescan Missing.
    """
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    f = request.files["file"]
    if not f.filename:
        return jsonify({"error": "No filename"}), 400

    try:
        content = f.read().decode("utf-8")
    except UnicodeDecodeError:
        try:
            f.seek(0)
            content = f.read().decode("cp1251")  # common for Russian exports
        except Exception:
            return jsonify({"error": "Could not decode file — try saving as UTF-8"}), 400

    try:
        result = import_file(f.filename, content)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"Parse error: {e}"}), 400

    if not result.tracks:
        return jsonify({"error": f"No tracks found in file. Detected format: {result.platform}"}), 400

    # Dedup against existing results
    data = _load_results()
    existing_keys = set(data["tracks"].keys())

    new_tracks = []
    dupes = 0
    for track in result.tracks:
        key = f"{track.artist.lower().strip()}::{track.title.lower().strip()}"
        if key in existing_keys:
            dupes += 1
        else:
            existing_keys.add(key)
            new_tracks.append((key, track))

    # Add new tracks as 'no_match' so Rescan Missing picks them up
    for key, track in new_tracks:
        data["tracks"][key] = {
            "source": {
                "artist": track.artist,
                "title": track.title,
                "duration": track.duration,
            },
            "status": "no_match",
            "match": None,
            "candidates_count": 0,
            "llm_reason": f"Imported from {result.platform}",
        }

    if new_tracks:
        # Update metadata
        data["metadata"]["total_tracks"] = len(data["tracks"])
        data["metadata"]["no_match"] = sum(
            1 for e in data["tracks"].values() if e["status"] == "no_match"
        )
        _save_results(data)

    return jsonify({
        "ok": True,
        "platform": result.platform,
        "parsed": len(result.tracks),
        "new": len(new_tracks),
        "duplicates": dupes,
        "total_library": len(data["tracks"]),
    })


@app.route("/api/yandex/import", methods=["POST"])
def api_yandex_import():
    """Fetch liked tracks from Yandex Music using an API token and import them."""
    body = request.get_json(silent=True) or {}
    token = (body.get("token") or "").strip()
    if not token:
        return jsonify({"error": "No token provided"}), 400

    from yandex_import import fetch_liked_tracks
    tracks = fetch_liked_tracks(token)

    if not tracks:
        return jsonify({"error": "No liked tracks found in Yandex Music library"}), 400

    # Same dedup + save logic as file import
    data = _load_results()
    existing_keys = set(data["tracks"].keys())

    new_tracks = []
    dupes = 0
    for track in tracks:
        key = f"{track.artist.lower().strip()}::{track.title.lower().strip()}"
        if key in existing_keys:
            dupes += 1
        else:
            existing_keys.add(key)
            new_tracks.append((key, track))

    for key, track in new_tracks:
        data["tracks"][key] = {
            "source": {
                "artist": track.artist,
                "title": track.title,
                "duration": track.duration,
            },
            "status": "no_match",
            "match": None,
            "candidates_count": 0,
            "llm_reason": "Imported from Yandex Music",
        }

    if new_tracks:
        data["metadata"]["total_tracks"] = len(data["tracks"])
        data["metadata"]["no_match"] = sum(
            1 for e in data["tracks"].values() if e["status"] == "no_match"
        )
        _save_results(data)

    return jsonify({
        "ok": True,
        "platform": "Yandex Music",
        "parsed": len(tracks),
        "new": len(new_tracks),
        "duplicates": dupes,
        "total_library": len(data["tracks"]),
    })


@app.route("/api/shutdown", methods=["POST"])
def api_shutdown():
    """Cleanly shut down the MART server."""
    import os
    import threading
    threading.Timer(0.3, lambda: os._exit(0)).start()
    return jsonify({"ok": True})


def run_ui(host: str = "127.0.0.1", port: int = 8000):
    """Start the MART UI server."""
    import os
    import webbrowser
    import threading

    global _server_port
    _server_port = port

    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

    _load_session()
    if _state["oauth_token"]:
        print("  Session restored (token + playlists + staged swaps)")

    url = f"http://{host}:{port}"
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    print(f"\n  MART UI running at {url}\n")
    print("  Press Ctrl+C or click Quit in the UI to stop.\n")
    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    run_ui()
