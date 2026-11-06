"""Add glyphs to the Scaleform (GFx) font files the game's menus and subtitles use.

A .gfx file is a Flash movie. Each font is a DefineFont3 tag: glyph outlines as Flash shapes
at 20 units per point of a 1024-unit em, a sorted table of character codes, advances, bounds
and kerning. Scaleform draws the outlines itself and has no text shaping, so every Hindi
syllable is drawn ahead of time as one glyph (see gamemod.core.devanagari).

Flash fills each outline by which side of an edge is inside, so outlines that overlap leave
holes or stray fill. Pre-shaped syllables overlap where a vowel sign meets the headline, so
their outlines are merged into one non-overlapping shape first.
"""
from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field
from typing import Iterable, Mapping

import pathops
from fontTools.cu2qu import curve_to_quadratic
from fontTools.pens.recordingPen import RecordingPen

TAG_DEFINE_FONT3 = 75
EM = 1024 * 20  # DefineFont3 units per em
_MAX_EDGE_BITS = 17  # an edge delta must fit in 17 signed bits
_MAX_ADVANCE = 32767


class _BitReader:
    def __init__(self, data: bytes, position: int = 0):
        self.data = data
        self.bit = position * 8

    def unsigned(self, count: int) -> int:
        value = 0
        for _ in range(count):
            value = (value << 1) | ((self.data[self.bit >> 3] >> (7 - (self.bit & 7))) & 1)
            self.bit += 1
        return value

    def signed(self, count: int) -> int:
        value = self.unsigned(count)
        return value - (1 << count) if count and value >> (count - 1) else value

    def align(self) -> int:
        self.bit = (self.bit + 7) // 8 * 8
        return self.bit // 8


class _BitWriter:
    def __init__(self):
        self.out = bytearray()
        self.current = 0
        self.used = 0

    def unsigned(self, value: int, count: int) -> None:
        for shift in range(count - 1, -1, -1):
            self.current = (self.current << 1) | ((value >> shift) & 1)
            self.used += 1
            if self.used == 8:
                self.out.append(self.current)
                self.current = self.used = 0

    def signed(self, value: int, count: int) -> None:
        self.unsigned(value & ((1 << count) - 1), count)

    def finish(self) -> bytes:
        if self.used:
            self.out.append(self.current << (8 - self.used))
            self.current = self.used = 0
        return bytes(self.out)


def _bits_signed(*values: int) -> int:
    return max(max(abs(v) for v in values).bit_length() + 1, 1) if values else 1


def _rect(xmin: int, xmax: int, ymin: int, ymax: int) -> bytes:
    bits = _bits_signed(xmin, xmax, ymin, ymax)
    writer = _BitWriter()
    writer.unsigned(bits, 5)
    for value in (xmin, xmax, ymin, ymax):
        writer.signed(value, bits)
    return writer.finish()


def _rect_length(data: bytes, position: int) -> int:
    reader = _BitReader(data, position)
    bits = reader.unsigned(5)
    reader.unsigned(bits * 4)
    return reader.align() - position


@dataclass
class Glyph:
    shape: bytes  # a complete SHAPE record
    code: int
    advance: int
    bounds: bytes  # a RECT record


