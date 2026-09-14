#!/usr/bin/env python3
"""Off-target sample-frame renderer — Step 10 verification.

Renders a diagnostic test image for each supported panel using only Pillow
(no spidev, no gpiozero, no Raspberry Pi required) and saves them as PNG
files so composition can be visually sanity-checked on a workstation.

Expected output
---------------
  sample_st7789_240x280.png   — six colour bars, white border, diagonals,
                                 centre label "ST7789 240x280"
  sample_gc9a01_240x240.png   — same pattern at 240x240, label "GC9A01 240x240"

Both files are written to the current working directory (or the directory
given by --output-dir).

Usage
-----
    python3 scripts/render_sample_frames.py
    python3 scripts/render_sample_frames.py --output-dir /tmp
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Ensure the repo lib/ is importable when run directly from the repo root.
_REPO_ROOT = Path(__file__).resolve().parents[1]
_LIB_DIR = _REPO_ROOT / "lib"
for _p in (str(_LIB_DIR), str(_REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Import the off-target-safe helpers from display_test.  They use only Pillow
# and the stdlib — no SPI/GPIO imports are triggered here.
from display.display_test import draw_test_image  # noqa: E402

_PANELS = [
    {"name": "ST7789", "width": 240, "height": 280},
    {"name": "GC9A01", "width": 240, "height": 240},
]

_MOCK_SPI_FREQ = 40_000_000  # Hz — shown in the image label; no hardware used


def render_all(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for panel in _PANELS:
        w, h, name = panel["width"], panel["height"], panel["name"]
        label = f"{name} {w}x{h}"
        img = draw_test_image(w, h, _MOCK_SPI_FREQ, panel_label=label)
        out_path = output_dir / f"sample_{name.lower()}_{w}x{h}.png"
        img.save(out_path)
        print(f"  {out_path}  ({w}x{h}, {len(img.tobytes()) // 1024} KB raw)")
        # Sanity-check: buffer dimensions must match the declared panel size.
        assert img.width == w, f"width mismatch: {img.width} != {w}"
        assert img.height == h, f"height mismatch: {img.height} != {h}"
    print("OK — both sample frames rendered without hardware.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("."),
        help="directory to write PNG files (default: current directory)",
    )
    args = parser.parse_args()
    render_all(args.output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
