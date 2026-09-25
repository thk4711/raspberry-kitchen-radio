"""Shared request and response contracts for web route modules."""

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple, Union

from . import (
    auth,
)
from .auth import Session

# A response: (status, content_type, body, extra_headers). ``body`` is text for
# HTML/plain responses and raw ``bytes`` for binary downloads (diagnostics).
# ``extra_headers`` is a list of (name, value) pairs for cookies, redirects, etc.
Response = Tuple[int, str, Union[str, bytes], List[Tuple[str, str]]]


@dataclass
class UploadedFile:
    """One uploaded form file; routes use only its bounded in-memory bytes."""

    filename: str
    content_type: str
    data: bytes


_HTML = "text/html; charset=utf-8"
_TEXT = "text/plain; charset=utf-8"
_JSON = "application/json; charset=utf-8"

# Cookie name for the session token.
SESSION_COOKIE = "radio_session"


@dataclass
class Request:
    """Per-request context handed to a route handler.

    Attributes:
        method: Upper-case HTTP method.
        path: Path component only (query string stripped by the server).
        form: Parsed ``application/x-www-form-urlencoded`` fields (last value
            wins). Empty for GET.
        query: Parsed query-string fields (last value wins). Used by GET
            handlers to surface a PRG flash message (e.g. ``?msg=saved``).
        media_type: Lower-case request media type without parameters. Empty
            when the Content-Type header is missing or ambiguous.
        body: Raw bounded request body.
        client_ip: Remote address, used as the rate-limit key.
        session: The validated session, or ``None`` when unauthenticated.
        sessions: The shared session store (so login/logout can mutate it).
        rate_limiter: The shared login rate limiter.
    """

    method: str
    path: str
    form: Dict[str, str] = field(default_factory=dict)
    files: Dict[str, UploadedFile] = field(default_factory=dict)
    query: Dict[str, str] = field(default_factory=dict)
    media_type: str = ""
    body: bytes = b""
    client_ip: str = ""
    session: Optional[Session] = None
    sessions: Optional[auth.SessionStore] = None
    rate_limiter: Optional[auth.RateLimiter] = None


Handler = Callable[[Request], Response]


def _ok(body: str, content_type: str = _HTML) -> Response:
    return 200, content_type, body, []


def _redirect(location: str, extra_headers: Optional[List[Tuple[str, str]]] = None) -> Response:
    # 303 See Other: the Post/Redirect/Get idiom.
    headers = list(extra_headers or [])
    headers.append(("Location", location))
    body = f'<p>Redirecting to <a href="{location}">{location}</a>.</p>'
    return 303, _HTML, body, headers


def _set_cookie_header(token: str) -> Tuple[str, str]:
    # HttpOnly + SameSite=Strict. No Secure flag in v1
    # because the prototype serves plain HTTP on the trusted LAN.
    return (
        "Set-Cookie",
        f"{SESSION_COOKIE}={token}; HttpOnly; SameSite=Strict; Path=/",
    )


def _clear_cookie_header() -> Tuple[str, str]:
    return (
        "Set-Cookie",
        f"{SESSION_COOKIE}=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0",
    )
