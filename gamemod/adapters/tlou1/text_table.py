"""Parse and rebuild the game's text tables (text2/<lang>.subtitles and friends).

Layout (little-endian):
    u32 count
    `count` records of {u64 key, u32 offset, u32 reserved}, sorted by key
    string area: NUL-terminated UTF-8. Offsets are relative to the start of this area.

Strings are stored in scene order, which is not the record order.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Mapping

_RECORD = struct.Struct("<QII")


class TextTableError(Exception):
    pass


@dataclass
class Record:
    key: int
    reserved: int
    text: bytes
    gap: bytes = b""  # bytes between this string's NUL and the next string, in scene order


class TextTable:
    def __init__(self, records: list[Record], scene_order: list[int], prefix: bytes):
        self.records = records  # table order
        self.scene_order = scene_order  # indexes into records, in string-area order
        self.prefix = prefix  # bytes before the first string

    @classmethod
    def parse(cls, data: bytes) -> "TextTable":
        count = struct.unpack("<I", data[:4])[0]
        base = 4 + count * _RECORD.size
        if base > len(data):
            raise TextTableError("Truncated text table")
        raw = [_RECORD.unpack_from(data, 4 + i * _RECORD.size) for i in range(count)]
        order = sorted(range(count), key=lambda i: raw[i][1])

        records: list[Record] = [Record(key, reserved, b"") for key, _, reserved in raw]
        for position, index in enumerate(order):
            start = base + raw[index][1]
            end = data.find(b"\0", start)
            if end < 0:
                raise TextTableError("Unterminated string in text table")
            next_start = base + raw[order[position + 1]][1] if position + 1 < count else len(data)
            if next_start <= end:
                raise TextTableError("Overlapping strings in text table")
            records[index].text = data[start:end]
            records[index].gap = data[end + 1 : next_start]

        prefix = data[base : base + raw[order[0]][1]] if count else data[base:]
        return cls(records, order, prefix)

    def build(self, replacements: Mapping[int, bytes] | None = None) -> bytes:
        """Serialize the table. `replacements` maps a key to new UTF-8 text."""
        replacements = replacements or {}
        area = bytearray(self.prefix)
        offsets = [0] * len(self.records)
        for index in self.scene_order:
            record = self.records[index]
            text = replacements.get(record.key, record.text)
            if b"\0" in text:
                raise TextTableError("Text may not contain NUL bytes")
            offsets[index] = len(area)
            area += text
            area += b"\0"
            area += record.gap
        out = bytearray(struct.pack("<I", len(self.records)))
        for record, offset in zip(self.records, offsets):
            out += _RECORD.pack(record.key, offset, record.reserved)
        out += area
        return bytes(out)

    def as_dict(self) -> dict[int, str]:
        return {r.key: r.text.decode("utf-8") for r in self.records}

    def in_scene_order(self) -> list[Record]:
        return [self.records[i] for i in self.scene_order]
