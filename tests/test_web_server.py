"""Integration tests for the radio_web HTTP server.

Bind to 127.0.0.1 on an ephemeral port (never the fixed 8080) and drive it with
urllib. Only the loopback bind is exercised so the tests never touch wlan0.
"""

import base64
import hashlib
import http.cookiejar
import io
import json
import socket
import time
import urllib.error
import urllib.request

import pytest

from radio_web import server


@pytest.fixture
def running_server(monkeypatch):
    # Loopback only, ephemeral port (0) so tests never clash on 8080.
    monkeypatch.setattr(server, "_wlan_ipv4", lambda: None)
    servers = server.serve(port=0, forever=False)
    assert servers, "expected at least the loopback server"
    host, port = servers[0].server_address
    # Give the daemon serve_forever thread a moment to be ready.
    time.sleep(0.05)
    try:
        yield host, port
    finally:
        for srv in servers:
            srv.shutdown()
            srv.server_close()


def _get(host, port, path="/"):
    url = f"http://{host}:{port}{path}"
    return urllib.request.urlopen(url, timeout=5)  # noqa: S310


class TestServer:
    def test_parse_multipart_form_and_file(self):
        boundary = "test-boundary"
        body = (
            b"--test-boundary\r\n"
            b'Content-Disposition: form-data; name="op"\r\n\r\n'
            b"upload_logo\r\n"
            b"--test-boundary\r\n"
            b'Content-Disposition: form-data; name="logo_file"; filename="x.png"\r\n'
            b"Content-Type: image/png\r\n\r\n"
            b"png-bytes\r\n"
            b"--test-boundary--\r\n"
        )
        form, files = server._parse_multipart(f"multipart/form-data; boundary={boundary}", body)
        assert form == {"op": "upload_logo"}
        assert files["logo_file"].filename == "x.png"
        assert files["logo_file"].data == b"png-bytes"

    def test_dashboard_get_200(self, running_server):
        host, port = running_server
        resp = _get(host, port, "/")
        assert resp.status == 200
        body = resp.read().decode("utf-8")
        assert "PiSonic" in body
        assert resp.headers.get("Content-Type", "").startswith("text/html")
        assert resp.headers.get("X-Frame-Options") == "DENY"
        assert "default-src 'self'" in resp.headers.get("Content-Security-Policy", "")
        assert "script-src 'self'" in resp.headers.get("Content-Security-Policy", "")

    def test_healthz(self, running_server):
        host, port = running_server
        resp = _get(host, port, "/healthz")
        assert resp.status == 200
        assert resp.read().decode("utf-8") == "ok"

    def test_static_stylesheet_headers(self, running_server):
        host, port = running_server
        resp = _get(host, port, "/static/app.css")
        assert resp.status == 200
        assert resp.headers.get("Content-Type", "").startswith("text/css")
        assert resp.headers.get("Cache-Control") == "public, max-age=3600"
        assert resp.headers.get_all("Cache-Control") == ["public, max-age=3600"]
        assert b".dashboard-grid" in resp.read()

    def test_dashboard_script(self, running_server):
        host, port = running_server
        resp = _get(host, port, "/static/app.js")
        assert resp.status == 200
        assert resp.headers.get("Content-Type", "").startswith("text/javascript")
        body = resp.read()
        assert b'fetch("/api/v1/player"' in body
        assert b'credentials: "omit"' in body
        assert b'querySelectorAll(".maintenance-dialog")' in body
        assert b"dialog.showModal()" in body
        assert b"new XMLHttpRequest()" in body
        assert b'"X-CSRF-Token"' in body
        assert b'upload.addEventListener("progress"' in body
        assert b"response.status === 404" in body
        assert b"discardUnavailableTracking()" in body
        assert b'sessionStorage.removeItem("firmwareTrackingToken")' in body
        assert b"if (restoring && !wizard.open)" in body
        assert b"pollTracking(savedToken, true)" in body
        assert b"setInterval(refreshNowPlaying, 5000)" in body
        assert b"new WebSocket" in body
        assert b'getElementById("rootfs-expand-form")' not in body

    def test_websocket_requires_auth(self, running_server):
        host, port = running_server
        key = base64.b64encode(b"0123456789abcdef").decode()
        request = (
            f"GET /debug/adc/ws HTTP/1.1\r\nHost: {host}:{port}\r\n"
            "Upgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        )
        with socket.create_connection((host, port), timeout=5) as sock:
            sock.sendall(request.encode())
            assert b"401 Unauthorized" in sock.recv(1024)

    def test_authenticated_websocket_upgrade(self, running_server):
        host, port = running_server
        session = server.SESSIONS.create()
        key = base64.b64encode(b"0123456789abcdef").decode()
        request = (
            f"GET /debug/adc/ws HTTP/1.1\r\nHost: {host}:{port}\r\n"
            f"Origin: http://{host}:{port}\r\nCookie: radio_session={session.token}\r\n"
            "Upgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        )
        expected = base64.b64encode(
            hashlib.sha1((key + server.WEBSOCKET_GUID).encode()).digest()  # noqa: S324
        )
        with socket.create_connection((host, port), timeout=5) as sock:
            sock.sendall(request.encode())
            response = sock.recv(2048)
            assert b"101 Switching Protocols" in response
            assert b"Sec-WebSocket-Accept: " + expected in response

    def test_obsolete_now_playing_fragment_is_not_served(self, running_server):
        host, port = running_server
        with pytest.raises(urllib.error.HTTPError) as exc:
            _get(host, port, "/dashboard/now-playing")
        assert exc.value.code == 404

    def test_unknown_path_404(self, running_server):
        host, port = running_server
        with pytest.raises(urllib.error.HTTPError) as exc:
            _get(host, port, "/does-not-exist")
        assert exc.value.code == 404

    def test_no_auth_required_in_phase3(self, running_server):
        # Phase 3 is read-only and unauthenticated: a plain GET must succeed
        # without any cookie/credential, and no auth challenge is issued.
        host, port = running_server
        resp = _get(host, port, "/")
        assert resp.status == 200
        assert resp.headers.get("WWW-Authenticate") is None

    def test_post_is_405(self, running_server):
        host, port = running_server
        req = urllib.request.Request(f"http://{host}:{port}/", data=b"x=1", method="POST")
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(req, timeout=5)  # noqa: S310
        assert exc.value.code == 405

    def test_oversized_body_rejected(self, running_server):
        host, port = running_server
        request = (
            f"POST / HTTP/1.1\r\nHost: {host}:{port}\r\n"
            f"Content-Length: {server.MAX_REQUEST_BODY + 1}\r\nConnection: close\r\n\r\n"
        )
        with socket.create_connection((host, port), timeout=5) as sock:
            sock.sendall(request.encode())
            assert sock.recv(1024).startswith(b"HTTP/1.1 413 ")

    def test_unhandled_route_error_returns_500(self, running_server, monkeypatch):
        monkeypatch.setattr(
            "radio_web.routes.resolve",
            lambda _req: (_ for _ in ()).throw(RuntimeError("private detail")),
        )
        host, port = running_server
        with pytest.raises(urllib.error.HTTPError) as exc:
            _get(host, port, "/")
        assert exc.value.code == 500
        assert exc.value.read() == b"internal server error"


def test_websocket_text_frame_short_payload():
    frame = server._websocket_text_frame("hello")
    assert frame == b"\x81\x05hello"


class TestLanListener:
    """The LAN listener follows WiFi even when DHCP completes after startup."""

    class FakeServer:
        instances = []

        def __init__(self, server_address, _handler):
            self.server_address = server_address
            self.shutdown_called = False
            self.close_called = False
            self.__class__.instances.append(self)

        def serve_forever(self):
            return None

        def shutdown(self):
            self.shutdown_called = True

        def server_close(self):
            self.close_called = True

    @pytest.fixture(autouse=True)
    def fake_server(self, monkeypatch):
        self.FakeServer.instances = []
        monkeypatch.setattr(server, "_Server", self.FakeServer)

    def test_adds_listener_when_wifi_address_appears(self, monkeypatch):
        current_address = [None]
        monkeypatch.setattr(server, "_wlan_ipv4", lambda: current_address[0])
        listener = server._LanListener(8080)

        listener.reconcile()
        assert listener.server is None

        current_address[0] = "192.168.178.192"
        listener.reconcile()

        assert listener.address == "192.168.178.192"
        assert self.FakeServer.instances[-1].server_address == (
            "192.168.178.192",
            8080,
        )
        listener.close()

    def test_replaces_listener_when_wifi_address_changes(self, monkeypatch):
        current_address = ["192.168.178.10"]
        monkeypatch.setattr(server, "_wlan_ipv4", lambda: current_address[0])
        listener = server._LanListener(8080)
        listener.reconcile()
        old_server = self.FakeServer.instances[-1]

        current_address[0] = "192.168.178.11"
        listener.reconcile()

        assert old_server.shutdown_called
        assert old_server.close_called
        assert listener.address == "192.168.178.11"
        listener.close()

    def test_retries_a_failed_lan_bind(self, monkeypatch):
        attempts = []

        class FailOnceServer(self.FakeServer):
            def __init__(self, server_address, handler):
                attempts.append(server_address)
                if len(attempts) == 1:
                    raise OSError("address not ready")
                super().__init__(server_address, handler)

        monkeypatch.setattr(server, "_Server", FailOnceServer)
        monkeypatch.setattr(server, "_wlan_ipv4", lambda: "192.168.178.192")
        listener = server._LanListener(8080)

        listener.reconcile()
        assert listener.server is None
        listener.reconcile()

        assert len(attempts) == 2
        assert listener.address == "192.168.178.192"
        listener.close()


class TestServerAuth:
    """End-to-end auth flow through the real HTTP server on loopback."""

    def test_setup_login_logout_flow(self, running_server, monkeypatch, tmp_path):
        # Point the secret store at a temp dir and reset shared auth state so
        # this test is isolated from any other server test.
        from radio_web import auth

        monkeypatch.setattr("radio_web.config_store.MANAGED_CONFIG_DIR", str(tmp_path))
        monkeypatch.setattr(server, "SESSIONS", auth.SessionStore())
        monkeypatch.setattr(server, "RATE_LIMITER", auth.RateLimiter())

        host, port = running_server
        base = f"http://{host}:{port}"

        # A cookie-aware opener that does NOT auto-follow redirects, so we can
        # inspect Set-Cookie and Location headers.
        cookie_jar = http.cookiejar.CookieJar()

        class _NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None

        opener = urllib.request.build_opener(
            _NoRedirect, urllib.request.HTTPCookieProcessor(cookie_jar)
        )

        def _open(path, data=None, method="GET"):
            req = urllib.request.Request(f"{base}{path}", data=data, method=method)
            try:
                return opener.open(req, timeout=5)
            except urllib.error.HTTPError as exc:
                return exc

        # 1. First-use setup creates the password and logs us in (303 + cookie).
        resp = _open(
            "/setup",
            data=b"password=supersecret&confirm=supersecret",
            method="POST",
        )
        assert resp.status == 303
        assert resp.headers.get("Location") == "/"
        assert any(c.name == "radio_session" for c in cookie_jar)
        cookie = next(c for c in cookie_jar if c.name == "radio_session")
        # cookielib does not expose HttpOnly/SameSite as attributes reliably,
        # so assert on the raw Set-Cookie header value instead.
        set_cookie = resp.headers.get("Set-Cookie", "")
        assert "HttpOnly" in set_cookie and "SameSite=Strict" in set_cookie

        # 2. The dashboard stays public regardless of auth.
        assert _open("/").status == 200

        # 3. Grab a CSRF token for logout from the live session store.
        session = server.SESSIONS.validate(cookie.value)
        assert session is not None

        # 4. Logout without CSRF is rejected (403); session survives.
        resp = _open("/logout", data=b"csrf_token=bogus", method="POST")
        assert resp.status == 403
        assert server.SESSIONS.validate(cookie.value) is not None

        # 5. Logout with the correct CSRF clears the session (303 to /login).
        resp = _open(
            "/logout",
            data=f"csrf_token={session.csrf_token}".encode(),
            method="POST",
        )
        assert resp.status == 303
        assert resp.headers.get("Location") == "/login"
        assert server.SESSIONS.validate(cookie.value) is None


class TestFirmwareUploadHttp:
    def _handler(self, monkeypatch, *, headers=None, body=b""):
        handler = object.__new__(server.Handler)
        handler.headers = headers or {}
        handler.rfile = io.BytesIO(body)
        handler.path = "/firmware/upload"
        handler.command = "POST"
        handler.close_connection = False
        responses = []
        monkeypatch.setattr(
            handler,
            "_send",
            lambda status, content_type, data, extra=None: responses.append(
                (status, content_type, data, extra)
            ),
        )
        return handler, responses

    def test_unauthenticated_rejected_before_body_read(self, monkeypatch):
        handler, responses = self._handler(monkeypatch, body=b"do-not-read")
        handler._handle_firmware_upload()
        assert responses[0][0] == 401
        assert handler.rfile.tell() == 0

    def test_expect_continue_is_rejected_before_body_read(self, monkeypatch):
        handler, responses = self._handler(monkeypatch, body=b"do-not-read")
        assert handler.handle_expect_100() is False
        assert responses[0][0] == 417
        assert handler.rfile.tell() == 0

    def test_bad_csrf_rejected_before_body_read(self, monkeypatch):
        from email.message import Message

        sessions = server.auth.SessionStore()
        session = sessions.create()
        monkeypatch.setattr(server, "SESSIONS", sessions)
        headers = Message()
        headers["Cookie"] = f"radio_session={session.token}"
        headers["X-CSRF-Token"] = "wrong"
        handler, responses = self._handler(monkeypatch, headers=headers, body=b"do-not-read")
        handler._handle_firmware_upload()
        assert responses[0][0] == 403
        assert handler.rfile.tell() == 0

    def test_valid_upload_returns_json(self, monkeypatch):
        from email.message import Message

        sessions = server.auth.SessionStore()
        session = sessions.create()
        monkeypatch.setattr(server, "SESSIONS", sessions)
        headers = Message()
        headers["Cookie"] = f"radio_session={session.token}"
        headers["X-CSRF-Token"] = session.csrf_token
        headers["Content-Type"] = "application/octet-stream"
        headers["Content-Length"] = "4"
        grant = server.firmware_authorization.issue(session.token, "tracking-token")
        headers["X-Firmware-Grant"] = grant
        handler, responses = self._handler(monkeypatch, headers=headers, body=b"data")
        handler.connection = type(
            "Connection",
            (),
            {"gettimeout": lambda self: 15, "settimeout": lambda self, value: None},
        )()
        monkeypatch.setattr(
            server.firmware_upload,
            "stage_upload",
            lambda stream, length, filename="": server.firmware_upload.UploadResult(
                length, "abc123"
            ),
        )
        monkeypatch.setattr(server.actions, "run_action", lambda _action: (True, "started"))
        monkeypatch.setattr(
            server.firmware_tracking,
            "status",
            lambda token: {"state": "starting"} if token == "tracking-token" else None,
        )
        monkeypatch.setattr(server.firmware_tracking, "update", lambda *_args, **_kwargs: None)
        handler._handle_firmware_upload()
        status, content_type, raw, _headers = responses[0]
        assert status == 200
        assert content_type.startswith("application/json")
        assert json.loads(raw)["sha256"] == "abc123"
        assert json.loads(raw)["tracking_token"] == "tracking-token"

    def test_upload_requires_one_use_password_grant_before_body_read(self, monkeypatch):
        from email.message import Message

        sessions = server.auth.SessionStore()
        session = sessions.create()
        monkeypatch.setattr(server, "SESSIONS", sessions)
        headers = Message()
        headers["Cookie"] = f"radio_session={session.token}"
        headers["X-CSRF-Token"] = session.csrf_token
        headers["Content-Type"] = "application/octet-stream"
        headers["Content-Length"] = "4"
        handler, responses = self._handler(monkeypatch, headers=headers, body=b"data")
        handler._handle_firmware_upload()
        assert responses[0][0] == 403
        assert handler.rfile.tell() == 0

    def test_tracking_endpoint_uses_capability_without_session(self, monkeypatch):
        from email.message import Message

        handler, responses = self._handler(monkeypatch, headers=Message())
        handler.headers["X-Firmware-Tracking"] = "tracker"
        monkeypatch.setattr(
            server.firmware_tracking,
            "status",
            lambda token: {"state": "accepted"} if token == "tracker" else None,
        )
        handler._handle_firmware_tracking()
        status, _content_type, raw, _headers = responses[0]
        assert status == 200
        assert json.loads(raw)["update"] == {"state": "accepted"}

    @pytest.mark.parametrize(
        ("header", "value", "status"),
        [
            ("Content-Type", "text/plain", 415),
            ("Content-Length", "0", 400),
            ("Content-Length", "invalid", 400),
            ("Transfer-Encoding", "chunked", 400),
        ],
    )
    def test_invalid_upload_headers_are_rejected(self, monkeypatch, header, value, status):
        from email.message import Message

        sessions = server.auth.SessionStore()
        session = sessions.create()
        monkeypatch.setattr(server, "SESSIONS", sessions)
        headers = Message()
        headers["Cookie"] = f"radio_session={session.token}"
        headers["X-CSRF-Token"] = session.csrf_token
        headers["Content-Type"] = "application/octet-stream"
        headers["Content-Length"] = "4"
        if header in headers:
            headers.replace_header(header, value)
        else:
            headers[header] = value
        handler, responses = self._handler(monkeypatch, headers=headers, body=b"data")
        handler._handle_firmware_upload()
        assert responses[0][0] == status
        assert handler.rfile.tell() == 0

    def test_missing_content_length_is_rejected(self, monkeypatch):
        from email.message import Message

        sessions = server.auth.SessionStore()
        session = sessions.create()
        monkeypatch.setattr(server, "SESSIONS", sessions)
        headers = Message()
        headers["Cookie"] = f"radio_session={session.token}"
        headers["X-CSRF-Token"] = session.csrf_token
        headers["Content-Type"] = "application/octet-stream"
        handler, responses = self._handler(monkeypatch, headers=headers, body=b"data")
        handler._handle_firmware_upload()
        assert responses[0][0] == 400
        assert handler.rfile.tell() == 0

    def test_duplicate_content_length_is_rejected(self, monkeypatch):
        from email.message import Message

        sessions = server.auth.SessionStore()
        session = sessions.create()
        monkeypatch.setattr(server, "SESSIONS", sessions)
        headers = Message()
        headers["Cookie"] = f"radio_session={session.token}"
        headers["X-CSRF-Token"] = session.csrf_token
        headers["Content-Type"] = "application/octet-stream"
        headers["Content-Length"] = "4"
        headers["Content-Length"] = "4"
        handler, responses = self._handler(monkeypatch, headers=headers, body=b"data")
        handler._handle_firmware_upload()
        assert responses[0][0] == 400
        assert handler.rfile.tell() == 0

    def test_oversized_firmware_is_rejected_before_body_read(self, monkeypatch):
        from email.message import Message

        sessions = server.auth.SessionStore()
        session = sessions.create()
        monkeypatch.setattr(server, "SESSIONS", sessions)
        monkeypatch.setattr(server.firmware_upload, "MAX_FIRMWARE_BYTES", 3)
        headers = Message()
        headers["Cookie"] = f"radio_session={session.token}"
        headers["X-CSRF-Token"] = session.csrf_token
        headers["Content-Type"] = "application/octet-stream"
        headers["Content-Length"] = "4"
        handler, responses = self._handler(monkeypatch, headers=headers, body=b"data")
        handler._handle_firmware_upload()
        assert responses[0][0] == 413
        assert handler.rfile.tell() == 0
