"""Managed configuration store: paths and the atomic-write recipe.

The web UI never mutates the read-mostly application image. Instead it writes
managed override files under a dedicated directory.
The directory is :data:`MANAGED_CONFIG_DIR`, resolved from the ``RADIO_CONFIG_DIR``
environment variable (default ``/etc/radio``) so it matches the value the player
uses in :mod:`lib.utilities` and so tests can point it at a temporary directory.

:func:`atomic_write` implements the durable, never-partial write from
Atomic write: a temp file **in the same directory**, flush +
``fsync`` it, set the exact permission bits, then ``os.replace`` it over the
target. Phase 4 uses it for ``admin.secret`` (mode ``0600``); later phases reuse
it for the ``*.ini`` files (mode ``0644``).
"""

import os
import tempfile
from typing import Optional

# Managed override directory. Overridable via RADIO_CONFIG_DIR so it stays in
# lock-step with lib.utilities.MANAGED_CONFIG_DIR and so tests avoid touching
# /etc. Read once at import, exactly like the player side.
MANAGED_CONFIG_DIR = os.environ.get("RADIO_CONFIG_DIR", "/etc/radio")

# PBKDF2 hash + salt for the web admin password (0600). See radio_web.auth.
ADMIN_SECRET_FILENAME = "admin.secret"

# Permission bits for the two managed file classes.
CONFIG_MODE = 0o644
SECRET_MODE = 0o600


def managed_dir() -> str:
    """Return the managed configuration directory (evaluated per call).

    Read live rather than caching the module-level constant so a test that sets
    ``RADIO_CONFIG_DIR`` (or monkeypatches :data:`MANAGED_CONFIG_DIR`) takes
    effect without re-importing the module.
    """
    return MANAGED_CONFIG_DIR


def admin_secret_path() -> str:
    """Return the absolute path to the admin password secret file."""
    return os.path.join(managed_dir(), ADMIN_SECRET_FILENAME)


def atomic_write(path: str, data: str, mode: int = CONFIG_MODE) -> None:
    """Atomically write ``data`` to ``path`` with permission ``mode``.

    Implements the atomic-write recipe: a temporary file in the
    *same directory* (so ``os.replace`` is a same-filesystem atomic rename),
    flushed and ``fsync``-ed for durability, ``chmod``-ed to ``mode`` before the
    swap so the target never appears with loose permissions, then swapped in.
    The parent directory is created if missing. On any failure the temp file is
    removed so no partial artefact is left behind.

    Args:
        path: Absolute destination path.
        data: File contents (UTF-8 text).
        mode: Octal permission bits for the destination (default ``0644``).
    """
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    tmp_path: Optional[str] = None
    try:
        fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".tmp-")
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_path, mode)
        os.replace(tmp_path, path)
        tmp_path = None
    finally:
        if tmp_path is not None and os.path.exists(tmp_path):
            os.unlink(tmp_path)


def read_text(path: str) -> Optional[str]:
    """Return the contents of ``path`` as text, or ``None`` if it is absent."""
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        return None
