"""Tests for the Step 1 sound-card selection store (``audio_hardware_store``).

Mirrors ``tests/test_web_sources.py``: a ``managed`` fixture points the managed
config dir at a tmp dir so no real ``/etc`` is touched, and the assertions
iterate the :data:`PROFILES` catalog rather than hard-coding a specific card so
adding a future card needs no test change.
"""

import os
import re
import stat

import pytest

from radio_web import (
    audio_hardware_apply,
    config_store,
    helper,
    helper_protocol,
    validators,
)
from radio_web import audio_hardware_store as store


@pytest.fixture()
def managed(monkeypatch, tmp_path):
    monkeypatch.setattr(config_store, "MANAGED_CONFIG_DIR", str(tmp_path))
    return tmp_path


class TestCatalog:
    def test_default_profile_is_in_catalog(self):
        assert store.DEFAULT_PROFILE in store.PROFILES

    def test_default_profile_uses_always_available_headphones(self):
        default = store.PROFILES[store.DEFAULT_PROFILE]
        assert default.kind == "onboard"
        assert default.audio_param == "on"
        assert default.overlay == ""
        assert default.alsa_card == "Headphones"
        assert default.mpd_device == "default"
        assert default.mpd_mixer == "PCM"
        assert default.knob_mixer == "PCM"
        assert default.mixer_max_percent == 100
        assert default.output_format == "S16_LE"

    def test_catalog_contains_the_supported_first_release(self):
        assert set(store.profile_ids()) == {
            "headphones",
            "iqaudio_dac",
            "generic_pcm510x",
            "generic_max98357a",
            "hifiberry_dac",
            "hifiberry_dacplus_std",
            "hifiberry_dacplus_pro",
            "hifiberry_amp2",
            "hifiberry_amp",
            "allo_boss",
            "audiophonics_i_sabre_q2m",
            "allo_katana",
            "justboom_dac_amp",
            "merus_amp",
        }

    def test_catalog_schema_is_valid(self):
        assert store.catalog_errors() == []

    def test_catalog_is_loaded_from_shared_json(self):
        assert os.path.isfile(store.CATALOG_PATH)
        assert store._load_catalog() == store.PROFILES

    def test_every_i2s_profile_has_stable_card_and_kernel_requirements(self):
        for profile in store.PROFILES.values():
            if profile.kind == "i2s":
                assert profile.alsa_card
                assert profile.required_kernel_symbols

    def test_ess_profiles_use_their_kernel_hardware_mixers(self):
        i_sabre = store.PROFILES["audiophonics_i_sabre_q2m"]
        assert i_sabre.volume_control == "hardware"
        assert i_sabre.alsa_card == "ISabreQ2MDAC"
        assert i_sabre.knob_mixer == "Digital"

        katana = store.PROFILES["allo_katana"]
        assert katana.volume_control == "hardware"
        assert katana.alsa_card == "AlloKatana"
        assert katana.knob_mixer == "Master"

    def test_onboard_profile_uses_stable_card_id(self):
        # analog-audio.md warns to use a stable card id so USB/HDMI renumbering
        # cannot misroute audio.
        headphones = store.PROFILES["headphones"]
        assert headphones.kind == "onboard"
        assert headphones.audio_param == "on"
        assert headphones.overlay == ""
        assert headphones.alsa_card == "Headphones"
        assert headphones.modules == ("snd-bcm2835",)


class TestLoad:
    def test_defaults_when_no_managed_file(self, managed):
        assert store.load_profile() == store.DEFAULT_PROFILE

    def test_unknown_id_in_file_falls_back_to_default(self, managed):
        config_store.atomic_write(
            store.managed_hardware_path(),
            "[audio_hardware]\nprofile = bogus\n",
            config_store.CONFIG_MODE,
        )
        assert store.load_profile() == store.DEFAULT_PROFILE

    def test_missing_key_falls_back_to_default(self, managed):
        config_store.atomic_write(
            store.managed_hardware_path(),
            "[audio_hardware]\n# no profile key\n",
            config_store.CONFIG_MODE,
        )
        assert store.load_profile() == store.DEFAULT_PROFILE


