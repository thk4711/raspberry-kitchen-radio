"""Parametric equalizer persistence, routing, and privileged apply tests."""

import math

import pytest

from radio_web import (
    audio_hardware_apply,
    audio_hardware_store,
    config_store,
    equalizer_store,
    helper,
)


@pytest.fixture()
def managed(monkeypatch, tmp_path):
    monkeypatch.setattr(config_store, "MANAGED_CONFIG_DIR", str(tmp_path))
    return tmp_path


def _form():
    settings = equalizer_store.defaults()
    values = equalizer_store.settings_as_form(settings)
    values["eq_enabled"] = "true"
    values["eq_preamp_db"] = "-6"
    values["eq_band_1_enabled"] = "true"
    values["eq_band_1_type"] = "high_pass"
    values["eq_band_1_frequency"] = "80"
    return values


def test_save_and_load_round_trip(managed):
    saved = equalizer_store.save_equalizer(_form())
    loaded = equalizer_store.load_equalizer()
    assert loaded == saved
    assert loaded["enabled"] is True
    assert loaded["bands"][0]["type"] == "high_pass"
    assert loaded["bands"][0]["frequency"] == 80


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("eq_preamp_db", "1"),
        ("eq_band_1_frequency", "10"),
        ("eq_band_1_gain_db", "nan"),
        ("eq_band_1_q", "inf"),
        ("eq_band_1_type", "shell_command"),
    ],
)
def test_validation_rejects_unsafe_values(field, value):
    form = _form()
    form[field] = value
    with pytest.raises(ValueError):
        equalizer_store.validate_settings(form)


def test_enabled_equalizer_wraps_default_and_serializes_all_controls():
    settings = equalizer_store.validate_settings(_form())
    rendered = audio_hardware_apply.render_asound(
        audio_hardware_store.PROFILES["headphones"], settings
    )
    assert "type ladspa" in rendered
    assert "    channels 2" in rendered
    assert 'label "radio_equalizer"' in rendered
    assert 'slave.pcm "radio_output"' in rendered
    assert 'slave.pcm "radio_equalizer"' in rendered
    assert 'slave.pcm "hw:CARD=Headphones,DEV=0"' in rendered
    assert "sysdefault:CARD=Headphones" not in rendered
    assert "                    0 -6" in rendered
    assert "                    1 1" in rendered
    assert "                    2 3" in rendered
    assert "                    3 80" in rendered


def test_disabled_equalizer_has_no_ladspa():
    rendered = audio_hardware_apply.render_asound(
        audio_hardware_store.PROFILES["headphones"], equalizer_store.defaults()
    )
    assert "type ladspa" not in rendered
    assert 'slave.pcm "sysdefault:CARD=Headphones"' in rendered


def test_helper_apply_structural_change_restarts_every_pcm_consumer(monkeypatch):
    calls = []
    # A "restart:" message from apply_equalizer means the EQ stage was inserted
    # or removed, so every long-lived consumer must reopen the route.
    monkeypatch.setattr(
        helper.audio_hardware_apply, "apply_equalizer", lambda: (True, "restart: route updated")
    )

    def fake_run(argv, _ok, _fail):
        calls.append(argv)
        return True, "ok"

    monkeypatch.setattr(helper, "_run_argv", fake_run)
    assert helper.dispatch("apply_equalizer", {})[0] is True
    assert calls == [
        ["/etc/init.d/S50mpd", "restart"],
        ["/etc/init.d/S90radio", "restart"],
        ["/etc/init.d/S42bluetooth", "restart"],
        ["/etc/init.d/S39usb-audio", "restart"],
    ]


def test_helper_apply_live_change_restarts_nothing(monkeypatch):
    calls = []
    # A "live:" message means the parameters were pushed to the runtime file the
    # LADSPA plugin re-reads; no consumer restart is required.
    monkeypatch.setattr(
        helper.audio_hardware_apply, "apply_equalizer", lambda: (True, "live: updated")
    )

    def fake_run(argv, _ok, _fail):
        calls.append(argv)
        return True, "ok"

    monkeypatch.setattr(helper, "_run_argv", fake_run)
    ok, message = helper.dispatch("apply_equalizer", {})
    assert ok is True
    assert "live" in message
    assert calls == []


def test_control_values_order_and_disabled_bypass():
    settings = equalizer_store.validate_settings(_form())
    values = equalizer_store.control_values(settings)
    assert len(values) == equalizer_store.RUNTIME_CONTROL_VALUES
    assert values[0] == -6.0  # preamp
    assert values[1] == 1.0  # band 1 enabled
    assert values[2] == float(equalizer_store.FILTER_TYPE_IDS["high_pass"])
    assert values[3] == 80.0  # band 1 frequency
    # A globally disabled EQ collapses to identity so the live path bypasses.
    disabled = equalizer_store.defaults()
    assert equalizer_store.control_values(disabled) == [0.0] * equalizer_store.RUNTIME_CONTROL_VALUES


