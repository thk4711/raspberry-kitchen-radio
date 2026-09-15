"""``python3 -m radio_web`` entry point used by the ``S80radio-web`` init script.

Configures logging from the ``RADIO_LOG_LEVEL`` env var (mirroring ``radio.py``)
and starts the read-only dashboard server on port 8080, bound to the wlan0 LAN
address and to loopback (see :mod:`radio_web.server`).
"""

import logging
import os

from . import DEFAULT_PORT, server


def _resolve_log_level(default: int = logging.INFO) -> int:
    raw = os.environ.get("RADIO_LOG_LEVEL")
    if not raw:
        return default
    raw = raw.strip()
    if raw.isdigit():
        return int(raw)
    return getattr(logging, raw.upper(), default)


def main() -> None:
    logging.basicConfig(level=_resolve_log_level())
    port = int(os.environ.get("RADIO_WEB_PORT", str(DEFAULT_PORT)))
    server.serve(port=port, forever=True)


if __name__ == "__main__":
    main()
