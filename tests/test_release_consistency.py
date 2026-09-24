"""Tests for repository release-identity consistency validation."""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check-release-consistency.py"


def _load_checker():
    scripts = str(ROOT / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    spec = importlib.util.spec_from_file_location("check_release_consistency", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


checker = _load_checker()


def _git(path, *args, env=None):
    subprocess.run(
        ["git", "-C", str(path), *args],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )


def _repository(tmp_path, version="1.2.3", release_date="2026-09-15"):
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "Release Test")
    _git(root, "config", "user.email", "release@example.invalid")
    (root / "tracked.txt").write_text("release\n", encoding="utf-8")
    _git(root, "add", "tracked.txt")
    env = os.environ.copy()
    env["GIT_AUTHOR_DATE"] = f"{release_date}T12:00:00+0000"
    env["GIT_COMMITTER_DATE"] = f"{release_date}T12:00:00+0000"
    _git(root, "commit", "-q", "-m", "release", env=env)
    return root, version, release_date, env


def test_repository_release_is_consistent():
    assert checker.check_release_consistency(ROOT) == "0.4.0"


def test_version_and_changelog_parsers_reject_ambiguity(tmp_path):
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('version = "1.2.3"\n', encoding="utf-8")
    assert checker.pyproject_version(pyproject) == "1.2.3"
    pyproject.write_text('version = "1.2.3"\nversion = "2.0.0"\n', encoding="utf-8")
    with pytest.raises(checker.ConsistencyError, match="exactly one"):
        checker.pyproject_version(pyproject)

    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text("## [1.2.3] - 2026-09-15\n", encoding="utf-8")
    assert checker.latest_changelog_release(changelog) == ("1.2.3", "2026-09-15")
    changelog.write_text("## [1.2.3] - 2026-02-30\n", encoding="utf-8")
    with pytest.raises(checker.ConsistencyError, match="invalid changelog release date"):
        checker.latest_changelog_release(changelog)


def test_documentation_rejects_fixed_firmware_versions(tmp_path):
    (tmp_path / "doc").mkdir()
    (tmp_path / "README.md").write_text("Use pisonic-1.2.3.swu\n", encoding="utf-8")
    with pytest.raises(checker.ConsistencyError, match="fixed-version firmware examples"):
        checker.check_documentation_examples(tmp_path)
    (tmp_path / "README.md").write_text("Use pisonic-<version>.swu\n", encoding="utf-8")
    checker.check_documentation_examples(tmp_path)


def test_annotated_tag_and_date_are_required(tmp_path):
    root, version, release_date, env = _repository(tmp_path)
    _git(root, "tag", "-a", f"v{version}", "-m", f"v{version}", env=env)
    checker.check_tag(root, version, release_date)
    with pytest.raises(checker.ConsistencyError, match="does not match changelog date"):
        checker.check_tag(root, version, "2026-09-16")


def test_unapproved_lightweight_tag_is_rejected(tmp_path):
    root, version, release_date, _env = _repository(tmp_path)
    _git(root, "tag", f"v{version}")
    with pytest.raises(checker.ConsistencyError, match="must be annotated"):
        checker.check_tag(root, version, release_date)


def test_historical_v020_lightweight_tag_is_accepted(tmp_path):
    root, _version, release_date, _env = _repository(
        tmp_path, version="0.2.0", release_date="2026-09-10"
    )
    _git(root, "tag", "v0.2.0")
    checker.check_tag(root, "0.2.0", release_date)
