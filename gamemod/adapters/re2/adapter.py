"""Resident Evil 2 (2019) Remake adapter.

Hindi subtitles via the game's Polish (pl, slot 7) option.
Menus, item names, documents, and hints remain 100% in English.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Mapping

import yaml

from gamemod.adapters.base import (
    GameAdapter,
    GameRunning,
    InstallState,
    ProgressFn,
    SourceLine,
)
from gamemod.adapters.re_engine.font import decrypt_font, encrypt_font
from gamemod.adapters.re_engine.msg import MsgFile
from gamemod.adapters.re_engine.pak import PakReader, write_pak
from gamemod.adapters.re2.paths import (
    ACTIVE_MSG_PATHS,
    FONTS,
    HEALTHWARNING_PATH_EN,
    HEALTHWARNING_PATH_PL,
)
from gamemod.adapters.re2.timed import (
    build_timed_entry,
    is_timed_entry,
    split_timed_entry,
)
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

_APP_ID = 883710
_EXE = "re2.exe"
_BASE_PAK = "re_chunk_000.pak"
_PATCH_NAME = "re_chunk_000.pak.patch_{:03d}.pak"
_PATCH_GLOB = "re_chunk_000.pak.patch_*.pak"
_TEMP_SUFFIX = ".gamemod.tmp"
_RECORD = "installed_patch.json"
# A small file in our pak so that any copy of GameMod can recognise it. The game ignores it.
_MARKER = "gamemod/hindi_subtitles.marker"

IN_SCOPE_DIRS = ("/mes_cf/", "/mes_conv/", "/mes_ev/", "/mes_conv_4th/")

# Who speaks, from the ending of a line's name. Worked out from who the lines address and
# from the grammar of the official Russian. Codes left out could not be identified.
_SPEAKERS = {
    "pl0000": "Leon, male",
    "pl1000": "Claire, female",
    "pl2000": "Ada, female",
    "pl3000": "Sherry, young girl",
    "pl5300": "Annette, female",
    "pl5100": "Chief Irons, male",
    "em9000": "Chief Irons, male, hunting Sherry",
    "pl5200": "Ben, male",
    "pl5000": "Robert Kendo, male",
    "pl6000": "William Birkin, male",
    "pl7800": "Marvin, male",
    "pl5700": "male police officer",
    "pl7200": "male",
    "pl7300": "truck driver, male",
    "vo0030": "male",
    "vo0040": "male",
    "pl4000": "male, on the radio",
    "pl4100": "male, on the radio",
    "vo0050": "public announcement, no gender",
    "vo0080": "automated announcement, no gender",
}
_SPEAKER_TAG = re.compile(r"_([a-z]+\d+)$", re.IGNORECASE)


_ONLY_TAGS = re.compile(r"^(?:<[^>]+>\s*)+$")


def _scene_entry(
    english: str,
    entry_name: str,
    file_trans: Mapping[str, str],
    text_to_trans: Mapping[str, str],
    mapping: Mapping[str, int],
) -> str | None:
    """A whole-scene entry made from its pages' translations, or None if a page has none.

    Using the translation of each page's own line keeps its wording and speaker gender.
    """
    leading, _times, pieces = split_timed_entry(english)
    built: list[tuple[str, str, str]] = []
    for i, (prefix, core, suffix) in enumerate(pieces):
        translated = file_trans.get(f"{entry_name}__page__{i}") or text_to_trans.get(core)
        if translated is None and core:
            return None
        built.append((prefix, encode_text(translated or "", mapping), suffix))
    return build_timed_entry(leading, built)


def _sha256(path: Path) -> str:
    with open(path, "rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def _plain_breaks(text: str) -> str:
    """The game's text uses Windows line breaks, which batch files and checks do not accept."""
    return text.replace("\r\n", "\n")


def _hint(entry_name: str, russian: str) -> str:
    """Who is speaking and the official Russian, for translators only."""
    match = _SPEAKER_TAG.search(entry_name)
    speaker = _SPEAKERS.get(match.group(1).lower(), "") if match else ""
    return " | ".join(part for part in (speaker, russian) if part)


