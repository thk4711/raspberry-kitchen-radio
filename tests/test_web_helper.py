"""Tests for the Phase 7 privileged helper protocol and dispatch."""

import json
import os
import socket
import threading

import pytest

from radio_web import helper, helper_protocol


class TestProtocol:
    def test_request_round_trip(self):
        raw = helper_protocol.encode_request("restart_radio", {"x": 1})
        action, args = helper_protocol.decode_request(raw)
        assert action == "restart_radio"
        assert args == {"x": 1}

    def test_response_round_trip(self):
        raw = helper_protocol.encode_response(True, "done")
        ok, message = helper_protocol.decode_response(raw)
        assert ok is True
        assert message == "done"

    def test_structured_response_round_trip(self):
        raw = helper_protocol.encode_response(True, "done", {"phase": "installing"})
        ok, message, data = helper_protocol.decode_data_response(raw)
        assert (ok, message) == (True, "done")
        assert data == {"phase": "installing"}

    def test_decode_request_rejects_non_object(self):
        with pytest.raises(ValueError):
            helper_protocol.decode_request(b"[1,2,3]\n")

    def test_decode_request_rejects_missing_action(self):
        with pytest.raises(ValueError):
            helper_protocol.decode_request(b'{"args": {}}\n')

    def test_decode_request_rejects_bad_args(self):
        with pytest.raises(ValueError):
            helper_protocol.decode_request(b'{"action": "reboot", "args": 5}\n')


