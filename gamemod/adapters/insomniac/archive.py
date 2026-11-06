"""The asset index (`asset_archive/toc`) and the archives it points into.

The toc is a zlib-compressed DAT1 container. For every asset it stores a 64-bit id (a hash of
the asset's path), the archive that holds it, the position there, and the size. The same id
appears once per "span": span 0 holds assets every language uses, and each language has its
own spans, so a text table has one copy per language.

Big archives are DSAR files made of LZ4 blocks, and the toc's positions count in unpacked
bytes. An archive without the DSAR header is read as plain bytes, which is what this mod
writes.
"""
from __future__ import annotations

import bisect
import struct
import zlib
from array import array
from dataclasses import dataclass
from pathlib import Path

from gamemod.adapters.insomniac.lz4 import decompress_block

TOC_MAGIC = 0x77AF12AF
DAT1_MAGIC = 0x44415431
DSAR_MAGIC = 0x52415344

SECTION_ARCHIVES = 0x398ABFF0
SECTION_IDS = 0x506D7B8A
SECTION_SIZES = 0x65BCF461
SECTION_OFFSETS = 0xDCD720B5
SECTION_SPANS = 0xEDE8ADA9

_ARCHIVE_ENTRY = struct.Struct("<II64s")
_SPAN_COUNT = 256
_DSAR_LZ4 = 3

_CRC_TABLE = []
for _byte in range(256):
    _value = _byte
    for _ in range(8):
        _value = (_value >> 1) ^ 0xC96C5795D7870F42 if _value & 1 else _value >> 1
    _CRC_TABLE.append(_value)


def asset_id(path: str) -> int:
    """The id the toc uses for an asset path, such as 'ui/export/fonts/Font_LatinAS3.gfx'."""
    value = 0xC96C5795D7870F42
    for byte in path.lower().replace("\\", "/").encode("utf-8"):
        value = _CRC_TABLE[(value ^ byte) & 0xFF] ^ (value >> 8)
    return (value >> 2) | 0x8000000000000000


class Dat1:
    """A DAT1 container: a type hash and tagged sections. Sections keep their order on disk."""

    def __init__(self, data: bytes):
        magic, self.type_hash, total, count, self.unknown = struct.unpack_from("<IIIHH", data)
        if magic != DAT1_MAGIC:
            raise ValueError("not a DAT1 container")
        entries = [struct.unpack_from("<III", data, 16 + 12 * i) for i in range(count)]
        self.tags = [tag for tag, _offset, _size in entries]  # order in the section table
        layout = sorted(entries, key=lambda entry: entry[1])
        self.layout = [tag for tag, _offset, _size in layout]  # order of the data itself
        table_end = 16 + 12 * count
        # Data between the table and the first section, such as a name string. Kept as is.
        self.preamble = data[table_end : layout[0][1]] if layout else data[table_end:total]
        self.sections = {tag: data[offset : offset + size] for tag, offset, size in entries}

    def to_bytes(self) -> bytes:
        count = len(self.tags)
        out = bytearray(16 + 12 * count)
        out += self.preamble
        placed: dict[int, tuple[int, int]] = {}
        for tag in self.layout:
            out += b"\0" * (-len(out) % 16)
            placed[tag] = (len(out), len(self.sections[tag]))
            out += self.sections[tag]
        struct.pack_into("<IIIHH", out, 0, DAT1_MAGIC, self.type_hash, len(out), count, self.unknown)
        for i, tag in enumerate(self.tags):
            struct.pack_into("<III", out, 16 + 12 * i, tag, *placed[tag])
        return bytes(out)


@dataclass
class ArchiveEntry:
    install_bucket: int
    chunk: int
    name: str


