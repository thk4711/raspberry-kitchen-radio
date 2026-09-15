"""Tests for completed-Buildroot audio catalog verification."""

import importlib.util
from pathlib import Path

from radio_web import audio_hardware_store as store

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verify-audio-catalog.py"
SOUND_DEVICE_DOC = ROOT / "doc" / "sound-devices.md"
HARDWARE_DOC = ROOT / "doc" / "hardware.md"


def _module():
    spec = importlib.util.spec_from_file_location("verify_audio_catalog", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_completed_build_with_catalog_overlays_and_symbols_passes(tmp_path):
    overlays = tmp_path / "images" / "rpi-firmware" / "overlays"
    overlays.mkdir(parents=True)
    for overlay in store.overlay_ids():
        (overlays / f"{overlay}.dtbo").touch()
    kernel = tmp_path / "build" / "linux-test"
    kernel.mkdir(parents=True)
    symbols = {
        symbol for profile in store.PROFILES.values() for symbol in profile.required_kernel_symbols
    }
    (kernel / ".config").write_text(
        "".join(f"{symbol}=m\n" for symbol in sorted(symbols)), encoding="utf-8"
    )
    assert _module().verify(tmp_path) == []


def test_missing_overlay_and_symbol_are_reported(tmp_path):
    (tmp_path / "images" / "rpi-firmware" / "overlays").mkdir(parents=True)
    kernel = tmp_path / "build" / "linux-test"
    kernel.mkdir(parents=True)
    (kernel / ".config").write_text("", encoding="utf-8")
    errors = _module().verify(tmp_path)
    assert any("missing overlay" in error for error in errors)
    assert any("kernel symbol is not enabled" in error for error in errors)


def test_every_audio_profile_has_one_documented_pinout_table():
    """Keep the human pinouts in step with the selectable profile catalog."""
    text = SOUND_DEVICE_DOC.read_text(encoding="utf-8")
    markers = [f"<!-- audio-profile: {profile_id} -->" for profile_id in store.PROFILES]

    for marker in markers:
        assert text.count(marker) == 1, f"missing or duplicate documentation marker: {marker}"

    positions = [text.index(marker) for marker in markers]
    assert positions == sorted(positions), "sound-device sections must follow catalog order"

    for index, profile in enumerate(store.PROFILES.values()):
        start = positions[index]
        end = (
            positions[index + 1]
            if index + 1 < len(positions)
            else text.index("## Stable routing", start)
        )
        section = text[start:end]
        assert f"### {profile.label}" in section
        assert (
            section.count(
                "| BCM | Signal / radio connection | Physical pin (odd) | "
                "Physical pin (even) | Signal / radio connection | BCM |"
            )
            == 1
        )
        for odd in range(1, 40, 2):
            assert f"**{odd}** | **{odd + 1}**" in section
        assert any(marker in section for marker in ("🟥", "🟧", "⬛"))
        assert profile.alsa_card in section
        assert profile.knob_mixer in section


def test_generic_hardware_doc_does_not_duplicate_complete_header_map():
    text = HARDWARE_DOC.read_text(encoding="utf-8")
    assert "Complete 40-pin header map" not in text
    assert "Physical pin (odd)" not in text
