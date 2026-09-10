"""Tests for the Phase 7 device-settings store and validators."""

import os
import stat

import pytest

from radio_web import config_store, device_store, validators


@pytest.fixture()
def managed(monkeypatch, tmp_path):
    monkeypatch.setattr(config_store, "MANAGED_CONFIG_DIR", str(tmp_path))
    return tmp_path


class TestDeviceValidators:
    @pytest.mark.parametrize("name", ["kitchen-radio", "radio1", "a", "A-B-9"])
    def test_valid_names(self, name):
        assert validators.validate_device_name(name) == name

    @pytest.mark.parametrize(
        "name",
        ["", "-lead", "trail-", "has space", "under_score", "dot.name", "a" * 64],
    )
    def test_invalid_names(self, name):
        with pytest.raises(ValueError):
            validators.validate_device_name(name)

    def test_valid_timezone(self):
        assert validators.validate_timezone("Europe/Berlin") == "Europe/Berlin"

    @pytest.mark.parametrize("tz", ["", "../etc/passwd", "/abs", "has space", "a" * 65])
    def test_invalid_timezone(self, tz):
        with pytest.raises(ValueError):
            validators.validate_timezone(tz)

    @pytest.mark.parametrize("server", ["pool.ntp.org", "192.168.1.1", "ntp1"])
    def test_valid_ntp(self, server):
        assert validators.validate_ntp_server(server) == server

    @pytest.mark.parametrize("server", ["", "bad;server", "has space", "a" * 254])
    def test_invalid_ntp(self, server):
        with pytest.raises(ValueError):
            validators.validate_ntp_server(server)


class TestDeviceStore:
    def test_load_defaults_from_system(self, managed, monkeypatch, tmp_path):
        hostname = tmp_path / "hostname"
        timezone = tmp_path / "timezone"
        chrony = tmp_path / "chrony.conf"
        hostname.write_text("kitchen-radio\n")
        timezone.write_text("Europe/Berlin\n")
        chrony.write_text("pool pool.ntp.org iburst\n")
        monkeypatch.setattr(device_store, "HOSTNAME_FILE", str(hostname))
        monkeypatch.setattr(device_store, "TIMEZONE_FILE", str(timezone))
        monkeypatch.setattr(device_store, "CHRONY_CONF", str(chrony))
        assert device_store.load_device() == {
            "name": "kitchen-radio",
            "timezone": "Europe/Berlin",
            "ntp_server": "pool.ntp.org",
            "ssh_enabled": "false",
        }

    def test_managed_values_override_system(self, managed, monkeypatch, tmp_path):
        hostname = tmp_path / "hostname"
        hostname.write_text("system-name\n")
        monkeypatch.setattr(device_store, "HOSTNAME_FILE", str(hostname))
        device_store.save_device({"name": "saved-name"})
        assert device_store.load_device()["name"] == "saved-name"

    def test_ssh_setting_round_trips(self, managed):
        device_store.save_device({"ssh_enabled": "false"})
        assert device_store.load_device()["ssh_enabled"] == "false"
        assert "[remote_access]" in open(device_store.managed_device_path()).read()

    def test_timezone_falls_back_to_localtime_symlink(self, managed, monkeypatch, tmp_path):
        zoneinfo = tmp_path / "zoneinfo"
        (zoneinfo / "Europe").mkdir(parents=True)
        (zoneinfo / "Europe" / "Berlin").write_text("TZif")
        localtime = tmp_path / "localtime"
        localtime.symlink_to(zoneinfo / "Europe" / "Berlin")
        monkeypatch.setattr(device_store, "TIMEZONE_FILE", str(tmp_path / "missing"))
        monkeypatch.setattr(device_store, "LOCALTIME_LINK", str(localtime))
        monkeypatch.setattr(device_store, "ZONEINFO_DIR", str(zoneinfo))
        assert device_store.load_device()["timezone"] == "Europe/Berlin"

    def test_available_timezones_uses_zone_table(self, monkeypatch, tmp_path):
        (tmp_path / "zone1970.tab").write_text(
            "DE\t+5230+01322\tEurope/Berlin\nUS\t+404251-0740023\tAmerica/New_York\n"
        )
        (tmp_path / "UTC").write_text("TZif")
        monkeypatch.setattr(device_store, "ZONEINFO_DIR", str(tmp_path))
        assert device_store.available_timezones() == ["America/New_York", "Europe/Berlin", "UTC"]

    def test_save_and_load_round_trip(self, managed):
        device_store.save_device(
            {"name": "radio1", "timezone": "Europe/Berlin", "ntp_server": "pool.ntp.org"}
        )
        loaded = device_store.load_device()
        assert loaded["name"] == "radio1"
        assert loaded["timezone"] == "Europe/Berlin"
        assert loaded["ntp_server"] == "pool.ntp.org"

    def test_save_creates_file_0644_and_backup(self, managed):
        device_store.save_device({"name": "radio1"})
        path = device_store.managed_device_path()
        assert os.path.isfile(path)
        mode = stat.S_IMODE(os.stat(path).st_mode)
        assert mode == config_store.CONFIG_MODE
        assert not os.path.isfile(device_store.managed_backup_path())
        device_store.save_device({"name": "radio2"})
        assert os.path.isfile(device_store.managed_backup_path())

    def test_serialize_omits_empty_fields(self):
        text = device_store.serialize_device({"name": "radio1"})
        assert "[device]" in text
        assert "name = radio1" in text
        assert "timezone" not in text
        assert "ntp_server" not in text

    def test_serialize_round_trips_through_player_parser(self, managed):
        import sys

        sys.path.insert(0, os.path.join(os.getcwd(), "lib"))
        from utilities import UtilityLibrary  # noqa: E402

        text = device_store.serialize_device({"name": "radio1", "timezone": "Europe/Berlin"})
        tmp = os.path.join(os.getcwd(), "tests", "_tmp_device.ini")
        with open(tmp, "w", encoding="utf-8") as handle:
            handle.write(text)
        try:
            parsed = UtilityLibrary._parse_config(tmp)
        finally:
            os.unlink(tmp)
        assert parsed["device"]["name"] == "radio1"
        assert parsed["time"]["timezone"] == "Europe/Berlin"

    def test_restore_removes_files(self, managed):
        device_store.save_device({"name": "radio1"})
        device_store.save_device({"name": "radio2"})  # create a .bak too
        device_store.restore_builtin()
        assert not os.path.isfile(device_store.managed_device_path())
        assert not os.path.isfile(device_store.managed_backup_path())

    def test_restore_idempotent(self, managed):
        device_store.restore_builtin()  # no files present, must not raise


