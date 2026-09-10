"""Tests for the radio_web route table and templates."""

import base64
import json
import os
from pathlib import Path

import pytest

from radio_web import adc_store, audio_hardware_store, auth, routes, templates


def _req(method, path, **kw):
    return routes.Request(method=method, path=path, **kw)


class TestRoutes:
    def test_dashboard_route_ok(self):
        status, ctype, body, _hdrs = routes.resolve(_req("GET", "/"))
        assert status == 200
        assert "text/html" in ctype
        assert "Kitchen Radio" in body

    def test_healthz_route(self):
        status, ctype, body, _hdrs = routes.resolve(_req("GET", "/healthz"))
        assert status == 200
        assert "text/plain" in ctype
        assert body == "ok"

    def test_allowlisted_static_assets(self):
        status, ctype, body, headers = routes.resolve(_req("GET", "/static/app.css"))
        assert status == 200
        assert ctype.startswith("text/css")
        assert isinstance(body, bytes)
        assert b".site-header" in body
        assert ("Cache-Control", "public, max-age=3600") in headers

        status, ctype, body, _headers = routes.resolve(_req("GET", "/static/radio.svg"))
        assert status == 200
        assert ctype == "image/svg+xml"
        assert body.startswith(b"<svg")

        status, ctype, body, _headers = routes.resolve(_req("GET", "/static/app.js"))
        assert status == 200
        assert ctype.startswith("text/javascript")
        assert b"setInterval(refreshNowPlaying, 5000)" in body

    def test_unknown_static_path_is_not_served(self):
        status, _ctype, _body, _headers = routes.resolve(_req("GET", "/static/../auth.py"))
        assert status == 404

    def test_unknown_path_404(self):
        status, _ctype, body, _hdrs = routes.resolve(_req("GET", "/nope"))
        assert status == 404
        assert "Not found" in body

    def test_known_path_wrong_method_405(self):
        # "/" exists for GET only; POST must be rejected as 405, not 404.
        status, _ctype, body, _hdrs = routes.resolve(_req("POST", "/"))
        assert status == 405
        assert "Method not allowed" in body

    def test_method_case_insensitive(self):
        status, _ctype, _body, _hdrs = routes.resolve(_req("get", "/"))
        assert status == 200


