"""Every encoded Hindi character must resolve to a glyph drawn like the HarfBuzz reference."""
import io
from pathlib import Path

import pytest
import uharfbuzz as hb
from conftest import open_original_core
from fontTools.pens.recordingPen import RecordingPen
from fontTools.ttLib import TTFont

from gamemod.core.devanagari import (
    assign_codepoints,
    encode_text,
    find_broken_marks,
    iter_clusters,
    merge_into_font,
    unique_clusters,
)
from gamemod.core.devanagari.clusters import PUA_END, PUA_START, TooManyClusters

GAME = Path(r"D:\SteamLibrary\steamapps\common\The Last of Us Part I")
MAIN = GAME / "build" / "pc" / "main"
NEEDS_GAME = pytest.mark.skipif(not MAIN.exists(), reason="game not installed")

SAMPLE = "क्या तुम ठीक हो? ज़िंदगी कठिन है। विद्यार्थी क्षत्रिय श्रीकृष्ण हिंदी रूपया ट्रक कर्म पूँछ हूँ"


@pytest.mark.parametrize(
    "text,expected",
    [
        ("कि", ["कि"]),
        ("क्ष", ["क्ष"]),
        ("विद्यार्थी", ["वि", "द्या", "र्थी"]),
        ("हूँ", ["हूँ"]),
        ("श्रीकृष्ण", ["श्री", "कृ", "ष्ण"]),
        ("क्", ["क्"]),
        ("अंत", ["अं", "त"]),
        ("चलो।", ["च", "लो", "।"]),
        ("OK 12", []),
    ],
)
def test_cluster_splitting(text, expected):
    assert iter_clusters(text) == expected


def test_encoding_leaves_other_text_alone():
    mapping = assign_codepoints(unique_clusters(["चलो"]))
    encoded = encode_text('<font color="#fff">Joel, चलो 123!</font>', mapping)
    assert encoded.startswith('<font color="#fff">Joel, ')
    assert encoded.endswith(" 123!</font>")
    assert all(not 0x0900 <= ord(c) <= 0x097F for c in encoded)
    assert sum(PUA_START <= ord(c) <= PUA_END for c in encoded) == 2


def test_nfc_variants_share_one_cluster():
    composed = "\u095B"  # precomposed ज़
    decomposed = "\u091C\u093C"
    assert unique_clusters([composed, decomposed]) == [decomposed]


def test_broken_marks_are_detected():
    assert find_broken_marks(" ा") == ["ा"]
    assert find_broken_marks("चलो") == []


def test_too_many_clusters_is_an_error():
    with pytest.raises(TooManyClusters):
        assign_codepoints([chr(0x10000 + i) for i in range(PUA_END - PUA_START + 2)])


def test_assign_codepoints_with_skip():
    mapping = assign_codepoints(["क", "ख", "ग"], start=0xF000, skip=(0xF001, 0xF002))
    assert mapping["क"] == 0xF000
    assert mapping["ख"] == 0xF003
    assert mapping["ग"] == 0xF004



@NEEDS_GAME
@pytest.mark.parametrize(
    "name,weight",
    [("fonts/DINPro-Medium.otf", 500), ("fonts/HelveticaNeueBold.ttf", 700)],
)
def test_merged_font_contains_clusters_and_keeps_latin(name, weight):
    with open_original_core() as core:
        base = core.read(name)
    mapping = assign_codepoints(unique_clusters([SAMPLE]))
    merged = merge_into_font(base, mapping, weight)

    before, after = TTFont(io.BytesIO(base)), TTFont(io.BytesIO(merged))
    before_cmap, after_cmap = before.getBestCmap(), after.getBestCmap()
    for code in mapping.values():
        assert code in after_cmap
        assert after["hmtx"][after_cmap[code]][0] > 0
    # Latin must be untouched, or English text would shift.
    # Compare by glyph ID, since names are dropped from TrueType fonts to save space.
    for code, glyph in before_cmap.items():
        new_glyph = after_cmap[code]
        assert after.getGlyphID(new_glyph) == before.getGlyphID(glyph)
        assert after["hmtx"][new_glyph] == before["hmtx"][glyph]

    glyphs = after.getGlyphSet()
    for code in mapping.values():
        pen = RecordingPen()
        glyphs[after_cmap[code]].draw(pen)
        assert pen.value, "cluster glyph has no outline"


@NEEDS_GAME
def test_single_codepoint_render_matches_harfbuzz_advance():
    """A merged glyph, drawn with no shaping, has the width HarfBuzz gave the whole cluster."""
    with open_original_core() as core:
        base = core.read("fonts/DINPro-Medium.otf")
    mapping = assign_codepoints(unique_clusters([SAMPLE]))
    merged = merge_into_font(base, mapping, 500)

    face = hb.Face(hb.Blob(merged))
    font = hb.Font(face)
    for cluster, code in mapping.items():
        buffer = hb.Buffer()
        buffer.add_str(chr(code))
        buffer.guess_segment_properties()
        hb.shape(font, buffer)
        assert len(buffer.glyph_infos) == 1, f"{cluster} did not map to a single glyph"
        assert buffer.glyph_infos[0].codepoint != 0, f"{cluster} is missing from the font"
