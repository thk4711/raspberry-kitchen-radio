"""Allowlisted static assets for the web administration interface.

The web server deliberately has no general-purpose static-file or directory
route.  Only the files listed in :data:`ASSETS` can be returned, which keeps
path traversal and accidental source/config exposure out of the request path.
"""

from pathlib import Path
from typing import Dict, Optional, Tuple

Asset = Tuple[str, bytes]

_STATIC_DIR = Path(__file__).with_name("static")

# URL path -> (file name, MIME type). Additions must be explicit.
ASSETS: Dict[str, Tuple[str, str]] = {
    "/static/app.css": ("app.css", "text/css; charset=utf-8"),
    "/static/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/static/radio.svg": ("radio.svg", "image/svg+xml"),
    "/static/PiSonic-Logo.svg": ("PiSonic-Logo.svg", "image/svg+xml"),
    "/static/PiSonic-Logo.png": ("PiSonic-Logo.png", "image/png"),
}


def get(path: str) -> Optional[Asset]:
    """Return ``(content_type, data)`` for an allowlisted URL, else ``None``."""
    entry = ASSETS.get(path)
    if entry is None:
        return None
    filename, content_type = entry
    try:
        return content_type, (_STATIC_DIR / filename).read_bytes()
    except OSError:
        # A malformed/incomplete installation should render as a normal 404,
        # not expose a traceback or make the web process fail to start.
        return None
