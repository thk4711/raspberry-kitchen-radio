"""Managed audio settings: the user-selected maximum-volume cap.

The Audio page exposes one safe, hand-edited audio option: a **maximum** output
volume cap (0..100). The ALSA mixer and amplifier GPIO are derived from the
canonical sound-card profile and are not duplicated here. The value is written
to ``<managed_dir>/audio.ini`` and layered over ``radio.conf`` by ``radio.py``.

The maximum volume is a cap the ADC volume loop clamps against
(``lib/adc_controller.ADCController``), which is safer than a fixed startup
volume the physical knob would overwrite.

Standard-library only, mirroring :mod:`radio_web.sources_store`.
"""

import os
import re
from typing import Dict

from . import config_store, validators

AUDIO_FILENAME = "audio.ini"
AUDIO_BACKUP_FILENAME = "audio.ini.bak"

AUDIO_SECTION = "audio"
VOLUME_SECTION = "volume"
GPIO_SECTION = "gpio"

_SECTION_RE = re.compile(r"^\[(.+)\]$")

# Shipped default: 100% means no user-selected cap.
DEFAULTS: Dict[str, str] = {"max_volume": "100"}


def managed_audio_path() -> str:
    """Return the absolute path of the managed ``audio.ini``."""
    return os.path.join(config_store.managed_dir(), AUDIO_FILENAME)


def managed_backup_path() -> str:
    """Return the absolute path of the managed ``audio.ini.bak``."""
    return os.path.join(config_store.managed_dir(), AUDIO_BACKUP_FILENAME)


def _parse_audio(text: str) -> Dict[str, str]:
    """Parse ``audio.ini`` into a flat ``{field: value}`` dict.

    Recognises ``mixer`` (``[audio]``), ``max_volume``/``max`` (``[volume]``) and
    ``amp`` (``[gpio]``). Unknown sections/keys are ignored (advisory file).
    """
    fields: Dict[str, str] = {}
    section = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _SECTION_RE.match(line)
        if match is not None:
            section = match.group(1)
            continue
        if "=" not in line:
            continue
        key, value = (part.strip() for part in line.split("=", 1))
        # Legacy profile-derived fields remain readable for one rollback
        # generation, but new writes contain only the canonical user choice.
        if section == AUDIO_SECTION and key == "mixer":
            fields["mixer"] = value
        elif section == VOLUME_SECTION and key in ("max", "max_volume"):
            fields["max_volume"] = value
        elif section == GPIO_SECTION and key == "amp":
            fields["amp"] = value
    return fields


def load_audio() -> Dict[str, str]:
    """Return the managed audio settings for the form, merged over defaults."""
    settings = dict(DEFAULTS)
    managed = config_store.read_text(managed_audio_path())
    if managed is not None:
        parsed = _parse_audio(managed)
        for key in DEFAULTS:
            if key in parsed:
                settings[key] = parsed[key]
    return settings


def validate_settings(submitted: Dict[str, str]) -> Dict[str, str]:
    """Return a cleaned ``{field: value}`` dict, or raise :class:`ValueError`.

    ``max_volume`` is optional and the stored value is retained when absent.
    """
    current = load_audio()
    if "max_volume" in submitted:
        max_volume = str(
            validators.validate_volume_percent(submitted["max_volume"], "Maximum volume")
        )
    else:
        max_volume = current["max_volume"]
    return {"max_volume": max_volume}


def serialize_audio(cleaned: Dict[str, str]) -> str:
    """Render the canonical user-selected volume cap."""
    return f"[{VOLUME_SECTION}]\nmax = {cleaned['max_volume']}\n"


def _backup_existing() -> None:
    current = config_store.read_text(managed_audio_path())
    if current is not None:
        config_store.atomic_write(managed_backup_path(), current, config_store.CONFIG_MODE)


def save_audio(submitted: Dict[str, str]) -> None:
    """Validate, back up and atomically persist ``submitted`` to ``audio.ini``.

    Follows the managed-file recipe: validate first, keep one
    ``.bak``, then write atomically at mode ``0644``. Raises :class:`ValueError`
    on any invalid field (nothing is written in that case).
    """
    cleaned = validate_settings(submitted)
    payload = serialize_audio(cleaned)
    _backup_existing()
    config_store.atomic_write(managed_audio_path(), payload, config_store.CONFIG_MODE)


def restore_builtin() -> None:
    """Remove the managed override so the shipped audio defaults return.

    Deletes ``audio.ini`` (and its ``.bak``) if present. Idempotent.
    """
    for path in (managed_audio_path(), managed_backup_path()):
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
