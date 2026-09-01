"""Unit tests for the pure numpy pixel-math helpers in ``compositor``.

These functions take no hardware and no Pillow, so they run on any machine
(the display redesign plan's Workstream 7.1). ``numpy`` is required; it is a
target runtime dependency and is installed by ``requirements-dev.txt`` so the
suite runs off-Pi.
"""
import numpy as np
import pytest
from display_1_inch_69 import compositor


def test_solid_rgb_shape_and_fill():
    buf = compositor.solid_rgb(4, 3, (10, 20, 30))
    assert buf.shape == (3, 4, 3)
    assert buf.dtype == np.uint8
    assert (buf == np.array([10, 20, 30], dtype=np.uint8)).all()


def test_solid_rgb_clips_out_of_range_channels():
    buf = compositor.solid_rgb(2, 2, (300, -5, 128))
    assert (buf[0, 0] == [255, 0, 128]).all()


@pytest.mark.parametrize("bad", [(0, 4), (4, 0), (-1, 4)])
def test_solid_rgb_rejects_nonpositive_dims(bad):
    with pytest.raises(ValueError):
        compositor.solid_rgb(bad[0], bad[1], (0, 0, 0))


def test_vertical_gradient_endpoints_and_shape():
    g = compositor.vertical_gradient(5, 5, (0, 0, 0), (100, 200, 50))
    assert g.shape == (5, 5, 3)
    assert (g[0, 0] == [0, 0, 0]).all()
    assert (g[-1, 0] == [100, 200, 50]).all()
    # Constant across a row (only varies down the Y axis).
    assert (g[2] == g[2, 0]).all()
    # Monotonic non-decreasing per channel down the rows.
    col = g[:, 0].astype(int)
    assert (np.diff(col[:, 0]) >= 0).all()


def test_vertical_gradient_single_row_uses_top_color():
    g = compositor.vertical_gradient(3, 1, (10, 20, 30), (200, 200, 200))
    assert g.shape == (1, 3, 3)
    assert (g[0, 0] == [10, 20, 30]).all()


def test_apply_scrim_darkens_only_the_band_and_copies():
    base = compositor.solid_rgb(4, 10, (200, 200, 200))
    out = compositor.apply_scrim(base, 2, 5, (0, 0, 0), 0.5)
    # Rows outside [2, 5) untouched.
    assert (out[0, 0] == [200, 200, 200]).all()
    assert (out[5, 0] == [200, 200, 200]).all()
    # Rows inside blended half-way to black.
    assert (out[3, 0] == [100, 100, 100]).all()
    # The input buffer is not mutated.
    assert (base[3, 0] == [200, 200, 200]).all()


def test_apply_scrim_zero_opacity_is_noop():
    base = compositor.solid_rgb(3, 6, (120, 130, 140))
    out = compositor.apply_scrim(base, 0, 6, (0, 0, 0), 0.0)
    assert (out == base).all()


def test_apply_scrim_clamps_band_to_buffer():
    base = compositor.solid_rgb(3, 4, (200, 200, 200))
    # Band fully outside the buffer -> no change.
    out = compositor.apply_scrim(base, 10, 20, (0, 0, 0), 1.0)
    assert (out == base).all()


def test_alpha_blend_endpoints_and_midpoint():
    b = compositor.solid_rgb(2, 2, (0, 0, 0))
    o = compositor.solid_rgb(2, 2, (255, 255, 255))
    full = np.full((2, 2), 255, np.uint8)
    zero = np.zeros((2, 2), np.uint8)
    half = np.full((2, 2), 128, np.uint8)
    assert (compositor.alpha_blend(b, o, full) == 255).all()
    assert (compositor.alpha_blend(b, o, zero) == 0).all()
    # 128/255 rounds to a clean 128 with round-to-nearest.
    assert (compositor.alpha_blend(b, o, half) == 128).all()


def test_alpha_blend_rejects_mismatched_alpha():
    b = compositor.solid_rgb(2, 2, (0, 0, 0))
    o = compositor.solid_rgb(2, 2, (255, 255, 255))
    with pytest.raises(ValueError):
        compositor.alpha_blend(b, o, np.zeros((3, 3), np.uint8))


