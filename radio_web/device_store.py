"""Managed device settings: name, time and remote-access configuration.

The web UI writes ``<managed_dir>/device.ini`` which the player reads layered
over ``radio.conf`` (Phase 1 wired ``read_config_layered`` in ``radio.py``).
This module handles the **web-side** load/validate/serialize/save/restore of
that file (atomic ``0644`` + one ``.bak``), plus the
**privileged** file writes the root helper performs to actually apply the
hostname / timezone / NTP (``apply_hostname_files`` / ``apply_time_files``),
mirroring ``usr/sbin/provision-from-boot`` so both paths behave identically.

A hostname set on the SD card ``pisonic-config.txt`` wins over the web UI.
``provision-from-boot`` drops a marker file when it
applied a hostname from the card; :func:`sd_card_hostname_locked` checks it so
the UI can show + lock the device-name field.

Standard-library only, mirroring :mod:`radio_web.sources_store`.
"""

import os
import re
from typing import Dict, List

from . import config_store

DEVICE_FILENAME = "device.ini"
DEVICE_BACKUP_FILENAME = "device.ini.bak"

DEVICE_SECTION = "device"
TIME_SECTION = "time"
REMOTE_ACCESS_SECTION = "remote_access"

# System files the privileged helper writes (same targets provision-from-boot
# manages). Overridable so tests never touch the real /etc or /run.
HOSTNAME_FILE = os.environ.get("RADIO_HOSTNAME_FILE", "/etc/hostname")
HOSTS_FILE = os.environ.get("RADIO_HOSTS_FILE", "/etc/hosts")
LOCALTIME_LINK = os.environ.get("RADIO_LOCALTIME_LINK", "/etc/localtime")
TIMEZONE_FILE = os.environ.get("RADIO_TIMEZONE_FILE", "/etc/timezone")
CHRONY_CONF = os.environ.get("RADIO_CHRONY_CONF", "/etc/chrony.conf")
ZONEINFO_DIR = os.environ.get("RADIO_ZONEINFO_DIR", "/usr/share/zoneinfo")

# Marker written by provision-from-boot when the SD card set the hostname.
# Overridable for tests.
HOSTNAME_LOCK_MARKER = os.environ.get(
    "RADIO_HOSTNAME_LOCK_MARKER", "/run/provision-hostname-locked"
)
ROOT_CREDENTIAL_MARKER = os.environ.get(
    "RADIO_ROOT_CREDENTIAL_MARKER", "/data/radio/root-credential-provisioned"
)

_SECTION_RE = re.compile(r"^\[(.+)\]$")

_ZONEINFO_TABLES = ("zone1970.tab", "zone.tab")
_ZONEINFO_METADATA = {
    "+VERSION",
    "iso3166.tab",
    "leap-seconds.list",
    "leapseconds",
    "localtime",
    "posixrules",
    "tzdata.zi",
    "zone.tab",
    "zone1970.tab",
}


def managed_device_path() -> str:
    """Return the absolute path of the managed ``device.ini``."""
    return os.path.join(config_store.managed_dir(), DEVICE_FILENAME)


def managed_backup_path() -> str:
    """Return the absolute path of the managed ``device.ini.bak``."""
    return os.path.join(config_store.managed_dir(), DEVICE_BACKUP_FILENAME)


