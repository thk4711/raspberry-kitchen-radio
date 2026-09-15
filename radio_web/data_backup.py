"""Safe backup and restore of allowlisted persistent appliance data."""

import hashlib
import io
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from lib import __version__

from . import persistent_config

DATA_DIR = Path(os.environ.get("RADIO_DATA_DIR", "/data"))
STATE_DIR = DATA_DIR / "update" / "backup-restore"
BACKUP_PATH = STATE_DIR / "persistent-data.tar.gz"
RESTORE_PATH = STATE_DIR / "restore.tar.gz"

INCLUDED_ROOTS = ("radio", "network", "identity", "bluetooth")
FORMAT_VERSION = 1
MAX_ARCHIVE_BYTES = 4 * 1024 * 1024
MAX_EXPANDED_BYTES = 64 * 1024 * 1024
MAX_MEMBERS = 2048
MAX_PATH_BYTES = 512
SERVICE_SCRIPTS = (
    "/etc/init.d/S90radio",
    "/etc/init.d/S50mpd",
    "/etc/init.d/S42bluetooth",
)


class BackupError(Exception):
    """A safe validation or persistence error suitable for the web UI."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_entries() -> List[Tuple[str, Path]]:
    entries: List[Tuple[str, Path]] = []
    for root in INCLUDED_ROOTS:
        base = DATA_DIR / root
        if not base.is_dir() or base.is_symlink():
            raise BackupError(f"Persistent directory {root} is unavailable.")
        entries.append((f"data/{root}", base))
        for current, dirs, files in os.walk(base, followlinks=False):
            current_path = Path(current)
            for name in sorted(dirs):
                path = current_path / name
                if path.is_symlink():
                    raise BackupError("Symlinks are not allowed in a backup.")
                entries.append((f"data/{path.relative_to(DATA_DIR).as_posix()}", path))
            for name in sorted(files):
                path = current_path / name
                if path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1:
                    raise BackupError("Backup contains an unsupported file.")
                entries.append((f"data/{path.relative_to(DATA_DIR).as_posix()}", path))
    return entries


def _manifest(entries: List[Tuple[str, Path]]) -> Dict[str, Any]:
    files = []
    for name, path in entries:
        if path.is_file():
            files.append({"path": name, "size": path.stat().st_size, "sha256": _sha256(path)})
    return {
        "format": FORMAT_VERSION,
        "created_at": int(time.time()),
        "radio_version": __version__,
        "included_roots": list(INCLUDED_ROOTS),
        "persistent_schema": persistent_config.compatibility_state(),
        "files": files,
    }


def create_backup() -> Tuple[bool, str]:
    """Create the fixed-path archive as root and make it readable by radio-web."""
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        entries = _safe_entries()
        payload = (
            json.dumps(_manifest(entries), separators=(",", ":"), sort_keys=True).encode("utf-8")
            + b"\n"
        )
        fd, temporary_name = tempfile.mkstemp(dir=STATE_DIR, prefix=".backup-")
        os.close(fd)
        temporary = Path(temporary_name)
        try:
            with tarfile.open(temporary, "w:gz") as archive:
                info = tarfile.TarInfo("manifest.json")
                info.size = len(payload)
                info.mode = 0o600
                info.mtime = int(time.time())
                archive.addfile(info, io.BytesIO(payload))
                for name, path in entries:
                    archive.add(path, arcname=name, recursive=False)
            if temporary.stat().st_size > MAX_ARCHIVE_BYTES:
                raise BackupError("The backup archive is too large.")
            with temporary.open("rb") as handle:
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o640)
            if os.geteuid() == 0:
                os.chown(temporary, 601, 601)
            os.replace(temporary, BACKUP_PATH)
        finally:
            temporary.unlink(missing_ok=True)
        return True, "Backup created."
    except (BackupError, OSError, ValueError, tarfile.TarError) as exc:
        return False, f"Could not create the backup: {exc}"


def stage_restore(data: bytes) -> None:
    """Atomically stage one bounded upload at the helper's fixed input path."""
    if not data or len(data) > MAX_ARCHIVE_BYTES:
        raise BackupError("The backup file is empty or too large.")
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=STATE_DIR, prefix=".restore-")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, RESTORE_PATH)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _valid_name(name: str) -> bool:
    parts = Path(name).parts
    return (
        bool(name)
        and not name.startswith("/")
        and "\x00" not in name
        and len(name.encode("utf-8")) <= MAX_PATH_BYTES
        and not any(part in ("", ".", "..") for part in parts)
    )