@dataclass
class DefineFont3:
    font_id: int
    flags: int
    language: int
    name: bytes
    glyphs: list[Glyph]
    ascent: int
    descent: int
    leading: int
    kerning: bytes  # count and records, kept as they are
    has_layout: bool = field(default=True)

    @classmethod
    def parse(cls, body: bytes) -> "DefineFont3":
        font_id, flags, language, name_length = struct.unpack_from("<HBBB", body)
        name = body[5 : 5 + name_length]
        position = 5 + name_length
        count = struct.unpack_from("<H", body, position)[0]
        position += 2
        wide = bool(flags & 0x08)
        if not flags & 0x04:
            raise ValueError("font without wide codes")
        offsets = struct.unpack_from(f"<{count + 1}{'I' if wide else 'H'}", body, position)
        table = position
        shapes = [body[table + offsets[i] : table + offsets[i + 1]] for i in range(count)]
        position = table + offsets[count]
        codes = struct.unpack_from(f"<{count}H", body, position)
        position += 2 * count
        has_layout = bool(flags & 0x80)
        if not has_layout:
            raise ValueError("font without layout data")
        ascent, descent, leading = struct.unpack_from("<HHh", body, position)
        position += 6
        advances = struct.unpack_from(f"<{count}h", body, position)
        position += 2 * count
        bounds = []
        for _ in range(count):
            length = _rect_length(body, position)
            bounds.append(body[position : position + length])
            position += length
        kerning = body[position:]
        glyphs = [Glyph(shapes[i], codes[i], advances[i], bounds[i]) for i in range(count)]
        return cls(font_id, flags, language, name, glyphs, ascent, descent, leading, kerning)

    def to_bytes(self) -> bytes:
        glyphs = sorted(self.glyphs, key=lambda glyph: glyph.code)
        count = len(glyphs)
        flags = self.flags | 0x08  # 32-bit offsets, since added glyphs can pass 64 KB
        out = bytearray(struct.pack("<HBBB", self.font_id, flags, self.language, len(self.name)))
        out += self.name + struct.pack("<H", count)
        offset = 4 * (count + 1)
        offsets = []
        for glyph in glyphs:
            offsets.append(offset)
            offset += len(glyph.shape)
        offsets.append(offset)
        out += struct.pack(f"<{count + 1}I", *offsets)
        for glyph in glyphs:
            out += glyph.shape
        out += struct.pack(f"<{count}H", *(glyph.code for glyph in glyphs))
        out += struct.pack("<HHh", self.ascent, self.descent, self.leading)
        out += struct.pack(f"<{count}h", *(glyph.advance for glyph in glyphs))
        for glyph in glyphs:
            out += glyph.bounds
        out += self.kerning
        return bytes(out)

    @property
    def codes(self) -> set[int]:
        return {glyph.code for glyph in self.glyphs}


@dataclass
class Tag:
    code: int
    body: bytes
    long_header: bool = True


class GfxFile:
    """A Flash or GFx movie as a list of tags. Only uncompressed files are supported."""

    def __init__(self, data: bytes):
        if data[:3] not in (b"GFX", b"FWS"):
            raise ValueError("not an uncompressed GFx or SWF file")
        self.signature = data[:4]
        bits = data[8] >> 3
        header_end = 8 + (5 + 4 * bits + 7) // 8 + 4
        self.header = data[8:header_end]
        self.tags: list[Tag] = []
        position = header_end
        while position < len(data):
            code_and_length = struct.unpack_from("<H", data, position)[0]
            code, length = code_and_length >> 6, code_and_length & 0x3F
            position += 2
            long_header = length == 0x3F
            if long_header:
                length = struct.unpack_from("<I", data, position)[0]
                position += 4
            self.tags.append(Tag(code, data[position : position + length], long_header))
            position += length
            if code == 0:
                break

    def fonts(self) -> list[tuple[int, DefineFont3]]:
        return [
            (i, DefineFont3.parse(tag.body))
            for i, tag in enumerate(self.tags)
            if tag.code == TAG_DEFINE_FONT3
        ]

    def to_bytes(self) -> bytes:
        body = bytearray(self.header)
        for tag in self.tags:
            if not tag.long_header and len(tag.body) < 0x3F:
                body += struct.pack("<H", (tag.code << 6) | len(tag.body))
            else:
                body += struct.pack("<HI", (tag.code << 6) | 0x3F, len(tag.body))
            body += tag.body
        return self.signature + struct.pack("<I", 8 + len(body)) + bytes(body)


# ---- Turning outlines into Flash shapes ----------------------------------------------------

def _merged_contours(recording: RecordingPen) -> list[list[tuple]]:
    """Outlines with overlaps removed, as lists of ("move"|"line"|"quad", points...)."""
    path = pathops.Path()
    recording.replay(path.getPen())
    try:
        path.simplify(fix_winding=True, keep_starting_points=False)
    except pathops.PathOpsError:
        pass  # keep the original outline rather than lose the glyph
    merged = RecordingPen()
    path.draw(merged)

    contours: list[list[tuple]] = []
    current: list[tuple] = []
    last = (0.0, 0.0)
    start = (0.0, 0.0)
    for operator, points in merged.value:
        if operator == "moveTo":
            current = [("move", points[0])]
            contours.append(current)
            last = start = points[0]
        elif operator == "lineTo":
            current.append(("line", points[0]))
            last = points[0]
        elif operator == "qCurveTo":
            # A run of off-curve points with implied on-curve points between them.
            *controls, end = points
            if end is None:
                # A closed contour with no on-curve point at all, such as a round dot. It
                # starts and ends halfway between its last and first off-curve points.
                end = _midpoint(controls[-1], controls[0])
                current = [("move", end)]
                contours.append(current)
                start = end
            for i, control in enumerate(controls):
                if i + 1 < len(controls):
                    following = controls[i + 1]
                    anchor = ((control[0] + following[0]) / 2, (control[1] + following[1]) / 2)
                else:
                    anchor = end
                current.append(("quad", control, anchor))
            last = end
        elif operator == "curveTo":
            quadratic = curve_to_quadratic([last, *points], 1.0)
            for i in range(1, len(quadratic) - 1):
                control = quadratic[i]
                if i + 1 < len(quadratic) - 1:
                    following = quadratic[i + 1]
                    anchor = ((control[0] + following[0]) / 2, (control[1] + following[1]) / 2)
                else:
                    anchor = quadratic[-1]
                current.append(("quad", control, anchor))
            last = points[-1]
        elif operator in ("closePath", "endPath"):
            if last != start:
                current.append(("line", start))
            last = start
    return [contour for contour in contours if len(contour) > 2]


