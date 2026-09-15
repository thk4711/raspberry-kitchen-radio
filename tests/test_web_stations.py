"""Tests for the Phase 5 station-editing store, validators and actions."""

import os

import pytest

from radio_web import actions, config_store, stations_store, validators


class TestValidators:
    def test_valid_name(self):
        assert validators.validate_station_name("  MDR Kultur ") == "MDR Kultur"

    @pytest.mark.parametrize("value", ["", "   ", "a\x00b", "with[bracket", "x" * 65])
    def test_invalid_name(self, value):
        with pytest.raises(ValueError):
            validators.validate_station_name(value)

    def test_valid_url(self):
        url = "https://st01.example.com/stream.mp3"
        assert validators.validate_stream_url(url) == url

    @pytest.mark.parametrize(
        "value",
        ["", "ftp://host/x", "file:///etc/passwd", "http://", "notaurl", "https://"],
    )
    def test_invalid_url(self, value):
        with pytest.raises(ValueError):
            validators.validate_stream_url(value)

    def test_logo_empty_allowed(self):
        assert validators.validate_logo_filename("") == ""

    def test_logo_valid(self):
        assert validators.validate_logo_filename("MDR_KULTUR.png") == "MDR_KULTUR.png"

    @pytest.mark.parametrize(
        "value",
        ["../etc/passwd", "a/b.png", "logo.txt", "..", "logo.png.exe", "a\\b.png"],
    )
    def test_logo_rejected(self, value):
        with pytest.raises(ValueError):
            validators.validate_logo_filename(value)


@pytest.fixture()
def managed(monkeypatch, tmp_path):
    monkeypatch.setattr(config_store, "MANAGED_CONFIG_DIR", str(tmp_path))
    return tmp_path


class TestStationsStore:
    def test_load_defaults_when_no_managed_file(self, managed):
        slots = stations_store.load_stations()
        assert len(slots) == stations_store.PRESET_COUNT
        # The shipped built-in list leads with Deutschlandfunk.
        assert slots[0]["name"] == "Deutschlandfunk"
        assert slots[0]["url"].startswith("http")

    def test_serialize_round_trips_through_player_parser(self, tmp_path):
        # What we write must parse back with the SAME player-side parser
        # (lib.utilities), so managed edits load correctly on the device.
        import sys

        sys.path.insert(0, os.path.join(os.getcwd(), "lib"))
        from utilities import UtilityLibrary  # noqa: E402

        slots = [
            {"name": "Alpha", "url": "https://a.example/s", "logo": "a.png"},
            {"name": "Beta", "url": "http://b.example/s", "logo": ""},
        ] + [{"name": "", "url": "", "logo": ""}] * 4
        text = stations_store.serialize_stations(slots)
        tmp = tmp_path / "stations.conf"
        tmp.write_text(text, encoding="utf-8")
        parsed = UtilityLibrary._parse_config(str(tmp))
        assert parsed["Alpha"]["url"] == "https://a.example/s"
        assert parsed["Alpha"]["logo"] == "a.png"
        assert parsed["Beta"]["name"] == "Beta"
        # Empty slots are omitted, not written as blank presets.
        assert "" not in parsed

    def test_save_creates_file_and_backup(self, managed):
        first = [{"name": "One", "url": "https://one.example/s", "logo": ""}] + [
            {"name": "", "url": "", "logo": ""}
        ] * 5
        stations_store.save_stations(first)
        path = stations_store.managed_stations_path()
        assert os.path.isfile(path)
        # First save: no prior file, so no backup yet.
        assert not os.path.isfile(stations_store.managed_backup_path())
        # Mode is 0644 (atomic managed-file write).
        assert (os.stat(path).st_mode & 0o777) == config_store.CONFIG_MODE

        second = [{"name": "Two", "url": "https://two.example/s", "logo": ""}] + [
            {"name": "", "url": "", "logo": ""}
        ] * 5
        stations_store.save_stations(second)
        # Second save keeps exactly one .bak of the previous content.
        assert os.path.isfile(stations_store.managed_backup_path())
        assert "One" in config_store.read_text(stations_store.managed_backup_path())
        assert "Two" in config_store.read_text(path)

    def test_save_rejects_invalid_and_writes_nothing(self, managed):
        bad = [{"name": "Bad", "url": "ftp://nope/x", "logo": ""}] + [
            {"name": "", "url": "", "logo": ""}
        ] * 5
        with pytest.raises(ValueError):
            stations_store.save_stations(bad)
        assert not os.path.isfile(stations_store.managed_stations_path())

    def test_restore_builtin_removes_managed_files(self, managed):
        slots = [{"name": "One", "url": "https://one.example/s", "logo": ""}] + [
            {"name": "", "url": "", "logo": ""}
        ] * 5
        stations_store.save_stations(slots)
        stations_store.save_stations(slots)  # create a .bak too
        stations_store.restore_builtin()
        assert not os.path.isfile(stations_store.managed_stations_path())
        assert not os.path.isfile(stations_store.managed_backup_path())
        # Idempotent: a second restore does not raise.
        stations_store.restore_builtin()
        # And loading now falls back to the built-in list.
        assert stations_store.load_stations()[0]["name"] == "Deutschlandfunk"

    def test_test_stream_rejects_bad_url_without_network(self, managed):
        ok, detail = stations_store.test_stream("ftp://nope/x")
        assert ok is False
        assert "http" in detail.lower()


class TestActions:
    def test_run_action_rejects_unknown(self):
        ok, detail = actions.run_action("/bin/sh -c reboot")
        assert ok is False
        assert "Unknown" in detail
        assert not actions.is_valid_action("rm -rf")

    def test_restart_radio_is_whitelisted(self):
        assert actions.is_valid_action("restart_radio")

    def test_regular_action_uses_normal_timeout(self, monkeypatch):
        seen = {}

        def fake_exchange(_request, *, timeout):
            seen["timeout"] = timeout
            return True, "done"

        monkeypatch.setattr(actions, "_exchange", fake_exchange)
        assert actions.run_action("restart_radio") == (True, "done")
        assert seen["timeout"] == actions._CONNECT_TIMEOUT_SECONDS

    def test_equalizer_apply_uses_multi_service_timeout(self, monkeypatch):
        seen = {}

        def fake_exchange(_request, *, timeout):
            seen["timeout"] = timeout
            return True, "done"

        monkeypatch.setattr(actions, "_exchange", fake_exchange)
        assert actions.run_action("apply_equalizer") == (True, "done")
        assert seen["timeout"] == actions._EQUALIZER_TIMEOUT_SECONDS

    def test_run_action_helper_unavailable(self, monkeypatch, tmp_path):
        # With no helper listening on the socket, a whitelisted action degrades
        # to a friendly failure rather than raising (Phase 7 socket client).
        monkeypatch.setattr(
            "radio_web.helper_protocol.SOCKET_PATH",
            str(tmp_path / "absent.sock"),
        )
        ok, detail = actions.run_action("restart_radio")
        assert ok is False
        assert "helper" in detail.lower()
