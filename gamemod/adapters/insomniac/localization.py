"""The text table `localization/localization_all.localization`, one copy per language.

The asset is a 36-byte header (type hash, size, zeros) and a DAT1 container. Every entry has a
key such as SPID_M_A1_GALLERY_009, a text in UTF-8, and four flag bytes. Spoken lines carry
timing tags: <ts=&quot;0.03;1.55&quot;>.
"""
from __future__ import annotations

import struct
from array import array
from typing import Mapping

from gamemod.adapters.insomniac.archive import Dat1

LOCALIZATION_PATH = "localization/localization_all.localization"

_HEADER_SIZE = 36
_COUNT = 0xD540A903
_KEY_HASHES = 0x06A58050
_KEY_OFFSETS = 0xA4EA55B2
_KEY_STRINGS = 0x4D73CEBD
_TEXT_OFFSETS = 0xF80DEEB4
_TEXT_STRINGS = 0x70A382B8
_FLAGS = 0xB0653243


def _string_at(blob: bytes, offset: int) -> str:
    return blob[offset : blob.index(b"\0", offset)].decode("utf-8")


class Localization:
    def __init__(self, raw: bytes):
        self._header = raw[:_HEADER_SIZE]
        self._body = raw[_HEADER_SIZE:]
        sections = Dat1(self._body).sections
        count = struct.unpack_from("<I", sections[_COUNT])[0]
        key_offsets = array("I", sections[_KEY_OFFSETS])
        self._text_offsets = array("I", sections[_TEXT_OFFSETS])
        if len(key_offsets) != count or len(self._text_offsets) != count:
            raise ValueError("localization tables do not match")
        keys_blob = sections[_KEY_STRINGS]
        self._texts_blob = sections[_TEXT_STRINGS]
        self.keys = [_string_at(keys_blob, offset) for offset in key_offsets]
        self.texts = [_string_at(self._texts_blob, offset) for offset in self._text_offsets]

    def as_dict(self) -> dict[str, str]:
        return dict(zip(self.keys, self.texts))

    def rebuild(self, texts: Mapping[str, str]) -> bytes:
        """A copy of this table with the given keys' texts replaced.

        The game's own strings stay exactly where they were and new ones are added after
        them, so every entry that is not replaced reads the same bytes as before.
        """
        unknown = set(texts) - set(self.keys)
        if unknown:
            raise KeyError(f"keys not in the table: {sorted(unknown)[:5]}")
        blob = bytearray(self._texts_blob)
        offsets = array("I", self._text_offsets)
        placed: dict[str, int] = {}
        for index, key in enumerate(self.keys):
            if key not in texts or texts[key] == self.texts[index]:
                continue
            text = texts[key]
            if "\0" in text:
                raise ValueError(f"{key} contains a null character")
            if text not in placed:
                placed[text] = len(blob)
                blob += text.encode("utf-8") + b"\0"
            offsets[index] = placed[text]
        dat1 = Dat1(self._body)
        dat1.sections[_TEXT_STRINGS] = bytes(blob)
        dat1.sections[_TEXT_OFFSETS] = offsets.tobytes()
        body = dat1.to_bytes()
        header = bytearray(self._header)
        struct.pack_into("<I", header, 4, len(body))
        return bytes(header) + body
