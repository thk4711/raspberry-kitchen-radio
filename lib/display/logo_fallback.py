# logo_fallback.py
"""Generate a branded initials tile for stations without a logo (Workstream 6.3).

When a preset station has no logo file (a user-added station, or a ``logo=``
pointing at a missing file), the radio-mode art would otherwise fall back to a
flat neutral backdrop. This module renders a small, deterministic "initials
tile" from the station name instead — a rounded square in a name-derived colour
with one or two centred initials — so the display still looks intentional and
the dominant-colour backdrop has something branded to sample.

Pure Pillow only (no hardware, no numpy, no I/O), so it is unit-testable on any
machine like ``layout.py`` / ``textformat.py`` / ``theme.py``. The colour is a
deterministic function of the name, so a given station always yields the same
tile (keeping the art cache stable) without any per-station configuration.
"""
from __future__ import annotations

import colorsys
import hashlib
from typing import Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

Color = Tuple[int, int, int]

# Placeholder tile colour for the Bluetooth source. Bluetooth A2DP/AVRCP carries
# no cover art, so when a phone is connected the display would otherwise render
# the name-derived initials tile (a blank artist collapses to a "?" on a
# name-hashed colour). Instead we show a dedicated Bluetooth-glyph tile in a
# calm blue. The blue is derived from the *same* muted saturation/value the
# initials tiles use (:func:`tile_color`: sat 0.45, val 0.55) at a blue hue
# (~212 deg) so it reads as part of the same family, just unmistakably "blue"
# rather than a random name-hashed hue.
BLUETOOTH_TILE_COLOR: Color = (77, 107, 140)  # colorsys.hsv_to_rgb(212/360, 0.45, 0.55)

# Official Bluetooth mark, transcribed from the public-domain SVG
# (commons.wikimedia.org/wiki/File:Bluetooth.svg, viewBox 0 0 640 976,
# single ``fill="none"`` stroked path, stroke-width 53). The path
#   "m157 330 305 307 -147 178 V179 l147 170 -305 299"
# decodes to this continuous polyline of absolute points:
#   upper-left knee -> lower-right tip -> spine bottom -> spine top
#   -> upper-right tip -> lower-left knee
# The spine is the (315,815)<->(315,179) segment. Because it is just a stroked
# polyline, Pillow's ``ImageDraw.line`` reproduces it exactly with no SVG
# rasteriser (unavailable on the appliance) — we only scale it into the tile.
_BT_VIEWBOX: Tuple[int, int] = (640, 976)
_BT_STROKE: int = 53
_BT_PATH: Tuple[Tuple[int, int], ...] = (
    (157, 330),   # upper-left knee (path start)
    (462, 637),   # lower-right tip
    (315, 815),   # spine bottom
    (315, 179),   # spine top
    (462, 349),   # upper-right tip
    (157, 648),   # lower-left knee
)


def initials(name: str, max_len: int = 2) -> str:
    """Return up to ``max_len`` uppercase initials for a station ``name``.

    Uses the first letter of each word for multi-word names ("MDR JUMP" -> "MJ",
    "Deutschlandfunk Nova" -> "DN"); for a single word it takes the first
    ``max_len`` letters ("KEXP" -> "KE"). Non-alphanumeric junk is ignored, and
    an empty/blank name yields "?" so the tile is never blank.

    Args:
        name: The station name.
        max_len: Maximum number of initials to return (1 or 2 look best).

    Returns:
        A short uppercase string of 1..``max_len`` characters.
    """
    words = [w for w in (name or "").split() if any(c.isalnum() for c in w)]
    if not words:
        return "?"
    if len(words) >= 2:
        letters = "".join(_first_alnum(w) for w in words)
        return letters[:max_len].upper() or "?"
    # Single word: take its first ``max_len`` alphanumeric characters.
    alnum = "".join(c for c in words[0] if c.isalnum())
    return (alnum[:max_len] or "?").upper()


def _first_alnum(word: str) -> str:
    """Return the first alphanumeric character of ``word`` (or '')."""
    for c in word:
        if c.isalnum():
            return c
    return ""


def tile_color(name: str) -> Color:
    """Return a deterministic, pleasant ``(r, g, b)`` colour for ``name``.

    The hue is derived from a stable hash of the name (so the same station
    always maps to the same colour) at a fixed, muted saturation/value so the
    tile reads as a calm brand chip rather than a harsh primary.

    Args:
        name: The station name (case-insensitive for hashing).

    Returns:
        An ``(r, g, b)`` int triple.
    """
    digest = hashlib.sha256((name or "").strip().lower().encode("utf-8")).digest()
    hue = digest[0] / 255.0            # 0..1 around the colour wheel
    sat = 0.45                          # muted, not garish
    val = 0.55                          # mid brightness, good text contrast
    r, g, b = colorsys.hsv_to_rgb(hue, sat, val)
    return (int(round(r * 255)), int(round(g * 255)), int(round(b * 255)))


