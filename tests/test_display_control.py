"""Behavioural tests for the full-frame ``DisplayController`` (Workstream 1).

These exercise the rendering-architecture invariants without a Raspberry Pi:

* one composed frame is a full 240x280 image;
* pushes are single full-frame writes, deduplicated by content unless forced;
* metadata changes flag the frame dirty; overflowing text marks a row
  scrolling (the animation gate); idle recomposition emits no SPI write.

The ST7789 driver is replaced with a tiny recording fake, and the compositor
thread is prevented from auto-starting so the loop can be stepped
deterministically. numpy + Pillow are target runtime deps installed by
``requirements-dev.txt``.
"""
import sys
import types

import pytest
from display_1_inch_69 import compositor, logo_fallback
from PIL import Image


class _FakePanel:
    """Records full-frame writes instead of touching SPI/GPIO."""

    width = 240
    height = 280

    def __init__(self, *args, **kwargs):
        self.frames = []
        self.backlight = None

    def Init(self):
        pass

    def bl_DutyCycle(self, duty):
        self.backlight = duty

    def clear(self):
        pass

    def ShowFullFrame(self, pix):
        expected = self.width * self.height * 2
        assert len(pix) == expected, f"expected {expected} bytes, got {len(pix)}"
        self.frames.append(pix)


@pytest.fixture
def controller(monkeypatch):
    """Build a DisplayController with a fake panel and no live thread.

    The real driver module is swapped for a fake exposing ``LCD_1inch69``, and
    ``threading.Thread`` is neutralised inside ``display_control`` so the
    compositor loop does not run on its own; tests drive it explicitly.
    """
    fake_driver = types.ModuleType("display_1_inch_69.LCD_1inch69")
    fake_driver.LCD_1inch69 = _FakePanel
    monkeypatch.setitem(sys.modules, "display_1_inch_69.LCD_1inch69", fake_driver)

    # Import (or re-import) the controller against the fake driver.
    import importlib

    import display_1_inch_69.display_control as dc
    dc = importlib.reload(dc)

    class _InertThread:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            pass

    monkeypatch.setattr(dc.threading, "Thread", _InertThread)

    # Pin the monotonic clock to a fixed baseline *before* constructing the
    # controller so TransientState.last_activity (seeded from monotonic() in
    # __init__) is host-independent. Without this the seed is the real process
    # uptime, so idle/screensaver predicates behave differently on a fresh CI
    # runner (small uptime) than on a long-lived dev box, making some tests
    # flaky. Individual tests re-patch dc.monotonic to drive their own timeline.
    monkeypatch.setattr(dc, "monotonic", lambda: 0.0)

    ctrl = dc.DisplayController()
    return ctrl


def test_initial_frame_is_full_240x280(controller):
    frame = controller._render_frame()
    assert frame.size == (controller.width, controller.height) == (240, 280)
    assert frame.mode == "RGB"


def test_init_pushes_exactly_one_full_frame(controller):
    # __init__ paints one initial background frame (force=True).
    assert len(controller.disp.frames) == 1
    assert len(controller.disp.frames[0]) == 240 * 280 * 2


def test_push_frame_dedupes_identical_content(controller):
    controller.disp.frames.clear()
    controller._last_frame_sig = None
    frame = controller._render_frame()
    assert controller._push_frame(frame) is True          # first push transmits
    assert controller._push_frame(frame) is False         # identical -> no write
    assert len(controller.disp.frames) == 1


def test_push_frame_force_transmits_identical_content(controller):
    controller.disp.frames.clear()
    frame = controller._render_frame()
    controller._push_frame(frame)
    n = len(controller.disp.frames)
    assert controller._push_frame(frame, force=True) is True
    assert len(controller.disp.frames) == n + 1


def test_update_metadata_sets_dirty(controller):
    controller._dirty = False
    controller.update_metadata("Radio", "Song", "", "0")
    assert controller._dirty is True


