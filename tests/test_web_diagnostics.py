"""Tests for the Phase 7 diagnostics bundle."""

import io
import tarfile

from radio_web import diagnostics


def _members(data):
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        return {m.name: tar.extractfile(m).read() for m in tar.getmembers()}


class TestDiagnostics:
    def test_builds_gzip_with_snapshots(self, monkeypatch, tmp_path):
        monkeypatch.setattr(diagnostics, "LOG_GLOBS", ())
        monkeypatch.setattr(diagnostics, "_run", lambda cmd: "output\n")
        monkeypatch.setattr(diagnostics, "_dmesg_tail", lambda lines=200: "dmesg\n")
        filename, data = diagnostics.build_bundle(now=1_700_000_000)
        assert filename.startswith("radio-diagnostics-")
        assert filename.endswith(".tar.gz")
        members = _members(data)
        assert "snapshots/status.txt" in members
        assert "snapshots/uname.txt" in members

    def test_includes_logs(self, monkeypatch, tmp_path):
        log = tmp_path / "radio.log"
        log.write_text("hello log\n")
        monkeypatch.setattr(diagnostics, "LOG_GLOBS", (str(tmp_path / "*.log"),))
        monkeypatch.setattr(diagnostics, "_run", lambda cmd: "")
        monkeypatch.setattr(diagnostics, "_dmesg_tail", lambda lines=200: "")
        _filename, data = diagnostics.build_bundle()
        members = _members(data)
        assert "logs/radio.log" in members
        assert b"hello log" in members["logs/radio.log"]

    def test_includes_persistent_operational_summary(self, monkeypatch, tmp_path):
        operational = tmp_path / "summary.json"
        operational.write_text('{"schema":1,"events":[]}\n')
        monkeypatch.setattr(diagnostics, "OPERATIONAL_SUMMARY_PATH", str(operational))
        monkeypatch.setattr(diagnostics, "LOG_GLOBS", ())
        monkeypatch.setattr(diagnostics, "_run", lambda cmd: "")
        monkeypatch.setattr(diagnostics, "_dmesg_tail", lambda lines=200: "")
        _filename, data = diagnostics.build_bundle()
        members = _members(data)
        assert members["snapshots/operational-summary.json"] == operational.read_bytes()

    def test_excludes_secrets(self, monkeypatch, tmp_path):
        # Even if a secret file somehow matched a glob, it must never be added.
        (tmp_path / "admin.secret").write_text("SECRET")
        (tmp_path / "wpa_supplicant.conf").write_text("psk=SECRET")
        monkeypatch.setattr(diagnostics, "LOG_GLOBS", (str(tmp_path / "*"),))
        monkeypatch.setattr(diagnostics, "_run", lambda cmd: "")
        monkeypatch.setattr(diagnostics, "_dmesg_tail", lambda lines=200: "")
        _filename, data = diagnostics.build_bundle()
        members = _members(data)
        assert not any("admin.secret" in name for name in members)
        assert not any("wpa_supplicant.conf" in name for name in members)
        assert b"SECRET" not in data

    def test_capped_at_1_mib(self, monkeypatch, tmp_path):
        big = tmp_path / "radio.log"
        big.write_text("x" * (3 * 1024 * 1024))  # 3 MiB, over the cap
        monkeypatch.setattr(diagnostics, "LOG_GLOBS", (str(tmp_path / "*.log"),))
        monkeypatch.setattr(diagnostics, "_run", lambda cmd: "")
        monkeypatch.setattr(diagnostics, "_dmesg_tail", lambda lines=200: "")
        _filename, data = diagnostics.build_bundle()
        members = _members(data)
        total = sum(len(payload) for payload in members.values())
        assert total <= diagnostics.MAX_BUNDLE_BYTES
        assert b"truncated" in members["logs/radio.log"]
