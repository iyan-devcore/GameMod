"""Split Hindi text into syllable clusters and encode them as Private Use characters."""
from __future__ import annotations

import re
import unicodedata
from typing import Iterable

# U+E900-F6BF avoids every Private Use code the game's own fonts define:
# Korean E0BC-E4F6, Traditional Chinese E78D-E864, svg F6C3, Thai F700/F70F/F8FF.
PUA_START = 0xE900
PUA_END = 0xF6BF

_CONSONANT = "[क-हक़-य़ॸ-ॿ]़?"
_VOWEL = "[ऄ-औॠॡॲ-ॷ]़?"
_HALANT = "्[‌‍]?"
_SIGN = "[ऺऻा-ौॎॏॕ-ॗॢॣ]"
_MODIFIER = "[ऀ-ः]"

_SYLLABLE = f"(?:{_CONSONANT}{_HALANT})*{_CONSONANT}(?:{_HALANT})?{_SIGN}*{_MODIFIER}*"
_VOWEL_SYLLABLE = f"{_VOWEL}{_SIGN}*{_MODIFIER}*"
_CLUSTER_RE = re.compile(f"{_SYLLABLE}|{_VOWEL_SYLLABLE}|[ऀ-ॿ]")

_ORPHAN = re.compile(f"^(?:{_SIGN}|{_MODIFIER}|्|़)$")
_JOINERS = re.compile("[‌‍]")


class TooManyClusters(Exception):
    pass


def normalize(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def iter_clusters(text: str) -> list[str]:
    return _CLUSTER_RE.findall(normalize(text))


def unique_clusters(texts: Iterable[str]) -> list[str]:
    found: set[str] = set()
    for text in texts:
        found.update(iter_clusters(text))
    return sorted(found)


def find_broken_marks(text: str) -> list[str]:
    """Vowel signs or marks with no letter to attach to. These would show a dotted circle."""
    return [c for c in iter_clusters(text) if _ORPHAN.match(c)]


def assign_codepoints(clusters: Iterable[str], start: int = PUA_START) -> dict[str, int]:
    ordered = sorted(set(clusters))
    if start + len(ordered) - 1 > PUA_END:
        raise TooManyClusters(
            f"{len(ordered)} clusters do not fit in {PUA_END - start + 1} Private Use slots"
        )
    return {cluster: start + i for i, cluster in enumerate(ordered)}


def encode_text(text: str, mapping: dict[str, int]) -> str:
    """Replace every Devanagari cluster with its Private Use character."""
    encoded = _CLUSTER_RE.sub(lambda m: chr(mapping[m.group()]), normalize(text))
    return _JOINERS.sub("", encoded)
