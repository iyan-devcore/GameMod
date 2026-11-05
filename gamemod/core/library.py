"""Find supported games on this PC, automatically or from a folder the user chose."""
from __future__ import annotations

import ctypes
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Sequence

from gamemod.adapters.base import GameAdapter
from gamemod.core import paths, steam

MANUAL = "manual"
STEAM = "steam"
FOLDER = "folder"
_DRIVE_FIXED = 3
_PARENTS_TO_CHECK = 3  # so choosing a subfolder of the game still works


@dataclass(frozen=True)
class GameEntry:
    adapter_class: type[GameAdapter]
    path: Path | None
    how: str = ""

    @property
    def found(self) -> bool:
        return self.path is not None


def _saved_file() -> Path:
    return paths.app_root() / "data" / "games.json"


def saved_folders() -> dict[str, str]:
    try:
        saved = json.loads(_saved_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return saved if isinstance(saved, dict) else {}


def remember_folder(game_id: str, folder: Path) -> None:
    saved = saved_folders()
    saved[game_id] = str(folder)
    file = _saved_file()
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps(saved, indent=2), encoding="utf-8")


def _fixed_drives() -> list[Path]:
    """Internal drives only. Asking network or disc drives can hang for a long time."""
    drives = []
    mask = ctypes.windll.kernel32.GetLogicalDrives()
    for index in range(26):
        if mask & (1 << index):
            root = f"{chr(ord('A') + index)}:\\"
            if ctypes.windll.kernel32.GetDriveTypeW(root) == _DRIVE_FIXED:
                drives.append(Path(root))
    return drives


def _usual_places(cls: type[GameAdapter]) -> Iterator[Path]:
    """Direct checks of common install locations. This never searches a whole drive."""
    libraries = list(steam.library_folders())
    for drive in _fixed_drives():
        libraries += [
            drive / "SteamLibrary",
            drive / "Steam",
            drive / "Program Files (x86)" / "Steam",
            drive / "Program Files" / "Steam",
            drive / "Games" / "Steam",
            drive / "Games" / "SteamLibrary",
        ]
    for library in libraries:
        for name in cls.folder_names:
            yield library / "steamapps" / "common" / name
    for drive in _fixed_drives():
        for parent in (
            drive / "Program Files" / "Epic Games",
            drive / "Epic Games",
            drive / "Games",
        ):
            for name in cls.folder_names:
                yield parent / name


def find_folder(cls: type[GameAdapter]) -> tuple[Path | None, str]:
    """A folder the user chose wins, then Steam's own records, then common locations."""
    chosen = saved_folders().get(cls.id)
    if chosen and cls.is_game_folder(Path(chosen)):
        return Path(chosen), MANUAL
    game = steam.find_game(cls.steam_app_id)
    if game and cls.is_game_folder(game.path):
        return game.path, STEAM
    for folder in _usual_places(cls):
        if cls.is_game_folder(folder):
            return folder, FOLDER
    return None, ""


def scan(classes: Iterable[type[GameAdapter]]) -> list[GameEntry]:
    entries = []
    for cls in classes:
        folder, how = find_folder(cls)
        entries.append(GameEntry(cls, folder, how))
    return entries


def matching_folder(selected: Path, cls: type[GameAdapter]) -> Path | None:
    """The chosen folder, or one just above it, if it holds this game."""
    selected = Path(selected)
    for folder in [selected, *list(selected.parents)[:_PARENTS_TO_CHECK]]:
        if cls.is_game_folder(folder):
            return folder
    return None


def identify_folder(
    selected: Path, classes: Sequence[type[GameAdapter]]
) -> tuple[type[GameAdapter], Path] | None:
    """Work out which supported game a chosen folder holds."""
    for cls in classes:
        folder = matching_folder(selected, cls)
        if folder is not None:
            return cls, folder
    return None
