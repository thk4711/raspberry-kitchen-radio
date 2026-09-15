#!/usr/bin/env python3
"""Build and validate the deterministic unsigned kitchen-radio SWUpdate archive."""

import argparse
import gzip
import hashlib
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import BinaryIO, List, Optional, Tuple

VERSION_RE = re.compile(r'^__version__\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+)"\s*$', re.MULTILINE)
PLACEHOLDER_RE = re.compile(r"@[A-Z0-9_]+@")
HARDWARE_REVISION = "1"
PAYLOAD_NAME = "rootfs.ext4.gz"
PERSISTENT_SCHEMA = 1
PERSISTENT_READER_SCHEMA = 1
MINIMUM_ROLLBACK_READER_SCHEMA = 1


def artifact_name(version: str) -> str:
    """Return the canonical versioned SWUpdate artifact name."""
    return f"kitchen-radio-{version}.swu"


def project_version(version_file: Path) -> str:
    """Return the one strict semantic version assignment in *version_file*."""
    matches = VERSION_RE.findall(version_file.read_text(encoding="utf-8"))
    if len(matches) != 1:
        raise ValueError(f"expected exactly one X.Y.Z __version__ assignment in {version_file}")
    return matches[0]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def render_manifest(template: Path, version: str, payload: Path) -> str:
    """Render manifest values that describe the compressed CPIO member."""
    text = template.read_text(encoding="utf-8")
    replacements = {
        "@FIRMWARE_VERSION@": version,
        "@PERSISTENT_SCHEMA@": str(PERSISTENT_SCHEMA),
        "@PERSISTENT_READER_SCHEMA@": str(PERSISTENT_READER_SCHEMA),
        "@MINIMUM_ROLLBACK_READER_SCHEMA@": str(MINIMUM_ROLLBACK_READER_SCHEMA),
        "@ROOTFS_ARCHIVE_SIZE@": str(payload.stat().st_size),
        "@ROOTFS_ARCHIVE_SHA256@": sha256(payload),
    }
    for marker, value in replacements.items():
        text = text.replace(marker, value)
    unresolved = PLACEHOLDER_RE.findall(text)
    if unresolved:
        raise ValueError(f"unresolved manifest placeholders: {', '.join(sorted(set(unresolved)))}")
    return text


def _newc_header(name: str, size: int, checksum: int, inode: int) -> bytes:
    values = (inode, 0o100644, 0, 0, 1, 0, size, 0, 0, 0, 0, len(name) + 1, checksum)
    return b"070702" + b"".join(f"{value:08x}".encode("ascii") for value in values)


def _padding(length: int) -> bytes:
    return b"\0" * ((-length) % 4)


def _byte_sum(path: Path) -> int:
    checksum = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            checksum = (checksum + sum(chunk)) & 0xFFFFFFFF
    return checksum


def write_cpio(output: Path, members: List[Tuple[str, Path]]) -> None:
    """Write a deterministic SVR4 CRC CPIO, preserving the supplied member order."""
    with output.open("wb") as archive:
        for inode, (name, source) in enumerate(members, 1):
            encoded_name = name.encode("utf-8") + b"\0"
            size = source.stat().st_size
            archive.write(_newc_header(name, size, _byte_sum(source), inode))
            archive.write(encoded_name)
            archive.write(_padding(110 + len(encoded_name)))
            with source.open("rb") as member:
                shutil.copyfileobj(member, archive, length=1024 * 1024)
            archive.write(_padding(size))
        trailer = b"TRAILER!!!\0"
        archive.write(_newc_header("TRAILER!!!", 0, 0, len(members) + 1))
        archive.write(trailer)
        archive.write(_padding(110 + len(trailer)))
        archive.write(b"\0" * ((-archive.tell()) % 512))


