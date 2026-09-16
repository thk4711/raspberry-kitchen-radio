"""Host-side tests for deterministic firmware SWU artifact generation."""

import gzip
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path

import pytest

from scripts import build_firmware_swu as swu

ROOT = Path(__file__).resolve().parents[1]
BOARD = ROOT / "buildroot" / "external" / "board" / "radio"
TEMPLATE = BOARD / "sw-description.in"
VERSION_FILE = ROOT / "lib" / "_version.py"
BUILD_SCRIPT = ROOT / "buildroot" / "build.sh"
BUILD_SWU_SCRIPT = BOARD / "build-swu.sh"
ARTIFACT_VALIDATOR = ROOT / "buildroot" / "validate-artifacts.sh"
RELEASE_GENERATOR = ROOT / "scripts" / "generate_release_metadata.py"


@pytest.fixture
def checker(tmp_path):
    path = tmp_path / "swupdate"
    # Mirror the exact host-side check invocation, including the hardware
    # revision override that satisfies the manifest without /etc/hwrevision.
    path.write_text(
        "#!/bin/sh\n"
        '[ "$1" = -c ] && [ "$2" = -H ] && [ "$3" = "radio:'
        + swu.HARDWARE_REVISION
        + '" ] && [ "$4" = -i ] && [ "$6" = -e ]\n',
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def _build(tmp_path, checker, rootfs_data=b"root filesystem\n"):
    rootfs = tmp_path / "rootfs.ext4"
    rootfs.write_bytes(rootfs_data)
    output = tmp_path / "images"
    artifact = swu.build_archive(
        rootfs,
        TEMPLATE,
        VERSION_FILE,
        output,
        1024 * 1024,
        checker,
    )
    return rootfs, artifact


def test_project_version_is_strict_and_unambiguous(tmp_path):
    assert swu.project_version(VERSION_FILE) == "0.3.0"
    invalid = tmp_path / "version.py"
    invalid.write_text('__version__ = "1.2"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="exactly one"):
        swu.project_version(invalid)
    invalid.write_text('__version__ = "1.2.3"\n__version__ = "2.0.0"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="exactly one"):
        swu.project_version(invalid)


def test_release_metadata_generator_uses_version_and_hardware_contract(tmp_path):
    output = tmp_path / "radio-release.json"
    subprocess.run(["python3", str(RELEASE_GENERATOR), str(VERSION_FILE), str(output)], check=True)
    assert json.loads(output.read_text(encoding="utf-8")) == {
        "buildroot_commit": "unknown",
        "hardware_revision": swu.HARDWARE_REVISION,
        "minimum_rollback_reader_schema": swu.MINIMUM_ROLLBACK_READER_SCHEMA,
        "persistent_reader_schema": swu.PERSISTENT_READER_SCHEMA,
        "persistent_schema": swu.PERSISTENT_SCHEMA,
        "schema": 1,
        "repository_commit": "unknown",
        "version": "0.3.0",
    }


def test_archive_order_metadata_and_payload_integrity(tmp_path, checker):
    rootfs, artifact = _build(tmp_path, checker)
    assert artifact.name == "kitchen-radio-0.3.0.swu"
    members = swu.read_cpio(artifact)
    assert [name for name, _ in members] == ["sw-description", "rootfs.ext4.gz"]
    manifest = members[0][1].decode()
    payload = members[1][1]
    assert 'version = "0.3.0";' in manifest
    assert swu.HARDWARE_REVISION in manifest
    assert manifest.count('filename = "rootfs.ext4.gz";') == 2
    assert manifest.count(f"size = {len(payload)};") == 2
    assert manifest.count(f'sha256 = "{hashlib.sha256(payload).hexdigest()}";') == 2
    assert gzip.decompress(payload) == rootfs.read_bytes()
    assert "/dev/mmcblk0p2" in manifest and "/dev/mmcblk0p3" in manifest
    for forbidden in ("/dev/mmcblk0p1", "/dev/mmcblk0p4", "radio-config.txt", "data.ext4"):
        assert forbidden not in manifest


def test_build_is_reproducible_and_removes_stale_artifacts(tmp_path, checker):
    rootfs, first = _build(tmp_path, checker)
    first_bytes = first.read_bytes()
    stale = first.parent / "kitchen-radio-9.9.9.swu"
    stale.write_bytes(b"stale")
    second = swu.build_archive(
        rootfs,
        TEMPLATE,
        VERSION_FILE,
        first.parent,
        1024 * 1024,
        checker,
    )
    assert second.read_bytes() == first_bytes
    assert not stale.exists()


def test_build_rejects_rootfs_larger_than_slot(tmp_path, checker):
    rootfs = tmp_path / "rootfs.ext4"
    rootfs.write_bytes(b"too large")
    with pytest.raises(ValueError, match="larger than"):
        swu.build_archive(rootfs, TEMPLATE, VERSION_FILE, tmp_path, 2, checker)


def test_checker_failure_prevents_publication(tmp_path):
    checker = tmp_path / "swupdate"
    checker.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    checker.chmod(0o755)
    rootfs = tmp_path / "rootfs.ext4"
    rootfs.write_bytes(b"rootfs")
    with pytest.raises(Exception):
        swu.build_archive(rootfs, TEMPLATE, VERSION_FILE, tmp_path, 1024, checker)
    assert not (tmp_path / "kitchen-radio-0.3.0.swu").exists()


def test_checker_receives_hardware_revision_override(tmp_path, monkeypatch):
    # The host has no /etc/hwrevision, so the check-mode invocation must pass
    # -H <board>:<revision> matching the manifest for both A/B selections.
    calls = []

    def fake_run(cmd, *args, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(swu.subprocess, "run", fake_run)
    rootfs = tmp_path / "rootfs.ext4"
    rootfs.write_bytes(b"rootfs")
    swu.build_archive(rootfs, TEMPLATE, VERSION_FILE, tmp_path, 1024, tmp_path / "swupdate")
    check_calls = [[str(part) for part in cmd] for cmd in calls if "-c" in cmd]
    assert len(check_calls) == 2
    for selection, cmd in zip(("stable,slot-a", "stable,slot-b"), check_calls):
        assert cmd[1:5] == ["-c", "-H", f"radio:{swu.HARDWARE_REVISION}", "-i"]
        assert cmd[-2:] == ["-e", selection]


def test_manifest_has_no_old_or_unresolved_placeholders():
    text = TEMPLATE.read_text(encoding="utf-8")
    assert "@ROOTFS_ARCHIVE_SIZE@" in text
    assert "@ROOTFS_ARCHIVE_SHA256@" in text
    assert "@ROOTFS_PAYLOAD_SIZE@" not in text
    assert "@ROOTFS_PAYLOAD_SHA256@" not in text


def test_top_level_build_requires_and_reports_both_artifacts():
    text = BUILD_SCRIPT.read_text(encoding="utf-8")
    assert "images_dir=$(CDPATH='' cd -- \"${BUILDROOT_DIR}/output/images\" && pwd)" in text
    assert 'swu="${images_dir}/kitchen-radio-${version}.swu"' in text
    assert 'validate-artifacts.sh" "$images_dir" "$version" "$BUILD_MARKER"' in text
    assert 'report_artifact "Install" "$img"' in text
    assert 'report_artifact "Update " "$swu"' in text
    assert 'BUILD_MARKER="${BUILDROOT_DIR}/output/radio-build.started"' in text
    assert 'touch "$BUILD_MARKER"' in text


def test_build_swu_prefers_native_checker_locations_before_path():
    text = BUILD_SWU_SCRIPT.read_text(encoding="utf-8")
    assert "SWUPDATE_CHECKER:-" in text
    assert '"${buildroot_parent}/swupdate-checker-build/swupdate"' in text
    assert '"${buildroot_parent}/swupdate-native/usr/bin/swupdate"' in text
    assert "path_checker=$(command -v swupdate || true)" in text
    assert 'set -- "$@" --swupdate-checker "${SWUPDATE_CHECKER}"' in text


def test_build_swu_only_accepts_a_checker_that_actually_runs():
    text = BUILD_SWU_SCRIPT.read_text(encoding="utf-8")
    # A candidate must be executable AND run (skip dynamic-loader failures / 127).
    assert "checker_runs()" in text
    assert '[ "$_status" -ne 127 ]' in text
    assert '[ -x "$candidate" ] && checker_runs "$candidate"' in text


def test_checker_runs_probe_rejects_loader_failures(tmp_path):
    """The run-probe must reject a checker that exits 127 (loader failure)."""
    # Fake checkers: one exits 127 (as a binary with unresolved libs would),
    # one exits 1 (ran, rejected the empty probe input). The probe accepts any
    # exit != 127.
    broken = tmp_path / "broken"
    broken.write_text("#!/bin/sh\nexit 127\n", encoding="utf-8")
    broken.chmod(0o755)
    good = tmp_path / "good"
    good.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    good.chmod(0o755)

    probe = (
        "checker_runs() {\n"
        "  p=$(mktemp) || return 1\n"
        '  "$1" -c -i "$p" -e stable,slot-a >/dev/null 2>&1\n'
        "  s=$?\n"
        '  rm -f "$p"\n'
        '  [ "$s" -ne 127 ]\n'
        "}\n"
        'if checker_runs "$1"; then echo RUN; else echo SKIP; fi\n'
    )
    script = tmp_path / "probe.sh"
    script.write_text(probe, encoding="utf-8")

    broken_out = subprocess.run(
        ["sh", str(script), str(broken)],
        capture_output=True,
        text=True,
        check=True,
    )
    good_out = subprocess.run(
        ["sh", str(script), str(good)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert broken_out.stdout.strip() == "SKIP"
    assert good_out.stdout.strip() == "RUN"


def test_build_sh_preflights_a_runnable_swupdate_checker():
    text = BUILD_SCRIPT.read_text(encoding="utf-8")
    assert "preflight_tooling()" in text
    assert '[ "$status" -ne 127 ]' in text
    assert "preflight_tooling\n" in text  # wired into the main sequence
    # The checker package is installed by the apt step.
    assert "swupdate" in text
    assert "libubootenv-tool" in text


def _validate_artifacts(tmp_path, *, image=b"image", update=b"update", stale=False):
    images = tmp_path / "images"
    images.mkdir()
    marker = tmp_path / "build.started"
    marker.touch()
    old = time.time() - 2
    os.utime(marker, (old, old))
    if image is not None:
        (images / "sdcard.img").write_bytes(image)
    if update is not None:
        (images / "kitchen-radio-0.2.0.swu").write_bytes(update)
    if stale:
        (images / "kitchen-radio-9.9.9.swu").write_bytes(b"old")
    return subprocess.run(
        [str(ARTIFACT_VALIDATOR), str(images), "0.2.0", str(marker)],
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize(
    ("image", "update"),
    [(None, b"update"), (b"", b"update"), (b"image", None), (b"image", b"")],
)
def test_artifact_validator_rejects_missing_or_empty_outputs(tmp_path, image, update):
    result = _validate_artifacts(tmp_path, image=image, update=update)
    assert result.returncode == 1
    assert "missing or empty" in result.stderr


def test_artifact_validator_rejects_old_or_ambiguous_outputs(tmp_path):
    result = _validate_artifacts(tmp_path, stale=True)
    assert result.returncode == 1
    assert "stale firmware artifact remains" in result.stderr

    images = tmp_path / "images"
    (images / "kitchen-radio-9.9.9.swu").unlink()
    marker = tmp_path / "build.started"
    future = time.time() + 2
    os.utime(marker, (future, future))
    result = subprocess.run(
        [str(ARTIFACT_VALIDATOR), str(images), "0.2.0", str(marker)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "is stale" in result.stderr


def test_artifact_validator_accepts_fresh_exact_outputs(tmp_path):
    result = _validate_artifacts(tmp_path)
    assert result.returncode == 0
    assert "present, fresh, and unambiguous" in result.stdout
