"""Privileged sound-card apply: config.txt + ALSA + MPD + modules + mixer.

Runs **only inside the root helper** (:mod:`radio_web.helper`), mirroring
:mod:`radio_web.network_apply` and the ``device_store`` privileged writers.
Given a re-validated profile *id* from :data:`radio_web.audio_hardware_store`,
it applies the whole card selection:

  (a) mounts the FAT boot partition read-write, backs up ``config.txt.bak``,
      edits **only** the ``dtparam=audio=`` / ``dtoverlay=`` lines, then unmounts
      (always, in a ``finally``);
  (b) writes ``/etc/asound.conf`` using stable ALSA card ids and an optional
      shared ``softvol`` layer for devices without hardware volume;
  (c) rewrites the MPD ``audio_output`` ``device`` / ``mixer_control``;
  (d) writes ``/etc/modules-load.d`` (empty for I2S DACs);
  (e) leaves profile-derived mixer and amplifier values in the profile catalog;
      ``audio.ini`` stores only the user-selected maximum-volume cap.

Every renderer is **catalog-generic** over the :class:`AudioProfile` fields and
never branches on a specific card id — adding a card is one catalog row. The
change is **reboot-level** (a device-tree overlay is resolved by the firmware at
boot); there is no live overlay reload. The riskiest piece is writing the FAT
boot partition: it mounts RW only briefly, backs up first, edits only known
lines, always unmounts, and is non-fatal on error — the SD-card
``pisonic-config.txt`` remains the recovery route.

Standard-library only. Every path and the boot block device are env-overridable
so host tests never touch the real ``/etc`` or a real device.
"""

import logging
import os
import re
import subprocess
from typing import Optional, Tuple

from . import audio_hardware_store, equalizer_store, validators

logger = logging.getLogger("radio_web.audio_hardware_apply")

# System files/paths the helper owns. Overridable so tests never touch the real
# /etc, /mnt or a real block device.
ASOUND_CONF = os.environ.get("RADIO_ASOUND_CONF", "/etc/asound.conf")
MPD_CONF = os.environ.get("RADIO_MPD_CONF", "/etc/mpd.conf")
MODULES_LOAD_DIR = os.environ.get("RADIO_MODULES_LOAD_DIR", "/etc/modules-load.d")
# The modules-load.d file this feature owns (rewritten every apply).
MODULES_LOAD_FILENAME = "radio-audio.conf"
USB_OUTPUT_FILENAME = "usb_audio_output.ini"

# The boot-partition mount lifecycle. BOOT_MOUNT is created in post-build.sh
# (like /mnt/boot). BOOT_DEV, when set, short-circuits find_boot_dev() so tests
# never probe /proc/cmdline or a real device.
BOOT_MOUNT = os.environ.get("RADIO_BOOT_MOUNT", "/mnt/boot-rw")
BOOT_CONFIG_NAME = os.environ.get("RADIO_BOOT_CONFIG_NAME", "config.txt")
BOOT_DEV = os.environ.get("RADIO_BOOT_DEV", "")
_MOUNT_CMD = os.environ.get("RADIO_MOUNT_CMD", "/bin/mount")
_UMOUNT_CMD = os.environ.get("RADIO_UMOUNT_CMD", "/bin/umount")

_CMD_TIMEOUT_SECONDS = 30.0


