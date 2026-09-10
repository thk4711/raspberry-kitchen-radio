from radio_web import config_store, usb_audio_store


def test_default_is_uac1_when_no_override(monkeypatch, tmp_path):
    monkeypatch.setattr(config_store, "MANAGED_CONFIG_DIR", str(tmp_path))
    assert usb_audio_store.load_mode() == "uac1"


def test_save_and_load_uac2(monkeypatch, tmp_path):
    monkeypatch.setattr(config_store, "MANAGED_CONFIG_DIR", str(tmp_path))
    assert usb_audio_store.save_mode("uac2") == "uac2"
    assert usb_audio_store.load_mode() == "uac2"


def test_invalid_mode_is_rejected(monkeypatch, tmp_path):
    monkeypatch.setattr(config_store, "MANAGED_CONFIG_DIR", str(tmp_path))
    try:
        usb_audio_store.save_mode("uac3")
    except ValueError as exc:
        assert "UAC1 or UAC2" in str(exc)
    else:
        raise AssertionError("invalid USB Audio mode was accepted")
