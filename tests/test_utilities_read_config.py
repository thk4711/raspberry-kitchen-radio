"""Tests for UtilityLibrary.read_config in lib/utilities.py.

Exercises the simple INI parser: sections, key/value splitting, boolean and
integer coercion, comment skipping, whitespace handling, and the missing-file
behaviour (which calls ``exit(1)``).
"""
import pytest
from utilities import UtilityLibrary


def _write(tmp_path, text):
    path = tmp_path / "radio.conf"
    path.write_text(text)
    return str(path)


class TestReadConfig:
    def test_sections_and_basic_values(self, tmp_path):
        cfg = _write(tmp_path, "[mpd]\nhost = localhost\nport = 6600\n")
        conf = UtilityLibrary.read_config(cfg)
        assert conf["mpd"]["host"] == "localhost"
        assert conf["mpd"]["port"] == 6600
        assert isinstance(conf["mpd"]["port"], int)

    def test_boolean_coercion(self, tmp_path):
        cfg = _write(tmp_path, "[ssh]\nenabled = true\ndisabled = False\n")
        conf = UtilityLibrary.read_config(cfg)
        assert conf["ssh"]["enabled"] is True
        assert conf["ssh"]["disabled"] is False

    def test_comments_and_blank_lines_ignored(self, tmp_path):
        cfg = _write(
            tmp_path,
            "# a comment\n[audio]\n# another comment\nmixer = Digital\n\n",
        )
        conf = UtilityLibrary.read_config(cfg)
        assert conf["audio"] == {"mixer": "Digital"}

    def test_whitespace_is_stripped(self, tmp_path):
        cfg = _write(tmp_path, "[metadata]\n   update_interval   =   3   \n")
        conf = UtilityLibrary.read_config(cfg)
        assert conf["metadata"]["update_interval"] == 3

    def test_value_with_equals_sign_is_preserved(self, tmp_path):
        cfg = _write(tmp_path, "[spotify]\ntoken = a=b=c\n")
        conf = UtilityLibrary.read_config(cfg)
        # Only the first '=' splits key/value.
        assert conf["spotify"]["token"] == "a=b=c"

    def test_multiple_sections(self, tmp_path):
        cfg = _write(
            tmp_path,
            "[mpd]\nhost = localhost\n[gpio]\namp = 26\n",
        )
        conf = UtilityLibrary.read_config(cfg)
        assert conf["mpd"]["host"] == "localhost"
        assert conf["gpio"]["amp"] == 26

    def test_missing_file_exits(self, tmp_path):
        missing = str(tmp_path / "does-not-exist.conf")
        with pytest.raises(SystemExit):
            UtilityLibrary.read_config(missing)


class TestReadConfigLayered:
    def test_override_key_wins_and_others_preserved(self, tmp_path):
        default = tmp_path / "default.conf"
        default.write_text(
            "[mpd]\nhost = localhost\nport = 6600\n[gpio]\namp = 26\n"
        )
        override = tmp_path / "device.ini"
        override.write_text("[mpd]\nhost = radio.local\n")
        conf = UtilityLibrary.read_config_layered(str(default), str(override))
        # Overridden key wins ...
        assert conf["mpd"]["host"] == "radio.local"
        # ... while untouched keys in the same section and other sections stay.
        assert conf["mpd"]["port"] == 6600
        assert conf["gpio"]["amp"] == 26

    def test_override_can_add_new_section(self, tmp_path):
        default = tmp_path / "default.conf"
        default.write_text("[mpd]\nhost = localhost\n")
        override = tmp_path / "device.ini"
        override.write_text("[extra]\nkey = value\n")
        conf = UtilityLibrary.read_config_layered(str(default), str(override))
        assert conf["mpd"]["host"] == "localhost"
        assert conf["extra"]["key"] == "value"

    def test_absent_override_is_noop(self, tmp_path):
        default = tmp_path / "default.conf"
        default.write_text("[mpd]\nhost = localhost\nport = 6600\n")
        missing = str(tmp_path / "no-override.ini")
        conf = UtilityLibrary.read_config_layered(str(default), missing)
        assert conf == {"mpd": {"host": "localhost", "port": 6600}}

    def test_none_override_is_noop(self, tmp_path):
        default = tmp_path / "default.conf"
        default.write_text("[mpd]\nhost = localhost\n")
        conf = UtilityLibrary.read_config_layered(str(default), None)
        assert conf == {"mpd": {"host": "localhost"}}

    def test_coercion_preserved_through_override(self, tmp_path):
        default = tmp_path / "default.conf"
        default.write_text("[flags]\nenabled = false\ncount = 1\n")
        override = tmp_path / "device.ini"
        override.write_text("[flags]\nenabled = true\ncount = 42\n")
        conf = UtilityLibrary.read_config_layered(str(default), str(override))
        assert conf["flags"]["enabled"] is True
        assert conf["flags"]["count"] == 42
        assert isinstance(conf["flags"]["count"], int)

    def test_missing_default_exits(self, tmp_path):
        missing_default = str(tmp_path / "does-not-exist.conf")
        with pytest.raises(SystemExit):
            UtilityLibrary.read_config_layered(missing_default, None)


class TestReadConfigPreferred:
    def test_override_present_replaces_default(self, tmp_path):
        default = tmp_path / "stations.conf"
        default.write_text("[one]\nurl = http://default\n")
        override = tmp_path / "stations.ini"
        override.write_text("[two]\nurl = http://override\n")
        conf = UtilityLibrary.read_config_preferred(str(override), str(default))
        # Whole-file replacement: the default section is gone entirely.
        assert conf == {"two": {"url": "http://override"}}

    def test_override_absent_uses_default(self, tmp_path):
        default = tmp_path / "stations.conf"
        default.write_text("[one]\nurl = http://default\n")
        missing = str(tmp_path / "no-stations.ini")
        conf = UtilityLibrary.read_config_preferred(missing, str(default))
        assert conf == {"one": {"url": "http://default"}}

    def test_missing_default_exits_when_no_override(self, tmp_path):
        missing_override = str(tmp_path / "no-stations.ini")
        missing_default = str(tmp_path / "does-not-exist.conf")
        with pytest.raises(SystemExit):
            UtilityLibrary.read_config_preferred(missing_override, missing_default)
