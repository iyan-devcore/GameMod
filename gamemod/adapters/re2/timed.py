"""Handling and reconstruction for Resident Evil 2 timed cutscene entries."""
from __future__ import annotations

import re
from typing import Sequence

TIME_TAG_RE = re.compile(r"<TIME [^>]+>")
LEADING_TIMES_RE = re.compile(r"^(?:\s*<TIME [^>]+>\s*)+")


def is_timed_entry(text: str) -> bool:
    """Return True if text is a timed scene entry containing <TIME> tags."""
    return "<TIME" in text


def split_timed_entry(
    text: str,
) -> tuple[str, list[str], list[tuple[str, str, str]]]:
    """Split a timed entry into leading time tags and page pieces.

    Returns:
        (leading_text, list_of_time_tags, list_of_(prefix, core_text, suffix))
    """
    match = LEADING_TIMES_RE.match(text)
    if not match:
        return "", [], []

    leading = match.group()
    times = TIME_TAG_RE.findall(leading)
    body = text[len(leading) :]
    raw_pieces = body.split("<PAGE>")

    parsed_pieces: list[tuple[str, str, str]] = []
    for raw in raw_pieces:
        core = raw.strip()
        if not core:
            prefix = raw
            suffix = ""
        else:
            idx = raw.find(core)
            prefix = raw[:idx]
            suffix = raw[idx + len(core) :]
        parsed_pieces.append((prefix, core, suffix))

    return leading, times, parsed_pieces


def build_timed_entry(
    leading: str,
    pieces: Sequence[tuple[str, str, str]],
) -> str:
    """Reconstruct a timed entry from leading time tags and translated pieces."""
    body = "<PAGE>".join(prefix + core + suffix for prefix, core, suffix in pieces)
    return leading + body
