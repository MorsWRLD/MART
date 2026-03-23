"""Query cleaning and generation for improved SoundCloud search matching."""

from __future__ import annotations

import re
from vk_import import Track


# ---------------------------------------------------------------------------
# Cyrillic → Latin transliteration table
# ---------------------------------------------------------------------------

_CYR_TO_LAT: dict[str, str] = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}

# Build uppercase mapping
_CYR_TO_LAT_UPPER: dict[str, str] = {}
for _k, _v in _CYR_TO_LAT.items():
    _CYR_TO_LAT_UPPER[_k.upper()] = _v.capitalize() if _v else ""


def transliterate(text: str) -> str:
    """Transliterate Cyrillic characters to Latin equivalents."""
    result: list[str] = []
    for ch in text:
        if ch in _CYR_TO_LAT:
            result.append(_CYR_TO_LAT[ch])
        elif ch in _CYR_TO_LAT_UPPER:
            result.append(_CYR_TO_LAT_UPPER[ch])
        else:
            result.append(ch)
    return "".join(result)


def _has_cyrillic(text: str) -> bool:
    """Check if text contains any Cyrillic characters."""
    return bool(re.search(r"[\u0400-\u04FF]", text))


# ---------------------------------------------------------------------------
# Title cleaning
# ---------------------------------------------------------------------------

# Patterns to strip from titles (compiled once)
_TITLE_PATTERNS: list[re.Pattern] = [
    # Specific bracketed tags
    re.compile(r"\[Рифмы и Панчи\]", re.IGNORECASE),
    re.compile(r"\[AnimeNewMusic\]", re.IGNORECASE),
    re.compile(r"\[Post-Hardcore\.\w+\]", re.IGNORECASE),
    re.compile(r"\[Новый Рэп\]", re.IGNORECASE),
    re.compile(r"\[_?nightcorebot\]", re.IGNORECASE),
    re.compile(r"\[HQ\]", re.IGNORECASE),
    re.compile(r"\[Extended\]", re.IGNORECASE),
    re.compile(r"\[Lyrics\]", re.IGNORECASE),

    # Production credits: (prod. by X), (prod. X), [Prod. by X], [PROD. BY X]
    re.compile(r"\(prod\.?\s*(?:by\s+)?[^)]+\)", re.IGNORECASE),
    re.compile(r"\[prod\.?\s*(?:by\s+)?[^]]+\]", re.IGNORECASE),
    re.compile(r"\[PRODUCED\s+BY\s+[^]]+\]", re.IGNORECASE),

    # VK watermarks and URLs
    re.compile(r"vk\.com[/\\]\S*", re.IGNORECASE),
    re.compile(r"\[vk\.com[^\]]*\]", re.IGNORECASE),
    re.compile(r"www\.\S+\.\S+"),
    re.compile(r"muzaf\.net", re.IGNORECASE),
    re.compile(r"radiorecord\.ru", re.IGNORECASE),

    # BPM tags
    re.compile(r"\d+\s*bpm", re.IGNORECASE),

    # Leading track numbers: "06. ", "3. ", "13. "
    re.compile(r"^\d+\.\s+"),

    # Feat in title — both parenthesized and bare
    re.compile(r"\(feat\.?\s+[^)]+\)", re.IGNORECASE),
    re.compile(r"\(ft\.?\s+[^)]+\)", re.IGNORECASE),
    re.compile(r"\bfeat\.?\s+[^(\[]+$", re.IGNORECASE),
    re.compile(r"\bft\.?\s+[^(\[]+$", re.IGNORECASE),

    # Emoji (broad Unicode ranges for emoji)
    re.compile(
        r"[\U0001F300-\U0001F9FF\U00002702-\U000027B0\U0000FE00-\U0000FE0F"
        r"\U0000200D\U00002600-\U000026FF\U00002B50-\U00002B55"
        r"\U0000203C-\U00003299〄⚜💰💮]+",
    ),
]


