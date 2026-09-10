"""ADC managed calibration and live snapshot tests."""

import json
import os
import stat
import time

import pytest

from radio_web import adc_store, config_store


@pytest.fixture()
def managed(monkeypatch, tmp_path):
    monkeypatch.setattr(config_store, "MANAGED_CONFIG_DIR", str(tmp_path))
    return tmp_path


def test_defaults(managed):
    assert adc_store.load_adc() == adc_store.DEFAULTS


def test_save_roundtrip_and_mode(managed):
    values = dict(adc_store.DEFAULTS)
    values["switch_threshold"] = "420.5"
    adc_store.save_adc(values)
    assert adc_store.load_adc()["switch_threshold"] == "420.5"
    assert stat.S_IMODE(os.stat(adc_store.managed_adc_path()).st_mode) == 0o644
    assert "[adc]" in open(adc_store.managed_adc_path()).read()


def test_rejects_reversed_volume_without_writing(managed):
    values = dict(adc_store.DEFAULTS)
    values["volume_min_input"] = "2000"
    values["volume_max_input"] = "1000"
    with pytest.raises(ValueError):
        adc_store.save_adc(values)
    assert not os.path.exists(adc_store.managed_adc_path())


def test_rejects_overlapping_button_bands(managed):
    values = dict(adc_store.DEFAULTS)
    values["button_tolerance"] = "300"
    with pytest.raises(ValueError, match="do not overlap"):
        adc_store.validate_settings(values)


def test_live_snapshot(monkeypatch, tmp_path):
    path = tmp_path / "adc.json"
    path.write_text(json.dumps({"updated_at": time.time(), "channels": {"0": {"raw_mv": 12}}}))
    monkeypatch.setattr(adc_store, "ADC_STATUS_FILE", str(path))
    result = adc_store.live_snapshot()
    assert result["available"] is True
    assert result["stale"] is False


def test_missing_live_snapshot(monkeypatch, tmp_path):
    monkeypatch.setattr(adc_store, "ADC_STATUS_FILE", str(tmp_path / "missing"))
    assert adc_store.live_snapshot()["available"] is False
