#!/usr/bin/env python3
"""One-shot branded boot splash for the ST7789 1.69 inch SPI panel.

This paints a single branded frame on the SPI display **very early in boot**,
long before the radio app itself starts. It is launched (detached) from the
``sysinit`` line ``/usr/sbin/radio-boot-splash`` in ``/etc/inittab`` so the
panel shows the product identity within a second or two of power-on instead of
staying dark until ``radio.py`` comes up at the very end of boot.

Design goals (see doc/buildroot.md, "early boot splash"):

* **Never delay boot.** The launcher backgrounds this process, so ``init`` does
  not block on the ~1 s Python startup; the splash renders in parallel with
  provisioning / rcS. This module therefore just does the minimum: init the
  panel, push one frame, turn the backlight on, release SPI/GPIO and exit.
* **Never block boot on failure.** Every error is caught and logged to stderr
  (the launcher redirects that to a tmpfs log); the process always exits 0.
* **Clean handoff.** After drawing, SPI is closed and the GPIO lines are
  released *without* blanking the backlight, so the image stays lit until
  ``radio.py``'s ``DisplayController`` grabs the same pins and repaints. The
  splash intentionally reuses the same fonts, ``[ui]`` gradient colours and
  ``compositor`` helpers as the in-app splash so the two look identical.

On the Buildroot target this file is installed with the rest of the app under::

    /opt/raspberry-kitchen-radio/lib/display_1_inch_69/boot_splash.py

The heavy/hardware imports (the ST7789 driver) are deferred into :func:`main`
so :func:`render_splash_frame` stays importable — and unit-testable — on a
plain workstation with only numpy + Pillow available.
"""
from __future__ import annotations

import os
import sys
import traceback

import numpy as np
from PIL import Image, ImageDraw, ImageFont

# Make ``display_1_inch_69`` importable both from the source tree and from the
# Buildroot install location (/opt/raspberry-kitchen-radio/lib), mirroring
# display_test.py so the module runs the same way in both places.
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_LIB_DIR = os.path.dirname(_SCRIPT_DIR)
if _LIB_DIR not in sys.path:
    sys.path.insert(0, _LIB_DIR)

from display_1_inch_69 import compositor  # noqa: E402
from display_1_inch_69 import theme as theme_mod  # noqa: E402

try:
    from _version import __version__ as _VERSION  # noqa: E402
except Exception:  # pragma: no cover - defensive; splash must never fail import
    _VERSION = ""

# The two lines of the splash. Kept here (not read from config) so the early
# splash is fully self-contained: a missing/short display.conf still renders.
# The subtitle carries the build version so a flashed image advertises exactly
# which build it booted.
_TITLE = "RADIO"
_SUBTITLE = f"v{_VERSION}" if _VERSION else "starting…"

_FONTS_DIR = os.path.join(_SCRIPT_DIR, "fonts")
_BOLD_FONT = os.path.join(_FONTS_DIR, "Roboto-Condensed-Bold.ttf")
_REGULAR_FONT = os.path.join(_FONTS_DIR, "Roboto-Condensed-Regular.ttf")


def _load_font(path: str, size: int) -> ImageFont.ImageFont:
    """Load ``path`` at ``size``, falling back to Pillow's default bitmap font.

    Keeps the splash rendering even on a partial deploy where a font is missing
    — a legible frame beats crashing this early in boot.
    """
    try:
        return ImageFont.truetype(path, size)
    except Exception:  # pragma: no cover - defensive, exercised only on-target
        return ImageFont.load_default()


def _measure(draw: ImageDraw.ImageDraw, text: str,
             font: ImageFont.ImageFont) -> tuple[int, int, int]:
    """Return ``(width, height, top)`` of ``text`` in ``font`` via a bbox.

    ``top`` is the y-offset of the glyph box so callers can baseline-align the
    text exactly like ``DisplayController._measure`` does.
    """
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    return right - left, bottom - top, top


