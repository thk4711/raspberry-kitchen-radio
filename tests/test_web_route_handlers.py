"""Route-handler tests for the four thin-coverage web route modules.

These drive radio_web.routes.resolve end to end for the stations/sources,
ADC/network and device/maintenance handlers, mirroring the existing
TestStationRoutes pattern in tests/test_web_routes.py: a real in-memory
SessionStore/Session and a valid per-session CSRF token. The privileged helper
socket is never contacted -- actions.run_action and actions.query_firmware_status
are monkeypatched, so the tests exercise the route branching
(apply/restore/unknown-op, error rendering, PRG redirects) without a Pi.
"""

import pytest

from radio_web import actions, adc_store, auth, device_store, routes, sources_store


def _ctx(monkeypatch, tmp_path, method, path, *, authed=True, **kw):
    """Build a Request for routes.resolve with an isolated managed-config dir.

    When authed=True a fresh session is created and, for POST forms, a matching
    csrf_token is injected so the handler passes the auth+CSRF gate.
    """
    monkeypatch.setattr("radio_web.config_store.MANAGED_CONFIG_DIR", str(tmp_path))
    sessions = kw.pop("sessions", auth.SessionStore())
    kw.setdefault("rate_limiter", auth.RateLimiter())
    session = None
    if authed:
        session = sessions.create()
        form = kw.get("form")
        if form is not None and "csrf_token" not in form:
            form["csrf_token"] = session.csrf_token
    return routes.Request(method=method, path=path, sessions=sessions, session=session, **kw)


def _stub_action(monkeypatch, result):
    """Replace the privileged-helper call with a fixed (ok, detail) result."""
    monkeypatch.setattr(actions, "run_action", lambda *a, **k: result)


class TestStationRouteBranches:
    def test_unknown_op_rerenders_with_error(self, monkeypatch, tmp_path):
        req = _ctx(monkeypatch, tmp_path, "POST", "/stations", form={"op": "bogus", "index": "0"})
        status, _c, body, _h = routes.resolve(req)
        assert status == 200
        assert "Unknown action." in body

    def test_restore_redirects(self, monkeypatch, tmp_path):
        req = _ctx(monkeypatch, tmp_path, "POST", "/stations", form={"op": "restore"})
        status, _c, _b, headers = routes.resolve(req)
        assert status == 303
        assert ("Location", "/stations?msg=restored") in headers

    def test_apply_success_redirects(self, monkeypatch, tmp_path):
        _stub_action(monkeypatch, (True, "ok"))
        req = _ctx(monkeypatch, tmp_path, "POST", "/stations", form={"op": "apply"})
        status, _c, _b, headers = routes.resolve(req)
        assert status == 303
        assert ("Location", "/stations?msg=applied") in headers

    def test_apply_failure_rerenders_error(self, monkeypatch, tmp_path):
        _stub_action(monkeypatch, (False, "helper down"))
        req = _ctx(monkeypatch, tmp_path, "POST", "/stations", form={"op": "apply"})
        status, _c, body, _h = routes.resolve(req)
        assert status == 200
        assert "helper down" in body

    def test_upload_logo_without_file_errors(self, monkeypatch, tmp_path):
        req = _ctx(monkeypatch, tmp_path, "POST", "/stations", form={"op": "upload_logo"})
        status, _c, body, _h = routes.resolve(req)
        assert status == 200
        assert "Choose an image file." in body

    def test_get_flash_message_rendered(self, monkeypatch, tmp_path):
        req = _ctx(monkeypatch, tmp_path, "GET", "/stations", query={"msg": "saved"})
        status, _c, body, _h = routes.resolve(req)
        assert status == 200
        assert "Preset saved." in body


