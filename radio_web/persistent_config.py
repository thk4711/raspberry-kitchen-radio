"""Early-boot migration, identity handling and runtime configuration rendering."""

import argparse
import fcntl
import json
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from lib import __version__

from . import (
    audio_hardware_apply,
    audio_hardware_store,
    config_store,
    device_store,
    validators,
)

CURRENT_SCHEMA = 1
MINIMUM_READER_SCHEMA = 1
DATA_DIR = Path(os.environ.get("RADIO_DATA_DIR", "/data"))
RADIO_DIR = DATA_DIR / "radio"
IDENTITY_DIR = DATA_DIR / "identity"
BACKUP_DIR = DATA_DIR / "update" / "config-backups"
SCHEMA_FILE = RADIO_DIR / "schema-version"
COMPATIBILITY_FILE = RADIO_DIR / "schema-compatibility.json"
ROOT_HASH_FILE = IDENTITY_DIR / "root-password.hash"
SHADOW_FILE = Path(os.environ.get("RADIO_SHADOW_FILE", "/etc/shadow"))
LOCK_FILE = Path(os.environ.get("RADIO_CONFIG_LOCK", "/run/radio-config.lock"))
MPD_TEMPLATE = Path(os.environ.get("RADIO_MPD_TEMPLATE", "/usr/share/radio/templates/mpd.conf"))
MAX_BACKUPS = 3
MAX_COMPATIBILITY_BYTES = 4096

_ROOT_HASH_RE = re.compile(r"^(?:[!*]+|\$[A-Za-z0-9./]+\$[^:\n]+)$")
_DEVICE_VALIDATORS = {
    "name": validators.validate_device_name,
    "timezone": validators.validate_timezone,
    "ntp_server": validators.validate_ntp_server,
}


