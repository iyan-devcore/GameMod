"""Add pre-shaped Hindi clusters to an existing game font."""
from __future__ import annotations

import io
from dataclasses import dataclass
from functools import lru_cache
from typing import Mapping

import uharfbuzz as hb
from fontTools.misc.roundTools import otRound
from fontTools.pens.boundsPen import ControlBoundsPen
from fontTools.pens.recordingPen import RecordingPen
from fontTools.pens.reverseContourPen import ReverseContourPen
from fontTools.pens.t2CharStringPen import T2CharStringPen
from fontTools.pens.transformPen import TransformPen
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont

from gamemod.core.paths import asset

NOTO_PATH = asset("fonts", "NotoSansDevanagari-VF.ttf")


@dataclass(frozen=True)
class FontStyle:
    # Devanagari looks smaller than Latin at the same size.
    scale: float = 1.12
    y_shift: int = 0
    # Slightly negative so the headline bar stays joined after pixel rounding.
    tracking: int = -10


class _Shaper:
    def __init__(self, weight: int):
        self._face = hb.Face(hb.Blob.from_file_path(str(NOTO_PATH)))
        self._font = hb.Font(self._face)
        self._font.set_variations({"wght": weight, "wdth": 100})
        self.upem = self._face.upem

    def outline(self, cluster: str, factor: float, y_shift: int) -> tuple[RecordingPen, int]:
        buffer = hb.Buffer()
        buffer.add_str(cluster)
        buffer.direction = "ltr"
        buffer.script = "deva"
        buffer.language = "hi"
        hb.shape(self._font, buffer)

        recording = RecordingPen()
        pen_x = 0
        for info, pos in zip(buffer.glyph_infos, buffer.glyph_positions):
            transform = (
                factor, 0, 0, factor,
                (pen_x + pos.x_offset) * factor,
                pos.y_offset * factor + y_shift,
            )
            self._font.draw_glyph_with_pen(info.codepoint, TransformPen(recording, transform))
            pen_x += pos.x_advance
        return recording, otRound(pen_x * factor)


@lru_cache(maxsize=None)
def _shaper(weight: int) -> _Shaper:
    return _Shaper(weight)


def cluster_outlines(
    mapping: Mapping[str, int],
    weight: int,
    units_per_em: int,
    style: FontStyle = FontStyle(),
):
    """Yield (code, outline, advance) for each cluster, drawn for a font of the given em size.

    The outline is in that font's units with y pointing up, for engines whose fonts are not
    TrueType or OpenType files. `style.tracking` is per 1000 units of em.
    """
    shaper = _shaper(weight)
    factor = style.scale * units_per_em / shaper.upem
    y_shift = style.y_shift * units_per_em / 1000
    tracking = style.tracking * units_per_em / 1000
    for cluster, code in mapping.items():
        recording, advance = shaper.outline(cluster, factor, y_shift)
        yield code, recording, max(0, otRound(advance + tracking))


def merge_into_font(
    font_bytes: bytes,
    mapping: Mapping[str, int],
    weight: int,
    style: FontStyle = FontStyle(),
) -> bytes:
    """Return a copy of the font that also contains every cluster in `mapping`."""
    font = TTFont(io.BytesIO(font_bytes), recalcTimestamp=False)
    shaper = _shaper(weight)
    factor = style.scale * font["head"].unitsPerEm / shaper.upem
    hmtx = font["hmtx"]
    order = list(font.getGlyphOrder())
    is_cff = "CFF " in font

    if is_cff:
        cff = font["CFF "].cff
        top = cff.topDictIndex[0]
        char_strings = top.CharStrings
        private = top.Private
        nominal = getattr(private, "nominalWidthX", 0)
    else:
        glyf = font["glyf"]

    new_codes: dict[int, str] = {}
    for cluster, code in mapping.items():
        name = f"uni{code:04X}"
        recording, advance = shaper.outline(cluster, factor, style.y_shift)
        advance = max(0, advance + style.tracking)

        bounds = ControlBoundsPen(None)
        recording.replay(bounds)
        lsb = otRound(bounds.bounds[0]) if bounds.bounds else 0

        if is_cff:
            # CFF outlines run the opposite way to TrueType.
            pen = T2CharStringPen(advance - nominal, None)
            recording.replay(ReverseContourPen(pen))
            charstring = pen.getCharString(private=private, globalSubrs=cff.GlobalSubrs)
            char_strings.charStringsIndex.append(charstring)
            char_strings.charStrings[name] = len(char_strings.charStringsIndex) - 1
            top.charset.append(name)
        else:
            pen = TTGlyphPen(None)
            recording.replay(pen)
            glyf[name] = pen.glyph()

        hmtx[name] = (advance, lsb)
        order.append(name)
        new_codes[code] = name

    font.setGlyphOrder(order)
    if not is_cff:
        font["post"].formatType = 3.0  # glyph names are not needed and would add size

    # A game may read either character table, so every Unicode one gets the new letters.
    for table in font["cmap"].tables:
        if table.format in (4, 12):
            table.cmap.update(new_codes)

    if "DSIG" in font:
        del font["DSIG"]

    out = io.BytesIO()
    font.save(out)
    return out.getvalue()