class TestTemplates:
    def _status(self, **overrides):
        base = {
            "hostname": "radio",
            "version": "0.1.0",
            "uptime_seconds": 3661,
            "cpu_temperature_c": 48.1,
            "memory": {"total_kib": 507860, "available_kib": 305012},
            "rootfs": {"total": 1_000_000, "used": 400_000, "free": 600_000},
            "wifi": {"ssid": "Net", "ip": "10.0.0.5", "signal_dbm": -50},
            "heartbeat_age_seconds": 2.0,
            "player": {
                "available": True,
                "stale": False,
                "power": True,
                "active_source": "mpd",
                "now_playing": {"name": "DLF", "title": "News", "state": True},
                "sources": {"mpd": {"playing": True}},
            },
            "bluetooth": {
                "available": True,
                "alias": "radio",
                "powered": True,
                "discoverable": True,
                "pairable": True,
            },
            "source_labels": [("mpd", "Internet Radio"), ("airplay", "AirPlay")],
        }
        base.update(overrides)
        return base

    def test_dashboard_contains_sections(self):
        html = templates.dashboard(self._status())
        assert "Now playing" in html
        assert "Sources" in html
        assert "System" in html
        assert "0.1.0" in html
        assert "Internet Radio" in html
        assert 'class="dashboard-grid"' in html
        assert 'class="admin-links"' not in html
        assert 'id="now-playing"' in html
        assert 'src="/static/app.js?v=1"' in html
        assert "Playing" in html

    def test_dashboard_shows_bluetooth_adapter_info(self):
        html = templates.dashboard(self._status())
        # Read-only informational block only: name/state, no controls.
        assert "Bluetooth" in html
        assert "Discoverable" in html
        assert 'name="op" value="pairing"' not in html
        assert 'action="/bluetooth"' not in html

    def test_dashboard_bluetooth_unavailable_message(self):
        html = templates.dashboard(self._status(bluetooth={"available": False}))
        assert "Bluetooth adapter status is unavailable" in html

    def test_now_playing_indicates_not_playing(self):
        status = self._status()
        status["player"]["sources"]["mpd"]["playing"] = False
        html = templates.dashboard(status)
        assert "Not playing" in html

    def test_now_playing_uses_metadata_state_as_fallback(self):
        status = self._status()
        status["player"]["sources"] = {}
        html = templates.dashboard(status)
        assert "Playing" in html

    def test_now_playing_indicates_unknown_without_state(self):
        status = self._status()
        status["player"]["sources"] = {}
        status["player"]["now_playing"].pop("state")
        html = templates.dashboard(status)
        assert "Unknown" in html

    def test_now_playing_fragment_route(self, monkeypatch):
        monkeypatch.setattr(
            "radio_web.system_status.player_status", lambda: self._status()["player"]
        )
        status, ctype, body, _headers = routes.resolve(_req("GET", "/dashboard/now-playing"))
        assert status == 200
        assert "text/html" in ctype
        assert 'class="card now-playing"' in body
        assert "Playing" in body
        assert "<!DOCTYPE html>" not in body

    def test_now_playing_renders_artwork(self):
        status = self._status()
        status["player"]["now_playing"]["artwork"] = {
            "id": "station:Deutschlandfunk.png",
            "version": "abc123",
        }
        html = templates.dashboard(status)
        assert "/dashboard/artwork?id=station:Deutschlandfunk.png&amp;v=abc123" in html
        assert 'alt="Artwork for DLF"' in html

    def test_now_playing_without_artwork_renders_placeholder(self):
        html = templates.dashboard(self._status())
        assert 'class="artwork artwork-fallback"' in html

    def test_artwork_route_serves_validated_image(self, monkeypatch, tmp_path):
        image = tmp_path / "logo.png"
        image.write_bytes(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAAB"))
        monkeypatch.setattr("radio_web.artwork.LOGO_DIR", str(tmp_path))
        monkeypatch.setattr(
            "radio_web.artwork.stations_store.load_stations",
            lambda: [{"logo": "logo.png"}],
        )
        status, ctype, body, headers = routes.resolve(
            _req("GET", "/dashboard/artwork", query={"id": "station:logo.png"})
        )
        assert status == 200
        assert ctype == "image/png"
        assert body.startswith(b"\x89PNG")
        assert ("Cache-Control", "public, max-age=86400") in headers

    def test_artwork_route_rejects_traversal(self):
        status, _ctype, _body, _headers = routes.resolve(
            _req("GET", "/dashboard/artwork", query={"id": "station:../../secret.png"})
        )
        assert status == 404

    def test_page_uses_shared_application_shell(self):
        html = templates.dashboard(self._status())
        assert 'href="/static/app.css?v=16"' in html
        assert 'href="/static/radio.svg?v=1"' in html
        assert 'class="site-header"' in html
        assert 'aria-label="Main navigation"' in html
        assert 'aria-current="page"' in html
        assert 'class="skip-link"' in html
        assert 'src="/static/app.js?v=1"' in html
        assert "<style>" not in html

    def test_navigation_highlight_has_square_corners(self):
        css = os.path.dirname(routes.__file__) + "/static/app.css"
        text = open(css, encoding="utf-8").read()
        nav_rule = text.split(".primary-nav a {", 1)[1].split("}", 1)[0]
        assert "border-radius: 0" in nav_rule

    def test_dashboard_escapes_injected_values(self):
        status = self._status(wifi={"ssid": "<script>x</script>", "ip": None, "signal_dbm": None})
        html = templates.dashboard(status)
        assert "<script>x</script>" not in html
        assert "&lt;script&gt;" in html

    def test_dashboard_escapes_now_playing(self):
        status = self._status(
            player={
                "available": True,
                "stale": False,
                "power": True,
                "active_source": "mpd",
                "now_playing": {"name": "<b>x</b>", "title": "t", "state": True},
                "sources": {},
            }
        )
        html = templates.dashboard(status)
        assert "<b>x</b>" not in html
        assert "&lt;b&gt;" in html

    def test_player_unavailable_message(self):
        status = self._status(player={"available": False})
        html = templates.dashboard(status)
        assert "Player status unavailable" in html

    def test_volatile_label_present(self):
        html = templates.dashboard(self._status())
        assert "volatile memory" in html

    def test_login_page_has_form_and_notice(self):
        html = templates.login_page()
        assert 'action="/login"' in html
        assert 'type="password"' in html
        assert "not encrypted" in html

    def test_login_page_shows_error_escaped(self):
        html = templates.login_page(error="<x>bad")
        assert "&lt;x&gt;bad" in html
        assert "<x>bad" not in html

    def test_setup_page_has_confirm(self):
        html = templates.setup_page()
        assert 'action="/setup"' in html
        assert 'name="confirm"' in html

    def test_csrf_field_escaped(self):
        html = templates.csrf_field('a"b')
        assert 'name="csrf_token"' in html
        assert "&quot;" in html


class TestAuthFlowRoutes:
    """Route-level behaviour of the Phase 4 auth flow, using a tmp secret dir."""

    def _ctx(self, monkeypatch, tmp_path, method, path, **kw):
        monkeypatch.setattr("radio_web.config_store.MANAGED_CONFIG_DIR", str(tmp_path))
        kw.setdefault("sessions", auth.SessionStore())
        kw.setdefault("rate_limiter", auth.RateLimiter())
        return routes.Request(method=method, path=path, **kw)

    def test_setup_redirects_to_login_once_configured(self, monkeypatch, tmp_path):
        req = self._ctx(monkeypatch, tmp_path, "GET", "/setup")
        auth.set_password("initialpw123")
        status, _c, _b, headers = routes.resolve(req)
        assert status == 303
        assert ("Location", "/login") in headers

    def test_setup_post_creates_password_and_logs_in(self, monkeypatch, tmp_path):
        req = self._ctx(
            monkeypatch,
            tmp_path,
            "POST",
            "/setup",
            form={"password": "goodpassword", "confirm": "goodpassword"},
        )
        status, _c, _b, headers = routes.resolve(req)
        assert status == 303
        assert ("Location", "/") in headers
        assert any(n == "Set-Cookie" for n, _v in headers)
        assert auth.is_configured()

    def test_setup_post_mismatch_reports_error(self, monkeypatch, tmp_path):
        req = self._ctx(
            monkeypatch,
            tmp_path,
            "POST",
            "/setup",
            form={"password": "goodpassword", "confirm": "different1"},
        )
        status, _c, body, _headers = routes.resolve(req)
        assert status == 200
        assert "do not match" in body
        assert not auth.is_configured()

    def test_setup_post_write_failure_reports_error(self, monkeypatch, tmp_path):
        req = self._ctx(
            monkeypatch,
            tmp_path,
            "POST",
            "/setup",
            form={"password": "goodpassword", "confirm": "goodpassword"},
        )
        monkeypatch.setattr(
            "radio_web.auth.set_password",
            lambda _password: (_ for _ in ()).throw(PermissionError("denied")),
        )
        status, _c, body, headers = routes.resolve(req)
        assert status == 500
        assert "Unable to save the password" in body
        assert "denied" not in body
        assert headers == []

    def test_login_get_redirects_to_setup_when_unconfigured(self, monkeypatch, tmp_path):
        req = self._ctx(monkeypatch, tmp_path, "GET", "/login")
        status, _c, _b, headers = routes.resolve(req)
        assert status == 303
        assert ("Location", "/setup") in headers

    def test_login_post_success_sets_cookie(self, monkeypatch, tmp_path):
        sessions = auth.SessionStore()
        req = self._ctx(monkeypatch, tmp_path, "POST", "/login", sessions=sessions)
        auth.set_password("goodpassword")
        req.form = {"password": "goodpassword"}
        status, _c, _b, headers = routes.resolve(req)
        assert status == 303
        assert ("Location", "/") in headers
        cookie = next(v for n, v in headers if n == "Set-Cookie")
        assert "HttpOnly" in cookie and "SameSite=Strict" in cookie

    def test_login_post_wrong_password_401(self, monkeypatch, tmp_path):
        req = self._ctx(monkeypatch, tmp_path, "POST", "/login")
        auth.set_password("goodpassword")
        req.form = {"password": "wrong"}
        status, _c, body, _headers = routes.resolve(req)
        assert status == 401
        assert "Incorrect password" in body

    def test_login_rate_limited(self, monkeypatch, tmp_path):
        limiter = auth.RateLimiter(max_attempts=2, window_seconds=100)
        req = self._ctx(monkeypatch, tmp_path, "POST", "/login", rate_limiter=limiter)
        auth.set_password("goodpassword")
        req.form = {"password": "wrong"}
        req.client_ip = "10.0.0.9"
        routes.resolve(req)
        routes.resolve(req)
        status, _c, _b, _h = routes.resolve(req)
        assert status == 429

    def test_logout_requires_csrf(self, monkeypatch, tmp_path):
        sessions = auth.SessionStore()
        session = sessions.create()
        req = self._ctx(
            monkeypatch,
            tmp_path,
            "POST",
            "/logout",
            sessions=sessions,
            session=session,
            form={"csrf_token": "wrong"},
        )
        status, _c, _b, _h = routes.resolve(req)
        assert status == 403
        assert sessions.validate(session.token) is not None

    def test_logout_success_clears_session(self, monkeypatch, tmp_path):
        sessions = auth.SessionStore()
        session = sessions.create()
        req = self._ctx(
            monkeypatch,
            tmp_path,
            "POST",
            "/logout",
            sessions=sessions,
            session=session,
            form={"csrf_token": session.csrf_token},
        )
        status, _c, _b, headers = routes.resolve(req)
        assert status == 303
        assert ("Location", "/login") in headers
        assert sessions.validate(session.token) is None


class TestStationRoutes:
    """Phase 5 station-editing routes: auth + CSRF gating and PRG."""

    def _ctx(self, monkeypatch, tmp_path, method, path, **kw):
        monkeypatch.setattr("radio_web.config_store.MANAGED_CONFIG_DIR", str(tmp_path))
        kw.setdefault("sessions", auth.SessionStore())
        kw.setdefault("rate_limiter", auth.RateLimiter())
        return routes.Request(method=method, path=path, **kw)

    def test_stations_get_requires_auth(self, monkeypatch, tmp_path):
        req = self._ctx(monkeypatch, tmp_path, "GET", "/stations")
        status, _c, _b, headers = routes.resolve(req)
        assert status == 303
        assert ("Location", "/login") in headers

    def test_stations_get_renders_when_authenticated(self, monkeypatch, tmp_path):
        sessions = auth.SessionStore()
        session = sessions.create()
        req = self._ctx(
            monkeypatch,
            tmp_path,
            "GET",
            "/stations",
            sessions=sessions,
            session=session,
        )
        status, _c, body, _h = routes.resolve(req)
        assert status == 200
        assert "Radio stations" in body
        assert "Preset 1" in body
        assert 'name="csrf_token"' in body
        assert body.count('name="logo_file"') == 1
        assert body.count('value="upload_logo"') == 1

    def test_stations_post_requires_csrf(self, monkeypatch, tmp_path):
        sessions = auth.SessionStore()
        session = sessions.create()
        req = self._ctx(
            monkeypatch,
            tmp_path,
            "POST",
            "/stations",
            sessions=sessions,
            session=session,
            form={
                "op": "save",
                "index": "0",
                "name": "X",
                "url": "https://x.example/s",
                "csrf_token": "wrong",
            },
        )
        status, _c, _b, _h = routes.resolve(req)
        assert status == 403

    def test_stations_save_writes_and_redirects(self, monkeypatch, tmp_path):
        from radio_web import stations_store

        sessions = auth.SessionStore()
        session = sessions.create()
        req = self._ctx(
            monkeypatch,
            tmp_path,
            "POST",
            "/stations",
            sessions=sessions,
            session=session,
            form={
                "op": "save",
                "index": "0",
                "name": "MyStation",
                "url": "https://my.example/s",
                "logo": "",
                "csrf_token": session.csrf_token,
            },
        )
        status, _c, _b, headers = routes.resolve(req)
        assert status == 303
        assert ("Location", "/stations?msg=saved") in headers
        assert "MyStation" in stations_store.load_stations()[0]["name"]

    def test_stations_upload_requires_auth_and_csrf(self, monkeypatch, tmp_path):
        req = self._ctx(monkeypatch, tmp_path, "POST", "/stations", form={"op": "upload_logo"})
        status, _c, _b, headers = routes.resolve(req)
        assert status == 303
        assert ("Location", "/login") in headers

        sessions = auth.SessionStore()
        session = sessions.create()
        req = self._ctx(
            monkeypatch,
            tmp_path,
            "POST",
            "/stations",
            sessions=sessions,
            session=session,
            form={"op": "upload_logo", "csrf_token": "wrong"},
        )
        status, _c, _b, _headers = routes.resolve(req)
        assert status == 403

    def test_stations_save_invalid_url_shows_error(self, monkeypatch, tmp_path):
        sessions = auth.SessionStore()
        session = sessions.create()
        req = self._ctx(
            monkeypatch,
            tmp_path,
            "POST",
            "/stations",
            sessions=sessions,
            session=session,
            form={
                "op": "save",
                "index": "0",
                "name": "Bad",
                "url": "ftp://nope/x",
                "csrf_token": session.csrf_token,
            },
        )
        status, _c, body, _h = routes.resolve(req)
        assert status == 200
        assert "http" in body.lower()

    def test_stations_restore_redirects(self, monkeypatch, tmp_path):
        sessions = auth.SessionStore()
        session = sessions.create()
        req = self._ctx(
            monkeypatch,
            tmp_path,
            "POST",
            "/stations",
            sessions=sessions,
            session=session,
            form={"op": "restore", "csrf_token": session.csrf_token},
        )
        status, _c, _b, headers = routes.resolve(req)
        assert status == 303
        assert ("Location", "/stations?msg=restored") in headers


class TestSourceRoutes:
    """Phase 6 source feature-flag routes: auth + CSRF gating and PRG."""

    def _ctx(self, monkeypatch, tmp_path, method, path, **kw):
        monkeypatch.setattr("radio_web.config_store.MANAGED_CONFIG_DIR", str(tmp_path))
        kw.setdefault("sessions", auth.SessionStore())
        kw.setdefault("rate_limiter", auth.RateLimiter())
        return routes.Request(method=method, path=path, **kw)

    def test_sources_get_requires_auth(self, monkeypatch, tmp_path):
        req = self._ctx(monkeypatch, tmp_path, "GET", "/sources")
        status, _c, _b, headers = routes.resolve(req)
        assert status == 303
        assert ("Location", "/login") in headers

    def test_sources_get_renders_when_authenticated(self, monkeypatch, tmp_path):
        sessions = auth.SessionStore()
        session = sessions.create()
        req = self._ctx(
            monkeypatch,
            tmp_path,
            "GET",
            "/sources",
            sessions=sessions,
            session=session,
        )
        status, _c, body, _h = routes.resolve(req)
        assert status == 200
        assert "Music sources" in body
        assert "Internet Radio" in body
        assert ">SSH<" not in body
        assert 'name="csrf_token"' in body
        # The internal-dependency notes (D-Bus/Avahi) are no longer shown.
        assert "D-Bus" not in body
        assert "Dependencies" not in body

    def test_sources_post_requires_csrf(self, monkeypatch, tmp_path):
        sessions = auth.SessionStore()
        session = sessions.create()
        req = self._ctx(
            monkeypatch,
            tmp_path,
            "POST",
            "/sources",
            sessions=sessions,
            session=session,
            form={"op": "save", "internet_radio": "true", "csrf_token": "wrong"},
        )
        status, _c, _b, _h = routes.resolve(req)
        assert status == 403

    def test_sources_save_writes_and_redirects(self, monkeypatch, tmp_path):
        from radio_web import sources_store

        sessions = auth.SessionStore()
        session = sessions.create()
        # Only internet_radio checked -> everything else disabled.
        req = self._ctx(
            monkeypatch,
            tmp_path,
            "POST",
            "/sources",
            sessions=sessions,
            session=session,
            form={"op": "save", "internet_radio": "true", "csrf_token": session.csrf_token},
        )
        status, _c, _b, headers = routes.resolve(req)
        assert status == 303
        assert ("Location", "/sources?msg=saved") in headers
        flags = sources_store.load_sources()
        assert flags["internet_radio"] is True
        assert flags["bluetooth"] is False
        assert "ssh" not in flags

    def test_sources_save_unknown_key_shows_error(self, monkeypatch, tmp_path):
        sessions = auth.SessionStore()
        session = sessions.create()
        req = self._ctx(
            monkeypatch,
            tmp_path,
            "POST",
            "/sources",
            sessions=sessions,
            session=session,
            form={"op": "save", "root_password": "x", "csrf_token": session.csrf_token},
        )
        status, _c, body, _h = routes.resolve(req)
        assert status == 200
        assert "Unknown source" in body

    def test_sources_apply_restarts_radio(self, monkeypatch, tmp_path):
        from radio_web import actions

        sessions = auth.SessionStore()
        session = sessions.create()
        monkeypatch.setattr(actions, "run_action", lambda *a, **k: (True, "ok"))
        req = self._ctx(
            monkeypatch,
            tmp_path,
            "POST",
            "/sources",
            sessions=sessions,
            session=session,
            form={
                "op": "apply",
                "internet_radio": "true",
                "airplay": "true",
                "csrf_token": session.csrf_token,
            },
        )
        status, _c, _b, headers = routes.resolve(req)
        assert status == 303
        assert ("Location", "/sources?msg=applied") in headers

    def test_sources_restore_redirects(self, monkeypatch, tmp_path):
        sessions = auth.SessionStore()
        session = sessions.create()
        req = self._ctx(
            monkeypatch,
            tmp_path,
            "POST",
            "/sources",
            sessions=sessions,
            session=session,
            form={"op": "restore", "csrf_token": session.csrf_token},
        )
        status, _c, _b, headers = routes.resolve(req)
        assert status == 303
        assert ("Location", "/sources?msg=restored") in headers


class TestDeviceRoutes:
    """Phase 7 device-settings routes: auth + CSRF gating and PRG."""

    def _ctx(self, monkeypatch, tmp_path, method, path, **kw):
        monkeypatch.setattr("radio_web.config_store.MANAGED_CONFIG_DIR", str(tmp_path))
        monkeypatch.setattr(
            "radio_web.device_store.HOSTNAME_LOCK_MARKER",
            str(tmp_path / "absent"),
        )
        monkeypatch.setattr("radio_web.actions.run_action", lambda *a, **k: (True, "ok"))
        monkeypatch.setattr(
            "radio_web.device_store.available_timezones",
            lambda: ["America/New_York", "Europe/Berlin", "UTC"],
        )
        kw.setdefault("sessions", auth.SessionStore())
        kw.setdefault("rate_limiter", auth.RateLimiter())
        return routes.Request(method=method, path=path, **kw)

    def test_device_get_requires_auth(self, monkeypatch, tmp_path):
        req = self._ctx(monkeypatch, tmp_path, "GET", "/device")
        status, _c, _b, headers = routes.resolve(req)
        assert status == 303
        assert ("Location", "/login") in headers

    def test_device_get_renders(self, monkeypatch, tmp_path):
        sessions = auth.SessionStore()
        session = sessions.create()
        req = self._ctx(
            monkeypatch,
            tmp_path,
            "GET",
            "/device",
            sessions=sessions,
            session=session,
        )
        status, _c, body, _h = routes.resolve(req)
        assert status == 200
        assert "Device settings" in body
        assert 'name="csrf_token"' in body
        assert '<select name="timezone" required>' in body
        assert '<option value="Europe/Berlin"' in body
        assert "Expand root filesystem" not in body
        assert "Enable SSH" in body

    def test_device_post_requires_csrf(self, monkeypatch, tmp_path):
        sessions = auth.SessionStore()
        session = sessions.create()
        req = self._ctx(
            monkeypatch,
            tmp_path,
            "POST",
            "/device",
            sessions=sessions,
            session=session,
            form={"op": "save", "name": "radio1", "csrf_token": "wrong"},
        )
        status, _c, _b, _h = routes.resolve(req)
        assert status == 403

    def test_device_save_writes_and_redirects(self, monkeypatch, tmp_path):
        from radio_web import device_store

        sessions = auth.SessionStore()
        session = sessions.create()
        req = self._ctx(
            monkeypatch,
            tmp_path,
            "POST",
            "/device",
            sessions=sessions,
            session=session,
            form={
                "op": "save",
                "name": "radio1",
                "timezone": "Europe/Berlin",
                "ntp_server": "pool.ntp.org",
                "ssh_enabled": "false",
                "csrf_token": session.csrf_token,
            },
        )
        status, _c, _b, headers = routes.resolve(req)
        assert status == 303
        assert ("Location", "/device?msg=saved") in headers
        assert device_store.load_device()["name"] == "radio1"
        assert device_store.load_device()["ssh_enabled"] == "false"

    def test_device_save_invalid_name_shows_error(self, monkeypatch, tmp_path):
        sessions = auth.SessionStore()
        session = sessions.create()
        req = self._ctx(
            monkeypatch,
            tmp_path,
            "POST",
            "/device",
            sessions=sessions,
            session=session,
            form={"op": "save", "name": "bad name!", "csrf_token": session.csrf_token},
        )
        status, _c, body, _h = routes.resolve(req)
        assert status == 200
        assert "Device name" in body

    def test_device_locked_hostname_field_disabled(self, monkeypatch, tmp_path):
        sessions = auth.SessionStore()
        session = sessions.create()
        req = self._ctx(
            monkeypatch,
            tmp_path,
            "GET",
            "/device",
            sessions=sessions,
            session=session,
        )
        monkeypatch.setattr("radio_web.device_store.sd_card_hostname_locked", lambda: True)
        status, _c, body, _h = routes.resolve(req)
        assert status == 200
        assert "SD card" in body
        assert "disabled" in body

    def test_device_restore_redirects(self, monkeypatch, tmp_path):
        sessions = auth.SessionStore()
        session = sessions.create()
        req = self._ctx(
            monkeypatch,
            tmp_path,
            "POST",
            "/device",
            sessions=sessions,
            session=session,
            form={"op": "restore", "csrf_token": session.csrf_token},
        )
        status, _c, _b, headers = routes.resolve(req)
        assert status == 303
        assert ("Location", "/device?msg=restored") in headers


class TestMaintenanceRoutes:
    """Phase 7 maintenance routes: restart, confirm + fresh auth, diagnostics."""

    def _ctx(self, monkeypatch, tmp_path, method, path, **kw):
        monkeypatch.setattr("radio_web.config_store.MANAGED_CONFIG_DIR", str(tmp_path))
        monkeypatch.setattr("radio_web.actions.run_action", lambda *a, **k: (True, "done"))
        monkeypatch.setattr(
            "radio_web.actions.query_firmware_status",
            lambda: (True, "available", {"phase": "idle", "running_slot": "A"}),
        )
        kw.setdefault("sessions", auth.SessionStore())
        kw.setdefault("rate_limiter", auth.RateLimiter())
        return routes.Request(method=method, path=path, **kw)

    def test_maintenance_get_requires_auth(self, monkeypatch, tmp_path):
        req = self._ctx(monkeypatch, tmp_path, "GET", "/maintenance")
        status, _c, _b, headers = routes.resolve(req)
        assert status == 303
        assert ("Location", "/login") in headers

    def test_firmware_get_requires_auth(self, monkeypatch, tmp_path):
        req = self._ctx(monkeypatch, tmp_path, "GET", "/firmware")
        status, _c, _b, headers = routes.resolve(req)
        assert status == 303
        assert ("Location", "/login") in headers

    def test_firmware_get_redirects_to_maintenance(self, monkeypatch, tmp_path):
        sessions = auth.SessionStore()
        session = sessions.create()
        req = self._ctx(
            monkeypatch,
            tmp_path,
            "GET",
            "/firmware",
            sessions=sessions,
            session=session,
        )
        status, _c, _body, headers = routes.resolve(req)
        assert status == 303
        assert ("Location", "/maintenance") in headers

    def test_firmware_status_requires_auth(self, monkeypatch, tmp_path):
        req = self._ctx(monkeypatch, tmp_path, "GET", "/firmware/status")
        status, content_type, body, _headers = routes.resolve(req)
        assert status == 401
        assert content_type.startswith("application/json")
        assert json.loads(body)["ok"] is False

    @pytest.mark.parametrize(
        "path",
        [
            "/firmware/authorize",
            "/firmware/switch/confirm",
            "/firmware/switch",
        ],
    )
    def test_every_firmware_mutation_requires_csrf(self, monkeypatch, tmp_path, path):
        sessions = auth.SessionStore()
        session = sessions.create()
        actions_seen = []
        monkeypatch.setattr(
            "radio_web.actions.run_action",
            lambda action, **args: (actions_seen.append((action, args)) is None, "unexpected"),
        )
        req = self._ctx(
            monkeypatch,
            tmp_path,
            "POST",
            path,
            sessions=sessions,
            session=session,
            form={"csrf_token": "wrong", "confirmed": "1", "password": "wrong"},
        )
        status, _content_type, _body, _headers = routes.resolve(req)
        assert status == 403
        assert actions_seen == []

    def test_update_authorization_requires_fresh_password(self, monkeypatch, tmp_path):
        monkeypatch.setattr("radio_web.config_store.MANAGED_CONFIG_DIR", str(tmp_path))
        auth.set_password("correct-horse")
        sessions = auth.SessionStore()
        session = sessions.create()
        req = self._ctx(
            monkeypatch,
            tmp_path,
            "POST",
            "/firmware/authorize",
            sessions=sessions,
            session=session,
            form={
                "password": "wrong",
                "csrf_token": session.csrf_token,
            },
        )
        status, _content_type, body, _headers = routes.resolve(req)
        assert status == 401
        assert json.loads(body)["message"] == "Incorrect password."

    def test_update_authorization_returns_upload_grant(self, monkeypatch, tmp_path):
        monkeypatch.setattr("radio_web.config_store.MANAGED_CONFIG_DIR", str(tmp_path))
        monkeypatch.setattr("radio_web.firmware_tracking.TRACKING_PATH", tmp_path / "current.json")
        auth.set_password("correct-horse")
        sessions = auth.SessionStore()
        session = sessions.create()
        req = routes.Request(
            method="POST",
            path="/firmware/authorize",
            sessions=sessions,
            session=session,
            form={
                "password": "correct-horse",
                "csrf_token": session.csrf_token,
            },
        )
        status, _content_type, body, _headers = routes.resolve(req)
        assert status == 200
        payload = json.loads(body)
        assert payload["ok"] is True
        assert payload["grant"]
        assert payload["tracking_token"]

    def test_maintenance_contains_guided_firmware_wizard(self, monkeypatch, tmp_path):
        sessions = auth.SessionStore()
        session = sessions.create()
        req = self._ctx(
            monkeypatch,
            tmp_path,
            "GET",
            "/maintenance",
            sessions=sessions,
            session=session,
        )
        status, _c, body, _headers = routes.resolve(req)
        assert status == 200
        assert 'href="/firmware"' not in body
        assert 'id="firmware-wizard"' in body
        assert 'id="firmware-update-open"' in body
        assert 'src="/static/app.js?v=10"' in body
        assert "Running version" in body
        assert "Switch to previous firmware" in body
        assert "Audio services" in body
        assert "Restart player" in body
        assert "Device power" in body
        assert "Reboot device" in body
        assert "Shut down device" in body
        assert 'class="btn-primary" type="button" id="firmware-update-open"' in body
        assert '<div class="btnrow maintenance-actions">' in body
        assert '<div class="btnrow firmware-actions maintenance-actions">' in body
        assert '<p class="btnrow firmware-actions' not in body

    def test_shared_button_roles_are_explicit(self):
        html = templates.sources_page({}, [], {}, "csrf")
        assert 'class="btn-primary" type="submit" name="op" value="save"' in html
        assert 'class="btn-apply" type="submit" name="op" value="apply"' in html
        assert 'class="btn-danger" type="submit" name="op" value="restore"' in html

    def test_maintenance_shows_helper_approved_rollback(self, monkeypatch, tmp_path):
        sessions = auth.SessionStore()
        session = sessions.create()
        req = self._ctx(
            monkeypatch,
            tmp_path,
            "GET",
            "/maintenance",
            sessions=sessions,
            session=session,
        )
        monkeypatch.setattr(
            "radio_web.actions.query_firmware_status",
            lambda: (
                True,
                "available",
                {
                    "running_version": "1.2.0",
                    "running_slot": "B",
                    "other_version": "1.1.0",
                    "other_slot": "A",
                    "switch_allowed": True,
                    "switch_label": "Roll back to 1.1.0",
                },
            ),
        )
        status, _content_type, body, _headers = routes.resolve(req)
        assert status == 200
        assert "Previous version</dt><dd>1.1.0" in body
        assert "Switch to previous firmware" in body
        assert 'action="/firmware/switch/confirm"' in body

    def test_firmware_switch_requires_current_password(self, monkeypatch, tmp_path):
        monkeypatch.setattr("radio_web.config_store.MANAGED_CONFIG_DIR", str(tmp_path))
        auth.set_password("correct-horse")
        firmware = {
            "running_version": "1.2.0",
            "running_slot": "B",
            "other_version": "1.1.0",
            "other_slot": "A",
            "switch_allowed": True,
            "switch_label": "Roll back to 1.1.0",
        }
        monkeypatch.setattr(
            "radio_web.actions.query_firmware_status", lambda: (True, "available", firmware)
        )
        sessions = auth.SessionStore()
        session = sessions.create()
        req = routes.Request(
            method="POST",
            path="/firmware/switch",
            sessions=sessions,
            session=session,
            form={
                "confirmed": "1",
                "password": "wrong",
                "csrf_token": session.csrf_token,
            },
        )
        status, _content_type, body, _headers = routes.resolve(req)
        assert status == 401
        assert "Incorrect password" in body

    def test_firmware_switch_dispatches_only_fixed_action(self, monkeypatch, tmp_path):
        monkeypatch.setattr("radio_web.config_store.MANAGED_CONFIG_DIR", str(tmp_path))
        auth.set_password("correct-horse")
        firmware = {
            "running_version": "1.2.0",
            "running_slot": "B",
            "other_version": "1.1.0",
            "other_slot": "A",
            "switch_allowed": True,
            "switch_label": "Roll back to 1.1.0",
        }
        monkeypatch.setattr(
            "radio_web.actions.query_firmware_status", lambda: (True, "available", firmware)
        )
        seen = []
        monkeypatch.setattr(
            "radio_web.actions.run_action",
            lambda action, **args: (seen.append((action, args)) is None, "switching"),
        )
        sessions = auth.SessionStore()
        session = sessions.create()
        req = routes.Request(
            method="POST",
            path="/firmware/switch",
            sessions=sessions,
            session=session,
            form={
                "confirmed": "1",
                "password": "correct-horse",
                "csrf_token": session.csrf_token,
            },
        )
        status, _content_type, body, _headers = routes.resolve(req)
        assert status == 200
        assert "switching" in body
        assert seen == [("prepare_rollback", {})]

    def test_firmware_switch_rechecks_helper_eligibility(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            "radio_web.actions.query_firmware_status",
            lambda: (
                True,
                "available",
                {"switch_allowed": False, "switch_reason": "A firmware trial is active."},
            ),
        )
        sessions = auth.SessionStore()
        session = sessions.create()
        req = routes.Request(
            method="POST",
            path="/firmware/switch/confirm",
            sessions=sessions,
            session=session,
            form={"csrf_token": session.csrf_token},
        )
        status, _content_type, body, _headers = routes.resolve(req)
        assert status == 409
        assert "trial is active" in body

    def test_restart_radio_redirects(self, monkeypatch, tmp_path):
        sessions = auth.SessionStore()
        session = sessions.create()
        req = self._ctx(
            monkeypatch,
            tmp_path,
            "POST",
            "/maintenance",
            sessions=sessions,
            session=session,
            form={"action": "restart_radio", "csrf_token": session.csrf_token},
        )
        status, _c, _b, headers = routes.resolve(req)
        assert status == 303
        assert ("Location", "/maintenance?msg=restart_radio") in headers

    def test_reboot_requires_confirmation(self, monkeypatch, tmp_path):
        sessions = auth.SessionStore()
        session = sessions.create()
        req = self._ctx(
            monkeypatch,
            tmp_path,
            "POST",
            "/maintenance",
            sessions=sessions,
            session=session,
            form={"action": "reboot", "csrf_token": session.csrf_token},
        )
        status, _c, body, _h = routes.resolve(req)
        assert status == 200
        assert "Confirm" in body
        assert 'type="password"' in body

    def test_reboot_wrong_password_rejected(self, monkeypatch, tmp_path):
        monkeypatch.setattr("radio_web.config_store.MANAGED_CONFIG_DIR", str(tmp_path))
        auth.set_password("correct-horse")
        sessions = auth.SessionStore()
        session = sessions.create()
        req = self._ctx(
            monkeypatch,
            tmp_path,
            "POST",
            "/maintenance",
            sessions=sessions,
            session=session,
            form={
                "action": "reboot",
                "confirmed": "1",
                "password": "wrong",
                "csrf_token": session.csrf_token,
            },
        )
        status, _c, body, _h = routes.resolve(req)
        assert status == 401
        assert "Incorrect password" in body

    def test_reboot_correct_password_runs(self, monkeypatch, tmp_path):
        monkeypatch.setattr("radio_web.config_store.MANAGED_CONFIG_DIR", str(tmp_path))
        auth.set_password("correct-horse")
        sessions = auth.SessionStore()
        session = sessions.create()
        # _ctx stubs run_action to return (True, "done").
        req = self._ctx(
            monkeypatch,
            tmp_path,
            "POST",
            "/maintenance",
            sessions=sessions,
            session=session,
            form={
                "action": "reboot",
                "confirmed": "1",
                "password": "correct-horse",
                "csrf_token": session.csrf_token,
            },
        )
        status, _c, body, _h = routes.resolve(req)
        assert status == 200
        # Success renders the maintenance page with the helper's message.
        assert "done" in body
        assert "Maintenance" in body

    def test_diagnostics_requires_auth(self, monkeypatch, tmp_path):
        req = self._ctx(monkeypatch, tmp_path, "GET", "/diagnostics")
        status, _c, _b, headers = routes.resolve(req)
        assert status == 303
        assert ("Location", "/login") in headers

    def test_diagnostics_download(self, monkeypatch, tmp_path):
        monkeypatch.setattr("radio_web.diagnostics.LOG_GLOBS", ())
        monkeypatch.setattr("radio_web.diagnostics._run", lambda cmd: "")
        monkeypatch.setattr("radio_web.diagnostics._dmesg_tail", lambda lines=200: "")
        sessions = auth.SessionStore()
        session = sessions.create()
        req = self._ctx(
            monkeypatch,
            tmp_path,
            "GET",
            "/diagnostics",
            sessions=sessions,
            session=session,
        )
        status, ctype, body, headers = routes.resolve(req)
        assert status == 200
        assert ctype == "application/gzip"
        assert isinstance(body, bytes)
        assert any("attachment" in v for _n, v in headers)

    def test_maintenance_contains_backup_and_restore_dialogs(self, monkeypatch, tmp_path):
        sessions = auth.SessionStore()
        session = sessions.create()
        req = self._ctx(
            monkeypatch,
            tmp_path,
            "GET",
            "/maintenance",
            sessions=sessions,
            session=session,
        )
        status, _ctype, body, _headers = routes.resolve(req)
        assert status == 200
        assert 'id="backup-open"' in body
        assert 'id="restore-open"' in body
        assert 'id="backup-dialog"' in body
        assert 'id="restore-dialog"' in body
        assert "Backup archive" not in body
        assert "Save your radio settings and connections to a backup file" in body
        assert body.count('name="password"') == 2
        assert 'name="backup_file"' in body
        assert 'name="confirmed" value="1"' in body
        card = body.split('<div class="card"><h2>Backup &amp; restore</h2>', 1)[1].split(
            '<dialog id="backup-dialog"', 1
        )[0]
        assert 'type="password"' not in card

    def test_backup_download_requires_auth(self, monkeypatch, tmp_path):
        req = self._ctx(monkeypatch, tmp_path, "POST", "/backup/download")
        status, _ctype, _body, headers = routes.resolve(req)
        assert status == 303
        assert ("Location", "/login") in headers

    def test_restore_requires_csrf(self, monkeypatch, tmp_path):
        sessions = auth.SessionStore()
        session = sessions.create()
        req = self._ctx(
            monkeypatch,
            tmp_path,
            "POST",
            "/restore",
            sessions=sessions,
            session=session,
            form={"password": "unused"},
        )
        status, _ctype, _body, _headers = routes.resolve(req)
        assert status == 403

    def test_restore_requires_explicit_confirmation(self, monkeypatch, tmp_path):
        sessions = auth.SessionStore()
        session = sessions.create()
        req = self._ctx(
            monkeypatch,
            tmp_path,
            "POST",
            "/restore",
            sessions=sessions,
            session=session,
            form={"password": "unused", "csrf_token": session.csrf_token},
        )
        status, _ctype, body, _headers = routes.resolve(req)
        assert status == 400
        assert "Confirm that the current settings may be replaced" in body


class TestSettingsRoutes:
    """Area A display & audio routes: auth + CSRF gating and PRG."""

    def _ctx(self, monkeypatch, tmp_path, method, path, **kw):
        monkeypatch.setattr("radio_web.config_store.MANAGED_CONFIG_DIR", str(tmp_path))
        kw.setdefault("sessions", auth.SessionStore())
        kw.setdefault("rate_limiter", auth.RateLimiter())
        return routes.Request(method=method, path=path, **kw)

    def _authed(self, monkeypatch, tmp_path, method, path, **kw):
        sessions = auth.SessionStore()
        session = sessions.create()
        return self._ctx(
            monkeypatch,
            tmp_path,
            method,
            path,
            sessions=sessions,
            session=session,
            **kw,
        )

    def test_settings_get_requires_auth(self, monkeypatch, tmp_path):
        req = self._ctx(monkeypatch, tmp_path, "GET", "/settings")
        status, _c, _b, headers = routes.resolve(req)
        assert status == 303
        assert ("Location", "/login") in headers

    def test_settings_get_renders(self, monkeypatch, tmp_path):
        req = self._authed(monkeypatch, tmp_path, "GET", "/settings")
        status, _c, body, _h = routes.resolve(req)
        assert status == 200
        # The page is display-only now: audio (max volume / mixer) moved to the
        # Audio page and Physical controls moved to Device settings.
        assert "<h1>Display</h1>" in body
        assert "<h2>Display</h2>" in body
        assert "<h2>Audio</h2>" not in body
        assert "Maximum volume" not in body
        assert "ALSA mixer name" not in body
        assert "Physical controls" not in body
        assert 'href="/debug/adc"' not in body
        assert 'form="display-settings"' in body

    def test_settings_post_bad_csrf_403(self, monkeypatch, tmp_path):
        req = self._authed(
            monkeypatch,
            tmp_path,
            "POST",
            "/settings",
            form={"op": "save", "csrf_token": "wrong"},
        )
        status, _c, _b, _h = routes.resolve(req)
        assert status == 403

    def test_settings_post_save_prg(self, monkeypatch, tmp_path):
        sessions = auth.SessionStore()
        session = sessions.create()
        form = {
            "op": "save",
            "csrf_token": session.csrf_token,
            "theme_preset": "default",
            "animations": "true",
            "idle_timeout": "30",
            "crossfade_ms": "150",
            "clock_size": "24",
            "osd_duration": "1.5",
            "toast_duration": "1.6",
        }
        req = self._ctx(
            monkeypatch,
            tmp_path,
            "POST",
            "/settings",
            sessions=sessions,
            session=session,
            form=form,
        )
        status, _c, _b, headers = routes.resolve(req)
        assert status == 303
        assert ("Location", "/settings?msg=saved") in headers


class TestAudioHardwareRoutes:
    """Step 5 sound-card routes: auth + CSRF gating, save → dispatch → confirm."""

    def _ctx(self, monkeypatch, tmp_path, method, **kw):
        monkeypatch.setattr("radio_web.config_store.MANAGED_CONFIG_DIR", str(tmp_path))
        kw.setdefault("sessions", auth.SessionStore())
        kw.setdefault("rate_limiter", auth.RateLimiter())
        return routes.Request(method=method, path="/audio-hardware", **kw)

    def _authed(self, monkeypatch, tmp_path, method, **kw):
        sessions = auth.SessionStore()
        session = sessions.create()
        return self._ctx(monkeypatch, tmp_path, method, sessions=sessions, session=session, **kw)

    def test_get_requires_auth(self, monkeypatch, tmp_path):
        status, _c, _b, headers = routes.resolve(self._ctx(monkeypatch, tmp_path, "GET"))
        assert status == 303
        assert ("Location", "/login") in headers

    def test_get_renders_picker_with_current(self, monkeypatch, tmp_path):
        status, _c, body, _h = routes.resolve(self._authed(monkeypatch, tmp_path, "GET"))
        assert status == 200
        assert "<h1>Audio</h1>" in body
        assert "IQaudIO DAC+" in body and "Built-in headphone jack" in body
        assert "Generic PCM5102A" in body and "MERUS Amp" in body
        assert "Kernel-supported" in body and "Experimental" in body
        assert "Requires display CS on SPI0 CE1" in body
        # Default is selected when there is no managed override.
        assert 'value="headphones" selected' in body
        assert 'value="iqaudio_dac" selected' not in body
        # The maximum-volume control now lives on this page.
        assert "Maximum volume" in body
        assert 'name="max_volume"' in body
        assert body.count('value="apply_all"') == 1
        assert "Apply changes" in body
        assert 'id="audio-hardware-form"' in body
        assert 'id="equalizer-form"' in body
        assert body.index('value="restore"') > body.index('id="equalizer-form"')
        assert body.index('id="parametric-equalizer"') < body.index('id="audio-output-settings"')
        hardware_form = body.split('id="audio-hardware-form"', 1)[1].split("</form>", 1)[0]
        equalizer_form = body.split('id="equalizer-form"', 1)[1].split("</form>", 1)[0]
        assert 'name="profile"' in hardware_form
        assert 'name="usb_audio_mode"' in hardware_form
        assert 'name="max_volume"' in hardware_form
        assert 'name="eq_enabled"' not in hardware_form
        assert 'name="eq_enabled"' in equalizer_form
        assert 'name="profile"' not in equalizer_form
        assert 'id="usb-audio-form"' not in body
        assert 'id="audio-volume-form"' not in body
        assert 'id="eq-graph"' in body
        assert 'name="eq_enabled"' in body
        assert body.count('class="eq-band"') == 10
        assert 'value="apply_equalizer"' in body
        assert 'name="ajax" value=""' in body
        assert 'id="eq-flash"' in body
        assert 'src="/static/app.js?v=17"' in body
        assert 'name="eq_preamp_db" min="-24" max="0" step="0.1" value="-3.0"' in body
        assert "Save and Apply updates the sound live without interrupting playback" in body

        script = (Path(routes.__file__).with_name("static") / "app.js").read_text(
            encoding="utf-8"
        )
        stylesheet = (Path(routes.__file__).with_name("static") / "app.css").read_text(
            encoding="utf-8"
        )
        assert "if (!pointEnabled(index)) return" in script
        assert "enabledControl.checked && bandValues(bands[index]).enabled" in script
        assert 'body.set("ajax", "1")' in script
        assert 'setAttribute("tabindex", interactive ? "0" : "-1")' in script
        assert ".eq-point { cursor: default; pointer-events: none" in stylesheet
        assert ".eq-point.enabled { cursor: grab; pointer-events: auto" in stylesheet

    def test_post_apply_equalizer_persists_and_dispatches(self, monkeypatch, tmp_path):
        from radio_web import equalizer_store

        seen = []
        monkeypatch.setattr(
            "radio_web.actions.run_action",
            lambda action_id, **args: (seen.append((action_id, args)) is None, "applied"),
        )
        sessions = auth.SessionStore()
        session = sessions.create()
        form = {
            key: str(value).lower() if isinstance(value, bool) else str(value)
            for key, value in equalizer_store.settings_as_form(equalizer_store.defaults()).items()
        }
        form.update(
            {
                "op": "apply_equalizer",
                "csrf_token": session.csrf_token,
                "eq_enabled": "true",
                "eq_band_1_enabled": "true",
                "eq_band_1_gain_db": "4.5",
            }
        )
        req = self._ctx(
            monkeypatch,
            tmp_path,
            "POST",
            sessions=sessions,
            session=session,
            form=form,
        )
        status, _content_type, _body, headers = routes.resolve(req)
        assert status == 303
        assert ("Location", "/audio-hardware?msg=equalizer_applied") in headers
        assert seen == [("apply_equalizer", {})]
        assert equalizer_store.load_equalizer()["bands"][0]["gain_db"] == 4.5

    def test_post_apply_equalizer_ajax_returns_json_without_redirect(
        self, monkeypatch, tmp_path
    ):
        import json

        from radio_web import equalizer_store

        seen = []
        monkeypatch.setattr(
            "radio_web.actions.run_action",
            lambda action_id, **args: (seen.append((action_id, args)) is None, "applied"),
        )
        sessions = auth.SessionStore()
        session = sessions.create()
        form = {
            key: str(value).lower() if isinstance(value, bool) else str(value)
            for key, value in equalizer_store.settings_as_form(equalizer_store.defaults()).items()
        }
        form.update(
            {
                "op": "apply_equalizer",
                "ajax": "1",
                "csrf_token": session.csrf_token,
                "eq_enabled": "true",
                "eq_band_1_enabled": "true",
                "eq_band_1_gain_db": "4.5",
            }
        )
        req = self._ctx(
            monkeypatch, tmp_path, "POST", sessions=sessions, session=session, form=form
        )
        status, content_type, body, headers = routes.resolve(req)
        assert status == 200
        assert content_type.startswith("application/json")
        assert not any(name == "Location" for name, _value in headers)
        payload = json.loads(body)
        assert payload == {"ok": True, "message": "Equalizer applied to every audio source."}
        assert seen == [("apply_equalizer", {})]
        assert equalizer_store.load_equalizer()["bands"][0]["gain_db"] == 4.5

    # The UI no longer exposes a Save-only EQ button; save-only AJAX test removed.

    def test_post_equalizer_ajax_validation_error_returns_json(self, monkeypatch, tmp_path):
        import json

        from radio_web import equalizer_store

        seen = []
        monkeypatch.setattr(
            "radio_web.actions.run_action",
            lambda action_id, **args: (seen.append((action_id, args)) is None, "x"),
        )
        sessions = auth.SessionStore()
        session = sessions.create()
        form = {
            key: str(value).lower() if isinstance(value, bool) else str(value)
            for key, value in equalizer_store.settings_as_form(equalizer_store.defaults()).items()
        }
        # Out-of-range preamp (valid range is -24..0) is rejected by the store.
        form.update(
            {
                "op": "apply_equalizer",
                "ajax": "1",
                "csrf_token": session.csrf_token,
                "eq_preamp_db": "99",
            }
        )
        req = self._ctx(
            monkeypatch, tmp_path, "POST", sessions=sessions, session=session, form=form
        )
        status, content_type, body, _headers = routes.resolve(req)
        assert status == 200
        assert content_type.startswith("application/json")
        payload = json.loads(body)
        assert payload["ok"] is False
        assert payload["message"]
        assert seen == []

    def test_post_bad_csrf_403(self, monkeypatch, tmp_path):
        req = self._authed(
            monkeypatch,
            tmp_path,
            "POST",
            form={"op": "apply", "csrf_token": "wrong", "profile": "headphones"},
        )
        status, _c, _b, _h = routes.resolve(req)
        assert status == 403

    def test_unified_apply_with_no_changes_does_not_restart(self, monkeypatch, tmp_path):
        seen = []
        monkeypatch.setattr(
            "radio_web.actions.run_action",
            lambda action_id, **args: (seen.append((action_id, args)) is None, "ok"),
        )
        sessions = auth.SessionStore()
        session = sessions.create()
        req = self._ctx(
            monkeypatch,
            tmp_path,
            "POST",
            sessions=sessions,
            session=session,
            form={
                "op": "apply_all",
                "csrf_token": session.csrf_token,
                "profile": "headphones",
                "usb_audio_mode": "uac1",
                "max_volume": "100",
            },
        )
        status, _c, _b, headers = routes.resolve(req)
        assert status == 303
        assert ("Location", "/audio-hardware?msg=unchanged") in headers
        assert seen == []

    def test_unified_apply_reboot_change_saves_every_setting(self, monkeypatch, tmp_path):
        from radio_web import audio_store, usb_audio_store

        seen = []
        monkeypatch.setattr(
            "radio_web.actions.run_action",
            lambda action_id, **args: (seen.append((action_id, args)) is None, "configured"),
        )
        sessions = auth.SessionStore()
        session = sessions.create()
        req = self._ctx(
            monkeypatch,
            tmp_path,
            "POST",
            sessions=sessions,
            session=session,
            form={
                "op": "apply_all",
                "csrf_token": session.csrf_token,
                "profile": "iqaudio_dac",
                "usb_audio_mode": "uac2",
                "max_volume": "80",
            },
        )
        status, _c, body, _headers = routes.resolve(req)
        assert status == 200
        assert "Reboot required" in body
        assert "disconnect and reconnect the USB host" in body
        assert seen == [("set_audio_hardware", {"profile": "iqaudio_dac"})]
        assert audio_hardware_store.load_profile() == "iqaudio_dac"
        assert usb_audio_store.load_mode() == "uac2"
        assert audio_store.load_audio()["max_volume"] == "80"

    def test_unified_apply_volume_only_restarts_player(self, monkeypatch, tmp_path):
        from radio_web import audio_store

        seen = []
        monkeypatch.setattr(
            "radio_web.actions.run_action",
            lambda action_id, **args: (seen.append((action_id, args)) is None, "restarted"),
        )
        sessions = auth.SessionStore()
        session = sessions.create()
        req = self._ctx(
            monkeypatch,
            tmp_path,
            "POST",
            sessions=sessions,
            session=session,
            form={
                "op": "apply_all",
                "csrf_token": session.csrf_token,
                "profile": "headphones",
                "usb_audio_mode": "uac1",
                "max_volume": "70",
            },
        )
        status, _c, _b, headers = routes.resolve(req)
        assert status == 303
        assert ("Location", "/audio-hardware?msg=applied") in headers
        assert seen == [("restart_radio", {})]
        assert audio_store.load_audio()["max_volume"] == "70"

    def test_post_apply_saves_dispatches_and_confirms(self, monkeypatch, tmp_path):
        seen = {}

        def fake_run(action_id, **args):
            seen["action"] = action_id
            seen["args"] = args
            return True, "Sound card changed. Reboot to apply."

        monkeypatch.setattr("radio_web.actions.run_action", fake_run)
        sessions = auth.SessionStore()
        session = sessions.create()
        form = {"op": "apply", "csrf_token": session.csrf_token, "profile": "headphones"}
        req = self._ctx(
            monkeypatch,
            tmp_path,
            "POST",
            sessions=sessions,
            session=session,
            form=form,
        )
        status, _c, body, _h = routes.resolve(req)
        assert status == 200
        assert "Reboot required" in body
        assert "Built-in headphone jack" in body
        assert 'name="action" value="reboot"' in body
        # It saved the selection and dispatched the fixed action with the id.
        assert audio_hardware_store.load_profile() == "headphones"
        assert seen["action"] == "set_audio_hardware"
        assert seen["args"] == {"profile": "headphones"}

    def test_post_apply_invalid_profile_shows_error(self, monkeypatch, tmp_path):
        sessions = auth.SessionStore()
        session = sessions.create()
        form = {"op": "apply", "csrf_token": session.csrf_token, "profile": "bogus"}
        req = self._ctx(
            monkeypatch,
            tmp_path,
            "POST",
            sessions=sessions,
            session=session,
            form=form,
        )
        status, _c, body, _h = routes.resolve(req)
        assert status == 200
        assert "Unknown sound card" in body
        # Nothing persisted; still the default.
        assert audio_hardware_store.load_profile() == "headphones"

    def test_post_apply_helper_failure_shows_error(self, monkeypatch, tmp_path):
        monkeypatch.setattr("radio_web.config_store.MANAGED_CONFIG_DIR", str(tmp_path))
        audio_hardware_store.save_profile("iqaudio_dac")
        monkeypatch.setattr(
            "radio_web.actions.run_action",
            lambda action_id, **a: (False, "The privileged helper is not available."),
        )
        sessions = auth.SessionStore()
        session = sessions.create()
        form = {"op": "apply", "csrf_token": session.csrf_token, "profile": "merus_amp"}
        req = self._ctx(
            monkeypatch,
            tmp_path,
            "POST",
            sessions=sessions,
            session=session,
            form=form,
        )
        status, _c, body, _h = routes.resolve(req)
        assert status == 200
        assert "not available" in body
        assert "Reboot required" not in body
        assert audio_hardware_store.load_profile() == "iqaudio_dac"

    def test_post_restore_redirects(self, monkeypatch, tmp_path):
        sessions = auth.SessionStore()
        session = sessions.create()
        seen = {}

        def fake_run(action_id, **args):
            seen["action"] = action_id
            seen["args"] = args
            return True, "Sound card changed. Reboot to apply."

        monkeypatch.setattr("radio_web.actions.run_action", fake_run)
        # Seed a non-default override first.
        monkeypatch.setattr("radio_web.config_store.MANAGED_CONFIG_DIR", str(tmp_path))
        audio_hardware_store.save_profile("iqaudio_dac")
        form = {"op": "restore", "csrf_token": session.csrf_token}
        req = self._ctx(
            monkeypatch,
            tmp_path,
            "POST",
            sessions=sessions,
            session=session,
            form=form,
        )
        status, _c, _b, headers = routes.resolve(req)
        assert status == 303
        assert ("Location", "/audio-hardware?msg=restored") in headers
        assert audio_hardware_store.load_profile() == "headphones"
        assert seen == {
            "action": "set_audio_hardware",
            "args": {"profile": "headphones"},
        }

    def test_post_restore_resets_hardware_audio_but_keeps_equalizer(self, monkeypatch, tmp_path):
        from radio_web import audio_store, equalizer_store

        sessions = auth.SessionStore()
        session = sessions.create()
        monkeypatch.setattr(
            "radio_web.actions.run_action",
            lambda action_id, **args: (True, "Sound card changed."),
        )
        monkeypatch.setattr("radio_web.config_store.MANAGED_CONFIG_DIR", str(tmp_path))
        # Seed a non-default max volume so restore has something to clear.
        audio_store.save_audio({"max_volume": "50"})
        equalizer_store.save_equalizer(
            equalizer_store.settings_as_form(equalizer_store.defaults())
        )
        assert os.path.exists(audio_store.managed_audio_path())
        assert os.path.exists(equalizer_store.managed_equalizer_path())
        form = {"op": "restore", "csrf_token": session.csrf_token}
        req = self._ctx(
            monkeypatch,
            tmp_path,
            "POST",
            sessions=sessions,
            session=session,
            form=form,
        )
        status, _c, _b, headers = routes.resolve(req)
        assert status == 303
        assert ("Location", "/audio-hardware?msg=restored") in headers
        # Hardware/volume defaults are independent from the EQ form.
        assert audio_hardware_store.load_profile() == "headphones"
        assert not os.path.exists(audio_store.managed_audio_path())
        assert os.path.exists(equalizer_store.managed_equalizer_path())

    def test_post_restore_helper_failure_keeps_existing_selection(self, monkeypatch, tmp_path):
        sessions = auth.SessionStore()
        session = sessions.create()
        monkeypatch.setattr("radio_web.config_store.MANAGED_CONFIG_DIR", str(tmp_path))
        audio_hardware_store.save_profile("merus_amp")
        monkeypatch.setattr(
            "radio_web.actions.run_action",
            lambda action_id, **args: (False, "Boot configuration write failed."),
        )
        req = self._ctx(
            monkeypatch,
            tmp_path,
            "POST",
            sessions=sessions,
            session=session,
            form={"op": "restore", "csrf_token": session.csrf_token},
        )

        status, _content_type, body, _headers = routes.resolve(req)

        assert status == 200
        assert "Boot configuration write failed" in body
        assert audio_hardware_store.load_profile() == "merus_amp"

    def test_post_save_volume_persists_and_redirects(self, monkeypatch, tmp_path):
        import radio_web.audio_store as audio_store

        sessions = auth.SessionStore()
        session = sessions.create()
        monkeypatch.setattr("radio_web.config_store.MANAGED_CONFIG_DIR", str(tmp_path))
        form = {"op": "save_volume", "csrf_token": session.csrf_token, "max_volume": "80"}
        req = self._ctx(
            monkeypatch,
            tmp_path,
            "POST",
            sessions=sessions,
            session=session,
            form=form,
        )
        status, _c, _b, headers = routes.resolve(req)
        assert status == 303
        assert ("Location", "/audio-hardware?msg=volume_saved") in headers
        assert audio_store.load_audio()["max_volume"] == "80"

    def test_post_apply_volume_restarts_radio(self, monkeypatch, tmp_path):
        import radio_web.audio_store as audio_store

        seen = {}

        def fake_run(action_id, **args):
            seen["action"] = action_id
            return True, "Radio restarted."

        monkeypatch.setattr("radio_web.actions.run_action", fake_run)
        sessions = auth.SessionStore()
        session = sessions.create()
        monkeypatch.setattr("radio_web.config_store.MANAGED_CONFIG_DIR", str(tmp_path))
        form = {"op": "apply_volume", "csrf_token": session.csrf_token, "max_volume": "70"}
        req = self._ctx(
            monkeypatch,
            tmp_path,
            "POST",
            sessions=sessions,
            session=session,
            form=form,
        )
        status, _c, _b, headers = routes.resolve(req)
        assert status == 303
        assert ("Location", "/audio-hardware?msg=volume_applied") in headers
        assert seen["action"] == "restart_radio"
        assert audio_store.load_audio()["max_volume"] == "70"

    def test_post_save_volume_invalid_shows_error(self, monkeypatch, tmp_path):
        sessions = auth.SessionStore()
        session = sessions.create()
        monkeypatch.setattr("radio_web.config_store.MANAGED_CONFIG_DIR", str(tmp_path))
        form = {"op": "save_volume", "csrf_token": session.csrf_token, "max_volume": "500"}
        req = self._ctx(
            monkeypatch,
            tmp_path,
            "POST",
            sessions=sessions,
            session=session,
            form=form,
        )
        status, _c, body, _h = routes.resolve(req)
        assert status == 200
        assert "Maximum volume" in body


class TestAdcDebugRoutes:
    def _request(self, monkeypatch, tmp_path, method, session=None, form=None):
        monkeypatch.setattr("radio_web.config_store.MANAGED_CONFIG_DIR", str(tmp_path))
        return routes.Request(
            method=method,
            path="/debug/adc",
            session=session,
            sessions=auth.SessionStore(),
            rate_limiter=auth.RateLimiter(),
            form=form or {},
        )

    def test_get_requires_auth(self, monkeypatch, tmp_path):
        response = routes.resolve(self._request(monkeypatch, tmp_path, "GET"))
        assert response[0] == 303
        assert ("Location", "/login") in response[3]

    def test_get_renders_live_channels(self, monkeypatch, tmp_path):
        session = auth.SessionStore().create()
        response = routes.resolve(self._request(monkeypatch, tmp_path, "GET", session))
        assert response[0] == 200
        assert "AIN0" in response[2] and "AIN3" in response[2]
        assert "/debug/adc/ws" in response[2]

    def test_post_saves_calibration(self, monkeypatch, tmp_path):
        session = auth.SessionStore().create()
        form = dict(adc_store.DEFAULTS)
        form.update({"op": "save", "csrf_token": session.csrf_token})
        response = routes.resolve(self._request(monkeypatch, tmp_path, "POST", session, form))
        assert response[0] == 303
        assert ("Location", "/debug/adc?msg=saved") in response[3]

    def test_post_rejects_bad_csrf(self, monkeypatch, tmp_path):
        session = auth.SessionStore().create()
        response = routes.resolve(
            self._request(monkeypatch, tmp_path, "POST", session, {"csrf_token": "bad"})
        )
        assert response[0] == 403


class TestNetworkRoutes:
    """Area C WiFi routes: auth + CSRF gating."""

    def _ctx(self, monkeypatch, tmp_path, method, path, **kw):
        monkeypatch.setattr("radio_web.config_store.MANAGED_CONFIG_DIR", str(tmp_path))
        monkeypatch.setattr(
            "radio_web.network_store.current_status",
            lambda: {
                "ssid": "Net",
                "mode": "dhcp",
                "static": {},
                "pending": None,
            },
        )
        kw.setdefault("sessions", auth.SessionStore())
        kw.setdefault("rate_limiter", auth.RateLimiter())
        return routes.Request(method=method, path=path, **kw)

    def test_network_get_requires_auth(self, monkeypatch, tmp_path):
        req = self._ctx(monkeypatch, tmp_path, "GET", "/network")
        status, _c, _b, headers = routes.resolve(req)
        assert status == 303
        assert ("Location", "/login") in headers

    def test_network_get_renders(self, monkeypatch, tmp_path):
        sessions = auth.SessionStore()
        session = sessions.create()
        req = self._ctx(
            monkeypatch,
            tmp_path,
            "GET",
            "/network",
            sessions=sessions,
            session=session,
        )
        status, _c, body, _h = routes.resolve(req)
        assert status == 200
        assert "WiFi" in body

    def test_network_apply_invalid_ssid_rerenders(self, monkeypatch, tmp_path):
        sessions = auth.SessionStore()
        session = sessions.create()
        req = self._ctx(
            monkeypatch,
            tmp_path,
            "POST",
            "/network",
            sessions=sessions,
            session=session,
            form={
                "op": "apply",
                "ssid": "",
                "psk": "password1",
                "ip_mode": "dhcp",
                "csrf_token": session.csrf_token,
            },
        )
        status, _c, body, _h = routes.resolve(req)
        assert status == 200
        assert "SSID" in body

    def test_network_apply_ok(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            "radio_web.actions.run_action",
            lambda action, **kw: (True, "Trying the new WiFi settings."),
        )
        sessions = auth.SessionStore()
        session = sessions.create()
        req = self._ctx(
            monkeypatch,
            tmp_path,
            "POST",
            "/network",
            sessions=sessions,
            session=session,
            form={
                "op": "apply",
                "ssid": "Net",
                "psk": "password1",
                "ip_mode": "dhcp",
                "csrf_token": session.csrf_token,
            },
        )
        status, _c, body, _h = routes.resolve(req)
        assert status == 200
        assert "Trying" in body

    def test_network_confirm_requires_csrf(self, monkeypatch, tmp_path):
        sessions = auth.SessionStore()
        session = sessions.create()
        req = self._ctx(
            monkeypatch,
            tmp_path,
            "POST",
            "/network",
            sessions=sessions,
            session=session,
            form={"op": "confirm", "csrf_token": "wrong"},
        )
        status, _c, _b, _h = routes.resolve(req)
        assert status == 403
