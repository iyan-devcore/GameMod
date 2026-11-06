"""Tests for the Marvel's Spider-Man Remastered adapter, on a small fake game folder."""
from __future__ import annotations

from pathlib import Path

import pytest

from gamemod.adapters.base import InstallState
from gamemod.adapters.insomniac.archive import AssetReader, Toc, asset_id
from gamemod.adapters.insomniac.gfx import GfxFile
from gamemod.adapters.insomniac.localization import LOCALIZATION_PATH, Localization
from gamemod.adapters.msmr import lines as spoken
from gamemod.adapters.msmr.adapter import (
    ARCHIVE_NAME,
    DANISH_SPAN,
    ENGLISH_SPAN,
    FONT_PATH,
    FONT_WEIGHTS,
    RUSSIAN_SPAN,
    TABLE,
    MsmrAdapter,
)
from tests.test_insomniac import build_font_file, build_localization, build_toc

TS = "<ts=&quot;{};{}&quot;>"


def timed(*parts: str) -> str:
    return "".join(TS.format(i, i + 1) + part for i, part in enumerate(parts))


ENGLISH = [
    ("INVALID", ""),
    ("AUDIO_SUBTITLES", "SUBTITLES"),
    ("NAME_SUBTITLE_SPID", "<span class='emphasis'>Spider-Man</span>"),
    ("NAME_SUBTITLE_SAG1", "<span class='emphasis'>Sable Agent</span>"),
    ("SAG1_M_A1_TEST_002", timed("Stop &quot;right&quot; there!")),
    ("SPID_M_A1_TEST_001", timed("One down ... ", "lots to go.")),
    ("SPID_M_A1_TEST_003", "Untimed but spoken."),
    ("SPID_M_A1_TEST_004", ""),
    ("SPID_M_A1_TEST_005", timed("<exerts for HeavyInnerDemon_Intro>")),
    ("TWIT_POST_01", "Social media post, not spoken."),
]
RUSSIAN = [(key, text.replace("Stop", "Стой")) for key, text in ENGLISH]


def fake_game(root: Path) -> Path:
    game = root / "game"
    folder = game / "asset_archive"
    folder.mkdir(parents=True)
    (game / "Spider-Man.exe").write_bytes(b"")
    english = build_localization(ENGLISH)
    russian = build_localization(RUSSIAN)
    danish = build_localization([(key, "dansk") for key, _text in ENGLISH])
    font = build_font_file(list(FONT_WEIGHTS))
    blobs = [english, russian, danish, font]
    starts = [sum(len(b) for b in blobs[:i]) for i in range(len(blobs))]
    (folder / "g00s000").write_bytes(b"".join(blobs))
    loc = asset_id(LOCALIZATION_PATH)
    (folder / "toc").write_bytes(
        build_toc(
            ["g00s000"],
            {
                0: [(asset_id(FONT_PATH), 0, starts[3], len(font)), (loc, 0, starts[0], len(english))],
                ENGLISH_SPAN: [(loc, 0, starts[0], len(english))],
                DANISH_SPAN: [(loc, 0, starts[2], len(danish))],
                RUSSIAN_SPAN: [(loc, 0, starts[1], len(russian))],
            },
        )
    )
    return game


def danish_table(game: Path) -> dict[str, str]:
    folder = game / "asset_archive"
    with AssetReader(folder, Toc.read(folder / "toc")) as reader:
        return Localization(reader.read(LOCALIZATION_PATH, DANISH_SPAN)).as_dict()


HINDI = {
    TABLE: {
        "SPID_M_A1_TEST_001": "एक गया... [PAUSE] बहुत बाक़ी हैं।",
        "SAG1_M_A1_TEST_002": 'वहीं "रुको"!',
        "SPID_M_A1_TEST_003": "बोला गया।",
    }
}


# ---- spoken lines --------------------------------------------------------------------------

def test_spoken_line_round_trip():
    line = spoken.parse("<name=&quot;MNEG&quot;>" + timed("Lost ", "sight of &quot;him&quot;!"))
    assert line.speaker_override == "MNEG"
    assert spoken.for_translator(line) == 'Lost [PAUSE] sight of "him"!'
    rebuilt = spoken.rebuild(line, 'उसे [PAUSE] "खो" दिया!')
    assert rebuilt == "<name=&quot;MNEG&quot;>" + timed("उसे", "&quot;खो&quot; दिया!")
    assert spoken.rebuild(line, "बिना रुके") is None


def test_empty_parts_stay_empty():
    line = spoken.parse(timed("Hi.", "", "Bye."))
    assert spoken.for_translator(line) == "Hi. [PAUSE] Bye."
    assert spoken.rebuild(line, "हाय। [PAUSE] बाय।") == timed("हाय।", "", "बाय।")


