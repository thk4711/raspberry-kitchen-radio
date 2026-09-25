"""Controller-owned Unix socket server for local playback commands."""

from __future__ import annotations

import grp
import logging
import os
import socket
import socketserver
import threading
from typing import Any, Callable, Optional

from radio_web import player_protocol

logger = logging.getLogger(__name__)

ActionHandler = Callable[[str], Any]


def _response(ok: bool, code: str, message: str, source: str = "") -> bytes:
    return player_protocol.encode_response(ok, code, message, source)


class _Handler(socketserver.StreamRequestHandler):
    """Handle exactly one bounded, newline-terminated request."""

    def handle(self) -> None:
        server = self.server
        self.request.settimeout(server.request_timeout)  # type: ignore[attr-defined]
        try:
            raw = self.rfile.readline(player_protocol.MAX_MESSAGE_BYTES + 1)
        except (socket.timeout, TimeoutError):
            self._write(_response(False, "request_timeout", "Playback request timed out"))
            return

        if len(raw) > player_protocol.MAX_MESSAGE_BYTES:
            self._write(_response(False, "request_too_large", "Playback request is too large"))
            return
        if not raw.endswith(b"\n"):
            self._write(_response(False, "invalid_request", "Playback request is not terminated"))
            return
        try:
            action = player_protocol.decode_request(raw)
        except (ValueError, UnicodeDecodeError):
            self._write(_response(False, "invalid_request", "Malformed playback request"))
            return
        if action not in player_protocol.ACTION_IDS:
            self._write(_response(False, "unknown_action", "Unknown playback action"))
            return

        try:
            result = server.action_handler(action)  # type: ignore[attr-defined]
            raw_response = _response(result.ok, result.code, result.message, result.source)
        except Exception as exc:
            logger.error("Playback control dispatch failed: %s", exc)
            raw_response = _response(False, "internal_error", "Playback command failed")
        self._write(raw_response)

    def _write(self, raw: bytes) -> None:
        try:
            self.wfile.write(raw)
        except OSError:
            pass


class _Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        path: str,
        action_handler: ActionHandler,
        request_timeout: float,
    ) -> None:
        self.action_handler = action_handler
        self.request_timeout = request_timeout
        super().__init__(path, _Handler)


class PlaybackControlServer:
    """Own the playback listener thread and its socket-file lifecycle."""

    def __init__(
        self,
        action_handler: ActionHandler,
        socket_path: Optional[str] = None,
        group_name: str = "radio-web",
        request_timeout: float = player_protocol.SOCKET_TIMEOUT_SECONDS,
    ) -> None:
        self._action_handler = action_handler
        self.socket_path = socket_path or player_protocol.SOCKET_PATH
        self._group_name = group_name
        self._request_timeout = request_timeout
        self._server: Optional[_Server] = None
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Create, secure, and start the local listener."""
        if self._server is not None:
            return
        try:
            os.unlink(self.socket_path)
        except FileNotFoundError:
            pass

        server: Optional[_Server] = None
        try:
            server = _Server(self.socket_path, self._action_handler, self._request_timeout)
            group_id = grp.getgrnam(self._group_name).gr_gid
            os.chown(self.socket_path, -1, group_id)
            os.chmod(self.socket_path, 0o660)
        except Exception:
            if server is not None:
                server.server_close()
            try:
                os.unlink(self.socket_path)
            except FileNotFoundError:
                pass
            raise

        thread = threading.Thread(
            target=server.serve_forever,
            name="playback-control",
            daemon=True,
        )
        self._server = server
        self._thread = thread
        thread.start()
        logger.info("Playback control listening on %s", self.socket_path)

    def close(self) -> None:
        """Stop the listener and remove its socket file."""
        server = self._server
        thread = self._thread
        self._server = None
        self._thread = None
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None:
            thread.join(timeout=player_protocol.SOCKET_TIMEOUT_SECONDS)
        try:
            os.unlink(self.socket_path)
        except FileNotFoundError:
            pass
