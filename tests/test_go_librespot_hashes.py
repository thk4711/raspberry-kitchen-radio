"""Repository contract checks for go-librespot download verification."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "buildroot" / "external" / "package" / "go-librespot"
MAKEFILE = PACKAGE / "go-librespot.mk"
HASH_FILE = PACKAGE / "go-librespot.hash"
DEFCONFIG = ROOT / "buildroot" / "external" / "configs" / "radio_rpi3_defconfig"
BUILDROOT_GUIDE = ROOT / "doc" / "buildroot.md"


def _package_version() -> str:
    match = re.search(
        r"^GO_LIBRESPOT_VERSION\s*=\s*(\S+)\s*$",
        MAKEFILE.read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    assert match is not None
    return match.group(1)


def _hashed_files() -> list[str]:
    entries = []
    for line in HASH_FILE.read_text(encoding="utf-8").splitlines():
        fields = line.split()
        if fields and fields[0] == "sha256":
            assert len(fields) == 3
            assert re.fullmatch(r"[0-9a-f]{64}", fields[1])
            entries.append(fields[2])
    return entries


def test_go_librespot_hash_matches_package_and_buildroot_archive_format():
    version = _package_version()
    assert _hashed_files() == [f"go-librespot-{version}-go2.tar.gz", "LICENSE"]


def test_go_librespot_hash_is_enforced_without_global_forced_checking():
    defconfig = DEFCONFIG.read_text(encoding="utf-8")
    assert "# BR2_DOWNLOAD_FORCE_CHECK_HASHES is not set" in defconfig
    assert "BR2_DOWNLOAD_FORCE_CHECK_HASHES=y" not in defconfig
    assert HASH_FILE.is_file()


def test_go_librespot_hash_documentation_describes_actual_buildroot_flow():
    text = "\n".join(
        (
            MAKEFILE.read_text(encoding="utf-8"),
            HASH_FILE.read_text(encoding="utf-8"),
            DEFCONFIG.read_text(encoding="utf-8"),
            BUILDROOT_GUIDE.read_text(encoding="utf-8"),
        )
    )
    for required in ("go mod vendor", "post-processed", "archive-format version"):
        assert required in text
    assert "ships no `.hash` file" not in text
    assert "We ship no .hash" not in text