def test_untimed_line_and_ampersand():
    line = spoken.parse("Tom & Jerry")
    assert spoken.rebuild(line, "टॉम & जेरी &quot;") == "टॉम &amp; जेरी &quot;"


# ---- adapter -------------------------------------------------------------------------------

def test_metadata_and_brief():
    adapter = MsmrAdapter(Path("dummy"), Path("work"))
    assert adapter.steam_app_id == 1817070
    assert "Danish" in adapter.where_to_enable
    assert "[PAUSE]" in adapter.brief().system_prompt


def test_extract_lines_finds_only_spoken_lines(tmp_path: Path):
    adapter = MsmrAdapter(fake_game(tmp_path), tmp_path / "work")
    found = {line.key: line for line in adapter.extract_lines()}
    assert set(found) == {"SPID_M_A1_TEST_001", "SAG1_M_A1_TEST_002", "SPID_M_A1_TEST_003"}
    assert found["SPID_M_A1_TEST_001"].text == "One down ... [PAUSE] lots to go."
    assert found["SAG1_M_A1_TEST_002"].text == 'Stop "right" there!'
    # Gender unknown, so the Russian line comes along for the translator.
    assert found["SAG1_M_A1_TEST_002"].hint == 'Sable Agent | Стой "right" there!'
    assert found["SPID_M_A1_TEST_003"].hint.startswith("Spider-Man (Peter Parker), male")
    orders = [found[k].order for k in ("SPID_M_A1_TEST_001", "SAG1_M_A1_TEST_002", "SPID_M_A1_TEST_003")]
    assert orders == sorted(orders)


def test_install_puts_hindi_in_the_danish_slot_only(tmp_path: Path):
    game = fake_game(tmp_path)
    original_toc = (game / "asset_archive" / "toc").read_bytes()
    adapter = MsmrAdapter(game, tmp_path / "work")
    assert adapter.state() is InstallState.NOT_INSTALLED
    adapter.install(HINDI)
    assert adapter.state() is InstallState.INSTALLED

    danish = danish_table(game)
    english = dict(ENGLISH)
    first = spoken.parse(danish["SPID_M_A1_TEST_001"])
    assert first.timings == spoken.parse(english["SPID_M_A1_TEST_001"]).timings
    assert all(0xE900 <= ord(c) <= 0xF6BF or c in " .।" for c in "".join(first.parts))
    assert "&quot;" in danish["SAG1_M_A1_TEST_002"]
    # Everything that is not a spoken line is the English, menus included.
    for key in ("AUDIO_SUBTITLES", "NAME_SUBTITLE_SPID", "TWIT_POST_01", "SPID_M_A1_TEST_004"):
        assert danish[key] == english[key]

    folder = game / "asset_archive"
    toc = Toc.read(folder / "toc")
    with AssetReader(folder, toc) as reader:
        assert Localization(reader.read(LOCALIZATION_PATH, ENGLISH_SPAN)).as_dict() == english
        fonts = {font.name: font for _i, font in GfxFile(reader.read(FONT_PATH)).fonts()}
    for name in FONT_WEIGHTS:
        assert max(g.code for g in fonts[name].glyphs) >= 0xE900

    # Installing again replaces the mod rather than stacking a second copy.
    adapter.install(HINDI)
    assert [a.name for a in Toc.read(folder / "toc").archives].count(ARCHIVE_NAME) == 1

    adapter.uninstall()
    assert (folder / "toc").read_bytes() == original_toc
    assert not (folder / ARCHIVE_NAME).exists()
    assert adapter.state() is InstallState.NOT_INSTALLED


def test_steam_restoring_the_toc_is_noticed_and_fixed(tmp_path: Path):
    game = fake_game(tmp_path)
    original_toc = (game / "asset_archive" / "toc").read_bytes()
    adapter = MsmrAdapter(game, tmp_path / "work")
    adapter.install(HINDI)
    (game / "asset_archive" / "toc").write_bytes(original_toc)
    assert adapter.state() is InstallState.GAME_UPDATED
    adapter.install(HINDI)
    assert adapter.state() is InstallState.INSTALLED
    adapter.uninstall()
    assert (game / "asset_archive" / "toc").read_bytes() == original_toc


def test_another_copy_of_gamemod_is_detected(tmp_path: Path):
    game = fake_game(tmp_path)
    MsmrAdapter(game, tmp_path / "one").install(HINDI)
    other = MsmrAdapter(game, tmp_path / "two")
    assert other.state() is InstallState.INSTALLED_ELSEWHERE
    with pytest.raises(RuntimeError):
        other.install(HINDI)


def test_lines_still_read_from_the_original_while_installed(tmp_path: Path):
    game = fake_game(tmp_path)
    adapter = MsmrAdapter(game, tmp_path / "work")
    before = adapter.extract_lines()
    adapter.install(HINDI)
    assert adapter.extract_lines() == before
