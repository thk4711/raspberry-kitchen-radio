"""Unit tests for the pure ``logo_fallback`` module (Workstream 6.3).

Pillow only, no hardware — the generated initials tile is deterministic in the
station name, so these run on any machine.
"""

from display import logo_fallback
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
        assert logo_fallback.tile_color("MDR Jump") == logo_fallback.tile_color("  mdr jump ")

    def test_different_names_usually_differ(self):
        assert logo_fallback.tile_color("KEXP") != logo_fallback.tile_color("MDR JUMP")

    def test_returns_rgb_triple_in_range(self):
        c = logo_fallback.tile_color("Deutschlandfunk")
        assert len(c) == 3
        assert all(0 <= ch <= 255 for ch in c)


def _font(size=40):
    return ImageFont.truetype("lib/display/fonts/Roboto-Condensed-Bold.ttf", size)


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
        tile = logo_fallback.render_initials_tile("X", 60, _font(24), bg_color=(10, 20, 30))
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
        # A corner point inside the rounded rect but away from the centred glyph
        # is the blue tile background, not the white rune.
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

    def test_draws_glyph_over_background(self):
        # The rune is drawn in glyph_color, so some pixels differ from the plain
        # background (i.e. the tile is not a flat blue square).
        tile = logo_fallback.render_bluetooth_tile(120, glyph_color=(255, 255, 255))
        colors = {tile.getpixel((x, y))[:3] for x in range(0, 120, 2) for y in range(0, 120, 2)}
        assert (255, 255, 255) in colors
        assert logo_fallback.BLUETOOTH_TILE_COLOR in colors

    def test_explicit_bg_color_used(self):
        tile = logo_fallback.render_bluetooth_tile(60, bg_color=(10, 20, 30))
        assert tile.getpixel((5, 30))[:3] == (10, 20, 30)

    def test_min_size_does_not_crash(self):
        tile = logo_fallback.render_bluetooth_tile(1)
        assert tile.size == (1, 1)


class TestRenderUsbTile:
    def test_size_and_mode(self):
        tile = logo_fallback.render_usb_tile(120)
        assert tile.size == (120, 120)
        assert tile.mode == "RGBA"

    def test_uses_teal_background(self):
        tile = logo_fallback.render_usb_tile(120)
        # A corner point inside the rounded rect but away from the centred glyph
        # is the teal tile background, not the white glyph.
        assert tile.getpixel((10, 10))[:3] == logo_fallback.USB_TILE_COLOR

    def test_has_opaque_and_transparent_pixels(self):
        tile = logo_fallback.render_usb_tile(120)
        alpha = tile.split()[3]
        lo, hi = alpha.getextrema()
        assert lo == 0 and hi == 255

    def test_deterministic_bytes(self):
        a = logo_fallback.render_usb_tile(100)
        b = logo_fallback.render_usb_tile(100)
        assert a.tobytes() == b.tobytes()

    def test_draws_glyph_over_background(self):
        tile = logo_fallback.render_usb_tile(120, glyph_color=(255, 255, 255))
        colors = {tile.getpixel((x, y))[:3] for x in range(0, 120, 2) for y in range(0, 120, 2)}
        assert (255, 255, 255) in colors
        assert logo_fallback.USB_TILE_COLOR in colors

    def test_explicit_bg_color_used(self):
        tile = logo_fallback.render_usb_tile(60, bg_color=(10, 20, 30))
        assert tile.getpixel((5, 30))[:3] == (10, 20, 30)

    def test_min_size_does_not_crash(self):
        tile = logo_fallback.render_usb_tile(1)
        assert tile.size == (1, 1)