def find_boot_dev() -> Optional[str]:
    """Return the FAT boot-partition device node, or ``None``.

    Mirrors ``provision-from-boot``'s ``find_boot_dev``: prefer deriving it from
    the running root device (``root=/dev/mmcblk0p2`` -> ``p1``), fall back to a
    generic "last digit is the partition number" rule, then the common Pi node
    ``/dev/mmcblk0p1``, then a vfat-labelled ``boot`` via ``blkid``. The
    :data:`BOOT_DEV` env override short-circuits everything (used by tests).
    """
    if BOOT_DEV:
        return BOOT_DEV
    root_dev = ""
    try:
        with open("/proc/cmdline", encoding="utf-8") as handle:
            cmdline = handle.read()
        match = re.search(r"root=(\S+)", cmdline)
        if match:
            root_dev = match.group(1)
    except OSError:
        root_dev = ""
    candidates = []
    if re.match(r"^/dev/mmcblk\d+p2$", root_dev):
        candidates.append(re.sub(r"p2$", "p1", root_dev))
    elif re.match(r"^/dev/.*\d$", root_dev):
        candidates.append(re.sub(r"\d$", "1", root_dev))
    candidates.append("/dev/mmcblk0p1")
    for candidate in candidates:
        try:
            if os.path.exists(candidate):
                return candidate
        except OSError:
            continue
    # Last resort: a vfat partition labelled "boot".
    try:
        out = subprocess.run(  # noqa: S603 - fixed argv, shell=False
            ["blkid", "-t", "LABEL=boot", "-o", "device"],
            capture_output=True,
            text=True,
            timeout=_CMD_TIMEOUT_SECONDS,
            check=False,
        ).stdout
        device = out.strip().splitlines()[0].strip() if out.strip() else ""
        if device and os.path.exists(device):
            return device
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def edit_config_txt(text: str, profile: audio_hardware_store.AudioProfile) -> str:
    """Return ``text`` with the audio overlay/param lines set for ``profile``.

    Catalog-driven and idempotent, generalising the proven ``post-image.sh``
    sed pattern: comment out **every** known ``dtoverlay=<x>`` for ``x`` in the
    catalog's overlays, then uncomment/insert the selected one (nothing to add
    for on-board audio, whose ``overlay`` is empty), and set
    ``dtparam=audio=<audio_param>``. Only these lines are touched; every other
    line is preserved verbatim. Pure function, unit-testable without a device.
    """
    known_overlays = set(audio_hardware_store.managed_overlay_ids())
    selected = profile.overlay
    selected_setting = profile.overlay_setting
    out = []
    saw_audio_param = False
    saw_selected_overlay = False
    for line in text.splitlines(keepends=True):
        eol = "\n" if line.endswith("\n") else ""
        body = line[: -len(eol)] if eol else line
        stripped = body.strip()
        # dtparam=audio=<on|off> — set to the profile's value.
        if re.match(r"^#?\s*dtparam=audio=", stripped):
            out.append(f"dtparam=audio={profile.audio_param}\n")
            saw_audio_param = True
            continue
        # dtoverlay=<known-card> — comment out, or uncomment if it is selected.
        overlay_match = re.match(r"^#?\s*dtoverlay=([A-Za-z0-9_.-]+)(?:,.*)?$", stripped)
        if overlay_match and overlay_match.group(1) in known_overlays:
            name = overlay_match.group(1)
            if selected and name == selected and not saw_selected_overlay:
                out.append(f"dtoverlay={selected_setting}\n")
                saw_selected_overlay = True
            else:
                out.append(f"#dtoverlay={body.lstrip('#').strip()[len('dtoverlay=') :]}\n")
            continue
        out.append(line)
    # Ensure the selected overlay is present even if config.txt lacked its line.
    if selected and not saw_selected_overlay:
        if out and not out[-1].endswith("\n"):
            out[-1] = out[-1] + "\n"
        out.append(f"dtoverlay={selected_setting}\n")
    # Ensure a dtparam=audio= line exists even if config.txt lacked one.
    if not saw_audio_param:
        if out and not out[-1].endswith("\n"):
            out[-1] = out[-1] + "\n"
        out.append(f"dtparam=audio={profile.audio_param}\n")
    return "".join(out)


def _equalizer_controls(settings: equalizer_store.EqualizerSettings) -> str:
    values = [settings["preamp_db"]]
    for band in settings["bands"]:
        values.extend(
            (
                1 if band["enabled"] else 0,
                equalizer_store.FILTER_TYPE_IDS[band["type"]],
                band["frequency"],
                band["gain_db"],
                band["q"],
            )
        )
    # Loudness controls (enabled, amount, current volume). The static asound.conf
    # is only the fallback used before the live runtime file exists, so seed the
    # volume at full (100 -> no boost); the ADC loop then tapers it live.
    values.extend(
        (
            1 if settings["loudness_enabled"] else 0,
            settings["loudness_amount"],
            100,
        )
    )
    return "\n".join(f"                    {index} {value:g}" for index, value in enumerate(values))


