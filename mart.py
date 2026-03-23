"""MART — Music Archive Rescue Tool. CLI entry point and orchestrator."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import click
import httpx

from vk_import import Track, load_tracks
from sc_search import search_soundcloud_multi
from query_clean import generate_queries
from matcher import llm_match


def dedup_key(track: Track) -> str:
    return f"{track.artist.lower().strip()}::{track.title.lower().strip()}"


def load_results(path: Path) -> dict:
    """Load existing results file for resume support, or create a fresh structure."""
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "metadata": {
            "started_at": datetime.now(timezone.utc).isoformat(),
            "source_file": "",
            "total_tracks": 0,
            "processed": 0,
            "matched": 0,
            "no_match": 0,
            "failed": 0,
        },
        "tracks": {},
    }


def save_results(results: dict, path: Path) -> None:
    """Write results to disk (atomic-ish: write + flush)."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
        f.flush()


def save_track_result(
    results: dict,
    key: str,
    track: Track,
    status: str,
    match: dict | None = None,
    candidates_count: int = 0,
    reason: str = "",
) -> None:
    """Save a single track result and update metadata counters."""
    results["tracks"][key] = {
        "source": {
            "artist": track.artist,
            "title": track.title,
            "duration": track.duration,
        },
        "status": status,
        "match": match,
        "candidates_count": candidates_count,
        "llm_reason": reason,
    }
    results["metadata"]["processed"] += 1
    if status == "matched":
        results["metadata"]["matched"] += 1
    elif status == "no_match":
        results["metadata"]["no_match"] += 1
    elif status == "failed":
        results["metadata"]["failed"] += 1


def print_summary(results: dict) -> None:
    meta = results["metadata"]
    click.echo("\n--- Summary ---")
    click.echo(f"Total tracks:  {meta['total_tracks']}")
    click.echo(f"Processed:     {meta['processed']}")
    click.echo(f"Matched:       {meta['matched']}")
    click.echo(f"No match:      {meta['no_match']}")
    click.echo(f"Failed:        {meta['failed']}")


def _make_progress(total: int):
    """Create a progress display. Uses rich if available, else plain counter."""
    try:
        from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeRemainingColumn

        progress = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("{task.completed}/{task.total}"),
            TimeRemainingColumn(),
        )
        return progress, True
    except ImportError:
        return None, False


@click.group(invoke_without_command=True)
@click.pass_context
def main(ctx):
    """MART — Music Archive Rescue Tool. Recover your VK Music library on SoundCloud."""
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


