"""What a game needs to provide to work with the mod manager.

Adding a game means writing one adapter. The translator, database, quality checks,
review screen and Hindi font builder are shared.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Mapping

ProgressFn = Callable[[str], None]


@dataclass(frozen=True)
class SourceLine:
    table: str
    key: str
    order: int  # scene order across the whole game
    text: str  # the words to translate, with any engine tags removed
    hint: str = ""  # optional text that helps translation, never shown to the player
    prefix: str = ""  # engine tags to put back before the translation
    suffix: str = ""  # engine tags or whitespace to put back after it


class InstallState(Enum):
    NOT_INSTALLED = "not_installed"
    INSTALLED = "installed"
    GAME_UPDATED = "game_updated"  # Steam replaced our patched files
    INTERRUPTED = "interrupted"  # an install stopped part way
    INSTALLED_ELSEWHERE = "installed_elsewhere"  # patched by a copy of GameMod in another folder
    GAME_MISSING = "game_missing"


class GameRunning(Exception):
    pass


class GameAdapter(ABC):
    id: str
    title: str
    steam_app_id: int
    mod_title: str
    where_to_enable: str
    # How to recognise the game on disk
    folder_names: tuple[str, ...] = ()  # usual install folder names
    required_files: tuple[str, ...] = ()  # files only this game's folder has
    folder_hint: str = "the game's main folder"
    game_path: Path | None = None

    @classmethod
    def is_game_folder(cls, folder: Path) -> bool:
        folder = Path(folder)
        return bool(cls.required_files) and all((folder / name).exists() for name in cls.required_files)

    @abstractmethod
    def state(self) -> InstallState:
        ...

    @abstractmethod
    def extract_lines(self) -> list[SourceLine]:
        ...

    @abstractmethod
    def brief(self) -> "Brief":  # noqa: F821
        ...

    @abstractmethod
    def install(
        self,
        translations: Mapping[str, Mapping[str, str]],
        progress: ProgressFn = lambda _: None,
        hindi_scale: float = 1.12,
    ) -> None:
        """`translations` maps table name, then line key, to the finished text."""

    @abstractmethod
    def uninstall(self, progress: ProgressFn = lambda _: None) -> None:
        ...
