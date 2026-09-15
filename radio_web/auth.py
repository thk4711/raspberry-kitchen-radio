"""Authentication primitives for the web administration interface.

Standard-library only: PBKDF2-HMAC password
hashing with a random salt, secure random session tokens, per-session CSRF
tokens, and a small login rate limiter. The admin password and the SSH/root
password are separate; this module never touches either the shell or the root
account, and **never logs the plaintext password**.

Design notes:

* Sessions and rate-limit state are **in-memory** and per-process. That fits the
  appliance: a single ``radio-web`` process, and sessions that naturally clear on
  restart/reboot (mirroring the volatile-by-design ``/tmp`` logs). No database.
* The stored secret format is ``pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>``
  so the parameters travel with the hash and can be raised later without a
  migration.
* All secret comparisons use :func:`hmac.compare_digest` to avoid timing leaks.
"""

import hashlib
import hmac
import logging
import os
import secrets
import threading
import time
from typing import Dict, List, Optional

from . import config_store

logger = logging.getLogger("radio_web.auth")

# --- Password hashing -------------------------------------------------------

# PBKDF2 parameters. SHA-256 with a generous iteration count; the Pi 3A+ is
# modest but a login is rare, so a few hundred ms is acceptable.
_PBKDF2_ALGORITHM = "sha256"
_PBKDF2_ITERATIONS = 200_000
_SALT_BYTES = 16
_HASH_PREFIX = "pbkdf2_sha256"

# Minimum admin password length. Kept low enough not to annoy on a home LAN but
# non-trivial; the UI states credentials travel in the clear over plain HTTP.
MIN_PASSWORD_LENGTH = 8


def hash_password(password: str, *, iterations: int = _PBKDF2_ITERATIONS) -> str:
    """Hash ``password`` with PBKDF2-HMAC-SHA256 and a fresh random salt.

    Returns the serialized ``pbkdf2_sha256$<iters>$<salt_hex>$<hash_hex>``
    string suitable for storing in ``admin.secret``. Never logs ``password``.
    """
    salt = secrets.token_bytes(_SALT_BYTES)
    derived = hashlib.pbkdf2_hmac(_PBKDF2_ALGORITHM, password.encode("utf-8"), salt, iterations)
    return f"{_HASH_PREFIX}${iterations}${salt.hex()}${derived.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Return True iff ``password`` matches the serialized ``stored`` hash.

    A malformed ``stored`` value returns False rather than raising, so a
    corrupt secret file simply denies access. Comparison is constant-time.
    """
    try:
        prefix, iter_text, salt_hex, hash_hex = stored.strip().split("$")
        if prefix != _HASH_PREFIX:
            return False
        iterations = int(iter_text)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except (ValueError, AttributeError):
        return False
    derived = hashlib.pbkdf2_hmac(_PBKDF2_ALGORITHM, password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(derived, expected)


# --- Admin secret file ------------------------------------------------------


def is_configured() -> bool:
    """Return True iff the admin password has been set (secret file exists)."""
    return os.path.isfile(config_store.admin_secret_path())


def set_password(password: str) -> None:
    """Validate and store ``password`` as a PBKDF2 hash in ``admin.secret``.

    Written atomically at mode ``0600``. Raises
    :class:`ValueError` if the password is shorter than
    :data:`MIN_PASSWORD_LENGTH`. Never logs the plaintext.
    """
    if password is None or len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    serialized = hash_password(password)
    config_store.atomic_write(
        config_store.admin_secret_path(), serialized + "\n", config_store.SECRET_MODE
    )
    logger.info("Admin password set")


def check_password(password: str) -> bool:
    """Return True iff ``password`` matches the stored admin secret.

    Returns False when no password has been configured yet.
    """
    stored = config_store.read_text(config_store.admin_secret_path())
    if not stored:
        return False
    return verify_password(password, stored)


# --- Sessions + CSRF --------------------------------------------------------

# Session lifetime (seconds). A logged-in admin session survives modest idle
# periods but not indefinitely.
SESSION_TTL_SECONDS = 60 * 60  # 1 hour


class Session:
    """A single authenticated session with its own CSRF token."""

    __slots__ = ("token", "csrf_token", "expires_at")

    def __init__(self, token: str, csrf_token: str, expires_at: float) -> None:
        self.token = token
        self.csrf_token = csrf_token
        self.expires_at = expires_at


class SessionStore:
    """Thread-safe, in-memory session store with TTL expiry.

    ``ThreadingHTTPServer`` serves each request on its own thread, so all
    mutating access is guarded by a lock. Expired sessions are swept lazily on
    access and can be swept eagerly via :meth:`sweep`.
    """

    def __init__(self, ttl_seconds: float = SESSION_TTL_SECONDS, clock=time.monotonic) -> None:
        self._ttl = ttl_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._sessions: Dict[str, Session] = {}

    def create(self) -> Session:
        """Create and store a new session; return it."""
        token = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(32)
        session = Session(token, csrf_token, self._clock() + self._ttl)
        with self._lock:
            self._sessions[token] = session
        return session

    def validate(self, token: Optional[str]) -> Optional[Session]:
        """Return the live session for ``token``, or ``None`` if absent/expired."""
        if not token:
            return None
        now = self._clock()
        with self._lock:
            session = self._sessions.get(token)
            if session is None:
                return None
            if session.expires_at <= now:
                del self._sessions[token]
                return None
            return session

    def destroy(self, token: Optional[str]) -> None:
        """Remove ``token`` if present (idempotent)."""
        if not token:
            return
        with self._lock:
            self._sessions.pop(token, None)

    def sweep(self) -> None:
        """Drop all expired sessions."""
        now = self._clock()
        with self._lock:
            expired = [t for t, s in self._sessions.items() if s.expires_at <= now]
            for token in expired:
                del self._sessions[token]


def check_csrf(session: Optional[Session], token: Optional[str]) -> bool:
    """Return True iff ``token`` matches ``session``'s CSRF token (constant-time)."""
    if session is None or not token:
        return False
    return hmac.compare_digest(session.csrf_token, token)


