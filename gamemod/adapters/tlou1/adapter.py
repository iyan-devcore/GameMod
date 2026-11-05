"""The Last of Us Part I: Hindi as a choice in the game's own subtitle language list.

English subtitles are never changed. Hindi takes over the Croatian language slot, which
has no voice audio, so it appears only in the Text and Subtitles lists. The only visible
menu change is that entry's name.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Mapping

import yaml

from gamemod.adapters.base import GameAdapter, GameRunning, InstallState, ProgressFn, SourceLine
from gamemod.core import library
from gamemod.core.devanagari import (
    FontStyle,
    assign_codepoints,
    encode_text,
    merge_into_font,
    unique_clusters,
)
from gamemod.core.paths import data_dir
from gamemod.core.translate.base import Brief

from . import psarc
from .text_table import TextTable

_APP_ID = 1888930
_EXE = "tlou-i.exe"
_SLOT = "hrv"
_LANGUAGE_NAME_KEY = 0x2C5C365FF1AA41A9  # "Hrvatski" in the language lists
_LANGUAGE_NAME = "हिन्दी"
# The text language tables that display the language list. The slot's own table is
# included so that choosing Hindi under Text keeps the menus in English.
_MENU_TABLES = ("eng", "uke", _SLOT)
_HINT_LANGUAGE = "rus"  # official translation whose grammar shows the speaker's gender

SUBTITLES = "subtitles"
SYSTEMIC = "subtitles-systemic"
_SYSTEMIC_ORDER_START = 1_000_000

# Font files in the English fallback chain, with the Hindi weight that suits each.
_FONTS = {
    "fonts/DINPro-Medium.otf": 500,
    "fonts/DINPro-Regular.otf": 400,
    "fonts/HelveticaNeueBold.ttf": 700,
}

_LEADING_TAGS = re.compile(r"^(?:\s*\|[@#]-?\d+\|\s*)+")


def split_engine_tags(text: str) -> tuple[str, str, str]:
    """Separate timing tags and outer whitespace from the words."""
    match = _LEADING_TAGS.match(text)
    prefix = match.group() if match else ""
    rest = text[len(prefix):]
    core = rest.rstrip()
    suffix = rest[len(core):]
    stripped = core.lstrip()
    prefix += core[: len(core) - len(stripped)]
    return prefix, stripped, suffix


class Tlou1Adapter(GameAdapter):
    id = "tlou1"
    title = "The Last of Us Part I"
    steam_app_id = _APP_ID
    mod_title = "Hindi Subtitles"
    where_to_enable = "In the game: Options > Language > Subtitles > हिन्दी"
    folder_names = ("The Last of Us Part I", "The Last of Us™ Part I", "TheLastOfUsPartI")
    required_files = ("build/pc/main/core.psarc", "oo2core_9_win64.dll")
    folder_hint = "the folder that contains tlou-i.exe"

    def __init__(self, game_path: Path | None = None, work_dir: Path | None = None):
        if game_path is None:
            game_path, _how = library.find_folder(type(self))
        self.game_path = game_path
        self.work_dir = work_dir or data_dir(self.id)
        self._backup = self.work_dir / "core.backup"

    @property
    def _main(self) -> Path:
        return self.game_path / "build" / "pc" / "main"

    @property
    def _core(self) -> Path:
        return self._main / "core.psarc"

    @property
    def _oodle(self) -> Path:
        return self.game_path / "oo2core_9_win64.dll"

    def _reader(self) -> psarc.PsarcReader:
        return psarc.PsarcReader(self._core, self._oodle)

    def _ensure_closed(self) -> None:
        result = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {_EXE}", "/NH"],
            capture_output=True,
            text=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if _EXE in result.stdout.lower():
            raise GameRunning("Close The Last of Us Part I first.")

    def state(self) -> InstallState:
        if not self.game_path or not self._core.exists():
            return InstallState.GAME_MISSING
        patch = psarc.read_patch_state(self._backup)
        if patch is None:
            if psarc.is_patched(self._core):
                return InstallState.INSTALLED_ELSEWHERE
            return InstallState.NOT_INSTALLED
        if patch["state"] != "patched":
            return InstallState.INTERRUPTED
        current = psarc.toc_fingerprint(self._core)
        if current["toc_sha256"] == patch["patched_toc_sha256"]:
            return InstallState.INSTALLED
        return InstallState.GAME_UPDATED

    def extract_lines(self) -> list[SourceLine]:
        lines: list[SourceLine] = []
        with self._reader() as core:
            for table, start in ((SUBTITLES, 0), (SYSTEMIC, _SYSTEMIC_ORDER_START)):
                english = TextTable.parse(core.read(f"text2/eng.{table}"))
                hints = TextTable.parse(core.read(f"text2/{_HINT_LANGUAGE}.{table}")).as_dict()
                for position, record in enumerate(english.in_scene_order()):
                    original = record.text.decode("utf-8")
                    prefix, words, suffix = split_engine_tags(original)
                    if not words:
                        continue
                    hint = split_engine_tags(hints.get(record.key, ""))[1]
                    lines.append(
                        SourceLine(
                            table=table,
                            key=f"{record.key:016x}",
                            order=start + position,
                            text=words,
                            hint=hint,
                            prefix=prefix,
                            suffix=suffix,
                        )
                    )
        return lines

    def brief(self) -> Brief:
        data = yaml.safe_load((Path(__file__).parent / "glossary.yaml").read_text(encoding="utf-8"))
        return Brief(system_prompt=data["system_prompt"], fixed=data["fixed"])

    def build_files(
        self,
        core: psarc.PsarcReader,
        translations: Mapping[str, Mapping[str, str]],
        progress: ProgressFn = lambda _: None,
        hindi_scale: float = 1.12,
    ) -> dict[str, bytes]:
        """Every file the mod replaces. The archive must be in its original state."""
        texts = [text for table in translations.values() for text in table.values()]
        texts.append(_LANGUAGE_NAME)
        mapping = assign_codepoints(unique_clusters(texts))

        progress(f"Building Hindi fonts ({len(mapping):,} letter combinations)...")
        style = FontStyle(scale=hindi_scale)
        files = {
            name: merge_into_font(core.read(name), mapping, weight, style)
            for name, weight in _FONTS.items()
        }

        progress("Building subtitle files...")
        for table in (SUBTITLES, SYSTEMIC):
            english = TextTable.parse(core.read(f"text2/eng.{table}"))
            table_translations = translations.get(table, {})
            replaced = {
                record.key: encode_text(table_translations[f"{record.key:016x}"], mapping).encode("utf-8")
                for record in english.records
                if f"{record.key:016x}" in table_translations
            }
            files[f"text2/{_SLOT}.{table}"] = english.build(replaced)

        name = encode_text(_LANGUAGE_NAME, mapping).encode("utf-8")
        english_menus = core.read("text2/eng.common")
        for language in _MENU_TABLES:
            source = english_menus if language == _SLOT else core.read(f"text2/{language}.common")
            files[f"text2/{language}.common"] = TextTable.parse(source).build({_LANGUAGE_NAME_KEY: name})
        return files

    def install(
        self,
        translations: Mapping[str, Mapping[str, str]],
        progress: ProgressFn = lambda _: None,
        hindi_scale: float = 1.12,
    ) -> None:
        self._ensure_closed()
        state = self.state()
        if state is InstallState.GAME_MISSING:
            raise RuntimeError("The game could not be found.")
        if state is InstallState.INSTALLED_ELSEWHERE:
            raise RuntimeError(
                "GameMod from another folder has already changed this game. Turn it off there first, "
                "or use Verify Integrity in Steam to reset the game."
            )
        if state is InstallState.GAME_UPDATED:
            # Steam replaced the patched table, so the old backup no longer applies.
            psarc.forget_patch(self._backup)
        elif state in (InstallState.INSTALLED, InstallState.INTERRUPTED):
            progress("Removing the previous install...")
            psarc.restore(self._core, self._backup)

        with self._reader() as core:
            files = self.build_files(core, translations, progress, hindi_scale)
        progress("Installing into the game...")
        psarc.patch_in_place(self._core, files, self._backup, self._oodle)
        progress("Installed.")

    def uninstall(self, progress: ProgressFn = lambda _: None) -> None:
        self._ensure_closed()
        state = self.state()
        if state is InstallState.GAME_UPDATED:
            psarc.forget_patch(self._backup)
        elif state in (InstallState.INSTALLED, InstallState.INTERRUPTED):
            progress("Restoring the game's original files...")
            psarc.restore(self._core, self._backup)
        progress("Removed.")
