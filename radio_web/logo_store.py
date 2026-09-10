"""Managed station-logo storage and image normalization for the web UI."""

import hashlib
import os
import re
import tempfile
from io import BytesIO
from typing import List, Optional

from PIL import Image, UnidentifiedImageError

from . import config_store, validators

LOGO_DIRECTORY = "logos"
MAX_UPLOAD_BYTES = 4 * 1024 * 1024
MAX_IMAGE_PIXELS = 4_000_000
MAX_DIMENSION = 512
LOGO_MODE = 0o644

# Longest derived logo *stem* (before the ``.png`` extension) we keep, so the
# whole filename stays comfortably under validators.MAX_LOGO_LENGTH even after a
# collision suffix is appended. The stem comes from the user's original upload
# name, sanitized to the same character class the filename whitelist accepts.
MAX_NAME_STEM = 96
_UNSAFE_NAME_CHARS = re.compile(r"[^A-Za-z0-9._-]+")
_COLLAPSE_DASHES = re.compile(r"-{2,}")

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILTIN_LOGO_DIR = os.path.join(_REPO_ROOT, "lib", "mpd_service", "logos")


def managed_logo_dir() -> str:
    """Return the web-writable directory containing normalized uploaded logos."""
    return os.path.join(config_store.managed_dir(), LOGO_DIRECTORY)


def _valid_image(path: str) -> bool:
    try:
        with Image.open(path) as image:
            image.verify()
        return True
    except (OSError, UnidentifiedImageError):
        return False


def available_logos() -> List[str]:
    """Return sorted valid logo names, with managed files taking precedence."""
    names = set()
    for directory in (BUILTIN_LOGO_DIR, managed_logo_dir()):
        try:
            entries = os.listdir(directory)
        except OSError:
            continue
        for name in entries:
            try:
                validators.validate_logo_filename(name)
            except ValueError:
                continue
            path = os.path.join(directory, name)
            if os.path.isfile(path) and _valid_image(path):
                names.add(name)
    return sorted(names, key=str.casefold)


def resolve_logo_path(filename: str) -> Optional[str]:
    """Return the managed-first path for a configured logo filename."""
    try:
        name = validators.validate_logo_filename(filename)
    except ValueError:
        return None
    if not name:
        return None
    for directory in (managed_logo_dir(), BUILTIN_LOGO_DIR):
        path = os.path.join(directory, name)
        if os.path.isfile(path):
            return path
    return None


def _atomic_write_bytes(path: str, data: bytes) -> None:
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    tmp_path: Optional[str] = None
    try:
        fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".tmp-")
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_path, LOGO_MODE)
        os.replace(tmp_path, path)
        tmp_path = None
    finally:
        if tmp_path is not None and os.path.exists(tmp_path):
            os.unlink(tmp_path)


def save_upload(data: bytes, original_name: str = "") -> str:
    """Validate, downscale and persist an uploaded PNG/JPEG as a PNG logo.

    The stored file keeps a *human-readable* name derived from the user's
    original upload name (e.g. ``MDR JUMP Logo.PNG`` becomes
    ``mdr-jump-logo.png``) so the Logo dropdown is legible. The name is
    sanitized to the tightly whitelisted filename character class so it can
    never escape the logo directory. When no usable name is supplied we fall
    back to the content-addressed ``upload-<hash>.png``.

    Distinct images that sanitize to the *same* name never clobber each other:
    a short content hash is appended so a second, different logo becomes e.g.
    ``logo-1a2b3c4d.png``. Re-uploading the byte-identical file is idempotent
    (it resolves to the same existing name).
    """
    if not data:
        raise ValueError("Choose an image file to upload.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError("Logo image must be at most 4 MiB.")
    try:
        with Image.open(BytesIO(data)) as source:
            if source.format not in ("PNG", "JPEG"):
                raise ValueError("Logo must be a PNG or JPEG image.")
            width, height = source.size
            if width < 1 or height < 1 or width * height > MAX_IMAGE_PIXELS:
                raise ValueError("Logo image dimensions are too large.")
            image = source.convert("RGBA")
            image.thumbnail((MAX_DIMENSION, MAX_DIMENSION), Image.Resampling.LANCZOS)
            output = BytesIO()
            image.save(output, format="PNG", optimize=True)
    except (OSError, UnidentifiedImageError) as exc:
        raise ValueError("Logo is not a valid PNG or JPEG image.") from exc
    normalized = output.getvalue()
    # Hash the *normalized* PNG (what we actually store) so an idempotent
    # re-upload of the same source keeps its friendly name, and only a genuinely
    # different image gets a disambiguating suffix.
    digest = hashlib.sha256(normalized).hexdigest()[:16]
    filename = _pick_filename(original_name, digest)
    _atomic_write_bytes(os.path.join(managed_logo_dir(), filename), normalized)
    return filename


def _derive_stem(original_name: str) -> str:
    """Return a safe lowercase filename stem from a user's upload name.

    Takes only the basename, drops any extension, lowercases, replaces every
    character outside the whitelist with ``-``, collapses runs of ``-`` and
    trims leading/trailing ``-`` / ``.``. Returns ``""`` when nothing usable
    remains (e.g. a name of only unsafe characters), so the caller falls back
    to the content-addressed name.
    """
    base = os.path.basename((original_name or "").strip())
    # Strip a single trailing extension; the stored file is always PNG.
    stem = base.rsplit(".", 1)[0] if "." in base else base
    stem = _UNSAFE_NAME_CHARS.sub("-", stem.lower())
    stem = _COLLAPSE_DASHES.sub("-", stem).strip("-.")
    return stem[:MAX_NAME_STEM].strip("-.")


def _pick_filename(original_name: str, digest: str) -> str:
    """Choose the stored filename, avoiding collisions with different images.

    Prefers a friendly ``<stem>.png`` from the original name. If that name is
    already taken by a *different* image, a short content-hash suffix is added
    (``<stem>-<hash8>.png``); because the suffix is derived from the image
    bytes, re-uploading the identical file always resolves to the same name.
    Falls back to ``upload-<digest>.png`` when no usable stem is derived.
    """
    stem = _derive_stem(original_name)
    if not stem:
        return f"upload-{digest}.png"
    candidate = f"{stem}.png"
    try:
        validators.validate_logo_filename(candidate)
    except ValueError:
        return f"upload-{digest}.png"
    existing = os.path.join(managed_logo_dir(), candidate)
    if os.path.isfile(existing) and _png_digest(existing) != digest:
        # A *different* image already owns the friendly name; disambiguate with
        # the content hash so we never overwrite an unrelated logo.
        candidate = f"{stem}-{digest[:8]}.png"
    return candidate


def _png_digest(path: str) -> str:
    """Return the short sha256 of a stored file (best-effort, empty on error).

    Note this hashes the *normalized* PNG on disk, so it matches ``digest``
    only when the same normalized bytes were previously written — good enough
    to keep an idempotent re-upload on its friendly name while still forcing a
    suffix for a genuinely different image.
    """
    try:
        with open(path, "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest()[:16]
    except OSError:
        return ""


