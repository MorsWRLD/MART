"""LLM-based matching of VK tracks to SoundCloud candidates."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

import httpx

from vk_import import Track
from sc_search import Candidate


@dataclass
class MatchResult:
    candidate: Candidate | None
    reason: str
    input_tokens: int
    output_tokens: int


# Provider configuration: (base_url, default_model, auth_header_prefix)
_PROVIDERS = {
    "GEMINI_API_KEY": (
        "https://generativelanguage.googleapis.com/v1beta/openai",
        "gemini-2.0-flash",
        "Bearer",
    ),
    "OPENROUTER_API_KEY": (
        "https://openrouter.ai/api/v1",
        "google/gemini-2.0-flash-001",
        "Bearer",
    ),
    "OPENAI_API_KEY": (
        "https://api.openai.com/v1",
        "gpt-4o-mini",
        "Bearer",
    ),
    "ANTHROPIC_API_KEY": (
        "https://api.anthropic.com/v1",
        "claude-haiku-4-5-20251001",
        None,  # Anthropic uses different auth
    ),
}


def _get_provider() -> tuple[str, str, str, str]:
    """Return (env_key, api_key, base_url, model) for the first available provider."""
    model_override = os.environ.get("MART_MODEL")

    for env_key, (base_url, default_model, _) in _PROVIDERS.items():
        api_key = os.environ.get(env_key)
        if api_key:
            model = model_override or default_model
            return env_key, api_key, base_url, model

    raise RuntimeError(
        "No LLM API key found. Set one of: "
        "GEMINI_API_KEY (free), OPENROUTER_API_KEY, OPENAI_API_KEY, ANTHROPIC_API_KEY"
    )


def _detect_source_type(title: str) -> str | None:
    """Detect if source track is itself a remix/cover/speed-up."""
    lower = title.lower()
    if any(x in lower for x in ["speed up", "sped up", "nightcore"]):
        return "speed-up/nightcore"
    if any(x in lower for x in ["cover", "tribute"]):
        return "cover"
    if "remix" in lower:
        return "remix"
    if any(x in lower for x in ["bass boosted", "bass boost", "bassboosted"]):
        return "bass boosted"
    if "mashup" in lower:
        return "mashup"
    return None


def _build_prompt(track: Track, candidates: list[Candidate]) -> str:
    duration_str = f"{track.duration}s" if track.duration else "unknown"

    candidate_lines: list[str] = []
    for i, c in enumerate(candidates, 1):
        dur = f"{c.duration}s" if c.duration else "unknown"
        plays = f"{c.play_count:,}" if c.play_count else "unknown"
        candidate_lines.append(
            f"{i}. \"{c.title}\" by {c.uploader} | duration: {dur} | plays: {plays} | {c.url}"
        )

    formatted_candidates = "\n".join(candidate_lines)

    source_type = _detect_source_type(track.title)
    special_rule = ""
    if source_type:
        special_rule = f"""
SPECIAL: The source track is itself a {source_type} version. The user saved this
specific version intentionally. DO accept matching {source_type} versions.
Still match artist and title. Prefer the version closest to the source title.
"""

    return f"""You are matching music tracks from a VK Music library to SoundCloud search results.

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
{special_rule}
SOURCE TRACK:
Artist: {track.artist}
Title: {track.title}
Duration: {duration_str}

CANDIDATES:
{formatted_candidates}

Respond with ONLY valid JSON, no markdown:
{{"pick": <1-based candidate number or null>, "reason": "<brief explanation>"}}"""


def _call_openai_compatible(
    api_key: str, base_url: str, model: str, prompt: str, client: httpx.Client
) -> tuple[str, int, int]:
    """Call an OpenAI-compatible chat completions endpoint."""
    resp = client.post(
        f"{base_url}/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
        },
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()

    content = data["choices"][0]["message"]["content"]
    usage = data.get("usage", {})
    input_tokens = usage.get("prompt_tokens", 0)
    output_tokens = usage.get("completion_tokens", 0)

    return content, input_tokens, output_tokens


def _call_anthropic(
    api_key: str, model: str, prompt: str, client: httpx.Client
) -> tuple[str, int, int]:
    """Call the Anthropic Messages API."""
    resp = client.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "max_tokens": 256,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
        },
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()

    content = data["content"][0]["text"]
    usage = data.get("usage", {})
    input_tokens = usage.get("input_tokens", 0)
    output_tokens = usage.get("output_tokens", 0)

    return content, input_tokens, output_tokens


def _parse_llm_response(
    raw: str, candidates: list[Candidate]
) -> tuple[Candidate | None, str]:
    """Parse the LLM JSON response. Returns (picked_candidate_or_None, reason)."""
    # Strip markdown fences if present
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        cleaned = "\n".join(lines).strip()

    data = json.loads(cleaned)
    pick = data.get("pick")
    reason = data.get("reason", "")

    if pick is None or pick == "null":
        return None, reason

    pick = int(pick)
    if 1 <= pick <= len(candidates):
        return candidates[pick - 1], reason

    return None, f"Invalid pick index: {pick}"


def llm_match(
    track: Track, candidates: list[Candidate], client: httpx.Client
) -> MatchResult:
    """Use an LLM to pick the best SoundCloud match for a VK track."""
    env_key, api_key, base_url, model = _get_provider()
    prompt = _build_prompt(track, candidates)

    for attempt in range(2):  # one retry on parse failure
        try:
            if env_key == "ANTHROPIC_API_KEY":
                raw, in_tok, out_tok = _call_anthropic(
                    api_key, model, prompt, client
                )
            else:
                raw, in_tok, out_tok = _call_openai_compatible(
                    api_key, base_url, model, prompt, client
                )

            picked, reason = _parse_llm_response(raw, candidates)
            return MatchResult(
                candidate=picked,
                reason=reason,
                input_tokens=in_tok,
                output_tokens=out_tok,
            )

        except (json.JSONDecodeError, KeyError, ValueError):
            if attempt == 0:
                continue
            return MatchResult(
                candidate=None,
                reason=f"Failed to parse LLM response: {raw[:200]}",
                input_tokens=0,
                output_tokens=0,
            )

        except httpx.HTTPError as e:
            return MatchResult(
                candidate=None,
                reason=f"LLM API error: {e}",
                input_tokens=0,
                output_tokens=0,
            )
