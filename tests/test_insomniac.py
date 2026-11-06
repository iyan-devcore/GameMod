"""Tests for the Insomniac engine readers and writers used by Marvel's Spider-Man Remastered."""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

import pytest
from fontTools.pens.recordingPen import RecordingPen

from gamemod.adapters.insomniac import archive as arc
from gamemod.adapters.insomniac.archive import AssetReader, Dat1, Toc, asset_id
from gamemod.adapters.insomniac.gfx import (
    DefineFont3,
    GfxFile,
    Glyph,
    Tag,
    _BitReader,
    _encode_shape,
    _rect,
    add_glyphs,
    make_glyph,
    outline_area_sign,
)
from gamemod.adapters.insomniac.localization import LOCALIZATION_PATH, Localization
from gamemod.adapters.insomniac.lz4 import decompress_block

GAME = Path(r"D:\SteamLibrary\steamapps\common\Marvel's Spider-Man Remastered")
needs_game = pytest.mark.skipif(not (GAME / "asset_archive" / "toc").exists(), reason="game not installed")


# ---- builders for small fake game files, shared with the adapter tests ----------------------

def build_dat1(type_hash: int, sections: list[tuple[int, bytes]], preamble: bytes = b"") -> bytes:
    count = len(sections)
    out = bytearray(16 + 12 * count) + preamble
    table = []
    for tag, data in sections:
        out += b"\0" * (-len(out) % 16)
        table.append((tag, len(out), len(data)))
        out += data
    struct.pack_into("<IIIHH", out, 0, arc.DAT1_MAGIC, type_hash, len(out), count, 0)
    for i, entry in enumerate(table):
        struct.pack_into("<III", out, 16 + 12 * i, *entry)
    return bytes(out)


def build_toc(archives: list[str], spans: dict[int, list[tuple[int, int, int, int]]]) -> bytes:
    """spans: span number -> [(asset id, archive index, position, size)]."""
    ids, sizes, offsets, span_table = [], [], [], []
    for span in range(256):
        entries = sorted(spans.get(span, []))
        span_table.append((len(ids), len(entries)))
        for asset, archive, position, size in entries:
            sizes += [1, size, len(ids)]
            ids.append(asset)
            offsets += [archive, position]
    sections = [
        (arc.SECTION_SPANS, b"".join(struct.pack("<II", *s) for s in span_table)),
        (arc.SECTION_IDS, struct.pack(f"<{len(ids)}Q", *ids)),
        (arc.SECTION_SIZES, struct.pack(f"<{len(sizes)}I", *sizes)),
        (arc.SECTION_OFFSETS, struct.pack(f"<{len(offsets)}I", *offsets)),
        (arc.SECTION_ARCHIVES, b"".join(struct.pack("<II64s", 0, 10000 + i, n.encode()) for i, n in enumerate(archives))),
    ]
    body = build_dat1(0x51B8E006, sections)
    return struct.pack("<II", arc.TOC_MAGIC, len(body)) + zlib.compress(body)


def build_localization(entries: list[tuple[str, str]]) -> bytes:
    keys_blob, texts_blob = bytearray(), bytearray(b"\0")
    key_offsets, text_offsets = [], []
    for key, text in entries:
        key_offsets.append(len(keys_blob))
        keys_blob += key.encode() + b"\0"
        if text:
            text_offsets.append(len(texts_blob))
            texts_blob += text.encode() + b"\0"
        else:
            text_offsets.append(0)
    count = len(entries)
    pack = lambda values: struct.pack(f"<{count}I", *values)
    sections = [
        (0xD540A903, struct.pack("<I", count)),
        (0x06A58050, pack(range(count))),
        (0xA4EA55B2, pack(key_offsets)),
        (0xF80DEEB4, pack(text_offsets)),
        (0xB0653243, pack([0] * count)),
        (0x4D73CEBD, bytes(keys_blob)),
        (0x70A382B8, bytes(texts_blob)),
    ]
    body = build_dat1(0x122BB0AB, sections, preamble=b"Localization Built File\0")
    return struct.pack("<II", 0x122BB0AB, len(body)) + b"\0" * 28 + body


def square(size: int = 1000) -> RecordingPen:
    pen = RecordingPen()
    pen.moveTo((100, 0))
    pen.lineTo((100, size))
    pen.lineTo((100 + size, size))
    pen.lineTo((100 + size, 0))
    pen.closePath()
    return pen


def build_font_file(names: list[bytes]) -> bytes:
    """A GFx movie with one DefineFont3 per name, each holding only the letter O."""
    tags = [Tag(69, b"\x08\x00\x00\x00", False)]
    for font_id, name in enumerate(names, 1):
        letter = make_glyph(ord("O"), square(10000), 12000, 1)
        font = DefineFont3(font_id, 0x8C, 1, name, [letter], 21000, 7000, 0, b"\0\0")
        tags.append(Tag(75, font.to_bytes(), True))
    tags.append(Tag(1, b"", False))
    tags.append(Tag(0, b"", False))
    header = _rect(0, 20000, 0, 20000) + b"\x00\x1e\x01\x00"
    body = bytearray(header)
    for tag in tags:
        if tag.long_header:
            body += struct.pack("<HI", (tag.code << 6) | 0x3F, len(tag.body))
        else:
            body += struct.pack("<H", (tag.code << 6) | len(tag.body))
        body += tag.body
    return b"GFX\x0e" + struct.pack("<I", 8 + len(body)) + bytes(body)


# ---- tests ---------------------------------------------------------------------------------

def test_asset_id_matches_the_game():
    assert asset_id(LOCALIZATION_PATH) == 0xBE55D94F171BF8DE
    assert asset_id("LOCALIZATION\\localization_all.localization") == asset_id(LOCALIZATION_PATH)