class TestSourceRouteBranches:
    def test_get_requires_auth(self, monkeypatch, tmp_path):
        req = _ctx(monkeypatch, tmp_path, "GET", "/sources", authed=False)
        status, _c, _b, headers = routes.resolve(req)
        assert status == 303
        assert ("Location", "/login") in headers

    def test_get_renders_when_authenticated(self, monkeypatch, tmp_path):
        req = _ctx(monkeypatch, tmp_path, "GET", "/sources", query={"msg": "saved"})
        status, _c, body, _h = routes.resolve(req)
        assert status == 200
        assert "Sources saved." in body

    def test_post_requires_csrf(self, monkeypatch, tmp_path):
        req = _ctx(
            monkeypatch, tmp_path, "POST", "/sources", form={"op": "save", "csrf_token": "wrong"}
        )
        status, _c, _b, _h = routes.resolve(req)
        assert status == 403

    def test_restore_redirects(self, monkeypatch, tmp_path):
        req = _ctx(monkeypatch, tmp_path, "POST", "/sources", form={"op": "restore"})
        status, _c, _b, headers = routes.resolve(req)
        assert status == 303
        assert ("Location", "/sources?msg=restored") in headers

    def test_save_persists_and_redirects(self, monkeypatch, tmp_path):
        form = {key: "true" for key, _ in sources_store.SOURCE_KEYS}
        form["op"] = "save"
        req = _ctx(monkeypatch, tmp_path, "POST", "/sources", form=form)
        status, _c, _b, headers = routes.resolve(req)
        assert status == 303
        assert ("Location", "/sources?msg=saved") in headers

    def test_apply_success_redirects(self, monkeypatch, tmp_path):
        _stub_action(monkeypatch, (True, "ok"))
        form = {key: "true" for key, _ in sources_store.SOURCE_KEYS}
        form["op"] = "apply"
        req = _ctx(monkeypatch, tmp_path, "POST", "/sources", form=form)
        status, _c, _b, headers = routes.resolve(req)
        assert status == 303
        assert ("Location", "/sources?msg=applied") in headers

    def test_unknown_op_errors(self, monkeypatch, tmp_path):
        req = _ctx(monkeypatch, tmp_path, "POST", "/sources", form={"op": "bogus"})
        status, _c, body, _h = routes.resolve(req)
        assert status == 200
        assert "Unknown action." in body


class TestAdcRouteBranches:
    def _valid_form(self, op):
        form = dict(adc_store.DEFAULTS)
        form["op"] = op
        return form

    def test_get_requires_auth(self, monkeypatch, tmp_path):
        req = _ctx(monkeypatch, tmp_path, "GET", "/debug/adc", authed=False)
        status, _c, _b, headers = routes.resolve(req)
        assert status == 303
        assert ("Location", "/login") in headers

    def test_get_renders_flash(self, monkeypatch, tmp_path):
        req = _ctx(monkeypatch, tmp_path, "GET", "/debug/adc", query={"msg": "saved"})
        status, _c, body, _h = routes.resolve(req)
        assert status == 200
        assert "ADC calibration saved" in body

    def test_post_requires_csrf(self, monkeypatch, tmp_path):
        req = _ctx(
            monkeypatch, tmp_path, "POST", "/debug/adc", form={"op": "save", "csrf_token": "wrong"}
        )
        status, _c, _b, _h = routes.resolve(req)
        assert status == 403

    def test_restore_redirects(self, monkeypatch, tmp_path):
        req = _ctx(monkeypatch, tmp_path, "POST", "/debug/adc", form={"op": "restore"})
        status, _c, _b, headers = routes.resolve(req)
        assert status == 303
        assert ("Location", "/debug/adc?msg=restored") in headers

    def test_save_redirects(self, monkeypatch, tmp_path):
        req = _ctx(monkeypatch, tmp_path, "POST", "/debug/adc", form=self._valid_form("save"))
        status, _c, _b, headers = routes.resolve(req)
        assert status == 303
        assert ("Location", "/debug/adc?msg=saved") in headers

    def test_apply_success_redirects(self, monkeypatch, tmp_path):
        _stub_action(monkeypatch, (True, "ok"))
        req = _ctx(monkeypatch, tmp_path, "POST", "/debug/adc", form=self._valid_form("apply"))
        status, _c, _b, headers = routes.resolve(req)
        assert status == 303
        assert ("Location", "/debug/adc?msg=applied") in headers

    def test_apply_failure_rerenders_error(self, monkeypatch, tmp_path):
        _stub_action(monkeypatch, (False, "no helper"))
        req = _ctx(monkeypatch, tmp_path, "POST", "/debug/adc", form=self._valid_form("apply"))
        status, _c, body, _h = routes.resolve(req)
        assert status == 200
        assert "no helper" in body

    def test_invalid_values_rerender_error(self, monkeypatch, tmp_path):
        form = self._valid_form("save")
        form["volume_min_input"] = "2000"
        form["volume_max_input"] = "1000"
        req = _ctx(monkeypatch, tmp_path, "POST", "/debug/adc", form=form)
        status, _c, _b, _h = routes.resolve(req)
        assert status == 200

    def test_unknown_op_errors(self, monkeypatch, tmp_path):
        req = _ctx(monkeypatch, tmp_path, "POST", "/debug/adc", form=self._valid_form("bogus"))
        status, _c, body, _h = routes.resolve(req)
        assert status == 200
        assert "Unknown action." in body


