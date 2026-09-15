#!/usr/bin/env python3
"""Generate bounded immutable metadata embedded in each firmware root filesystem."""

import argparse
import json
import os
from pathlib import Path

from build_firmware_swu import (
    HARDWARE_REVISION,
    MINIMUM_ROLLBACK_READER_SCHEMA,
    PERSISTENT_READER_SCHEMA,
    PERSISTENT_SCHEMA,
    project_version,
)


def release_metadata(version_file: Path) -> dict:
    """Return the stable metadata contract used to inspect an inactive slot."""
    return {
        "hardware_revision": HARDWARE_REVISION,
        "buildroot_commit": os.environ.get("RADIO_BUILDROOT_COMMIT", "unknown"),
        "minimum_rollback_reader_schema": MINIMUM_ROLLBACK_READER_SCHEMA,
        "persistent_reader_schema": PERSISTENT_READER_SCHEMA,
        "persistent_schema": PERSISTENT_SCHEMA,
        "schema": 1,
        "repository_commit": os.environ.get("RADIO_REPO_COMMIT", "unknown"),
        "version": project_version(version_file),
    }


def write_release_metadata(version_file: Path, output: Path) -> None:
    """Write deterministic release metadata, creating the parent directory."""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(release_metadata(version_file), separators=(",", ":"), sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("version_file", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    write_release_metadata(args.version_file, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