class TestValidate:
    def test_accepts_every_catalog_id(self):
        for profile_id in store.profile_ids():
            assert store.validate_profile(profile_id) == profile_id

    def test_trims_whitespace(self):
        assert store.validate_profile("  headphones  ") == "headphones"

    def test_rejects_unknown_id(self):
        with pytest.raises(ValueError):
            store.validate_profile("bogus")

    def test_rejects_empty(self):
        with pytest.raises(ValueError):
            store.validate_profile("")


class TestAudioProfileValidator:
    """The web-layer validator (validators.validate_audio_profile).

    The plan requires the value be "validated twice" (web layer + helper). This
    covers the web-layer half: a strict, *dynamic* membership check against the
    single PROFILES catalog so it iterates the catalog and needs no change when a
    card is added.
    """

    def test_accepts_every_catalog_id(self):
        for profile_id in store.profile_ids():
            assert validators.validate_audio_profile(profile_id) == profile_id

    def test_trims_whitespace(self):
        assert validators.validate_audio_profile(" headphones ") == "headphones"

    def test_rejects_unknown_id(self):
        with pytest.raises(ValueError):
            validators.validate_audio_profile("bogus")

    def test_rejects_empty(self):
        with pytest.raises(ValueError):
            validators.validate_audio_profile("")

    def test_rejects_none(self):
        with pytest.raises(ValueError):
            validators.validate_audio_profile(None)  # type: ignore[arg-type]

    def test_membership_is_dynamic_against_catalog(self, monkeypatch):
        # Adding a card to PROFILES makes the validator accept it with no
        # change to validators.py (proves the single-extension-point design).
        extra = dict(store.PROFILES)
        extra["fake_card"] = store.PROFILES[store.DEFAULT_PROFILE]
        monkeypatch.setattr(store, "PROFILES", extra)
        assert validators.validate_audio_profile("fake_card") == "fake_card"


class TestSaveRestore:
    def test_save_and_load_round_trip(self, managed):
        store.save_profile("headphones")
        assert store.load_profile() == "headphones"

    def test_save_creates_file_with_0644_and_no_backup_first(self, managed):
        store.save_profile("headphones")
        path = store.managed_hardware_path()
        assert os.path.isfile(path)
        mode = stat.S_IMODE(os.stat(path).st_mode)
        assert mode == config_store.CONFIG_MODE
        assert not os.path.isfile(store.managed_backup_path())

    def test_second_save_keeps_one_backup(self, managed):
        store.save_profile("headphones")
        store.save_profile("iqaudio_dac")
        assert os.path.isfile(store.managed_backup_path())

    def test_invalid_id_writes_nothing(self, managed):
        with pytest.raises(ValueError):
            store.save_profile("bogus")
        assert not os.path.isfile(store.managed_hardware_path())
        assert not os.path.isfile(store.managed_backup_path())

    def test_restore_builtin_removes_files(self, managed):
        store.save_profile("headphones")
        store.save_profile("iqaudio_dac")  # create a .bak too
        assert os.path.isfile(store.managed_hardware_path())
        store.restore_builtin()
        assert not os.path.isfile(store.managed_hardware_path())
        assert not os.path.isfile(store.managed_backup_path())
        assert store.load_profile() == store.DEFAULT_PROFILE

    def test_restore_builtin_idempotent(self, managed):
        store.restore_builtin()  # no files present, must not raise