class TestNetworkRouteBranches:
    def test_get_requires_auth(self, monkeypatch, tmp_path):
        req = _ctx(monkeypatch, tmp_path, "GET", "/network", authed=False)
        status, _c, _b, headers = routes.resolve(req)
        assert status == 303
        assert ("Location", "/login") in headers

    def test_get_renders_flash(self, monkeypatch, tmp_path):
        req = _ctx(monkeypatch, tmp_path, "GET", "/network", query={"msg": "confirmed"})
        status, _c, body, _h = routes.resolve(req)
        assert status == 200
        assert "WiFi settings confirmed." in body

    def test_post_requires_csrf(self, monkeypatch, tmp_path):
        req = _ctx(
            monkeypatch, tmp_path, "POST", "/network", form={"op": "confirm", "csrf_token": "wrong"}
        )
        status, _c, _b, _h = routes.resolve(req)
        assert status == 403

    def test_confirm_success_redirects(self, monkeypatch, tmp_path):
        _stub_action(monkeypatch, (True, "ok"))
        req = _ctx(monkeypatch, tmp_path, "POST", "/network", form={"op": "confirm"})
        status, _c, _b, headers = routes.resolve(req)
        assert status == 303
        assert ("Location", "/network?msg=confirmed") in headers

    def test_confirm_failure_rerenders_error(self, monkeypatch, tmp_path):
        _stub_action(monkeypatch, (False, "cannot confirm"))
        req = _ctx(monkeypatch, tmp_path, "POST", "/network", form={"op": "confirm"})
        status, _c, body, _h = routes.resolve(req)
        assert status == 200
        assert "cannot confirm" in body

    def test_rollback_success_redirects(self, monkeypatch, tmp_path):
        _stub_action(monkeypatch, (True, "ok"))
        req = _ctx(monkeypatch, tmp_path, "POST", "/network", form={"op": "rollback"})
        status, _c, _b, headers = routes.resolve(req)
        assert status == 303
        assert ("Location", "/network?msg=reverted") in headers

    def test_apply_valid_form_calls_helper(self, monkeypatch, tmp_path):
        _stub_action(monkeypatch, (True, "WiFi updated."))
        form = {"op": "apply", "ssid": "Net", "psk": "password1", "ip_mode": "dhcp"}
        req = _ctx(monkeypatch, tmp_path, "POST", "/network", form=form)
        status, _c, body, _h = routes.resolve(req)
        assert status == 200
        assert "WiFi updated." in body

    def test_apply_invalid_form_rerenders_error(self, monkeypatch, tmp_path):
        form = {"op": "apply", "ssid": "", "psk": "password1", "ip_mode": "dhcp"}
        req = _ctx(monkeypatch, tmp_path, "POST", "/network", form=form)
        status, _c, _b, _h = routes.resolve(req)
        assert status == 200

    def test_unknown_op_errors(self, monkeypatch, tmp_path):
        req = _ctx(monkeypatch, tmp_path, "POST", "/network", form={"op": "bogus"})
        status, _c, body, _h = routes.resolve(req)
        assert status == 200
        assert "Unknown action." in body


