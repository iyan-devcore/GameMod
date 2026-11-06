"""Spoken lines in the text table, and turning them into text for translators and back.

A spoken line looks like this, with a timing tag before each part the subtitle shows:

    <ts=&quot;0;0.7&quot;>One down ... <ts=&quot;1.1;2.85&quot;>lots and lots to go.

Some begin with a tag that changes the speaker name: <name=&quot;MNEG&quot;>. Translators see
the parts joined by [PAUSE], with &quot; as a plain quote:

    One down ... [PAUSE] lots and lots to go.

The Hindi is split at the same markers and each part goes back after its own timing tag, so
the subtitle changes when the English did.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

PAUSE = "[PAUSE]"

_TIMING = re.compile(r"<ts=&quot;[\d.]+;[\d.]+&quot;>")
_NAME_TAG = re.compile(r"^<name=&quot;(\w+)&quot;>")
_LONE_AMPERSAND = re.compile(r"&(?!\w+;)")
_PAUSE_SPLIT = re.compile(r"\s*\[PAUSE\]\s*")


@dataclass(frozen=True)
class Spoken:
    lead: str  # tags before the first timing tag
    timings: list[str]  # one per part; empty for a line without timings
    parts: list[str]  # the words of each part, as stored in the game

    @property
    def speaker_override(self) -> str | None:
        match = _NAME_TAG.match(self.lead)
        return match.group(1) if match else None


def parse(text: str) -> Spoken:
    if "<ts=" not in text:
        lead = ""
        match = _NAME_TAG.match(text)
        if match:
            lead, text = match.group(), text[match.end() :]
        return Spoken(lead, [], [text])
    first = _TIMING.search(text)
    lead = text[: first.start()] if first else ""
    timings = _TIMING.findall(text)
    parts = _TIMING.split(text[len(lead) :])[1:]
    return Spoken(lead, timings, parts)


def _unescape(text: str) -> str:
    return text.replace("&quot;", '"').replace("&amp;", "&")


def _escape(text: str) -> str:
    return _LONE_AMPERSAND.sub("&amp;", text).replace('"', "&quot;")


def for_translator(spoken: Spoken) -> str:
    """The words to translate, with [PAUSE] where the subtitle changes. Empty parts are left out."""
    words = [_unescape(part).strip() for part in spoken.parts]
    return f" {PAUSE} ".join(part for part in words if part)


def rebuild(spoken: Spoken, translated: str) -> str | None:
    """The game entry with the translation in place of the English, or None if it does not fit."""
    pieces = [_escape(piece.strip()) for piece in _PAUSE_SPLIT.split(translated.strip())]
    filled = [i for i, part in enumerate(spoken.parts) if part.strip()]
    if len(pieces) != len(filled):
        return None
    parts = [""] * len(spoken.parts)
    for index, piece in zip(filled, pieces):
        parts[index] = piece
    if not spoken.timings:
        return spoken.lead + parts[0]
    return spoken.lead + "".join(timing + part for timing, part in zip(spoken.timings, parts))