def _validated() -> Tuple[Dict[str, Any], List[tarfile.TarInfo]]:
    """Validate structure, scope, compatibility, sizes, and every file digest."""
    try:
        if RESTORE_PATH.stat().st_size > MAX_ARCHIVE_BYTES:
            raise BackupError("The backup file is too large.")
        with tarfile.open(RESTORE_PATH, "r:gz") as archive:
            members = archive.getmembers()
            if len(members) > MAX_MEMBERS:
                raise BackupError("The backup contains too many entries.")
            names = set()
            by_name: Dict[str, tarfile.TarInfo] = {}
            total = 0
            for member in members:
                if not _valid_name(member.name) or member.name in names:
                    raise BackupError("The backup contains an unsafe or duplicate path.")
                names.add(member.name)
                if member.name != "manifest.json":
                    parts = Path(member.name).parts
                    if len(parts) < 2 or parts[0] != "data" or parts[1] not in INCLUDED_ROOTS:
                        raise BackupError("The backup contains unexpected data.")
                if not (member.isdir() or member.isreg()):
                    raise BackupError("The backup contains an unsupported entry.")
                total += member.size
                if total > MAX_EXPANDED_BYTES:
                    raise BackupError("The backup expands too large.")
                by_name[member.name] = member

            manifest_member = by_name.get("manifest.json")
            if manifest_member is None or manifest_member.size > 64 * 1024:
                raise BackupError("The backup manifest is missing.")
            source = archive.extractfile(manifest_member)
            if source is None:
                raise BackupError("The backup manifest is unreadable.")
            manifest = json.loads(source.read().decode("utf-8"))
            if (
                manifest.get("format") != FORMAT_VERSION
                or tuple(manifest.get("included_roots", [])) != INCLUDED_ROOTS
            ):
                raise BackupError("Unsupported backup format or scope.")
            for root in INCLUDED_ROOTS:
                root_member = by_name.get(f"data/{root}")
                if root_member is None or not root_member.isdir():
                    raise BackupError(f"The backup is missing the {root} data directory.")
            schema = manifest.get("persistent_schema", {})
            if (
                not isinstance(schema, dict)
                or int(schema.get("minimum_reader_schema", 999)) > persistent_config.CURRENT_SCHEMA
            ):
                raise BackupError("This firmware cannot read the backup schema.")

            records = manifest.get("files")
            if not isinstance(records, list):
                raise BackupError("The backup manifest has no file inventory.")
            expected = {record["path"]: record for record in records}
            if len(expected) != len(records):
                raise BackupError("The backup manifest contains duplicate files.")
            actual = {
                name: member
                for name, member in by_name.items()
                if member.isreg() and name != "manifest.json"
            }
            if set(expected) != set(actual):
                raise BackupError("The backup file list does not match its contents.")
            for name, member in actual.items():
                source = archive.extractfile(member)
                if source is None:
                    raise BackupError("The backup contains an unreadable file.")
                data = source.read()
                record = expected[name]
                if len(data) != record.get("size") or hashlib.sha256(
                    data
                ).hexdigest() != record.get("sha256"):
                    raise BackupError("The backup failed its integrity check.")
            return manifest, members
    except BackupError:
        raise
    except (
        OSError,
        tarfile.TarError,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ValueError,
    ) as exc:
        raise BackupError("The selected file is not a valid radio backup.") from exc


