"""Subtitle packs: the translated lines for one game, shipped beside the app.

    GameMod.exe
    subtitles/
        tlou1/hindi.json
        another-game/hindi.json

A pack holds only the translations, by line. The English comes from the player's own copy
of the game, so a pack contains none of the game's original text.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

from gamemod.core import paths
from gamemod.core.store import Store
from gamemod.core.translate.qa import check_translation

_FORMAT = 1


class PackError(Exception):
    pass


def pack_path(game_id: str, language: str = "hindi") -> Path:
    return paths.app_root() / "subtitles" / game_id / f"{language}.json"


def write_pack(path: Path, game_id: str, title: str, lines: Mapping[str, Mapping[str, str]]) -> int:
    """Save a pack and return how many lines it holds."""
    path.parent.mkdir(parents=True, exist_ok=True)
    count = sum(len(table) for table in lines.values())
    document = {"format": _FORMAT, "game": game_id, "title": title, "line_count": count, "lines": lines}
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(document, ensure_ascii=False, indent=0), encoding="utf-8")
    temporary.replace(path)
    return count


def read_pack(path: Path, game_id: str) -> dict[str, dict[str, str]] | None:
    """The pack's lines, or None if there is no pack for this game."""
    if not path.exists():
        return None
    problem = f"The subtitle file {path.name} for this game is damaged. Please download it again."
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise PackError(problem) from None
    if not isinstance(document, dict) or not isinstance(document.get("lines"), dict):
        raise PackError(problem)
    if document.get("format", 0) > _FORMAT:
        raise PackError(f"The subtitle file {path.name} needs a newer version of GameMod.")
    if document.get("game") != game_id:
        raise PackError(f"The subtitle file in the {path.parent.name} folder is for a different game.")
    lines = document["lines"]
    for table in lines.values():
        if not isinstance(table, dict) or not all(isinstance(text, str) for text in table.values()):
            raise PackError(problem)
    return lines


def _is_usable(source: str, target: str) -> bool:
    return not check_translation(source, target).blocking


def load_into_store(store: Store, game_id: str) -> bool:
    """Bring a game's pack into its database. Returns False if there is no pack."""
    lines = read_pack(pack_path(game_id), game_id)
    if lines is None:
        return False
    store.import_pack(lines, _is_usable)
    return True
