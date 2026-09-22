"""Tests for strict validation of the stock Buildroot checkout."""

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "buildroot" / "validate-checkout.sh"


def _git(path: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(path), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _checkout(tmp_path: Path) -> tuple[Path, str]:
    checkout = tmp_path / "buildroot"
    checkout.mkdir()
    _git(checkout, "init", "-q")
    _git(checkout, "config", "user.name", "Buildroot Test")
    _git(checkout, "config", "user.email", "buildroot@example.invalid")
    (checkout / "Makefile").write_text("all:\n\t@true\n", encoding="utf-8")
    _git(checkout, "add", "Makefile")
    _git(checkout, "commit", "-q", "-m", "pinned Buildroot")
    return checkout, _git(checkout, "rev-parse", "HEAD").stdout.strip()


def _validate(checkout: Path, commit: str, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(VALIDATOR), str(checkout), "test-version", commit, *extra],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "LC_ALL": "C"},
    )


def test_clean_checkout_at_pinned_commit_is_accepted(tmp_path):
    checkout, commit = _checkout(tmp_path)
    result = _validate(checkout, commit)
    assert result.returncode == 0
    assert result.stdout.strip() == commit
    assert result.stderr == ""


def test_mismatched_revision_is_rejected_by_default(tmp_path):
    checkout, _commit = _checkout(tmp_path)
    result = _validate(checkout, "0" * 40)
    assert result.returncode == 1
    assert "must be" in result.stderr


def test_dirty_checkout_is_rejected_by_default(tmp_path):
    checkout, commit = _checkout(tmp_path)
    (checkout / "local-change").write_text("dirty\n", encoding="utf-8")
    result = _validate(checkout, commit)
    assert result.returncode == 1
    assert "checkout is dirty" in result.stderr


def test_non_git_directory_is_rejected_by_default(tmp_path):
    checkout = tmp_path / "buildroot"
    checkout.mkdir()
    result = _validate(checkout, "0" * 40)
    assert result.returncode == 1
    assert "not a Git checkout" in result.stderr


def test_explicit_override_allows_unverified_development_checkout(tmp_path):
    checkout, commit = _checkout(tmp_path)
    (checkout / "local-change").write_text("dirty\n", encoding="utf-8")
    result = _validate(checkout, "0" * 40, "--allow-unverified")
    assert result.returncode == 0
    assert result.stdout.strip() == commit
    assert result.stderr.count("--allow-unverified-buildroot") == 2


def test_build_script_pins_and_records_source_revisions():
    text = (ROOT / "buildroot" / "build.sh").read_text(encoding="utf-8")
    assert "72d9d4fa636a371ef9eb99c92a735ce9f6d829d5" in text
    assert '"${SCRIPT_DIR}/validate-checkout.sh"' in text
    assert "--allow-unverified-buildroot" in text
    assert "RADIO_REPO_COMMIT" in text
    assert "RADIO_BUILDROOT_COMMIT" in text
    assert "RADIO_REPO_COMMIT is not a full lowercase Git object ID" in text
    # shairport-sync 5.6-dev runs plistutil while configuring AirPlay 2.
    assert "libplist-utils" in text


def test_release_metadata_records_source_revisions(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    from scripts.generate_release_metadata import release_metadata

    version_file = tmp_path / "version.py"
    version_file.write_text('__version__ = "1.2.3"\n', encoding="utf-8")
    monkeypatch.setenv("RADIO_REPO_COMMIT", "a" * 40)
    monkeypatch.setenv("RADIO_BUILDROOT_COMMIT", "b" * 40)
    metadata = release_metadata(version_file)
    assert metadata["repository_commit"] == "a" * 40
    assert metadata["buildroot_commit"] == "b" * 40
