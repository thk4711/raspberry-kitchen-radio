#!/usr/bin/env python3
"""Verify selectable audio profiles against a completed Buildroot output tree."""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from radio_web import audio_hardware_store as store  # noqa: E402


def verify(output: Path) -> list[str]:
    errors = store.catalog_errors()
    overlays = output / "images" / "rpi-firmware" / "overlays"
    linux_dirs = sorted((output / "build").glob("linux-*"))
    kernel_config = linux_dirs[-1] / ".config" if linux_dirs else None

    for overlay in store.overlay_ids():
        if not (overlays / f"{overlay}.dtbo").is_file():
            errors.append(f"missing overlay: {overlay}.dtbo")

    if kernel_config is None or not kernel_config.is_file():
        errors.append("missing completed kernel .config under output/build/linux-*")
        return errors

    enabled = set()
    for line in kernel_config.read_text(encoding="utf-8").splitlines():
        if line.startswith("CONFIG_") and line.endswith(("=y", "=m")):
            enabled.add(line.split("=", 1)[0])
    for profile_id, profile in store.PROFILES.items():
        for symbol in profile.required_kernel_symbols:
            if symbol not in enabled:
                errors.append(f"{profile_id}: kernel symbol is not enabled: {symbol}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path, help="Buildroot output directory")
    args = parser.parse_args()
    errors = verify(args.output)
    if errors:
        for error in errors:
            print(f"audio catalog: ERROR: {error}", file=sys.stderr)
        return 1
    print(f"audio catalog: verified {len(store.PROFILES)} profiles")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