def render_splash_frame(width: int, height: int,
                        theme: theme_mod.Theme) -> Image.Image:
    """Render the branded boot-splash frame as a full ``width x height`` image.

    A dark vertical gradient (the ``[ui]`` idle-backdrop colours) with the
    product name centred over a small subtitle, matching the in-app
    ``DisplayController._render_splash`` look. Pure: no SPI/GPIO, so it is
    unit-testable on any machine with numpy + Pillow.

    Args:
        width: Frame width in pixels (portrait, e.g. 240).
        height: Frame height in pixels (portrait, e.g. 280).
        theme: Resolved :class:`theme.Theme` (colours + font sizes).

    Returns:
        A ``PIL.Image`` in RGB mode of size ``(width, height)``.
    """
    arr = compositor.vertical_gradient(width, height,
                                       theme.idle_bg_top, theme.idle_bg_bottom)
    frame = Image.fromarray(arr, "RGB")
    draw = ImageDraw.Draw(frame)

    font_title = _load_font(_BOLD_FONT, theme.clock_large_size)
    font_sub = _load_font(_REGULAR_FONT, theme.date_size)

    tw, th, ttop = _measure(draw, _TITLE, font_title)
    sw, sh, stop = _measure(draw, _SUBTITLE, font_sub)
    block_h = th + 10 + sh
    y = (height - block_h) // 2
    draw.text(((width - tw) // 2, y - ttop), _TITLE,
              font=font_title, fill=theme.text_color)
    y += th + 10
    draw.text(((width - sw) // 2, y - stop), _SUBTITLE,
              font=font_sub, fill=theme.subtext_color)
    return frame


def _read_display_conf() -> dict:
    """Read the shipped ``display.conf`` next to this module.

    Uses ``UtilityLibrary.read_config`` (the same parser the app uses) so the
    ``[display]`` pins/size and the ``[ui]`` theme are read identically. Any
    failure yields an empty dict so :func:`main` falls back to safe defaults.
    """
    try:
        from utilities import UtilityLibrary  # noqa: PLC0415
        conf = UtilityLibrary().read_config(os.path.join(_SCRIPT_DIR, "display.conf"))
        return conf or {}
    except Exception:  # pragma: no cover - defensive; never block boot
        return {}


def main() -> int:
    """Draw the splash once, turn the backlight on, release SPI/GPIO, exit 0.

    Always returns 0: this runs from ``sysinit`` and must never block or fail
    the boot. On any error it prints a traceback (redirected by the launcher to
    a tmpfs log) and still returns 0.
    """
    try:
        conf = _read_display_conf()
        display = conf.get("display", {}) if isinstance(conf, dict) else {}
        width = int(display.get("width", 240))
        height = int(display.get("height", 280))
        rst = int(display.get("rst", 24))
        dc = int(display.get("dc", 25))
        bl = int(display.get("bl", 22))
        spi_freq = int(display.get("spi_freq", 40_000_000))
        theme = theme_mod.build_theme(conf.get("ui") if isinstance(conf, dict) else None)

        frame = render_splash_frame(width, height, theme)
        pix = compositor.pack_rgb565(np.asarray(frame))

        # Defer the hardware driver import until here so this module stays
        # importable (for tests / --help style use) without spidev/gpiozero.
        from display_1_inch_69 import LCD_1inch69  # noqa: PLC0415

        disp = LCD_1inch69.LCD_1inch69(rst=rst, dc=dc, bl=bl, spi_freq=spi_freq)
        try:
            disp.Init()
            disp.ShowFullFrame(pix)
            # Turn the backlight fully on so the splash is visible during boot.
            disp.bl_DutyCycle(100)
        finally:
            # Release SPI + GPIO so radio.py's DisplayController can grab the
            # pins cleanly later, but leave the panel contents + backlight as
            # they are (module_exit does not blank the backlight duty cycle).
            try:
                disp.module_exit()
            except Exception:  # pragma: no cover - best-effort cleanup
                traceback.print_exc()
    except Exception:  # pragma: no cover - never block boot on the splash
        traceback.print_exc()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