@main.command()
@click.option("--input", "input_file", required=True, type=click.Path(exists=True), help="Path to VK playlist export (JSON or CSV)")
@click.option("--output", "output_file", default="results.json", type=click.Path(), help="Path to results file (default: results.json)")
@click.option("--model", "model", default=None, help="Override LLM model name")
@click.option("--retry-failed", "retry_failed", is_flag=True, help="Re-process tracks with no_match status using improved queries")
def run(input_file: str, output_file: str, model: str | None, retry_failed: bool) -> None:
    """Run the matching pipeline: search SoundCloud and match tracks via LLM."""
    import os
    if model:
        os.environ["MART_MODEL"] = model

    # Load and dedup tracks
    raw_tracks = load_tracks(input_file)
    seen: dict[str, Track] = {}
    tracks: list[Track] = []
    dupes = 0
    for t in raw_tracks:
        key = dedup_key(t)
        if key not in seen:
            seen[key] = t
            tracks.append(t)
        else:
            dupes += 1

    click.echo(f"Loaded {len(raw_tracks)} tracks, {dupes} duplicates removed, {len(tracks)} unique")

    # Load or create results
    output_path = Path(output_file)
    results = load_results(output_path)
    results["metadata"]["source_file"] = str(input_file)
    results["metadata"]["total_tracks"] = len(tracks)

    # --retry-failed: clear no_match entries so they get re-processed
    if retry_failed:
        cleared = 0
        for t in tracks:
            key = dedup_key(t)
            entry = results["tracks"].get(key)
            if entry and entry["status"] == "no_match":
                del results["tracks"][key]
                results["metadata"]["processed"] -= 1
                results["metadata"]["no_match"] -= 1
                cleared += 1
        if cleared:
            click.echo(f"Retry mode: cleared {cleared} no_match entries for re-processing")
            save_results(results, output_path)

    # Count already processed for resume
    skipped = sum(1 for t in tracks if dedup_key(t) in results["tracks"])
    if skipped:
        click.echo(f"Resuming: {skipped} tracks already processed, {len(tracks) - skipped} remaining")

    # Token tracking
    total_input_tokens = 0
    total_output_tokens = 0

    # Process tracks
    progress, use_rich = _make_progress(len(tracks))

    with httpx.Client(
        timeout=httpx.Timeout(30, connect=10),
        follow_redirects=True,
    ) as client:
        if use_rich:
            with progress:
                task_id = progress.add_task("Processing", total=len(tracks), completed=skipped)
                for i, track in enumerate(tracks):
                    key = dedup_key(track)
                    if key in results["tracks"]:
                        continue

                    progress.update(task_id, description=f"{track.artist} — {track.title}"[:60])

                    _process_track(track, key, results, client, output_path)
                    result_entry = results["tracks"][key]
                    if "input_tokens" in result_entry:
                        total_input_tokens += result_entry.pop("input_tokens", 0)
                        total_output_tokens += result_entry.pop("output_tokens", 0)

                    progress.update(task_id, advance=1)
        else:
            for i, track in enumerate(tracks):
                key = dedup_key(track)
                if key in results["tracks"]:
                    continue

                click.echo(f"[{i+1}/{len(tracks)}] {track.artist} — {track.title}")

                _process_track(track, key, results, client, output_path)
                result_entry = results["tracks"][key]
                if "input_tokens" in result_entry:
                    total_input_tokens += result_entry.pop("input_tokens", 0)
                    total_output_tokens += result_entry.pop("output_tokens", 0)

    # Final save and summary
    save_results(results, output_path)
    print_summary(results)

    if total_input_tokens or total_output_tokens:
        click.echo(f"\nLLM tokens used — input: {total_input_tokens:,}, output: {total_output_tokens:,}")


def _process_track(
    track: Track,
    key: str,
    results: dict,
    client: httpx.Client,
    output_path: Path,
) -> None:
    """Search SoundCloud and run LLM matching for a single track."""
    try:
        queries = generate_queries(track)
        candidates = search_soundcloud_multi(track, client, queries)
    except Exception as e:
        save_track_result(results, key, track, status="failed", reason=f"Search error: {e}")
        save_results(results, output_path)
        return

    if not candidates:
        save_track_result(results, key, track, status="no_match", candidates_count=0, reason="No SoundCloud results")
        save_results(results, output_path)
        return

    try:
        result = llm_match(track, candidates, client)
    except Exception as e:
        save_track_result(results, key, track, status="failed", candidates_count=len(candidates), reason=f"LLM error: {e}")
        save_results(results, output_path)
        return

    if result.candidate:
        match_data = {
            "title": result.candidate.title,
            "uploader": result.candidate.uploader,
            "url": result.candidate.url,
            "duration": result.candidate.duration,
        }
        save_track_result(
            results, key, track,
            status="matched",
            match=match_data,
            candidates_count=len(candidates),
            reason=result.reason,
        )
    else:
        save_track_result(
            results, key, track,
            status="no_match",
            candidates_count=len(candidates),
            reason=result.reason,
        )

    # Stash token counts temporarily for aggregation
    results["tracks"][key]["input_tokens"] = result.input_tokens
    results["tracks"][key]["output_tokens"] = result.output_tokens

    save_results(results, output_path)


@main.command()
@click.option("--host", default="127.0.0.1", help="Host to bind to")
@click.option("--port", default=8000, type=int, help="Port to bind to")
def ui(host: str, port: int) -> None:
    """Open the MART web UI for playlist management."""
    from ui import run_ui
    run_ui(host=host, port=port)


if __name__ == "__main__":
    main()
