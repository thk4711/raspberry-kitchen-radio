"""Managed station-logo storage and image normalization for the web UI."""

import hashlib
import os
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


def save_upload(data: bytes) -> str:
    """Validate, downscale and persist an uploaded PNG/JPEG as a PNG logo."""
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
    digest = hashlib.sha256(data).hexdigest()[:16]
    filename = f"upload-{digest}.png"
    _atomic_write_bytes(os.path.join(managed_logo_dir(), filename), output.getvalue())
    return filename
