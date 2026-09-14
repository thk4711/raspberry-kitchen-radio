# layout.py
"""Pure safe-area layout geometry for the now-playing display UI.

Workstream 2 of the display redesign introduces a layered layout: a full-bleed
art layer, a darkened top chrome band and a darkened bottom chrome band, all
kept inside a safe-area inset so nothing legible lands in the panel's rounded
physical corners.

This module computes *only pixel rectangles* — no Pillow, no numpy, no
hardware — so the geometry is unit-testable on any machine. It supports two
panel shapes:

* **rect** — the default 240x280 ST7789 rectangular panel. Later workstreams
  place concrete widgets into these rects:

  * top band     -> clock (centre), source badge + play/pause glyph (WS3.3),
                    all within an inner ~70% width so they are never cornered;
  * bottom band  -> title / artist text rows (WS2/WS3) and the volume OSD (WS4.2).

* **round** — the 240x240 GC9A01 circular panel. The layout centres the text
  region on the inscribed circle, shrinks the bands to fit within it, and
  exposes ``center`` / ``radius`` on the returned :class:`Layout` so draw sites
  can clamp elements to the circular edge via ``chord_width``.

A :class:`Rect` is an axis-aligned box in the clean compose-space pixels
(the driver applies any panel-specific GRAM offset separately).
"""
from __future__ import annotations

import math
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
    """The full set of layout rectangles for one composed frame.

    The first six fields are shared by both panel shapes (``rect`` and
    ``round``); a rectangular ST7789 layout uses only these and every historical
    caller/test that constructs a :class:`Layout` positionally keeps working.

    The trailing fields carry the extra geometry the **round** GC9A01 layout
    needs (the inscribed-circle centre + radius). They default to ``None`` for
    the rectangular shape so nothing rectangular has to know about them.
    """

    frame: Rect        # the whole panel (0, 0, width, height)
    safe: Rect         # inset safe area; nothing legible outside this
    top_band: Rect     # top chrome band (scrim + clock / badge / play-pause)
    bottom_band: Rect  # bottom chrome band (scrim + title / artist / OSD)
    top_inner: Rect    # inner ~70%-width region of the top band (never cornered)
    bottom_inner: Rect  # inner ~70%-width region of the bottom band
    # Round-shape only (``None`` for ``rect``): the inscribed circle geometry so
    # draw sites can clamp elements to the circular edge via ``chord_width``.
    shape: str = "rect"
    center: Optional[Rect] = None  # a 1x1 Rect marking the circle centre (cx, cy)
    radius: Optional[int] = None   # inscribed-circle radius in pixels



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


def chord_width(cy: int, radius: int, center_y: Optional[int] = None) -> int:
    """Return the width of the inscribed circle's horizontal chord at row ``cy``.

    For a circle of ``radius`` centred vertically at ``center_y`` (defaulting to
    ``radius``, i.e. a circle that spans ``0 .. 2*radius``), this is the pixel
    width available on the row ``cy`` before an element would cross the circular
    edge. Round-mode draw sites clamp element widths to this so nothing is
    clipped by the panel's circular bezel.

    The value is widest (``2*radius``) at the centre row and shrinks
    monotonically toward the poles, reaching ``0`` at or beyond ``center_y ±
    radius``. Pure integer geometry — no PIL/hardware — so it is unit-testable
    anywhere.

    Args:
        cy: The row (y coordinate) to measure the chord at.
        radius: The inscribed-circle radius in pixels.
        center_y: The circle's vertical centre; defaults to ``radius`` (a circle
            occupying ``0 .. 2*radius``).

    Returns:
        The chord width in pixels (``0`` outside the circle), rounded to the
        nearest whole pixel.
    """
    if radius <= 0:
        return 0
    if center_y is None:
        center_y = radius
    dy = abs(int(cy) - int(center_y))
    if dy >= radius:
        return 0
    return int(round(2.0 * math.sqrt(radius * radius - dy * dy)))


