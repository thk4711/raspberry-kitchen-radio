"""Tests for the Area A display & audio settings store and validators."""

import os
import stat

import pytest

from radio_web import artwork_store, audio_store, config_store, display_store, validators


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
        display_store.save_display({**display_store.DEFAULTS, "rotate_180": "true"})
        loaded = display_store.load_display()
        assert loaded["rotate_180"] == "true"

    def test_rotate_180_persisted_in_ui_block(self, managed):
        display_store.save_display({**display_store.DEFAULTS, "rotate_180": "true"})
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

    # --- panel selection --------------------------------------------------

    def test_panel_default_is_st7789(self):
        assert display_store.DEFAULTS["panel"] == "st7789"

    def test_panel_gc9a01_roundtrip(self, managed):
        display_store.save_display({**display_store.DEFAULTS, "panel": "gc9a01"})
        loaded = display_store.load_display()
        assert loaded["panel"] == "gc9a01"

    def test_panel_st7789_roundtrip(self, managed):
        display_store.save_display({**display_store.DEFAULTS, "panel": "st7789"})
        loaded = display_store.load_display()
        assert loaded["panel"] == "st7789"

    def test_panel_writes_display_section_with_geometry(self, managed):
        display_store.save_display({**display_store.DEFAULTS, "panel": "gc9a01"})
        text = open(display_store.managed_display_path()).read()
        assert "[display]" in text
        assert "panel = gc9a01" in text
        assert "width = 240" in text
        assert "height = 240" in text

    def test_panel_st7789_writes_correct_geometry(self, managed):
        display_store.save_display({**display_store.DEFAULTS, "panel": "st7789"})
        text = open(display_store.managed_display_path()).read()
        assert "panel = st7789" in text
        assert "width = 240" in text
        assert "height = 280" in text

    def test_invalid_panel_raises_and_writes_nothing(self, managed):
        with pytest.raises(ValueError, match="Unknown display panel"):
            display_store.save_display({**display_store.DEFAULTS, "panel": "ili9341"})
        assert not os.path.exists(display_store.managed_display_path())

    def test_panel_missing_from_post_defaults_to_st7789(self, managed):
        # simulate a form that omits the panel key entirely
        form = {k: v for k, v in display_store.DEFAULTS.items() if k != "panel"}
        display_store.save_display(form)
        loaded = display_store.load_display()
        assert loaded["panel"] == "st7789"


class TestDisplayPanelValidators:
    def test_validate_panel_st7789(self):
        assert validators.validate_panel("st7789") == "st7789"

    def test_validate_panel_gc9a01(self):
        assert validators.validate_panel("gc9a01") == "gc9a01"

    def test_validate_panel_case_insensitive(self):
        assert validators.validate_panel("GC9A01") == "gc9a01"
        assert validators.validate_panel("ST7789") == "st7789"

    def test_validate_panel_empty_defaults_to_st7789(self):
        assert validators.validate_panel("") == "st7789"
        assert validators.validate_panel(None) == "st7789"  # type: ignore[arg-type]

    def test_validate_panel_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown display panel"):
            validators.validate_panel("ili9341")

    def test_panel_geometry_st7789(self):
        assert validators.PANEL_GEOMETRY["st7789"] == (240, 280)

    def test_panel_geometry_gc9a01(self):
        assert validators.PANEL_GEOMETRY["gc9a01"] == (240, 240)


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


class TestArtworkStore:
    def test_defaults_disabled_when_no_managed_file(self, managed):
        assert artwork_store.load_artwork() == {
            "enabled": "false",
            "provider": "musicbrainz",
        }

    def test_enabled_roundtrip_uses_fixed_provider_and_mode(self, managed):
        artwork_store.save_artwork({"enabled": "true"})

        assert artwork_store.load_artwork()["enabled"] == "true"
        assert open(artwork_store.managed_artwork_path()).read() == (
            "[online_artwork]\nenabled = true\nprovider = musicbrainz\n"
        )
        assert stat.S_IMODE(os.stat(artwork_store.managed_artwork_path()).st_mode) == 0o644

    def test_invalid_or_unsupported_file_fails_closed(self, managed):
        open(artwork_store.managed_artwork_path(), "w").write(
            "[online_artwork]\nenabled = true\nprovider = unreviewed\n"
        )
        assert artwork_store.load_artwork()["enabled"] == "false"

        open(artwork_store.managed_artwork_path(), "w").write(
            "[online_artwork]\nenabled = perhaps\nprovider = musicbrainz\n"
        )
        assert artwork_store.load_artwork()["enabled"] == "false"

    def test_invalid_submission_writes_nothing(self, managed):
        with pytest.raises(ValueError):
            artwork_store.save_artwork({"enabled": "maybe"})
        assert not os.path.exists(artwork_store.managed_artwork_path())

    def test_backup_and_restore(self, managed):
        artwork_store.save_artwork({"enabled": "false"})
        original = open(artwork_store.managed_artwork_path()).read()
        artwork_store.save_artwork({"enabled": "true"})
        assert open(artwork_store.managed_backup_path()).read() == original

        artwork_store.restore_builtin()
        assert artwork_store.load_artwork() == artwork_store.DEFAULTS
        assert not os.path.exists(artwork_store.managed_artwork_path())
        assert not os.path.exists(artwork_store.managed_backup_path())
