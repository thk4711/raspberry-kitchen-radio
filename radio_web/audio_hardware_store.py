"""Declarative sound-card catalog and managed profile selection."""

import json
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from . import config_store


@dataclass(frozen=True)
class AudioProfile:
    """All settings needed to select and route one audio device."""

    label: str
    family: str
    kind: str
    support_level: str
    compatibility_note: str
    audio_param: str
    overlay: str
    mpd_device: str
    mpd_mixer: str
    knob_mixer: str
    overlay_params: Tuple[str, ...] = field(default_factory=tuple)
    volume_control: str = "hardware"
    modules: Tuple[str, ...] = field(default_factory=tuple)
    alsa_card: Optional[str] = None
    amp_gpio: Optional[int] = None
    required_kernel_symbols: Tuple[str, ...] = field(default_factory=tuple)
    mixer_max_percent: int = 100
    output_format: str = "S16_LE"
    output_rate: int = 44100

    @property
    def overlay_setting(self) -> str:
        """Return the complete value written after ``dtoverlay=``."""
        return ",".join((self.overlay, *self.overlay_params)) if self.overlay else ""


CATALOG_PATH = os.path.join(os.path.dirname(__file__), "audio_hardware_profiles.json")


def _load_catalog(path: str = CATALOG_PATH) -> Dict[str, AudioProfile]:
    """Load the shared JSON catalog used by runtime and Buildroot verification."""
    with open(path, encoding="utf-8") as handle:
        raw = json.load(handle)
    if not isinstance(raw, dict):
        raise ValueError("audio profile catalog must be a JSON object")
    profiles: Dict[str, AudioProfile] = {}
    for profile_id, values in raw.items():
        if not isinstance(profile_id, str) or not isinstance(values, dict):
            raise ValueError("invalid audio profile catalog entry")
        item = dict(values)
        for key in ("overlay_params", "modules", "required_kernel_symbols"):
            value = item.get(key, [])
            if not isinstance(value, list) or not all(isinstance(part, str) for part in value):
                raise ValueError(f"{profile_id}: {key} must be an array of strings")
            item[key] = tuple(value)
        profiles[profile_id] = AudioProfile(**item)
    return profiles


# Ordered JSON objects preserve catalog order in Python 3.9+.
PROFILES = _load_catalog()

# Current and legacy audio overlays owned by the config.txt editor.
MANAGED_AUDIO_OVERLAYS: Tuple[str, ...] = (
    "iqaudio-dacplus",
    "hifiberry-dac",
    "hifiberry-dacplus",
    "hifiberry-dacplus-std",
    "hifiberry-dacplus-pro",
    "hifiberry-amp",
    "max98357a",
    "allo-boss-dac-pcm512x-audio",
    "i-sabre-q2m",
    "allo-katana-dac-audio",
    "justboom-dac",
    "merus-amp",
)

DEFAULT_PROFILE = "headphones"
HARDWARE_SECTION = "audio_hardware"
HARDWARE_FILENAME = "audio_hardware.ini"
HARDWARE_BACKUP_FILENAME = "audio_hardware.ini.bak"
_SECTION_RE = re.compile(r"^\[(.+)\]$")


def profile_ids() -> List[str]:
    return list(PROFILES.keys())


def overlay_ids() -> List[str]:
    return list(dict.fromkeys(p.overlay for p in PROFILES.values() if p.overlay))


def managed_overlay_ids() -> List[str]:
    return list(MANAGED_AUDIO_OVERLAYS)


def catalog_errors() -> List[str]:
    """Return schema/consistency errors for the declarative catalog."""
    errors: List[str] = []
    safe_token = re.compile(r"^[A-Za-z0-9_.-]+(?:=[A-Za-z0-9_.-]+)?$")
    for profile_id, profile in PROFILES.items():
        if not re.fullmatch(r"[a-z0-9_]+", profile_id):
            errors.append(f"invalid profile id: {profile_id}")
        if profile.support_level not in {"tested", "kernel-supported", "experimental"}:
            errors.append(f"{profile_id}: invalid support level")
        if profile.volume_control not in {"hardware", "softvol", "fixed"}:
            errors.append(f"{profile_id}: invalid volume control")
        if profile.kind == "i2s" and not profile.overlay:
            errors.append(f"{profile_id}: I2S profile has no overlay")
        if profile.overlay and not safe_token.fullmatch(profile.overlay):
            errors.append(f"{profile_id}: unsafe overlay")
        if any(not safe_token.fullmatch(param) for param in profile.overlay_params):
            errors.append(f"{profile_id}: unsafe overlay parameter")
        if profile.kind == "i2s" and not profile.alsa_card:
            errors.append(f"{profile_id}: no stable ALSA card id")
        if profile.volume_control == "hardware" and not profile.knob_mixer:
            errors.append(f"{profile_id}: hardware volume needs a mixer")
        if profile.volume_control == "softvol" and profile.knob_mixer != "Radio Volume":
            errors.append(f"{profile_id}: softvol must use Radio Volume")
        if not 0 <= profile.mixer_max_percent <= 100:
            errors.append(f"{profile_id}: mixer maximum must be 0..100")
        if profile.output_format not in {"S16_LE", "S32_LE", "S24_LE", "S24_3LE"}:
            errors.append(f"{profile_id}: unsupported output format")
        if profile.output_rate not in {44100, 48000, 96000}:
            errors.append(f"{profile_id}: unsupported output rate")
    missing = set(overlay_ids()) - set(MANAGED_AUDIO_OVERLAYS)
    if missing:
        errors.append(f"selectable overlays are unmanaged: {sorted(missing)}")
    return errors


def managed_hardware_path() -> str:
    return os.path.join(config_store.managed_dir(), HARDWARE_FILENAME)


def managed_backup_path() -> str:
    return os.path.join(config_store.managed_dir(), HARDWARE_BACKUP_FILENAME)


def _parse_profile_id(text: str) -> Optional[str]:
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
        if section == HARDWARE_SECTION and key == "profile":
            return value if value in PROFILES else None
    return None


def load_profile() -> str:
    managed = config_store.read_text(managed_hardware_path())
    if managed is not None:
        parsed = _parse_profile_id(managed)
        if parsed is not None:
            return parsed
    return DEFAULT_PROFILE


def validate_profile(profile_id: str) -> str:
    cleaned = (profile_id or "").strip()
    if cleaned not in PROFILES:
        raise ValueError(f"Unknown sound card '{profile_id}'.")
    return cleaned


def serialize_profile(profile_id: str) -> str:
    return f"[{HARDWARE_SECTION}]\nprofile = {profile_id}\n"


def _backup_existing() -> None:
    current = config_store.read_text(managed_hardware_path())
    if current is not None:
        config_store.atomic_write(managed_backup_path(), current, config_store.CONFIG_MODE)


def save_profile(profile_id: str) -> str:
    cleaned = validate_profile(profile_id)
    _backup_existing()
    config_store.atomic_write(
        managed_hardware_path(), serialize_profile(cleaned), config_store.CONFIG_MODE
    )
    return cleaned


def restore_builtin() -> None:
    for path in (managed_hardware_path(), managed_backup_path()):
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