def _round_layout(
    width: int,
    height: int,
    band_height: int,
    bottom_h: int,
    inner_pct: float,
) -> Layout:
    """Compute the shape-aware layout for a round (GC9A01) panel.

    The panel is a circle inscribed in the ``width x height`` square (for the
    GC9A01 both are 240, so ``radius = 120`` centred at ``(120, 120)``). Unlike
    the rectangular ST7789, the round layout deliberately avoids full-width
    chrome bars (which would waste the corners and be clipped by the circular
    bezel). Instead it defines three chord-clamped, horizontally centred zones:

    * a **status zone** (``top_band``) riding near the top of the circle. Its
      first row holds the clock at the very top edge; the row directly beneath
      it holds the source badge (drawn left-aligned) and the play/pause glyph
      (drawn right-aligned). ``display_control`` draws these directly and does
      *not* darken this zone (no scrim), so the clock sits on the artwork.
    * a large empty **centre** left for the station logo / cover art — this is
      the gap between ``top_band.bottom`` and ``bottom_band.y`` and is where the
      circular volume gauge is also drawn; and
    * a **text region** (``bottom_band``) that hugs the bottom of the circle
      (with a small safety margin) so the two metadata rows stay wide and
      legible while freeing the whole centre for the logo.

    Every zone's width is clamped to the chord at its own rows so nothing
    crosses the circular edge. ``center`` / ``radius`` are populated so draw
    sites can clamp per-row via :func:`chord_width`.
    """
    radius = min(width, height) // 2
    cx = width // 2
    cy = height // 2
    frame = Rect(0, 0, width, height)
    top_edge = cy - radius  # y of the circle's topmost pixel
    bottom_edge = cy + radius  # y just past the circle's lowest pixel

    # --- Status zone: a single centred line (the active source) near the top -
    # Only the source name is drawn here now (no clock, no play/pause glyph), so
    # the zone is a shallow single-line strip riding just inside the top of the
    # circle. A small inset keeps the text off the very tip where the chord
    # collapses to nothing.
    top_inset = max(2, radius // 12)
    status_h = max(1, min(band_height // 2, radius))
    top_y = top_edge + top_inset
    # Clamp the zone's width to the narrowest chord across its rows so the source
    # text stays inside the circle.
    top_chord = min(chord_width(top_y, radius, cy),
                    chord_width(top_y + status_h, radius, cy))
    top_w = max(1, min(width, top_chord))
    top_band = Rect(cx - top_w // 2, top_y, top_w, status_h)

    # --- Text region hugging the bottom of the circle ------------------------
    # Sit the two metadata rows near the bottom edge with a small safety margin
    # so the lower row keeps a comfortably wide chord instead of pinching to a
    # point. Keep the region tight (about two snug lines) so the title and
    # artist/station rows sit close together rather than floating apart, and so
    # the whole centre of the circle is freed for the station logo.
    bottom_margin = max(2, radius // 8)
    text_h = max(1, min(int(bottom_h * 0.62), radius))
    text_y = bottom_edge - bottom_margin - text_h
    # Never let the text region climb into (or above) the status zone.
    text_y = max(top_band.bottom + 1, text_y)
    text_chord = min(chord_width(text_y, radius, cy),
                     chord_width(text_y + text_h, radius, cy))
    text_w = max(1, min(width, text_chord))
    bottom_band = Rect(cx - text_w // 2, text_y, text_w, text_h)

    top_inner = inner_rect(top_band, inner_pct)
    bottom_inner = inner_rect(bottom_band, inner_pct)

    return Layout(
        frame=frame,
        safe=Rect(cx - radius, cy - radius, 2 * radius, 2 * radius),
        top_band=top_band,
        bottom_band=bottom_band,
        top_inner=top_inner,
        bottom_inner=bottom_inner,
        shape="round",
        center=Rect(cx, cy, 1, 1),
        radius=radius,
    )



def compute_layout(
    width: int,
    height: int,
    inset: int = 14,
    band_height: int = 56,
    bottom_band_height: Optional[int] = None,
    inner_pct: float = 0.70,
    top_margin: int = 0,
    shape: str = "rect",
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
        shape: ``"rect"`` (default, the 240x280 ST7789) or ``"round"`` (the
            240x240 GC9A01). The ``rect`` result is byte-identical to the
            historical layout; ``round`` centres the text region and shrinks the
            bands to the inscribed circle (see :func:`_round_layout`).

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

    if shape == "round":
        return _round_layout(width, height, band_height, bottom_h, inner_pct)

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

