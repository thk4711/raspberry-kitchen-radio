#!/usr/bin/env python3
"""Cut a Raspberry Kitchen Radio release: build, stage, checksum, and publish.

This orchestrates the mechanical, automatable tail of the ``CHANGELOG.md`` release
checklist. It:

  1. validates that the working tree already describes the requested X.Y.Z
     release (``lib/_version.py``, ``pyproject.toml``, the top ``CHANGELOG.md``
     heading) and runs ``scripts/check-release-consistency.py``;
  2. builds the SD card image and the firmware ``.swu`` through
     ``scripts/build_image.py`` using the current ``scripts/build-image.ini``
     (unless ``--skip-build``);
  3. reads the release-notes markdown from an explicit ``--notes`` file;
  4. stages clean, version-named release assets and writes ``SHA256SUMS``;
  5. creates and uploads the GitHub release with the ``gh`` CLI.

It deliberately does NOT bump versions, create the annotated tag (unless asked
with ``--create-tag``), or replace the human on-hardware A/B validation. Those
stay manual gates in the checklist.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Sequence

SCRIPT_PATH = Path(__file__).resolve()
DEFAULT_REPOSITORY = SCRIPT_PATH.parents[1]
DEFAULT_CONFIG = SCRIPT_PATH.with_name("build-image.ini")
BUILD_IMAGE = SCRIPT_PATH.with_name("build_image.py")
CONSISTENCY_CHECK = SCRIPT_PATH.with_name("check-release-consistency.py")

SEMVER_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
VERSION_FILE_RE = re.compile(r'^__version__ = "([0-9]+\.[0-9]+\.[0-9]+)"$', re.MULTILINE)
PYPROJECT_VERSION_RE = re.compile(r'^version\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+)"\s*$', re.MULTILINE)
CHANGELOG_RELEASE_RE = re.compile(
    r"^## \[([0-9]+\.[0-9]+\.[0-9]+)\] - \d{4}-\d{2}-\d{2}$", re.MULTILINE
)
# The timestamped artifacts build_image.py leaves in artifacts/, e.g.
# kitchen-radio-0.3.0-f359584-20260916-220329-sdcard.img.zip
IMAGE_STAMP_RE = r"-[0-9a-f]+(?:-dirty)?-[0-9]{8}-[0-9]{6}-sdcard\.img\.zip$"
SWU_STAMP_RE = r"-[0-9a-f]+(?:-dirty)?-[0-9]{8}-[0-9]{6}\.swu$"


class ReleaseError(RuntimeError):
    """An expected release precondition, build, or publish step failed."""


def run(
    argv: Sequence[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
    capture_output: bool = False,
    dry_run: bool = False,
) -> subprocess.CompletedProcess[str] | None:
    """Run a local command without invoking a shell, echoing it first."""
    command = [str(part) for part in argv]
    print("+", " ".join(shlex.quote(part) for part in command))
    if dry_run:
        return None
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            check=False,
            text=True,
            capture_output=capture_output,
        )
    except FileNotFoundError as exc:
        raise ReleaseError(f"required executable is not installed: {command[0]}") from exc
    if check and result.returncode != 0:
        raise ReleaseError(f"command failed with exit status {result.returncode}: {command[0]}")
    return result


def sha256(path: Path) -> str:
    """Calculate a file's SHA-256 in bounded-size blocks."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def human_size(num_bytes: int) -> str:
    """Return a compact human-readable byte count."""
    size = float(num_bytes)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024.0 or unit == "GiB":
            return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{num_bytes} B"


def _one_version(pattern: re.Pattern, text: str, label: str) -> str:
    matches = pattern.findall(text)
    if len(matches) != 1:
        raise ReleaseError(f"expected exactly one {label}, found {len(matches)}")
    return matches[0]


def repository_version(repository: Path) -> str:
    """Read the X.Y.Z version from lib/_version.py (source of truth)."""
    version_file = repository / "lib" / "_version.py"
    try:
        text = version_file.read_text(encoding="utf-8")
    except OSError as exc:
        raise ReleaseError(f"cannot read {version_file}") from exc
    return _one_version(VERSION_FILE_RE, text, "lib/_version.py __version__")