# --- Login rate limiting ----------------------------------------------------


class RateLimiter:
    """Fixed-window login rate limiter keyed by client identifier (IP).

    After ``max_attempts`` failures within ``window_seconds``, the key is
    locked out until the window elapses. A success clears the key. The clock is
    injectable so tests do not need to sleep.
    """

    def __init__(
        self,
        max_attempts: int = 5,
        window_seconds: float = 300.0,
        clock=time.monotonic,
    ) -> None:
        self._max = max_attempts
        self._window = window_seconds
        self._clock = clock
        self._lock = threading.Lock()
        # key -> [window_start, attempt_count]
        self._state: Dict[str, List[float]] = {}

    def is_blocked(self, key: str) -> bool:
        """Return True iff ``key`` is currently locked out."""
        now = self._clock()
        with self._lock:
            entry = self._state.get(key)
            if entry is None:
                return False
            start, count = entry
            if now - start >= self._window:
                # Window elapsed; the caller's next attempt starts fresh.
                del self._state[key]
                return False
            return count >= self._max

    def register_failure(self, key: str) -> None:
        """Record a failed attempt for ``key``."""
        now = self._clock()
        with self._lock:
            entry = self._state.get(key)
            if entry is None or now - entry[0] >= self._window:
                self._state[key] = [now, 1]
            else:
                entry[1] += 1

    def register_success(self, key: str) -> None:
        """Clear any accumulated failures for ``key``."""
        with self._lock:
            self._state.pop(key, None)


# --- Route gating -----------------------------------------------------------


def require_auth(session: Optional[Session]) -> bool:
    """Return True iff ``session`` is a valid, non-expired authenticated session.

    The gate for every mutating route (Phases 5+). Phase 4 wires this into the
    server so future POST handlers can call it uniformly; the read-only
    dashboard stays public.
    """
    return session is not None
