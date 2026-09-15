"""Tests for the repository-contained local/remote build orchestrator."""

import hashlib
import subprocess
import zipfile
from pathlib import Path

import pytest

from scripts import build_image


def test_defaults_are_portable_and_local():
    args = build_image.parse_args([])
    assert args.execution == "local"
    assert args.repository == build_image.DEFAULT_REPOSITORY
    assert args.host == ""
    assert args.remote_root == ""
    assert args.jobs >= 1
    text = Path(build_image.__file__).read_text(encoding="utf-8")
    assert "/Users/" not in text
    assert "/home/papa" not in text
    assert "192.168." not in text


def test_config_is_optional_and_cli_overrides_it(tmp_path):
    config = tmp_path / "build.ini"
    config.write_text(
        "[build_image]\n"
        "execution = remote\n"
        "host = builder@example.test\n"
        "remote_root = /srv/radio\n"
        "jobs = 3\n"
        "zip_image = false\n",
        encoding="utf-8",
    )
    args = build_image.parse_args(
        ["--config", str(config), "--execution", "local", "--jobs", "7", "--zip"]
    )
    assert args.execution == "local"
    assert args.host == "builder@example.test"
    assert args.jobs == 7
    assert args.zip_image is True


def test_bad_config_is_rejected(tmp_path):
    config = tmp_path / "build.ini"
    config.write_text("[build_image]\npersonal_default = value\n", encoding="utf-8")
    with pytest.raises(build_image.BuildError, match="unknown configuration"):
        build_image.parse_args(["--config", str(config)])


def test_remote_mode_requires_explicit_host_and_root():
    with pytest.raises(build_image.BuildError, match="requires both"):
        build_image.main(["--execution", "remote", "--dry-run"])


def test_local_dry_run_does_not_execute_commands(monkeypatch, tmp_path, capsys):
    def unexpected(*args, **kwargs):
        raise AssertionError("dry run executed a command")

    monkeypatch.setattr(build_image, "run", unexpected)
    assert build_image.main(["--artifacts-dir", str(tmp_path), "--dry-run"]) == 0
    output = capsys.readouterr().out
    assert "Execution: local" in output
    assert "Dry run" in output


def test_remote_dry_run_does_not_require_ssh(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(
        build_image,
        "remote",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("SSH used")),
    )
    result = build_image.main(
        [
            "--execution",
            "remote",
            "--host",
            "builder@example.test",
            "--remote-root",
            "/srv/radio",
            "--artifacts-dir",
            str(tmp_path),
            "--dry-run",
        ]
    )
    assert result == 0
    assert "SSH host: builder@example.test" in capsys.readouterr().out


def test_local_build_invokes_repository_script_without_ssh(monkeypatch, tmp_path):
    calls = []

    def record(argv, **kwargs):
        calls.append((list(argv), kwargs))
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(build_image, "run", record)
    args = build_image.parse_args(
        [
            "--repository",
            str(build_image.DEFAULT_REPOSITORY),
            "--buildroot-dir",
            str(tmp_path / "buildroot"),
            "--download-cache",
            str(tmp_path / "dl"),
            "--jobs",
            "2",
        ]
    )
    args.version = "0.2.0"
    build_image.local_build(args, build_image.DEFAULT_REPOSITORY)
    assert calls[0][0][0].endswith("/buildroot/build.sh")
    assert calls[0][0][1:] == ["--jobs", "2", "--no-apt"]
    assert calls[0][1]["env"]["BUILDROOT_DIR"] == str(tmp_path / "buildroot")
    assert all("ssh" not in call[0] for call in calls)


def test_remote_build_stages_and_invokes_supported_build_script(monkeypatch):
    local_calls = []
    remote_calls = []

    def record_local(argv, **kwargs):
        local_calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0)

    def record_remote(host, command, **kwargs):
        remote_calls.append((host, command))
        return subprocess.CompletedProcess(["ssh"], 0)

    monkeypatch.setattr(build_image, "run", record_local)
    monkeypatch.setattr(build_image, "remote", record_remote)
    args = build_image.parse_args(
        [
            "--execution",
            "remote",
            "--host",
            "builder@example.test",
            "--remote-root",
            "/srv/radio",
            "--buildroot-dir",
            "/srv/buildroot",
            "--download-cache",
            "/srv/dl",
            "--jobs",
            "3",
        ]
    )
    args.version = "0.2.0"
    args.stamp = "20260915-120000"
    image, swu = build_image.remote_build(args, build_image.DEFAULT_REPOSITORY)

    assert local_calls[0][0:2] == ["rsync", "-az"]
    assert "--delete" in local_calls[0]
    assert local_calls[0][-1] == (
        "builder@example.test:/srv/radio/radio-repo-0.2.0-20260915-120000/"
    )
    assert all(host == "builder@example.test" for host, _ in remote_calls)
    assert any("./buildroot/build.sh --jobs 3 --no-apt" in command for _, command in remote_calls)
    assert image == "/srv/buildroot/output/images/sdcard.img"
    assert swu == "/srv/buildroot/output/images/kitchen-radio-0.2.0.swu"


def test_zip_verification_accepts_exact_member_and_rejects_wrong_hash(tmp_path):
    archive = tmp_path / "image.zip"
    member = "radio.img"
    payload = b"firmware image"
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr(member, payload)
    digest = hashlib.sha256(payload).hexdigest()
    build_image.verify_zip_member_sha256(archive, member, digest)
    with pytest.raises(build_image.BuildError, match="checksum"):
        build_image.verify_zip_member_sha256(archive, member, "0" * 64)


def test_example_configuration_contains_only_dummy_remote_identity():
    example = (build_image.DEFAULT_REPOSITORY / "scripts" / "build-image.example.ini").read_text(
        encoding="utf-8"
    )
    assert "build-user@build-host.example" in example
    assert "papa@" not in example
    assert "192.168." not in example
