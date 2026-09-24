#!/usr/bin/env python3
"""Rasterize the source-symbol SVGs into white-on-transparent PNG glyphs.

The PiSonic appliance image ships **no SVG rasteriser** (only Pillow), so the
now-playing display cannot turn an SVG into pixels at runtime. Instead, the two
source glyphs are rasterised **once on a build/dev host** by this script and the
resulting PNGs are committed under ``lib/display/glyphs/`` (shipped verbatim by
``radio-app.mk``'s ``cp -a lib/``). At runtime ``logo_fallback`` loads a PNG,
tints it to the theme colour via its alpha mask, and composites it onto the
placeholder tile — no runtime SVG dependency.

The glyphs are normalised to **pure white with the original coverage alpha** so
the runtime can recolour them to any theme colour by keeping the alpha channel
and replacing RGB. This handles both the filled USB path and the stroked
Bluetooth path uniformly (only the alpha coverage matters afterwards).

Run from the repository root::

    .venv/bin/python scripts/render-source-glyphs.py

This is a **dev/build-time tool only**; ``cairosvg`` is a dev dependency and is
never installed on the appliance.
"""

from __future__ import annotations

import io
from pathlib import Path

import cairosvg  # dev-only dependency; not shipped to the appliance
from PIL import Image

# Rasterise at a comfortably high resolution so downscaling to the ~200 px tile
# on the panel stays crisp. Square canvas; SVGs are centred in their viewBox.
RENDER_SIZE = 512

REPO_ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = REPO_ROOT / "radio_web" / "static"
OUT_DIR = REPO_ROOT / "lib" / "display" / "glyphs"

# (source SVG, output PNG) pairs.
GLYPHS = (
    (STATIC_DIR / "usb-symbol.svg", OUT_DIR / "usb-symbol.png"),
    (STATIC_DIR / "bluetooth-symbol.svg", OUT_DIR / "bluetooth-symbol.png"),
)


def _render_svg(svg_path: Path) -> Image.Image:
    """Rasterise ``svg_path`` to a square RGBA :class:`PIL.Image.Image`."""
    png_bytes = cairosvg.svg2png(
        url=str(svg_path),
        output_width=RENDER_SIZE,
        output_height=RENDER_SIZE,
    )
    with Image.open(io.BytesIO(png_bytes)) as img:
        return img.convert("RGBA")


def _normalise_to_white(img: Image.Image) -> Image.Image:
    """Replace RGB with pure white, keeping the original alpha coverage.

    The runtime tints the glyph by recolouring RGB and reusing this alpha as a
    mask, so the committed PNG only needs a faithful coverage (alpha) channel.
    """
    r, g, b, a = img.split()
    white = Image.new("L", img.size, 255)
    return Image.merge("RGBA", (white, white, white, a))


def _autocrop(img: Image.Image) -> Image.Image:
    """Crop transparent margins so the runtime can size the glyph precisely."""
    bbox = img.split()[3].getbbox()
    return img.crop(bbox) if bbox else img


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for svg_path, png_path in GLYPHS:
        if not svg_path.exists():
            raise SystemExit(f"missing SVG: {svg_path}")
        glyph = _autocrop(_normalise_to_white(_render_svg(svg_path)))
        glyph.save(png_path, "PNG")
        print(f"wrote {png_path.relative_to(REPO_ROOT)}  ({glyph.width}x{glyph.height})")


if __name__ == "__main__":
    main()
