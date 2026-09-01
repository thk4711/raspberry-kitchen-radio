"""Tests for the early boot splash (lib/display_1_inch_69/boot_splash.py).

The splash runs from inittab sysinit long before radio.py. These tests cover
the two invariants that matter without a Raspberry Pi:

* :func:`render_splash_frame` produces a full-size RGB frame (pure numpy +
  Pillow, no SPI/GPIO), matching the panel geometry; and
* :func:`main` initialises the panel, pushes **exactly one** full frame, turns
  the backlight fully on (so the image is visible through boot) and always
  returns 0 (it must never block/fail the boot).

The ST7789 driver is replaced with a recording fake (mirroring
``test_display_control``); spidev/gpiozero are already stubbed in conftest.
"""
import sys
import types

import numpy as np
from display_1_inch_69 import boot_splash, compositor
from display_1_inch_69 import theme as theme_mod


class _FakePanel:
    """Records the single full-frame write instead of touching SPI/GPIO."""

    def __init__(self, *args, **kwargs):
        self.frames = []
        self.backlight = None
        self.init_called = False
        self.exited = False

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


def test_main_pushes_one_frame_and_lights_backlight(monkeypatch):
    fake_driver = types.ModuleType("display_1_inch_69.LCD_1inch69")
    fake_driver.LCD_1inch69 = _FakePanel
    monkeypatch.setitem(sys.modules, "display_1_inch_69.LCD_1inch69", fake_driver)

    created = {}
    real_panel_cls = _FakePanel

    def _record(*args, **kwargs):
        panel = real_panel_cls(*args, **kwargs)
        created["panel"] = panel
        return panel

    fake_driver.LCD_1inch69 = _record

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

    fake_driver = types.ModuleType("display_1_inch_69.LCD_1inch69")
    fake_driver.LCD_1inch69 = _Boom
    monkeypatch.setitem(sys.modules, "display_1_inch_69.LCD_1inch69", fake_driver)

    assert boot_splash.main() == 0