def test_update_metadata_no_change_keeps_clean(controller):
    controller.update_metadata("Radio", "Song", "", "abc")
    controller._dirty = False
    controller.update_metadata("Radio", "Song", "", "abc")
    assert controller._dirty is False


def test_short_text_does_not_scroll(controller):
    controller.update_metadata("OK", "OK", "", "0")
    controller._render_frame()  # measures + sets scrolling flags
    assert controller._any_scrolling() is False


def test_long_text_marks_row_scrolling(controller):
    long_title = "A very very very long track title that definitely overflows"
    controller.update_metadata("Radio", long_title, "", "0")
    controller._render_frame()
    assert controller.metadata["title"]["scrolling"] is True
    assert controller._any_scrolling() is True


def test_scrolling_advances_position_each_render(controller):
    long_title = "A very very very long track title that definitely overflows"
    controller.update_metadata("Radio", long_title, "", "0")
    controller._render_frame()
    first = controller.metadata["title"]["position"]
    controller._render_frame()
    second = controller.metadata["title"]["position"]
    assert second != first


def test_render_frame_packs_to_expected_byte_length(controller):
    import numpy as np
    frame = controller._render_frame()
    packed = compositor.pack_rgb565(np.asarray(frame))
    assert len(packed) == 240 * 280 * 2


def test_toggle_backlight_controls_duty_and_dirty(controller):
    controller._dirty = False
    controller.toggle_backlight(True)
    assert controller.disp.backlight == 100
    assert controller._dirty is True
    controller.toggle_backlight(False)
    assert controller.disp.backlight == 0


def test_radio_mode_builds_backdrop_and_caches_art_layer(controller, tmp_path):
    # A radio station logo drives a dominant-colour backdrop + centred logo.
    # The composed static art layer is cached by (cover, md5, art_mode).
    logo_path = tmp_path / "logo.png"
    Image.new("RGBA", (160, 120), (10, 200, 40, 255)).save(logo_path)

    controller.update_metadata("Radio", "Song", str(logo_path), "hash1",
                               art_mode="radio")
    frame = controller._render_frame()
    assert frame.size == (240, 280)

    cached = controller._art_layer
    assert cached is not None
    assert cached.size == (240, 280)

    # Re-composing with the same cover reuses the cached art layer object.
    controller._render_frame()
    assert controller._art_layer is cached


def _luminance(px):
    r, g, b = px[:3]
    return 0.213 * r + 0.715 * g + 0.072 * b


def test_radio_backdrop_is_darker_than_bright_logo(controller, tmp_path):
    # WS8: the backdrop is deliberately darker than the (bright) logo so the
    # centred tile stands out instead of washing into a same-colour field.
    # A bright, fully-opaque logo tile must render its centre clearly brighter
    # than the surrounding backdrop just outside the logo box.
    logo_path = tmp_path / "bright.png"
    Image.new("RGBA", (300, 300), (60, 150, 240, 255)).save(logo_path)

    controller.update_metadata("Radio", "", str(logo_path), "bright1",
                               state=True, art_mode="radio", source="mpd")
    controller._transient.clear_crossfade()
    controller._transient.crossfade_until = 0.0
    art = controller._build_art_layer()

    box = controller.logo_box
    centre_lum = _luminance(art.getpixel((box.cx, box.cy)))
    # Sample the backdrop just left of the logo box, still in the un-scrimmed
    # middle band (not under the darkened top/bottom chrome).
    edge_x = max(0, box.x - 2)
    backdrop_lum = _luminance(art.getpixel((edge_x, box.cy)))
    # The tile should be clearly brighter than its surrounding wash. The old
    # 1.15/0.35 formula left this gap tiny; the darker backdrop widens it.
    assert centre_lum - backdrop_lum > 40