def _with_equalizer(base: str, settings: equalizer_store.EqualizerSettings) -> str:
    """Wrap the rendered default PCM in the project LADSPA equalizer."""
    if not settings["enabled"]:
        return base
    output = base.replace("pcm.!default {", "pcm.radio_output {", 1)
    return output + (
        "\n\n# User-configured ten-band parametric equalizer.\n"
        "pcm.radio_equalizer {\n"
        "    type ladspa\n"
        "    channels 2\n"
        '    path "/usr/lib/ladspa"\n'
        '    slave.pcm "radio_output"\n'
        "    playback_plugins {\n"
        "        0 {\n"
        '            label "radio_equalizer"\n'
        "            policy duplicate\n"
        "            input.controls {\n"
        f"{_equalizer_controls(settings)}\n"
        "            }\n"
        "        }\n"
        "    }\n"
        "}\n\n"
        "pcm.!default {\n"
        "    type plug\n"
        '    slave.pcm "radio_equalizer"\n'
        "}\n"
    )


def render_asound(
    profile: audio_hardware_store.AudioProfile,
    equalizer: Optional[equalizer_store.EqualizerSettings] = None,
) -> str:
    """Render stable-card ALSA routing, with optional shared softvol."""
    card = profile.alsa_card
    if not card:
        raise ValueError("Audio profile has no stable ALSA card id")
    if profile.volume_control == "softvol":
        base = (
            "# PiSonic — shared software volume on a stable card.\n"
            "# All sources use pcm.!default, so the physical knob controls MPD,\n"
            "# AirPlay, Spotify, Bluetooth and USB Audio consistently.\n"
            "pcm.radio_dmix {\n"
            "    type dmix\n"
            "    ipc_key 1024\n"
            "    ipc_gid audio\n"
            "    ipc_perm 0660\n"
            "    slave {\n"
            f'        pcm "hw:CARD={card},DEV=0"\n'
            f"        rate {profile.output_rate}\n"
            f"        format {profile.output_format}\n"
            "        period_time 0\n"
            "        period_size 1024\n"
            "        buffer_size 4096\n"
            "    }\n"
            "}\n\n"
            "pcm.radio_softvol {\n"
            "    type softvol\n"
            '    slave.pcm "radio_dmix"\n'
            "    control {\n"
            '        name "Radio Volume"\n'
            f"        card {card}\n"
            "    }\n"
            "    min_dB -60.0\n"
            "    max_dB 0.0\n"
            "    resolution 101\n"
            "}\n\n"
            "pcm.!default {\n"
            "    type plug\n"
            '    slave.pcm "radio_softvol"\n'
            "}\n\n"
            "ctl.!default {\n"
            "    type hw\n"
            f"    card {card}\n"
            "}\n"
        )
        return _with_equalizer(base, equalizer or equalizer_store.defaults())
    if profile.kind == "i2s":
        base = (
            "# PiSonic — shared hardware-volume I2S output.\n"
            "# The PCM route and control route both use a stable ALSA card id.\n"
            "pcm.radio_dmix {\n"
            "    type dmix\n"
            "    ipc_key 1024\n"
            "    ipc_gid audio\n"
            "    ipc_perm 0660\n"
            "    slave {\n"
            f'        pcm "hw:CARD={card},DEV=0"\n'
            f"        rate {profile.output_rate}\n"
            f"        format {profile.output_format}\n"
            "        period_time 0\n"
            "        period_size 1024\n"
            "        buffer_size 4096\n"
            "    }\n"
            "}\n\n"
            "pcm.!default {\n"
            "    type plug\n"
            '    slave.pcm "radio_dmix"\n'
            "}\n\n"
            "ctl.!default {\n"
            "    type hw\n"
            f"    card {card}\n"
            "}\n"
        )
        return _with_equalizer(base, equalizer or equalizer_store.defaults())
    # The ALSA LADSPA PCM requires FLOAT/non-interleaved samples. Its downstream
    # plug can negotiate that against a raw stable-card PCM, but not against the
    # onboard ``sysdefault`` alias (which is itself another plug layer): that
    # plug->plug nesting yields an empty hw-parameter interval on alsa-lib 1.2.15.
    # Keep sysdefault for the normal bypass route and use stable raw hw only while
    # the EQ wrapper is active.
    onboard_pcm = (
        f"hw:CARD={card},DEV=0"
        if equalizer is not None and equalizer["enabled"]
        else f"sysdefault:CARD={card}"
    )
    base = (
        "# PiSonic — audio via a stable card id.\n"
        "# Written by radio-web (privileged helper). A stable CARD= id keeps\n"
        "# USB-gadget/HDMI card renumbering from misrouting audio.\n"
        "pcm.!default {\n"
        "    type plug\n"
        f'    slave.pcm "{onboard_pcm}"\n'
        "}\n"
        "\n"
        "ctl.!default {\n"
        "    type hw\n"
        f"    card {card}\n"
        "}\n"
    )
    return _with_equalizer(base, equalizer or equalizer_store.defaults())


