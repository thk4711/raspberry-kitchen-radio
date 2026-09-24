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
    # The preamp is automatic; _form() has no boosts or loudness, so it is 0 dB.
    assert "                    0 0" in rendered
    assert "                    1 1" in rendered
    assert "                    2 3" in rendered
    assert "                    3 80" in rendered


def test_enabled_equalizer_preamp_reserves_headroom_for_boost():
    form = _form()
    form["eq_band_5_enabled"] = "true"
    form["eq_band_5_type"] = "bell"
    form["eq_band_5_gain_db"] = "9"
    settings = equalizer_store.validate_settings(form)
    rendered = audio_hardware_apply.render_asound(
        audio_hardware_store.PROFILES["headphones"], settings
    )
    # Preamp is minus the largest positive band gain.
    assert "                    0 -9" in rendered


def test_control_values_include_loudness_triple():
    form = _form()
    form["loudness_amount"] = "8"
    settings = equalizer_store.validate_settings(form)
    values = equalizer_store.control_values(settings, current_volume=30.0)
    # The last three control values are loudness enabled, amount and volume.
    assert values[equalizer_store.RUNTIME_CONTROL_VALUES - 3] == 1.0
    assert values[equalizer_store.RUNTIME_CONTROL_VALUES - 2] == 8.0
    assert values[equalizer_store.RUNTIME_CONTROL_VALUES - 1] == 30.0
    # Volume is clamped to 0..100.
    high = equalizer_store.control_values(settings, current_volume=250.0)
    assert high[equalizer_store.RUNTIME_CONTROL_VALUES - 1] == 100.0


def test_loudness_amount_zero_disables_loudness():
    # A level of 0 is equivalent to loudness off: the derived flag and the
    # runtime "enabled" control are both cleared.
    form = _form()
    form["loudness_amount"] = "0"
    settings = equalizer_store.validate_settings(form)
    assert settings["loudness_enabled"] is False
    values = equalizer_store.control_values(settings, current_volume=30.0)
    assert values[equalizer_store.RUNTIME_CONTROL_VALUES - 3] == 0.0


def test_computed_preamp_reserves_headroom():
    form = _form()
    # A flat, band-only EQ with no loudness and no boosts needs no reduction.
    form["loudness_amount"] = "0"
    for index in range(1, equalizer_store.MAX_BANDS + 1):
        form[f"eq_band_{index}_enabled"] = "false"
        form[f"eq_band_{index}_type"] = "bell"
        form[f"eq_band_{index}_gain_db"] = "0"
    flat = equalizer_store.validate_settings(form)
    assert equalizer_store.computed_preamp_db(flat) == 0.0

    # Loudness at full level reserves the low-shelf headroom (LOUDNESS_LOW_MAX_DB).
    loud = dict(form)
    loud["loudness_amount"] = "10"
    assert (
        equalizer_store.computed_preamp_db(equalizer_store.validate_settings(loud))
        == -equalizer_store.LOUDNESS_LOW_MAX_DB
    )

    # A boosted enabled band reserves headroom equal to its gain.
    boosted = dict(form)
    boosted["eq_band_2_enabled"] = "true"
    boosted["eq_band_2_gain_db"] = "12"
    assert equalizer_store.computed_preamp_db(equalizer_store.validate_settings(boosted)) == -12.0

    # The largest boost wins (band vs loudness).
    both = dict(boosted)
    both["loudness_amount"] = "10"
    assert equalizer_store.computed_preamp_db(equalizer_store.validate_settings(both)) == -12.0


def test_computed_preamp_off_when_equalizer_disabled():
    form = _form()
    form["eq_enabled"] = "false"
    form["loudness_amount"] = "10"
    form["eq_band_1_enabled"] = "true"
    form["eq_band_1_gain_db"] = "15"
    settings = equalizer_store.validate_settings(form)
    # A disabled stage passes through untouched, so no preamp reduction applies.
    assert equalizer_store.computed_preamp_db(settings) == 0.0


def test_validate_sets_preamp_automatically():
    form = _form()
    form["loudness_amount"] = "10"
    # Any submitted preamp is ignored; the stored value is the computed one.
    form["eq_preamp_db"] = "0"
    settings = equalizer_store.validate_settings(form)
    assert settings["preamp_db"] == -equalizer_store.LOUDNESS_LOW_MAX_DB


def test_loudness_round_trips_through_ini(managed):
    form = _form()
    form["loudness_enabled"] = "true"
    form["loudness_amount"] = "7.5"
    saved = equalizer_store.save_equalizer(form)
    loaded = equalizer_store.load_equalizer()
    assert loaded == saved
    assert loaded["loudness_enabled"] is True
    assert loaded["loudness_amount"] == 7.5


def test_loudness_amount_out_of_range_is_rejected():
    form = _form()
    form["loudness_amount"] = "11"
    with pytest.raises(ValueError):
        equalizer_store.validate_settings(form)


def test_write_runtime_volume_tracks_knob_and_preserves_settings(managed, monkeypatch, tmp_path):
    rt = tmp_path / "run" / "equalizer.rt"
    monkeypatch.setenv("RADIO_EQUALIZER_RT", str(rt))
    form = _form()
    form["loudness_amount"] = "6"
    equalizer_store.save_equalizer(form)
    # A full EQ write seeds the file (volume defaults to 100 when absent).
    equalizer_store.write_runtime(equalizer_store.load_equalizer())
    _m, gen1, *values1 = equalizer_store.RUNTIME_STRUCT.unpack(rt.read_bytes())
    assert values1[equalizer_store.RUNTIME_CONTROL_VALUES - 1] == 100.0
    # The volume-only update changes just the volume slot and bumps generation.
    equalizer_store.write_runtime_volume(20.0)
    _m2, gen2, *values2 = equalizer_store.RUNTIME_STRUCT.unpack(rt.read_bytes())
    assert gen2 == gen1 + 1
    assert values2[equalizer_store.RUNTIME_CONTROL_VALUES - 1] == 20.0
    assert values2[equalizer_store.RUNTIME_CONTROL_VALUES - 2] == 6.0  # amount kept
    # A subsequent full EQ write carries the tracked volume over.
    equalizer_store.write_runtime(equalizer_store.load_equalizer())
    _m3, _gen3, *values3 = equalizer_store.RUNTIME_STRUCT.unpack(rt.read_bytes())
    assert values3[equalizer_store.RUNTIME_CONTROL_VALUES - 1] == 20.0


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
    assert values[0] == 0.0  # automatic preamp (no boosts, no loudness -> 0 dB)
    assert values[1] == 1.0  # band 1 enabled
    assert values[2] == float(equalizer_store.FILTER_TYPE_IDS["high_pass"])
    assert values[3] == 80.0  # band 1 frequency
    # A globally disabled EQ collapses to identity so the live path bypasses.
    disabled = equalizer_store.defaults()
    assert (
        equalizer_store.control_values(disabled) == [0.0] * equalizer_store.RUNTIME_CONTROL_VALUES
    )


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
