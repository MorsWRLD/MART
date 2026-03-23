"""Bulk-like SoundCloud tracks from a list of URLs."""

from __future__ import annotations

import sys
import time

import click
import httpx

from sc_search import get_client_id, _rate_limit


def resolve_track_id(url: str, client_id: str, client: httpx.Client) -> int | None:
    """Resolve a SoundCloud permalink URL to a track ID."""
    _rate_limit()
    try:
        resp = client.get(
            "https://api-v2.soundcloud.com/resolve",
            params={"url": url, "client_id": client_id},
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()
        return data.get("id")
    except httpx.HTTPError:
        return None


def like_track(track_id: int, oauth_token: str, client_id: str, client: httpx.Client) -> bool:
    """Like a track on SoundCloud. Returns True on success."""
    _rate_limit()
    try:
        resp = client.post(
            f"https://api-v2.soundcloud.com/users/726635713/track_likes/{track_id}",
            params={"client_id": client_id},
            headers={"Authorization": f"OAuth {oauth_token}"},
        )
        # 200/201 = liked, 422 = already liked — all good
        return resp.status_code in (200, 201, 422)
    except httpx.HTTPError:
        return False


@click.command()
@click.option("--urls", default="soundcloud_urls.txt", type=click.Path(exists=True), help="File with SoundCloud URLs, one per line")
@click.option("--token", required=True, help="SoundCloud OAuth token")
def main(urls: str, token: str) -> None:
    """Bulk-like SoundCloud tracks from a URL list."""
    with open(urls, "r", encoding="utf-8") as f:
        url_list = [line.strip() for line in f if line.strip()]

    click.echo(f"Loaded {len(url_list)} URLs")

    liked = 0
    skipped = 0
    failed = 0

    with httpx.Client(timeout=httpx.Timeout(30, connect=10), follow_redirects=True) as client:
        client_id = get_client_id(client)
        click.echo(f"Got client_id, starting...\n")

        for i, url in enumerate(url_list, 1):
            # Resolve URL to track ID
            track_id = resolve_track_id(url, client_id, client)
            if not track_id:
                click.echo(f"[{i}/{len(url_list)}] SKIP (dead link): {url}")
                skipped += 1
                continue

            # Like it
            ok = like_track(track_id, token, client_id, client)
            if ok:
                liked += 1
                click.echo(f"[{i}/{len(url_list)}] Liked: {url}")
            else:
                failed += 1
                click.echo(f"[{i}/{len(url_list)}] FAILED: {url}")

    click.echo(f"\n--- Done ---")
    click.echo(f"Liked:   {liked}")
    click.echo(f"Skipped: {skipped}")
    click.echo(f"Failed:  {failed}")


if __name__ == "__main__":
    main()
