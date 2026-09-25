"""HTTP server for the web administration interface.

A standard-library :class:`~http.server.ThreadingHTTPServer`. Binds to the
``wlan0`` LAN address **and** to ``127.0.0.1`` on port 8080
The loopback bind always runs so the service is
reachable for on-box testing even when WiFi is down. A small monitor adds or
replaces the best-effort ``wlan0`` listener when WiFi obtains or changes its
address after this service starts. There is no directory browsing and no
arbitrary static-file route: every response comes from the explicit
:mod:`radio_web.routes` table.

Phase 4 adds authentication plumbing: the handler parses the session cookie and
(for POST) the ``application/x-www-form-urlencoded`` body, validates the session
against a shared :class:`radio_web.auth.SessionStore`, and passes both — plus a
shared login :class:`radio_web.auth.RateLimiter` — to the route handlers via a
:class:`radio_web.routes.Request`. Responses may carry ``Set-Cookie`` and
``Location`` headers (login/logout/redirects).
"""

import base64
import hashlib
import json
import logging
import socket
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, List, Optional
from urllib.parse import parse_qs, unquote, urlsplit

from . import (
    DEFAULT_PORT,
    MAX_REQUEST_BODY,
    actions,
    adc_store,
    auth,
    firmware_authorization,
    firmware_tracking,
    firmware_upload,
    routes,
)

logger = logging.getLogger("radio_web.server")

# Per-request socket timeout (seconds): a slow/idle client must not tie up a
# worker thread forever.
REQUEST_TIMEOUT = 15
WEBSOCKET_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

# WiFi starts asynchronously on the appliance. Recheck often enough that the UI
# appears promptly after DHCP, without polling aggressively on an idle radio.
LAN_RECONCILE_SECONDS = 1.0

# Shared, process-wide auth state. Sessions and login rate-limit counters live
# in memory for the lifetime of the process; they clear on
# restart/reboot, which suits the appliance.
SESSIONS = auth.SessionStore()
RATE_LIMITER = auth.RateLimiter()


def _websocket_text_frame(payload: str) -> bytes:
    """Encode one unmasked server-to-client RFC 6455 text frame."""
    data = payload.encode("utf-8")
    length = len(data)
    if length < 126:
        header = bytes((0x81, length))
    elif length <= 0xFFFF:
        header = bytes((0x81, 126)) + struct.pack("!H", length)
    else:
        header = bytes((0x81, 127)) + struct.pack("!Q", length)
    return header + data