class TestDispatch:
    def test_operations_match_whitelist_exactly(self):
        assert set(helper._OPERATIONS) == set(helper_protocol.ACTION_IDS)

    def test_unknown_action_rejected(self):
        ok, message = helper.dispatch("/bin/sh -c reboot", {})
        assert ok is False
        assert "Unknown" in message

    def test_restart_radio_runs_fixed_argv(self, monkeypatch):
        recorded = {}

        def fake_run(argv, **kwargs):
            recorded["argv"] = argv

            class R:
                returncode = 0
                stderr = ""

            return R()

        monkeypatch.setattr(helper.subprocess, "run", fake_run)
        ok, _msg = helper.dispatch("restart_radio", {})
        assert ok is True
        assert recorded["argv"] == ["/etc/init.d/S90radio", "restart"]

    def test_restart_ssh_runs_fixed_argv(self, monkeypatch):
        recorded = {}

        def fake_run(argv, **kwargs):
            recorded["argv"] = argv

            class R:
                returncode = 0
                stderr = ""

            return R()

        monkeypatch.setattr(helper.subprocess, "run", fake_run)
        ok, _msg = helper.dispatch("restart_ssh", {})
        assert ok is True
        assert recorded["argv"] == ["/etc/init.d/S50dropbear", "restart"]

    def test_fixed_action_does_not_capture_background_service_pipes(self, monkeypatch):
        recorded = {}

        def fake_run(_argv, **kwargs):
            recorded.update(kwargs)

            class R:
                returncode = 0

            return R()

        monkeypatch.setattr(helper.subprocess, "run", fake_run)
        assert helper.dispatch("restart_radio", {})[0] is True
        assert recorded["stdout"] is helper.subprocess.DEVNULL
        assert recorded["stderr"] is helper.subprocess.DEVNULL
        assert "capture_output" not in recorded

    def test_set_hostname_revalidates_argument(self):
        # A bad name is rejected server-side even though the client "sent" it.
        ok, message = helper.dispatch("set_hostname", {"name": "bad name!"})
        assert ok is False
        assert "letters" in message.lower() or "hyphen" in message.lower()

    def test_set_hostname_writes_files(self, monkeypatch, tmp_path):
        hostname_file = tmp_path / "hostname"
        hostname_file.write_text("old\n")
        hosts_file = tmp_path / "hosts"
        hosts_file.write_text("127.0.0.1\tlocalhost old\n")
        monkeypatch.setattr("radio_web.device_store.HOSTNAME_FILE", str(hostname_file))
        monkeypatch.setattr("radio_web.device_store.HOSTS_FILE", str(hosts_file))
        ok, _msg = helper.dispatch("set_hostname", {"name": "new-name"})
        assert ok is True
        assert hostname_file.read_text().strip() == "new-name"
        assert "new-name" in hosts_file.read_text()

    @pytest.mark.parametrize(
        "action",
        [
            "inspect_firmware",
            "install_firmware",
            "cancel_staged_firmware",
            "firmware_status",
            "prepare_rollback",
        ],
    )
    def test_firmware_operations_reject_all_arguments(self, action):
        ok, message = helper.dispatch(action, {"path": "/tmp/attacker.swu"})
        assert ok is False
        assert "do not accept arguments" in message

    def test_firmware_operations_forward_without_arguments(self, monkeypatch):
        monkeypatch.setattr(
            helper.firmware_installer,
            "inspect_firmware",
            lambda: (True, "inspected"),
        )
        assert helper.dispatch("inspect_firmware", {}) == (True, "inspected")

    def test_firmware_status_returns_structured_data(self, monkeypatch):
        monkeypatch.setattr(
            helper.firmware_installer,
            "firmware_status",
            lambda: {"phase": "installing", "percent": 25},
        )
        assert helper.dispatch("firmware_status", {}) == (
            True,
            "Firmware status available.",
            {"phase": "installing", "percent": 25},
        )

    def test_prepare_rollback_forwards_without_arguments(self, monkeypatch):
        monkeypatch.setattr(
            helper.firmware_slots,
            "prepare_rollback",
            lambda: (True, "switching"),
        )
        assert helper.dispatch("prepare_rollback", {}) == (True, "switching")

    @pytest.mark.parametrize(
        "action",
        [
            "create_data_backup",
            "inspect_data_restore",
            "apply_data_restore",
            "cancel_data_restore",
        ],
    )
    def test_backup_operations_reject_all_arguments(self, action):
        ok, message = helper.dispatch(action, {"path": "/tmp/attacker.tar.gz"})
        assert ok is False
        assert "do not accept arguments" in message

    @pytest.mark.parametrize("action", ["reboot", "shutdown", "set_audio_hardware"])
    def test_boot_affecting_actions_blocked_during_install(self, monkeypatch, action):
        monkeypatch.setattr(helper.firmware_installer, "installation_active", lambda: True)
        args = {"profile": "headphones"} if action == "set_audio_hardware" else {}
        ok, message = helper.dispatch(action, args)
        assert ok is False
        assert "firmware installation" in message.lower()

    def test_set_time_saves_validated_values(self, monkeypatch):
        saved = {}
        monkeypatch.setattr(
            helper, "apply_time_files", lambda tz, ntp: saved.update(tz=tz, ntp=ntp)
        )
        monkeypatch.setattr(helper.validators, "validate_timezone", lambda v: "Europe/Berlin")
        monkeypatch.setattr(helper.validators, "validate_ntp_server", lambda v: "pool.ntp.org")
        ok, message = helper.dispatch("set_time", {"timezone": "x", "ntp_server": "y"})
        assert ok
        assert saved == {"tz": "Europe/Berlin", "ntp": "pool.ntp.org"}
        assert "saved" in message.lower()

    def test_set_time_rejects_non_string(self):
        ok, message = helper.dispatch("set_time", {"timezone": 5})
        assert not ok
        assert "Invalid" in message

    def test_set_time_reports_validation_error(self, monkeypatch):
        def bad(_v):
            raise ValueError("bad tz")

        monkeypatch.setattr(helper.validators, "validate_timezone", bad)
        ok, message = helper.dispatch("set_time", {"timezone": "junk"})
        assert not ok
        assert "bad tz" in message

    def test_set_time_reports_write_failure(self, monkeypatch):
        monkeypatch.setattr(helper.validators, "validate_timezone", lambda v: "Europe/Berlin")

        def boom(_tz, _ntp):
            raise OSError("read-only fs")

        monkeypatch.setattr(helper, "apply_time_files", boom)
        ok, message = helper.dispatch("set_time", {"timezone": "x"})
        assert not ok
        assert "Could not save" in message

    def test_apply_equalizer_rejects_arguments(self):
        ok, message = helper.dispatch("apply_equalizer", {"preamp": 1})
        assert not ok
        assert "does not accept arguments" in message

    def test_apply_equalizer_live_change_needs_no_restart(self, monkeypatch):
        monkeypatch.setattr(
            helper.audio_hardware_apply, "apply_equalizer", lambda: (True, "live: tweaked")
        )
        ok, message = helper.dispatch("apply_equalizer", {})
        assert ok
        assert "live" in message.lower()

    def test_apply_equalizer_structural_change_restarts_services(self, monkeypatch):
        monkeypatch.setattr(
            helper.audio_hardware_apply, "apply_equalizer", lambda: (True, "restart: inserted")
        )
        restarts = []
        monkeypatch.setattr(
            helper, "_run_argv", lambda argv, ok, fail: (restarts.append(argv) or (True, ok))
        )
        ok, message = helper.dispatch("apply_equalizer", {})
        assert ok
        assert len(restarts) == 4  # mpd, radio, bluetooth, usb-audio
        assert "all audio sources" in message

    def test_apply_equalizer_reports_underlying_failure(self, monkeypatch):
        monkeypatch.setattr(
            helper.audio_hardware_apply, "apply_equalizer", lambda: (False, "eq broken")
        )
        ok, message = helper.dispatch("apply_equalizer", {})
        assert not ok
        assert message == "eq broken"

    def test_run_argv_reports_not_found(self, monkeypatch):
        def boom(*_a, **_k):
            raise FileNotFoundError("no script")

        monkeypatch.setattr(helper.subprocess, "run", boom)
        ok, message = helper._run_argv(["/x", "restart"], "ok", "failed")
        assert not ok
        assert message == "failed"

    def test_run_argv_reports_timeout(self, monkeypatch):
        def boom(*_a, **_k):
            raise helper.subprocess.TimeoutExpired("x", 30)

        monkeypatch.setattr(helper.subprocess, "run", boom)
        ok, message = helper._run_argv(["/x", "restart"], "ok", "failed")
        assert not ok

    def test_run_argv_reports_os_error(self, monkeypatch):
        def boom(*_a, **_k):
            raise OSError("permission denied")

        monkeypatch.setattr(helper.subprocess, "run", boom)
        assert helper._run_argv(["/x"], "ok", "failed") == (False, "failed")

    def test_run_argv_reports_nonzero_exit(self, monkeypatch):
        class R:
            returncode = 3

        monkeypatch.setattr(helper.subprocess, "run", lambda *a, **k: R())
        assert helper._run_argv(["/x"], "ok", "failed") == (False, "failed")

    def test_set_wifi_forwards_normalised_config(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(
            helper.network_apply, "apply_wifi", lambda config: (seen.update(config) or (True, "ok"))
        )
        ok, _msg = helper.dispatch("set_wifi", {"ssid": "Net", "psk": "12345678", "country": "DE"})
        assert ok
        assert seen["ssid"] == "Net"
        assert set(seen) == {
            "ssid",
            "psk",
            "country",
            "ip_address",
            "ip_prefix",
            "ip_gateway",
            "ip_dns",
        }

    def test_wifi_confirm_and_rollback_forward(self, monkeypatch):
        monkeypatch.setattr(helper.network_apply, "confirm", lambda: (True, "confirmed"))
        monkeypatch.setattr(helper.network_apply, "rollback", lambda: (True, "reverted"))
        assert helper.dispatch("wifi_confirm", {}) == (True, "confirmed")
        assert helper.dispatch("wifi_rollback", {}) == (True, "reverted")


class TestSocketServer:
    def test_end_to_end_over_socket(self, monkeypatch):
        import tempfile

        # Point restart_radio at a no-op so the round trip does not touch init.
        monkeypatch.setattr(
            helper,
            "_OPERATIONS",
            {**helper._OPERATIONS, "restart_radio": lambda _a: (True, "ok")},
        )
        # Short socket path: macOS caps AF_UNIX sun_path at ~104 chars, and
        # pytest's tmp_path is longer than that.
        with tempfile.TemporaryDirectory() as short_dir:
            sock_path = os.path.join(short_dir, "h.sock")
            server = helper._Server(sock_path, helper._Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                    client.settimeout(5)
                    client.connect(sock_path)
                    client.sendall(helper_protocol.encode_request("restart_radio", {}))
                    reply = client.recv(4096)
                ok, message = helper_protocol.decode_response(reply)
                assert ok is True
                assert message == "ok"
            finally:
                server.shutdown()
                server.server_close()

    def test_malformed_request_rejected_over_socket(self):
        import tempfile

        with tempfile.TemporaryDirectory() as short_dir:
            sock_path = os.path.join(short_dir, "h.sock")
            server = helper._Server(sock_path, helper._Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                    client.settimeout(5)
                    client.connect(sock_path)
                    client.sendall(b"not json at all\n")
                    reply = client.recv(4096)
                data = json.loads(reply)
                assert data["ok"] is False
            finally:
                server.shutdown()
                server.server_close()

    def test_oversized_request_rejected_over_socket(self):
        import tempfile

        with tempfile.TemporaryDirectory() as short_dir:
            sock_path = os.path.join(short_dir, "h.sock")
            server = helper._Server(sock_path, helper._Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                    client.settimeout(5)
                    client.connect(sock_path)
                    oversized = b"x" * (helper_protocol.MAX_MESSAGE_BYTES + 8) + b"\n"
                    client.sendall(oversized)
                    reply = client.recv(4096)
                ok, message = helper_protocol.decode_response(reply)
                assert ok is False
                assert "too large" in message.lower()
            finally:
                server.shutdown()
                server.server_close()


class TestLifecycle:
    def test_seed_equalizer_runtime_swallows_oserror(self, monkeypatch):
        monkeypatch.setattr(helper.equalizer_store, "load_equalizer", lambda: {"enabled": True})

        def boom(_settings):
            raise OSError("tmpfs missing")

        monkeypatch.setattr(helper.equalizer_store, "write_runtime", boom)
        # Best-effort: must never raise even when the runtime write fails.
        helper._seed_equalizer_runtime()

    def test_seed_equalizer_runtime_writes_settings(self, monkeypatch):
        written = []
        monkeypatch.setattr(helper.equalizer_store, "load_equalizer", lambda: {"preamp_db": -3})
        monkeypatch.setattr(helper.equalizer_store, "write_runtime", written.append)
        helper._seed_equalizer_runtime()
        assert written == [{"preamp_db": -3}]

    def test_main_configures_logging_and_serves(self, monkeypatch):
        served = []
        monkeypatch.setattr(helper, "serve", lambda: served.append(True))
        monkeypatch.setenv("RADIO_LOG_LEVEL", "DEBUG")
        helper.main()
        assert served == [True]

    def test_main_accepts_numeric_log_level(self, monkeypatch):
        served = []
        monkeypatch.setattr(helper, "serve", lambda: served.append(True))
        monkeypatch.setenv("RADIO_LOG_LEVEL", "10")
        helper.main()
        assert served == [True]
