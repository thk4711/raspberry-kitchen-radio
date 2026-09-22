#!/usr/bin/env python3
"""One-shot branded boot splash for the PiSonic SPI display.

This paints a single branded frame on the SPI display **very early in boot**,
long before the radio app itself starts. It is launched (detached) from the
``sysinit`` line ``/usr/sbin/radio-boot-splash`` in ``/etc/inittab`` so the
panel shows the product identity within a second or two of power-on instead of
staying dark until ``radio.py`` comes up at the very end of boot.

The active panel driver is selected by the ``[display] panel`` key in
``display.conf`` (``st7789`` for the 240x280 rectangular panel, ``gc9a01``
for the 240x240 round panel; default is ``st7789``).

Design goals (see doc/buildroot.md, "early boot splash"):

* **Never delay boot.** The launcher backgrounds this process, so ``init`` does
  not block on the ~1 s Python startup; the splash renders in parallel with
  provisioning / rcS. This module therefore just does the minimum: init the
  panel, push one frame, turn the backlight on, release SPI/GPIO and exit.
* **Never block boot on failure.** Every error is caught and logged to stderr
  (the launcher redirects that to a tmpfs log); the process always exits 0.
* **Clean handoff.** After drawing, SPI is closed and the driver GPIO objects are
  released.  Because gpiozero returns PWM pins to input/low on interpreter exit,
  the backlight GPIO is then explicitly parked high via ``RPi.GPIO`` so the image
  stays lit until ``radio.py``'s ``DisplayController`` grabs the same pins and
  repaints. The splash intentionally reuses the same fonts, ``[ui]`` gradient
  colours and ``compositor`` helpers as the in-app splash so the two look
  identical.

On the Buildroot target this file is installed with the rest of the app under::

    /opt/pisonic/lib/display/boot_splash.py

The heavy/hardware imports (the panel driver) are deferred into :func:`main`
so :func:`render_splash_frame` stays importable — and unit-testable — on a
plain workstation with only numpy + Pillow available.
"""

from __future__ import annotations

import atexit
import os
import sys
import traceback

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFont

# Make ``display`` importable both from the source tree and from the
# Buildroot install location (/opt/pisonic/lib), mirroring
# display_test.py so the module runs the same way in both places.
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_LIB_DIR = os.path.dirname(_SCRIPT_DIR)
if _LIB_DIR not in sys.path:
    sys.path.insert(0, _LIB_DIR)

from display import compositor  # noqa: E402
from display import theme as theme_mod  # noqa: E402

try:
    from _version import __version__ as _VERSION  # noqa: E402
except Exception:  # pragma: no cover - defensive; splash must never fail import
    _VERSION = ""

# The splash is intentionally self-contained: the logo is loaded from the
# installed application tree and the subtitle carries the build version so a
# flashed image advertises exactly which build it booted.
_SUBTITLE = f"v{_VERSION}" if _VERSION else "starting…"
_LOGO_FILE = os.path.abspath(
    os.path.join(_LIB_DIR, os.pardir, "radio_web", "static", "PiSonic-Logo.png")
)

# Boot-splash visual style. Two variants are supported so the look can be
# A/B-compared and reverted by flipping a single constant:
#
# * ``"grey"``  — the PiSonic logo composited on a calm mid-grey gradient. The
#   logo's near-white canvas is keyed out to transparent (see
#   ``_apply_white_key``) so the same grey shows through and the mark blends
#   into the screen rather than reading as a bright card.
# * ``"dark"``  — the logo rendered as a clean white silhouette on near-black.
#   The white canvas is keyed out exactly as above, then every remaining
#   (visible) pixel is recoloured to white (see ``_recolor_logo_white``) so the
#   otherwise-dark parts of the mark (the dark bars and the navy "Sonic"
#   wordmark) stay visible on black. The version subtitle is drawn white too.
#
# This is intentionally a splash-only setting: the in-app idle screensaver
# keeps the theme's dark ``idle_bg_*`` gradient regardless.
_SPLASH_STYLE = "dark"

