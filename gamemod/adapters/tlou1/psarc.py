"""Read, create and patch the PSARC archives used by The Last of Us Part I (PC).

Layout (big-endian):
    32-byte header
    table of contents: `count` entries of 30 bytes each
        16-byte MD5 of the file name (all zeros for entry 0, the manifest)
        u32 first block index, u40 uncompressed size, u40 file offset
    block size table: u16 per block (0 = full raw block, == raw size = raw, else compressed)
    file data

The in-place patch never rewrites the multi-GB archive. It moves the manifest to the
end of the file, which frees room to grow the block table, then appends the new files
as raw blocks and rewrites only the table of contents. Undoing it restores a small
backup and truncates the file.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import struct
import zlib
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Mapping

_HEADER = struct.Struct(">4sHH4sIIIII")
_ENTRY_SIZE = 30
_BLOCK_SIZE = 65536
_BACKUP_MAGIC = b"GMBK0001"
_ZERO_HASH = bytes(16)


class PsarcError(Exception):
    pass


def name_hash(name: str) -> bytes:
    return hashlib.md5(name.encode("utf-8")).digest()


@dataclass(frozen=True)
class Entry:
    name_hash: bytes
    start_block: int
    raw_size: int
    offset: int

    def pack(self) -> bytes:
        return (
            self.name_hash
            + struct.pack(">I", self.start_block)
            + self.raw_size.to_bytes(5, "big")
            + self.offset.to_bytes(5, "big")
        )

    @classmethod
    def unpack(cls, raw: bytes) -> "Entry":
        return cls(
            raw[:16],
            struct.unpack(">I", raw[16:20])[0],
            int.from_bytes(raw[20:25], "big"),
            int.from_bytes(raw[25:30], "big"),
        )


@dataclass
class Toc:
    magic: bytes
    major: int
    minor: int
    compression: bytes
    toc_length: int
    block_size: int
    flags: int
    entries: list[Entry]
    block_sizes: list[int]

    @classmethod
    def parse(cls, data: bytes) -> "Toc":
        magic, major, minor, comp, toc_len, entry_size, count, block_size, flags = _HEADER.unpack(
            data[:32]
        )
        if magic != b"PSAR":
            raise PsarcError("Not a PSARC archive")
        if entry_size != _ENTRY_SIZE:
            raise PsarcError(f"Unexpected entry size {entry_size}")
        if block_size != _BLOCK_SIZE:
            raise PsarcError(f"Unexpected block size {block_size}")
        if len(data) < toc_len:
            raise PsarcError("Truncated table of contents")
        end = 32 + count * entry_size
        entries = [Entry.unpack(data[32 + i * entry_size : 32 + (i + 1) * entry_size]) for i in range(count)]
        table = data[end:toc_len]
        sizes = list(struct.unpack(f">{len(table) // 2}H", table[: len(table) // 2 * 2]))
        return cls(magic, major, minor, comp, toc_len, block_size, flags, entries, sizes)

    def pack(self) -> bytes:
        body = b"".join(e.pack() for e in self.entries)
        table = struct.pack(f">{len(self.block_sizes)}H", *self.block_sizes)
        toc_length = 32 + len(body) + len(table)
        header = _HEADER.pack(
            self.magic,
            self.major,
            self.minor,
            self.compression,
            toc_length,
            _ENTRY_SIZE,
            len(self.entries),
            self.block_size,
            self.flags,
        )
        return header + body + table

    def packed_size(self, entry: Entry) -> int:
        """Bytes this entry occupies on disk."""
        total = 0
        for i in range(_block_count(entry.raw_size, self.block_size)):
            size = self.block_sizes[entry.start_block + i]
            total += size if size else self.block_size
        return total


def _block_count(raw_size: int, block_size: int = _BLOCK_SIZE) -> int:
    return (raw_size + block_size - 1) // block_size


class _Oodle:
    def __init__(self, dll_path: Path):
        self._dll = ctypes.WinDLL(str(dll_path))
        fn = self._dll.OodleLZ_Decompress
        fn.restype = ctypes.c_int64
        fn.argtypes = [
            ctypes.c_char_p, ctypes.c_int64, ctypes.c_char_p, ctypes.c_int64,
            ctypes.c_int, ctypes.c_int, ctypes.c_int,
            ctypes.c_void_p, ctypes.c_int64, ctypes.c_void_p, ctypes.c_void_p,
            ctypes.c_void_p, ctypes.c_int64, ctypes.c_int,
        ]
        self._fn = fn

    def decompress(self, data: bytes, raw_size: int) -> bytes:
        out = ctypes.create_string_buffer(raw_size)
        got = self._fn(data, len(data), out, raw_size, 1, 0, 0, None, 0, None, None, None, 0, 3)
        if got != raw_size:
            raise PsarcError(f"Oodle decompression failed ({got} of {raw_size} bytes)")
        return out.raw


class PsarcReader:
    def __init__(self, path: str | Path, oodle_dll: str | Path | None = None):
        self.path = Path(path)
        self._file = open(self.path, "rb")
        head = self._file.read(32)
        if len(head) < 32:
            raise PsarcError("File too small to be a PSARC archive")
        toc_length = _HEADER.unpack(head)[4]
        self.toc = Toc.parse(head + self._file.read(toc_length - 32))
        self._by_hash = {e.name_hash: e for e in self.toc.entries}
        self._oodle = _Oodle(Path(oodle_dll)) if oodle_dll else None

    def __enter__(self) -> "PsarcReader":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def close(self) -> None:
        self._file.close()

    def has(self, name: str) -> bool:
        return name_hash(name) in self._by_hash

    def entry(self, name: str) -> Entry:
        try:
            return self._by_hash[name_hash(name)]
        except KeyError:
            raise PsarcError(f"{name} is not in {self.path.name}") from None

    def read(self, name: str) -> bytes:
        return self.read_entry(self.entry(name))

    def names(self) -> list[str]:
        manifest = self.read_entry(self.toc.entries[0])
        return [n.decode("utf-8") for n in manifest.split(b"\0") if n]

    def read_entry(self, entry: Entry) -> bytes:
        toc = self.toc
        self._file.seek(entry.offset)
        parts: list[bytes] = []
        remaining = entry.raw_size
        block = entry.start_block
        while remaining > 0:
            size = toc.block_sizes[block]
            want = min(toc.block_size, remaining)
            if size == 0:
                data = self._file.read(toc.block_size)[:want]
            else:
                data = self._file.read(size)
                if size != want:
                    data = self._decompress(data, want)
            if len(data) != want:
                raise PsarcError("Unexpected end of archive data")
            parts.append(data)
            remaining -= want
            block += 1
        return b"".join(parts)

    def _decompress(self, data: bytes, want: int) -> bytes:
        comp = self.toc.compression
        if comp == b"zlib":
            return zlib.decompress(data)
        if comp == b"oodl":
            if self._oodle is None:
                raise PsarcError("This archive needs the game's Oodle DLL to read")
            return self._oodle.decompress(data, want)
        raise PsarcError(f"Unknown compression {comp!r}")


def _raw_block_sizes(raw_size: int) -> list[int]:
    sizes = [0] * (raw_size // _BLOCK_SIZE)
    if raw_size % _BLOCK_SIZE:
        sizes.append(raw_size % _BLOCK_SIZE)
    return sizes


def write_archive(path: str | Path, files: Mapping[str, bytes]) -> None:
    """Create a new archive holding `files` as raw blocks."""
    names = sorted(files)
    manifest = b"\0".join(n.encode("utf-8") for n in names)
    ordered = [(_ZERO_HASH, manifest)] + sorted(
        ((name_hash(n), files[n]) for n in names), key=lambda item: item[0]
    )
    block_sizes: list[int] = []
    layout: list[tuple[bytes, int, int, int]] = []
    position = 0
    for digest, data in ordered:
        layout.append((digest, len(block_sizes), len(data), position))
        block_sizes.extend(_raw_block_sizes(len(data)))
        position += len(data)
    toc_length = 32 + len(ordered) * _ENTRY_SIZE + len(block_sizes) * 2
    toc = Toc(
        b"PSAR", 1, 4, b"oodl", toc_length, _BLOCK_SIZE, 0xC,
        [Entry(d, b, s, o + toc_length) for d, b, s, o in layout],
        block_sizes,
    )
    with open(path, "wb") as out:
        out.write(toc.pack())
        for _, data in ordered:
            out.write(data)
        out.flush()
        os.fsync(out.fileno())


def toc_fingerprint(path: str | Path) -> dict:
    """Cheap identity of an archive: its size plus a hash of its table of contents."""
    path = Path(path)
    with open(path, "rb") as f:
        head = f.read(32)
        toc_length = _HEADER.unpack(head)[4]
        digest = hashlib.sha256(head + f.read(toc_length - 32)).hexdigest()
    return {"size": path.stat().st_size, "toc_sha256": digest}


def is_patched(path: str | Path) -> bool:
    """Whether a patch is applied, even one made by a copy of GameMod in another folder.

    Every archive keeps its manifest right after the table of contents until it is patched.
    """
    with PsarcReader(path) as reader:
        return reader.toc.entries[0].offset != reader.toc.toc_length


def _state_path(backup_path: Path) -> Path:
    return backup_path.with_name(backup_path.name + ".json")


def _write_durable(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _write_state(backup_path: Path, state: dict) -> None:
    _write_durable(_state_path(backup_path), json.dumps(state, indent=2).encode("utf-8"))


def read_patch_state(backup_path: str | Path) -> dict | None:
    state_file = _state_path(Path(backup_path))
    if not state_file.exists():
        return None
    return json.loads(state_file.read_text(encoding="utf-8"))


def patch_in_place(
    archive: str | Path,
    files: Mapping[str, bytes],
    backup_path: str | Path,
    oodle_dll: str | Path | None = None,
) -> dict:
    """Replace existing files inside an archive. Returns the saved patch state."""
    archive = Path(archive)
    backup_path = Path(backup_path)
    if read_patch_state(backup_path):
        raise PsarcError("This archive already has a patch. Restore it first.")

    original_size = archive.stat().st_size
    with PsarcReader(archive, oodle_dll) as reader:
        toc = reader.toc
        missing = [n for n in files if not reader.has(n)]
        if missing:
            raise PsarcError(f"Files not in archive: {missing}")
        manifest_entry = toc.entries[0]
        if manifest_entry.name_hash != _ZERO_HASH or manifest_entry.offset != toc.toc_length:
            raise PsarcError("Unexpected archive layout: manifest does not follow the table of contents")
        manifest_packed = toc.packed_size(manifest_entry)

    new_blocks = sum(len(_raw_block_sizes(len(d))) for d in files.values())
    if new_blocks * 2 > manifest_packed:
        raise PsarcError("Not enough room in the archive for these files")

    with open(archive, "r+b") as f:
        head = f.read(toc.toc_length)
        region = f.read(manifest_packed)

        # 1. Backup first. Nothing in the archive has changed yet.
        backup = (
            _BACKUP_MAGIC
            + struct.pack("<QQQ", original_size, len(head), len(region))
            + head
            + region
        )
        _write_durable(backup_path, backup)
        state = {
            "state": "patching",
            "original_size": original_size,
            "original_toc_sha256": hashlib.sha256(head).hexdigest(),
            "files": sorted(files),
        }
        _write_state(backup_path, state)

        # 2. Append data past the end. The old table still ignores it, so the
        #    archive stays valid if this step is interrupted.
        f.seek(0, os.SEEK_END)
        f.write(region)
        position = original_size + len(region)
        updates: dict[bytes, Entry] = {}
        sizes = list(toc.block_sizes)
        for name, data in files.items():
            digest = name_hash(name)
            updates[digest] = Entry(digest, len(sizes), len(data), position)
            sizes.extend(_raw_block_sizes(len(data)))
            f.write(data)
            position += len(data)
        f.flush()
        os.fsync(f.fileno())

        # 3. Swap in the new table. This is the only step that needs the journal.
        entries = []
        for entry in toc.entries:
            if entry.name_hash == _ZERO_HASH:
                entries.append(replace(entry, offset=original_size))
            else:
                entries.append(updates.get(entry.name_hash, entry))
        new_toc = replace(toc, entries=entries, block_sizes=sizes).pack()
        f.seek(0)
        f.write(new_toc)
        f.flush()
        os.fsync(f.fileno())

    with PsarcReader(archive, oodle_dll) as check:
        for name, data in files.items():
            if check.read(name) != data:
                raise PsarcError(f"Verification failed for {name}")
        if not check.names():
            raise PsarcError("Verification failed: manifest is unreadable")

    state.update(
        state="patched",
        patched_size=position,
        patched_toc_sha256=hashlib.sha256(new_toc).hexdigest(),
    )
    _write_state(backup_path, state)
    return state


def restore(archive: str | Path, backup_path: str | Path) -> None:
    """Undo patch_in_place, including one that was interrupted."""
    archive = Path(archive)
    backup_path = Path(backup_path)
    state = read_patch_state(backup_path)
    if state is None:
        raise PsarcError("No patch to restore")

    backup = backup_path.read_bytes()
    if backup[:8] != _BACKUP_MAGIC:
        raise PsarcError("Backup file is damaged")
    original_size, head_len, region_len = struct.unpack("<QQQ", backup[8:32])
    head = backup[32 : 32 + head_len]
    region = backup[32 + head_len : 32 + head_len + region_len]
    if hashlib.sha256(head).hexdigest() != state["original_toc_sha256"]:
        raise PsarcError("Backup file is damaged")

    if state["state"] == "patched":
        current = toc_fingerprint(archive)
        if current["toc_sha256"] != state["patched_toc_sha256"]:
            raise PsarcError(
                "The archive changed since it was patched, probably from a Steam update. "
                "Use Steam's Verify Integrity to restore it."
            )

    with open(archive, "r+b") as f:
        f.seek(0)
        f.write(head)
        f.write(region)
        f.truncate(original_size)
        f.flush()
        os.fsync(f.fileno())

    if toc_fingerprint(archive)["toc_sha256"] != state["original_toc_sha256"]:
        raise PsarcError("Restore verification failed")
    if archive.stat().st_size != original_size:
        raise PsarcError("Restore verification failed")

    _state_path(backup_path).unlink()
    backup_path.unlink()


def forget_patch(backup_path: str | Path) -> None:
    """Discard a backup that no longer applies, because Steam replaced the patched archive."""
    backup_path = Path(backup_path)
    _state_path(backup_path).unlink(missing_ok=True)
    backup_path.unlink(missing_ok=True)
