#!/usr/bin/env python3
"""Build, collect, and verify Raspberry Kitchen Radio firmware artifacts.

Local execution is the default and needs no SSH tooling. Remote execution stages
the current checkout with rsync, builds over SSH, and retrieves both artifacts.
Settings may be supplied by command line or an optional INI configuration file.
"""

from __future__ import annotations

import argparse
import configparser
import hashlib
import os
import re
import shlex
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Sequence

SCRIPT_PATH = Path(__file__).resolve()
DEFAULT_REPOSITORY = SCRIPT_PATH.parents[1]
DEFAULT_CONFIG = SCRIPT_PATH.with_name("build-image.ini")
CONFIG_SECTION = "build_image"
CONFIG_KEYS = {
    "execution",
    "repository",
    "host",
    "ssh_port",
    "remote_root",
    "buildroot_dir",
    "download_cache",
    "artifacts_dir",
    "jobs",
    "zip_image",
    "delete_remote_files",
    "install_host_packages",
    "allow_unverified_buildroot",
}


class BuildError(RuntimeError):
    """An expected configuration, build, or transfer operation failed."""


def run(
    argv: Sequence[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    check: bool = True,
    capture_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run a local command without invoking a shell."""
    command = [str(part) for part in argv]
    print("+", " ".join(shlex.quote(part) for part in command))
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            check=False,
            text=True,
            capture_output=capture_output,
        )
    except FileNotFoundError as exc:
        raise BuildError(f"required executable is not installed: {command[0]}") from exc
    if check and result.returncode != 0:
        raise BuildError(f"command failed with exit status {result.returncode}: {command[0]}")
    return result


def remote(
    host: str,
    command: str,
    *,
    port: int = 22,
    check: bool = True,
    capture_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run one non-interactive SSH command.

    ``-n`` redirects the remote stdin from /dev/null so the SSH channel does not
    stay open waiting on an inherited stdin. Without it, a long remote build can
    leave the ``ssh`` process blocked after the remote command has already
    finished (observed as a build that "hangs" once the artifacts exist),
    because the session's standard input is never closed.
    """
    ssh = ["ssh", "-n", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15"]
    if port != 22:
        ssh += ["-p", str(port)]
    ssh += [host, command]
    return run(
        ssh,
        check=check,
        capture_output=capture_output,
    )


def remote_output(host: str, command: str, *, port: int = 22) -> str:
    """Run a remote command and return trimmed standard output."""
    return remote(host, command, port=port, capture_output=True).stdout.strip()


def sha256(path: Path) -> str:
    """Calculate a local file's SHA-256 in bounded-size blocks."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def firmware_version(repository: Path) -> str:
    """Read the repository's unique X.Y.Z firmware version."""
    version_file = repository / "lib" / "_version.py"
    try:
        text = version_file.read_text(encoding="utf-8")
    except OSError as exc:
        raise BuildError(f"cannot read firmware version from {version_file}") from exc
    matches = re.findall(r'^__version__ = "([0-9]+\.[0-9]+\.[0-9]+)"$', text, re.MULTILINE)
    if len(matches) != 1:
        raise BuildError(f"cannot read one firmware version from {version_file}")
    return matches[0]


def git_revision(repository: Path) -> str:
    """Return <short-hash>, with -dirty when applicable, or unknown."""
    if shutil.which("git") is None:
        return "unknown"
    head = subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "--short", "HEAD"],
        check=False,
        text=True,
        capture_output=True,
    )
    if head.returncode != 0:
        return "unknown"
    revision = head.stdout.strip()
    status = subprocess.run(
        ["git", "-C", str(repository), "status", "--porcelain"],
        check=False,
        text=True,
        capture_output=True,
    )
    if status.returncode == 0 and status.stdout.strip():
        revision = f"{revision}-dirty"
    return revision


def git_commit(repository: Path) -> str:
    """Return the exact full repository commit, rejecting non-Git source trees."""
    if shutil.which("git") is None:
        raise BuildError("git is required to identify the repository commit")
    result = subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "--verify", "HEAD"],
        check=False,
        text=True,
        capture_output=True,
    )
    commit = result.stdout.strip()
    if result.returncode != 0 or not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise BuildError(f"cannot identify the repository commit in {repository}")
    return commit


