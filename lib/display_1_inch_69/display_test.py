#!/usr/bin/env python3
"""Minimal ST7789 1.69 inch display smoke test.

This script initializes only the SPI display used by Raspberry Kitchen Radio,
turns the backlight on, and draws a simple diagnostic image. It intentionally
avoids starting the radio app, MPD, ADC polling, AirPlay, or Spotify services.

On the Buildroot target this file is installed with the rest of the app under:

    /opt/raspberry-kitchen-radio/lib/display_1_inch_69/display_test.py

Recommended target usage:

    /etc/init.d/S90radio stop
    cd /opt/raspberry-kitchen-radio
    python3 lib/display_1_inch_69/display_test.py

If the wiring and SPI stack are correct, the display should light up and show
colored bars, a border, diagonals, and text.
"""

from __future__ import annotations

import argparse
import configparser
import sys
import time
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

SCRIPT_DIR = Path(__file__).resolve().parent
APP_ROOT = SCRIPT_DIR.parents[1]
LIB_DIR = APP_ROOT / "lib"

# Make the import work both from the source tree and from the Buildroot install
# location (/opt/raspberry-kitchen-radio).
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

DEFAULT_CONFIG = SCRIPT_DIR / "display.conf"
DEFAULT_FONT = SCRIPT_DIR / "fonts" / "Roboto-Condensed-Regular.ttf"


def read_display_config(path: Path) -> dict[str, int]:
    """Read display.conf using the same keys as DisplayController."""
    parser = configparser.ConfigParser()
    if not parser.read(path):
        raise FileNotFoundError(f"Could not read display config: {path}")

    if "display" not in parser:
        raise KeyError(f"Missing [display] section in {path}")

    section = parser["display"]
    return {
        "width": section.getint("width", fallback=240),
        "height": section.getint("height", fallback=280),
        "rst": section.getint("rst", fallback=24),
        "dc": section.getint("dc", fallback=25),
        "bl": section.getint("bl", fallback=22),
        "spi_freq": section.getint("spi_freq", fallback=40_000_000),
    }


def load_font(size: int) -> ImageFont.ImageFont:
    """Load the bundled font, falling back to PIL's default bitmap font."""
    try:
        return ImageFont.truetype(str(DEFAULT_FONT), size)
    except Exception:
        return ImageFont.load_default()