def _midpoint(a: tuple[float, float], b: tuple[float, float]) -> tuple[float, float]:
    return ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)


def _signed_area(points: list[tuple[float, float]]) -> float:
    return sum(
        points[i][0] * points[(i + 1) % len(points)][1] - points[(i + 1) % len(points)][0] * points[i][1]
        for i in range(len(points))
    ) / 2


def _nodes(contour: list[tuple]) -> list[tuple[float, float]]:
    return [contour[0][1]] + [segment[-1] for segment in contour[1:]]


def _reverse(contour: list[tuple]) -> list[tuple]:
    """The same outline drawn the other way round."""
    nodes = _nodes(contour)
    reversed_contour = [("move", nodes[-1])]
    for i in range(len(contour) - 1, 0, -1):
        segment = contour[i]
        if segment[0] == "quad":
            reversed_contour.append(("quad", segment[1], nodes[i - 1]))
        else:
            reversed_contour.append(("line", nodes[i - 1]))
    return reversed_contour


def outline_area_sign(shape: bytes) -> int:
    """+1 or -1: the direction the game's own glyphs draw their largest outline."""
    reader = _BitReader(shape)
    fill_bits, line_bits = reader.unsigned(4), reader.unsigned(4)
    contours: list[list[tuple[int, int]]] = []
    x = y = 0
    while True:
        if reader.unsigned(1) == 0:
            flags = reader.unsigned(5)
            if flags == 0:
                break
            if flags & 1:
                bits = reader.unsigned(5)
                x, y = reader.signed(bits), reader.signed(bits)
                contours.append([(x, y)])
            if flags & 2:
                reader.unsigned(fill_bits)
            if flags & 4:
                reader.unsigned(fill_bits)
            if flags & 8:
                reader.unsigned(line_bits)
            if flags & 16:
                raise ValueError("glyph defines new styles")
        elif reader.unsigned(1):
            bits = reader.unsigned(4) + 2
            if reader.unsigned(1):
                x += reader.signed(bits)
                y += reader.signed(bits)
            elif reader.unsigned(1):
                y += reader.signed(bits)
            else:
                x += reader.signed(bits)
            contours[-1].append((x, y))
        else:
            bits = reader.unsigned(4) + 2
            cx, cy = x + reader.signed(bits), y + reader.signed(bits)
            x, y = cx + reader.signed(bits), cy + reader.signed(bits)
            contours[-1].append((x, y))
    biggest = max(contours, key=lambda points: abs(_signed_area(points)))
    return 1 if _signed_area(biggest) > 0 else -1


def _encode_shape(contours: list[list[tuple]]) -> tuple[bytes, tuple[int, int, int, int]]:
    """A SHAPE record with fill style 1 on the left of each edge, and its bounds."""
    writer = _BitWriter()
    writer.unsigned(1, 4)  # fill style bits
    writer.unsigned(0, 4)  # line style bits
    xs: list[int] = []
    ys: list[int] = []
    first = True
    for contour in contours:
        x, y = (round(v) for v in contour[0][1])
        writer.unsigned(0, 1)
        writer.unsigned(0b00011 if first else 0b00001, 5)
        bits = max(_bits_signed(x, y), 1)
        writer.unsigned(bits, 5)
        writer.signed(x, bits)
        writer.signed(y, bits)
        if first:
            writer.unsigned(1, 1)
            first = False
        xs.append(x)
        ys.append(y)
        for segment in contour[1:]:
            if segment[0] == "line":
                tx, ty = (round(v) for v in segment[1])
                _straight(writer, tx - x, ty - y)
                x, y = tx, ty
            else:
                cx, cy = (round(v) for v in segment[1])
                tx, ty = (round(v) for v in segment[2])
                _curve(writer, cx - x, cy - y, tx - cx, ty - cy)
                xs.append(cx)
                ys.append(cy)
                x, y = tx, ty
            xs.append(x)
            ys.append(y)
    writer.unsigned(0, 6)  # end of shape
    bounds = (min(xs), max(xs), min(ys), max(ys)) if xs else (0, 0, 0, 0)
    return writer.finish(), bounds


