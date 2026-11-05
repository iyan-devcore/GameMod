"""Find Steam and the games installed through it."""
from __future__ import annotations

import re
import winreg
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class InstalledGame:
    app_id: int
    name: str
    path: Path
    build_id: str


def steam_root() -> Path | None:
    for hive, subkey, value in (
        (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
    ):
        try:
            with winreg.OpenKey(hive, subkey) as key:
                path = Path(winreg.QueryValueEx(key, value)[0])
        except OSError:
            continue
        if path.exists():
            return path
    return None


def library_folders() -> list[Path]:
    root = steam_root()
    if root is None:
        return []
    folders = [root]
    vdf = root / "steamapps" / "libraryfolders.vdf"
    if vdf.exists():
        text = vdf.read_text(encoding="utf-8", errors="replace")
        for match in re.finditer(r'"path"\s+"(.+?)"', text):
            folder = Path(match.group(1).replace("\\\\", "\\"))
            if folder not in folders and folder.exists():
                folders.append(folder)
    return folders


def _manifest_value(text: str, name: str) -> str:
    match = re.search(rf'"{name}"\s+"(.*?)"', text)
    return match.group(1) if match else ""


def installed_games() -> list[InstalledGame]:
    games: dict[int, InstalledGame] = {}
    for library in library_folders():
        apps = library / "steamapps"
        for manifest in apps.glob("appmanifest_*.acf"):
            text = manifest.read_text(encoding="utf-8", errors="replace")
            try:
                app_id = int(_manifest_value(text, "appid"))
            except ValueError:
                continue
            path = apps / "common" / _manifest_value(text, "installdir")
            if path.exists():
                games[app_id] = InstalledGame(
                    app_id, _manifest_value(text, "name"), path, _manifest_value(text, "buildid")
                )
    return sorted(games.values(), key=lambda g: g.name.lower())


def find_game(app_id: int) -> InstalledGame | None:
    return next((g for g in installed_games() if g.app_id == app_id), None)
