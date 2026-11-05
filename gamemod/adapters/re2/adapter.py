"""Resident Evil 2 (2019) Remake adapter.

Hindi subtitles via the game's Polish (pl, slot 7) option.
Menus, item names, documents, and hints remain 100% in English.
"""
from __future__ import annotations

import json
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
_PATCH_NAME = "re_chunk_000.pak.patch_008.pak"

IN_SCOPE_DIRS = ("/mes_cf/", "/mes_conv/", "/mes_ev/", "/mes_conv_4th/")


class Re2Adapter(GameAdapter):
    id = "re2"
    title = "Resident Evil 2"
    steam_app_id = _APP_ID
    mod_title = "Hindi Subtitles"
    where_to_enable = "In the game: Options > Language > Display Language > Polish [pl]"
    folder_names = ("RESIDENT EVIL 2  BIOHAZARD RE2", "RESIDENT EVIL 2", "ResidentEvil2")
    required_files = ("re2.exe", "re_chunk_000.pak")
    folder_hint = "the folder that contains re2.exe"

    def __init__(self, game_path: Path | None = None, work_dir: Path | None = None):
        if game_path is None:
            game_path, _how = library.find_folder(type(self))
        self.game_path = game_path
        self.work_dir = work_dir or data_dir(self.id)

    @property
    def patch_pak_path(self) -> Path | None:
        if not self.game_path:
            return None
        return self.game_path / _PATCH_NAME

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
        if not self.game_path or not (self.game_path / "re_chunk_000.pak").exists():
            return InstallState.GAME_MISSING
        patch = self.patch_pak_path
        if patch and patch.exists():
            if patch.stat().st_size > 16:
                return InstallState.INSTALLED
            return InstallState.INTERRUPTED
        return InstallState.NOT_INSTALLED

    def _reader(self) -> PakReader:
        if not self.game_path:
            raise RuntimeError("Game folder not set")
        paks = sorted(self.game_path.glob("*.pak"))
        return PakReader(paks)

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
                e.texts[1].strip()
                for e in msg.entries
                if not is_timed_entry(e.texts[1]) and e.texts[1].strip()
            }

            for entry in msg.entries:
                eng = entry.texts[1].strip()
                if not eng or eng == "<END>" or eng.startswith("<COLOR FF0000>#Rejected#"):
                    continue

                rus_hint = entry.texts[6].strip() if msg.lang_count > 6 else ""

                if is_timed_entry(entry.texts[1]):
                    leading, times, pieces = split_timed_entry(entry.texts[1])
                    for i, (prefix, core, suffix) in enumerate(pieces):
                        core_clean = core.strip()
                        if core_clean and core_clean not in individual_texts:
                            lines.append(
                                SourceLine(
                                    table=path,
                                    key=f"{entry.name}__page__{i}",
                                    order=order,
                                    text=core_clean,
                                    hint=rus_hint,
                                    prefix=prefix,
                                    suffix=suffix,
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
                            hint=rus_hint,
                            prefix="",
                            suffix="",
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

        # Seed with common TLOU1 clusters to provide full Hindi alphabet & ligatures
        tlou1_pack = Path(__file__).resolve().parents[3] / "subtitles" / "tlou1" / "hindi.json"
        if tlou1_pack.exists():
            try:
                tdata = json.loads(tlou1_pack.read_text(encoding="utf-8"))
                for tbl in tdata.get("lines", {}).values():
                    all_texts.extend(tbl.values())
            except Exception:
                pass

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
                    if is_timed_entry(entry.texts[1]):
                        leading, times, pieces = split_timed_entry(entry.texts[1])
                        new_pieces: list[tuple[str, str, str]] = []
                        for i, (prefix, core, suffix) in enumerate(pieces):
                            piece_key = f"{entry.name}__page__{i}"
                            if piece_key in file_trans:
                                tr = file_trans[piece_key]
                            elif core.strip() in text_to_trans:
                                tr = text_to_trans[core.strip()]
                            else:
                                tr = core
                            encoded_core = encode_text(tr, mapping)
                            new_pieces.append((prefix, encoded_core, suffix))
                        slot_7[entry.name] = build_timed_entry(leading, new_pieces)
                    elif entry.name in file_trans:
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

        reader = self._reader()
        files = self.build_files(reader, translations, progress, hindi_scale)

        progress("Writing patch archive...")
        patch_file = self.patch_pak_path
        if not patch_file:
            raise RuntimeError("Invalid game patch destination")

        write_pak(patch_file, files)
        progress("Installed.")

    def uninstall(self, progress: ProgressFn = lambda _: None) -> None:
        self._ensure_closed()
        patch_file = self.patch_pak_path
        if patch_file and patch_file.exists():
            progress("Removing patch archive...")
            patch_file.unlink()
        progress("Removed.")
