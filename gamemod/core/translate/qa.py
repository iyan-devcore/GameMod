"""Automatic checks on each translated line."""
from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field

from gamemod.core.devanagari import find_broken_marks, iter_clusters

_TAG = re.compile(r"\|[^|\s]+\||</?[a-zA-Z][^>]*>|\[[A-Z0-9_]+\]")
_DEVANAGARI = re.compile("[ऀ-ॿ]")
_LATIN_WORD = re.compile(r"[A-Za-z]{2,}")
_DIGITS = re.compile(r"\d+")
_INVALID_CHARACTERS = re.compile("[-\u0000-\u0008\u000B-\u001F�]")
_DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")


def _other_script_characters(text: str) -> list[str]:
    """Letters from scripts the Hindi font does not have, such as Telugu or Urdu."""
    found = []
    for character in text:
        if unicodedata.category(character)[0] not in "LM":
            continue
        code = ord(character)
        if not (0x0900 <= code <= 0x097F or code < 0x250):
            found.append(character)
    return found


@dataclass(frozen=True)
class Check:
    blocking: list[str] = field(default_factory=list)  # the line cannot be used
    warnings: list[str] = field(default_factory=list)  # usable, but a person should look

    @property
    def clean(self) -> bool:
        return not self.blocking and not self.warnings

    def describe(self) -> str:
        return "; ".join(self.blocking + self.warnings)


def check_translation(source: str, target: str | None) -> Check:
    if not target or not target.strip():
        return Check(blocking=["empty translation"])

    blocking: list[str] = []
    warnings: list[str] = []

    if Counter(_TAG.findall(source)) != Counter(_TAG.findall(target)):
        blocking.append(f"must keep these tags exactly: {_TAG.findall(source)}")
    if not _DEVANAGARI.search(target):
        blocking.append("no Devanagari text")
    if _INVALID_CHARACTERS.search(target):
        blocking.append("contains invalid characters")
    others = _other_script_characters(target)
    if others:
        blocking.append(f"must use only Hindi letters, found: {others[:5]}")
    if "\n" in target and "\n" not in source:
        blocking.append("adds a line break the English line does not have")
    broken = find_broken_marks(target)
    if broken:
        blocking.append(f"vowel signs with no letter: {broken[:3]}")

    words = _TAG.sub(" ", target)
    latin = _LATIN_WORD.findall(words)
    if latin:
        warnings.append(f"English words left in: {latin[:3]}")
    source_digits = Counter(_DIGITS.findall(_TAG.sub(" ", source)))
    target_digits = Counter(_DIGITS.findall(words.translate(_DEVANAGARI_DIGITS)))
    if source_digits != target_digits:
        warnings.append("numbers differ from the English line")
    if len(iter_clusters(words)) > len(source) * 2 + 25:
        warnings.append("much longer than the English line")

    return Check(blocking, warnings)
