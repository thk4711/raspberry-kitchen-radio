"""Tests for the Phase 6 source feature-flag store and validators."""
import os
import stat

import pytest

from radio_web import config_store, sources_store, validators


@pytest.fixture()
def managed(monkeypatch, tmp_path):
    monkeypatch.setattr(config_store, "MANAGED_CONFIG_DIR", str(tmp_path))
    return tmp_path


class TestSourceValidators:
    def test_all_present_returns_all_enabled(self):
        form = {key: "true" for key, _ in sources_store.SOURCE_KEYS}
        flags = validators.validate_source_flags(form)
        assert flags == sources_store.default_flags()

    def test_missing_checkboxes_are_disabled(self):
        # Unchecked HTML checkboxes are simply absent from the POST body.
        flags = validators.validate_source_flags({"spotify": "true"})
        assert flags["spotify"] is True
        assert flags["bluetooth"] is False
        assert set(flags) == {key for key, _ in sources_store.SOURCE_KEYS}

    def test_empty_form_disables_everything(self):
        flags = validators.validate_source_flags({})
        assert all(value is False for value in flags.values())
        assert set(flags) == {key for key, _ in sources_store.SOURCE_KEYS}

    def test_unknown_key_rejected(self):
        with pytest.raises(ValueError):
            validators.validate_source_flags({"root_password": "x"})


class TestSourcesStore:
    def test_defaults_when_no_managed_file(self, managed):
        flags = sources_store.load_sources()
        assert flags == sources_store.default_flags()
        assert all(flags.values())

    def test_save_and_load_round_trip(self, managed):
        flags = sources_store.default_flags()
        flags["bluetooth"] = False
        sources_store.save_sources(flags)
        loaded = sources_store.load_sources()
        assert loaded["bluetooth"] is False
        assert loaded["internet_radio"] is True

    def test_ssh_is_not_a_music_source(self, managed):
        assert "ssh" not in sources_store.default_flags()
        with pytest.raises(ValueError):
            sources_store.save_sources({"ssh": False})

    def test_missing_key_in_file_defaults_enabled(self, managed):
        # A partial file (only one key) leaves the rest enabled.
        config_store.atomic_write(
            sources_store.managed_sources_path(),
            "[sources]\nspotify = false\n",
            config_store.CONFIG_MODE,
        )
        flags = sources_store.load_sources()
        assert flags["spotify"] is False
        assert flags["airplay"] is True

    def test_save_creates_file_and_backup(self, managed):
        sources_store.save_sources(sources_store.default_flags())
        path = sources_store.managed_sources_path()
        assert os.path.isfile(path)
        mode = stat.S_IMODE(os.stat(path).st_mode)
        assert mode == config_store.CONFIG_MODE
        assert not os.path.isfile(sources_store.managed_backup_path())
        # A second save creates exactly one .bak.
        flags = sources_store.default_flags()
        flags["usb_audio"] = False
        sources_store.save_sources(flags)
        assert os.path.isfile(sources_store.managed_backup_path())

    def test_serialize_round_trips_through_player_parser(self):
        import sys

        sys.path.insert(0, os.path.join(os.getcwd(), "lib"))
        from utilities import UtilityLibrary  # noqa: E402

        flags = sources_store.default_flags()
        flags["bluetooth"] = False
        text = sources_store.serialize_sources(flags)
        tmp = os.path.join(os.getcwd(), "tests", "_tmp_sources.ini")
        with open(tmp, "w", encoding="utf-8") as handle:
            handle.write(text)
        try:
            parsed = UtilityLibrary._parse_config(tmp)
        finally:
            os.unlink(tmp)
        # The player parser coerces true/false to bool.
        assert parsed["sources"]["bluetooth"] is False
        assert parsed["sources"]["internet_radio"] is True

    def test_restore_builtin_removes_files(self, managed):
        flags = sources_store.default_flags()
        flags["spotify"] = False
        sources_store.save_sources(flags)
        sources_store.save_sources(flags)  # create a .bak too
        assert os.path.isfile(sources_store.managed_sources_path())
        sources_store.restore_builtin()
        assert not os.path.isfile(sources_store.managed_sources_path())
        assert not os.path.isfile(sources_store.managed_backup_path())
        # Load now falls back to all-enabled defaults.
        assert sources_store.load_sources() == sources_store.default_flags()

    def test_restore_builtin_idempotent(self, managed):
        sources_store.restore_builtin()  # no files present, must not raise

    def test_validate_flags_rejects_unknown_key(self):
        with pytest.raises(ValueError):
            sources_store.validate_flags({"internet_radio": True, "bogus": True})


class TestDerivedDependencies:
    def test_bluetooth_and_airplay_notes(self):
        flags = sources_store.default_flags()
        notes = sources_store.derived_dependencies(flags)
        assert any("D-Bus" in note for note in notes)
        assert any("Avahi" in note for note in notes)

    def test_disabled_sources_drop_their_notes(self):
        flags = sources_store.default_flags()
        flags["bluetooth"] = False
        flags["airplay"] = False
        assert sources_store.derived_dependencies(flags) == []