def rewrite_mpd(text: str, profile: audio_hardware_store.AudioProfile) -> str:
    """Return ``text`` with MPD device and mixer fields for ``profile``."""
    out = []
    depth = 0
    in_output = False
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith("audio_output") and "{" in stripped:
            in_output = True
            depth = stripped.count("{") - stripped.count("}")
            out.append(line)
            continue
        if in_output:
            if re.match(r'^\s*device\s+"', line):
                out.append('    device      "default"\n')
            elif re.match(r'^\s*mixer_device\s+"', line):
                out.append(f'    mixer_device "hw:CARD={profile.alsa_card}"\n')
            elif re.match(r'^\s*mixer_type\s+"', line):
                out.append('    mixer_type  "hardware"\n')
            elif re.match(r'^\s*mixer_control\s+"', line):
                out.append(f'    mixer_control "{profile.mpd_mixer}"\n')
            else:
                out.append(line)
            depth += stripped.count("{") - stripped.count("}")
            if depth <= 0:
                in_output = False
            continue
        out.append(line)
    return "".join(out)


def render_modules(profile: audio_hardware_store.AudioProfile) -> str:
    """Render the ``modules-load.d`` file body for ``profile`` (may be empty)."""
    if not profile.modules:
        return ""
    header = "# Written by radio-web (privileged helper): modules for the sound card.\n"
    return header + "".join(f"{module}\n" for module in profile.modules)


def render_usb_output(profile: audio_hardware_store.AudioProfile) -> str:
    """Render the output policy consumed by the USB bridge."""
    return (
        "# Generated from the selected audio hardware profile.\n"
        "[audio_output]\n"
        f"format = {profile.output_format}\n"
        f"rate = {profile.output_rate}\n"
    )


def _atomic_write(path: str, data: str, mode: int = 0o644) -> None:
    """Durable, never-partial write (same-dir temp + fsync + replace)."""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        return ""