class TestDeviceRouteBranches:
    def test_get_requires_auth(self, monkeypatch, tmp_path):
        req = _ctx(monkeypatch, tmp_path, "GET", "/device", authed=False)
        status, _c, _b, headers = routes.resolve(req)
        assert status == 303
        assert ("Location", "/login") in headers

    def test_get_renders_flash(self, monkeypatch, tmp_path):
        req = _ctx(monkeypatch, tmp_path, "GET", "/device", query={"msg": "saved"})
        status, _c, body, _h = routes.resolve(req)
        assert status == 200
        assert "Device settings saved." in body

    def test_post_requires_csrf(self, monkeypatch, tmp_path):
        req = _ctx(
            monkeypatch, tmp_path, "POST", "/device", form={"op": "save", "csrf_token": "wrong"}
        )
        status, _c, _b, _h = routes.resolve(req)
        assert status == 403

    def test_unknown_op_errors(self, monkeypatch, tmp_path):
        req = _ctx(monkeypatch, tmp_path, "POST", "/device", form={"op": "bogus"})
        status, _c, body, _h = routes.resolve(req)
        assert status == 200
        assert "Unknown action." in body

    def test_restore_success_redirects(self, monkeypatch, tmp_path):
        _stub_action(monkeypatch, (True, "ok"))
        req = _ctx(monkeypatch, tmp_path, "POST", "/device", form={"op": "restore"})
        status, _c, _b, headers = routes.resolve(req)
        assert status == 303
        assert ("Location", "/device?msg=restored") in headers

    def test_restore_helper_failure_rerenders(self, monkeypatch, tmp_path):
        _stub_action(monkeypatch, (False, "ssh restart failed"))
        req = _ctx(monkeypatch, tmp_path, "POST", "/device", form={"op": "restore"})
        status, _c, body, _h = routes.resolve(req)
        assert status == 200
        assert "ssh restart failed" in body

    def test_save_name_success_redirects(self, monkeypatch, tmp_path):
        monkeypatch.setattr(device_store, "sd_card_hostname_locked", lambda: False)
        _stub_action(monkeypatch, (True, "ok"))
        form = {
            "op": "save",
            "name": "kitchen",
            "timezone": "",
            "ntp_server": "",
            "ssh_enabled": "false",
        }
        req = _ctx(monkeypatch, tmp_path, "POST", "/device", form=form)
        status, _c, _b, headers = routes.resolve(req)
        assert status == 303
        assert ("Location", "/device?msg=saved") in headers

    def test_save_invalid_name_rerenders_error(self, monkeypatch, tmp_path):
        monkeypatch.setattr(device_store, "sd_card_hostname_locked", lambda: False)
        form = {
            "op": "save",
            "name": "bad name!",
            "timezone": "",
            "ntp_server": "",
            "ssh_enabled": "false",
        }
        req = _ctx(monkeypatch, tmp_path, "POST", "/device", form=form)
        status, _c, _b, _h = routes.resolve(req)
        assert status == 200

    def test_save_helper_error_is_reported(self, monkeypatch, tmp_path):
        monkeypatch.setattr(device_store, "sd_card_hostname_locked", lambda: False)
        _stub_action(monkeypatch, (False, "hostname failed"))
        form = {
            "op": "save",
            "name": "kitchen",
            "timezone": "",
            "ntp_server": "",
            "ssh_enabled": "false",
        }
        req = _ctx(monkeypatch, tmp_path, "POST", "/device", form=form)
        status, _c, body, _h = routes.resolve(req)
        assert status == 200
        assert "hostname failed" in body

    def test_save_ssh_requires_provisioned_root(self, monkeypatch, tmp_path):
        monkeypatch.setattr(device_store, "sd_card_hostname_locked", lambda: False)
        monkeypatch.setattr(device_store, "root_password_is_provisioned", lambda: False)
        form = {"op": "save", "name": "", "timezone": "", "ntp_server": "", "ssh_enabled": "true"}
        req = _ctx(monkeypatch, tmp_path, "POST", "/device", form=form)
        status, _c, body, _h = routes.resolve(req)
        assert status == 200
        assert "root_password" in body


def _stub_firmware_status(monkeypatch, ok=True, status=None):
    payload = status if status is not None else {"switch_allowed": False}
    monkeypatch.setattr(actions, "query_firmware_status", lambda: (ok, "detail", payload))