class TestSdCardLock:
    def test_locked_when_marker_present(self, monkeypatch, tmp_path):
        marker = tmp_path / "locked"
        marker.write_text("")
        monkeypatch.setattr(device_store, "HOSTNAME_LOCK_MARKER", str(marker))
        assert device_store.sd_card_hostname_locked() is True

    def test_unlocked_when_marker_absent(self, monkeypatch, tmp_path):
        monkeypatch.setattr(device_store, "HOSTNAME_LOCK_MARKER", str(tmp_path / "absent"))
        assert device_store.sd_card_hostname_locked() is False


class TestPrivilegedFileWrites:
    def test_apply_hostname_files(self, monkeypatch, tmp_path):
        hostname_file = tmp_path / "hostname"
        hostname_file.write_text("old\n")
        hosts_file = tmp_path / "hosts"
        hosts_file.write_text("127.0.0.1\told localhost\n")
        monkeypatch.setattr(device_store, "HOSTNAME_FILE", str(hostname_file))
        monkeypatch.setattr(device_store, "HOSTS_FILE", str(hosts_file))
        device_store.apply_hostname_files("new-name")
        assert hostname_file.read_text().strip() == "new-name"
        assert "new-name" in hosts_file.read_text()

    def test_apply_time_files(self, monkeypatch, tmp_path):
        zoneinfo = tmp_path / "zoneinfo"
        (zoneinfo / "Europe").mkdir(parents=True)
        (zoneinfo / "Europe" / "Berlin").write_text("TZif")
        localtime = tmp_path / "localtime"
        timezone_file = tmp_path / "timezone"
        chrony = tmp_path / "chrony.conf"
        chrony.write_text("pool old.pool iburst\ndriftfile /x\n")
        monkeypatch.setattr(device_store, "ZONEINFO_DIR", str(zoneinfo))
        monkeypatch.setattr(device_store, "LOCALTIME_LINK", str(localtime))
        monkeypatch.setattr(device_store, "TIMEZONE_FILE", str(timezone_file))
        monkeypatch.setattr(device_store, "CHRONY_CONF", str(chrony))
        device_store.apply_time_files("Europe/Berlin", "pool.ntp.org")
        assert os.path.islink(localtime)
        assert timezone_file.read_text().strip() == "Europe/Berlin"
        text = chrony.read_text()
        assert "server pool.ntp.org iburst" in text
        assert "old.pool" not in text
        assert "driftfile /x" in text

    def test_apply_time_files_separates_server_after_nonterminated_last_line(
        self, monkeypatch, tmp_path
    ):
        chrony = tmp_path / "chrony.conf"
        chrony.write_text("pool old.pool iburst\nlogdir /tmp")
        monkeypatch.setattr(device_store, "CHRONY_CONF", str(chrony))

        device_store.apply_time_files("", "pool.ntp.org")

        assert chrony.read_text() == "logdir /tmp\nserver pool.ntp.org iburst\n"
