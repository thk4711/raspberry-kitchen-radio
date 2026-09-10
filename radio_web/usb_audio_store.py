"""Managed USB Audio gadget mode selection."""

import os
import re
from typing import Optional

from . import config_store

USB_AUDIO_SECTION = "usb_audio"
USB_AUDIO_FILENAME = "usb_audio.ini"
USB_AUDIO_BACKUP_FILENAME = "usb_audio.ini.bak"
DEFAULT_MODE = "uac1"
MODES = ("uac1", "uac2")
_SECTION_RE = re.compile(r"^\[(.+)\]$")


def managed_usb_audio_path() -> str:
    return os.path.join(config_store.managed_dir(), USB_AUDIO_FILENAME)


def managed_backup_path() -> str:
    return os.path.join(config_store.managed_dir(), USB_AUDIO_BACKUP_FILENAME)


def validate_mode(mode: str) -> str:
    cleaned = (mode or "").strip().lower()
    if cleaned not in MODES:
        raise ValueError("Unknown USB Audio mode. Choose UAC1 or UAC2.")
    return cleaned


def _parse_mode(text: str) -> Optional[str]:
    section = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _SECTION_RE.match(line)
        if match:
            section = match.group(1)
            continue
        if section == USB_AUDIO_SECTION and "=" in line:
            key, value = (part.strip() for part in line.split("=", 1))
            if key == "mode":
                try:
                    return validate_mode(value)
                except ValueError:
                    return None
    return None


def load_mode() -> str:
    text = config_store.read_text(managed_usb_audio_path())
    return _parse_mode(text) or DEFAULT_MODE if text is not None else DEFAULT_MODE


def save_mode(mode: str) -> str:
    cleaned = validate_mode(mode)
    current = config_store.read_text(managed_usb_audio_path())
    if current is not None:
        config_store.atomic_write(managed_backup_path(), current, config_store.CONFIG_MODE)
    config_store.atomic_write(
        managed_usb_audio_path(),
        f"[{USB_AUDIO_SECTION}]\nmode = {cleaned}\n",
        config_store.CONFIG_MODE,
    )
    return cleaned


def restore_builtin() -> None:
    for path in (managed_usb_audio_path(), managed_backup_path()):
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
