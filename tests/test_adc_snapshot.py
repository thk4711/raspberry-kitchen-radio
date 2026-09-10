import json
import stat

import adc_snapshot


def test_write_adc_snapshot(tmp_path):
    path = tmp_path / "adc.json"
    adc_snapshot.write_adc_snapshot(str(path), {"channels": {"0": {"raw_mv": 42}}})
    data = json.loads(path.read_text())
    assert data["channels"]["0"]["raw_mv"] == 42
    assert "updated_at" in data
    assert stat.S_IMODE(path.stat().st_mode) == 0o644


def test_resolve_adc_status_file(tmp_path):
    path = tmp_path / "adc.json"
    assert adc_snapshot.resolve_adc_status_file({}, str(path)) == str(path)
    assert adc_snapshot.resolve_adc_status_file({"adc_status": {"enabled": False}}, None) == ""