def test_write_runtime_round_trip_and_generation(managed, monkeypatch, tmp_path):
    rt = tmp_path / "run" / "equalizer.rt"
    monkeypatch.setenv("RADIO_EQUALIZER_RT", str(rt))
    settings = equalizer_store.validate_settings(_form())
    equalizer_store.write_runtime(settings)
    magic, gen1, *values = equalizer_store.RUNTIME_STRUCT.unpack(rt.read_bytes())
    assert magic == equalizer_store.RUNTIME_MAGIC
    assert gen1 >= 1
    assert values == equalizer_store.control_values(settings)
    # A second write bumps the generation so the plugin notices the change.
    equalizer_store.write_runtime(settings)
    _magic, gen2, *_rest = equalizer_store.RUNTIME_STRUCT.unpack(rt.read_bytes())
    assert gen2 == gen1 + 1


def test_write_runtime_directory_is_world_readable_under_tight_umask(
    managed, monkeypatch, tmp_path
):
    # The target's root umask is 0077; the runtime directory must still be 0755
    # so the unprivileged 'mpd' user can read equalizer.rt inside the plugin.
    import os
    import stat

    rt = tmp_path / "runradio" / "equalizer.rt"
    monkeypatch.setenv("RADIO_EQUALIZER_RT", str(rt))
    old_umask = os.umask(0o077)
    try:
        equalizer_store.write_runtime(equalizer_store.defaults())
    finally:
        os.umask(old_umask)
    directory_mode = stat.S_IMODE(os.stat(rt.parent).st_mode)
    file_mode = stat.S_IMODE(os.stat(rt).st_mode)
    assert directory_mode == 0o755
    assert file_mode == 0o644


def test_apply_equalizer_live_when_stage_unchanged(managed, monkeypatch, tmp_path):
    rt = tmp_path / "equalizer.rt"
    asound = tmp_path / "asound.conf"
    monkeypatch.setenv("RADIO_EQUALIZER_RT", str(rt))
    monkeypatch.setattr(audio_hardware_apply, "ASOUND_CONF", str(asound))
    # A disabled EQ matches a bypass asound.conf (no ladspa stage) -> live.
    equalizer_store.save_equalizer(equalizer_store.settings_as_form(equalizer_store.defaults()))
    asound.write_text("pcm.!default { type plug }\n")
    ok, message = audio_hardware_apply.apply_equalizer()
    assert ok is True
    assert message.startswith("live:")
    assert rt.exists()
    # asound.conf is untouched on the live path.
    assert "radio_equalizer" not in asound.read_text()


def test_apply_equalizer_structural_when_stage_toggles(managed, monkeypatch, tmp_path):
    rt = tmp_path / "equalizer.rt"
    asound = tmp_path / "asound.conf"
    monkeypatch.setenv("RADIO_EQUALIZER_RT", str(rt))
    monkeypatch.setattr(audio_hardware_apply, "ASOUND_CONF", str(asound))
    # Enabling the EQ while asound.conf has no stage forces a route rewrite.
    equalizer_store.save_equalizer(_form())
    asound.write_text("pcm.!default { type plug }\n")
    ok, message = audio_hardware_apply.apply_equalizer()
    assert ok is True
    assert message.startswith("restart:")
    assert "radio_equalizer" in asound.read_text()
    assert rt.exists()


def test_defaults_are_finite():
    settings = equalizer_store.defaults()
    assert settings["enabled"] is False
    assert settings["preamp_db"] == -3.0
    assert [band["frequency"] for band in settings["bands"]] == [
        60.0,
        120.0,
        250.0,
        500.0,
        1000.0,
        2000.0,
        4000.0,
        8000.0,
        12000.0,
        16000.0,
    ]
    assert all(not band["enabled"] for band in settings["bands"])
    assert all(band["type"] == "bell" for band in settings["bands"])
    assert all(band["gain_db"] == 0.0 for band in settings["bands"])
    assert all(band["q"] == 1.0 for band in settings["bands"])
    assert all(
        math.isfinite(value)
        for band in settings["bands"]
        for value in (band["frequency"], band["gain_db"], band["q"])
    )


def test_restore_returns_complete_flat_defaults(managed):
    equalizer_store.save_equalizer(_form())
    equalizer_store.restore_builtin()
    assert equalizer_store.load_equalizer() == equalizer_store.defaults()