def test_pack_rgb565_length_and_layout():
    rng = np.random.default_rng(0)
    arr = rng.integers(0, 256, (8, 6, 3), dtype=np.uint8)
    packed = compositor.pack_rgb565(arr)
    assert len(packed) == 8 * 6 * 2

    # Reproduce the ST7789 driver's own encoder byte-for-byte so the compositor
    # and the driver never diverge.
    ref = np.zeros((8, 6, 2), dtype=np.uint8)
    ref[..., 0] = (arr[..., 0] & 0xF8) | (arr[..., 1] >> 5)
    ref[..., 1] = ((arr[..., 1] << 3) & 0xE0) | (arr[..., 2] >> 3)
    assert packed == ref.tobytes()


def test_pack_rgb565_known_colors():
    # Pure red -> 0xF800, green -> 0x07E0, blue -> 0x001F (big-endian bytes).
    red = compositor.pack_rgb565(np.array([[[255, 0, 0]]], dtype=np.uint8))
    green = compositor.pack_rgb565(np.array([[[0, 255, 0]]], dtype=np.uint8))
    blue = compositor.pack_rgb565(np.array([[[0, 0, 255]]], dtype=np.uint8))
    assert red == bytes([0xF8, 0x00])
    assert green == bytes([0x07, 0xE0])
    assert blue == bytes([0x00, 0x1F])


def test_dominant_color_solid_image():
    img = np.full((10, 10, 3), (30, 120, 200), dtype=np.uint8)
    assert compositor.dominant_color(img) == (30, 120, 200)


def test_dominant_color_ignores_transparent_pixels():
    # Half opaque red, half transparent green. With the alpha mask the
    # dominant colour should be red (transparent green is ignored), not a
    # muddy average of the two.
    rgb = np.zeros((2, 2, 3), dtype=np.uint8)
    rgb[0] = (200, 0, 0)   # opaque row
    rgb[1] = (0, 200, 0)   # transparent row
    alpha = np.array([[255, 255], [0, 0]], dtype=np.uint8)
    assert compositor.dominant_color(rgb, alpha) == (200, 0, 0)


def test_dominant_color_fully_transparent_falls_back_to_unmasked_mean():
    rgb = np.full((4, 4, 3), (80, 80, 80), dtype=np.uint8)
    alpha = np.zeros((4, 4), dtype=np.uint8)  # nothing opaque
    # Falls back to the unmasked mean rather than dividing by zero.
    assert compositor.dominant_color(rgb, alpha) == (80, 80, 80)


def test_dominant_color_rejects_mismatched_alpha():
    rgb = np.zeros((3, 3, 3), dtype=np.uint8)
    with pytest.raises(ValueError):
        compositor.dominant_color(rgb, np.zeros((2, 2), dtype=np.uint8))


def test_scale_color_brightens_and_darkens_with_clipping():
    assert compositor.scale_color((100, 100, 100), 0.5) == (50, 50, 50)
    # Brighten past 255 clips.
    assert compositor.scale_color((200, 10, 0), 2.0) == (255, 20, 0)


def test_horizontal_edge_fade_interior_is_pure_region():
    # A wide interior (no fade) copies the region verbatim over the base.
    base = compositor.solid_rgb(20, 3, (0, 0, 0))
    region = compositor.solid_rgb(20, 3, (255, 255, 255))
    out = compositor.horizontal_edge_fade(base, region, 4, 4)
    # A column well inside both ramps is fully the region colour.
    assert (out[:, 10] == [255, 255, 255]).all()


def test_horizontal_edge_fade_edges_ramp_to_base():
    base = compositor.solid_rgb(20, 1, (0, 0, 0))
    region = compositor.solid_rgb(20, 1, (255, 255, 255))
    out = compositor.horizontal_edge_fade(base, region, 4, 4)
    # The very first / last columns sit at alpha 0 -> pure base (black).
    assert (out[0, 0] == [0, 0, 0]).all()
    assert (out[0, -1] == [0, 0, 0]).all()
    # The left ramp is monotonically non-decreasing toward the interior.
    left = out[0, :4, 0].astype(int)
    assert (np.diff(left) >= 0).all()


def test_horizontal_edge_fade_zero_widths_is_full_region():
    base = compositor.solid_rgb(6, 2, (10, 10, 10))
    region = compositor.solid_rgb(6, 2, (200, 200, 200))
    out = compositor.horizontal_edge_fade(base, region, 0, 0)
    assert (out == region).all()


def test_horizontal_edge_fade_rejects_mismatched_shapes():
    base = compositor.solid_rgb(6, 2, (0, 0, 0))
    region = compositor.solid_rgb(5, 2, (0, 0, 0))
    with pytest.raises(ValueError):
        compositor.horizontal_edge_fade(base, region, 1, 1)

