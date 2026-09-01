"""Unit tests for the pure ``logo_fallback`` module (Workstream 6.3).

Pillow only, no hardware — the generated initials tile is deterministic in the
station name, so these run on any machine.
"""
from display_1_inch_69 import logo_fallback
from PIL import ImageFont


class TestInitials:
    def test_two_words(self):
        assert logo_fallback.initials("MDR JUMP") == "MJ"

    def test_multiword_takes_first_two(self):
        assert logo_fallback.initials("Deutschlandfunk Nova") == "DN"

    def test_three_words_capped_at_two(self):
        assert logo_fallback.initials("Bayerischer Rundfunk Drei") == "BR"

    def test_single_word_takes_first_two_letters(self):
        assert logo_fallback.initials("KEXP") == "KE"

    def test_single_short_word(self):
        assert logo_fallback.initials("Q") == "Q"

    def test_blank_is_question_mark(self):
        assert logo_fallback.initials("   ") == "?"
        assert logo_fallback.initials("") == "?"

    def test_max_len_one(self):
        assert logo_fallback.initials("MDR JUMP", max_len=1) == "M"

    def test_ignores_punctuation_word(self):
        # "!!!" has no alphanumerics and is dropped, leaving one real word.
        assert logo_fallback.initials("!!! Party") == "PA"

    def test_uppercased(self):
        assert logo_fallback.initials("radio eins") == "RE"


class TestTileColor:
    def test_deterministic(self):
        assert logo_fallback.tile_color("KEXP") == logo_fallback.tile_color("KEXP")

    def test_case_and_space_insensitive(self):
        assert (logo_fallback.tile_color("MDR Jump")
                == logo_fallback.tile_color("  mdr jump "))

    def test_different_names_usually_differ(self):
        assert logo_fallback.tile_color("KEXP") != logo_fallback.tile_color("MDR JUMP")

    def test_returns_rgb_triple_in_range(self):
        c = logo_fallback.tile_color("Deutschlandfunk")
        assert len(c) == 3
        assert all(0 <= ch <= 255 for ch in c)


def _font(size=40):
    return ImageFont.truetype(
        "lib/display_1_inch_69/fonts/Roboto-Condensed-Bold.ttf", size)


class TestRenderTile:
    def test_size_and_mode(self):
        tile = logo_fallback.render_initials_tile("KEXP", 120, _font())
        assert tile.size == (120, 120)
        assert tile.mode == "RGBA"

    def test_has_opaque_and_transparent_pixels(self):
        # Rounded corners leave transparent pixels; the body is opaque.
        tile = logo_fallback.render_initials_tile("KEXP", 120, _font())
        alpha = tile.split()[3]
        lo, hi = alpha.getextrema()
        assert lo == 0 and hi == 255

    def test_deterministic_bytes(self):
        a = logo_fallback.render_initials_tile("MDR JUMP", 100, _font())
        b = logo_fallback.render_initials_tile("MDR JUMP", 100, _font())
        assert a.tobytes() == b.tobytes()

    def test_explicit_bg_color_used(self):
        tile = logo_fallback.render_initials_tile(
            "X", 60, _font(24), bg_color=(10, 20, 30))
        # The centre pixel is inside the rounded rect -> the bg colour.
        assert tile.getpixel((30, 5))[:3] == (10, 20, 30)

    def test_min_size_does_not_crash(self):
        tile = logo_fallback.render_initials_tile("KEXP", 1, _font(6))
        assert tile.size == (1, 1)


class TestRenderBluetoothTile:
    def test_size_and_mode(self):
        tile = logo_fallback.render_bluetooth_tile(120)
        assert tile.size == (120, 120)
        assert tile.mode == "RGBA"

    def test_uses_blue_background(self):
        tile = logo_fallback.render_bluetooth_tile(120)
        # A point inside the rounded rect but away from the centred glyph is
        # the blue tile background, not the white rune.
        assert tile.getpixel((10, 10))[:3] == logo_fallback.BLUETOOTH_TILE_COLOR

    def test_has_opaque_and_transparent_pixels(self):
        # Rounded corners leave transparent pixels; the body is opaque.
        tile = logo_fallback.render_bluetooth_tile(120)
        alpha = tile.split()[3]
        lo, hi = alpha.getextrema()
        assert lo == 0 and hi == 255

    def test_deterministic_bytes(self):
        a = logo_fallback.render_bluetooth_tile(100)
        b = logo_fallback.render_bluetooth_tile(100)
        assert a.tobytes() == b.tobytes()

    def test_has_vertical_spine(self):
        # The central stem must be drawn (regression: an earlier polyline drew
        # the two bows but omitted the vertical spine, leaving an "X"). Sample
        # the actual spine column derived from the official path geometry.
        size = 200
        tile = logo_fallback.render_bluetooth_tile(size, glyph_color=(255, 255, 255))
        xs = [p[0] for p in logo_fallback._BT_PATH]
        ys = [p[1] for p in logo_fallback._BT_PATH]
        gx0, gx1 = min(xs), max(xs)
        gy0, gy1 = min(ys), max(ys)
        scale = (size * 0.72) / (gy1 - gy0)  # tall glyph -> height-limited
        off_x = (size - (gx1 - gx0) * scale) / 2.0 - gx0 * scale
        off_y = (size - (gy1 - gy0) * scale) / 2.0 - gy0 * scale
        spine_x = int(315 * scale + off_x)   # spine vertices are at x=315
        # Every point down the spine's vertical extent is the white glyph.
        for gy in (200, 350, 500, 650, 790):
            py = int(gy * scale + off_y)
            assert tile.getpixel((spine_x, py))[:3] == (255, 255, 255)

    def test_bows_are_asymmetric_left_and_right(self):
        # The official mark reaches right (tips) and left (knees) of the spine;
        # both the upper-right and lower-left quadrants must contain glyph pixels.
        size = 200
        tile = logo_fallback.render_bluetooth_tile(size, glyph_color=(255, 255, 255))
        white = (255, 255, 255)
        upper_right = any(tile.getpixel((x, y))[:3] == white
                          for x in range(size // 2, size)
                          for y in range(0, size // 2))
        lower_left = any(tile.getpixel((x, y))[:3] == white
                         for x in range(0, size // 2)
                         for y in range(size // 2, size))
        assert upper_right and lower_left

    def test_draws_glyph_over_background(self):
        # The rune is drawn in glyph_color, so some pixels differ from the plain
        # background (i.e. the tile is not a flat blue square).
        tile = logo_fallback.render_bluetooth_tile(120, glyph_color=(255, 255, 255))
        colors = {tile.getpixel((x, y))[:3]
                  for x in range(0, 120, 4) for y in range(0, 120, 4)}
        assert (255, 255, 255) in colors
        assert logo_fallback.BLUETOOTH_TILE_COLOR in colors

    def test_explicit_bg_color_used(self):
        tile = logo_fallback.render_bluetooth_tile(60, bg_color=(10, 20, 30))
        assert tile.getpixel((5, 30))[:3] == (10, 20, 30)

    def test_min_size_does_not_crash(self):
        tile = logo_fallback.render_bluetooth_tile(1)
        assert tile.size == (1, 1)