def assert_versions_match(repository: Path, version: str) -> None:
    """Fail unless _version.py, pyproject.toml, and CHANGELOG all equal version."""
    lib_version = repository_version(repository)
    if lib_version != version:
        raise ReleaseError(
            f"lib/_version.py is {lib_version}, not the requested {version}; "
            "bump the version and update the changelog before releasing"
        )
    pyproject = repository / "pyproject.toml"
    try:
        package_version = _one_version(
            PYPROJECT_VERSION_RE, pyproject.read_text(encoding="utf-8"), "pyproject.toml version"
        )
    except OSError as exc:
        raise ReleaseError(f"cannot read {pyproject}") from exc
    if package_version != version:
        raise ReleaseError(f"pyproject.toml version {package_version} does not match {version}")
    changelog = repository / "CHANGELOG.md"
    try:
        releases = CHANGELOG_RELEASE_RE.findall(changelog.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ReleaseError(f"cannot read {changelog}") from exc
    if not releases:
        raise ReleaseError("CHANGELOG.md has no X.Y.Z release heading with an ISO date")
    if releases[0] != version:
        raise ReleaseError(f"latest CHANGELOG.md release {releases[0]} does not match {version}")


def require_tools(*tools: str) -> None:
    """Fail unless every required executable is on PATH."""
    missing = [tool for tool in tools if shutil.which(tool) is None]
    if missing:
        raise ReleaseError(f"required executable(s) not installed: {', '.join(missing)}")


def check_gh_auth(dry_run: bool) -> None:
    """Confirm the gh CLI is authenticated for github.com."""
    result = run(
        ["gh", "auth", "status", "--hostname", "github.com"],
        check=False,
        capture_output=True,
        dry_run=dry_run,
    )
    if result is None:
        return
    if result.returncode != 0:
        raise ReleaseError("gh is not authenticated for github.com; run 'gh auth login' first")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("version", help="new release number, e.g. 0.4.0")
    parser.add_argument(
        "--notes",
        type=Path,
        required=True,
        metavar="FILE.md",
        help="markdown file whose contents become the GitHub release body",
    )
    parser.add_argument(
        "--repository",
        type=Path,
        default=DEFAULT_REPOSITORY,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help=f"build-image INI passed to build_image.py (default: {DEFAULT_CONFIG})",
    )
    parser.add_argument(
        "--artifacts-dir",
        type=Path,
        default=None,
        help="directory holding build artifacts (default: <repository>/artifacts)",
    )
    draft_group = parser.add_mutually_exclusive_group()
    draft_group.add_argument(
        "--draft", dest="draft", action="store_true", help="create a draft release (default)"
    )
    draft_group.add_argument(
        "--publish",
        dest="draft",
        action="store_false",
        help="publish immediately instead of creating a draft",
    )
    parser.set_defaults(draft=True)
    parser.add_argument(
        "--skip-build",
        action="store_true",
        help="reuse existing artifacts instead of running build_image.py",
    )
    parser.add_argument(
        "--create-tag",
        action="store_true",
        help="create the annotated tag vX.Y.Z if it is missing (dated today)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="overwrite staged assets and update an existing release/draft",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="print actions without changing state"
    )
    # Build passthroughs forwarded verbatim to build_image.py.
    parser.add_argument("--clean", action="store_true", help="forward --clean to build_image.py")
    parser.add_argument("--fast", action="store_true", help="forward --fast to build_image.py")
    parser.add_argument("--jobs", type=int, default=None, help="forward --jobs to build_image.py")
    zip_group = parser.add_mutually_exclusive_group()
    zip_group.add_argument("--zip", dest="zip_image", action="store_true", default=None)
    zip_group.add_argument("--no-zip", dest="zip_image", action="store_false", default=None)
    return parser.parse_args(argv)


def build_artifacts(args: argparse.Namespace) -> None:
    """Run build_image.py with the configured INI and forwarded flags."""
    command: list[str] = [sys.executable, str(BUILD_IMAGE), "--config", str(args.config)]
    if args.clean:
        command.append("--clean")
    if args.fast:
        command.append("--fast")
    if args.jobs is not None:
        command += ["--jobs", str(args.jobs)]
    if args.zip_image is True:
        command.append("--zip")
    elif args.zip_image is False:
        command.append("--no-zip")
    run(command, cwd=args.repository, dry_run=args.dry_run)


def newest_match(artifacts_dir: Path, version: str, suffix_re: str, label: str) -> Path:
    """Return the newest timestamped build artifact for version, or fail."""
    pattern = re.compile(rf"^kitchen-radio-{re.escape(version)}{suffix_re}")
    candidates = sorted(
        (
            path
            for path in artifacts_dir.glob(f"kitchen-radio-{version}-*")
            if pattern.match(path.name)
        ),
        key=lambda path: path.stat().st_mtime,
    )
    if not candidates:
        raise ReleaseError(
            f"no built {label} for {version} in {artifacts_dir}; "
            "run without --skip-build or check the build output"
        )
    return candidates[-1]


def stage_asset(source: Path, destination: Path, force: bool, dry_run: bool) -> None:
    """Copy a timestamped build output to its clean, version-named asset."""
    if destination.exists() and not force:
        if sha256(destination) == sha256(source):
            print(f"= {destination.name} already staged and identical")
            return
        raise ReleaseError(f"refusing to overwrite existing {destination}; pass --force")
    print(f"+ stage {source.name} -> {destination.name}")
    if dry_run:
        return
    shutil.copy2(source, destination)
    if sha256(destination) != sha256(source):
        destination.unlink(missing_ok=True)
        raise ReleaseError(f"checksum mismatch after staging {destination}")


def write_checksums(assets: list[Path], output: Path, dry_run: bool) -> dict[str, str]:
    """Write a SHA256SUMS file listing the clean assets, and return the hashes."""
    hashes = {asset.name: sha256(asset) for asset in assets}
    lines = [f"{hashes[asset.name]}  {asset.name}\n" for asset in assets]
    print(f"+ write {output.name}")
    if not dry_run:
        output.write_text("".join(lines), encoding="utf-8")
    return hashes


def tag_exists(repository: Path, tag: str) -> bool:
    result = subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "--verify", "--quiet", f"{tag}^{{}}"],
        check=False,
        text=True,
        capture_output=True,
    )
    return result.returncode == 0