def _straight(writer: _BitWriter, dx: int, dy: int) -> None:
    if dx == 0 and dy == 0:
        return
    if max(abs(dx), abs(dy)).bit_length() + 1 > _MAX_EDGE_BITS:
        half_x, half_y = dx // 2, dy // 2
        _straight(writer, half_x, half_y)
        _straight(writer, dx - half_x, dy - half_y)
        return
    bits = max(_bits_signed(dx, dy), 2)
    writer.unsigned(0b11, 2)
    writer.unsigned(bits - 2, 4)
    if dx and dy:
        writer.unsigned(1, 1)
        writer.signed(dx, bits)
        writer.signed(dy, bits)
    elif dx:
        writer.unsigned(0, 2)
        writer.signed(dx, bits)
    else:
        writer.unsigned(0b01, 2)
        writer.signed(dy, bits)


def _curve(writer: _BitWriter, cdx: int, cdy: int, adx: int, ady: int) -> None:
    if max(abs(cdx), abs(cdy), abs(adx), abs(ady)).bit_length() + 1 > _MAX_EDGE_BITS:
        # Too long for one record: draw it as two straight halves through the curve.
        mid_x = round((cdx + (cdx + adx)) / 4 + cdx / 4)
        mid_y = round((cdy + (cdy + ady)) / 4 + cdy / 4)
        _straight(writer, mid_x, mid_y)
        _straight(writer, cdx + adx - mid_x, cdy + ady - mid_y)
        return
    bits = max(_bits_signed(cdx, cdy, adx, ady), 2)
    writer.unsigned(0b10, 2)
    writer.unsigned(bits - 2, 4)
    for value in (cdx, cdy, adx, ady):
        writer.signed(value, bits)


def make_glyph(code: int, recording: RecordingPen, advance: int, direction: int) -> Glyph:
    """A glyph from an outline drawn in font units with y pointing up, at DefineFont3 scale.

    `direction` is the sign of the game's own outer outlines (see outline_area_sign).
    """
    # Advances are 16-bit. A rare very wide syllable is narrowed to fit rather than overflow.
    squeeze = min(1.0, _MAX_ADVANCE / advance) if advance > 0 else 1.0
    flipped = RecordingPen()
    for operator, points in recording.value:
        flipped.value.append(
            (operator, tuple(None if p is None else (p[0] * squeeze, -p[1]) for p in points))
        )
    contours = _merged_contours(flipped)
    # pathops gives outer outlines one direction and holes the other. Match the game's fonts.
    if contours:
        areas = [_signed_area(_nodes(contour)) for contour in contours]
        biggest = max(range(len(areas)), key=lambda i: abs(areas[i]))
        if math.copysign(1, areas[biggest]) != direction:
            contours = [_reverse(contour) for contour in contours]
    shape, (xmin, xmax, ymin, ymax) = _encode_shape(contours)
    return Glyph(shape, code, round(advance * squeeze), _rect(xmin, xmax, ymin, ymax))


def add_glyphs(gfx_bytes: bytes, glyphs_by_font: Mapping[bytes, Iterable[Glyph]]) -> bytes:
    """Add glyphs to the named fonts (names as stored, with the trailing zero byte)."""
    movie = GfxFile(gfx_bytes)
    found = set()
    for index, font in movie.fonts():
        if font.name not in glyphs_by_font:
            continue
        found.add(font.name)
        new = list(glyphs_by_font[font.name])
        clash = font.codes & {glyph.code for glyph in new}
        if clash:
            raise ValueError(f"{font.name!r} already has codes {sorted(clash)[:5]}")
        font.glyphs.extend(new)
        movie.tags[index] = Tag(TAG_DEFINE_FONT3, font.to_bytes())
    missing = set(glyphs_by_font) - found
    if missing:
        raise KeyError(f"fonts not found: {sorted(missing)}")
    return movie.to_bytes()
