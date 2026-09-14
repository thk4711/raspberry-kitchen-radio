"""Tests for the Area A display & audio settings store and validators."""

import os
import stat

import pytest

from radio_web import audio_store, config_store, display_store, validators


@pytest.fixture()
def managed(monkeypatch, tmp_path):
    monkeypatch.setattr(config_store, "MANAGED_CONFIG_DIR", str(tmp_path))
    return tmp_path


class TestDisplayValidators:
    def test_bool_flag_true_and_false(self):
        assert validators.validate_bool_flag("true") is True
        assert validators.validate_bool_flag("") is False
        assert validators.validate_bool_flag("on") is True

    def test_bool_flag_rejects_junk(self):
        with pytest.raises(ValueError):
            validators.validate_bool_flag("maybe")

    def test_idle_timeout_bounds(self):
        assert validators.validate_idle_timeout("30") == 30
        with pytest.raises(ValueError):
            validators.validate_idle_timeout("-1")
        with pytest.raises(ValueError):
            validators.validate_idle_timeout("999999")

    def test_crossfade_ms_bounds(self):
        assert validators.validate_crossfade_ms("0") == 0
        with pytest.raises(ValueError):
            validators.validate_crossfade_ms("6000")

    def test_overlay_duration(self):
        assert validators.validate_overlay_duration("1.5", "OSD") == 1.5
        with pytest.raises(ValueError):
            validators.validate_overlay_duration("100", "OSD")

    def test_theme_preset_whitelist(self):
        assert validators.validate_theme_preset("") == "default"
        assert validators.validate_theme_preset("dim_night") == "dim_night"
        with pytest.raises(ValueError):
            validators.validate_theme_preset("neon")

    def test_mixer_name(self):
        assert validators.validate_mixer_name("Digital") == "Digital"
        with pytest.raises(ValueError):
            validators.validate_mixer_name("Digital; rm -rf /")

    def test_volume_percent(self):
        assert validators.validate_volume_percent("100") == 100
        with pytest.raises(ValueError):
            validators.validate_volume_percent("101")

    def test_amp_gpio_accepts_pin_in_range(self):
        assert validators.validate_amp_gpio("26") == "26"
        assert validators.validate_amp_gpio(" 0 ") == "0"
        assert validators.validate_amp_gpio("27") == "27"

    def test_amp_gpio_sentinel_for_no_amp(self):
        # A board with no power amp: sentinel, empty and "false" all mean "none".
        assert validators.validate_amp_gpio("none") == validators.NO_AMP_GPIO
        assert validators.validate_amp_gpio("") == validators.NO_AMP_GPIO
        assert validators.validate_amp_gpio("false") == validators.NO_AMP_GPIO
        assert validators.validate_amp_gpio("None") == validators.NO_AMP_GPIO

    def test_amp_gpio_rejects_out_of_range_and_junk(self):
        for bad in ("28", "-1", "999", "rm -rf /", "26; reboot"):
            with pytest.raises(ValueError):
                validators.validate_amp_gpio(bad)