def test_cover_mode_fills_full_frame(controller, tmp_path):
    # A real album cover renders full-bleed (fill + crop) to the whole panel.
    cover_path = tmp_path / "cover.jpg"
    Image.new("RGB", (600, 600), (200, 30, 30)).save(cover_path)

    controller.update_metadata("Artist", "Track", str(cover_path), "c1",
                               art_mode="cover")
    art = controller._build_art_layer()
    assert art.size == (240, 280)

    # A center pixel (outside the scrim bands) is dominated by the cover colour
    # (red channel clearly highest), i.e. the art is actually full-bleed.
    r, g, b = art.getpixel((120, 140))
    assert r > g and r > b


def test_scrim_darkens_bottom_band_vs_center(controller, tmp_path):
    # The bottom chrome band must be visibly darker than the un-scrimmed centre
    # so text stays legible over bright art.
    cover_path = tmp_path / "bright.jpg"
    Image.new("RGB", (600, 600), (240, 240, 240)).save(cover_path)
    controller.update_metadata("Artist", "Track", str(cover_path), "c2",
                               art_mode="cover")
    art = controller._build_art_layer()

    bottom = controller.layout.bottom_band
    center_lum = sum(art.getpixel((120, 140)))
    band_lum = sum(art.getpixel((120, bottom.cy)))
    assert band_lum < center_lum


def test_missing_cover_file_degrades_without_raising(controller, tmp_path):
    controller.update_metadata("Radio", "Song", str(tmp_path / "nope.png"), "x",
                               art_mode="radio")
    # Must not raise; composes a backdrop-only frame.
    frame = controller._render_frame()
    assert frame.size == (240, 280)


def test_art_mode_change_flags_dirty(controller, tmp_path):
    cover_path = tmp_path / "c.png"
    Image.new("RGBA", (160, 120), (10, 10, 200, 255)).save(cover_path)
    controller.update_metadata("Radio", "Song", str(cover_path), "m1",
                               art_mode="radio")
    controller._dirty = False
    controller.update_metadata("Radio", "Song", str(cover_path), "m1",
                               art_mode="cover")
    assert controller._dirty is True


def test_state_forwarding_updates_metadata(controller):
    controller.update_metadata("Radio", "Song", "", "0", state=True)
    assert controller.metadata["state"] is True
    controller._dirty = False
    # Same state again -> no dirty flip.
    controller.update_metadata("Radio", "Song", "", "0", state=True)
    assert controller._dirty is False




# --- Workstream 3: typography & status strip -------------------------------

def test_bold_title_font_is_actually_bold(controller):
    # The title font loaded from Roboto-Condensed-Bold.ttf reports Bold style.
    name = controller.font_title.getname()
    assert "Bold" in name[1] or name == ('Roboto Condensed', 'Bold')


def test_controller_has_three_distinct_fonts(controller):
    assert controller.font_title is not None
    assert controller.font_artist is not None
    assert controller.font_small is not None
    # Title (bold) and artist (regular) are different font objects.
    assert controller.font_title is not controller.font_artist


def test_artist_title_split_populates_rows_radio(controller):
    controller.update_metadata("Deutschlandfunk", "Coldplay - Yellow", "", "0",
                               art_mode="radio")
    assert controller.metadata["title"]["text"] == "Yellow"
    assert controller.metadata["name"]["text"] == "Coldplay"


def test_artist_title_split_populates_rows_cover(controller):
    controller.update_metadata("Daft Punk", "One More Time", "", "0",
                               art_mode="cover")
    assert controller.metadata["title"]["text"] == "One More Time"
    assert controller.metadata["name"]["text"] == "Daft Punk"


def test_source_change_flags_dirty(controller):
    controller.update_metadata("A", "B", "", "0", source="mpd")
    controller._dirty = False
    controller.update_metadata("A", "B", "", "0", source="spotify")
    assert controller._dirty is True