def read_cpio(path: Path) -> List[Tuple[str, bytes]]:
    """Read the restricted newc/CRC form emitted by :func:`write_cpio`."""
    result: List[Tuple[str, bytes]] = []
    data = path.read_bytes()
    offset = 0
    while offset + 110 <= len(data):
        header = data[offset : offset + 110]
        if header[:6] not in (b"070701", b"070702"):
            raise ValueError("invalid CPIO header")
        fields = [int(header[pos : pos + 8], 16) for pos in range(6, 110, 8)]
        size, name_size, expected_sum = fields[6], fields[11], fields[12]
        offset += 110
        name_bytes = data[offset : offset + name_size]
        if len(name_bytes) != name_size or not name_bytes.endswith(b"\0"):
            raise ValueError("invalid CPIO member name")
        name = name_bytes[:-1].decode("utf-8")
        offset += name_size
        offset += (-offset) % 4
        member = data[offset : offset + size]
        if len(member) != size:
            raise ValueError("truncated CPIO member")
        if header[:6] == b"070702" and (sum(member) & 0xFFFFFFFF) != expected_sum:
            raise ValueError(f"CPIO checksum mismatch for {name}")
        offset += size
        offset += (-offset) % 4
        if name == "TRAILER!!!":
            return result
        result.append((name, member))
    raise ValueError("CPIO trailer is missing")


def _read_exact(stream: BinaryIO, length: int) -> bytes:
    data = stream.read(length)
    if len(data) != length:
        raise ValueError("truncated CPIO archive")
    return data


def _skip_exact(stream: BinaryIO, length: int) -> None:
    if length:
        _read_exact(stream, length)


def _stream_member(stream: BinaryIO, size: int, output: BinaryIO) -> Tuple[str, int]:
    digest = hashlib.sha256()
    checksum = 0
    remaining = size
    while remaining:
        chunk = _read_exact(stream, min(1024 * 1024, remaining))
        output.write(chunk)
        digest.update(chunk)
        checksum = (checksum + sum(chunk)) & 0xFFFFFFFF
        remaining -= len(chunk)
    return digest.hexdigest(), checksum


def _same_content(first: Path, second: Path) -> bool:
    if first.stat().st_size != second.stat().st_size:
        return False
    with first.open("rb") as left, second.open("rb") as right:
        while True:
            left_chunk = left.read(1024 * 1024)
            right_chunk = right.read(1024 * 1024)
            if left_chunk != right_chunk:
                return False
            if not left_chunk:
                return True