def render_initials_tile(
    name: str,
    size: int,
    font: ImageFont.FreeTypeFont,
    text_color: Color = (255, 255, 255),
    bg_color: Optional[Color] = None,
) -> Image.Image:
    """Render an ``size`` x ``size`` RGBA initials tile for ``name``.

    A rounded square filled with ``bg_color`` (a deterministic name-derived
    colour when not given) and the station's :func:`initials` centred in
    ``font``. Returned as RGBA so it composites like a real logo (the display
    pastes it with its own alpha and samples it for the backdrop colour).

    Args:
        name: The station name.
        size: The tile's width and height in pixels.
        font: A loaded font used to draw the initials.
        text_color: Colour of the initials.
        bg_color: Tile background; defaults to :func:`tile_color` of ``name``.

    Returns:
        An RGBA :class:`PIL.Image.Image` of ``size`` x ``size``.
    """
    size = max(1, int(size))
    if bg_color is None:
        bg_color = tile_color(name)

    tile = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(tile)
    radius = max(1, size // 8)
    draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=radius,
                           fill=bg_color + (255,))

    text = initials(name)
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    tx = (size - tw) // 2 - bbox[0]
    ty = (size - th) // 2 - bbox[1]
    draw.text((tx, ty), text, font=font, fill=text_color + (255,))
    return tile


def render_bluetooth_tile(
    size: int,
    bg_color: Color = BLUETOOTH_TILE_COLOR,
    glyph_color: Color = (255, 255, 255),
) -> Image.Image:
    """Render an ``size`` x ``size`` RGBA tile with the Bluetooth glyph.

    Used as the placeholder art for the Bluetooth source, which never carries
    cover art. The tile matches :func:`render_initials_tile` (a rounded square
    with transparent corners) so the display composites and samples it exactly
    like a real logo, but instead of name-derived initials it draws the official
    Bluetooth mark.

    The glyph is the public-domain ``Bluetooth.svg`` path (see :data:`_BT_PATH`)
    scaled into the tile and stroked with :meth:`PIL.ImageDraw.line`. That SVG
    is a single ``fill="none"`` stroked polyline, so this reproduces it exactly
    with no SVG rasteriser (none is available on the appliance) and no font
    dependency, staying crisp and deterministic at any tile size.

    Args:
        size: The tile's width and height in pixels.
        bg_color: Tile background; defaults to :data:`BLUETOOTH_TILE_COLOR`.
        glyph_color: Colour of the Bluetooth rune.

    Returns:
        An RGBA :class:`PIL.Image.Image` of ``size`` x ``size``.
    """
    size = max(1, int(size))
    tile = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(tile)
    radius = max(1, size // 8)
    draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=radius,
                           fill=bg_color + (255,))

    # Scale the official SVG path into a centred region of the tile, preserving
    # its (tall) aspect ratio. The glyph's own bounding box is computed from the
    # path so the mark is centred no matter the vertices.
    fill = glyph_color + (255,)
    xs = [p[0] for p in _BT_PATH]
    ys = [p[1] for p in _BT_PATH]
    gx0, gx1 = min(xs), max(xs)
    gy0, gy1 = min(ys), max(ys)
    gw = gx1 - gx0
    gh = gy1 - gy0

    # Leave a margin so the stroke (drawn centred on the path) never touches the
    # rounded corners; ~72% of the tile is the drawable region.
    avail = size * 0.72
    scale = min(avail / gw, avail / gh) if gw and gh else 1.0
    # Centre the scaled bounding box in the tile.
    off_x = (size - gw * scale) / 2.0 - gx0 * scale
    off_y = (size - gh * scale) / 2.0 - gy0 * scale
    points = [(x * scale + off_x, y * scale + off_y) for x, y in _BT_PATH]

    # Stroke width scales with the glyph, matching the SVG's 53/640 proportion.
    width = max(1, int(round(_BT_STROKE * scale)))

    # One stroked polyline, exactly like the source SVG, with round joints.
    draw.line(points, fill=fill, width=width, joint="curve")
    # Round the two open path ends so they read soft on the small LCD (the SVG
    # uses butt caps; rounded looks better at panel resolution).
    r = width / 2.0
    for px, py in (points[0], points[-1]):
        draw.ellipse((px - r, py - r, px + r, py + r), fill=fill)
    return tile

