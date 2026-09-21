"""PiSonic web administration interface (``radio_web``).

A small, dependency-free administration UI served by a separate, unprivileged
process from the player (``radio.py``). It ships a **read-only dashboard** and,
in later phases, authenticated management pages.

The whole package uses the Python standard library only — no web framework, no
database, no Node.js. HTML is server-rendered and every displayed value is
HTML-escaped. See :mod:`radio_web.server` for the HTTP server and the LAN bind
policy, and :mod:`radio_web.system_status` for the read-only status collectors.
"""

# Default TCP port for the web UI. Bound to the wlan0 LAN address and to
# 127.0.0.1 (loopback for on-box testing); never to a public interface.
DEFAULT_PORT = 8080

# Reject request bodies larger than this to bound memory. Logo uploads need room
# for a 4 MiB image plus small multipart form overhead.
MAX_REQUEST_BODY = 5 * 1024 * 1024