class Toc:
    def __init__(self, raw: bytes):
        magic, size = struct.unpack_from("<II", raw)
        if magic != TOC_MAGIC:
            raise ValueError("not a Spider-Man toc file")
        unpacked = zlib.decompressobj().decompress(raw[8:])
        if len(unpacked) != size:
            raise ValueError("toc is truncated")
        self.dat1 = Dat1(unpacked)
        sections = self.dat1.sections

        archives = sections[SECTION_ARCHIVES]
        self.archives = [
            ArchiveEntry(bucket, chunk, name.split(b"\0", 1)[0].decode("ascii"))
            for bucket, chunk, name in _ARCHIVE_ENTRY.iter_unpack(archives)
        ]
        self.ids = array("Q", sections[SECTION_IDS])
        self.sizes = array("I", sections[SECTION_SIZES])  # three numbers per asset; the middle is the size
        self.offsets = array("I", sections[SECTION_OFFSETS])  # archive index, position
        spans = array("I", sections[SECTION_SPANS])
        self.spans = [(spans[2 * i], spans[2 * i + 1]) for i in range(_SPAN_COUNT)]
        if not (len(self.ids) * 3 == len(self.sizes) and len(self.ids) * 2 == len(self.offsets)):
            raise ValueError("toc tables do not match")

    @classmethod
    def read(cls, path: Path) -> "Toc":
        return cls(Path(path).read_bytes())

    def find(self, path_or_id: str | int, span: int = 0) -> int | None:
        """The asset's position in the tables for one span, or None."""
        wanted = asset_id(path_or_id) if isinstance(path_or_id, str) else path_or_id
        start, count = self.spans[span]
        # Ids are sorted within each span.
        index = bisect.bisect_left(self.ids, wanted, start, start + count)
        if index < start + count and self.ids[index] == wanted:
            return index
        return None

    def location(self, index: int) -> tuple[int, int, int]:
        """(archive index, position, size) of one asset."""
        return self.offsets[2 * index], self.offsets[2 * index + 1], self.sizes[3 * index + 1]

    def archive_index(self, name: str) -> int | None:
        return next((i for i, entry in enumerate(self.archives) if entry.name == name), None)

    def add_archive(self, name: str) -> int:
        """Add an archive that every language loads. Returns its index."""
        existing = self.archive_index(name)
        if existing is not None:
            return existing
        if len(name.encode("ascii")) >= 64:
            raise ValueError("archive name too long")
        index = len(self.archives)
        # The game's own shared archives use bucket 0 and chunk 10000 + their index.
        self.archives.append(ArchiveEntry(0, 10000 + index, name))
        return index

    def point(self, index: int, archive: int, position: int, size: int) -> None:
        self.offsets[2 * index] = archive
        self.offsets[2 * index + 1] = position
        self.sizes[3 * index + 1] = size

    def to_bytes(self) -> bytes:
        sections = self.dat1.sections
        sections[SECTION_ARCHIVES] = b"".join(
            _ARCHIVE_ENTRY.pack(entry.install_bucket, entry.chunk, entry.name.encode("ascii"))
            for entry in self.archives
        )
        sections[SECTION_SIZES] = self.sizes.tobytes()
        sections[SECTION_OFFSETS] = self.offsets.tobytes()
        unpacked = self.dat1.to_bytes()
        return struct.pack("<II", TOC_MAGIC, len(unpacked)) + zlib.compress(unpacked, 9)


class _Dsar:
    def __init__(self, path: Path):
        self._file = open(path, "rb")
        magic, _version, count, _header_size, _total = struct.unpack("<IIIIQ", self._file.read(24))
        if magic != DSAR_MAGIC:
            raise ValueError(f"{path.name} is not a DSAR archive")
        self._file.seek(32)
        table = self._file.read(32 * count)
        self._blocks = [struct.unpack_from("<QQIIB", table, 32 * i) for i in range(count)]
        self._starts = [block[0] for block in self._blocks]

    def read(self, position: int, size: int) -> bytes:
        out = bytearray()
        i = bisect.bisect_right(self._starts, position) - 1
        while len(out) < size:
            unpacked_at, packed_at, unpacked_size, packed_size, method = self._blocks[i]
            self._file.seek(packed_at)
            packed = self._file.read(packed_size)
            if method == _DSAR_LZ4:
                block = decompress_block(packed, unpacked_size)
            elif packed_size == unpacked_size:
                block = packed
            else:
                raise ValueError(f"unknown DSAR compression {method}")
            skip = max(position - unpacked_at, 0)
            out += block[skip : skip + size - len(out)]
            i += 1
        return bytes(out)

    def close(self) -> None:
        self._file.close()


class AssetReader:
    """Reads assets from a game's asset_archive folder using a given toc."""

    def __init__(self, folder: Path, toc: Toc):
        self.folder = Path(folder)
        self.toc = toc
        self._open: dict[int, _Dsar | None] = {}

    def _archive(self, index: int) -> _Dsar | None:
        if index not in self._open:
            path = self.folder / self.toc.archives[index].name
            with open(path, "rb") as file:
                is_dsar = file.read(4) == struct.pack("<I", DSAR_MAGIC)
            self._open[index] = _Dsar(path) if is_dsar else None
        return self._open[index]

    def read_index(self, index: int) -> bytes:
        archive, position, size = self.toc.location(index)
        dsar = self._archive(archive)
        if dsar is not None:
            return dsar.read(position, size)
        with open(self.folder / self.toc.archives[archive].name, "rb") as file:
            file.seek(position)
            data = file.read(size)
        if len(data) != size:
            raise ValueError("asset runs past the end of its archive")
        return data

    def read(self, path: str, span: int = 0) -> bytes:
        index = self.toc.find(path, span)
        if index is None:
            raise KeyError(f"{path} is not in span {span}")
        return self.read_index(index)

    def close(self) -> None:
        for dsar in self._open.values():
            if dsar is not None:
                dsar.close()
        self._open.clear()

    def __enter__(self) -> "AssetReader":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()