def test_play_vs_pause_glyph_differs(controller):
    from PIL import ImageDraw
    # Render the status strip twice (playing vs paused) and confirm the
    # right-hand glyph region differs.
    def strip_region(playing):
        controller.update_metadata("Radio", "Song", "", "0", state=playing,
                                    source="mpd", art_mode="radio")
        frame = controller._build_art_layer().copy()
        draw = ImageDraw.Draw(frame)
        controller._draw_status_strip(draw)
        band = controller.layout.top_band
        # The play/pause glyph is anchored near the right of the safe area (it
        # spreads outward from the inner band toward the corners, clamped to the
        # safe edge — see _draw_status_strip). Crop the right slice of the safe
        # width so we always capture the glyph in both play and pause states.
        right = controller.layout.safe.right
        return frame.crop((right - 24, band.y, right, band.bottom))

    playing = strip_region(True).tobytes()
    paused = strip_region(False).tobytes()
    assert playing != paused


def test_status_strip_renders_within_top_band(controller):
    from PIL import ImageDraw
    controller.update_metadata("Radio", "Song", "", "0", state=True,
                               source="spotify", art_mode="cover")
    frame = controller._build_art_layer().copy()
    draw = ImageDraw.Draw(frame)
    # Must not raise and must keep the frame the right size.
    controller._draw_status_strip(draw)
    assert frame.size == (240, 280)


def test_clock_string_change_would_flag_dirty(controller):
    # Simulate a minute rollover the way the loop does.
    controller._last_clock = "00:00"
    controller._dirty = False
    clock = "00:01"
    if clock != controller._last_clock:
        controller._last_clock = clock
        controller._dirty = True
    assert controller._dirty is True


# --- Workstream 4: motion & states ----------------------------------------

def test_show_volume_sets_deadline_and_dirty(controller, monkeypatch):
    import display_1_inch_69.display_control as dc
    monkeypatch.setattr(dc, "monotonic", lambda: 1000.0)
    controller._dirty = False
    controller.show_volume(42)
    assert controller._transient.volume_pct == 42
    assert controller._dirty is True
    # Deadline is now + _OSD_DURATION and the OSD reports visible.
    assert controller._transient.osd_until == pytest.approx(1000.0 + dc._OSD_DURATION)
    assert controller._osd_visible() is True


def test_show_volume_clamps_to_0_100(controller):
    controller.show_volume(250)
    assert controller._transient.volume_pct == 100
    controller.show_volume(-10)
    assert controller._transient.volume_pct == 0


def test_osd_expires_after_duration(controller, monkeypatch):
    import display_1_inch_69.display_control as dc
    t = {"now": 1000.0}
    monkeypatch.setattr(dc, "monotonic", lambda: t["now"])
    controller.show_volume(50)
    assert controller._osd_visible() is True
    # Advance time past the OSD window.
    t["now"] = 1000.0 + dc._OSD_DURATION + 0.01
    assert controller._osd_visible() is False


def test_render_shows_osd_bar_and_hides_text_rows(controller, monkeypatch):
    import display_1_inch_69.display_control as dc
    monkeypatch.setattr(dc, "monotonic", lambda: 5000.0)
    # A very long title would normally scroll; with the OSD up it must not be
    # advanced (the OSD replaces the rows), so the frame differs from the plain
    # now-playing frame.
    long_title = "A very very very long track title that definitely overflows"
    controller.update_metadata("Radio", long_title, "", "0", art_mode="radio")
    # Baseline now-playing frame (OSD not shown yet).
    controller._transient.osd_until = 0.0
    plain = controller._render_frame().tobytes()
    # Now show the OSD and re-render.
    controller.show_volume(75)
    osd_frame = controller._render_frame()
    assert osd_frame.size == (240, 280)
    assert osd_frame.tobytes() != plain


def test_render_osd_fill_reflects_volume(controller, monkeypatch):
    import display_1_inch_69.display_control as dc
    monkeypatch.setattr(dc, "monotonic", lambda: 6000.0)
    controller.update_metadata("Radio", "Song", "", "0", art_mode="radio")
    controller.show_volume(10)
    low = controller._render_frame().tobytes()
    controller.show_volume(90)
    high = controller._render_frame().tobytes()
    # Different fill widths -> visibly different frames.
    assert low != high