def test_lz4_block_with_overlapping_copy():
    block = b"\x35abc\x03\x00\x10!"
    assert decompress_block(block, 13) == b"abcabcabcabc!"
    with pytest.raises(ValueError):
        decompress_block(block, 14)


def test_dat1_keeps_preamble_and_alignment():
    raw = build_dat1(7, [(1, b"abc"), (2, b"defgh")], preamble=b"name\0")
    dat1 = Dat1(raw)
    assert dat1.to_bytes() == raw
    dat1.sections[1] = b"abcdefghijklmnopq"
    again = Dat1(dat1.to_bytes())
    assert again.sections == {1: b"abcdefghijklmnopq", 2: b"defgh"}
    assert again.preamble.rstrip(b"\0") == b"name"  # padding up to the first section comes along


def test_toc_find_point_and_add_archive():
    toc = Toc(build_toc(["g00s000"], {0: [(5, 0, 0, 10), (9, 0, 10, 4)], 24: [(9, 0, 20, 3)]}))
    assert toc.find(9, 0) == 1 and toc.find(9, 24) == 2 and toc.find(9, 8) is None
    index = toc.add_archive("gamemod_hindi")
    assert index == 1 and toc.add_archive("gamemod_hindi") == 1
    toc.point(2, index, 0, 99)
    again = Toc(toc.to_bytes())
    assert [a.name for a in again.archives] == ["g00s000", "gamemod_hindi"]
    assert again.archives[1].chunk == 10001
    assert again.location(2) == (1, 0, 99) and again.location(1) == (0, 10, 4)


def test_asset_reader_reads_plain_archives(tmp_path: Path):
    (tmp_path / "plain").write_bytes(b"0123456789abcdef")
    toc = Toc(build_toc(["plain"], {0: [(asset_id("a/b.c"), 0, 4, 6)]}))
    with AssetReader(tmp_path, toc) as reader:
        assert reader.read("a/b.c") == b"456789"
        with pytest.raises(KeyError):
            reader.read("missing")


def test_localization_rebuild_keeps_untouched_entries():
    raw = build_localization([("INVALID", ""), ("A", "one"), ("B", "two"), ("C", "one")])
    table = Localization(raw)
    assert table.as_dict() == {"INVALID": "", "A": "one", "B": "two", "C": "one"}
    assert table.rebuild({}) == raw
    changed = Localization(table.rebuild({"B": "दो", "C": "दो"}))
    assert changed.as_dict() == {"INVALID": "", "A": "one", "B": "दो", "C": "दो"}
    with pytest.raises(KeyError):
        table.rebuild({"Z": "x"})


def test_shapes_round_trip_through_bits():
    contours = [[("move", (0, 0)), ("line", (0, -100)), ("quad", (50, -150), (100, -100)), ("line", (100, 0)), ("line", (0, 0))]]
    shape, bounds = _encode_shape(contours)
    assert bounds == (0, 100, -150, 0)
    reader = _BitReader(shape)
    assert (reader.unsigned(4), reader.unsigned(4)) == (1, 0)


def test_new_glyphs_are_added_in_code_order():
    names = [b"Azbuka Pro Medium\x00", b"Azbuka Pro Bold\x00"]
    raw = build_font_file(names)
    assert GfxFile(raw).to_bytes() == raw
    fonts = dict((font.name, font) for _i, font in GfxFile(raw).fonts())
    direction = outline_area_sign(fonts[names[0]].glyphs[0].shape)
    # Two overlapping squares become one outline, so Flash fills it without a hole.
    overlap = square(1000)
    overlap.moveTo((600, 0))
    overlap.lineTo((600, 1000))
    overlap.lineTo((1600, 1000))
    overlap.lineTo((1600, 0))
    overlap.closePath()
    glyph = make_glyph(0xE900, overlap, 1800, direction)
    assert outline_area_sign(glyph.shape) == direction
    out = add_glyphs(raw, {names[0]: [glyph]})
    medium = dict((font.name, font) for _i, font in GfxFile(out).fonts())[names[0]]
    assert [g.code for g in medium.glyphs] == [ord("O"), 0xE900]
    with pytest.raises(ValueError):
        add_glyphs(out, {names[0]: [glyph]})
    with pytest.raises(KeyError):
        add_glyphs(raw, {b"Missing\x00": [glyph]})


def test_round_dot_with_no_on_curve_points():
    dot = RecordingPen()
    dot.qCurveTo((0, 100), (100, 100), (100, 0), (0, 0), None)
    dot.closePath()
    glyph = make_glyph(0xE902, dot, 200, 1)
    assert outline_area_sign(glyph.shape) == 1


def test_very_wide_glyph_is_narrowed_to_fit():
    glyph = make_glyph(0xE901, square(1000), 40000, 1)
    assert glyph.advance == 32767


@needs_game
def test_real_toc_and_text_table_round_trip():
    raw = (GAME / "asset_archive" / "toc").read_bytes()
    toc = Toc(raw)
    assert zlib.decompress(toc.to_bytes()[8:]) == zlib.decompressobj().decompress(raw[8:])
    if toc.archive_index("gamemod_hindi") is not None:
        pytest.skip("the mod is installed")
    with AssetReader(GAME / "asset_archive", toc) as reader:
        english = reader.read(LOCALIZATION_PATH, 8)
        assert Localization(english).rebuild({}) == english
        assert Localization(reader.read(LOCALIZATION_PATH, 24)).keys == Localization(english).keys
        font = reader.read("ui/export/fonts/Font_LatinAS3.gfx")
        assert GfxFile(font).to_bytes() == font
