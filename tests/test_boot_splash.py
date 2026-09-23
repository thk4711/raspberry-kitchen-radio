"""Tests for the early boot splash (lib/display/boot_splash.py).

The splash runs from inittab sysinit long before radio.py. These tests cover
the two invariants that matter without a Raspberry Pi:

* :func:`render_splash_frame` produces a full-size RGB frame (pure numpy +
  Pillow, no SPI/GPIO), matching the panel geometry; and
* :func:`main` initialises the panel, pushes **exactly one** full frame, turns
  the backlight fully on (so the image is visible through boot) and always
  returns 0 (it must never block/fail the boot).

The panel driver is replaced with a recording fake by patching
``panel_factory.get_panel_class`` so that it returns ``_FakePanel`` regardless
of which panel name is configured; spidev/gpiozero are already stubbed in
conftest.
"""

import sys
import types
from unittest import mock

import numpy as np
import pytest
from display import boot_splash, compositor, panel_factory
from display import theme as theme_mod


class _FakePanel:
    """Records the single full-frame write instead of touching SPI/GPIO."""

    def __init__(self, *args, **kwargs):
        self.frames = []
        self.backlight = None
        self.init_called = False
        self.exited = False
        self.init_kwargs = kwargs

    def Init(self):
        self.init_called = True

    def ShowFullFrame(self, pix):
        self.frames.append(pix)

    def bl_DutyCycle(self, duty):
        self.backlight = duty

    def module_exit(self):
        self.exited = True


def test_render_splash_frame_is_full_size_rgb():
    theme = theme_mod.build_theme(None)
    frame = boot_splash.render_splash_frame(240, 280, theme)
    assert frame.size == (240, 280)
    assert frame.mode == "RGB"


def test_render_splash_frame_matches_packed_signature():
    # The rendered frame packs to the expected RGB565 buffer length; this also
    # exercises the exact pack path main() hands to the panel.
    theme = theme_mod.build_theme(None)
    frame = boot_splash.render_splash_frame(240, 280, theme)
    pix = compositor.pack_rgb565(np.asarray(frame))
    assert len(pix) == 240 * 280 * 2


def test_dark_style_renders_white_logo_on_black(monkeypatch):
    # The "dark" splash style paints a near-black backdrop and recolours the
    # logo to a white silhouette so its otherwise-dark parts stay visible.
    monkeypatch.setattr(boot_splash, "_SPLASH_STYLE", "dark")
    theme = theme_mod.build_theme(None)
    frame = boot_splash.render_splash_frame(240, 280, theme)
    assert frame.size == (240, 280)
    assert frame.mode == "RGB"

    arr = np.asarray(frame)
    # The top-left corner is backdrop only: near-black under the "dark" style.
    assert tuple(int(c) for c in arr[2, 2]) == (8, 8, 10)
    # A white silhouette + white version line means plenty of near-white pixels.
    near_white = int((arr > 230).all(axis=2).sum())
    assert near_white > 2000


def test_dark_style_recolors_logo_to_white(monkeypatch):
    # Under the "dark" style the scaled logo's visible (opaque) pixels are pure
    # white — no residual dark navy/blue ink that would vanish on black.
    monkeypatch.setattr(boot_splash, "_SPLASH_STYLE", "dark")
    logo = boot_splash._load_scaled_logo(240, 280)
    assert logo is not None
    la = np.asarray(logo)
    visible = la[:, :, 3] > 200
    assert visible.any()
    for channel in range(3):
        assert la[:, :, channel][visible].min() == 255


def test_grey_style_keeps_logo_colour(monkeypatch):
    # The "grey" style leaves the logo in its native colours (blue/green/navy),
    # so its visible pixels are NOT all white.
    monkeypatch.setattr(boot_splash, "_SPLASH_STYLE", "grey")
    logo = boot_splash._load_scaled_logo(240, 280)
    assert logo is not None
    la = np.asarray(logo)
    visible = la[:, :, 3] > 200
    assert visible.any()
    mean_rgb = [int(la[:, :, c][visible].mean()) for c in range(3)]
    assert mean_rgb != [255, 255, 255]


def test_setup_required_subtitle_changes_splash():
    theme = theme_mod.build_theme(None)
    normal = boot_splash.render_splash_frame(240, 280, theme)
    warning = boot_splash.render_splash_frame(240, 280, theme, "SETUP REQUIRED")
    assert warning.tobytes() != normal.tobytes()


def test_main_pushes_one_frame_and_lights_backlight(monkeypatch):
    created = {}
    parked = []
    real_panel_cls = _FakePanel

    def _record(*args, **kwargs):
        panel = real_panel_cls(*args, **kwargs)
        created["panel"] = panel
        return panel

    # Patch the factory so it returns a class whose constructor is _record.
    class _RecordingFakeClass:
        def __new__(cls, *args, **kwargs):
            return _record(*args, **kwargs)

    monkeypatch.setattr(panel_factory, "get_panel_class", lambda name: _RecordingFakeClass)
    monkeypatch.setattr(boot_splash, "_park_backlight_on", lambda pin: parked.append(pin))

    rc = boot_splash.main()

    assert rc == 0
    panel = created["panel"]
    assert panel.init_called is True
    # Exactly one full frame is pushed, of the right RGB565 length.
    assert len(panel.frames) == 1
    assert len(panel.frames[0]) == 240 * 280 * 2
    # Backlight is turned fully on so the splash is visible during boot.
    assert panel.backlight == 100
    # SPI/GPIO are released for a clean handoff to radio.py.
    assert panel.exited is True
    # The backlight GPIO is parked high after gpiozero cleanup so the one-shot
    # process exiting does not make the already-written splash appear black.
    assert parked == [12]