def verify_zip_member_sha256(archive: Path, member: str, expected: str) -> None:
    """Check ZIP integrity and the SHA-256 of its single image member."""
    try:
        with zipfile.ZipFile(archive) as zipped:
            if zipped.namelist() != [member]:
                raise BuildError(f"unexpected ZIP members in {archive}: {zipped.namelist()}")
            bad_member = zipped.testzip()
            if bad_member is not None:
                raise BuildError(f"ZIP CRC check failed for {bad_member}")
            digest = hashlib.sha256()
            with zipped.open(member) as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
    except (OSError, zipfile.BadZipFile) as exc:
        raise BuildError(f"cannot validate ZIP archive {archive}: {exc}") from exc
    if digest.hexdigest() != expected:
        raise BuildError("image checksum inside ZIP does not match the built image")


def config_values(path: Path | None, *, explicit: bool) -> dict[str, str]:
    """Load and validate the optional INI file."""
    if path is None or (not path.exists() and not explicit):
        return {}
    if not path.is_file():
        raise BuildError(f"configuration file does not exist: {path}")
    parser = configparser.ConfigParser(interpolation=None)
    try:
        with path.open(encoding="utf-8") as stream:
            parser.read_file(stream)
    except (OSError, configparser.Error) as exc:
        raise BuildError(f"cannot read configuration file {path}: {exc}") from exc
    if CONFIG_SECTION not in parser:
        raise BuildError(f"configuration file must contain [{CONFIG_SECTION}]")
    values = dict(parser[CONFIG_SECTION])
    unknown = sorted(set(values) - CONFIG_KEYS)
    if unknown:
        raise BuildError(f"unknown configuration option(s): {', '.join(unknown)}")
    return values


