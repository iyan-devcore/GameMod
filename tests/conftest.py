"""Shared test helpers."""
import struct
from pathlib import Path

from gamemod.adapters.tlou1.psarc import PsarcReader, Toc

GAME = Path(r"D:\SteamLibrary\steamapps\common\The Last of Us Part I")
_BACKUP = Path(__file__).resolve().parents[1] / "data" / "tlou1" / "core.backup"


def open_original_core() -> PsarcReader:
    """Read the game's files as shipped, even when the mod is installed.

    The mod only appends data and replaces the table of contents, so pointing a reader at
    the backed-up table gives back every original file except the moved manifest.
    """
    reader = PsarcReader(GAME / "build" / "pc" / "main" / "core.psarc", GAME / "oo2core_9_win64.dll")
    if _BACKUP.exists():
        raw = _BACKUP.read_bytes()
        head_length = struct.unpack("<Q", raw[16:24])[0]
        reader.toc = Toc.parse(raw[32 : 32 + head_length])
        reader._by_hash = {entry.name_hash: entry for entry in reader.toc.entries}
    return reader
