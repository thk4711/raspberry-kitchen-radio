"""Tests for lib/status_snapshot.py (the player's read-only status writer).

The web UI runs in a separate process and reads a small JSON snapshot the
player publishes atomically. These pure helpers are unit-tested in isolation,
without importing the full radio.py hardware/display stack.
"""

import json
import os
import stat

import status_snapshot


class TestResolveStatusFile:
    def test_env_value_wins(self, tmp_path):
        target = tmp_path / "s.json"
        assert status_snapshot.resolve_status_file({}, str(target)) == str(target)

    def test_disabled_via_config(self):
        assert status_snapshot.resolve_status_file({"status": {"enabled": False}}, None) == ""

    def test_empty_env_disables(self):
        assert status_snapshot.resolve_status_file({}, "") == ""

    def test_config_file_used_when_no_env(self, tmp_path):
        target = tmp_path / "from-config.json"
        cfg = {"status": {"file": str(target)}}
        assert status_snapshot.resolve_status_file(cfg, None) == str(target)

    def test_unwritable_path_disables(self):
        assert status_snapshot.resolve_status_file({}, "/no/such/dir/s.json") == ""


class TestBuildSnapshot:
    def _services(self):
        return [
            {"name": "mpd", "state": True},
            {"name": "airplay", "state": False},
        ]

    def test_fields_present_and_safe(self):
        now_playing = {"name": "DLF", "title": "News", "state": True}
        snap = status_snapshot.build_snapshot(True, "mpd", now_playing, self._services())
        assert snap["power"] is True
        assert snap["active_source"] == "mpd"
        assert snap["now_playing"] == now_playing
        assert snap["sources"] == {
            "mpd": {"playing": True},
            "airplay": {"playing": False},
        }
        assert "updated_at" in snap
        # No secrets/URLs anywhere in the serialized payload.
        assert "http" not in json.dumps(snap).lower()

    def test_empty_now_playing_allowed(self):
        snap = status_snapshot.build_snapshot(False, "mpd", {}, self._services())
        assert snap["now_playing"] == {}
        assert snap["power"] is False


class TestArtworkDescriptor:
    def test_station_logo_uses_basename_only(self):
        art = status_snapshot.artwork_descriptor("mpd", "Deutschlandfunk.png", "logo-hash")
        assert art["id"] == "station:Deutschlandfunk.png"
        assert len(art["version"]) == 16
        assert "/opt/" not in json.dumps(art)

    def test_known_runtime_artwork(self):
        assert (
            status_snapshot.artwork_descriptor("spotify", "/tmp/spotify_cover.jpg", "cover-v1")[
                "id"
            ]
            == "spotify"
        )
        assert (
            status_snapshot.artwork_descriptor("airplay", "/tmp/shairport-image.png", "cover-v2")[
                "id"
            ]
            == "airplay-png"
        )

    def test_unknown_or_traversal_artwork_is_omitted(self):
        assert status_snapshot.artwork_descriptor("mpd", "../../secret.png") == {}
        assert status_snapshot.artwork_descriptor("spotify", "/tmp/other.jpg") == {}


class TestWriteSnapshot:
    def test_atomic_write_creates_valid_json(self, tmp_path):
        target = tmp_path / "s.json"
        snap = {"active_source": "mpd", "updated_at": 1.0}
        status_snapshot.write_snapshot(str(target), snap)
        data = json.loads(target.read_text())
        assert data["active_source"] == "mpd"
        assert stat.S_IMODE(target.stat().st_mode) == status_snapshot.STATUS_FILE_MODE
        # No leftover temp files in the directory.
        leftovers = [p for p in os.listdir(tmp_path) if p.endswith(".tmp")]
        assert leftovers == []

    def test_disabled_is_noop(self, tmp_path):
        status_snapshot.write_snapshot("", {"x": 1})
        assert os.listdir(tmp_path) == []

    def test_write_failure_is_silent(self, tmp_path):
        # Directory does not exist -> OSError is swallowed, no raise.
        status_snapshot.write_snapshot(str(tmp_path / "missing_dir" / "s.json"), {"x": 1})

    def test_unserializable_is_silent(self, tmp_path):
        target = tmp_path / "s.json"
        # A set is not JSON-serializable; must be swallowed, no file written.
        status_snapshot.write_snapshot(str(target), {"bad": {1, 2, 3}})
        assert not target.exists()
