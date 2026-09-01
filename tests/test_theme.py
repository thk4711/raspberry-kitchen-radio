"""Unit tests for the pure ``theme`` module (Workstream 5).

No Pillow, numpy or hardware — the theme layer is pure string/number coercion,
so these run on any machine (consistent with ``test_layout``/``test_textformat``).
"""
from display_1_inch_69 import theme


def test_build_theme_none_is_all_defaults():
    assert theme.build_theme(None) == theme.Theme()


def test_build_theme_empty_is_all_defaults():
    assert theme.build_theme({}) == theme.Theme()


def test_partial_override_keeps_other_defaults():
    d = theme.Theme()
    t = theme.build_theme({"scrim_opacity": "0.8"})
    assert t.scrim_opacity == 0.8
    # Everything else stays at the default.
    assert t.text_color == d.text_color
    assert t.top_band_height == d.top_band_height


class TestParseColor:
    def test_hex(self):
        assert theme.parse_color("#FF8800", (0, 0, 0)) == (255, 136, 0)

    def test_hex_lowercase(self):
        assert theme.parse_color("#00ff00", (0, 0, 0)) == (0, 255, 0)

    def test_comma_triple(self):
        assert theme.parse_color("10, 20, 30", (0, 0, 0)) == (10, 20, 30)

    def test_comma_triple_clamped(self):
        assert theme.parse_color("300, -5, 40", (0, 0, 0)) == (255, 0, 40)

    def test_named(self):
        assert theme.parse_color("WHITE", (0, 0, 0)) == (255, 255, 255)
        assert theme.parse_color("black", (9, 9, 9)) == (0, 0, 0)

    def test_existing_tuple_passthrough(self):
        assert theme.parse_color((1, 2, 3), (0, 0, 0)) == (1, 2, 3)

    def test_invalid_hex_falls_back(self):
        assert theme.parse_color("#ZZZ", (7, 7, 7)) == (7, 7, 7)

    def test_bad_length_hex_falls_back(self):
        assert theme.parse_color("#FFF", (7, 7, 7)) == (7, 7, 7)

    def test_unrecognised_falls_back(self):
        assert theme.parse_color("chartreuse", (7, 7, 7)) == (7, 7, 7)

    def test_none_falls_back(self):
        assert theme.parse_color(None, (7, 7, 7)) == (7, 7, 7)


class TestParseFloat:
    def test_valid(self):
        assert theme.parse_float("0.42", 0.1, 0.0, 1.0) == 0.42

    def test_clamped_high(self):
        assert theme.parse_float("5", 0.1, 0.0, 1.0) == 1.0

    def test_clamped_low(self):
        assert theme.parse_float("-1", 0.1, 0.0, 1.0) == 0.0

    def test_invalid_falls_back(self):
        assert theme.parse_float("abc", 0.33, 0.0, 1.0) == 0.33


class TestParseInt:
    def test_valid(self):
        assert theme.parse_int("42", 10) == 42

    def test_int_passthrough(self):
        # read_config already coerces pure-digit strings to int.
        assert theme.parse_int(42, 10) == 42

    def test_clamped(self):
        assert theme.parse_int("-3", 10, lo=0, hi=100) == 0
        assert theme.parse_int("999", 10, lo=0, hi=100) == 100

    def test_invalid_falls_back(self):
        assert theme.parse_int("x", 10) == 10


class TestParseBool:
    def test_bool_passthrough(self):
        assert theme.parse_bool(True, False) is True
        assert theme.parse_bool(False, True) is False

    def test_truthy_strings(self):
        for v in ("true", "1", "yes", "on", "TRUE"):
            assert theme.parse_bool(v, False) is True

    def test_falsey_strings(self):
        for v in ("false", "0", "no", "off", "FALSE"):
            assert theme.parse_bool(v, True) is False

    def test_invalid_falls_back(self):
        assert theme.parse_bool("maybe", True) is True


def test_animations_off_zeroes_motion():
    t = theme.build_theme({"animations": False})
    assert t.animations is False
    assert t.crossfade_ms == 0
    assert t.edge_fade_px == 0


def test_animations_on_keeps_motion_defaults():
    d = theme.Theme()
    t = theme.build_theme({"animations": True})
    assert t.crossfade_ms == d.crossfade_ms
    assert t.edge_fade_px == d.edge_fade_px


def test_invalid_values_fall_back_per_key():
    t = theme.build_theme({
        "scrim_opacity": "nonsense",
        "text_color": "notacolor",
        "title_size": "huge",
    })
    d = theme.Theme()
    assert t.scrim_opacity == d.scrim_opacity
    assert t.text_color == d.text_color
    assert t.title_size == d.title_size