def _wlan_ipv4() -> Optional[str]:
    """Return the primary outbound IPv4 address, else ``None`` (WiFi down).

    Uses a UDP-connect trick: a datagram socket "connected" to a non-routable
    TEST-NET address never sends a packet but lets the kernel pick the source
    address for the default route (wlan0 on this WiFi-only board).
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 9))  # TEST-NET-1, never actually reached
            addr = probe.getsockname()[0]
        if addr and not addr.startswith("127."):
            return addr
    except OSError:
        pass
    return None


def _parse_cookies(header: Optional[str]) -> Dict[str, str]:
    """Parse a ``Cookie`` header into a name -> value dict (best effort)."""
    cookies: Dict[str, str] = {}
    if not header:
        return cookies
    for part in header.split(";"):
        if "=" in part:
            name, value = part.split("=", 1)
            cookies[name.strip()] = value.strip()
    return cookies


def _parse_form(body: bytes) -> Dict[str, str]:
    """Parse a urlencoded body into a flat dict (last value wins)."""
    parsed = parse_qs(body.decode("utf-8", "replace"), keep_blank_values=True)
    return {key: values[-1] for key, values in parsed.items()}


def _header_parameters(value: str) -> Dict[str, str]:
    """Parse a simple semicolon-delimited MIME header parameter list."""
    parts = [part.strip() for part in value.split(";")]
    params = {"": parts[0].lower()} if parts else {}
    for part in parts[1:]:
        if "=" not in part:
            continue
        key, item = part.split("=", 1)
        params[key.strip().lower()] = item.strip().strip('"')
    return params


def _parse_multipart(content_type: str, body: bytes):
    """Parse a bounded multipart form into scalar fields and uploaded files."""
    params = _header_parameters(content_type)
    main_type = params.get("", "")
    boundary = params.get("boundary", "")
    if main_type != "multipart/form-data" or not boundary:
        return {}, {}
    marker = b"--" + boundary.encode("ascii", "ignore")
    fields: Dict[str, str] = {}
    files: Dict[str, routes.UploadedFile] = {}
    for part in body.split(marker)[1:]:
        if part.startswith(b"--"):
            break
        part = part.lstrip(b"\r\n")
        headers, separator, payload = part.partition(b"\r\n\r\n")
        if not separator:
            continue
        if payload.endswith(b"\r\n"):
            payload = payload[:-2]
        header_lines = headers.decode("utf-8", "replace").split("\r\n")
        values = {}
        for line in header_lines:
            if ":" in line:
                key, value = line.split(":", 1)
                values[key.lower()] = value.strip()
        disp_params = _header_parameters(values.get("content-disposition", ""))
        disposition = disp_params.get("", "")
        name = disp_params.get("name", "")
        if disposition != "form-data" or not name:
            continue
        filename = disp_params.get("filename")
        if filename is None:
            fields[name] = payload.decode("utf-8", "replace")
        else:
            files[name] = routes.UploadedFile(
                filename=filename,
                content_type=values.get("content-type", ""),
                data=payload,
            )
    return fields, files


class Handler(BaseHTTPRequestHandler):
    """Request handler backed by the :mod:`radio_web.routes` table."""

    server_version = "radio-web/1.0"
    protocol_version = "HTTP/1.1"

    def handle(self) -> None:
        """Silently discard clients that reset an idle HTTP/WebSocket connection."""
        try:
            super().handle()
        except (BrokenPipeError, ConnectionResetError):
            logger.debug("Client reset connection")

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        """Route access logs through the module logger at DEBUG."""
        logger.debug("%s - %s", self.address_string(), format % args)

    def handle_expect_100(self) -> bool:
        """Reject firmware ``Expect`` requests before soliciting a large body."""
        if urlsplit(self.path).path == "/firmware/upload":
            self.close_connection = True
            self._send_json(
                417, "expectation_failed", "Expect is not supported for firmware uploads."
            )
            return False
        return super().handle_expect_100()

    def _send(
        self,
        status: int,
        content_type: str,
        body,
        extra_headers: Optional[List] = None,
    ) -> None:
        # ``body`` may be text (the common case) or raw bytes (binary downloads
        # such as the diagnostics bundle). Encode text; pass bytes through.
        payload = body if isinstance(body, (bytes, bytearray)) else body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self'; style-src 'self'; script-src 'self'; "
            "object-src 'none'; base-uri 'none'; form-action 'self'; "
            "frame-ancestors 'none'",
        )
        headers = list(extra_headers or [])
        if not any(name.lower() == "cache-control" for name, _value in headers):
            self.send_header("Cache-Control", "no-store")
        for name, value in headers:
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    def _send_json(self, status: int, code: str, message: str, **values) -> None:
        payload = {"ok": status < 400, "code": code, "message": message}
        payload.update(values)
        self._send(
            status,
            "application/json; charset=utf-8",
            json.dumps(payload, separators=(",", ":")),
            [("Cache-Control", "no-store")],
        )

    def _read_body(self) -> Optional[bytes]:
        """Return the request body, or ``None`` if it exceeds the cap.

        Reads at most :data:`MAX_REQUEST_BODY` bytes so a malicious
        ``Content-Length`` cannot exhaust memory.
        """
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except (TypeError, ValueError):
            length = 0
        if length <= 0:
            return b""
        if length > MAX_REQUEST_BODY:
            return None
        return self.rfile.read(length)

    def _build_request(self, method: str, path: str, body: bytes) -> routes.Request:
        cookies = _parse_cookies(self.headers.get("Cookie"))
        token = cookies.get(routes.SESSION_COOKIE)
        session = SESSIONS.validate(token)
        form: Dict[str, str] = {}
        files = {}
        if method == "POST":
            content_type = self.headers.get("Content-Type", "")
            if content_type.lower().startswith("multipart/form-data"):
                form, files = _parse_multipart(content_type, body)
            else:
                form = _parse_form(body)
        raw_query = urlsplit(self.path).query
        query = {
            key: values[-1] for key, values in parse_qs(raw_query, keep_blank_values=True).items()
        }
        content_types = self.headers.get_all("Content-Type", failobj=[])
        media_type = (
            content_types[0].split(";", 1)[0].strip().lower() if len(content_types) == 1 else ""
        )
        client_ip = self.client_address[0] if self.client_address else ""
        return routes.Request(
            method=method,
            path=path,
            form=form,
            files=files,
            query=query,
            media_type=media_type,
            body=body,
            client_ip=client_ip,
            session=session,
            sessions=SESSIONS,
            rate_limiter=RATE_LIMITER,
        )

    def _handle_firmware_upload(self) -> None:
        """Authenticate and stream a raw SWU body without normal form buffering."""
        cookies = _parse_cookies(self.headers.get("Cookie"))
        session = SESSIONS.validate(cookies.get(routes.SESSION_COOKIE))
        if session is None:
            self.close_connection = True
            self._send_json(401, "authentication_required", "Authentication is required.")
            return
        if not auth.check_csrf(session, self.headers.get("X-CSRF-Token")):
            self.close_connection = True
            self._send_json(403, "csrf_rejected", "The CSRF token is invalid.")
            return
        if self.headers.get("Transfer-Encoding"):
            self.close_connection = True
            self._send_json(
                400, "chunked_not_supported", "Chunked firmware uploads are not supported."
            )
            return
        content_types = self.headers.get_all("Content-Type", failobj=[])
        media_type = (
            content_types[0].split(";", 1)[0].strip().lower() if len(content_types) == 1 else ""
        )
        if media_type != "application/octet-stream":
            self.close_connection = True
            self._send_json(415, "unsupported_media_type", "Use application/octet-stream.")
            return
        lengths = self.headers.get_all("Content-Length", failobj=[])
        if len(lengths) != 1 or not lengths[0].strip().isdigit():
            self.close_connection = True
            self._send_json(400, "invalid_length", "A valid Content-Length is required.")
            return
        length = int(lengths[0])
        if length <= 0:
            self.close_connection = True
            self._send_json(400, "invalid_length", "A positive Content-Length is required.")
            return
        if length > firmware_upload.MAX_FIRMWARE_BYTES:
            self.close_connection = True
            self._send_json(413, "firmware_too_large", "The firmware file is too large.")
            return
        tracking_token = firmware_authorization.consume(
            self.headers.get("X-Firmware-Grant"), session.token
        )
        tracking_status = firmware_tracking.status(tracking_token or "")
        if (
            tracking_token is None
            or tracking_status is None
            or tracking_status.get("state") != "starting"
        ):
            self.close_connection = True
            self._send_json(
                403,
                "authorization_rejected",
                "Firmware authorization is missing, expired, or already used.",
            )
            return

        previous_timeout = self.connection.gettimeout()
        self.connection.settimeout(firmware_upload.UPLOAD_IDLE_TIMEOUT_SECONDS)
        try:
            result = firmware_upload.stage_upload(
                self.rfile,
                length,
                filename=unquote(self.headers.get("X-Firmware-Name", "firmware.swu")),
            )
        except firmware_upload.UploadFailure as exc:
            self.close_connection = True
            self._send_json(exc.status, exc.code, exc.message)
            return
        finally:
            self.connection.settimeout(previous_timeout)
        firmware_tracking.update("queued", message="Upload complete; starting validation.")
        ok, detail = actions.run_action("install_firmware")
        if not ok:
            firmware_tracking.update("failed", message=detail, result=detail)
            self._send_json(409, "install_not_started", detail)
            return
        self._send_json(
            200,
            "install_started",
            detail,
            bytes=result.bytes_received,
            sha256=result.sha256,
            tracking_token=tracking_token,
        )

    def _handle_firmware_tracking(self) -> None:
        """Return post-reboot update state to the holder of a tracking capability."""
        status = firmware_tracking.status(self.headers.get("X-Firmware-Tracking", ""))
        if status is None:
            self._send_json(404, "tracking_unavailable", "Update tracking is unavailable.")
            return
        self._send_json(200, "tracking_status", "Update status available.", update=status)

    def _handle(self, method: str) -> None:
        try:
            path = urlsplit(self.path).path
            if method == "POST" and path == "/firmware/upload":
                self._handle_firmware_upload()
                return
            if method == "GET" and path == "/firmware/tracking":
                self._handle_firmware_tracking()
                return
            body = self._read_body()
            if body is None:
                if path.startswith("/api/"):
                    self._send_json(
                        413,
                        "request_too_large",
                        "The request body is too large.",
                        source="",
                    )
                else:
                    self._send(413, "text/plain; charset=utf-8", "request body too large")
                return
            req = self._build_request(method, path, body)
            if method == "GET" and path == "/debug/adc/ws":
                self._handle_adc_websocket(req)
                return
            status, content_type, resp_body, extra_headers = routes.resolve(req)
            self._send(status, content_type, resp_body, extra_headers)
        except (BrokenPipeError, ConnectionResetError):
            # The client went away while a response was being generated/sent.
            logger.debug("Client disconnected during %s %s", method, self.path)
        except Exception:
            # BaseHTTPRequestHandler otherwise closes the socket without a
            # response, which browsers report as ERR_EMPTY_RESPONSE. Log the
            # traceback and return a generic response without exposing details.
            logger.exception("Unhandled error while serving %s %s", method, self.path)
            try:
                self.close_connection = True
                if urlsplit(self.path).path.startswith("/api/"):
                    self._send_json(500, "internal_error", "Internal server error.", source="")
                else:
                    self._send(
                        500,
                        "text/plain; charset=utf-8",
                        "internal server error",
                        [("Connection", "close")],
                    )
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass

    def _handle_adc_websocket(self, req: routes.Request) -> None:
        """Upgrade an authenticated same-origin request and stream ADC JSON."""
        if req.session is None:
            self.close_connection = True
            self._send(401, "text/plain; charset=utf-8", "authentication required")
            return
        origin = self.headers.get("Origin", "")
        host = self.headers.get("Host", "")
        if origin and urlsplit(origin).netloc != host:
            self.close_connection = True
            self._send(403, "text/plain; charset=utf-8", "origin rejected")
            return
        key = self.headers.get("Sec-WebSocket-Key", "").strip()
        if (
            self.headers.get("Upgrade", "").lower() != "websocket"
            or "upgrade" not in self.headers.get("Connection", "").lower()
            or self.headers.get("Sec-WebSocket-Version") != "13"
            or not key
        ):
            self.close_connection = True
            self._send(400, "text/plain; charset=utf-8", "invalid websocket request")
            return
        try:
            decoded = base64.b64decode(key, validate=True)
        except ValueError:
            decoded = b""
        if len(decoded) != 16:
            self.close_connection = True
            self._send(400, "text/plain; charset=utf-8", "invalid websocket key")
            return
        accept = base64.b64encode(
            hashlib.sha1((key + WEBSOCKET_GUID).encode("ascii")).digest()  # noqa: S324
        ).decode("ascii")
        self.send_response(101, "Switching Protocols")
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept)
        self.end_headers()
        self.close_connection = True
        try:
            while True:
                payload = json.dumps(adc_store.live_snapshot(), separators=(",", ":"))
                self.connection.sendall(_websocket_text_frame(payload))
                time.sleep(0.2)
        except (BrokenPipeError, ConnectionResetError, OSError, socket.timeout):
            logger.debug("ADC WebSocket client disconnected")

    def do_GET(self) -> None:  # noqa: N802
        self._handle("GET")

    def do_HEAD(self) -> None:  # noqa: N802
        self._handle("HEAD")

    def do_POST(self) -> None:  # noqa: N802
        self._handle("POST")

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._handle("OPTIONS")


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    # Bind quickly on restart without waiting out TIME_WAIT.
    allow_reuse_address = True
    timeout = REQUEST_TIMEOUT

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._lan_listener: Optional["_LanListener"] = None
        self._lan_stop: Optional[threading.Event] = None
        self._lan_thread: Optional[threading.Thread] = None

    def finish_request(self, request, client_address) -> None:  # type: ignore[override]
        request.settimeout(REQUEST_TIMEOUT)
        super().finish_request(request, client_address)

    def server_close(self) -> None:
        """Close this server and any LAN monitor owned by the loopback server."""
        if self._lan_stop is not None:
            self._lan_stop.set()
        if self._lan_thread is not None:
            self._lan_thread.join(timeout=REQUEST_TIMEOUT)
        if self._lan_listener is not None:
            self._lan_listener.close()
        self._lan_listener = None
        self._lan_stop = None
        self._lan_thread = None
        super().server_close()


class _LanListener:
    """Own the HTTP listener for the current WiFi IPv4 address.

    ``S41wlan`` deliberately starts WiFi in the background, so wlan0 commonly
    has no address when ``S80radio-web`` starts. Reconciliation also handles a
    later DHCP/static-IP change without broadening the bind to ``0.0.0.0``.
    """

    def __init__(self, port: int) -> None:
        self.port = port
        self.address: Optional[str] = None
        self.server: Optional[_Server] = None
        self.thread: Optional[threading.Thread] = None
        self._lock = threading.RLock()

    def reconcile(self) -> None:
        """Make the LAN listener match wlan0's current IPv4 address."""
        with self._lock:
            desired = _wlan_ipv4()
            if desired == self.address and self.server is not None:
                return

            if self.server is not None:
                self.close()
            if not desired:
                return

            try:
                lan_server = _Server((desired, self.port), Handler)
            except OSError as exc:
                # Keep address unset so the next monitor pass retries this bind.
                logger.warning("Could not bind %s:%s: %s", desired, self.port, exc)
                return

            self.address = desired
            self.server = lan_server
            self.thread = threading.Thread(
                target=lan_server.serve_forever,
                daemon=True,
                name="radio-web-lan",
            )
            self.thread.start()
            logger.info("radio-web listening on %s:%s", desired, self.port)

    def close(self) -> None:
        """Stop and close the current LAN listener, if any."""
        with self._lock:
            lan_server = self.server
            lan_thread = self.thread
            address = self.address
            self.server = None
            self.thread = None
            self.address = None
            if lan_server is None:
                return
            lan_server.shutdown()
            lan_server.server_close()
            if lan_thread is not None:
                lan_thread.join(timeout=REQUEST_TIMEOUT)
            logger.info("radio-web stopped listening on %s:%s", address, self.port)


