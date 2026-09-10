"""Short-lived, one-use authorization grants for firmware uploads."""

import secrets
import threading
import time
from typing import Dict, Optional, Tuple

GRANT_TTL_SECONDS = 10 * 60

_lock = threading.Lock()
_grants: Dict[str, Tuple[str, float, str]] = {}


def issue(session_token: str, tracking_token: str, *, clock=time.monotonic) -> str:
    """Issue a random grant bound to one authenticated web session."""
    grant = secrets.token_urlsafe(32)
    expires = clock() + GRANT_TTL_SECONDS
    with _lock:
        _grants[grant] = (session_token, expires, tracking_token)
    return grant


def consume(grant: Optional[str], session_token: str, *, clock=time.monotonic) -> Optional[str]:
    """Consume ``grant`` and return its tracking token when it is valid."""
    if not grant:
        return None
    now = clock()
    with _lock:
        expired = [key for key, (_session, expiry, _tracking) in _grants.items() if expiry <= now]
        for key in expired:
            del _grants[key]
        value = _grants.pop(grant, None)
    if value is None or value[0] != session_token or value[1] <= now:
        return None
    return value[2]


def clear() -> None:
    """Clear grants (used by tests and process shutdown)."""
    with _lock:
        _grants.clear()