class TestCatalogConsistency:
    def test_i2s_overlays_are_present_in_shipped_boot_config(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(root, "buildroot", "external", "board", "radio", "config.txt")
        with open(path, encoding="utf-8") as handle:
            config = handle.read()
        for overlay in store.overlay_ids():
            assert re.search(rf"^#dtoverlay={re.escape(overlay)}(?:,|$)", config, re.MULTILINE), (
                f"overlay '{overlay}' missing from shipped boot config"
            )

    def test_kernel_fragment_declares_every_profile_requirement(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        fragment = os.path.join(
            root, "buildroot", "external", "board", "radio", "linux-i2s-audio.fragment"
        )
        text = open(fragment, encoding="utf-8").read()
        required = {
            symbol
            for profile in store.PROFILES.values()
            for symbol in profile.required_kernel_symbols
        }
        for symbol in required:
            assert re.search(rf"^{re.escape(symbol)}=[ym]$", text, re.MULTILINE)

    def test_runtime_and_build_verifier_share_one_catalog_file(self):
        assert store.CATALOG_PATH.endswith("audio_hardware_profiles.json")
        assert store.PROFILES == store._load_catalog(store.CATALOG_PATH)

    def test_build_explicitly_installs_raspberry_pi_overlays(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(root, "buildroot", "external", "configs", "radio_rpi3_defconfig")
        assert (
            "BR2_PACKAGE_RPI_FIRMWARE_INSTALL_DTB_OVERLAYS=y" in open(path, encoding="utf-8").read()
        )


# Real shipped files under test (edited only in-memory / in tmp dirs).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SHIPPED_CONFIG = os.path.join(_REPO_ROOT, "buildroot", "external", "board", "radio", "config.txt")
_SHIPPED_MPD = os.path.join(
    _REPO_ROOT,
    "buildroot",
    "external",
    "board",
    "radio",
    "rootfs-overlay",
    "etc",
    "mpd.conf",
)
_SHIPPED_ASOUND = os.path.join(
    _REPO_ROOT,
    "buildroot",
    "external",
    "board",
    "radio",
    "rootfs-overlay",
    "etc",
    "asound.conf",
)
_SHIPPED_RADIO_CONF = os.path.join(_REPO_ROOT, "radio.conf")
_ROOTFS_OVERLAY = os.path.join(
    _REPO_ROOT, "buildroot", "external", "board", "radio", "rootfs-overlay"
)


class TestEditConfigTxt:
    """Pure config.txt editor: touches only the intended lines, idempotent."""

    def _lines(self, text):
        return [
            line for line in text.splitlines() if "dtparam=audio=" in line or "dtoverlay=" in line
        ]

    def test_iqaudio_selects_overlay_and_audio_off(self):
        cfg = open(_SHIPPED_CONFIG, encoding="utf-8").read()
        out = audio_hardware_apply.edit_config_txt(cfg, store.PROFILES["iqaudio_dac"])
        assert "dtparam=audio=off\n" in out
        assert "dtoverlay=iqaudio-dacplus\n" in out
        assert "#dtoverlay=hifiberry-dacplus\n" in out
        assert "#dtoverlay=merus-amp\n" in out

    def test_overlay_parameters_are_written_and_replaced(self):
        cfg = "#dtoverlay=max98357a,sdmode-pin=4\ndtoverlay=hifiberry-dacplus,slave\n"
        profile = store.PROFILES["generic_max98357a"]
        out = audio_hardware_apply.edit_config_txt(cfg, profile)
        assert "dtoverlay=max98357a,no-sdmode\n" in out
        assert "#dtoverlay=hifiberry-dacplus,slave\n" in out

    def test_duplicate_selected_overlay_is_enabled_only_once(self):
        cfg = "#dtoverlay=max98357a,no-sdmode\n#dtoverlay=max98357a,sdmode-pin=4\n"
        out = audio_hardware_apply.edit_config_txt(cfg, store.PROFILES["generic_max98357a"])
        assert out.count("\ndtoverlay=max98357a,no-sdmode\n") <= 1
        assert sum(line.startswith("dtoverlay=max98357a") for line in out.splitlines()) == 1

    def test_headphones_comments_all_overlays_and_audio_on(self):
        cfg = open(_SHIPPED_CONFIG, encoding="utf-8").read()
        out = audio_hardware_apply.edit_config_txt(cfg, store.PROFILES["headphones"])
        assert "dtparam=audio=on\n" in out
        assert "#dtoverlay=iqaudio-dacplus\n" in out
        # No known DAC overlay is left uncommented.
        for overlay in store.overlay_ids():
            assert f"\ndtoverlay={overlay}\n" not in "\n" + out

    def test_preserves_unrelated_dtoverlay_lines(self):
        # The dwc2 USB-gadget overlay and the pi3-miniuart-bt comment are NOT in
        # the catalog and must survive verbatim.
        cfg = open(_SHIPPED_CONFIG, encoding="utf-8").read()
        for profile in store.PROFILES.values():
            out = audio_hardware_apply.edit_config_txt(cfg, profile)
            assert "dtoverlay=dwc2,dr_mode=peripheral" in out
            assert "# dtoverlay=pi3-miniuart-bt" in out

    def test_idempotent(self):
        cfg = open(_SHIPPED_CONFIG, encoding="utf-8").read()
        for profile in store.PROFILES.values():
            once = audio_hardware_apply.edit_config_txt(cfg, profile)
            twice = audio_hardware_apply.edit_config_txt(once, profile)
            assert once == twice

    def test_inserts_lines_when_missing(self):
        # A config.txt with no audio lines at all still gets both set.
        out = audio_hardware_apply.edit_config_txt("dtparam=spi=on\n", store.PROFILES["headphones"])
        assert "dtparam=audio=on\n" in out
        assert "dtparam=spi=on\n" in out

    def test_touches_only_intended_lines(self):
        # Every line that is NOT a dtparam=audio= or a known DAC dtoverlay= must
        # survive byte-for-byte (the plan: "toggling only intended lines").
        cfg = open(_SHIPPED_CONFIG, encoding="utf-8").read()
        known = set(store.managed_overlay_ids())

        def is_audio_line(line):
            s = line.strip()
            if s.startswith("dtparam=audio="):
                return True
            m = re.match(r"^#?\s*dtoverlay=([A-Za-z0-9_.-]+)(?:,.*)?$", s)
            return bool(m and m.group(1) in known)

        for profile in store.PROFILES.values():
            out = audio_hardware_apply.edit_config_txt(cfg, profile)
            before = [ln for ln in cfg.splitlines() if not is_audio_line(ln)]
            after = [ln for ln in out.splitlines() if not is_audio_line(ln)]
            assert before == after


class TestRenderAsound:
    def test_i2s_profile_declares_high_resolution_output(self):
        profile = store.PROFILES["iqaudio_dac"]
        assert profile.output_format == "S32_LE"
        assert profile.output_rate == 48000

    def test_i2s_hardware_mixer_uses_stable_card(self):
        out = audio_hardware_apply.render_asound(store.PROFILES["iqaudio_dac"])
        assert 'pcm "hw:CARD=IQaudIODAC,DEV=0"' in out
        assert 'slave.pcm "radio_dmix"' in out
        assert "hw:0,0" not in out

    def test_softvol_wraps_stable_card_and_all_sources_default(self):
        out = audio_hardware_apply.render_asound(store.PROFILES["generic_pcm510x"])
        assert "type softvol" in out
        assert 'name "Radio Volume"' in out
        assert 'pcm "hw:CARD=sndrpihifiberry,DEV=0"' in out
        assert 'slave.pcm "radio_softvol"' in out

    def test_onboard_renders_stable_card_id(self):
        out = audio_hardware_apply.render_asound(store.PROFILES["headphones"])
        assert 'slave.pcm "sysdefault:CARD=Headphones"' in out
        assert "card Headphones" in out
        assert "dmix" not in out


class TestRewriteMpd:
    def test_rewrites_only_device_and_mixer(self):
        mpd = open(_SHIPPED_MPD, encoding="utf-8").read()
        out = audio_hardware_apply.rewrite_mpd(mpd, store.PROFILES["headphones"])
        assert 'device      "default"' in out
        assert 'mixer_control "PCM"' in out
        # Untouched lines survive.
        assert 'music_directory     "/var/lib/mpd/music"' in out
        assert 'port                "6600"' in out

    def test_iqaudio_keeps_default_device(self):
        mpd = open(_SHIPPED_MPD, encoding="utf-8").read()
        out = audio_hardware_apply.rewrite_mpd(mpd, store.PROFILES["iqaudio_dac"])
        assert 'device      "default"' in out
        assert 'mixer_control "Digital"' in out

    def test_softvol_mixer_uses_stable_control_card(self):
        mpd = open(_SHIPPED_MPD, encoding="utf-8").read()
        out = audio_hardware_apply.rewrite_mpd(mpd, store.PROFILES["generic_pcm510x"])
        assert 'mixer_device "hw:CARD=sndrpihifiberry"' in out
        assert 'mixer_control "Radio Volume"' in out


class TestRenderModules:
    def test_i2s_has_no_modules(self):
        assert audio_hardware_apply.render_modules(store.PROFILES["iqaudio_dac"]) == ""

    def test_onboard_loads_snd_bcm2835(self):
        out = audio_hardware_apply.render_modules(store.PROFILES["headphones"])
        assert "snd-bcm2835" in out


@pytest.fixture()
def apply_env(monkeypatch, tmp_path):
    """Point every apply target at a tmp dir + a fake boot partition.

    Simulates the mount by pointing BOOT_MOUNT at a tmp dir that already holds
    a fake config.txt, and stubbing mount/umount so no real device is touched.
    """
    etc = tmp_path / "etc"
    etc.mkdir()
    boot = tmp_path / "boot-rw"
    boot.mkdir()
    modules = etc / "modules-load.d"
    modules.mkdir()
    (boot / "config.txt").write_text(
        "dtparam=i2s=on\ndtparam=audio=off\ndtoverlay=iqaudio-dacplus\n"
        "#dtoverlay=hifiberry-dacplus\n#dtoverlay=merus-amp\n"
        "dtoverlay=dwc2,dr_mode=peripheral\n"
    )
    (etc / "mpd.conf").write_text(
        'audio_output {\n    type        "alsa"\n    device      "default"\n'
        '    mixer_control "Digital"\n}\n'
    )
    monkeypatch.setattr(audio_hardware_apply, "BOOT_MOUNT", str(boot))
    monkeypatch.setattr(audio_hardware_apply, "BOOT_DEV", "/dev/fake-boot")
    monkeypatch.setattr(audio_hardware_apply, "ASOUND_CONF", str(etc / "asound.conf"))
    monkeypatch.setattr(audio_hardware_apply, "MPD_CONF", str(etc / "mpd.conf"))
    monkeypatch.setattr(audio_hardware_apply, "MODULES_LOAD_DIR", str(modules))
    monkeypatch.setattr(config_store, "MANAGED_CONFIG_DIR", str(tmp_path / "radio"))
    calls = {"mount": 0, "umount": 0}

    def fake_run(argv):
        if argv[0] == audio_hardware_apply._MOUNT_CMD:
            calls["mount"] += 1
        elif argv[0] == audio_hardware_apply._UMOUNT_CMD:
            calls["umount"] += 1
        return True

    monkeypatch.setattr(audio_hardware_apply, "_run", fake_run)
    return {"etc": etc, "boot": boot, "modules": modules, "calls": calls}


class TestFindBootDev:
    def test_env_override_wins(self, monkeypatch):
        monkeypatch.setattr(audio_hardware_apply, "BOOT_DEV", "/dev/override")
        assert audio_hardware_apply.find_boot_dev() == "/dev/override"


class TestWriteBootConfig:
    def test_backs_up_edits_and_unmounts(self, apply_env):
        ok = audio_hardware_apply.write_boot_config(store.PROFILES["headphones"])
        assert ok is True
        boot = apply_env["boot"]
        edited = (boot / "config.txt").read_text()
        assert "dtparam=audio=on\n" in edited
        assert "#dtoverlay=iqaudio-dacplus\n" in edited
        assert (boot / "config.txt.bak").is_file()
        assert "dtoverlay=iqaudio-dacplus\n" in (boot / "config.txt.bak").read_text()
        assert apply_env["calls"]["mount"] == 1
        assert apply_env["calls"]["umount"] == 1

    def test_unmounts_even_on_missing_config(self, apply_env):
        (apply_env["boot"] / "config.txt").unlink()
        ok = audio_hardware_apply.write_boot_config(store.PROFILES["headphones"])
        assert ok is False
        assert apply_env["calls"]["umount"] == 1  # finally always runs

    def test_no_device_is_non_fatal(self, monkeypatch):
        monkeypatch.setattr(audio_hardware_apply, "find_boot_dev", lambda: None)
        assert audio_hardware_apply.write_boot_config(store.PROFILES["headphones"]) is False


class TestApply:
    def test_full_apply_headphones(self, apply_env):
        from radio_web import audio_store

        ok, message = audio_hardware_apply.apply("headphones")
        assert ok is True
        assert "Reboot" in message
        # (a) config.txt edited
        assert "dtparam=audio=on\n" in (apply_env["boot"] / "config.txt").read_text()
        # (b) asound.conf rendered for the stable card id
        asound = (apply_env["etc"] / "asound.conf").read_text()
        assert "sysdefault:CARD=Headphones" in asound
        # (c) mpd.conf rewritten
        mpd = (apply_env["etc"] / "mpd.conf").read_text()
        assert 'device      "default"' in mpd
        assert 'mixer_control "PCM"' in mpd
        # (d) modules-load.d written
        mod = apply_env["modules"] / audio_hardware_apply.MODULES_LOAD_FILENAME
        assert "snd-bcm2835" in mod.read_text()
        # (e) profile-derived values are not duplicated in audio.ini
        assert audio_store.load_audio() == {"max_volume": "100"}

    def test_full_apply_iqaudio_removes_modules_file(self, apply_env):
        from radio_web import audio_store

        mod = apply_env["modules"] / audio_hardware_apply.MODULES_LOAD_FILENAME
        mod.write_text("snd-bcm2835\n")  # as if headphones was applied earlier
        ok, _msg = audio_hardware_apply.apply("iqaudio_dac")
        assert ok is True
        assert not mod.is_file()  # I2S DAC has no modules; file removed
        asound = (apply_env["etc"] / "asound.conf").read_text()
        assert 'pcm "hw:CARD=IQaudIODAC,DEV=0"' in asound
        assert "hw:0,0" not in asound
        assert audio_store.load_audio() == {"max_volume": "100"}

    def test_apply_does_not_persist_profile_derived_audio_fields(self, apply_env):
        # Mixer and amplifier GPIO are derived from the selected profile.
        from radio_web import audio_store

        for profile_id in store.profile_ids():
            ok, message = audio_hardware_apply.apply(profile_id)
            assert ok is True, f"{profile_id}: {message}"
            assert audio_store.load_audio() == {"max_volume": "100"}

    def test_invalid_profile_writes_nothing(self, apply_env):
        ok, message = audio_hardware_apply.apply("bogus")
        assert ok is False
        assert "Unknown sound card" in message
        assert "dtoverlay=iqaudio-dacplus\n" in (apply_env["boot"] / "config.txt").read_text()
        assert not (apply_env["boot"] / "config.txt.bak").is_file()

    def test_boot_write_failure_is_reported_and_stops(self, apply_env, monkeypatch):
        monkeypatch.setattr(audio_hardware_apply, "write_boot_config", lambda p: False)
        ok, message = audio_hardware_apply.apply("headphones")
        assert ok is False
        assert "boot configuration" in message.lower()
        assert not (apply_env["etc"] / "asound.conf").is_file()

    def test_apply_succeeds_for_every_catalog_profile(self, apply_env):
        # Catalog-iterating: applying any shipped profile succeeds end-to-end and
        # writes an asound.conf, so adding a card needs no new apply test.
        for profile_id in store.profile_ids():
            ok, message = audio_hardware_apply.apply(profile_id)
            assert ok is True, f"{profile_id}: {message}"
            assert "Reboot" in message
            assert (apply_env["etc"] / "asound.conf").is_file()


class TestHelperDispatch:
    def test_dispatch_matches_whitelist(self):
        assert set(helper._OPERATIONS) == set(helper_protocol.ACTION_IDS)
        assert "set_audio_hardware" in helper_protocol.ACTION_IDS

    def test_helper_revalidates_bad_profile(self):
        ok, message = helper.dispatch("set_audio_hardware", {"profile": "bogus"})
        assert ok is False
        assert "Unknown sound card" in message

    def test_helper_rejects_non_string_profile(self):
        ok, _msg = helper.dispatch("set_audio_hardware", {"profile": 5})
        assert ok is False

    def test_helper_forwards_valid_profile(self, monkeypatch):
        seen = {}

        def fake_apply(profile_id):
            seen["id"] = profile_id
            return True, "Sound card changed. Reboot to apply."

        monkeypatch.setattr(helper.audio_hardware_apply, "apply", fake_apply)
        ok, message = helper.dispatch("set_audio_hardware", {"profile": "headphones"})
        assert ok is True
        assert seen["id"] == "headphones"
        assert "Reboot" in message

    def test_apply_re_validates_before_touching_files(self, apply_env):
        # "Validated twice": even reaching apply() directly with a bad id (as if
        # the web layer were bypassed) must reject it BEFORE any file is written.
        ok, message = audio_hardware_apply.apply("bogus")
        assert ok is False
        assert "Unknown sound card" in message
        assert not (apply_env["etc"] / "asound.conf").is_file()
        assert not (apply_env["boot"] / "config.txt.bak").is_file()
        assert apply_env["calls"]["mount"] == 0  # never even mounted the boot FS


def _config_audio_lines(text):
    """Return the audio-relevant config.txt lines (dtparam=audio / DAC overlay)."""
    known = set(store.managed_overlay_ids())
    lines = []
    for raw in text.splitlines():
        s = raw.strip()
        if s.startswith("dtparam=audio="):
            lines.append(s)
        else:
            m = re.match(r"^#?\s*dtoverlay=([A-Za-z0-9_.-]+)(?:,.*)?$", s)
            if m and m.group(1) in known:
                lines.append(s)
    return lines


class TestShippedHeadphoneDefault:
    """A fresh image consistently uses the built-in headphone profile."""

    def test_default_profile_matches_shipped_radio_conf(self):
        # radio.conf [audio] mixer / [gpio] amp must match the default profile.
        text = open(_SHIPPED_RADIO_CONF, encoding="utf-8").read()
        default = store.PROFILES[store.DEFAULT_PROFILE]
        assert re.search(r"^\s*mixer\s*=\s*PCM\s*$", text, re.MULTILINE)
        assert default.knob_mixer == "PCM"
        assert re.search(r"^\s*amp\s*=\s*none\s*$", text, re.MULTILINE)
        assert default.amp_gpio is None

    def test_default_profile_matches_shipped_mpd_conf(self):
        text = open(_SHIPPED_MPD, encoding="utf-8").read()
        default = store.PROFILES[store.DEFAULT_PROFILE]
        assert f'device      "{default.mpd_device}"' in text
        assert f'mixer_control "{default.mpd_mixer}"' in text

    def test_default_edit_config_txt_is_a_noop_on_shipped_config(self):
        # Re-rendering the shipped config.txt with the default profile must leave
        # the audio lines exactly as shipped (idempotent, no drift).
        shipped = open(_SHIPPED_CONFIG, encoding="utf-8").read()
        rendered = audio_hardware_apply.edit_config_txt(
            shipped, store.PROFILES[store.DEFAULT_PROFILE]
        )
        assert _config_audio_lines(rendered) == _config_audio_lines(shipped)

    def test_default_render_asound_matches_shipped(self):
        # The default routes to the stable built-in headphone card id.
        shipped = open(_SHIPPED_ASOUND, encoding="utf-8").read()
        rendered = audio_hardware_apply.render_asound(store.PROFILES[store.DEFAULT_PROFILE])
        for needle in ("sysdefault:CARD=Headphones", "card Headphones", "pcm.!default"):
            assert needle in shipped
            assert needle in rendered
        assert "sysdefault:CARD=Headphones" in rendered

    def test_default_loads_onboard_audio_module(self):
        assert "snd-bcm2835" in audio_hardware_apply.render_modules(
            store.PROFILES[store.DEFAULT_PROFILE]
        )

    def test_no_audio_hardware_ini_shipped(self):
        # No managed override is baked into the image, so load_profile() falls
        # back to the default on a fresh boot.
        for _root, _dirs, files in os.walk(_ROOTFS_OVERLAY):
            assert "audio_hardware.ini" not in files
        modules = os.path.join(_ROOTFS_OVERLAY, "etc", "modules-load.d", "radio-audio.conf")
        assert open(modules, encoding="utf-8").read().strip() == "snd-bcm2835"

    def test_missing_file_resolves_to_default(self, managed):
        # managed fixture points at an empty tmp dir (no audio_hardware.ini).
        assert not os.path.exists(store.managed_hardware_path())
        assert store.load_profile() == "headphones"
