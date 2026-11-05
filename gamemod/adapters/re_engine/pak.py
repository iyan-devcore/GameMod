"""RE Engine KPKA archive (.pak) reader and writer."""
from __future__ import annotations

import io
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

import zstandard

KPKA_MAGIC = b"KPKA"
KPKA_VERSION = 4


def murmur3_32(data: bytes, seed: int = 0xFFFFFFFF) -> int:
    """32-bit Murmur3 hash implementation matching RE Engine."""
    c1 = 0xCC9E2D51
    c2 = 0x1B873593
    h = seed & 0xFFFFFFFF
    length = len(data)
    nblocks = length // 4
    for i in range(nblocks):
        k = int.from_bytes(data[i * 4 : (i + 1) * 4], "little")
        k = (k * c1) & 0xFFFFFFFF
        k = ((k << 15) | (k >> 17)) & 0xFFFFFFFF
        k = (k * c2) & 0xFFFFFFFF
        h ^= k
        h = ((h << 13) | (h >> 19)) & 0xFFFFFFFF
        h = (h * 5 + 0xE6546B64) & 0xFFFFFFFF

    tail = data[nblocks * 4 :]
    k1 = 0
    if len(tail) == 3:
        k1 ^= tail[2] << 16
    if len(tail) >= 2:
        k1 ^= tail[1] << 8
    if len(tail) >= 1:
        k1 ^= tail[0]
        k1 = (k1 * c1) & 0xFFFFFFFF
        k1 = ((k1 << 15) | (k1 >> 17)) & 0xFFFFFFFF
        k1 = (k1 * c2) & 0xFFFFFFFF
        h ^= k1

    h ^= length
    h ^= h >> 16
    h = (h * 0x85EBCA6B) & 0xFFFFFFFF
    h ^= h >> 13
    h = (h * 0xC2B2AE35) & 0xFFFFFFFF
    h ^= h >> 16
    return h


def hash_re_path(path: str) -> tuple[int, int]:
    """Return (lower_hash, upper_hash) for an RE Engine path."""
    clean = path.replace("\\", "/")
    h_low = murmur3_32(clean.lower().encode("utf-16le"))
    h_hi = murmur3_32(clean.upper().encode("utf-16le"))
    return h_low, h_hi


@dataclass(frozen=True)
class PakEntry:
    h_low: int
    h_hi: int
    pak_path: Path
    offset: int
    c_size: int
    u_size: int
    flags: int
    checksum: int


class PakReader:
    """Reads RE Engine PAK archives with patch layering."""

    def __init__(self, pak_paths: Sequence[Path]):
        self._entries: dict[tuple[int, int], PakEntry] = {}
        self._dctx = zstandard.ZstdDecompressor()
        for path in pak_paths:
            path = Path(path)
            if not path.is_file() or path.stat().st_size == 0:
                continue
            self._load_pak(path)

    def _load_pak(self, pak_path: Path) -> None:
        with open(pak_path, "rb") as f:
            hdr = f.read(16)
            if len(hdr) < 16:
                return
            magic, v_maj, v_min, count, _ = struct.unpack("<4sHHII", hdr)
            if magic != KPKA_MAGIC or v_maj != KPKA_VERSION:
                return
            for _ in range(count):
                entry_raw = f.read(48)
                if len(entry_raw) < 48:
                    break
                h_low, h_hi = struct.unpack_from("<II", entry_raw, 0)
                offset, c_size, u_size = struct.unpack_from("<QQQ", entry_raw, 8)
                flags = struct.unpack_from("<Q", entry_raw, 32)[0]
                checksum = struct.unpack_from("<Q", entry_raw, 40)[0]
                # Later paks overwrite earlier paks
                self._entries[(h_low, h_hi)] = PakEntry(
                    h_low=h_low,
                    h_hi=h_hi,
                    pak_path=pak_path,
                    offset=offset,
                    c_size=c_size,
                    u_size=u_size,
                    flags=flags,
                    checksum=checksum,
                )

    def contains(self, target: str | tuple[int, int]) -> bool:
        key = hash_re_path(target) if isinstance(target, str) else target
        return key in self._entries

    def get(self, target: str | tuple[int, int]) -> bytes:
        key = hash_re_path(target) if isinstance(target, str) else target
        if key not in self._entries:
            raise KeyError(f"File {target} not found in archives")
        entry = self._entries[key]
        with open(entry.pak_path, "rb") as f:
            f.seek(entry.offset)
            cdata = f.read(entry.c_size)

        comp = entry.flags & 0x0F
        if comp == 0:
            return cdata
        elif comp == 1:
            return zlib.decompress(cdata, -zlib.MAX_WBITS)
        elif comp == 2:
            return self._dctx.decompress(cdata, max_output_size=entry.u_size)
        raise ValueError(f"Unsupported compression type {comp} for entry {key}")

    @property
    def entries(self) -> Mapping[tuple[int, int], PakEntry]:
        return self._entries


def write_pak(
    output_path: Path,
    files: Mapping[str | tuple[int, int], bytes],
) -> None:
    """Create a new uncompressed KPKA v4 patch archive."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    prepared: list[tuple[int, int, bytes]] = []
    for key, data in files.items():
        if isinstance(key, str):
            h_low, h_hi = hash_re_path(key)
        else:
            h_low, h_hi = key
        prepared.append((h_low, h_hi, data))

    # Sort entries by combined 64-bit hash (upper << 32 | lower)
    prepared.sort(key=lambda item: (item[1] << 32) | item[0])

    count = len(prepared)
    hdr_size = 16
    tbl_size = count * 48
    data_start = hdr_size + tbl_size

    with open(output_path, "wb") as f:
        # Header: KPKA, v4.0, file_count, fingerprint=0
        f.write(struct.pack("<4sHHII", KPKA_MAGIC, KPKA_VERSION, 0, count, 0))
        # Reserve table space
        f.write(b"\x00" * tbl_size)

        entries_meta: list[tuple[int, int, int, int]] = []
        for h_low, h_hi, data in prepared:
            offset = f.tell()
            f.write(data)
            size = len(data)
            entries_meta.append((h_low, h_hi, offset, size))

        # Seek back and write entry table
        f.seek(hdr_size)
        for h_low, h_hi, offset, size in entries_meta:
            # 48 bytes: h_low(4), h_hi(4), offset(8), c_size(8), u_size(8), flags(8)=0, checksum(8)=0
            f.write(struct.pack("<IIQQQQQ", h_low, h_hi, offset, size, size, 0, 0))
