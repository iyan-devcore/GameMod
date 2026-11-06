"""Marvel's Spider-Man Remastered (PC) adapter.

Hindi subtitles go in the Danish text slot, picked in the game under Settings > Text Language.
Danish has text but no voices, so the English voices play. In that slot only spoken lines
become Hindi; every other entry is an exact copy of the English, so menus and all other text
stay in English. The English slot is not changed.

Install writes one new archive beside the game's own, holding the Danish text table and the
Latin font file with Hindi syllables added, and points the toc at it. The original toc is
kept, and uninstalling puts it back and deletes the archive.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Mapping

import yaml

from gamemod.adapters.base import GameAdapter, GameRunning, InstallState, ProgressFn, SourceLine
from gamemod.adapters.insomniac.archive import AssetReader, Toc
from gamemod.adapters.insomniac.gfx import EM, GfxFile, add_glyphs, make_glyph, outline_area_sign
from gamemod.adapters.insomniac.localization import LOCALIZATION_PATH, Localization
from gamemod.adapters.msmr import lines as spoken_lines
from gamemod.adapters.msmr.speakers import describe
from gamemod.core import library
from gamemod.core.devanagari import FontStyle, assign_codepoints, encode_text, unique_clusters
from gamemod.core.devanagari.fontbuild import cluster_outlines
from gamemod.core.paths import data_dir
from gamemod.core.translate.base import Brief

_APP_ID = 1817070
_EXE = "Spider-Man.exe"
_ARCHIVES = "asset_archive"
_TOC = "toc"
ARCHIVE_NAME = "gamemod_hindi"  # our archive, beside the game's own in asset_archive
_TEMP_SUFFIX = ".gamemod.tmp"
_RECORD = "installed_toc.json"
_BACKUP = "toc.original"

TABLE = "localization_all"
ENGLISH_SPAN = 8
DANISH_SPAN = 24  # Hindi goes here
RUSSIAN_SPAN = 112  # official Russian, shown to translators for speaker gender

FONT_PATH = "ui/export/fonts/Font_LatinAS3.gfx"
# Every text font that Latin-script languages use, with the Noto weight that matches it.
FONT_WEIGHTS = {
    b"Azbuka Pro Medium\x00": 500,
    b"Azbuka Pro Bold\x00": 700,
    b"Azbuka Pro Bold Italic\x00": 700,
}

_NAME_PREFIX = "NAME_SUBTITLE_"
_TAG = re.compile(r"<[^>]+>")
_KEY = re.compile(r"^([A-Z0-9]+)_(.*?)(?:_(\d+))?$")
_YOU = re.compile(r"\b(you|your|you're|yourself|you've|you'll|you'd)\b", re.IGNORECASE)


def _sha256(path: Path) -> str:
    with open(path, "rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def _scene_order(key: str) -> tuple:
    """Lines of one scene share a key apart from the speaker code and number, so sorting by
    scene and then number puts each conversation in order, story acts first."""
    match = _KEY.match(key)
    if not match:
        return (9, key, 0, key)
    _code, scene, number = match.groups()
    act = next((rank for rank, mark in enumerate(("_A1_", "_A2_", "_A3_"), 1) if mark in f"_{scene}_"), 4)
    return (act, scene, int(number or 0), key)


class MsmrAdapter(GameAdapter):
    id = "msmr"
    title = "Marvel's Spider-Man Remastered"
    steam_app_id = _APP_ID
    mod_title = "Hindi Subtitles"
    where_to_enable = (
        "In the game: Settings > Text Language > Danish. "
        "Menus and voices stay in English; spoken lines show in Hindi."
    )
    folder_names = ("Marvel's Spider-Man Remastered",)
    required_files = (_EXE, f"{_ARCHIVES}/{_TOC}")
    folder_hint = "the folder that contains Spider-Man.exe"
    lines_depend_on_speaker = True

    def __init__(self, game_path: Path | None = None, work_dir: Path | None = None):
        if game_path is None:
            game_path, _how = library.find_folder(type(self))
        self.game_path = game_path
        self.work_dir = work_dir or data_dir(self.id)

    # ---- where things are ------------------------------------------------------------------

    @property
    def _archives(self) -> Path:
        return Path(self.game_path) / _ARCHIVES

    @property
    def _toc_path(self) -> Path:
        return self._archives / _TOC

    @property
    def _archive_path(self) -> Path:
        return self._archives / ARCHIVE_NAME

    @property
    def _record_path(self) -> Path:
        return self.work_dir / _RECORD

    @property
    def _backup_path(self) -> Path:
        return self.work_dir / _BACKUP

    def _read_record(self) -> dict | None:
        try:
            return json.loads(self._record_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    # ---- state -----------------------------------------------------------------------------

    def state(self) -> InstallState:
        if not self.game_path or not self._toc_path.exists():
            return InstallState.GAME_MISSING
        if any(self._archives.glob(f"*{_TEMP_SUFFIX}")):
            return InstallState.INTERRUPTED
        record = self._read_record()
        if record is None:
            if Toc.read(self._toc_path).archive_index(ARCHIVE_NAME) is not None:
                return InstallState.INSTALLED_ELSEWHERE
            return InstallState.NOT_INSTALLED
        if (
            _sha256(self._toc_path) == record["toc_sha256"]
            and self._archive_path.exists()
            and _sha256(self._archive_path) == record["archive_sha256"]
        ):
            return InstallState.INSTALLED
        # Steam replaces the toc when it updates or verifies the game.
        return InstallState.GAME_UPDATED

    def _our_toc_is_in_place(self) -> bool:
        """True when the game's toc is the one we wrote and our backup of the original is intact."""
        record = self._read_record()
        return (
            record is not None
            and self._backup_path.exists()
            and _sha256(self._toc_path) == record["toc_sha256"]
            and _sha256(self._backup_path) == record["original_sha256"]
        )

    def _original_toc_path(self) -> Path:
        """The file holding the game's own toc, even while the mod is installed."""
        if self._our_toc_is_in_place():
            return self._backup_path
        if Toc.read(self._toc_path).archive_index(ARCHIVE_NAME) is not None:
            raise RuntimeError(
                "This game's file list was changed by GameMod in another folder. Turn the mod off "
                "there first, or use Verify Integrity in Steam to reset the game."
            )
        return self._toc_path

    def _reader(self) -> AssetReader:
        if not self.game_path:
            raise RuntimeError("Game folder not set")
        return AssetReader(self._archives, Toc.read(self._original_toc_path()))

    # ---- lines -----------------------------------------------------------------------------

    def extract_lines(self) -> list[SourceLine]:
        with self._reader() as reader:
            english = Localization(reader.read(LOCALIZATION_PATH, ENGLISH_SPAN))
            russian = Localization(reader.read(LOCALIZATION_PATH, RUSSIAN_SPAN)).as_dict()
        names = {
            key[len(_NAME_PREFIX) :]: _TAG.sub("", text).strip()
            for key, text in zip(english.keys, english.texts)
            if key.startswith(_NAME_PREFIX)
        }
        found: list[tuple[tuple, SourceLine]] = []
        for key, text in zip(english.keys, english.texts):
            code = key.split("_", 1)[0]
            if "<ts=" not in text and code not in names:
                continue
            spoken = spoken_lines.parse(text)
            words = spoken_lines.for_translator(spoken)
            # Lines such as "<exerts for HeavyInnerDemon_Intro>" are notes for the voice
            # recording, not words anyone says, so they stay as they are.
            if not _TAG.sub("", words.replace(spoken_lines.PAUSE, "")).strip():
                continue
            speaker = spoken.speaker_override or code
            who, gender_known = describe(speaker, names.get(speaker, ""))
            hint = who
            # Russian verbs show the speaker's gender, and often the gender of who is addressed.
            if not gender_known or _YOU.search(words):
                other = spoken_lines.for_translator(spoken_lines.parse(russian.get(key, "")))
                if other:
                    hint += " | " + other.replace(f" {spoken_lines.PAUSE} ", " ")
            found.append((_scene_order(key), SourceLine(TABLE, key, 0, words, hint)))
        found.sort(key=lambda pair: pair[0])
        return [
            SourceLine(line.table, line.key, order, line.text, line.hint)
            for order, (_sort, line) in enumerate(found)
        ]

    def brief(self) -> Brief:
        data = yaml.safe_load((Path(__file__).parent / "glossary.yaml").read_text(encoding="utf-8"))
        return Brief(system_prompt=data["system_prompt"], fixed=data.get("fixed") or {})

    # ---- building --------------------------------------------------------------------------

    def build_assets(
        self,
        reader: AssetReader,
        translations: Mapping[str, Mapping[str, str]],
        progress: ProgressFn = lambda _: None,
        hindi_scale: float = 1.12,
    ) -> tuple[bytes, bytes, int]:
        """The Danish text table, the font file, and how many lines are in Hindi."""
        table = translations.get(TABLE, {})
        mapping = assign_codepoints(unique_clusters(table.values()))

        progress("Building the Hindi text table...")
        english = Localization(reader.read(LOCALIZATION_PATH, ENGLISH_SPAN))
        texts: dict[str, str] = {}
        for key, original in zip(english.keys, english.texts):
            hindi = table.get(key)
            if not hindi:
                continue
            entry = spoken_lines.rebuild(spoken_lines.parse(original), encode_text(hindi, mapping))
            if entry is not None:
                texts[key] = entry
        danish = english.rebuild(texts)

        progress(f"Adding {len(mapping):,} Hindi letter combinations to the fonts...")
        font_bytes = reader.read(FONT_PATH)
        movie = GfxFile(font_bytes)
        fonts = {font.name: font for _index, font in movie.fonts()}
        style = FontStyle(scale=hindi_scale)
        glyphs = {}
        for name, weight in FONT_WEIGHTS.items():
            sample = next(glyph.shape for glyph in fonts[name].glyphs if glyph.code == ord("O"))
            direction = outline_area_sign(sample)
            glyphs[name] = [
                make_glyph(code, outline, advance, direction)
                for code, outline, advance in cluster_outlines(mapping, weight, EM, style)
            ]
        return danish, add_glyphs(font_bytes, glyphs), len(texts)

    # ---- install ---------------------------------------------------------------------------

    def _ensure_closed(self) -> None:
        try:
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            result = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {_EXE}", "/NH"],
                capture_output=True,
                text=True,
                creationflags=flags,
            )
        except (FileNotFoundError, PermissionError):
            return
        if _EXE.lower() in result.stdout.lower():
            raise GameRunning("Close Marvel's Spider-Man Remastered first.")

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
        for leftover in self._archives.glob(f"*{_TEMP_SUFFIX}"):
            leftover.unlink()

        source = self._original_toc_path()
        with AssetReader(self._archives, Toc.read(source)) as reader:
            danish, font, count = self.build_assets(reader, translations, progress, hindi_scale)
        progress(f"Writing {count:,} Hindi lines...")

        font_at = len(danish) + (-len(danish) % 16)
        archive_bytes = danish + b"\0" * (font_at - len(danish)) + font
        toc = Toc.read(source)
        archive = toc.add_archive(ARCHIVE_NAME)
        danish_index = toc.find(LOCALIZATION_PATH, DANISH_SPAN)
        font_index = toc.find(FONT_PATH, 0)
        if danish_index is None or font_index is None:
            raise RuntimeError("This version of the game is laid out differently. GameMod needs an update.")
        toc.point(danish_index, archive, 0, len(danish))
        toc.point(font_index, archive, font_at, len(font))
        toc_bytes = toc.to_bytes()

        # Keep the game's own toc before changing anything.
        self.work_dir.mkdir(parents=True, exist_ok=True)
        if source != self._backup_path:
            temporary_backup = self._backup_path.with_name(_BACKUP + ".tmp")
            shutil.copyfile(self._toc_path, temporary_backup)
            os.replace(temporary_backup, self._backup_path)
        record = {
            "original_sha256": _sha256(self._backup_path),
            "toc_sha256": hashlib.sha256(toc_bytes).hexdigest(),
            "archive_sha256": hashlib.sha256(archive_bytes).hexdigest(),
        }
        # Archive first, then the toc, each written beside its target and then swapped in, so
        # the game never sees a toc pointing at a half-written archive.
        progress("Writing files...")
        new_archive = self._archives / (ARCHIVE_NAME + _TEMP_SUFFIX)
        new_archive.write_bytes(archive_bytes)
        os.replace(new_archive, self._archive_path)
        new_toc = self._archives / (_TOC + _TEMP_SUFFIX)
        new_toc.write_bytes(toc_bytes)
        self._record_path.write_text(json.dumps(record), encoding="utf-8")
        os.replace(new_toc, self._toc_path)
        progress("Installed.")

    def uninstall(self, progress: ProgressFn = lambda _: None) -> None:
        self._ensure_closed()
        progress("Putting the game's files back...")
        for leftover in self._archives.glob(f"*{_TEMP_SUFFIX}"):
            leftover.unlink()
        if self._read_record() is None:
            return
        if self._toc_path.exists() and self._our_toc_is_in_place():
            restored = self._archives / (_TOC + _TEMP_SUFFIX)
            shutil.copyfile(self._backup_path, restored)
            os.replace(restored, self._toc_path)
        # If Steam already replaced the toc, it no longer points at the archive.
        if Toc.read(self._toc_path).archive_index(ARCHIVE_NAME) is None and self._archive_path.exists():
            self._archive_path.unlink()
        self._record_path.unlink()
        if self._backup_path.exists():
            self._backup_path.unlink()
        progress("Removed.")