def draw_centered_text(
    draw: ImageDraw.ImageDraw,
    y: int,
    text: str,
    font: ImageFont.ImageFont,
    image_width: int,
    fill: str,
) -> int:
    """Draw one centered line with a small black shadow and return next y."""
    bbox = draw.textbbox((0, 0), text, font=font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    x = (image_width - text_width) // 2
    draw.text((x + 2, y + 2), text, font=font, fill="black")
    draw.text((x, y), text, font=font, fill=fill)
    return y + text_height + 10


def draw_test_image(width: int, height: int, spi_freq: int) -> Image.Image:
    """Create an obvious diagnostic image for orientation and color testing."""
    image = Image.new("RGB", (width, height), "black")
    draw = ImageDraw.Draw(image)

    # Six vertical color bars make swapped colors / missing data lines visible.
    colors = ["red", "lime", "blue", "yellow", "cyan", "magenta"]
    bar_width = max(1, width // len(colors))
    for index, color in enumerate(colors):
        x0 = index * bar_width
        x1 = width if index == len(colors) - 1 else (index + 1) * bar_width
        draw.rectangle((x0, 0, x1 - 1, height - 1), fill=color)

    # Black inset and white/orange borders make display offsets obvious.
    margin = 12
    draw.rectangle((margin, margin, width - margin - 1, height - margin - 1), fill="black")
    draw.rectangle((0, 0, width - 1, height - 1), outline="white", width=3)
    draw.rectangle((margin, margin, width - margin - 1, height - margin - 1), outline="orange", width=2)

    # Diagonals help spot mirroring/rotation.
    draw.line((0, 0, width - 1, height - 1), fill="white", width=2)
    draw.line((0, height - 1, width - 1, 0), fill="white", width=2)

    title_font = load_font(32)
    small_font = load_font(18)

    y = 78
    y = draw_centered_text(draw, y, "DISPLAY TEST", title_font, width, "white")
    y = draw_centered_text(draw, y, "ST7789 240x280", small_font, width, "orange")
    y = draw_centered_text(draw, y, f"SPI {spi_freq // 1_000_000} MHz", small_font, width, "orange")
    draw_centered_text(draw, y, datetime.now().strftime("%H:%M:%S"), small_font, width, "orange")

    return image


def run_mock_now_playing(args) -> int:
    """Drive the real DisplayController with mock metadata for on-target eyeballing.

    Builds a full :class:`DisplayController` (which owns the panel and its own
    single writer thread) and feeds it a sequence of mock states so the whole
    redesigned UI and the Workstream 4 motion can be validated on the physical
    panel with the radio app stopped:

        /etc/init.d/S90radio stop
        cd /opt/raspberry-kitchen-radio
        python3 lib/display_1_inch_69/display_test.py --mock-now-playing

    Cycles through: radio now-playing (with an "Artist - Title" stream), a cover
    crossfade to a second station (WS4.1), a volume OSD sweep (WS4.2), a preset
    toast (WS4.5), and finally the idle clock screensaver (WS4.4). The boot
    splash (WS4.6) is shown by the controller's own __init__.
    """
    from display_1_inch_69.display_control import DisplayController  # noqa: PLC0415

    logos = SCRIPT_DIR.parent / "mpd_service" / "logos"

    def logo(name: str) -> str:
        p = logos / name
        return str(p) if p.exists() else ""

    print("Building DisplayController (shows the boot splash first)...")
    display = DisplayController()
    display.toggle_backlight(True)

    try:
        print("Radio now-playing: Deutschlandfunk Nova + stream title.")
        display.update_metadata(
            "Deutschlandfunk Nova", "Chvrches - The Mother We Share",
            logo("Deutschlandfunk_Nova.png"), "mock-1",
            state=True, art_mode="radio", source="mpd")
        time.sleep(3)

        print("Crossfade to a second station (WS4.1).")
        display.update_metadata(
            "MDR JUMP", "", logo("MDR_JUMP.png"), "mock-2",
            state=True, art_mode="radio", source="mpd")
        time.sleep(3)

        print("Volume OSD sweep (WS4.2).")
        for pct in range(0, 101, 10):
            display.show_volume(pct)
            time.sleep(0.15)
        time.sleep(2)

        print("Preset toast (WS4.5).")
        display.show_toast("MDR KULTUR")
        display.update_metadata(
            "MDR KULTUR", "", logo("MDR_KULTUR.png"), "mock-3",
            state=True, art_mode="radio", source="mpd")
        time.sleep(3)

        print("Idle clock screensaver (WS4.4): pausing playback and waiting.")
        display.update_metadata(
            "MDR KULTUR", "", logo("MDR_KULTUR.png"), "mock-3",
            state=False, art_mode="radio", source="mpd")
        # Force the idle timer well into the past so the screensaver appears.
        with display._state_lock:
            display._transient.last_activity -= 10_000
        hold = args.seconds if args.seconds and args.seconds > 0 else 20
        print(f"Holding the screensaver for {hold:g}s. Press Ctrl+C to exit.")
        time.sleep(hold)
        return 0
    except KeyboardInterrupt:
        print("Interrupted.")
        return 130


def main() -> int:
    parser = argparse.ArgumentParser(description="Initialize the ST7789 display and show a test image.")
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help=f"display.conf path (default: {DEFAULT_CONFIG})",
    )
    parser.add_argument(
        "--seconds",
        type=float,
        default=60.0,
        help="seconds to keep the image/backlight on; use 0 to exit immediately after drawing",
    )
    parser.add_argument(
        "--backlight",
        type=int,
        default=100,
        choices=range(0, 101),
        metavar="0..100",
        help="backlight duty cycle percentage (default: 100)",
    )
    parser.add_argument(
        "--spi-freq",
        type=int,
        default=None,
        help="override SPI clock in Hz; try 10000000 for wiring/signal-integrity checks",
    )
    parser.add_argument(
        "--mock-now-playing",
        action="store_true",
        help="drive the real DisplayController with mock metadata to eyeball the "
             "redesigned now-playing UI + WS4 motion (crossfade, volume OSD, "
             "preset toast, idle screensaver). Ignores --backlight.",
    )
    args = parser.parse_args()

    conf = read_display_config(args.config)
    if args.spi_freq is not None:
        conf["spi_freq"] = args.spi_freq

    if args.mock_now_playing:
        return run_mock_now_playing(args)

    # Import the hardware driver only after parsing arguments. This keeps
    # ``display_test.py --help`` usable on non-Pi development machines where
    # spidev/gpiozero are not installed.
    from display_1_inch_69 import LCD_1inch69  # noqa: PLC0415

    print("Display configuration:")
    print(f"  size:      {conf['width']}x{conf['height']}")
    print(f"  RST/DC/BL: BCM {conf['rst']} / {conf['dc']} / {conf['bl']}")
    print("  SPI:       bus 0, device CE0, mode 0")
    print(f"  SPI freq:  {conf['spi_freq']} Hz")

    disp = LCD_1inch69.LCD_1inch69(
        rst=conf["rst"],
        dc=conf["dc"],
        bl=conf["bl"],
        spi_freq=conf["spi_freq"],
    )

    try:
        print("Initializing display...")
        disp.Init()
        print(f"Turning backlight on ({args.backlight}%)...")
        disp.bl_DutyCycle(args.backlight)

        image = draw_test_image(conf["width"], conf["height"], conf["spi_freq"])
        print("Writing test image...")
        disp.ShowImage(image)
        print("Done. The display should now show the test pattern.")

        if args.seconds > 0:
            print(f"Keeping image visible for {args.seconds:g} seconds. Press Ctrl+C to exit.")
            time.sleep(args.seconds)
        return 0
    except KeyboardInterrupt:
        print("Interrupted.")
        return 130
    finally:
        # Leave the display contents visible, but close SPI/GPIO handles cleanly.
        # The radio app will reinitialize the display when restarted.
        try:
            disp.module_exit()
        except Exception as exc:  # pragma: no cover - diagnostic best effort
            print(f"Warning: cleanup failed: {exc}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