class TestMaintenanceRouteBranches:
    def test_get_requires_auth(self, monkeypatch, tmp_path):
        req = _ctx(monkeypatch, tmp_path, "GET", "/maintenance", authed=False)
        status, _c, _b, headers = routes.resolve(req)
        assert status == 303
        assert ("Location", "/login") in headers

    def test_get_renders_flash(self, monkeypatch, tmp_path):
        _stub_firmware_status(monkeypatch)
        req = _ctx(monkeypatch, tmp_path, "GET", "/maintenance", query={"msg": "restart_radio"})
        status, _c, body, _h = routes.resolve(req)
        assert status == 200
        assert "Radio restarted." in body

    def test_firmware_get_redirects_to_maintenance(self, monkeypatch, tmp_path):
        req = _ctx(monkeypatch, tmp_path, "GET", "/firmware")
        status, _c, _b, headers = routes.resolve(req)
        assert status == 303
        assert ("Location", "/maintenance") in headers

    def test_firmware_status_requires_auth(self, monkeypatch, tmp_path):
        req = _ctx(monkeypatch, tmp_path, "GET", "/firmware/status", authed=False)
        status, ctype, _b, _h = routes.resolve(req)
        assert status == 401
        assert "json" in ctype

    def test_firmware_status_json_when_authed(self, monkeypatch, tmp_path):
        _stub_firmware_status(monkeypatch)
        req = _ctx(monkeypatch, tmp_path, "GET", "/firmware/status")
        status, ctype, body, headers = routes.resolve(req)
        assert status == 200
        assert "json" in ctype
        assert ("Cache-Control", "no-store") in headers
        assert '"ok"' in body

    def test_post_requires_csrf(self, monkeypatch, tmp_path):
        req = _ctx(
            monkeypatch,
            tmp_path,
            "POST",
            "/maintenance",
            form={"action": "restart_radio", "csrf_token": "wrong"},
        )
        status, _c, _b, _h = routes.resolve(req)
        assert status == 403

    def test_restart_radio_success_redirects(self, monkeypatch, tmp_path):
        _stub_action(monkeypatch, (True, "ok"))
        req = _ctx(monkeypatch, tmp_path, "POST", "/maintenance", form={"action": "restart_radio"})
        status, _c, _b, headers = routes.resolve(req)
        assert status == 303
        assert ("Location", "/maintenance?msg=restart_radio") in headers

    def test_restart_mpd_failure_rerenders(self, monkeypatch, tmp_path):
        _stub_action(monkeypatch, (False, "mpd down"))
        req = _ctx(monkeypatch, tmp_path, "POST", "/maintenance", form={"action": "restart_mpd"})
        status, _c, body, _h = routes.resolve(req)
        assert status == 200
        assert "mpd down" in body

    def test_unknown_action_errors(self, monkeypatch, tmp_path):
        req = _ctx(monkeypatch, tmp_path, "POST", "/maintenance", form={"action": "bogus"})
        status, _c, body, _h = routes.resolve(req)
        assert status == 200
        assert "Unknown action." in body

    def test_reboot_without_confirm_shows_confirm_page(self, monkeypatch, tmp_path):
        req = _ctx(monkeypatch, tmp_path, "POST", "/maintenance", form={"action": "reboot"})
        status, _c, body, _h = routes.resolve(req)
        assert status == 200
        assert "Reboot device" in body

    def test_reboot_confirmed_wrong_password_401(self, monkeypatch, tmp_path):
        req = _ctx(
            monkeypatch,
            tmp_path,
            "POST",
            "/maintenance",
            form={"action": "reboot", "confirmed": "1", "password": "nope"},
        )
        auth.set_password("correcthorse")
        status, _c, _b, _h = routes.resolve(req)
        assert status == 401

    def test_reboot_confirmed_correct_password_runs(self, monkeypatch, tmp_path):
        sessions = auth.SessionStore()
        req = _ctx(
            monkeypatch,
            tmp_path,
            "POST",
            "/maintenance",
            sessions=sessions,
            form={"action": "reboot", "confirmed": "1", "password": "correcthorse"},
        )
        auth.set_password("correcthorse")
        _stub_action(monkeypatch, (True, "Rebooting."))
        status, _c, body, _h = routes.resolve(req)
        assert status == 200
        assert "Rebooting." in body

    def test_confirm_post_unknown_action_redirects(self, monkeypatch, tmp_path):
        req = _ctx(monkeypatch, tmp_path, "POST", "/maintenance/confirm", form={"action": "bogus"})
        status, _c, _b, headers = routes.resolve(req)
        assert status == 303
        assert ("Location", "/maintenance") in headers

    def test_confirm_post_shows_confirm_page(self, monkeypatch, tmp_path):
        req = _ctx(
            monkeypatch, tmp_path, "POST", "/maintenance/confirm", form={"action": "shutdown"}
        )
        status, _c, body, _h = routes.resolve(req)
        assert status == 200
        assert "Shut down device" in body

    def test_diagnostics_requires_auth(self, monkeypatch, tmp_path):
        req = _ctx(monkeypatch, tmp_path, "GET", "/diagnostics", authed=False)
        status, _c, _b, headers = routes.resolve(req)
        assert status == 303
        assert ("Location", "/login") in headers


