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
