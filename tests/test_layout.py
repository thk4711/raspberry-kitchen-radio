"""Unit tests for the pure safe-area layout geometry (Workstream 2).

No Pillow, no numpy, no hardware — just pixel-rectangle math, so these run on
any machine.
"""
import pytest
from display_1_inch_69 import layout as layout_mod
from display_1_inch_69.layout import Rect, compute_layout, inner_rect


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
