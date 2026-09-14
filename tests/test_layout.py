"""Unit tests for the pure safe-area layout geometry (Workstream 2).

No Pillow, no numpy, no hardware — just pixel-rectangle math, so these run on
any machine.
"""
import pytest
from display import layout as layout_mod
from display.layout import Rect, chord_width, compute_layout, inner_rect


def test_rect_derived_properties():
    r = Rect(10, 20, 100, 40)
    assert r.right == 110
    assert r.bottom == 60
    assert r.cx == 60
    assert r.cy == 40


def test_compute_layout_frame_and_safe_area():
    lay = compute_layout(240, 280, inset=14, band_height=60)
    assert lay.frame == Rect(0, 0, 240, 280)
    # Safe area is inset on all sides.
    assert lay.safe == Rect(14, 14, 240 - 28, 280 - 28)


def test_bands_inside_safe_area_and_non_overlapping():
    lay = compute_layout(240, 280, inset=14, band_height=60)
    # Both bands stay within the safe area horizontally.
    for band in (lay.top_band, lay.bottom_band):
        assert band.x == lay.safe.x
        assert band.w == lay.safe.w
    # The top band rides near the physical top edge (above the safe inset, but
    # never off-panel) so the status strip sits right at the top.
    assert lay.top_band.y >= lay.frame.y
    assert lay.top_band.y < lay.safe.y
    # The bottom band still bottoms out inside the safe area.
    assert lay.bottom_band.y >= lay.safe.y
    assert lay.bottom_band.bottom <= lay.safe.bottom
    # Top band sits above bottom band and they never meet.
    assert lay.top_band.bottom <= lay.bottom_band.y


def test_bands_clamped_to_fit_and_not_overlap():
    # Huge band heights are clamped so the two bands still fit the safe area
    # and never meet (leaving at least a sliver of art between them).
    lay = compute_layout(240, 280, inset=0, band_height=1000,
                         bottom_band_height=1000)
    assert lay.top_band.bottom <= lay.bottom_band.y
    assert lay.top_band.y >= lay.safe.y
    assert lay.bottom_band.bottom <= lay.safe.bottom


def test_separate_top_and_bottom_band_heights():
    lay = compute_layout(240, 280, inset=14, band_height=44,
                         bottom_band_height=74)
    assert lay.top_band.h == 44
    assert lay.bottom_band.h == 74
    assert lay.top_band.bottom <= lay.bottom_band.y



def test_inner_regions_never_cornered():
    lay = compute_layout(240, 280, inset=14, band_height=60, inner_pct=0.70)
    for band, inner in ((lay.top_band, lay.top_inner),
                        (lay.bottom_band, lay.bottom_inner)):
        # Inner region is narrower than and centred within the band.
        assert inner.w < band.w
        assert inner.x > band.x
        assert inner.right < band.right
        assert abs(inner.cx - band.cx) <= 1


def test_inner_rect_pct_bounds():
    band = Rect(0, 0, 200, 50)
    assert inner_rect(band, 1.0).w == 200
    assert inner_rect(band, 0.0).w == 0
    # Clamps out-of-range percentages.
    assert inner_rect(band, 2.0).w == 200


@pytest.mark.parametrize("bad", [(0, 280), (240, 0)])
def test_compute_layout_rejects_nonpositive(bad):
    with pytest.raises(ValueError):
        compute_layout(bad[0], bad[1])


# --- Round-shape geometry (GC9A01 240x240) --------------------------------


def test_chord_width_widest_at_centre_and_zero_at_poles():
    r = 120
    # Widest chord is the full diameter at the centre row.
    assert chord_width(r, r) == 2 * r
    # At (and beyond) the poles the chord vanishes.
    assert chord_width(0, r) == 0
    assert chord_width(2 * r, r) == 0
    assert chord_width(-5, r) == 0
    assert chord_width(2 * r + 5, r) == 0


def test_chord_width_monotonic_toward_centre():
    r = 120
    # Moving from a pole toward the centre never narrows the chord.
    widths = [chord_width(y, r) for y in range(0, r + 1)]
    assert widths == sorted(widths)
    # Symmetric about the centre row.
    for dy in range(0, r + 1):
        assert chord_width(r - dy, r) == chord_width(r + dy, r)


def test_chord_width_respects_center_y_offset():
    r = 100
    # A circle centred at y=100 is widest there, not at y=r.
    assert chord_width(100, r, center_y=100) == 2 * r
    assert chord_width(0, r, center_y=100) == 0


def test_chord_width_nonpositive_radius_is_zero():
    assert chord_width(50, 0) == 0
    assert chord_width(50, -10) == 0


def test_round_layout_populates_center_and_radius():
    lay = compute_layout(240, 240, shape="round")
    assert lay.shape == "round"
    assert lay.radius == 120
    assert lay.center == Rect(120, 120, 1, 1)
    assert lay.frame == Rect(0, 0, 240, 240)


def _within_circle(band: Rect, cx: int, cy: int, radius: int) -> bool:
    """True when both top corners of ``band`` lie inside the inscribed circle."""
    def inside(x: int, y: int) -> bool:
        return (x - cx) ** 2 + (y - cy) ** 2 <= radius ** 2 + 1  # +1 rounding slack
    return (inside(band.x, band.cy) and inside(band.right, band.cy))


def test_round_bands_stay_within_inscribed_circle():
    lay = compute_layout(240, 240, band_height=44, bottom_band_height=82,
                         shape="round")
    cx, cy, r = lay.center.x, lay.center.y, lay.radius
    # Each band's width must not exceed the chord at its own centre row.
    assert lay.top_band.w <= chord_width(lay.top_band.cy, r, cy)
    assert lay.bottom_band.w <= chord_width(lay.bottom_band.cy, r, cy)
    # And the band edges lie inside the circle.
    assert _within_circle(lay.top_band, cx, cy, r)
    assert _within_circle(lay.bottom_band, cx, cy, r)


def test_round_bands_are_horizontally_centred():
    lay = compute_layout(240, 240, shape="round")
    # Both bands are centred on the panel's vertical axis (±1 px rounding).
    assert abs(lay.top_band.cx - lay.center.x) <= 1
    assert abs(lay.bottom_band.cx - lay.center.x) <= 1


def test_round_text_region_below_top_arc_and_non_overlapping():
    lay = compute_layout(240, 240, band_height=44, bottom_band_height=82,
                         shape="round")
    # The top status zone sits above the text region; they never meet.
    assert lay.top_band.bottom <= lay.bottom_band.y
    # The text region now hugs the bottom of the circle (below the centre) so
    # the whole middle is free for the station logo.
    assert lay.bottom_band.y > lay.center.y
    # It still stays inside the circle's lower half.
    assert lay.bottom_band.bottom <= lay.center.y + lay.radius


def test_round_status_zone_rides_near_top_edge():
    lay = compute_layout(240, 240, band_height=44, bottom_band_height=82,
                         shape="round")
    top_edge = lay.center.y - lay.radius
    # The status zone starts near the very top of the circle (small inset).
    assert lay.top_band.y - top_edge <= lay.radius // 6
    # And it sits above the vertical centre so the middle is left for the logo.
    assert lay.top_band.bottom < lay.center.y


def test_rect_shape_default_unchanged_by_round_support():
    # Regression: the default (rect) path is unaffected by the new shape arg.
    default = compute_layout(240, 280, inset=14, band_height=44,
                             bottom_band_height=82)
    explicit = compute_layout(240, 280, inset=14, band_height=44,
                              bottom_band_height=82, shape="rect")
    assert default == explicit
    assert default.shape == "rect"
    assert default.center is None
    assert default.radius is None

