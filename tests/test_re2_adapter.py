"""Tests for Re2Adapter."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from gamemod.adapters.base import InstallState
from gamemod.adapters.re2.adapter import Re2Adapter, _scene_entry
from gamemod.core.devanagari import unique_clusters
from gamemod.adapters.re_engine.pak import write_pak

PATCH_1 = "re_chunk_000.pak.patch_001.pak"
PATCH_2 = "re_chunk_000.pak.patch_002.pak"
HINDI = {"natives/a.txt": b"hindi"}

RE2_GAME = Path(r"D:\SteamLibrary\steamapps\common\RESIDENT EVIL 2  BIOHAZARD RE2")
needs_re2 = pytest.mark.skipif(not RE2_GAME.exists(), reason="RE2 game not installed")


def test_re2_adapter_metadata():
    adapter = Re2Adapter(Path("dummy/path"))
    assert adapter.id == "re2"
    assert adapter.steam_app_id == 883710
    assert "Polish [pl]" in adapter.where_to_enable
    assert "re2.exe" in adapter.required_files
    assert "re_chunk_000.pak" in adapter.required_files


def test_re2_adapter_brief():
    adapter = Re2Adapter(Path("dummy/path"))
    b = adapter.brief()
    assert "Resident Evil 2" in b.system_prompt
    assert "लियोन" in b.fixed.values()
    assert "क्लेयर" in b.fixed.values()


def _fake_game(tmp_path: Path, work: str = "work") -> Re2Adapter:
    game = tmp_path / "game"
    game.mkdir(exist_ok=True)
    write_pak(game / "re_chunk_000.pak", {"natives/a.txt": b"original"})
    return Re2Adapter(game, tmp_path / work)


def test_missing_game_is_reported(tmp_path: Path):
    assert Re2Adapter(tmp_path / "nothing", tmp_path / "work").state() == InstallState.GAME_MISSING


def test_empty_patch_is_used_and_put_back_empty(tmp_path: Path):
    adapter = _fake_game(tmp_path)
    slot = adapter.game_path / PATCH_1
    slot.write_bytes(b"")
    os.utime(slot, (1_000_000, 1_000_000))
    assert adapter.state() == InstallState.NOT_INSTALLED

    adapter._write_patch(HINDI)
    assert slot.stat().st_size > 0
    assert adapter.state() == InstallState.INSTALLED

    adapter.uninstall()
    assert slot.stat().st_size == 0
    assert slot.stat().st_mtime == 1_000_000
    assert adapter.state() == InstallState.NOT_INSTALLED


def test_files_with_content_are_never_replaced(tmp_path: Path):
    adapter = _fake_game(tmp_path)
    existing = adapter.game_path / PATCH_1
    write_pak(existing, {"natives/b.txt": b"someone else's mod"})
    before = existing.read_bytes()

    adapter._write_patch(HINDI)
    assert existing.read_bytes() == before
    assert (adapter.game_path / PATCH_2).exists()

    adapter.uninstall()
    assert existing.read_bytes() == before
    assert not (adapter.game_path / PATCH_2).exists()


def test_a_file_that_replaced_ours_is_not_removed(tmp_path: Path):
    adapter = _fake_game(tmp_path)
    adapter._write_patch(HINDI)
    slot = adapter.game_path / PATCH_1
    write_pak(slot, {"natives/c.txt": b"a Steam update"})
    assert adapter.state() == InstallState.GAME_UPDATED

    adapter.uninstall()
    assert slot.stat().st_size > 0


def test_steam_emptying_our_patch_shows_as_updated(tmp_path: Path):
    adapter = _fake_game(tmp_path)
    adapter._write_patch(HINDI)
    (adapter.game_path / PATCH_1).write_bytes(b"")
    assert adapter.state() == InstallState.GAME_UPDATED


def test_our_patch_is_never_read_as_the_original(tmp_path: Path):
    adapter = _fake_game(tmp_path)
    adapter._write_patch(HINDI)
    assert adapter._reader().get("natives/a.txt") == b"original"


def test_another_copy_of_gamemod_sees_the_patch(tmp_path: Path):
    _fake_game(tmp_path)._write_patch(HINDI)
    assert _fake_game(tmp_path, "other").state() == InstallState.INSTALLED_ELSEWHERE


def test_interrupted_write_is_cleaned_up(tmp_path: Path):
    adapter = _fake_game(tmp_path)
    leftover = adapter.game_path / (PATCH_1 + ".gamemod.tmp")
    leftover.write_bytes(b"partial")
    assert adapter.state() == InstallState.INTERRUPTED
    adapter.uninstall()
    assert not leftover.exists()
    assert adapter.state() == InstallState.NOT_INSTALLED


@needs_re2
def test_re2_extract_lines():
    adapter = Re2Adapter(RE2_GAME)
    lines = adapter.extract_lines()
    assert len(lines) > 1500
    for line in lines[:50]:
        assert line.text
        assert "<END>" not in line.text
        assert "#Rejected#" not in line.text
        assert line.table.endswith(".msg.50397457")


@needs_re2
def test_re2_lines_are_ready_for_batch_files():
    lines = Re2Adapter(RE2_GAME).extract_lines()
    assert not [line.key for line in lines if "\r" in line.text or "\r" in line.hint]
    # Spacing around a page is restored when building, so storing it would double it.
    assert not [line.key for line in lines if line.prefix or line.suffix]
    assert not [line.key for line in lines if "<TIME" in line.text]
    by_key = {line.key: line for line in lines}
    assert by_key["CF001_2010_pl1000"].hint.startswith("Claire, female | ")
    assert by_key["CF001_0010_pl0000"].hint.startswith("Leon, male | ")
    assert any("\n" in line.text for line in lines)


def test_scene_entry_uses_each_pages_own_translation():
    mapping = {cluster: 0xE900 + i for i, cluster in enumerate(unique_clusters(["हाँ", "ना"]))}
    trans = {"Yes.": "हाँ", "No.": "ना"}
    timed = _scene_entry("<TIME 1, 2><TIME 3, 4>Yes.<PAGE>\r\nNo.", "X", {}, trans, mapping)
    assert timed.startswith("<TIME 1, 2><TIME 3, 4>")
    assert timed.count("<PAGE>\r\n") == 1
    assert "Yes" not in timed and "No" not in timed
    pages_only = _scene_entry("Yes.<PAGE>\r\nNo.", "X", {}, trans, mapping)
    assert pages_only.count("<PAGE>\r\n") == 1 and "Yes" not in pages_only
    assert _scene_entry("Yes.<PAGE>\r\nMaybe.", "X", {}, trans, mapping) is None


def test_command_line_knows_every_game():
    from gamemod.cli import GAMES

    assert GAMES["re2"] is Re2Adapter
    assert GAMES["tlou1"].id == "tlou1"

