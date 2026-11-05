"""Generate the GameMod logo files in assets/logo. Run: python tools/make_logo.py

The mark is a hex nut (modding) with a D-pad cut out of it (games).
"""
from __future__ import annotations

import io
import math
from pathlib import Path

import uharfbuzz as hb
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from PIL import Image
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "assets" / "logo"
FONT = ROOT / "assets" / "fonts" / "NotoSansDevanagari-VF.ttf"

AMBER_TOP = "#f4bd5c"
AMBER_BOTTOM = "#d98f27"
AMBER_FLAT = "#e3a33b"
TILE_TOP = "#2a2f37"
TILE_BOTTOM = "#14161a"
ON_DARK = "#ecebe8"
ON_LIGHT = "#14161a"

ICON_SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256]
SIMPLE_AT_OR_BELOW = 32  # the small arrows turn to noise at these sizes


def _rounded(points, radii) -> str:
    count = len(points)
    parts = []
    for i in range(count):
        before, corner, after = points[i - 1], points[i], points[(i + 1) % count]
        radius = radii[i] if isinstance(radii, list) else radii

        def toward(a, b):
            length = math.hypot(b[0] - a[0], b[1] - a[1])
            step = min(radius, length / 2) / length
            return a[0] + (b[0] - a[0]) * step, a[1] + (b[1] - a[1]) * step

        start, end = toward(corner, before), toward(corner, after)
        parts.append(f"{'M' if i == 0 else 'L'}{start[0]:.2f} {start[1]:.2f}")
        parts.append(f"Q{corner[0]:.2f} {corner[1]:.2f} {end[0]:.2f} {end[1]:.2f}")
    return " ".join(parts) + "Z"


def mark_path(cx: float, cy: float, scale: float, arrows: bool) -> str:
    """The hex nut and D-pad as one path. D-pad and arrows work through even-odd filling."""
    radius, corner = 204 * scale, 30 * scale
    hexagon = [
        (cx + radius * math.cos(math.radians(60 * i - 90)), cy + radius * math.sin(math.radians(60 * i - 90)))
        for i in range(6)
    ]
    reach, half = 118 * scale, 40 * scale
    outer, inner = 12 * scale, 5 * scale
    cross = [
        (cx - half, cy - reach), (cx + half, cy - reach), (cx + half, cy - half), (cx + reach, cy - half),
        (cx + reach, cy + half), (cx + half, cy + half), (cx + half, cy + reach), (cx - half, cy + reach),
        (cx - half, cy + half), (cx - reach, cy + half), (cx - reach, cy - half), (cx - half, cy - half),
    ]
    cross_radii = [outer, outer, inner] * 4
    parts = [_rounded(hexagon, corner), _rounded(cross, cross_radii)]
    if arrows:
        size, width = 18 * scale, 16.2 * scale
        d = reach - size - 16 * scale
        for tip, left, right in (
            ((cx, cy - d - size), (cx + width, cy - d), (cx - width, cy - d)),
            ((cx + d + size, cy), (cx + d, cy + width), (cx + d, cy - width)),
            ((cx, cy + d + size), (cx - width, cy + d), (cx + width, cy + d)),
            ((cx - d - size, cy), (cx - d, cy - width), (cx - d, cy + width)),
        ):
            parts.append(_rounded([tip, left, right], 3 * scale))
    return " ".join(parts)


_AMBER_GRADIENT = (
    f'<linearGradient id="amber" x1="0" y1="0" x2="0" y2="1">'
    f'<stop offset="0" stop-color="{AMBER_TOP}"/><stop offset="1" stop-color="{AMBER_BOTTOM}"/></linearGradient>'
)


def icon_svg(arrows: bool = True) -> str:
    """The app icon: the mark on a dark rounded tile."""
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512"><defs>'
        f'<linearGradient id="tile" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0" stop-color="{TILE_TOP}"/><stop offset="1" stop-color="{TILE_BOTTOM}"/></linearGradient>'
        f"{_AMBER_GRADIENT}</defs>"
        '<rect x="8" y="8" width="496" height="496" rx="112" fill="url(#tile)"/>'
        f'<path d="{mark_path(256, 256, 1, arrows)}" fill="url(#amber)" fill-rule="evenodd"/></svg>'
    )


