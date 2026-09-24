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
import os
from typing import Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

Color = Tuple[int, int, int]

# Directory holding the pre-rasterised source glyph PNGs. The appliance image
# ships no SVG rasteriser (only Pillow), so the two source symbols under
# ``radio_web/static/`` are rasterised once on a build/dev host by
# ``scripts/render-source-glyphs.py`` and committed here as white-on-transparent
# PNGs. At runtime we load a PNG, tint it to the theme colour via its alpha
# mask, and composite it onto the placeholder tile.
_GLYPH_DIR = os.path.join(os.path.dirname(__file__), "glyphs")
_USB_GLYPH = os.path.join(_GLYPH_DIR, "usb-symbol.png")
_BLUETOOTH_GLYPH = os.path.join(_GLYPH_DIR, "bluetooth-symbol.png")

# Placeholder tile colour for the Bluetooth source. Bluetooth A2DP/AVRCP carries
# no cover art, so when a phone is connected the display would otherwise render
# the name-derived initials tile (a blank artist collapses to a "?" on a
# name-hashed colour). Instead we show a dedicated Bluetooth-glyph tile in a
# calm blue. The blue is derived from the *same* muted saturation/value the
# initials tiles use (:func:`tile_color`: sat 0.45, val 0.55) at a blue hue
# (~212 deg) so it reads as part of the same family, just unmistakably "blue"
# rather than a random name-hashed hue.
BLUETOOTH_TILE_COLOR: Color = (77, 107, 140)  # colorsys.hsv_to_rgb(212/360, 0.45, 0.55)

# Placeholder tile colour for the USB source. USB Audio Class delivers no cover
# art either, so the USB source uses a dedicated USB-glyph tile. The colour is a
# muted slate/teal from the same saturation/value family as the initials and
# Bluetooth tiles (sat 0.45, val 0.55) at a cyan-teal hue (~192 deg) so it reads
# as a sibling of the Bluetooth tile while staying distinct from it.
USB_TILE_COLOR: Color = (77, 129, 140)  # colorsys.hsv_to_rgb(192/360, 0.45, 0.55)


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
    hue = digest[0] / 255.0  # 0..1 around the colour wheel
    sat = 0.45  # muted, not garish
    val = 0.55  # mid brightness, good text contrast
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
    draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=bg_color + (255,))

    text = initials(name)
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    tx = (size - tw) // 2 - bbox[0]
    ty = (size - th) // 2 - bbox[1]
    draw.text((tx, ty), text, font=font, fill=text_color + (255,))
    return tile


def _render_glyph_tile(
    size: int,
    glyph_path: str,
    bg_color: Color,
    glyph_color: Color,
    coverage: float = 0.72,
) -> Image.Image:
    """Render a ``size`` x ``size`` RGBA tile with a pre-rasterised glyph.

    Draws the same rounded square as :func:`render_initials_tile`, then loads the
    white-on-transparent PNG at ``glyph_path`` (produced from the source SVG by
    ``scripts/render-source-glyphs.py``), tints it to ``glyph_color`` by keeping
    its alpha as a mask, scales it to ``coverage`` of the tile preserving aspect
    ratio, and composites it centred. Loading a committed PNG needs no SVG
    rasteriser (none ships on the appliance) yet stays faithful to the SVG.

    Args:
        size: The tile's width and height in pixels.
        glyph_path: Path to a white-on-transparent RGBA glyph PNG.
        bg_color: Tile background colour.
        glyph_color: Colour the glyph is tinted to.
        coverage: Fraction of the tile the glyph's longer side may occupy.

    Returns:
        An RGBA :class:`PIL.Image.Image` of ``size`` x ``size``.
    """
    size = max(1, int(size))
    tile = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(tile)
    radius = max(1, size // 8)
    draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=bg_color + (255,))

    with Image.open(glyph_path) as img:
        glyph = img.convert("RGBA")

    # Scale the glyph to ``coverage`` of the tile, preserving aspect ratio.
    avail = max(1, int(size * coverage))
    gw, gh = glyph.size
    scale = min(avail / gw, avail / gh) if gw and gh else 1.0
    nw = max(1, int(round(gw * scale)))
    nh = max(1, int(round(gh * scale)))
    glyph = glyph.resize((nw, nh), Image.Resampling.LANCZOS)

    # Tint: keep the glyph's alpha (coverage) but replace RGB with glyph_color,
    # so the mark takes the theme colour regardless of the PNG's own colour.
    alpha = glyph.split()[3]
    tinted = Image.new("RGBA", glyph.size, glyph_color + (0,))
    tinted.putalpha(alpha)

    ox = (size - nw) // 2
    oy = (size - nh) // 2
    tile.paste(tinted, (ox, oy), tinted)
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
    like a real logo, but instead of name-derived initials it shows the
    Bluetooth mark rasterised from ``radio_web/static/bluetooth-symbol.svg`` (see
    :data:`_BLUETOOTH_GLYPH` and ``scripts/render-source-glyphs.py``).

    Args:
        size: The tile's width and height in pixels.
        bg_color: Tile background; defaults to :data:`BLUETOOTH_TILE_COLOR`.
        glyph_color: Colour of the Bluetooth rune.

    Returns:
        An RGBA :class:`PIL.Image.Image` of ``size`` x ``size``.
    """
    return _render_glyph_tile(size, _BLUETOOTH_GLYPH, bg_color, glyph_color)


def render_usb_tile(
    size: int,
    bg_color: Color = USB_TILE_COLOR,
    glyph_color: Color = (255, 255, 255),
) -> Image.Image:
    """Render an ``size`` x ``size`` RGBA tile with the USB glyph.

    Used as the placeholder art for the USB Audio source, which delivers no
    cover art. Like :func:`render_bluetooth_tile` it is a rounded square with
    transparent corners so the display composites and samples it exactly like a
    real logo, but it shows the USB trident rasterised from
    ``radio_web/static/usb-symbol.svg`` (see :data:`_USB_GLYPH` and
    ``scripts/render-source-glyphs.py``).

    Args:
        size: The tile's width and height in pixels.
        bg_color: Tile background; defaults to :data:`USB_TILE_COLOR`.
        glyph_color: Colour of the USB mark.

    Returns:
        An RGBA :class:`PIL.Image.Image` of ``size`` x ``size``.
    """
    return _render_glyph_tile(size, _USB_GLYPH, bg_color, glyph_color)