def _monitor_lan(listener: _LanListener, stop: threading.Event) -> None:
    """Reconcile the wlan0 listener until the web service shuts down."""
    while not stop.is_set():
        listener.reconcile()
        stop.wait(LAN_RECONCILE_SECONDS)


def serve(port: int = DEFAULT_PORT, forever: bool = True) -> List[_Server]:
    """Start loopback immediately and track the changing wlan0 address.

    The loopback server is required. The LAN listener is best-effort and is
    reconciled in a daemon thread because WiFi/DHCP starts asynchronously and
    its address may later change. Tests call this with ``forever=False`` and
    close the returned loopback server, which also stops its LAN monitor.
    """
    loopback = _Server(("127.0.0.1", port), Handler)
    actual_port = int(loopback.server_address[1])
    logger.info("radio-web listening on 127.0.0.1:%s", actual_port)

    lan_listener = _LanListener(actual_port)
    lan_listener.reconcile()
    lan_stop = threading.Event()
    lan_thread = threading.Thread(
        target=_monitor_lan,
        args=(lan_listener, lan_stop),
        daemon=True,
        name="radio-web-lan-monitor",
    )
    loopback._lan_listener = lan_listener
    loopback._lan_stop = lan_stop
    loopback._lan_thread = lan_thread
    lan_thread.start()

    if not forever:
        threading.Thread(
            target=loopback.serve_forever,
            daemon=True,
            name="radio-web-loopback",
        ).start()
        return [loopback]

    try:
        loopback.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        loopback.server_close()
    return [loopback]
