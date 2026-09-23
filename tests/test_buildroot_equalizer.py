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


def test_loudness_controls_are_in_c_plugin_and_match_python_contract():
    """The C plugin and equalizer_store must agree on the runtime layout.

    Loudness added three control values and bumped the magic; if the C side and
    the Python writer drift, the plugin silently falls back to stale values, so
    guard the count and magic together here.
    """
    from radio_web import equalizer_store

    source = (PACKAGE / "radio_equalizer.c").read_text(encoding="utf-8")
    # The plugin implements the volume-tracked loudness shelves.
    assert "Loudness Enabled" in source
    assert "Loudness Amount" in source
    assert "Loudness Volume" in source
    assert "loudness_coefficients" in source
    # Magic must match RUNTIME_MAGIC (0x52454132, "REA2").
    assert "0x52454132u" in source
    assert equalizer_store.RUNTIME_MAGIC == 0x52454132
    # Three loudness controls appended after preamp + 10 bands x 5 fields.
    assert equalizer_store.RUNTIME_CONTROL_VALUES == 1 + 10 * 5 + 3
    assert "LOUDNESS_VALUES = 3" in source
