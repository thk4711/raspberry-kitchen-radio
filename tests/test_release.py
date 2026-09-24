"""Tests for the GitHub release orchestrator."""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "release.py"


def _load_release():
    spec = importlib.util.spec_from_file_location("release", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


release = _load_release()


def test_first_build_dry_run_does_not_require_artifacts(tmp_path, monkeypatch, capsys):
    repository = tmp_path / "repository"
    (repository / "buildroot").mkdir(parents=True)
    (repository / "buildroot" / "build.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    notes = repository / "notes.md"
    notes.write_text("Release notes\n", encoding="utf-8")

    monkeypatch.setattr(release, "require_tools", lambda *tools: None)
    monkeypatch.setattr(release, "assert_versions_match", lambda *args: None)
    monkeypatch.setattr(release, "check_gh_auth", lambda *args: None)
    monkeypatch.setattr(release, "ensure_tag", lambda *args: None)
    monkeypatch.setattr(release, "warn_head_differs_from_tag", lambda *args: None)
    monkeypatch.setattr(release, "run", lambda *args, **kwargs: None)
    monkeypatch.setattr(release, "build_artifacts", lambda *args: None)
    monkeypatch.setattr(release, "publish_release", lambda *args: None)

    assert (
        release.main(
            [
                "0.4.0",
                "--notes",
                str(notes),
                "--repository",
                str(repository),
                "--artifacts-dir",
                str(repository / "artifacts"),
                "--dry-run",
            ]
        )
        == 0
    )

    output = capsys.readouterr().out
    assert "stage newest pisonic-0.4.0-*-sdcard.img.zip" in output
    assert "stage newest pisonic-0.4.0-*.swu" in output
    assert not (repository / "artifacts").exists()
