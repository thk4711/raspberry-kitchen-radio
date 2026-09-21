"""Server-rendered, HTML-escaped templates for the web administration UI.

Pages are built from Python strings without a template-engine dependency, with
:func:`html.escape` applied to **every** dynamic value. The shared stylesheet
and radio mark are local, explicitly allowlisted assets; no page needs a CDN,
web font, or JavaScript to function.
"""

import html
import re
from typing import Any, Optional

_NAV_ITEMS = (
    ("PiSonic", "/", "Dashboard"),
    ("Radio stations", "/stations", "Stations"),
    ("Music sources", "/sources", "Sources"),
    ("Display", "/settings", "Display"),
    ("Audio", "/audio-hardware", "Audio"),
    ("WiFi & network", "/network", "Network"),
    ("Device settings", "/device", "Device"),
    ("Maintenance", "/maintenance", "Maintenance"),
)

_PLAIN_HTTP_NOTICE = (
    '<p class="note">This connection is not encrypted (plain HTTP). '
    "Only use it on your trusted home network.</p>"
)


def _esc(value: Any) -> str:
    """HTML-escape any value, rendering ``None`` as an em dash placeholder."""
    if value is None:
        return "&mdash;"
    return html.escape(str(value))


def _fmt_uptime(seconds: Optional[float]) -> str:
    if seconds is None:
        return "&mdash;"
    total = int(seconds)
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours or days:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    return _esc(" ".join(parts))


def _fmt_bytes(num: Optional[int]) -> str:
    if num is None:
        return "&mdash;"
    value = float(num)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return _esc(f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}")
        value /= 1024
    return _esc(num)


def _fmt_seconds_ago(seconds: Optional[float]) -> str:
    if seconds is None:
        return "&mdash;"
    return _esc(f"{seconds:.0f}s ago")


def _page(title: str, body: str, *, script: Optional[str] = None) -> str:
    """Wrap page ``body`` in the shared responsive application shell."""
    page_class = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    nav = []
    for item_title, path, label in _NAV_ITEMS:
        current = ' aria-current="page"' if title == item_title else ""
        nav.append(f'<a href="{path}"{current}>{_esc(label)}</a>')
    script_tag = f'<script src="{_esc(script)}" defer></script>' if script else ""
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="theme-color" content="#132238">'
        f"<title>{_esc(title)}</title>"
        '<link rel="icon" href="/static/radio.svg?v=1" type="image/svg+xml">'
        '<link rel="stylesheet" href="/static/app.css?v=17">'
        f"{script_tag}"
        '</head><body><a class="skip-link" href="#content">Skip to content</a>'
        '<header class="site-header"><div class="header-inner">'
        '<a class="brand" href="/">'
        '<img src="/static/radio.svg?v=1" alt="" width="38" height="38">'
        '<span class="brand-copy">PiSonic<small>Control panel</small></span>'
        '</a><nav class="primary-nav" aria-label="Main navigation">'
        f"{''.join(nav)}"
        "</nav></div></header>"
        f'<main id="content" class="page page-{page_class}">{body}</main>'
        '<footer class="site-footer">PiSonic &middot; Local control panel</footer>'
        "</body></html>"
    )


def csrf_field(token: str) -> str:
    """Return a hidden CSRF input for embedding in a state-changing form."""
    return f'<input type="hidden" name="csrf_token" value="{_esc(token)}">'


def _error_block(error: Optional[str]) -> str:
    if not error:
        return ""
    return f'<p class="warn">{_esc(error)}</p>'


def _flash_block(message: Optional[str], error: Optional[str]) -> str:
    """Render an optional success message and/or error banner."""
    parts = []
    if message:
        parts.append(f'<p class="ok">{_esc(message)}</p>')
    if error:
        parts.append(f'<p class="warn">{_esc(error)}</p>')
    return "".join(parts)