def _config_bool(values: dict[str, str], key: str, default: bool) -> bool:
    if key not in values:
        return default
    normalized = values[key].strip().lower()
    if normalized in {"1", "yes", "true", "on"}:
        return True
    if normalized in {"0", "no", "false", "off"}:
        return False
    raise BuildError(f"configuration option {key} must be true or false")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse configuration first, then let CLI values override it."""
    pre_parser = argparse.ArgumentParser(add_help=False)
    pre_parser.add_argument("--config", type=Path)
    preliminary, _ = pre_parser.parse_known_args(argv)
    explicit_config = preliminary.config is not None
    config_path = preliminary.config.expanduser().resolve() if explicit_config else DEFAULT_CONFIG
    values = config_values(config_path, explicit=explicit_config)
    try:
        jobs = int(values["jobs"]) if "jobs" in values else None
    except ValueError as exc:
        raise BuildError("configuration option jobs must be an integer") from exc
    try:
        ssh_port = int(values.get("ssh_port", 22))
    except ValueError as exc:
        raise BuildError("configuration option ssh_port must be an integer") from exc

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        help=f"INI configuration file (default: {DEFAULT_CONFIG} when present)",
    )
    parser.add_argument(
        "--execution",
        choices=("local", "remote"),
        default=values.get("execution", "local"),
        help="build on this host (default) or over SSH",
    )
    parser.add_argument(
        "--repository",
        type=Path,
        default=Path(values.get("repository", str(DEFAULT_REPOSITORY))),
    )
    parser.add_argument("--host", default=values.get("host", ""))
    parser.add_argument(
        "--ssh-port",
        type=int,
        default=ssh_port,
        help="SSH/scp/rsync port for remote execution (default: 22; only needed for non-standard ports)",
    )
    parser.add_argument("--remote-root", default=values.get("remote_root", ""))
    parser.add_argument(
        "--buildroot-dir",
        type=Path,
        default=Path(values.get("buildroot_dir", "~/embedded/buildroot")),
    )
    parser.add_argument(
        "--download-cache",
        type=Path,
        default=Path(values.get("download_cache", "~/embedded/dl")),
    )
    parser.add_argument(
        "--artifacts-dir",
        type=Path,
        default=Path(values.get("artifacts_dir", str(DEFAULT_REPOSITORY / "artifacts"))),
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=jobs,
        help="parallel make jobs on the build host "
        "(default: auto-detect the build host's core count)",
    )
    parser.add_argument("--dry-run", action="store_true")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--fast", action="store_true")
    mode.add_argument("--clean", action="store_true")
    mode.add_argument("--dirclean", action="store_true")
    zip_group = parser.add_mutually_exclusive_group()
    zip_group.add_argument("--zip", dest="zip_image", action="store_true")
    zip_group.add_argument("--no-zip", dest="zip_image", action="store_false")
    parser.set_defaults(zip_image=_config_bool(values, "zip_image", True))
    delete_group = parser.add_mutually_exclusive_group()
    delete_group.add_argument(
        "--delete-remote-files", dest="delete_remote_files", action="store_true"
    )
    delete_group.add_argument("--no-delete", dest="delete_remote_files", action="store_false")
    parser.set_defaults(delete_remote_files=_config_bool(values, "delete_remote_files", True))
    apt_group = parser.add_mutually_exclusive_group()
    apt_group.add_argument(
        "--install-host-packages", dest="install_host_packages", action="store_true"
    )
    apt_group.add_argument("--no-apt", dest="install_host_packages", action="store_false")
    parser.set_defaults(install_host_packages=_config_bool(values, "install_host_packages", False))
    unverified_group = parser.add_mutually_exclusive_group()
    unverified_group.add_argument(
        "--allow-unverified-buildroot",
        dest="allow_unverified_buildroot",
        action="store_true",
        help="force the build even if the Buildroot checkout is dirty or not the pinned commit "
        "(development only)",
    )
    unverified_group.add_argument(
        "--verified-buildroot", dest="allow_unverified_buildroot", action="store_false"
    )
    parser.set_defaults(
        allow_unverified_buildroot=_config_bool(values, "allow_unverified_buildroot", False)
    )
    return parser.parse_args(argv)


def build_flags(args: argparse.Namespace) -> list[str]:
    # Only forward --jobs when explicitly configured; otherwise let build.sh
    # default to nproc, which over SSH is the remote build host's core count.
    flags: list[str] = []
    if args.jobs is not None:
        flags += ["--jobs", str(args.jobs)]
    if not args.install_host_packages:
        flags.append("--no-apt")
    if args.dirclean:
        flags.append("--dirclean")
    elif args.clean:
        flags.append("--clean")
    if args.allow_unverified_buildroot:
        flags.append("--allow-unverified-buildroot")
    return flags


def native_swupdate_checker(buildroot_dir: Path) -> str:
    """Return build.sh's preferred native SWUpdate checker path.

    The ``--fast`` phase runs ``make`` directly instead of ``build.sh``, so it
    does not inherit build.sh's ``SWUPDATE_CHECKER`` selection. Without it,
    ``build-swu.sh`` falls back to ``swupdate`` on PATH, which on many hosts is
    built for signed images and rejects the deliberately unsigned package. Point
    the fast rebuild at the same unsigned native checker build.sh prefers.
    """
    parent = buildroot_dir.parent
    return str(parent / "swupdate-checker-build" / "swupdate")


def local_build(args: argparse.Namespace, repository: Path) -> tuple[Path, Path]:
    """Run the supported build helper directly on this host."""
    environment = os.environ.copy()
    environment["BUILDROOT_DIR"] = str(args.buildroot_dir)
    environment["BR2_DL_DIR"] = str(args.download_cache)
    run(
        [str(repository / "buildroot" / "build.sh"), *build_flags(args)],
        cwd=repository,
        env=environment,
    )
    if args.fast:
        run(
            ["make", "radio-app-dirclean", "radio-equalizer-dirclean"],
            cwd=args.buildroot_dir,
            env=environment,
        )
        fast_env = dict(environment)
        fast_env["SWUPDATE_CHECKER"] = native_swupdate_checker(args.buildroot_dir)
        run(["make", f"-j{args.jobs}"], cwd=args.buildroot_dir, env=fast_env)
    images = args.buildroot_dir / "output" / "images"
    return images / "sdcard.img", images / f"kitchen-radio-{args.version}.swu"


def scp_base(args: argparse.Namespace) -> list[str]:
    """Return the scp command prefix, adding the port only when non-standard."""
    command = ["scp", "-o", "BatchMode=yes"]
    if args.ssh_port != 22:
        command += ["-P", str(args.ssh_port)]
    return command


def remote_build(args: argparse.Namespace, repository: Path) -> tuple[str, str]:
    """Stage the checkout, build it remotely, and return artifact paths."""
    repository_commit = git_commit(repository)
    remote_repository = f"{args.remote_root.rstrip('/')}/radio-repo-{args.version}-{args.stamp}"
    remote(args.host, f"mkdir -p {shlex.quote(remote_repository)}", port=args.ssh_port)
    sync = ["rsync", "-az"]
    if args.ssh_port != 22:
        sync += ["-e", f"ssh -p {args.ssh_port}"]
    if args.delete_remote_files:
        sync.append("--delete")
    sync.extend(
        [
            "--exclude=.git/",
            "--exclude=.venv/",
            "--exclude=artifacts/",
            "--exclude=__pycache__/",
            "--exclude=.pytest_cache/",
            "--exclude=.mypy_cache/",
            "--exclude=.ruff_cache/",
            "--exclude=.coverage",
            "--exclude=.DS_Store",
            f"{repository}/",
            f"{args.host}:{remote_repository}/",
        ]
    )
    run(sync)
    flags = " ".join(shlex.quote(flag) for flag in build_flags(args))
    build = (
        f"cd {shlex.quote(remote_repository)} && "
        f"BUILDROOT_DIR={shlex.quote(str(args.buildroot_dir))} "
        f"BR2_DL_DIR={shlex.quote(str(args.download_cache))} "
        f"RADIO_REPO_COMMIT={shlex.quote(repository_commit)} "
        f"./buildroot/build.sh {flags}"
    )
    remote(args.host, build, port=args.ssh_port)
    if args.fast:
        checker = native_swupdate_checker(args.buildroot_dir)
        fast = (
            f"cd {shlex.quote(str(args.buildroot_dir))} && "
            "make radio-app-dirclean radio-equalizer-dirclean && "
            f"SWUPDATE_CHECKER={shlex.quote(checker)} make -j{args.jobs}"
        )
        remote(args.host, fast, port=args.ssh_port)
    images = f"{str(args.buildroot_dir).rstrip('/')}/output/images"
    image = f"{images}/sdcard.img"
    swu = f"{images}/kitchen-radio-{args.version}.swu"
    remote(
        args.host,
        f"test -s {shlex.quote(image)} && test -s {shlex.quote(swu)}",
        port=args.ssh_port,
    )
    return image, swu


def copy_local(source: Path, destination: Path, expected_hash: str) -> None:
    shutil.copy2(source, destination)
    if sha256(destination) != expected_hash:
        destination.unlink(missing_ok=True)
        raise BuildError(f"checksum mismatch after copying {source}")


def main(argv: Sequence[str] | None = None) -> int:
    """Resolve settings, build, collect both artifacts, and verify them."""
    args = parse_args(argv)
    repository = args.repository.expanduser().resolve()
    args.artifacts_dir = args.artifacts_dir.expanduser().resolve()
    if args.execution == "local":
        args.buildroot_dir = args.buildroot_dir.expanduser().resolve()
        args.download_cache = args.download_cache.expanduser().resolve()
    elif not args.host or not args.remote_root:
        raise BuildError("remote execution requires both host and remote_root")
    if not repository.is_dir() or not (repository / "buildroot" / "build.sh").is_file():
        raise BuildError(f"not a Raspberry Kitchen Radio repository: {repository}")
    if args.jobs is not None and args.jobs < 1:
        raise BuildError("jobs must be at least 1")
    if not 1 <= args.ssh_port <= 65535:
        raise BuildError("ssh_port must be between 1 and 65535")

    args.version = firmware_version(repository)
    revision = git_revision(repository)
    args.stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = f"kitchen-radio-{args.version}-{revision}-{args.stamp}"
    raw_name = f"{base}-sdcard.img"
    image_output = args.artifacts_dir / (f"{raw_name}.zip" if args.zip_image else raw_name)
    swu_output = args.artifacts_dir / f"{base}.swu"

    print(f"Execution: {args.execution}")
    print(f"Repository: {repository}")
    print(f"Buildroot: {args.buildroot_dir}")
    print(f"Download cache: {args.download_cache}")
    if args.jobs is None:
        print("Parallel jobs: auto (nproc on the build host)")
    else:
        print(f"Parallel jobs: {args.jobs}")
    if args.allow_unverified_buildroot:
        print("Allow unverified Buildroot: yes (dirty/unpinned checkout permitted)")
    if args.execution == "remote":
        print(f"SSH host: {args.host}")
        print(f"SSH port: {args.ssh_port}")
        print(f"Remote staging root: {args.remote_root}")
    print(f"Image output: {image_output}")
    print(f"Firmware update output: {swu_output}")
    if args.dry_run:
        print("Dry run: no files were copied and no build was started.")
        return 0
    for output in (image_output, swu_output):
        if output.exists():
            raise BuildError(f"refusing to overwrite existing artifact: {output}")
    args.artifacts_dir.mkdir(parents=True, exist_ok=True)

    if args.execution == "local":
        source_image, source_swu = local_build(args, repository)
        if not source_image.is_file() or not source_swu.is_file():
            raise BuildError("build completed without both expected artifacts")
        image_hash = sha256(source_image)
        swu_hash = sha256(source_swu)
        copy_local(source_swu, swu_output, swu_hash)
        if args.zip_image:
            with zipfile.ZipFile(
                image_output, "w", zipfile.ZIP_DEFLATED, allowZip64=True
            ) as zipped:
                zipped.write(source_image, raw_name)
            verify_zip_member_sha256(image_output, raw_name, image_hash)
        else:
            copy_local(source_image, image_output, image_hash)
    else:
        remote_image, remote_swu = remote_build(args, repository)
        image_hash = remote_output(
            args.host, f"sha256sum {shlex.quote(remote_image)}", port=args.ssh_port
        ).split()[0]
        swu_hash = remote_output(
            args.host, f"sha256sum {shlex.quote(remote_swu)}", port=args.ssh_port
        ).split()[0]
        run([*scp_base(args), f"{args.host}:{remote_swu}", str(swu_output)])
        if sha256(swu_output) != swu_hash:
            swu_output.unlink(missing_ok=True)
            raise BuildError("firmware checksum mismatch after transfer")
        if args.zip_image:
            remote_zip = f"{remote_image}.{args.stamp}.zip"
            python = (
                "import sys,zipfile;"
                "z=zipfile.ZipFile(sys.argv[1],'w',zipfile.ZIP_DEFLATED,allowZip64=True);"
                "z.write(sys.argv[2],sys.argv[3]);z.close()"
            )
            remote(
                args.host,
                f"python3 -c {shlex.quote(python)} {shlex.quote(remote_zip)} "
                f"{shlex.quote(remote_image)} {shlex.quote(raw_name)}",
                port=args.ssh_port,
            )
            run([*scp_base(args), f"{args.host}:{remote_zip}", str(image_output)])
            remote(args.host, f"rm -f {shlex.quote(remote_zip)}", port=args.ssh_port, check=False)
            verify_zip_member_sha256(image_output, raw_name, image_hash)
        else:
            run([*scp_base(args), f"{args.host}:{remote_image}", str(image_output)])
            if sha256(image_output) != image_hash:
                image_output.unlink(missing_ok=True)
                raise BuildError("image checksum mismatch after transfer")

    print("Build and artifact collection completed successfully.")
    print(f"Image: {image_output}")
    print(f"Raw image SHA-256: {image_hash}")
    print(f"Firmware update: {swu_output}")
    print(f"Firmware update SHA-256: {swu_hash}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BuildError as error:
        print(f"build_image.py: ERROR: {error}", file=sys.stderr)
        raise SystemExit(1) from error
