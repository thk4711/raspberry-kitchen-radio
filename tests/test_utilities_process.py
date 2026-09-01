"""Tests for bounded, stoppable backend process supervision (item 5)."""
import subprocess
from unittest import mock

import pytest
from utilities import UtilityLibrary


@pytest.fixture
def util():
    """A UtilityLibrary with a clean process registry and stop event."""
    u = UtilityLibrary()
    u._processes.clear()
    u._supervisors.clear()
    u._stop.clear()
    yield u
    u._stop.set()


def test_crash_loop_tracks_single_process_and_backs_off(util, monkeypatch):
    """Repeated crashes keep exactly one tracked record (no unbounded growth)."""
    procs = []

    def _popen(cmd_array, **kwargs):
        p = mock.Mock()
        p.pid = 100 + len(procs)
        p.poll.return_value = 1  # already exited -> crash
        procs.append(p)
        return p

    monkeypatch.setattr(subprocess, "Popen", _popen)

    backoffs = []
    original = util._restart_backoff

    def _tracked(delay):
        backoffs.append(delay)
        util._stop.set()  # stop after the first crash+backoff
        return original(delay)

    monkeypatch.setattr(util, "_restart_backoff", _tracked)

    util._start_and_monitor_binary(["cmd", "arg"], "cmd")

    assert len(procs) == 1
    assert list(util._processes.keys()) == ["cmd"]
    assert backoffs and backoffs[0] == 2.0


def test_failed_spawn_does_not_track_and_stops(util, monkeypatch):
    """An OSError on spawn is handled and leaves no tracked process."""
    monkeypatch.setattr(
        subprocess, "Popen", mock.Mock(side_effect=OSError("no such binary"))
    )
    monkeypatch.setattr(util, "_restart_backoff", lambda d: util._stop.set())

    util._start_and_monitor_binary(["missing"], "missing")

    assert util._processes == {}


def test_stop_event_prevents_relaunch(util, monkeypatch):
    """With the stop event set, the monitor loop never spawns a child."""
    util._stop.set()
    popen = mock.Mock()
    monkeypatch.setattr(subprocess, "Popen", popen)

    util._start_and_monitor_binary(["cmd"], "cmd")

    popen.assert_not_called()


def test_cleanup_terminates_waits_and_kills(util):
    """cleanup terminates, waits, and force-kills a stubborn process."""
    good = mock.Mock()
    good.pid = 1
    good.poll.return_value = None

    stubborn = mock.Mock()
    stubborn.pid = 2
    stubborn.poll.return_value = None
    stubborn.wait.side_effect = [subprocess.TimeoutExpired("x", 5), 0]

    util._processes = {"good": good, "stubborn": stubborn}

    util.cleanup(timeout=0.01)

    assert util._stop.is_set()
    good.terminate.assert_called_once()
    stubborn.terminate.assert_called_once()
    stubborn.kill.assert_called_once()
    assert util._processes == {}


def test_normal_exit_record_replaced_not_appended(util, monkeypatch):
    """A clean exit followed by a restart keeps one record for the backend."""
    count = {"n": 0}

    def _popen(cmd_array, **kwargs):
        count["n"] += 1
        p = mock.Mock()
        p.pid = count["n"]
        p.poll.return_value = 0  # exited normally
        return p

    monkeypatch.setattr(subprocess, "Popen", _popen)

    def _stop_after_second(delay):
        if count["n"] >= 2:
            util._stop.set()

    monkeypatch.setattr(util, "_restart_backoff", _stop_after_second)

    util._start_and_monitor_binary(["cmd"], "cmd")

    assert count["n"] >= 2
    assert list(util._processes.keys()) == ["cmd"]


def test_empty_command_is_rejected(util):
    """An empty command string starts no supervisor thread."""
    with mock.patch("threading.Thread") as thread:
        util.start_external_program_in_background("   ")
        thread.assert_not_called()