def mark_svg() -> str:
    """The mark alone on a transparent background."""
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 448 448"><defs>{_AMBER_GRADIENT}</defs>'
        f'<path d="{mark_path(224, 224, 1, True)}" fill="url(#amber)" fill-rule="evenodd"/></svg>'
    )


def _text_path(text: str, x: float, baseline: float, size: float) -> tuple[str, float]:
    """Text as outlines, so the logo looks the same without the font installed."""
    face = hb.Face(hb.Blob.from_file_path(str(FONT)))
    font = hb.Font(face)
    font.set_variations({"wght": 700, "wdth": 100})
    buffer = hb.Buffer()
    buffer.add_str(text)
    buffer.guess_segment_properties()
    hb.shape(font, buffer)
    scale = size / face.upem
    pen = SVGPathPen(None)
    pen_x = 0
    for info, position in zip(buffer.glyph_infos, buffer.glyph_positions):
        if info.codepoint == 0:
            raise ValueError(f"The logo font has no glyph in {text!r}")
        transform = (scale, 0, 0, -scale, x + pen_x * scale, baseline)
        font.draw_glyph_with_pen(info.codepoint, TransformPen(pen, transform))
        pen_x += position.x_advance
    return pen.getCommands(), pen_x * scale


def logo_svg(text_color: str) -> tuple[str, float, float]:
    """The mark beside the name. Returns the SVG with its width and height."""
    height = 300.0
    mark_scale = 0.62
    size = 158.0
    margin = 20.0
    centre = height / 2
    baseline = centre + size * 0.357  # centres the capital letters on the mark
    start = margin + 448 * mark_scale + 26
    game, game_width = _text_path("Game", start, baseline, size)
    mod, mod_width = _text_path("Mod", start + game_width, baseline, size)
    width = start + game_width + mod_width + margin
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width:.1f} {height:.1f}"><defs>{_AMBER_GRADIENT}</defs>'
        f'<path d="{mark_path(margin + 224 * mark_scale, centre, mark_scale, True)}" fill="url(#amber)" fill-rule="evenodd"/>'
        f'<path d="{game}" fill="{text_color}"/><path d="{mod}" fill="{AMBER_FLAT}"/></svg>'
    )
    return svg, width, height


def render(svg: str, width: int, height: int) -> Image.Image:
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    image = QImage(width, height, QImage.Format_ARGB32_Premultiplied)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    renderer.render(painter, QRectF(0, 0, width, height))
    painter.end()
    buffer = QBuffer()
    buffer.open(QIODevice.WriteOnly)
    image.save(buffer, "PNG")
    return Image.open(io.BytesIO(bytes(buffer.data()))).convert("RGBA")


def main() -> None:
    QGuiApplication([])
    OUT.mkdir(parents=True, exist_ok=True)

    (OUT / "gamemod-icon.svg").write_text(icon_svg(), encoding="utf-8")
    (OUT / "gamemod-mark.svg").write_text(mark_svg(), encoding="utf-8")

    icons = {
        size: render(icon_svg(arrows=size > SIMPLE_AT_OR_BELOW), size, size) for size in ICON_SIZES
    }
    largest = icons[ICON_SIZES[-1]]
    others = [icons[size] for size in ICON_SIZES[:-1]]
    largest.save(
        OUT / "gamemod.ico",
        format="ICO",
        sizes=[(size, size) for size in ICON_SIZES],
        append_images=others,
    )
    for size in (16, 32, 256):
        icons[size].save(OUT / f"gamemod-icon-{size}.png")
    render(icon_svg(), 1024, 1024).save(OUT / "gamemod-icon-1024.png")
    render(mark_svg(), 128, 128).save(OUT / "gamemod-mark-128.png")

    for name, color in (("on-dark", ON_DARK), ("on-light", ON_LIGHT)):
        svg, width, height = logo_svg(color)
        (OUT / f"gamemod-logo-{name}.svg").write_text(svg, encoding="utf-8")
        scale = 1600 / width
        render(svg, 1600, round(height * scale)).save(OUT / f"gamemod-logo-{name}.png")

    print("Wrote:", ", ".join(sorted(p.name for p in OUT.iterdir())))


if __name__ == "__main__":
    main()