def _parse_device(text: str) -> Dict[str, str]:
    """Parse ``device.ini`` text into a flat ``{field: value}`` dict.

    Recognises ``name`` (``[device]``), ``timezone`` and ``ntp_server``
    (``[time]``), plus ``ssh_enabled`` (``[remote_access]``). Unknown
    sections/keys are ignored (advisory file).
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
        if section == DEVICE_SECTION and key == "name":
            fields["name"] = value
        elif section == TIME_SECTION and key in ("timezone", "ntp_server"):
            fields[key] = value
        elif section == REMOTE_ACCESS_SECTION and key == "ssh_enabled":
            fields[key] = value
    return fields


def _read_value(path: str) -> str:
    """Return the first non-empty line in ``path``, or an empty string."""
    try:
        with open(path, encoding="utf-8") as handle:
            for raw in handle:
                value = raw.strip()
                if value:
                    return value
    except OSError:
        pass
    return ""


def _current_timezone() -> str:
    """Read the active zone from ``/etc/timezone`` or ``/etc/localtime``."""
    timezone = _read_value(TIMEZONE_FILE)
    if timezone:
        return timezone
    localtime = os.path.realpath(LOCALTIME_LINK)
    zoneinfo = os.path.realpath(ZONEINFO_DIR) + os.sep
    return localtime[len(zoneinfo) :] if localtime.startswith(zoneinfo) else ""


def _current_ntp_server() -> str:
    """Return the first configured Chrony pool or server hostname."""
    try:
        with open(CHRONY_CONF, encoding="utf-8") as handle:
            for raw in handle:
                match = re.match(r"^\s*(?:pool|server)\s+(\S+)", raw)
                if match is not None:
                    return match.group(1)
    except OSError:
        pass
    return ""


def current_device_settings() -> Dict[str, str]:
    """Read the hostname, timezone, NTP server and default SSH setting."""
    return {
        "name": _read_value(HOSTNAME_FILE),
        "timezone": _current_timezone(),
        "ntp_server": _current_ntp_server(),
        "ssh_enabled": "false",
    }


def load_device() -> Dict[str, str]:
    """Return effective settings, using system values as fresh-image defaults."""
    settings = current_device_settings()
    text = config_store.read_text(managed_device_path())
    if text is not None:
        settings.update({key: value for key, value in _parse_device(text).items() if value})
    return settings


def root_password_is_provisioned() -> bool:
    """Return whether root published the non-secret credential capability bit."""
    return os.path.isfile(ROOT_CREDENTIAL_MARKER)


def _timezones_from_table(path: str) -> List[str]:
    zones: List[str] = []
    try:
        with open(path, encoding="utf-8") as handle:
            for raw in handle:
                columns = raw.rstrip("\n").split("\t")
                if not raw.startswith("#") and len(columns) >= 3:
                    zones.append(columns[2])
    except OSError:
        pass
    return zones


def available_timezones() -> List[str]:
    """Return canonical zone names installed in the Buildroot image."""
    zones: List[str] = []
    for filename in _ZONEINFO_TABLES:
        zones = _timezones_from_table(os.path.join(ZONEINFO_DIR, filename))
        if zones:
            break
    if not zones:
        for root, dirs, files in os.walk(ZONEINFO_DIR):
            dirs[:] = [name for name in dirs if name not in ("posix", "right")]
            for filename in files:
                relative = os.path.relpath(os.path.join(root, filename), ZONEINFO_DIR)
                if relative not in _ZONEINFO_METADATA and not relative.startswith("."):
                    zones.append(relative)
    if os.path.isfile(os.path.join(ZONEINFO_DIR, "UTC")):
        zones.append("UTC")
    return sorted(set(zones))


def serialize_device(settings: Dict[str, str]) -> str:
    """Render ``settings`` as ``[device]``/``[time]``/remote-access INI text.

    Only non-empty fields are written, so an unset timezone/NTP simply falls
    back to the lower config layer rather than persisting an empty value. SSH
    defaults to disabled when absent.
    """
    lines = [f"[{DEVICE_SECTION}]"]
    name = settings.get("name", "").strip()
    if name:
        lines.append(f"name = {name}")
    time_lines = []
    tz = settings.get("timezone", "").strip()
    ntp = settings.get("ntp_server", "").strip()
    if tz:
        time_lines.append(f"timezone = {tz}")
    if ntp:
        time_lines.append(f"ntp_server = {ntp}")
    if time_lines:
        lines.append(f"[{TIME_SECTION}]")
        lines.extend(time_lines)
    ssh_enabled = settings.get("ssh_enabled", "false").strip().lower()
    if ssh_enabled in ("true", "false"):
        lines.append(f"[{REMOTE_ACCESS_SECTION}]")
        lines.append(f"ssh_enabled = {ssh_enabled}")
    return "\n".join(lines) + "\n"


def _backup_existing() -> None:
    current = config_store.read_text(managed_device_path())
    if current is not None:
        config_store.atomic_write(managed_backup_path(), current, config_store.CONFIG_MODE)


def save_device(settings: Dict[str, str]) -> None:
    """Back up and atomically persist device ``settings`` to ``device.ini``.

    The caller is responsible for validating fields first (via
    :mod:`radio_web.validators`). Writes atomically at mode ``0644`` after
    keeping one ``.bak`` of the previous managed file.
    """
    payload = serialize_device(settings)
    _backup_existing()
    config_store.atomic_write(managed_device_path(), payload, config_store.CONFIG_MODE)


def restore_builtin() -> None:
    """Remove the managed override so the built-in defaults take effect again.

    Deletes ``device.ini`` (and its ``.bak``) if present. Idempotent. Does not
    touch ``/etc/hostname`` etc. — those revert on the next reboot when
    ``provision-from-boot`` re-applies the baked/SD-card values.
    """
    for path in (managed_device_path(), managed_backup_path()):
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass


def sd_card_hostname_locked() -> bool:
    """Return True iff the SD card currently overrides the hostname.

    ``provision-from-boot`` drops :data:`HOSTNAME_LOCK_MARKER` at boot when it
    applied a hostname from ``pisonic-config.txt``. When
    present, the web UI locks the device-name field so the user does not believe
    a change will stick while the card override wins.
    """
    return os.path.isfile(HOSTNAME_LOCK_MARKER)


# --- Privileged file writes (run only inside the root helper) ---------------


def apply_hostname_files(name: str) -> None:
    """Write ``name`` to ``/etc/hostname`` and sync ``/etc/hosts``.

    Mirrors ``provision-from-boot``'s ``apply_hostname``: the new name takes
    effect on the next reboot (no live ``hostname`` call). The caller (the
    helper) has already re-validated ``name``.
    """
    old = ""
    try:
        with open(HOSTNAME_FILE, encoding="utf-8") as handle:
            old = handle.read().strip()
    except OSError:
        old = ""
    with open(HOSTNAME_FILE, "w", encoding="utf-8") as handle:
        handle.write(name + "\n")
    if old:
        _sync_hosts(old, name)


def _sync_hosts(old: str, new: str) -> None:
    try:
        with open(HOSTS_FILE, encoding="utf-8") as handle:
            lines = handle.readlines()
    except OSError:
        return
    pattern = re.compile(rf"(\s){re.escape(old)}(\s|$)")
    updated = [pattern.sub(rf"\g<1>{new}\g<2>", line) for line in lines]
    try:
        with open(HOSTS_FILE, "w", encoding="utf-8") as handle:
            handle.writelines(updated)
    except OSError:
        return


def apply_time_files(timezone: str, ntp_server: str) -> None:
    """Apply timezone and/or NTP server, mirroring ``provision-from-boot``.

    ``timezone`` (when non-empty) sets the ``/etc/localtime`` symlink and
    ``/etc/timezone``. ``ntp_server`` (when non-empty) rewrites the pool/server
    lines in ``chrony.conf``. Empty values are left untouched. Both values are
    already validated by the caller.
    """
    if timezone:
        zone = os.path.join(ZONEINFO_DIR, timezone)
        if os.path.isfile(zone):
            try:
                os.unlink(LOCALTIME_LINK)
            except FileNotFoundError:
                pass
            os.symlink(zone, LOCALTIME_LINK)
            with open(TIMEZONE_FILE, "w", encoding="utf-8") as handle:
                handle.write(timezone + "\n")
    if ntp_server:
        _rewrite_chrony(ntp_server)


def _rewrite_chrony(ntp_server: str) -> None:
    try:
        with open(CHRONY_CONF, encoding="utf-8") as handle:
            lines = handle.readlines()
    except OSError:
        return
    kept = [line for line in lines if not re.match(r"^\s*(pool|server)\s", line)]
    if kept and not kept[-1].endswith("\n"):
        kept[-1] += "\n"
    kept.append(f"server {ntp_server} iburst\n")
    tmp = f"{CHRONY_CONF}.tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.writelines(kept)
    os.replace(tmp, CHRONY_CONF)
