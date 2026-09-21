#!/usr/bin/env python3
"""Validate that all repository release identities describe the same release."""

import argparse
import json
import re
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path
from typing import List, Optional, Tuple

from build_firmware_swu import artifact_name, project_version, render_manifest
from generate_release_metadata import release_metadata

SEMVER = r"[0-9]+\.[0-9]+\.[0-9]+"
PYPROJECT_VERSION_RE = re.compile(rf'^version\s*=\s*"({SEMVER})"\s*$', re.MULTILINE)
CHANGELOG_RELEASE_RE = re.compile(rf"^## \[({SEMVER})\] - (\d{{4}}-\d{{2}}-\d{{2}})$", re.MULTILINE)
FIXED_ARTIFACT_RE = re.compile(rf"pisonic-({SEMVER})\.swu")

# v0.2.0 was already published as a lightweight tag before annotated tags became
# mandatory. Rewriting a public tag would damage traceability, so this one tag is
# retained and checked against its commit date. No new exceptions should be added.
LIGHTWEIGHT_TAG_EXCEPTIONS = {"v0.2.0"}


class ConsistencyError(ValueError):
    """Raised when release metadata is absent, malformed, or inconsistent."""


def _one_match(pattern: re.Pattern, text: str, label: str) -> str:
    matches = pattern.findall(text)
    if len(matches) != 1:
        raise ConsistencyError(f"expected exactly one {label}, found {len(matches)}")
    return matches[0]


def pyproject_version(path: Path) -> str:
    """Read the one static project version from pyproject.toml."""
    return _one_match(
        PYPROJECT_VERSION_RE, path.read_text(encoding="utf-8"), "pyproject.toml version"
    )


def latest_changelog_release(path: Path) -> Tuple[str, str]:
    """Return the first released version/date pair below Unreleased."""
    matches = CHANGELOG_RELEASE_RE.findall(path.read_text(encoding="utf-8"))
    if not matches:
        raise ConsistencyError("CHANGELOG.md has no X.Y.Z release heading with an ISO date")
    version, release_date = matches[0]
    try:
        date.fromisoformat(release_date)
    except ValueError as exc:
        raise ConsistencyError(f"invalid changelog release date: {release_date}") from exc
    return version, release_date


def _git(root: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", "") or str(exc)
        raise ConsistencyError(f"git {' '.join(args)} failed: {detail.strip()}") from exc
    return result.stdout.strip()


def check_tag(root: Path, version: str, release_date: str) -> None:
    """Require the release tag, its approved type, and matching creation date."""
    tag = f"v{version}"
    object_type = _git(root, "cat-file", "-t", tag)
    if object_type == "tag":
        tag_date = _git(root, "for-each-ref", "--format=%(taggerdate:short)", f"refs/tags/{tag}")
        if not tag_date:
            raise ConsistencyError(f"annotated tag {tag} has no tagger date")
    elif object_type == "commit" and tag in LIGHTWEIGHT_TAG_EXCEPTIONS:
        tag_date = _git(root, "show", "-s", "--format=%cs", tag)
    elif object_type == "commit":
        raise ConsistencyError(f"{tag} is lightweight; release tags must be annotated")
    else:
        raise ConsistencyError(f"{tag} has unsupported Git object type {object_type!r}")
    if tag_date != release_date:
        raise ConsistencyError(
            f"{tag} date {tag_date} does not match changelog date {release_date}"
        )


def check_documentation_examples(root: Path) -> None:
    """Reject fixed SWU versions in public docs; examples must use <version>."""
    paths = [root / "README.md", root / "buildroot" / "README.md"]
    paths.extend(sorted((root / "doc").glob("*.md")))
    stale = []
    for path in paths:
        if not path.is_file():
            continue
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if FIXED_ARTIFACT_RE.search(line):
                stale.append(f"{path.relative_to(root)}:{line_number}")
    if stale:
        raise ConsistencyError(
            "fixed-version firmware examples must use pisonic-<version>.swu: "
            + ", ".join(stale)
        )


def check_release_consistency(root: Path) -> str:
    """Validate versions, generated metadata, artifact naming, docs, and Git tag."""
    version_file = root / "lib" / "_version.py"
    version = project_version(version_file)
    package_version = pyproject_version(root / "pyproject.toml")
    changelog_version, release_date = latest_changelog_release(root / "CHANGELOG.md")
    if package_version != version:
        raise ConsistencyError(
            f"pyproject.toml version {package_version} does not match application version {version}"
        )
    if changelog_version != version:
        raise ConsistencyError(
            f"latest changelog release {changelog_version} does not match application version {version}"
        )

    expected_artifact = f"pisonic-{version}.swu"
    if artifact_name(version) != expected_artifact:
        raise ConsistencyError("SWUpdate artifact naming does not match the release version")

    metadata = release_metadata(version_file)
    if metadata.get("version") != version:
        raise ConsistencyError("generated radio-release.json metadata has the wrong version")
    # Also prove that the metadata contract remains JSON serializable.
    json.dumps(metadata, separators=(",", ":"), sort_keys=True)

    with tempfile.TemporaryDirectory(prefix="radio-release-check-") as directory:
        payload = Path(directory) / "rootfs.ext4.gz"
        payload.write_bytes(b"release-consistency-check")
        manifest = render_manifest(
            root / "buildroot" / "external" / "board" / "radio" / "sw-description.in",
            version,
            payload,
        )
    if f'version = "{version}";' not in manifest:
        raise ConsistencyError("rendered SWUpdate metadata has the wrong version")

    check_documentation_examples(root)
    check_tag(root, version, release_date)
    return version


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[1], help=argparse.SUPPRESS
    )
    args = parser.parse_args(argv)
    try:
        version = check_release_consistency(args.root.resolve())
    except (ConsistencyError, OSError, ValueError) as exc:
        print(f"check-release-consistency.py: ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"check-release-consistency.py: release v{version} is internally consistent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