# Per-style backdrop gradient (top -> bottom) and subtitle colour.
_SPLASH_STYLES: dict[str, dict[str, tuple[int, int, int]]] = {
    "grey": {
        "bg_top": (104, 106, 112),
        "bg_bottom": (78, 80, 86),
        # The theme's light-grey subtext washes out on grey, so use dark navy
        # matching the logo wordmark.
        "subtitle": (20, 28, 40),
    },
    "dark": {
        "bg_top": (8, 8, 10),
        "bg_bottom": (0, 0, 0),
        # White silhouette look: the version prints white to match the logo.
        "subtitle": (255, 255, 255),
    },
}


def _splash_style() -> dict[str, tuple[int, int, int]]:
    """Return the active splash style config, defaulting to ``"grey"``."""
    return _SPLASH_STYLES.get(_SPLASH_STYLE, _SPLASH_STYLES["grey"])


_FONTS_DIR = os.path.join(_SCRIPT_DIR, "fonts")
_REGULAR_FONT = os.path.join(_FONTS_DIR, "Roboto-Condensed-Regular.ttf")
_PROVISION_STATUS_FILE = os.environ.get(
    "RADIO_PROVISIONING_STATUS_FILE", "/data/radio/provisioning-status"
)


def _load_font(path: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load ``path`` at ``size``, falling back to Pillow's default bitmap font.

    Keeps the splash rendering even on a partial deploy where a font is missing
    — a legible frame beats crashing this early in boot.
    """
    try:
        return ImageFont.truetype(path, size)
    except Exception:  # pragma: no cover - defensive, exercised only on-target
        return ImageFont.load_default()


def _measure(
    draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont | ImageFont.ImageFont
) -> tuple[int, int, int]:
    """Return ``(width, height, top)`` of ``text`` in ``font`` via a bbox.

    ``top`` is the y-offset of the glyph box so callers can baseline-align the
    text exactly like ``DisplayController._measure`` does.
    """
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    return int(right - left), int(bottom - top), int(top)


def _load_scaled_logo(width: int, height: int) -> Image.Image | None:
    """Load the PiSonic logo and scale it to fit the configured panel.

    The source artwork is 3:2, while supported panels are 240 px wide. Keep a
    small margin on all sides and reserve vertical space for the subtitle so the
    complete mark fits both the 240x280 rectangular and 240x240 round displays.
    """
    try:
        logo = Image.open(_LOGO_FILE).convert("RGBA")
    except Exception as exc:  # pragma: no cover - defensive; splash must never fail
        print(f"boot splash: cannot load logo {_LOGO_FILE}: {exc}", file=sys.stderr)
        return None

    logo = _crop_logo_artwork(logo)
    if _SPLASH_STYLE == "dark":
        logo = _recolor_logo_white(logo)
    max_w = max(1, width - 10)
    max_h = max(1, int(height * 0.74))
    scale = min(max_w / logo.width, max_h / logo.height, 1.0)
    size = (max(1, int(logo.width * scale)), max(1, int(logo.height * scale)))
    resampling = getattr(Image, "Resampling", Image).LANCZOS
    return logo.resize(size, resampling)


def _crop_logo_artwork(logo: Image.Image) -> Image.Image:
    """Trim the logo canvas and key its white background out to transparent.

    The shipped ``PiSonic-Logo.png`` is an RGB image (no alpha) on a near-white
    canvas. Pasting it as-is onto the splash backdrop shows a bright card, so
    this cuts the near-white background to transparent and crops to the visible
    mark, letting the grey splash backdrop show through around the logo. An
    already-transparent asset (a future PNG with alpha) is honoured as-is.
    """
    if "A" in logo.getbands():
        alpha = logo.getchannel("A")
        alpha_bbox = alpha.getbbox()
        # A meaningful alpha channel is only present when the asset is genuinely
        # transparent somewhere. ``Image.open(rgb_png).convert("RGBA")`` adds a
        # fully-opaque alpha (extrema (255, 255)); in that case there is no real
        # transparency, so fall through to the white-key path below instead of
        # returning the untouched (opaque, white-canvas) image.
        if alpha.getextrema() != (255, 255):
            if alpha_bbox and alpha_bbox != (0, 0, logo.width, logo.height):
                return logo.crop(alpha_bbox)
            return logo

    keyed = _apply_white_key(logo)
    bbox = keyed.getchannel("A").getbbox()
    if not bbox:
        return keyed

    pad = max(8, min(logo.size) // 80)
    left = max(0, bbox[0] - pad)
    top = max(0, bbox[1] - pad)
    right = min(logo.width, bbox[2] + pad)
    bottom = min(logo.height, bbox[3] + pad)
    return keyed.crop((left, top, right, bottom))


def _apply_white_key(logo: Image.Image) -> Image.Image:
    """Return an RGBA copy of ``logo`` with its near-white canvas made transparent.

    Builds an alpha channel from how far each pixel differs from white: pixels
    at (or very close to) white become fully transparent, pixels clearly part of
    the mark stay fully opaque, and the narrow band between the two ramps
    linearly so the keyed edges stay anti-aliased on the small LCD. The PiSonic
    mark uses saturated blues/greens, dark navy and mid-grey side dashes — all
    far from white — so keying the near-white canvas never nibbles the artwork.
    """
    rgb = logo.convert("RGB")
    white = Image.new("RGB", rgb.size, (255, 255, 255))
    # Per-pixel max channel difference from white (0 = white, 255 = far).
    diff = ImageChops.difference(rgb, white).convert("L")
    # Ramp: <=lo stays transparent, >=hi fully opaque, linear in between.
    lo, hi = 18, 46
    scale = 255.0 / (hi - lo)
    alpha = diff.point(lambda v: 0 if v <= lo else (255 if v >= hi else int((v - lo) * scale)))
    out = rgb.convert("RGBA")
    out.putalpha(alpha)
    return out


def _recolor_logo_white(logo: Image.Image) -> Image.Image:
    """Return an RGBA copy of ``logo`` with every visible pixel painted white.

    Used by the ``"dark"`` splash style: the logo's near-white canvas has
    already been keyed out to transparent by :func:`_apply_white_key`, but the
    remaining ink is dark (the dark bars and the navy "Sonic" wordmark) and would
    be invisible on a black backdrop. This replaces the RGB of the whole mark
    with white while **preserving the keyed alpha channel**, so the logo becomes
    a clean white silhouette whose anti-aliased edges still fade correctly into
    the black background. If the image has no alpha it is returned unchanged
    (defensive; the splash pipeline always keys first).
    """
    if "A" not in logo.getbands():
        return logo
    alpha = logo.getchannel("A")
    white = Image.new("RGBA", logo.size, (255, 255, 255, 255))
    white.putalpha(alpha)
    return white


def render_splash_frame(
    width: int, height: int, theme: theme_mod.Theme, subtitle: str = _SUBTITLE
) -> Image.Image:
    """Render the branded boot-splash frame as a full ``width x height`` image.

    The active :data:`_SPLASH_STYLE` selects the look: ``"grey"`` composites the
    PiSonic logo (near-white canvas keyed out) on a calm mid-grey gradient so it
    blends into the screen; ``"dark"`` renders the logo as a white silhouette on
    near-black with a white version subtitle. Pure: no SPI/GPIO, so it is
    unit-testable on any machine with numpy + Pillow.

    Args:
        width: Frame width in pixels (portrait, e.g. 240).
        height: Frame height in pixels (portrait, e.g. 280).
        theme: Resolved :class:`theme.Theme` (used for font sizes).

    Returns:
        A ``PIL.Image`` in RGB mode of size ``(width, height)``.
    """
    style = _splash_style()
    arr = compositor.vertical_gradient(width, height, style["bg_top"], style["bg_bottom"])
    frame = Image.fromarray(arr, "RGB")
    draw = ImageDraw.Draw(frame)
    subtitle_color = style["subtitle"]

    font_sub = _load_font(_REGULAR_FONT, theme.date_size)
    sw, sh, stop = _measure(draw, subtitle, font_sub)

    logo = _load_scaled_logo(width, height)
    if logo is None:
        # A legible version/setup line still beats a failed early-boot splash if
        # an incomplete image somehow lacks the logo asset.
        y = (height - sh) // 2
        draw.text(((width - sw) // 2, y - stop), subtitle, font=font_sub, fill=subtitle_color)
        return frame

    gap = max(8, min(14, height // 18))
    block_h = logo.height + gap + sh
    y = max(0, (height - block_h) // 2)
    x = (width - logo.width) // 2
    frame.paste(logo, (x, y), logo)
    y += logo.height + gap
    draw.text(((width - sw) // 2, y - stop), subtitle, font=font_sub, fill=subtitle_color)
    return frame


def _read_display_conf() -> dict:
    """Read display config exactly like the main radio app.

    The shipped display.conf lives next to this module. The web UI writes an
    optional managed display.ini under MANAGED_CONFIG_DIR (/etc/radio on target,
    overridable for tests). Layering both files keeps early boot splash settings
    in sync with DisplayController, including panel geometry, theme colours and
    screen rotation. Any failure yields an empty dict so main() falls back to
    safe defaults.
    """
    try:
        from utilities import MANAGED_CONFIG_DIR, UtilityLibrary  # noqa: PLC0415

        conf = UtilityLibrary().read_config_layered(
            os.path.join(_SCRIPT_DIR, "display.conf"),
            os.path.join(MANAGED_CONFIG_DIR, "display.ini"),
        )
        return conf or {}
    except Exception:  # pragma: no cover - defensive; never block boot
        return {}


def _park_backlight_on(bl_pin: int) -> None:
    """Best-effort target-only parking of the backlight GPIO high.

    ``lcdconfig.RaspberryPi.module_exit()`` correctly closes the gpiozero PWM
    object so the later radio app can acquire the pin cleanly. On the Buildroot
    target, however, gpiozero also registers an interpreter-exit cleanup hook
    that returns BCM12 to input/low, so a one-shot process that exits immediately
    after drawing leaves the panel with a valid splash image but no backlight (a
    visually black screen).  After module_exit has already released the driver
    GPIO objects, unregister that hook and use RPi.GPIO to leave the electrical
    line asserted for the handoff.

    Any failure is ignored: this runs during early boot and must never make the
    splash fatal.
    """
    try:
        try:
            from gpiozero import (
                devices as gpiozero_devices,  # type: ignore[import-not-found]  # noqa: PLC0415
            )

            atexit.unregister(gpiozero_devices._shutdown)
        except Exception:
            pass

        import RPi.GPIO as GPIO  # type: ignore[import-not-found]  # noqa: PLC0415

        GPIO.setwarnings(False)
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(bl_pin, GPIO.OUT, initial=GPIO.HIGH)
        GPIO.output(bl_pin, GPIO.HIGH)
    except Exception:  # pragma: no cover - target best-effort path
        traceback.print_exc()


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
        bl = int(display.get("bl", 12))
        spi_bus = int(display.get("spi_bus", 0))
        spi_device = int(display.get("spi_device", 0))
        spi_freq = int(display.get("spi_freq", 40_000_000))
        panel_name = str(display.get("panel", "st7789"))
        theme = theme_mod.build_theme(conf.get("ui") if isinstance(conf, dict) else None)

        subtitle = "SETUP REQUIRED" if os.path.isfile(_PROVISION_STATUS_FILE) else _SUBTITLE
        frame = render_splash_frame(width, height, theme, subtitle)
        if theme.rotate_180:
            frame = frame.rotate(180)
        pix = compositor.pack_rgb565(np.asarray(frame))

        # Defer the hardware driver import until here so this module stays
        # importable (for tests / --help style use) without spidev/gpiozero.
        from display import panel_factory  # noqa: PLC0415

        PanelClass = panel_factory.get_panel_class(panel_name)
        disp = PanelClass(
            rst=rst,
            dc=dc,
            bl=bl,
            spi_bus=spi_bus,
            spi_device=spi_device,
            spi_freq=spi_freq,
        )
        try:
            disp.Init()
            disp.ShowFullFrame(pix)
            # Turn the backlight fully on so the splash is visible during boot.
            disp.bl_DutyCycle(100)
        finally:
            # Release SPI + gpiozero handles so radio.py's DisplayController can
            # grab the pins cleanly later, then park only the backlight line high
            # outside gpiozero's atexit cleanup so the one-shot process exiting
            # does not make the already-written splash appear black.
            try:
                disp.module_exit()
            except Exception:  # pragma: no cover - best-effort cleanup
                traceback.print_exc()
            _park_backlight_on(bl)
    except Exception:  # pragma: no cover - never block boot on the splash
        traceback.print_exc()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