def validate_archive(path: Path, source_rootfs: Optional[Path] = None) -> None:
    """Validate ordering, manifest metadata, payload integrity, and safe contents."""
    with path.open("rb") as archive, tempfile.TemporaryDirectory(prefix="radio-swu-check-") as tmp:
        extracted = Path(tmp) / PAYLOAD_NAME
        names = []
        manifest = ""
        payload_size = 0
        payload_hash = ""
        while True:
            header = _read_exact(archive, 110)
            if header[:6] not in (b"070701", b"070702"):
                raise ValueError("invalid CPIO header")
            fields = [int(header[pos : pos + 8], 16) for pos in range(6, 110, 8)]
            size, name_size, expected_sum = fields[6], fields[11], fields[12]
            name_bytes = _read_exact(archive, name_size)
            if not name_bytes.endswith(b"\0"):
                raise ValueError("invalid CPIO member name")
            name = name_bytes[:-1].decode("utf-8")
            _skip_exact(archive, (-(110 + name_size)) % 4)
            if name == "TRAILER!!!":
                break
            names.append(name)
            if name == "sw-description":
                data = _read_exact(archive, size)
                actual_sum = sum(data) & 0xFFFFFFFF
                manifest = data.decode("utf-8")
            elif name == PAYLOAD_NAME:
                with extracted.open("wb") as output:
                    payload_hash, actual_sum = _stream_member(archive, size, output)
                payload_size = size
            else:
                raise ValueError(f"unexpected SWU member: {name}")
            if header[:6] == b"070702" and actual_sum != expected_sum:
                raise ValueError(f"CPIO checksum mismatch for {name}")
            _skip_exact(archive, (-size) % 4)

        if names != ["sw-description", PAYLOAD_NAME]:
            raise ValueError("SWU must contain sw-description first and exactly one rootfs payload")
        if source_rootfs is not None:
            uncompressed = Path(tmp) / "rootfs.ext4"
            with gzip.open(extracted, "rb") as source, uncompressed.open("wb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)
            if not _same_content(uncompressed, source_rootfs):
                raise ValueError("compressed payload does not reproduce the source rootfs")

    expected_size = re.search(r"size = ([0-9]+);", manifest)
    expected_hash = re.search(r'sha256 = "([0-9a-f]{64})";', manifest)
    if not expected_size or int(expected_size.group(1)) != payload_size:
        raise ValueError("manifest payload size does not match compressed archive member")
    if not expected_hash or expected_hash.group(1) != payload_hash:
        raise ValueError("manifest payload SHA-256 does not match compressed archive member")
    if f'hardware-compatibility: [ "{HARDWARE_REVISION}" ];' not in manifest:
        raise ValueError("manifest hardware compatibility is missing")
    if manifest.count(f'filename = "{PAYLOAD_NAME}";') != 2:
        raise ValueError("both A/B selections must reference the shared payload")


def build_archive(
    rootfs: Path,
    template: Path,
    version_file: Path,
    output_dir: Path,
    slot_size: int,
    swupdate_checker: Path,
) -> Path:
    """Create, self-validate, optionally SWUpdate-check, and atomically publish an SWU."""
    if not rootfs.is_file() or rootfs.stat().st_size == 0:
        raise ValueError(f"rootfs payload is missing or empty: {rootfs}")
    if rootfs.stat().st_size > slot_size:
        raise ValueError(f"rootfs is larger than its {slot_size}-byte firmware slot")
    version = project_version(version_file)
    output_dir.mkdir(parents=True, exist_ok=True)
    artifact = output_dir / artifact_name(version)
    for stale in output_dir.glob("kitchen-radio-*.swu"):
        stale.unlink()

    with tempfile.TemporaryDirectory(prefix="radio-swu-") as directory:
        staging = Path(directory)
        payload = staging / PAYLOAD_NAME
        with rootfs.open("rb") as source, payload.open("wb") as raw_output:
            with gzip.GzipFile(
                filename="", mode="wb", fileobj=raw_output, mtime=0, compresslevel=9
            ) as gz:
                shutil.copyfileobj(source, gz, length=1024 * 1024)
        manifest = render_manifest(template, version, payload)
        manifest_path = staging / "sw-description"
        manifest_path.write_text(manifest, encoding="utf-8")
        candidate = staging / artifact.name
        write_cpio(candidate, [("sw-description", manifest_path), (PAYLOAD_NAME, payload)])
        validate_archive(candidate, rootfs)
        for selection in ("slot-a", "slot-b"):
            subprocess.run(
                [
                    str(swupdate_checker),
                    "-c",
                    "-i",
                    str(candidate),
                    "-e",
                    f"stable,{selection}",
                ],
                check=True,
            )
        # The staging directory may live on a different filesystem from the
        # Buildroot output (for example, /tmp is tmpfs on the build host).
        # Copy into the destination directory first, then atomically publish.
        pending = artifact.with_name(f".{artifact.name}.tmp")
        shutil.copyfile(candidate, pending)
        pending.replace(artifact)
    return artifact


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rootfs", type=Path, required=True)
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--version-file", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--slot-size", type=int, required=True)
    parser.add_argument("--swupdate-checker", type=Path, required=True)
    args = parser.parse_args()
    artifact = build_archive(
        args.rootfs,
        args.template,
        args.version_file,
        args.output_dir,
        args.slot_size,
        args.swupdate_checker,
    )
    print(f"build_firmware_swu.py: built and validated {artifact}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
