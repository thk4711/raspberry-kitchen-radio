"""Managed display (``[ui]``) settings: load, validate, serialize and persist.

The web UI exposes a small, user-facing subset of the rich display theme
screensaver idle timeout, animations on/off,
crossfade duration, clock size, and the OSD/toast overlay durations, plus four
named presets (Default / High contrast / Dim night / No animations).

Everything is written to ``<managed_dir>/display.ini`` under a ``[ui]`` section.
The player's :class:`DisplayController` already reads ``display.ini`` layered
over the shipped ``display.conf`` and feeds ``[ui]`` to
``lib/display/theme.build_theme`` — which re-validates and clamps every
value and falls back to the shipped default per key. So this module only needs to
persist a clean ``[ui]`` block; the player applies it on the next
``restart_radio``. A missing file means "shipped defaults", exactly as today.

Standard-library only, mirroring :mod:`radio_web.sources_store`.
"""
import os
import re
from typing import Dict

from . import config_store, validators

DISPLAY_FILENAME = "display.ini"
DISPLAY_BACKUP_FILENAME = "display.ini.bak"

UI_SECTION = "ui"

_SECTION_RE = re.compile(r"^\[(.+)\]$")

# The exposed fields and their built-in defaults. These mirror the shipped
# defaults in ``lib/display/theme.Theme`` so the form pre-fills with
# the out-of-the-box look when no override exists.
DEFAULTS: Dict[str, str] = {
    "idle_timeout": "30",
    "animations": "true",
    "rotate_180": "false",
    "crossfade_ms": "150",
    "clock_size": "24",
    "osd_duration": "1.5",
    "toast_duration": "1.6",
    "theme_preset": "default",
}

# Named presets. Each maps to the concrete ``[ui]`` keys it overrides; the
# "default" preset overrides nothing (the individual fields win). Presets are a
# convenience layered *before* the individual fields, so an explicit field edit
# still takes precedence when the user changes one.
PRESETS: Dict[str, Dict[str, str]] = {
    "default": {},
    "high_contrast": {
        "text_color": "WHITE",
        "subtext_color": "255, 255, 255",
        "scrim_opacity": "0.75",
    },
    "dim_night": {
        "idle_timeout": "15",
        "scrim_opacity": "0.7",
    },
    "no_animations": {
        "animations": "false",
        "crossfade_ms": "0",
    },
}


def managed_display_path() -> str:
    """Return the absolute path of the managed ``display.ini``."""
    return os.path.join(config_store.managed_dir(), DISPLAY_FILENAME)


def managed_backup_path() -> str:
    """Return the absolute path of the managed ``display.ini.bak``."""
    return os.path.join(config_store.managed_dir(), DISPLAY_BACKUP_FILENAME)


def _parse_ui(text: str) -> Dict[str, str]:
    """Parse ``display.ini`` text into a flat ``{key: value}`` dict.

    Only keys under the ``[ui]`` section are returned; everything else is
    ignored (the file is advisory, never fatal).
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
        if section != UI_SECTION or "=" not in line:
            continue
        key, value = (part.strip() for part in line.split("=", 1))
        fields[key] = value
    return fields


def load_display() -> Dict[str, str]:
    """Return the managed display settings for the form, merged over defaults.

    Always returns every key in :data:`DEFAULTS` so the template can render
    without ``KeyError``.
    """
    settings = dict(DEFAULTS)
    managed = config_store.read_text(managed_display_path())
    if managed is not None:
        parsed = _parse_ui(managed)
        for key in DEFAULTS:
            if key in parsed:
                settings[key] = parsed[key]
    return settings


def _fmt_float(value: float) -> str:
    """Render a float without a trailing ``.0`` clutter (``1.5`` / ``2``)."""
    return f"{value:g}"


def validate_settings(submitted: Dict[str, str]) -> Dict[str, str]:
    """Return a cleaned ``{key: value}`` dict, or raise :class:`ValueError`.

    Each field is validated with the shared per-field validators. Booleans are
    normalised to ``true``/``false`` strings so the serialized file round-trips
    through the player's parser and ``theme.build_theme``.
    """
    preset = validators.validate_theme_preset(submitted.get("theme_preset", ""))
    animations = validators.validate_bool_flag(submitted.get("animations", ""))
    rotate_180 = validators.validate_bool_flag(submitted.get("rotate_180", ""))
    return {
        "theme_preset": preset,
        "idle_timeout": str(
            validators.validate_idle_timeout(submitted.get("idle_timeout", ""))
        ),
        "animations": "true" if animations else "false",
        "rotate_180": "true" if rotate_180 else "false",
        "crossfade_ms": str(
            validators.validate_crossfade_ms(submitted.get("crossfade_ms", ""))
        ),
        "clock_size": str(
            validators.validate_volume_percent(
                submitted.get("clock_size", ""), "Clock size"
            )
        ),
        "osd_duration": _fmt_float(
            validators.validate_overlay_duration(
                submitted.get("osd_duration", ""), "Volume OSD duration"
            )
        ),
        "toast_duration": _fmt_float(
            validators.validate_overlay_duration(
                submitted.get("toast_duration", ""), "Toast duration"
            )
        ),
    }


def _resolve_ui_keys(cleaned: Dict[str, str]) -> Dict[str, str]:
    """Expand ``cleaned`` (form fields + preset) into concrete ``[ui]`` keys.

    The explicit form fields are written first, then the chosen preset's keys
    override them — so selecting a named preset (other than ``default``) wins
    for the fields that preset controls, which is what the user intends when
    they pick "No animations" etc. The ``default`` preset overrides nothing.
    The ``theme_preset`` marker is kept so the page can re-select it.
    """
    ui: Dict[str, str] = {}
    for key in ("idle_timeout", "animations", "rotate_180", "crossfade_ms", "clock_size",
                "osd_duration", "toast_duration"):
        ui[key] = cleaned[key]
    ui.update(PRESETS.get(cleaned.get("theme_preset", "default"), {}))
    # Persist the chosen preset marker; theme.build_theme ignores unknown keys.
    ui["theme_preset"] = cleaned.get("theme_preset", "default")
    return ui


def serialize_display(cleaned: Dict[str, str]) -> str:
    """Render ``cleaned`` as a ``[ui]`` INI block (stable key order)."""
    ui = _resolve_ui_keys(cleaned)
    lines = [f"[{UI_SECTION}]"]
    for key in sorted(ui):
        lines.append(f"{key} = {ui[key]}")
    return "\n".join(lines) + "\n"


def _backup_existing() -> None:
    current = config_store.read_text(managed_display_path())
    if current is not None:
        config_store.atomic_write(
            managed_backup_path(), current, config_store.CONFIG_MODE
        )


def save_display(submitted: Dict[str, str]) -> None:
    """Validate, back up and atomically persist ``submitted`` to ``display.ini``.

    Follows the managed-file recipe: validate everything first,
    keep one ``.bak`` of the previous managed file, then write atomically at
    mode ``0644``. Raises :class:`ValueError` if any field is invalid (nothing
    is written in that case).
    """
    cleaned = validate_settings(submitted)
    payload = serialize_display(cleaned)
    _backup_existing()
    config_store.atomic_write(
        managed_display_path(), payload, config_store.CONFIG_MODE
    )


def restore_builtin() -> None:
    """Remove the managed override so the shipped display look returns.

    Deletes ``display.ini`` (and its ``.bak``) if present; a subsequent
    :func:`load_display` — and the player's ``read_config_layered`` — then fall
    back to the shipped ``display.conf`` defaults. Idempotent.
    """
    for path in (managed_display_path(), managed_backup_path()):
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
