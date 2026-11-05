"""Tests for Re2Adapter."""
from __future__ import annotations

from pathlib import Path
import pytest

from gamemod.adapters.base import InstallState
from gamemod.adapters.re2.adapter import Re2Adapter, _PATCH_NAME
from gamemod.adapters.re2.paths import HEALTHWARNING_PATH_PL, FONTS

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


@needs_re2
def test_re2_adapter_state_transitions(tmp_path: Path):
    fake_game = tmp_path / "game"
    fake_game.mkdir()
    adapter = Re2Adapter(fake_game)
    assert adapter.state() == InstallState.GAME_MISSING

    (fake_game / "re_chunk_000.pak").write_bytes(b"dummy")
    assert adapter.state() == InstallState.NOT_INSTALLED

    patch_file = fake_game / _PATCH_NAME
    patch_file.write_bytes(b"0" * 32)
    assert adapter.state() == InstallState.INSTALLED

    adapter.uninstall()
    assert not patch_file.exists()
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
