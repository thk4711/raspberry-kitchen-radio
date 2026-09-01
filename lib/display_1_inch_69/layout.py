# layout.py
"""Pure safe-area layout geometry for the 240x280 now-playing UI.

Workstream 2 of the display redesign introduces a layered layout: a full-bleed
art layer, a darkened top chrome band and a darkened bottom chrome band, all
kept inside a safe-area inset so nothing legible lands in the panel's rounded
physical corners.

This module computes *only pixel rectangles* — no Pillow, no numpy, no
hardware — so the geometry is unit-testable on any machine. Later workstreams
place concrete widgets into these rects:

* top band     -> clock (centre), source badge + play/pause glyph (WS3.3),
                  all within an inner ~70% width so they are never cornered;
* bottom band  -> title / artist text rows (WS2/WS3) and the volume OSD (WS4.2).

A :class:`Rect` is an axis-aligned box in the clean 240x280 compose space
(the driver applies the panel's +20px GRAM offset separately).
"""
from __future__ import annotations

from typing import NamedTuple, Optional


class Rect(NamedTuple):
    """An axis-aligned rectangle ``(x, y, w, h)`` in compose-space pixels."""

    x: int
    y: int
    w: int
    h: int

    @property
    def right(self) -> int:
        """One past the last column (``x + w``)."""
        return self.x + self.w

    @property
    def bottom(self) -> int:
        """One past the last row (``y + h``)."""
        return self.y + self.h

    @property
    def cx(self) -> int:
        """Horizontal centre."""
        return self.x + self.w // 2

    @property
    def cy(self) -> int:
        """Vertical centre."""
        return self.y + self.h // 2


class Layout(NamedTuple):
    """The full set of layout rectangles for one composed frame."""

    frame: Rect        # the whole panel (0, 0, width, height)
    safe: Rect         # inset safe area; nothing legible outside this
    top_band: Rect     # top chrome band (scrim + clock / badge / play-pause)
    bottom_band: Rect  # bottom chrome band (scrim + title / artist / OSD)
    top_inner: Rect    # inner ~70%-width region of the top band (never cornered)
    bottom_inner: Rect  # inner ~70%-width region of the bottom band


def inner_rect(band: Rect, pct: float) -> Rect:
    """Return the horizontally-centred inner slice of ``band``.

    Used for the "never cornered" rule: badges/glyphs live within an inner
    fraction of the band's width so they stay clear of the rounded corners.

    Args:
        band: The band to shrink horizontally.
        pct: Fraction of the band width to keep (e.g. ``0.70`` for 70%).

    Returns:
        A new :class:`Rect` centred in ``band`` with width ``round(band.w*pct)``.
    """
    pct = max(0.0, min(1.0, pct))
    inner_w = int(round(band.w * pct))
    x = band.x + (band.w - inner_w) // 2
    return Rect(x, band.y, inner_w, band.h)


def compute_layout(
    width: int,
    height: int,
    inset: int = 14,
    band_height: int = 56,
    bottom_band_height: Optional[int] = None,
    inner_pct: float = 0.70,
    top_margin: int = 0,
) -> Layout:
    """Compute the safe-area layout rectangles for a ``width x height`` panel.

    Args:
        width: Panel width in pixels (240 for the ST7789 1.69").
        height: Panel height in pixels (280).
        inset: Safe-area inset in pixels kept clear of the rounded corners.
        band_height: Height of the top chrome band (clock / badge / play-pause).
        bottom_band_height: Height of the bottom band (title + artist rows);
            defaults to ``band_height`` when not given.
        inner_pct: Width fraction for the never-cornered inner regions.
        top_margin: Vertical gap in pixels above the top chrome band. Defaults
            to ``0`` so the status strip sits flush against the physical top
            edge; the band still keeps its horizontal safe inset so nothing
            lands in the rounded corners.

    Returns:
        A :class:`Layout` whose bottom band sits inside the safe area and whose
        top band rides near the top edge; the two bands never overlap (each is
        clamped so top and bottom cannot meet).
    """
    if width <= 0 or height <= 0:
        raise ValueError("width and height must be positive")
    inset = max(0, int(inset))
    band_height = max(1, int(band_height))
    bottom_h = max(1, int(band_height if bottom_band_height is None
                          else bottom_band_height))
    # The top band only needs to clear the *rounded corners*, so it can ride
    # right up to the physical top edge (``top_margin`` defaults to 0). Never
    # let the margin exceed the safe inset, so it stays sane on square panels.
    top_margin = max(0, min(int(top_margin), inset))

    frame = Rect(0, 0, width, height)
    safe_w = max(1, width - 2 * inset)
    safe_h = max(1, height - 2 * inset)
    safe = Rect(inset, inset, safe_w, safe_h)

    # Clamp both bands so together they never exceed the available height (top
    # and bottom can never meet, leaving room for the art/logo between them).
    # The top band starts at ``top_margin`` (near the physical top edge) rather
    # than the full safe inset; the bottom band still bottoms out at the safe
    # area. The usable span between them is measured from ``top_margin``.
    span = max(1, safe.bottom - top_margin)
    top_h = min(band_height, max(1, span - bottom_h - 1))
    bottom_h = min(bottom_h, max(1, span - top_h - 1))
    top_band = Rect(safe.x, top_margin, safe.w, top_h)
    bottom_band = Rect(safe.x, safe.bottom - bottom_h, safe.w, bottom_h)

    top_inner = inner_rect(top_band, inner_pct)
    bottom_inner = inner_rect(bottom_band, inner_pct)

    return Layout(
        frame=frame,
        safe=safe,
        top_band=top_band,
        bottom_band=bottom_band,
        top_inner=top_inner,
        bottom_inner=bottom_inner,
    )