def _atomic_write(path: Path, data: str, mode: int, uid: Optional[int] = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        with open(tmp, "w", encoding="utf-8") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, mode)
        if uid is not None and os.geteuid() == 0:
            os.chown(tmp, uid, uid)
        os.replace(tmp, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def _schema_version() -> int:
    try:
        value = int(SCHEMA_FILE.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return 0
    if value < 0:
        raise ValueError(f"unsupported persistent schema version {value}")
    return value


def _compatibility_payload(schema: int, minimum_reader: int) -> Dict[str, Any]:
    return {
        "minimum_reader_schema": minimum_reader,
        "schema": 1,
        "writer_schema": schema,
    }


def _write_compatibility(schema: int, minimum_reader: int) -> None:
    payload = json.dumps(
        _compatibility_payload(schema, minimum_reader), separators=(",", ":"), sort_keys=True
    )
    _atomic_write(COMPATIBILITY_FILE, payload + "\n", 0o644, uid=601)


def compatibility_state() -> Dict[str, int]:
    """Return and validate the public persistent-data compatibility contract.

    Schema 0/1 media created before this marker existed remain supported. A
    future writer schema, however, must publish the marker so an older retained
    slot can decide whether additive data is still safe to read.
    """
    writer = _schema_version()
    try:
        if COMPATIBILITY_FILE.stat().st_size > MAX_COMPATIBILITY_BYTES:
            raise ValueError("persistent compatibility metadata is too large")
        value = json.loads(COMPATIBILITY_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        if writer <= CURRENT_SCHEMA:
            return {"writer_schema": writer, "minimum_reader_schema": writer}
        raise ValueError("persistent compatibility metadata is missing") from None
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError) as exc:
        raise ValueError("persistent compatibility metadata is invalid") from exc
    if not isinstance(value, dict) or set(value) != {
        "schema",
        "writer_schema",
        "minimum_reader_schema",
    }:
        raise ValueError("persistent compatibility metadata is invalid")
    if value.get("schema") != 1 or not isinstance(value.get("writer_schema"), int):
        raise ValueError("persistent compatibility metadata is invalid")
    minimum = value.get("minimum_reader_schema")
    if not isinstance(minimum, int) or minimum < 0 or minimum > value["writer_schema"]:
        raise ValueError("persistent compatibility metadata is invalid")
    if value["writer_schema"] != writer:
        raise ValueError("persistent schema and compatibility metadata disagree")
    return {"writer_schema": writer, "minimum_reader_schema": minimum}


def can_read_persistent_state(reader_schema: int) -> bool:
    """Return whether a firmware reader may safely consume the shared data."""
    return compatibility_state()["minimum_reader_schema"] <= reader_schema


def _backup_configuration(source_schema: int, target_schema: int) -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    name = f"schema-{int(time.time())}-{os.getpid()}"
    staging = BACKUP_DIR / f".{name}.incomplete"
    destination = BACKUP_DIR / name
    try:
        staging.mkdir(mode=0o700)
        for relative in ("radio", "network", "identity"):
            source = DATA_DIR / relative
            if source.exists():
                shutil.copytree(source, staging / relative, symlinks=True)
        metadata = {
            "accepted": False,
            "created_at": int(time.time()),
            "firmware_version": __version__,
            "schema": 1,
            "source_schema": source_schema,
            "target_schema": target_schema,
        }
        _atomic_write(
            staging / "backup.json",
            json.dumps(metadata, separators=(",", ":"), sort_keys=True) + "\n",
            0o600,
        )
        os.replace(staging, destination)
        backups = sorted(path for path in BACKUP_DIR.iterdir() if path.is_dir())
        while len(backups) > MAX_BACKUPS:
            accepted = [
                path for path in backups if (_backup_metadata(path) or {}).get("accepted") is True
            ]
            protected = accepted[-1] if accepted else None
            expired = next((path for path in backups if path != protected), None)
            if expired is None:
                break
            shutil.rmtree(expired)
            backups.remove(expired)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return destination


def _migration_to_1() -> None:
    """Schema 1 established the persistent layout; no data rewrite is required."""


MIGRATIONS: Dict[int, Callable[[], None]] = {1: _migration_to_1}


def _backup_metadata(path: Path) -> Optional[Dict[str, Any]]:
    try:
        value = json.loads((path / "backup.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError):
        return None
    return value if isinstance(value, dict) and value.get("schema") == 1 else None


def _prune_accepted_backups() -> None:
    backups = sorted(path for path in BACKUP_DIR.iterdir() if path.is_dir())
    accepted = [path for path in backups if (_backup_metadata(path) or {}).get("accepted") is True]
    # Keep the current and immediately previous accepted migration generations.
    for expired in accepted[:-2]:
        shutil.rmtree(expired)


def mark_migration_accepted() -> None:
    """Accept the newest matching migration backup after firmware mark-good."""
    if not BACKUP_DIR.exists():
        return
    for path in reversed(sorted(item for item in BACKUP_DIR.iterdir() if item.is_dir())):
        metadata = _backup_metadata(path)
        if metadata is None or metadata.get("target_schema") != _schema_version():
            continue
        if metadata.get("accepted") is not True:
            metadata["accepted"] = True
            metadata["accepted_at"] = int(time.time())
            _atomic_write(
                path / "backup.json",
                json.dumps(metadata, separators=(",", ":"), sort_keys=True) + "\n",
                0o600,
            )
        _prune_accepted_backups()
        return


def migrate() -> None:
    """Migrate persistent state under an exclusive lock; safe to repeat."""
    RADIO_DIR.mkdir(parents=True, exist_ok=True)
    IDENTITY_DIR.mkdir(parents=True, exist_ok=True)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOCK_FILE, "a", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        source_schema = _schema_version()
        if source_schema > CURRENT_SCHEMA:
            if not can_read_persistent_state(CURRENT_SCHEMA):
                raise ValueError(
                    f"persistent schema {source_schema} requires a newer firmware reader"
                )
            return
        if source_schema == CURRENT_SCHEMA:
            state = compatibility_state()
            if not COMPATIBILITY_FILE.exists():
                _write_compatibility(source_schema, state["minimum_reader_schema"])
            return
        _backup_configuration(source_schema, CURRENT_SCHEMA)
        for target_schema in range(source_schema + 1, CURRENT_SCHEMA + 1):
            migration = MIGRATIONS.get(target_schema)
            if migration is None:
                raise ValueError(f"no migration to persistent schema {target_schema}")
            migration()
        _write_compatibility(CURRENT_SCHEMA, MINIMUM_READER_SCHEMA)
        _atomic_write(SCHEMA_FILE, f"{CURRENT_SCHEMA}\n", 0o644, uid=601)


def _root_hash_from_shadow() -> str:
    for line in SHADOW_FILE.read_text(encoding="utf-8").splitlines():
        fields = line.split(":")
        if fields and fields[0] == "root" and len(fields) >= 2:
            value = fields[1]
            if _ROOT_HASH_RE.fullmatch(value):
                return value
            raise ValueError("root password hash has an unsafe format")
    raise ValueError("root account is absent from shadow")


def capture_root_password() -> None:
    """Persist only root's password hash, never the complete shadow file."""
    _atomic_write(ROOT_HASH_FILE, _root_hash_from_shadow() + "\n", 0o600)


def apply_root_password() -> None:
    """Apply the persistent root hash while preserving all slot-local accounts."""
    try:
        value = ROOT_HASH_FILE.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return
    if not _ROOT_HASH_RE.fullmatch(value):
        raise ValueError("persistent root password hash has an unsafe format")
    lines = SHADOW_FILE.read_text(encoding="utf-8").splitlines()
    output = []
    changed = False
    for line in lines:
        fields = line.split(":")
        if fields and fields[0] == "root" and len(fields) >= 2:
            fields[1] = value
            line = ":".join(fields)
            changed = True
        output.append(line)
    if not changed:
        raise ValueError("root account is absent from shadow")
    _atomic_write(SHADOW_FILE, "\n".join(output) + "\n", 0o600)


def set_device(key: str, value: str) -> None:
    """Merge one validated provisioning value into persistent ``device.ini``."""
    current = device_store.load_device()
    if key == "ssh_enabled":
        if value not in ("true", "false"):
            raise ValueError("invalid SSH setting")
        cleaned = value
    else:
        validator = _DEVICE_VALIDATORS.get(key)
        if validator is None:
            raise ValueError("unsupported device setting")
        cleaned = validator(value)
    current[key] = cleaned
    payload = device_store.serialize_device(current)
    _atomic_write(RADIO_DIR / device_store.DEVICE_FILENAME, payload, 0o644, uid=601)


def render_runtime() -> None:
    """Regenerate firmware-owned files from canonical persistent inputs."""
    managed = config_store.read_text(str(RADIO_DIR / device_store.DEVICE_FILENAME))
    if managed is not None:
        values = device_store._parse_device(managed)
        if values.get("name"):
            device_store.apply_hostname_files(values["name"])
        device_store.apply_time_files(values.get("timezone", ""), values.get("ntp_server", ""))
    profile = audio_hardware_store.PROFILES[audio_hardware_store.load_profile()]
    audio_hardware_apply._write_alsa_mpd_modules(profile, mpd_template=MPD_TEMPLATE)


def prepare() -> None:
    migrate()
    apply_root_password()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "operation",
        choices=("prepare", "render", "capture-root-password", "set-device"),
    )
    parser.add_argument("key", nargs="?")
    parser.add_argument("value", nargs="?")
    args = parser.parse_args()
    if args.operation == "prepare":
        prepare()
    elif args.operation == "render":
        render_runtime()
    elif args.operation == "capture-root-password":
        capture_root_password()
    else:
        if args.key is None or args.value is None:
            parser.error("set-device requires key and value")
        set_device(args.key, args.value)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