def test_scrolling_row_edge_fade_does_not_raise(controller):
    # A scrolling row now composites through the numpy edge-fade path; make
    # sure the full pipeline renders a valid full frame.
    long_title = "Another extremely long track title that overflows the row width"
    controller.update_metadata("Radio", long_title, "", "0", art_mode="radio")
    frame = controller._render_frame()
    assert frame.size == (240, 280)
    assert controller.metadata["title"]["scrolling"] is True


def test_boot_splash_is_the_single_initial_frame(controller):
    # __init__ pushes exactly one frame and it is the branded splash.
    assert len(controller.disp.frames) == 1
    import numpy as np
    from display_1_inch_69 import compositor
    splash = compositor.pack_rgb565(np.asarray(controller._render_splash()))
    assert controller.disp.frames[0] == splash


def test_show_toast_sets_deadline_and_dismisses_screensaver(controller, monkeypatch):
    import display_1_inch_69.display_control as dc
    monkeypatch.setattr(dc, "monotonic", lambda: 2000.0)
    controller.show_toast("MDR JUMP")
    assert controller._transient.toast_text == "MDR JUMP"
    assert controller._toast_visible() is True
    # A preset press counts as activity, so it resets the idle timer.
    assert controller._transient.last_activity == 2000.0


def test_blank_toast_is_not_visible(controller):
    controller.show_toast("")
    assert controller._toast_visible() is False


def test_toast_expires_after_duration(controller, monkeypatch):
    import display_1_inch_69.display_control as dc
    t = {"now": 3000.0}
    monkeypatch.setattr(dc, "monotonic", lambda: t["now"])
    controller.show_toast("Preset")
    assert controller._toast_visible() is True
    t["now"] = 3000.0 + dc._TOAST_DURATION + 0.01
    assert controller._toast_visible() is False


def test_toast_changes_the_rendered_frame(controller, monkeypatch):
    import display_1_inch_69.display_control as dc
    monkeypatch.setattr(dc, "monotonic", lambda: 4000.0)
    controller.update_metadata("MDR JUMP", "", "", "0", state=True,
                               art_mode="radio", source="mpd")
    plain = controller._render_frame().tobytes()
    controller.show_toast("MDR JUMP")
    with_toast = controller._render_frame()
    assert with_toast.size == (240, 280)
    assert with_toast.tobytes() != plain


def test_screensaver_activates_after_idle_timeout(controller, monkeypatch):
    import display_1_inch_69.display_control as dc
    t = {"now": 5000.0}
    monkeypatch.setattr(dc, "monotonic", lambda: t["now"])
    # Not playing; last activity long ago.
    controller.update_metadata("Radio", "", "", "0", state=False, art_mode="radio")
    controller._transient.last_activity = 5000.0
    assert controller._screensaver_active() is False
    t["now"] = 5000.0 + dc._IDLE_TIMEOUT + 1
    assert controller._screensaver_active() is True
    frame = controller._render_frame()
    assert frame.size == (240, 280)


def test_playing_prevents_screensaver(controller, monkeypatch):
    import display_1_inch_69.display_control as dc
    monkeypatch.setattr(dc, "monotonic", lambda: 6000.0)
    controller.update_metadata("Radio", "Song", "", "0", state=True, art_mode="radio")
    controller._transient.last_activity = 0.0  # long ago, but we are playing
    assert controller._screensaver_active() is False


def test_osd_suppresses_screensaver(controller, monkeypatch):
    import display_1_inch_69.display_control as dc
    t = {"now": 7000.0}
    monkeypatch.setattr(dc, "monotonic", lambda: t["now"])
    controller.update_metadata("Radio", "", "", "0", state=False, art_mode="radio")
    controller._transient.last_activity = 0.0
    controller.show_volume(40)  # OSD up
    # OSD wins over the idle screensaver.
    assert controller._screensaver_active() is False