def _run(argv: list) -> bool:
    """Run a fixed ``argv`` (``shell=False``); return True on exit 0."""
    try:
        result = subprocess.run(  # noqa: S603 - fixed argv, shell=False
            argv,
            capture_output=True,
            text=True,
            timeout=_CMD_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.error("%s: %s", argv[0], exc)
        return False
    if result.returncode != 0:
        logger.error("%s: exit %s: %s", argv[0], result.returncode, result.stderr.strip())
        return False
    return True


def write_boot_config(profile: audio_hardware_store.AudioProfile) -> bool:
    """Mount the FAT boot partition RW, edit ``config.txt``, always unmount.

    The riskiest piece: it backs up ``config.txt.bak`` first, edits only the
    known ``dtparam=audio=`` / ``dtoverlay=`` lines via :func:`edit_config_txt`,
    and **always** unmounts in a ``finally``. Non-fatal: returns ``False`` (and
    logs) on any failure rather than raising, so the caller can still report a
    clear message and the SD-card ``pisonic-config.txt`` recovery route stays.
    """
    device = find_boot_dev()
    if not device:
        logger.error("write_boot_config: could not locate the FAT boot partition")
        return False
    os.makedirs(BOOT_MOUNT, exist_ok=True)
    mounted = False
    try:
        if not _run([_MOUNT_CMD, "-t", "vfat", "-o", "rw", device, BOOT_MOUNT]):
            logger.error("write_boot_config: mount %s failed", device)
            return False
        mounted = True
        config_path = os.path.join(BOOT_MOUNT, BOOT_CONFIG_NAME)
        current = _read(config_path)
        if not current:
            logger.error("write_boot_config: %s missing or empty", config_path)
            return False
        # Back up before editing (single .bak, overwritten each time).
        _atomic_write(config_path + ".bak", current, 0o644)
        _atomic_write(config_path, edit_config_txt(current, profile), 0o644)
        logger.info("write_boot_config: updated %s", config_path)
        return True
    except OSError as exc:
        logger.error("write_boot_config: %s", exc)
        return False
    finally:
        if mounted:
            if not _run([_UMOUNT_CMD, BOOT_MOUNT]):
                logger.error("write_boot_config: umount %s failed", BOOT_MOUNT)


def _write_alsa_mpd_modules(
    profile: audio_hardware_store.AudioProfile, *, mpd_template: Optional[os.PathLike] = None
) -> None:
    """Write asound.conf, rewrite mpd.conf, and refresh modules-load.d."""
    _atomic_write(ASOUND_CONF, render_asound(profile, equalizer_store.load_equalizer()), 0o644)
    mpd_source = os.fspath(mpd_template) if mpd_template is not None else MPD_CONF
    mpd_text = _read(mpd_source)
    if mpd_text:
        _atomic_write(MPD_CONF, rewrite_mpd(mpd_text, profile), 0o644)
    else:
        logger.warning("apply: %s missing; MPD output not rewritten", MPD_CONF)
    modules_path = os.path.join(MODULES_LOAD_DIR, MODULES_LOAD_FILENAME)
    _atomic_write(
        os.path.join(os.path.dirname(ASOUND_CONF), USB_OUTPUT_FILENAME),
        render_usb_output(profile),
        0o644,
    )
    body = render_modules(profile)
    if body:
        _atomic_write(modules_path, body, 0o644)
    else:
        # No modules for this card (I2S DACs): remove any file we own.
        try:
            os.unlink(modules_path)
        except FileNotFoundError:
            pass


def apply(profile_id: str) -> Tuple[bool, str]:
    """Apply the sound-card ``profile_id`` (re-validated here). Reboot to take effect.

    Server-side re-validation (the helper trusts nothing), then the catalog-
    driven edits: boot ``config.txt`` (mount/back-up/edit/unmount), ALSA/MPD/
    modules. Returns ``(ok, message)``.
    There is no live overlay reload — a device-tree change is resolved at boot.
    """
    try:
        clean_id = validators.validate_audio_profile(profile_id)
    except ValueError as exc:
        return False, str(exc)
    profile = audio_hardware_store.PROFILES[clean_id]
    if not write_boot_config(profile):
        return False, (
            "Could not update the boot configuration. The sound card was not "
            "changed. Use the SD-card pisonic-config.txt as a fallback."
        )
    try:
        _write_alsa_mpd_modules(profile)
    except OSError as exc:
        logger.error("apply: %s", exc)
        return False, "The boot configuration changed but ALSA/MPD could not be updated."
    logger.info("apply: sound card set to %s", clean_id)
    return True, "Sound card changed. Reboot to apply."


def _asound_has_equalizer_stage() -> bool:
    """Return whether the currently rendered asound.conf inserts the EQ stage.

    If asound.conf is missing or unreadable this returns ``False``, which makes
    an enabled EQ look like a stage-presence change and take the restart path.
    That is the safe direction: it rebuilds the route rather than silently
    assuming a stage that may not be there.
    """
    text = _read(ASOUND_CONF)
    return bool(text) and "pcm.radio_equalizer {" in text


def apply_equalizer() -> Tuple[bool, str]:
    """Apply persistent EQ settings, live where possible.

    The LADSPA parameters (preamp and every band) are pushed to the runtime file
    the plugin re-reads without a stream restart, so ordinary tweaks are live.
    The ALSA route is only rewritten when the *stage presence* changes (the EQ is
    toggled on<->off relative to what asound.conf currently renders), because
    inserting or removing the ladspa PCM is an open-time change on alsa-lib
    1.2.15. Returns ``(ok, message)``; ``message`` starts with ``"restart:"`` when
    the caller must restart audio consumers, or ``"live:"`` when it must not.
    """
    profile = audio_hardware_store.PROFILES[audio_hardware_store.load_profile()]
    settings = equalizer_store.load_equalizer()
    structural = settings["enabled"] != _asound_has_equalizer_stage()
    try:
        # Always refresh the live runtime values first so a following restart (or
        # a live-reading plugin) immediately sees the new parameters.
        equalizer_store.write_runtime(settings)
        if structural:
            _atomic_write(ASOUND_CONF, render_asound(profile, settings), 0o644)
    except (OSError, ValueError) as exc:
        logger.error("apply_equalizer: %s", exc)
        return False, "Could not update the ALSA equalizer route."
    if structural:
        return True, "restart: Equalizer route updated."
    return True, "live: Equalizer updated live."