def test_park_backlight_on_uses_rpi_gpio(monkeypatch):
    gpio = sys.modules["RPi.GPIO"]
    gpiozero = types.ModuleType("gpiozero")
    gpiozero_devices = types.ModuleType("gpiozero.devices")
    gpiozero_devices._shutdown = object()
    gpiozero.devices = gpiozero_devices
    unregister = mock.Mock()
    monkeypatch.setattr(boot_splash.atexit, "unregister", unregister)
    monkeypatch.setitem(sys.modules, "gpiozero", gpiozero)
    monkeypatch.setitem(sys.modules, "gpiozero.devices", gpiozero_devices)
    gpio.setwarnings.reset_mock()
    gpio.setmode.reset_mock()
    gpio.setup.reset_mock()
    gpio.output.reset_mock()

    boot_splash._park_backlight_on(12)

    unregister.assert_called_once_with(gpiozero_devices._shutdown)
    gpio.setwarnings.assert_called_once_with(False)
    gpio.setmode.assert_called_once_with(gpio.BCM)
    gpio.setup.assert_called_once_with(12, gpio.OUT, initial=gpio.HIGH)
    gpio.output.assert_called_once_with(12, gpio.HIGH)


def test_main_passes_configured_spi_chip_select(monkeypatch):
    created = {}

    def _record(*args, **kwargs):
        created["kwargs"] = kwargs
        return _FakePanel(*args, **kwargs)

    class _RecordingFakeClass:
        def __new__(cls, *args, **kwargs):
            return _record(*args, **kwargs)

    monkeypatch.setattr(panel_factory, "get_panel_class", lambda name: _RecordingFakeClass)
    monkeypatch.setattr(
        boot_splash,
        "_read_display_conf",
        lambda: {"display": {"spi_bus": 0, "spi_device": 1}},
    )

    assert boot_splash.main() == 0
    assert created["kwargs"]["spi_bus"] == 0
    assert created["kwargs"]["spi_device"] == 1


def test_read_display_conf_layers_managed_display_ini(monkeypatch, tmp_path):
    import utilities

    script_dir = tmp_path / "display"
    managed_dir = tmp_path / "managed"
    script_dir.mkdir()
    managed_dir.mkdir()
    nl = chr(10)
    default_text = nl.join(
        [
            "[display]",
            "width = 240",
            "height = 280",
            "spi_device = 0",
            "[ui]",
            "rotate_180 = false",
            "",
        ]
    )
    override_text = nl.join(
        [
            "[display]",
            "height = 240",
            "[ui]",
            "rotate_180 = true",
            "",
        ]
    )
    (script_dir / "display.conf").write_text(default_text)
    (managed_dir / "display.ini").write_text(override_text)
    monkeypatch.setattr(boot_splash, "_SCRIPT_DIR", str(script_dir))
    monkeypatch.setattr(utilities, "MANAGED_CONFIG_DIR", str(managed_dir))

    conf = boot_splash._read_display_conf()

    assert conf["display"]["width"] == 240
    assert conf["display"]["height"] == 240
    assert conf["display"]["spi_device"] == 0
    assert conf["ui"]["rotate_180"] is True


def test_main_applies_configured_180_rotation(monkeypatch):
    created = {}

    def _record(*args, **kwargs):
        panel = _FakePanel(*args, **kwargs)
        created["panel"] = panel
        return panel

    class _RecordingFakeClass:
        def __new__(cls, *args, **kwargs):
            return _record(*args, **kwargs)

    frame = boot_splash.Image.new("RGB", (2, 2))
    frame.putpixel((0, 0), (255, 0, 0))
    frame.putpixel((1, 0), (0, 255, 0))
    frame.putpixel((0, 1), (0, 0, 255))
    frame.putpixel((1, 1), (255, 255, 255))

    monkeypatch.setattr(panel_factory, "get_panel_class", lambda name: _RecordingFakeClass)
    monkeypatch.setattr(
        boot_splash,
        "_read_display_conf",
        lambda: {"display": {"width": 2, "height": 2}, "ui": {"rotate_180": True}},
    )
    monkeypatch.setattr(boot_splash, "render_splash_frame", lambda *args, **kwargs: frame)
    monkeypatch.setattr(boot_splash, "_park_backlight_on", lambda pin: None)

    assert boot_splash.main() == 0
    expected = compositor.pack_rgb565(np.asarray(frame.rotate(180)))
    assert created["panel"].frames == [expected]


def test_main_never_raises_and_returns_zero(monkeypatch):
    # A driver that explodes on Init must not propagate: main() swallows it and
    # still returns 0 so the splash can never block boot.
    class _Boom:
        def __init__(self, *args, **kwargs):
            pass

        def Init(self):
            raise RuntimeError("simulated SPI failure")

        def ShowFullFrame(self, pix):
            pass

        def bl_DutyCycle(self, duty):
            pass

        def module_exit(self):
            pass

    monkeypatch.setattr(panel_factory, "get_panel_class", lambda name: _Boom)

    assert boot_splash.main() == 0