class Re2Adapter(GameAdapter):
    id = "re2"
    title = "Resident Evil 2"
    steam_app_id = _APP_ID
    mod_title = "Hindi Subtitles"
    where_to_enable = "In the game: Options > Language > Display Language > Polish [pl]"
    folder_names = ("RESIDENT EVIL 2  BIOHAZARD RE2", "RESIDENT EVIL 2", "ResidentEvil2")
    required_files = ("re2.exe", "re_chunk_000.pak")
    folder_hint = "the folder that contains re2.exe"
    lines_depend_on_speaker = True

    def __init__(self, game_path: Path | None = None, work_dir: Path | None = None):
        if game_path is None:
            game_path, _how = library.find_folder(type(self))
        self.game_path = game_path
        self.work_dir = work_dir or data_dir(self.id)

    @property
    def _record_path(self) -> Path:
        return self.work_dir / _RECORD

    def _read_record(self) -> dict | None:
        try:
            return json.loads(self._record_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _patch_files(self) -> list[Path]:
        return sorted(self.game_path.glob(_PATCH_GLOB))

    @staticmethod
    def _is_ours(path: Path) -> bool:
        return path.stat().st_size > 0 and PakReader([path]).contains(_MARKER)

    def _free_slot(self) -> Path:
        """The lowest patch number that is missing or empty.

        The game did not open a patch placed above empty ones, so the mod cannot simply go
        after the last file. A file with content is never replaced.
        """
        number = 1
        while True:
            path = self.game_path / _PATCH_NAME.format(number)
            if not path.exists() or path.stat().st_size == 0:
                return path
            number += 1

    def _ensure_closed(self) -> None:
        try:
            flags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
            result = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {_EXE}", "/NH"],
                capture_output=True,
                text=True,
                creationflags=flags,
            )
            if _EXE in result.stdout.lower():
                raise GameRunning("Close Resident Evil 2 first.")
        except (FileNotFoundError, PermissionError):
            pass

    def state(self) -> InstallState:
        if not self.game_path or not (self.game_path / _BASE_PAK).exists():
            return InstallState.GAME_MISSING
        if any(self.game_path.glob(f"*{_TEMP_SUFFIX}")):
            return InstallState.INTERRUPTED
        record = self._read_record()
        if record is None:
            if any(self._is_ours(path) for path in self._patch_files()):
                return InstallState.INSTALLED_ELSEWHERE
            return InstallState.NOT_INSTALLED
        patch = self.game_path / record["name"]
        if patch.exists() and _sha256(patch) == record["sha256"]:
            return InstallState.INSTALLED
        # Steam can empty the file again when it updates or verifies the game.
        return InstallState.GAME_UPDATED

    def _reader(self) -> PakReader:
        """The game's own files only. Our patch must never be read as if it were the original."""
        if not self.game_path:
            raise RuntimeError("Game folder not set")
        originals = [path for path in self._patch_files() if not self._is_ours(path)]
        return PakReader([self.game_path / _BASE_PAK, *originals])

    def extract_lines(self) -> list[SourceLine]:
        reader = self._reader()
        lines: list[SourceLine] = []
        order = 0

        in_scope_paths = [
            p for p in ACTIVE_MSG_PATHS
            if any(d in p for d in IN_SCOPE_DIRS)
        ]

        for path in in_scope_paths:
            raw = reader.get(path)
            msg = MsgFile(raw)
            individual_texts = {
                _plain_breaks(e.texts[1]).strip()
                for e in msg.entries
                if not is_timed_entry(e.texts[1]) and e.texts[1].strip()
            }

            for entry in msg.entries:
                eng = _plain_breaks(entry.texts[1]).strip()
                if not eng or eng == "<END>" or eng.startswith("<COLOR FF0000>#Rejected#"):
                    continue
                if _ONLY_TAGS.match(eng):  # points at another line, so there are no words here
                    continue

                russian =_plain_breaks(entry.texts[6]).strip()

                if is_timed_entry(eng):
                    _leading, _times, pieces = split_timed_entry(eng)
                    russian_pieces = split_timed_entry(russian)[2] if is_timed_entry(russian) else []
                    for i, (_prefix, core, _suffix) in enumerate(pieces):
                        if core and core not in individual_texts:
                            # build_files puts each page's own spacing back, so none is stored here.
                            russian_page = russian_pieces[i][1] if i < len(russian_pieces) else ""
                            lines.append(
                                SourceLine(
                                    table=path,
                                    key=f"{entry.name}__page__{i}",
                                    order=order,
                                    text=core,
                                    hint=_hint(entry.name, russian_page),
                                )
                            )
                            order += 1
                else:
                    lines.append(
                        SourceLine(
                            table=path,
                            key=entry.name,
                            order=order,
                            text=eng,
                            hint=_hint(entry.name, russian),
                        )
                    )
                    order += 1

        return lines

    def brief(self) -> Brief:
        glossary_path = Path(__file__).parent / "glossary.yaml"
        data = yaml.safe_load(glossary_path.read_text(encoding="utf-8"))
        return Brief(system_prompt=data["system_prompt"], fixed=data.get("fixed", {}))

    def build_files(
        self,
        reader: PakReader,
        translations: Mapping[str, Mapping[str, str]],
        progress: ProgressFn = lambda _: None,
        hindi_scale: float = 1.12,
    ) -> dict[str | tuple[int, int], bytes]:
        """Build all modified game files for the patch PAK archive."""
        all_texts: list[str] = [
            text for table in translations.values() for text in table.values()
        ]

        mapping = assign_codepoints(unique_clusters(all_texts), skip=(0xF001, 0xF002))

        files: dict[str | tuple[int, int], bytes] = {}

        # 1. Build Hindi subtitle fonts
        progress(f"Building Hindi fonts ({len(mapping):,} letter combinations)...")
        style = FontStyle(scale=hindi_scale)
        for font_path, weight in FONTS.items():
            raw_font = reader.get(font_path)
            dec_font = decrypt_font(raw_font)
            merged_otf = merge_into_font(dec_font, mapping, weight, style)
            files[font_path] = encrypt_font(merged_otf)

        # 2. Health warning screen: Polish file replaced with English texture
        progress("Replacing Polish health warning with English version...")
        if reader.contains(HEALTHWARNING_PATH_EN):
            files[HEALTHWARNING_PATH_PL] = reader.get(HEALTHWARNING_PATH_EN)

        # 3. Build all 234 active message files
        progress("Building localized message files...")
        for path in ACTIVE_MSG_PATHS:
            raw = reader.get(path)
            msg = MsgFile(raw)
            file_trans = translations.get(path, {})

            if file_trans:
                slot_7: dict[str, str] = {}
                text_to_trans = {
                    e.texts[1].strip(): file_trans[e.name]
                    for e in msg.entries
                    if e.name in file_trans
                }

                for entry in msg.entries:
                    english = entry.texts[1]
                    if is_timed_entry(english) or "<PAGE>" in english:
                        scene = _scene_entry(english, entry.name, file_trans, text_to_trans, mapping)
                        if scene is not None:
                            slot_7[entry.name] = scene
                            continue
                    if entry.name in file_trans:
                        slot_7[entry.name] = encode_text(file_trans[entry.name], mapping)

                files[path] = msg.rebuild(
                    slot_7_translations=slot_7, copy_english_for_remaining=True
                )
            else:
                files[path] = msg.rebuild(
                    slot_7_translations=None, copy_english_for_remaining=True
                )

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

        self._remove_patch()
        files = self.build_files(self._reader(), translations, progress, hindi_scale)
        progress("Writing patch archive...")
        self._write_patch(files)
        progress("Installed.")

    def uninstall(self, progress: ProgressFn = lambda _: None) -> None:
        self._ensure_closed()
        progress("Removing patch archive...")
        self._remove_patch()
        progress("Removed.")

    def _write_patch(self, files: Mapping[str | tuple[int, int], bytes]) -> None:
        target = self._free_slot()
        record: dict[str, object] = {"name": target.name, "was_empty": target.exists()}
        if target.exists():
            record["old_mtime"] = target.stat().st_mtime
        # Written beside the target first, so the game never sees a half-written file.
        temporary = target.with_name(target.name + _TEMP_SUFFIX)
        write_pak(temporary, {**files, _MARKER: b"GameMod Hindi subtitles\n"})
        record["sha256"] = _sha256(temporary)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self._record_path.write_text(json.dumps(record), encoding="utf-8")
        os.replace(temporary, target)

    def _remove_patch(self) -> None:
        """Put the game folder back as it was. Files we did not write are left alone."""
        for leftover in self.game_path.glob(f"*{_TEMP_SUFFIX}"):
            leftover.unlink()
        record = self._read_record()
        if record is None:
            return
        patch = self.game_path / record["name"]
        if patch.exists() and self._is_ours(patch):
            if record["was_empty"]:
                patch.write_bytes(b"")
                os.utime(patch, (record["old_mtime"], record["old_mtime"]))
            else:
                patch.unlink()
        self._record_path.unlink()