class TestDisplayStore:
    def test_defaults_when_no_managed_file(self, managed):
        settings = display_store.load_display()
        assert settings == display_store.DEFAULTS

    def test_save_and_reload_roundtrip(self, managed):
        display_store.save_display(
            {
                "theme_preset": "default",
                "animations": "true",
                "idle_timeout": "45",
                "crossfade_ms": "200",
                "clock_size": "28",
                "osd_duration": "2",
                "toast_duration": "1.6",
            }
        )
        loaded = display_store.load_display()
        assert loaded["idle_timeout"] == "45"
        assert loaded["crossfade_ms"] == "200"
        assert loaded["animations"] == "true"

    def test_save_is_0644_and_has_ui_section(self, managed):
        display_store.save_display(dict(display_store.DEFAULTS))
        path = display_store.managed_display_path()
        mode = stat.S_IMODE(os.stat(path).st_mode)
        assert mode == 0o644
        text = open(path).read()
        assert "[ui]" in text

    def test_no_animations_preset_disables_crossfade(self, managed):
        display_store.save_display(
            {
                **display_store.DEFAULTS,
                "theme_preset": "no_animations",
            }
        )
        text = open(display_store.managed_display_path()).read()
        assert "animations = false" in text
        assert "crossfade_ms = 0" in text

    def test_invalid_field_writes_nothing(self, managed):
        with pytest.raises(ValueError):
            display_store.save_display(
                {
                    **display_store.DEFAULTS,
                    "idle_timeout": "nope",
                }
            )
        assert not os.path.exists(display_store.managed_display_path())

    def test_restore_removes_override(self, managed):
        display_store.save_display(dict(display_store.DEFAULTS))
        assert os.path.exists(display_store.managed_display_path())
        display_store.restore_builtin()
        assert not os.path.exists(display_store.managed_display_path())

    def test_backup_kept_on_second_save(self, managed):
        display_store.save_display(dict(display_store.DEFAULTS))
        display_store.save_display(
            {
                **display_store.DEFAULTS,
                "idle_timeout": "10",
            }
        )
        assert os.path.exists(display_store.managed_backup_path())

    def test_ui_section_parses_through_theme(self, managed):
        # What we write must round-trip through the player's theme builder.
        import sys

        sys.path.insert(
            0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "lib", "display")
        )
        import theme as theme_mod  # noqa: E402

        display_store.save_display(
            {
                **display_store.DEFAULTS,
                "idle_timeout": "12",
                "crossfade_ms": "300",
            }
        )
        text = open(display_store.managed_display_path()).read()
        ui = {}
        section = ""
        for raw in text.splitlines():
            line = raw.strip()
            if line.startswith("["):
                section = line.strip("[]")
            elif "=" in line and section == "ui":
                k, v = (p.strip() for p in line.split("=", 1))
                ui[k] = v
        built = theme_mod.build_theme(ui)
        assert built.idle_timeout == 12.0
        assert built.crossfade_ms == 300

    def test_rotate_180_default_is_false(self):
        assert display_store.DEFAULTS["rotate_180"] == "false"

    def test_rotate_180_roundtrip(self, managed):
        display_store.save_display(
            {**display_store.DEFAULTS, "rotate_180": "true"}
        )
        loaded = display_store.load_display()
        assert loaded["rotate_180"] == "true"

    def test_rotate_180_persisted_in_ui_block(self, managed):
        display_store.save_display(
            {**display_store.DEFAULTS, "rotate_180": "true"}
        )
        text = open(display_store.managed_display_path()).read()
        assert "rotate_180 = true" in text

    def test_rotate_180_unchecked_saves_false(self, managed):
        # Simulate an unchecked checkbox: the key is absent from the POST body.
        form = dict(display_store.DEFAULTS)
        form.pop("rotate_180")
        form["rotate_180"] = ""  # what routes.py sends when checkbox is absent
        display_store.save_display(form)
        loaded = display_store.load_display()
        assert loaded["rotate_180"] == "false"


class TestAudioStore:
    def test_defaults_when_no_managed_file(self, managed):
        assert audio_store.load_audio() == audio_store.DEFAULTS

    def test_save_and_reload_roundtrip(self, managed):
        audio_store.save_audio({"max_volume": "80"})
        loaded = audio_store.load_audio()
        assert loaded["max_volume"] == "80"

    def test_serialize_has_both_sections(self, managed):
        audio_store.save_audio({"max_volume": "90"})
        text = open(audio_store.managed_audio_path()).read()
        assert "[volume]" in text
        assert "max = 90" in text
        assert "mixer" not in text and "amp =" not in text

    def test_invalid_volume_writes_nothing(self, managed):
        with pytest.raises(ValueError):
            audio_store.save_audio({"max_volume": "500"})
        assert not os.path.exists(audio_store.managed_audio_path())

    def test_restore_removes_override(self, managed):
        audio_store.save_audio({"mixer": "Digital", "max_volume": "50"})
        audio_store.restore_builtin()
        assert not os.path.exists(audio_store.managed_audio_path())

    def test_save_max_volume_replaces_legacy_profile_fields(self, managed):
        audio_store.managed_audio_path()
        open(audio_store.managed_audio_path(), "w").write(
            "[audio]\nmixer = Digital\n[volume]\nmax = 90\n[gpio]\namp = 26\n"
        )
        audio_store.save_audio({"max_volume": "60"})
        text = open(audio_store.managed_audio_path()).read()
        assert text == "[volume]\nmax = 60\n"