def test_art_change_starts_crossfade(controller, monkeypatch):
    import display_1_inch_69.display_control as dc
    monkeypatch.setattr(dc, "monotonic", lambda: 8000.0)
    # First art establishes the cache (no crossfade yet).
    controller.update_metadata("A", "", "", "md5-a", art_mode="radio")
    controller._build_art_layer()
    assert controller._transient.crossfade_from is None
    # A second, different art layer starts a crossfade from the old one.
    controller.update_metadata("B", "", "", "md5-b", art_mode="radio")
    controller._build_art_layer()
    assert controller._transient.crossfade_from is not None
    assert controller._transient.crossfade_until == pytest.approx(8000.0 + dc._CROSSFADE_MS / 1000.0)
    assert controller._crossfade_active() is True


def test_crossfade_clears_after_window(controller, monkeypatch):
    import display_1_inch_69.display_control as dc
    t = {"now": 9000.0}
    monkeypatch.setattr(dc, "monotonic", lambda: t["now"])
    # Keep the idle screensaver deterministically OFF: seed the activity
    # baseline to "now" so _render_frame composes the now-playing frame (which
    # clears a finished crossfade) instead of the screensaver branch, which is
    # what this test asserts. See test_screensaver_activates_after_idle_timeout
    # for the same explicit-baseline pattern.
    controller._transient.last_activity = 9000.0
    controller.update_metadata("A", "", "", "md5-a", art_mode="radio")
    controller._build_art_layer()
    controller.update_metadata("B", "", "", "md5-b", art_mode="radio")
    controller._build_art_layer()
    assert controller._crossfade_active() is True
    # After the window, a compose falls back to the plain art and clears state.
    t["now"] = 9000.0 + dc._CROSSFADE_MS / 1000.0 + 0.01
    controller.update_metadata("B", "", "", "md5-b", art_mode="radio")  # no change
    controller._render_frame()
    assert controller._transient.crossfade_from is None


# --- Workstream 5: [ui] theming --------------------------------------------

def _controller_with_ui(monkeypatch, ui):
    """Build a DisplayController whose display.conf carries a given [ui] dict."""
    fake_driver = types.ModuleType("display_1_inch_69.LCD_1inch69")
    fake_driver.LCD_1inch69 = _FakePanel
    monkeypatch.setitem(sys.modules, "display_1_inch_69.LCD_1inch69", fake_driver)

    import importlib

    import display_1_inch_69.display_control as dc
    dc = importlib.reload(dc)

    class _InertThread:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            pass

    monkeypatch.setattr(dc.threading, "Thread", _InertThread)

    real_read_config = dc.utility.read_config

    def _read_with_ui(path):
        conf = real_read_config(path)
        if ui is not None:
            conf["ui"] = ui
        return conf

    monkeypatch.setattr(dc.utility, "read_config", _read_with_ui)
    return dc.DisplayController()


def test_no_ui_section_matches_theme_defaults(controller):
    # With no [ui] section the resolved theme equals the shipped defaults, so
    # the look is byte-identical to before Workstream 5.
    from display_1_inch_69 import theme
    assert controller.theme == theme.Theme()


def test_ui_section_changes_theme_and_frame(monkeypatch):
    from display_1_inch_69 import theme
    default = _controller_with_ui(monkeypatch, None)
    default.update_metadata("Radio", "Song", "", "0", state=True,
                            art_mode="radio", source="mpd")
    default._transient.clear_crossfade()
    default_bytes = default._render_frame().tobytes()

    themed = _controller_with_ui(monkeypatch, {
        "text_color": "#FF8800",
        "scrim_opacity": "0.85",
        "subtext_color": "255,0,0",
    })
    assert themed.theme.text_color == (255, 136, 0)
    assert themed.theme.scrim_opacity == 0.85
    assert themed.theme != theme.Theme()
    themed.update_metadata("Radio", "Song", "", "0", state=True,
                           art_mode="radio", source="mpd")
    themed._transient.clear_crossfade()
    assert themed._render_frame().tobytes() != default_bytes