def inspect_restore() -> Tuple[bool, str, Dict[str, Any]]:
    """Return bounded, non-secret metadata after complete archive validation."""
    try:
        manifest, members = _validated()
        return (
            True,
            "Backup validated.",
            {
                "created_at": manifest.get("created_at"),
                "radio_version": manifest.get("radio_version"),
                "files": sum(1 for member in members if member.isreg()) - 1,
            },
        )
    except BackupError as exc:
        return False, str(exc), {}


def _set_permissions(root: Path) -> None:
    web_owned = root.name == "radio"
    for current, dirs, files in os.walk(root):
        current_path = Path(current)
        os.chmod(current_path, 0o755 if web_owned else 0o700)
        if os.geteuid() == 0:
            os.chown(current_path, 601 if web_owned else 0, 601 if web_owned else 0)
        for name in dirs:
            path = current_path / name
            os.chmod(path, 0o755 if web_owned else 0o700)
            if os.geteuid() == 0:
                os.chown(path, 601 if web_owned else 0, 601 if web_owned else 0)
        for name in files:
            path = current_path / name
            helper_owned = (
                web_owned
                and path.parent == root
                and name
                in {
                    "wifi-country",
                    "wlan-static.env",
                }
            )
            secret = name == "admin.secret" or not web_owned or helper_owned
            os.chmod(path, 0o600 if secret else 0o644)
            if os.geteuid() == 0:
                owner = 0 if helper_owned or not web_owned else 601
                os.chown(path, owner, owner)


def _extract_validated(staging: Path) -> None:
    with tarfile.open(RESTORE_PATH, "r:gz") as archive:
        for member in archive.getmembers():
            if member.name == "manifest.json":
                continue
            target = staging / member.name
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                raise BackupError("The backup contains an unreadable file.")
            with target.open("wb") as output:
                shutil.copyfileobj(source, output)


def apply_restore() -> Tuple[bool, str]:
    """Replace all selected roots, rolling back on any activation failure."""
    staging: Optional[Path] = None
    rollback: Optional[Path] = None
    try:
        _validated()
        staging = Path(tempfile.mkdtemp(dir=STATE_DIR, prefix="extract-"))
        rollback = Path(tempfile.mkdtemp(dir=STATE_DIR, prefix="rollback-"))
        _extract_validated(staging)
        for script in SERVICE_SCRIPTS:
            if Path(script).exists():
                subprocess.run([script, "stop"], check=False, timeout=30)

        replaced = []
        try:
            for root in INCLUDED_ROOTS:
                current = DATA_DIR / root
                saved = rollback / root
                incoming = staging / "data" / root
                os.replace(current, saved)
                try:
                    os.replace(incoming, current)
                except Exception:
                    os.replace(saved, current)
                    raise
                replaced.append(root)
                _set_permissions(current)
            persistent_config.prepare()
            persistent_config.render_runtime()
        except Exception:
            for root in reversed(replaced):
                current = DATA_DIR / root
                failed = staging / f"failed-{root}"
                os.replace(current, failed)
                os.replace(rollback / root, current)
            raise
        RESTORE_PATH.unlink(missing_ok=True)
        return True, "Backup restored. Reboot the radio to activate all restored settings."
    except (BackupError, OSError, subprocess.SubprocessError, ValueError) as exc:
        return False, f"Could not restore the backup: {exc}"
    finally:
        if staging:
            shutil.rmtree(staging, ignore_errors=True)
        if rollback:
            shutil.rmtree(rollback, ignore_errors=True)


def cancel_restore() -> Tuple[bool, str]:
    try:
        RESTORE_PATH.unlink(missing_ok=True)
        return True, "Staged backup removed."
    except OSError:
        return False, "Could not remove the staged backup."
