"""Tests for radio_web.system_status (read-only status collectors).

Point the module-level path constants at ``tmp_path`` fakes and monkeypatch the
command runner so the collectors are exercised without any real /proc, /sys or
network access.
"""
import json

from radio_web import system_status


def _write(path, text):
    path.write_text(text)
    return str(path)


class TestSystemMetrics:
    def test_uptime_parses_first_field(self, tmp_path, monkeypatch):
        f = _write(tmp_path / "uptime", "12345.67 98765.43\n")
        monkeypatch.setattr(system_status, "PROC_UPTIME", f)
        assert system_status.uptime_seconds() == 12345.67

    def test_uptime_missing_file_is_none(self, monkeypatch):
        monkeypatch.setattr(system_status, "PROC_UPTIME", "/no/such/file")
        assert system_status.uptime_seconds() is None

    def test_cpu_temperature_millidegrees(self, tmp_path, monkeypatch):
        f = _write(tmp_path / "temp", "48123\n")
        monkeypatch.setattr(system_status, "CPU_TEMP_FILE", f)
        assert system_status.cpu_temperature_c() == 48.123

    def test_memory_info_parsed(self, tmp_path, monkeypatch):
        f = _write(
            tmp_path / "meminfo",
            "MemTotal:      507860 kB\nMemFree: 1 kB\nMemAvailable:  305012 kB\n",
        )
        monkeypatch.setattr(system_status, "PROC_MEMINFO", f)
        mem = system_status.memory_info()
        assert mem == {"total_kib": 507860, "available_kib": 305012}

    def test_memory_info_missing_fields_none(self, tmp_path, monkeypatch):
        f = _write(tmp_path / "meminfo", "SomethingElse: 5 kB\n")
        monkeypatch.setattr(system_status, "PROC_MEMINFO", f)
        assert system_status.memory_info() == {
            "total_kib": None,
            "available_kib": None,
        }

    def test_hostname_from_file(self, tmp_path, monkeypatch):
        f = _write(tmp_path / "hostname", "kitchen-radio\n")
        monkeypatch.setattr(system_status, "HOSTNAME_FILE", f)
        assert system_status.hostname() == "kitchen-radio"

    def test_app_version_matches_lib_version(self):
        from lib import __version__ as expected

        assert system_status.app_version() == expected


class TestWifiInfo:
    def test_parses_ip_ssid_signal(self, monkeypatch):
        outputs = {
            tuple(system_status._IP_ADDR_CMD): (
                "2: wlan0    inet 192.168.1.42/24 brd 192.168.1.255 scope global wlan0"
            ),
            tuple(system_status._IW_LINK_CMD): (
                "Connected to aa:bb\n\tSSID: MyNet\n\tsignal: -51 dBm\n"
            ),
        }
        monkeypatch.setattr(
            system_status, "_run", lambda cmd: outputs.get(tuple(cmd))
        )
        info = system_status.wifi_info()
        assert info["ip"] == "192.168.1.42"
        assert info["ssid"] == "MyNet"
        assert info["signal_dbm"] == -51

    def test_missing_commands_degrade_to_none(self, monkeypatch):
        monkeypatch.setattr(system_status, "_run", lambda cmd: None)
        monkeypatch.setattr(system_status, "PROC_NET_WIRELESS", "/no/such/file")
        info = system_status.wifi_info()
        assert info["ip"] is None
        assert info["ssid"] is None
        assert info["signal_dbm"] is None


class TestPlayerStatus:
    def test_missing_file_unavailable(self, monkeypatch):
        monkeypatch.setattr(system_status, "STATUS_FILE", "/no/such/file")
        st = system_status.player_status(now=1000.0)
        assert st["available"] is False
        assert st["stale"] is True

    def test_fresh_snapshot(self, tmp_path, monkeypatch):
        payload = {
            "updated_at": 990.0,
            "power": True,
            "active_source": "mpd",
            "now_playing": {"name": "DLF", "title": "News", "state": True},
            "sources": {"mpd": {"playing": True}},
        }
        f = _write(tmp_path / "status.json", json.dumps(payload))
        monkeypatch.setattr(system_status, "STATUS_FILE", f)
        st = system_status.player_status(now=1000.0)
        assert st["available"] is True
        assert st["stale"] is False
        assert st["age_seconds"] == 10.0
        assert st["active_source"] == "mpd"
        assert st["now_playing"]["title"] == "News"

    def test_stale_snapshot(self, tmp_path, monkeypatch):
        f = _write(tmp_path / "status.json", json.dumps({"updated_at": 900.0}))
        monkeypatch.setattr(system_status, "STATUS_FILE", f)
        monkeypatch.setattr(system_status, "STATUS_STALE_SECONDS", 30)
        st = system_status.player_status(now=1000.0)
        assert st["available"] is True
        assert st["stale"] is True

    def test_malformed_json_unavailable(self, tmp_path, monkeypatch):
        f = _write(tmp_path / "status.json", "{not json")
        monkeypatch.setattr(system_status, "STATUS_FILE", f)
        assert system_status.player_status(now=1000.0)["available"] is False


class TestHeartbeat:
    def test_age_from_mtime(self, tmp_path, monkeypatch):
        import os

        f = tmp_path / "radio.alive"
        f.write_text("")
        os.utime(str(f), (500.0, 500.0))
        age = system_status.heartbeat_age_seconds(str(f), now=530.0)
        assert age == 30.0

    def test_missing_heartbeat_none(self):
        assert system_status.heartbeat_age_seconds("/no/such/file") is None


class TestCollect:
    def test_collect_returns_all_keys(self):
        data = system_status.collect(now=1000.0)
        for key in (
            "hostname",
            "version",
            "uptime_seconds",
            "cpu_temperature_c",
            "memory",
            "rootfs",
            "wifi",
            "heartbeat_age_seconds",
            "player",
            "bluetooth",
            "source_labels",
        ):
            assert key in data