class TestStationEditHelpers:
    def test_save_valid_slot_redirects(self, monkeypatch, tmp_path):
        form = {
            "op": "save",
            "index": "0",
            "name": "MyStation",
            "url": "https://my.example/s",
            "logo": "",
        }
        req = _ctx(monkeypatch, tmp_path, "POST", "/stations", form=form)
        status, _c, _b, headers = routes.resolve(req)
        assert status == 303
        assert ("Location", "/stations?msg=saved") in headers

    def test_save_invalid_url_rerenders_error(self, monkeypatch, tmp_path):
        form = {"op": "save", "index": "0", "name": "Bad", "url": "ftp://nope/x", "logo": ""}
        req = _ctx(monkeypatch, tmp_path, "POST", "/stations", form=form)
        status, _c, _b, _h = routes.resolve(req)
        assert status == 200

    def test_move_up_at_top_reports_cannot_move(self, monkeypatch, tmp_path):
        form = {
            "op": "move_up",
            "index": "0",
            "name": "X",
            "url": "https://x.example/s",
            "logo": "",
        }
        req = _ctx(monkeypatch, tmp_path, "POST", "/stations", form=form)
        status, _c, body, _h = routes.resolve(req)
        assert status == 200
        assert "Cannot move preset." in body

    def test_move_down_reorders_and_redirects(self, monkeypatch, tmp_path):
        form = {
            "op": "move_down",
            "index": "0",
            "name": "X",
            "url": "https://x.example/s",
            "logo": "",
        }
        req = _ctx(monkeypatch, tmp_path, "POST", "/stations", form=form)
        status, _c, _b, headers = routes.resolve(req)
        assert status == 303
        assert ("Location", "/stations?msg=moved") in headers

    def test_move_non_integer_index_reports_invalid(self, monkeypatch, tmp_path):
        form = {
            "op": "move_up",
            "index": "x",
            "name": "X",
            "url": "https://x.example/s",
            "logo": "",
        }
        req = _ctx(monkeypatch, tmp_path, "POST", "/stations", form=form)
        status, _c, body, _h = routes.resolve(req)
        assert status == 200
        assert "Invalid preset." in body

    def test_test_op_reports_stream_result(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            "radio_web.stations_store.test_stream", lambda url: (True, "Stream reachable.")
        )
        form = {"op": "test", "index": "0", "name": "X", "url": "https://x.example/s", "logo": ""}
        req = _ctx(monkeypatch, tmp_path, "POST", "/stations", form=form)
        status, _c, body, _h = routes.resolve(req)
        assert status == 200
        assert "Stream reachable." in body

    def test_test_op_failure_shows_error(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            "radio_web.stations_store.test_stream", lambda url: (False, "Unreachable.")
        )
        form = {"op": "test", "index": "x", "url": "https://x.example/s"}
        req = _ctx(monkeypatch, tmp_path, "POST", "/stations", form=form)
        status, _c, body, _h = routes.resolve(req)
        assert status == 200
        assert "Unreachable." in body
