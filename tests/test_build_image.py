"""Tests for the repository-contained local/remote build orchestrator."""

import hashlib
import subprocess
import zipfile
from pathlib import Path

import pytest

from scripts import build_image


@pytest.fixture(autouse=True)
def isolate_default_config(monkeypatch, tmp_path):
    """Ignore any developer-local scripts/build-image.ini during tests.

    parse_args() auto-loads DEFAULT_CONFIG when it exists, so a real local
    build-image.ini would otherwise leak into tests that assert on defaults.
    """
    monkeypatch.setattr(build_image, "DEFAULT_CONFIG", tmp_path / "absent-build-image.ini")


def test_defaults_are_portable_and_local():
    args = build_image.parse_args([])
    assert args.execution == "local"
    assert args.repository == build_image.DEFAULT_REPOSITORY
    assert args.host == ""
    assert args.remote_root == ""
    assert args.jobs is None
    assert args.ssh_port == 22
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


def test_jobs_unset_omits_jobs_flag_so_build_sh_uses_remote_nproc():
    # No --jobs and no INI jobs: build_flags must NOT force a job count, letting
    # build.sh default to nproc (the remote host's core count for remote builds).
    args = build_image.parse_args([])
    assert args.jobs is None
    assert "--jobs" not in build_image.build_flags(args)


def test_jobs_explicit_is_forwarded_to_build_sh():
    args = build_image.parse_args(["--jobs", "5"])
    flags = build_image.build_flags(args)
    assert flags[:2] == ["--jobs", "5"]


def test_allow_unverified_buildroot_defaults_off_and_absent_from_flags():
    args = build_image.parse_args([])
    assert args.allow_unverified_buildroot is False
    assert "--allow-unverified-buildroot" not in build_image.build_flags(args)


def test_allow_unverified_buildroot_cli_adds_build_flag():
    args = build_image.parse_args(["--allow-unverified-buildroot"])
    assert args.allow_unverified_buildroot is True
    assert "--allow-unverified-buildroot" in build_image.build_flags(args)


def test_allow_unverified_buildroot_config_and_cli_override(tmp_path):
    config = tmp_path / "build.ini"
    config.write_text(
        "[build_image]\nallow_unverified_buildroot = true\n",
        encoding="utf-8",
    )
    assert build_image.parse_args(["--config", str(config)]).allow_unverified_buildroot is True
    # Command line can turn the INI value back off.
    overridden = build_image.parse_args(["--config", str(config), "--verified-buildroot"])
    assert overridden.allow_unverified_buildroot is False


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
    expected_commit = build_image.git_commit(build_image.DEFAULT_REPOSITORY)
    assert any(f"RADIO_REPO_COMMIT={expected_commit}" in command for _, command in remote_calls)
    assert image == "/srv/buildroot/output/images/sdcard.img"
    assert swu == "/srv/buildroot/output/images/kitchen-radio-0.2.0.swu"


def test_ssh_port_defaults_and_cli_and_config(tmp_path):
    assert build_image.parse_args([]).ssh_port == 22
    assert build_image.parse_args(["--ssh-port", "2222"]).ssh_port == 2222
    config = tmp_path / "build.ini"
    config.write_text(
        "[build_image]\nexecution = remote\nssh_port = 2022\n",
        encoding="utf-8",
    )
    assert build_image.parse_args(["--config", str(config)]).ssh_port == 2022
    # Command line overrides the configuration file value.
    assert build_image.parse_args(["--config", str(config), "--ssh-port", "40"]).ssh_port == 40


def test_ssh_port_must_be_an_integer(tmp_path):
    config = tmp_path / "build.ini"
    config.write_text("[build_image]\nssh_port = notaport\n", encoding="utf-8")
    with pytest.raises(build_image.BuildError, match="ssh_port must be an integer"):
        build_image.parse_args(["--config", str(config)])


def test_ssh_port_range_is_validated(tmp_path):
    with pytest.raises(build_image.BuildError, match="ssh_port must be between"):
        build_image.main(
            [
                "--execution",
                "remote",
                "--host",
                "builder@example.test",
                "--remote-root",
                "/srv/radio",
                "--artifacts-dir",
                str(tmp_path),
                "--ssh-port",
                "70000",
                "--dry-run",
            ]
        )


def test_default_ssh_port_leaves_remote_commands_unchanged(monkeypatch):
    local_calls = []
    remote_calls = []

    def record_local(argv, **kwargs):
        local_calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0)

    def record_remote(host, command, **kwargs):
        remote_calls.append((host, command, kwargs.get("port")))
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
        ]
    )
    args.version = "0.2.0"
    args.stamp = "20260915-120000"
    build_image.remote_build(args, build_image.DEFAULT_REPOSITORY)
    assert local_calls[0][0:2] == ["rsync", "-az"]
    assert "-e" not in local_calls[0]
    assert build_image.scp_base(args) == ["scp", "-o", "BatchMode=yes"]
    assert all(port == 22 for _, _, port in remote_calls)


def test_non_standard_ssh_port_flows_into_ssh_scp_and_rsync(monkeypatch):
    local_calls = []
    remote_calls = []

    def record_local(argv, **kwargs):
        local_calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0)

    def record_remote(host, command, **kwargs):
        remote_calls.append((host, command, kwargs.get("port")))
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
            "--ssh-port",
            "2222",
        ]
    )
    args.version = "0.2.0"
    args.stamp = "20260915-120000"
    build_image.remote_build(args, build_image.DEFAULT_REPOSITORY)
    assert local_calls[0][0:4] == ["rsync", "-az", "-e", "ssh -p 2222"]
    assert build_image.scp_base(args) == ["scp", "-o", "BatchMode=yes", "-P", "2222"]
    assert all(port == 2222 for _, _, port in remote_calls)


def test_remote_function_adds_port_flag_only_when_non_standard(monkeypatch):
    captured = []

    def record(argv, **kwargs):
        captured.append(list(argv))
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(build_image, "run", record)
    build_image.remote("builder@example.test", "true")
    build_image.remote("builder@example.test", "true", port=2222)
    assert "-p" not in captured[0]
    assert captured[1][captured[1].index("-p") + 1] == "2222"


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
