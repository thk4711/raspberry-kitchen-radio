"""Buildroot integration checks for the project LADSPA equalizer."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXTERNAL = ROOT / "buildroot" / "external"
PACKAGE = EXTERNAL / "package" / "radio-equalizer"
GUIDE = ROOT / "doc" / "equalizer.md"


def test_equalizer_package_is_selected_and_registered():
    assert 'source "$BR2_EXTERNAL_RADIO_PATH/package/radio-equalizer/Config.in"' in (
        EXTERNAL / "Config.in"
    ).read_text(encoding="utf-8")
    assert "BR2_PACKAGE_RADIO_EQUALIZER=y" in (
        EXTERNAL / "configs" / "radio_rpi3_defconfig"
    ).read_text(encoding="utf-8")


def test_equalizer_package_builds_and_installs_ladspa_module():
    makefile = (PACKAGE / "radio-equalizer.mk").read_text(encoding="utf-8")
    source = (PACKAGE / "radio_equalizer.c").read_text(encoding="utf-8")
    assert "$(TARGET_CC)" in makefile
    assert "$(TARGET_DIR)/usr/lib/ladspa/radio_equalizer.so" in makefile
    assert "ladspa_descriptor" in source
    assert '"radio_equalizer"' in source


def test_equalizer_user_and_developer_guide_covers_contract():
    text = GUIDE.read_text(encoding="utf-8")
    for required in (
        "## Using the equalizer",
        "### Save, apply and reset",
        "## Persistence and firmware updates",
        "/data/radio/equalizer.ini",
        "## Troubleshooting and on-device checks",
        "## Developer architecture",
        "radio_equalizer.so",
        "policy duplicate",
        "FLOAT/non-interleaved",
        "tests/test_equalizer.py",
    ):
        assert required in text