def clean_title(title: str) -> str:
    """Strip metadata junk from a track title."""
    result = title
    for pat in _TITLE_PATTERNS:
        result = pat.sub("", result)

    # Remove empty parentheses/brackets left after stripping
    result = re.sub(r"\(\s*\)", "", result)
    result = re.sub(r"\[\s*\]", "", result)
    # Collapse whitespace
    result = re.sub(r"\s+", " ", result).strip()
    # Strip trailing/leading punctuation junk
    result = result.strip("- _")
    return result


# ---------------------------------------------------------------------------
# Artist cleaning
# ---------------------------------------------------------------------------

_ARTIST_PATTERNS: list[re.Pattern] = [
    # Album name embedded after underscore: "Korn _ See You on the Other Side 2005"
    re.compile(r"\s*_\s+.+$"),

    # (ost) prefix
    re.compile(r"^\(ost\)\s*", re.IGNORECASE),

    # (CV_ ...) suffix — anime character voice credits
    re.compile(r"\(CV[_:\s][^)]+\)", re.IGNORECASE),

    # Parenthetical suffixes: (BlowChop remastered), (T.A. Inc.), etc.
    re.compile(r"\([^)]*(?:remaster|remix|edit|version|ver\.)[^)]*\)", re.IGNORECASE),

    # Emoji and special chars
    re.compile(
        r"[\U0001F300-\U0001F9FF\U00002702-\U000027B0♥⚡〄💰💮⚜]+",
    ),
]


def clean_artist(artist: str) -> str:
    """Strip metadata junk from an artist name."""
    result = artist
    for pat in _ARTIST_PATTERNS:
        result = pat.sub("", result)
    result = re.sub(r"\s+", " ", result).strip()
    result = result.strip("- _")
    return result


# ---------------------------------------------------------------------------
# Multi-artist splitting
# ---------------------------------------------------------------------------

# Separators between artists (order matters — longer first)
_ARTIST_SPLIT_RE = re.compile(
    r"\s*(?:"
    r"\bfeat\.\s*"
    r"|\bfeat\s+"
    r"|\bft\.\s*"
    r"|\bft\s+"
    r"|,\s+"
    r"|\s+x\s+"
    r"|\s+х\s+"  # Cyrillic х
    r"|\s+&\s+"
    r")\s*",
    re.IGNORECASE,
)


def split_artists(artist: str) -> list[str]:
    """Split a multi-artist string into individual artist names."""
    parts = _ARTIST_SPLIT_RE.split(artist)
    return [p.strip() for p in parts if p.strip()]


# ---------------------------------------------------------------------------
# Query variant generator
# ---------------------------------------------------------------------------

def generate_queries(track: Track) -> list[str]:
    """Generate an ordered list of search queries to try for a track.

    Returns unique queries, most specific first.
    """
    c_artist = clean_artist(track.artist)
    c_title = clean_title(track.title)
    artists = split_artists(c_artist)

    seen: set[str] = set()
    queries: list[str] = []

    def _add(q: str) -> None:
        normalized = q.lower().strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            queries.append(q.strip())

    # 1. Cleaned full artist + cleaned title
    _add(f"{c_artist} {c_title}")

    # 2. First artist only + cleaned title (helps multi-artist tracks)
    if len(artists) > 1:
        _add(f"{artists[0]} {c_title}")

    # 3. Transliterated artist + cleaned title (helps Cyrillic names)
    if _has_cyrillic(c_artist):
        t_artist = transliterate(c_artist)
        _add(f"{t_artist} {c_title}")
        # Also try first transliterated artist
        if len(artists) > 1:
            _add(f"{transliterate(artists[0])} {c_title}")

    # 4. Title-only search (last resort, broad)
    _add(c_title)

    # 5. Each remaining split artist + cleaned title
    for a in artists[1:]:
        _add(f"{a} {c_title}")
        if _has_cyrillic(a):
            _add(f"{transliterate(a)} {c_title}")

    return queries