def test_ui_font_sizes_change_layout(monkeypatch):
    themed = _controller_with_ui(monkeypatch, {"title_size": "18", "safe_inset": "20"})
    assert themed.theme.title_size == 18
    assert themed.theme.safe_inset == 20
    # The safe inset flows into the computed layout.
    assert themed.layout.safe.x == 20


def test_ui_animations_off_disables_crossfade(monkeypatch):
    themed = _controller_with_ui(monkeypatch, {"animations": False})
    assert themed.theme.crossfade_ms == 0
    themed.update_metadata("A", "", "", "md5-a", art_mode="radio")
    themed._build_art_layer()
    themed.update_metadata("B", "", "", "md5-b", art_mode="radio")
    themed._build_art_layer()
    # No crossfade is started when animations are off.
    assert themed._transient.crossfade_from is None
    assert themed._crossfade_active() is False


# --- Workstream 6: generated initials-tile fallback ------------------------

def test_radio_without_logo_renders_initials_tile(controller):
    # A radio station with no logo file must not render a flat backdrop: the
    # generated initials tile makes the centre non-uniform and the dominant
    # colour non-neutral, so the frame differs from the no-art baseline.
    controller.update_metadata("Jazz Radio Berlin", "", "", "no-logo-1",
                               state=True, art_mode="radio", source="mpd")
    controller._transient.clear_crossfade()
    frame = controller._build_art_layer()
    assert frame.size == (240, 280)
    # The logo box centre should carry the tile, not the plain backdrop, so it
    # differs from the top-left safe corner (pure backdrop).
    box = controller.logo_box
    centre = frame.getpixel((box.cx, box.cy))
    corner = frame.getpixel((box.x, box.y - 1)) if box.y > 0 else frame.getpixel((0, 0))
    assert centre != corner


def test_different_logoless_stations_differ(controller):
    controller.update_metadata("Jazz Radio", "", "", "k1", state=True,
                               art_mode="radio", source="mpd")
    controller._transient.clear_crossfade()
    controller._transient.crossfade_until = 0.0
    a = controller._build_art_layer().tobytes()
    controller.update_metadata("KEXP", "", "", "k2", state=True,
                               art_mode="radio", source="mpd")
    controller._transient.clear_crossfade()
    controller._transient.crossfade_until = 0.0
    b = controller._build_art_layer().tobytes()
    # Different names -> different tile initials/colour -> different art.
    assert a != b


def test_fallback_logo_is_rgba_tile(controller):
    controller.update_metadata("Some Station", "", "", "0", state=True,
                               art_mode="radio", source="mpd")
    tile = controller._fallback_logo()
    assert tile.mode == "RGBA"
    assert tile.size[0] == tile.size[1]  # square, sized to the logo box


def test_bluetooth_fallback_is_blue_glyph_tile(controller):
    # Bluetooth carries no cover art; with a blank artist the placeholder must
    # be the dedicated blue Bluetooth-glyph tile, not the "?" initials tile.
    controller.update_metadata("", "", "", "0", state=True,
                               art_mode="cover", source="bluetooth")
    tile = controller._fallback_logo()
    assert tile.mode == "RGBA"
    assert tile.size[0] == tile.size[1]
    # A body pixel (inside the rounded rect, off the centred glyph) is the blue
    # Bluetooth tile colour rather than a name-hashed initials-tile colour.
    assert tile.getpixel((2, tile.size[1] // 2))[:3] == logo_fallback.BLUETOOTH_TILE_COLOR


def test_non_bluetooth_fallback_still_uses_initials(controller):
    # A logoless radio station keeps its branded initials tile (WS6.3): its
    # body colour is the name-derived tile colour, not the Bluetooth blue.
    controller.update_metadata("Some Station", "", "", "0", state=True,
                               art_mode="radio", source="mpd")
    tile = controller._fallback_logo()
    body = tile.getpixel((2, tile.size[1] // 2))[:3]
    assert body == logo_fallback.tile_color("Some Station")
    assert body != logo_fallback.BLUETOOTH_TILE_COLOR