def ensure_tag(args: argparse.Namespace, tag: str) -> None:
    """Require the annotated release tag, optionally creating it on request."""
    if tag_exists(args.repository, tag):
        return
    if not args.create_tag:
        raise ReleaseError(
            f"annotated tag {tag} does not exist; create it per the CHANGELOG "
            f'checklist (git tag -a {tag} -m "{tag}") or pass --create-tag'
        )
    run(
        ["git", "-C", str(args.repository), "tag", "-a", tag, "-m", tag],
        dry_run=args.dry_run,
    )


def release_exists(tag: str, dry_run: bool) -> bool:
    result = run(
        ["gh", "release", "view", tag, "--json", "tagName"],
        check=False,
        capture_output=True,
        dry_run=dry_run,
    )
    if result is None:
        return False
    return result.returncode == 0


def publish_release(args: argparse.Namespace, tag: str, assets: list[Path]) -> None:
    """Create the release, or update it under --force, and attach assets."""
    title = f"Raspberry Kitchen Radio {tag}"
    asset_paths = [str(asset) for asset in assets]
    if release_exists(tag, args.dry_run):
        if not args.force:
            raise ReleaseError(f"a release for {tag} already exists; pass --force to update it")
        run(
            ["gh", "release", "edit", tag, "--title", title, "--notes-file", str(args.notes)],
            dry_run=args.dry_run,
        )
        run(
            ["gh", "release", "upload", tag, *asset_paths, "--clobber"],
            dry_run=args.dry_run,
        )
        return
    command = ["gh", "release", "create", tag, "--title", title, "--notes-file", str(args.notes)]
    if args.draft:
        command.append("--draft")
    command += asset_paths
    run(command, dry_run=args.dry_run)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    args.repository = args.repository.expanduser().resolve()
    args.config = args.config.expanduser().resolve()
    args.notes = args.notes.expanduser().resolve()
    args.artifacts_dir = (
        args.artifacts_dir.expanduser().resolve()
        if args.artifacts_dir is not None
        else args.repository / "artifacts"
    )

    if not SEMVER_RE.match(args.version):
        raise ReleaseError(f"version must be X.Y.Z, got: {args.version}")
    if not (args.repository / "buildroot" / "build.sh").is_file():
        raise ReleaseError(f"not a Raspberry Kitchen Radio repository: {args.repository}")
    if not args.notes.is_file() or not args.notes.read_text(encoding="utf-8").strip():
        raise ReleaseError(f"release notes file is missing or empty: {args.notes}")

    tag = f"v{args.version}"
    print(f"Releasing {tag}")
    print(f"Repository: {args.repository}")
    print(f"Release notes: {args.notes}")
    mode = "draft" if args.draft else "published"
    suffix = " (dry run)" if args.dry_run else ""
    print(f"Mode: {mode}{suffix}")

    require_tools("git", "gh")
    assert_versions_match(args.repository, args.version)
    check_gh_auth(args.dry_run)
    ensure_tag(args, tag)
    run([sys.executable, str(CONSISTENCY_CHECK)], cwd=args.repository, dry_run=args.dry_run)

    if not args.skip_build:
        build_artifacts(args)
    else:
        print("= skipping build (--skip-build); reusing existing artifacts")

    args.artifacts_dir.mkdir(parents=True, exist_ok=True)
    clean_image = args.artifacts_dir / f"kitchen-radio-{args.version}-sdcard.img.zip"
    clean_swu = args.artifacts_dir / f"kitchen-radio-{args.version}.swu"

    if args.skip_build:
        if not clean_image.is_file() or not clean_swu.is_file():
            raise ReleaseError(
                f"--skip-build requires staged assets {clean_image.name} and "
                f"{clean_swu.name} in {args.artifacts_dir}"
            )
    else:
        source_image = newest_match(
            args.artifacts_dir, args.version, IMAGE_STAMP_RE, "SD card image"
        )
        source_swu = newest_match(args.artifacts_dir, args.version, SWU_STAMP_RE, "firmware .swu")
        stage_asset(source_image, clean_image, args.force, args.dry_run)
        stage_asset(source_swu, clean_swu, args.force, args.dry_run)

    checksums_file = args.artifacts_dir / "SHA256SUMS"
    hashes: dict[str, str] = {}
    if not args.dry_run:
        hashes = write_checksums([clean_image, clean_swu], checksums_file, args.dry_run)
    else:
        print(f"+ write {checksums_file.name}")

    publish_release(args, tag, [clean_image, clean_swu, checksums_file])

    print()
    print("Release summary")
    print(f"  Tag:   {tag} ({mode})")
    for asset in (clean_image, clean_swu):
        if asset.is_file():
            size = human_size(asset.stat().st_size)
            digest = hashes.get(asset.name, "" if args.dry_run else sha256(asset))
            print(f"  Asset: {asset.name}  {size}  {digest}")
        else:
            print(f"  Asset: {asset.name}  (not present; dry run)")
    if not args.dry_run:
        run(["gh", "release", "view", tag, "--json", "url", "--jq", ".url"], check=False)
    if not args.draft:
        print(
            "\nReminder: publishing assumes the on-hardware A/B test "
            "(CHANGELOG checklist step 10) already passed."
        )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ReleaseError as error:
        print(f"release.py: ERROR: {error}", file=sys.stderr)
        raise SystemExit(1) from error
