"""Unit tests for gsas_server process-management helpers.

Covers the cross-platform liveness probe (_pid_alive), is_server_running(),
and stop_server() PID-file handling. All os.kill / PID-file interactions are
mocked with monkeypatch + tmp_path so NO real server is ever started and no
signals reach real processes.

Import note: gsas_server.py does NOT import GSAS-II at module top (only stdlib
+ pydantic); GSAS-II is imported lazily inside GSASServer.__init__ / the
request handlers. A plain module import is therefore safe without GSAS-II.
"""
import os
import signal
import subprocess
import sys

import pytest

pytestmark = pytest.mark.core

pytest.importorskip("pydantic")

from powderline.gateways.gsasii import server as gsas_server


# The ProcessLookupError/PermissionError semantics below are specific to the
# POSIX os.kill(pid, 0) implementation of _pid_alive; on Windows _pid_alive uses
# a ctypes/OpenProcess path and never calls os.kill, so monkeypatching os.kill
# is a no-op there. Those cases are POSIX-only; cross-platform behaviour is
# covered by the real-PID contract tests (current process alive / exited process
# dead), which run the actual platform branch with no mocking.
posix_only = pytest.mark.skipif(
    os.name == "nt", reason="tests the POSIX os.kill branch of _pid_alive"
)


@pytest.fixture
def isolated_pid_files(monkeypatch, tmp_path):
    """Point PID_FILE / PORT_FILE at a throwaway tmp dir (nonexistent by default)."""
    pid_file = tmp_path / "server.pid"
    port_file = tmp_path / "server.port"
    monkeypatch.setattr(gsas_server, "PID_FILE", pid_file)
    monkeypatch.setattr(gsas_server, "PORT_FILE", port_file)
    return pid_file, port_file


# --- _pid_alive: cross-platform contract (real PIDs, no mocking) ---

def test_pid_alive_true_for_current_process():
    """The running test process is alive on any platform."""
    assert gsas_server._pid_alive(os.getpid()) is True


def test_pid_alive_false_for_exited_process():
    """A process that has exited is reported dead on any platform."""
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    assert gsas_server._pid_alive(proc.pid) is False


# --- _pid_alive: POSIX os.kill branch (skipped on Windows) ---

@posix_only
def test_pid_alive_true_when_kill_succeeds(monkeypatch):
    monkeypatch.setattr(gsas_server.os, "kill", lambda pid, sig: None)
    assert gsas_server._pid_alive(12345) is True


@posix_only
def test_pid_alive_false_when_process_lookup_error(monkeypatch):
    def _raise(pid, sig):
        raise ProcessLookupError()

    monkeypatch.setattr(gsas_server.os, "kill", _raise)
    assert gsas_server._pid_alive(12345) is False


@posix_only
def test_pid_alive_true_on_permission_error(monkeypatch):
    # Process exists but we lack permission to signal it -> still "alive".
    def _raise(pid, sig):
        raise PermissionError()

    monkeypatch.setattr(gsas_server.os, "kill", _raise)
    assert gsas_server._pid_alive(1) is True


# --- is_server_running ---

def test_is_server_running_false_without_pid_file(isolated_pid_files):
    pid_file, _ = isolated_pid_files
    assert not pid_file.exists()
    assert gsas_server.is_server_running() is False


def test_is_server_running_true_for_live_pid(isolated_pid_files, monkeypatch):
    pid_file, _ = isolated_pid_files
    pid_file.write_text("4242")
    monkeypatch.setattr(gsas_server, "_pid_alive", lambda pid: True)
    assert gsas_server.is_server_running() is True
    # Live PID file is left in place.
    assert pid_file.exists()


def test_is_server_running_stale_pid_removes_file(isolated_pid_files, monkeypatch):
    pid_file, _ = isolated_pid_files
    pid_file.write_text("4242")
    monkeypatch.setattr(gsas_server, "_pid_alive", lambda pid: False)
    assert gsas_server.is_server_running() is False
    # Stale PID file is cleaned up.
    assert not pid_file.exists()


def test_is_server_running_garbage_pid_removes_file(isolated_pid_files):
    pid_file, _ = isolated_pid_files
    pid_file.write_text("not-an-int")
    assert gsas_server.is_server_running() is False
    assert not pid_file.exists()


# --- stop_server ---

def test_stop_server_when_not_running(isolated_pid_files, capsys):
    # No PID file at all -> graceful no-op, returns False, no exception.
    result = gsas_server.stop_server()
    assert result is False
    assert "not running" in capsys.readouterr().out.lower()


def test_stop_server_stale_pid_graceful(isolated_pid_files, monkeypatch, capsys):
    # PID file present but process dead: is_server_running() detects the stale
    # PID, removes the file, and stop_server() reports "not running" -> False.
    pid_file, _ = isolated_pid_files
    pid_file.write_text("999999")
    monkeypatch.setattr(gsas_server, "_pid_alive", lambda pid: False)

    # Guard: os.kill must never be invoked on a dead PID here.
    def _boom(pid, sig):
        raise AssertionError("os.kill should not be called for a stale PID")

    monkeypatch.setattr(gsas_server.os, "kill", _boom)

    result = gsas_server.stop_server()
    assert result is False
    assert "not running" in capsys.readouterr().out.lower()
    assert not pid_file.exists()


def test_stop_server_sends_sigterm_to_live_pid(isolated_pid_files, monkeypatch, capsys):
    # Live PID: stop_server should signal it, then observe it gone on next poll.
    pid_file, _ = isolated_pid_files
    pid_file.write_text("4242")

    calls = {"signals": []}

    def fake_kill(pid, sig):
        calls["signals"].append((pid, sig))

    # First is_server_running() (guard) True; after SIGTERM, report dead.
    alive_states = iter([True, False])
    monkeypatch.setattr(gsas_server, "_pid_alive", lambda pid: next(alive_states))
    monkeypatch.setattr(gsas_server.os, "kill", fake_kill)
    monkeypatch.setattr(gsas_server.time, "sleep", lambda s: None)

    result = gsas_server.stop_server()
    assert result is True
    assert calls["signals"], "expected at least one signal to be sent"
    assert calls["signals"][0][0] == 4242
    assert calls["signals"][0][1] == signal.SIGTERM
    assert "stopped" in capsys.readouterr().out.lower()


# The escalation signal stop_server() uses when the graceful SIGTERM window
# expires: SIGKILL where it exists; on Windows (no SIGKILL) os.kill(pid,
# SIGTERM) maps to TerminateProcess, so SIGTERM *is* the hard kill there.
_FORCE_SIGNAL = getattr(signal, "SIGKILL", signal.SIGTERM)


def test_stop_server_force_kill_escalation(isolated_pid_files, monkeypatch, capsys):
    # PID ignores SIGTERM through the whole graceful 5s window and dies only
    # after the force kill: exactly SIGTERM then the force signal are sent.
    pid_file, _ = isolated_pid_files
    pid_file.write_text("4242")

    calls = {"signals": []}

    def fake_kill(pid, sig):
        calls["signals"].append((pid, sig))

    # Alive until the second (force) signal has been sent, then dead.
    monkeypatch.setattr(gsas_server, "_pid_alive", lambda pid: len(calls["signals"]) < 2)
    monkeypatch.setattr(gsas_server.os, "kill", fake_kill)
    monkeypatch.setattr(gsas_server.time, "sleep", lambda s: None)

    result = gsas_server.stop_server()
    assert result is True
    assert calls["signals"] == [(4242, signal.SIGTERM), (4242, _FORCE_SIGNAL)]
    assert "forced" in capsys.readouterr().out.lower()


def test_stop_server_force_kill_never_dies_returns_false(isolated_pid_files, monkeypatch, capsys):
    # PID survives even the force kill: stop_server() gives up and reports False
    # after sending exactly SIGTERM then the force signal.
    pid_file, _ = isolated_pid_files
    pid_file.write_text("4242")

    calls = {"signals": []}

    def fake_kill(pid, sig):
        calls["signals"].append((pid, sig))

    monkeypatch.setattr(gsas_server, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(gsas_server.os, "kill", fake_kill)
    monkeypatch.setattr(gsas_server.time, "sleep", lambda s: None)

    result = gsas_server.stop_server()
    assert result is False
    assert calls["signals"] == [(4242, signal.SIGTERM), (4242, _FORCE_SIGNAL)]
    assert "failed to stop" in capsys.readouterr().out.lower()


def test_response_model_keeps_native_result_keys():
    """A native gsasii.* result crosses the server with its standard statistics and warnings (re/04, A125)."""
    native = {"success": True, "rwp": 6.53, "r_exp": 12.4, "gof": 0.52, "chi2_red": 0.27,
              "engine_details": {"engine": "GSAS-II", "parameters_varied": 13},
              "warnings": [{"code": "c", "message": "m", "field_path": None}]}
    dumped = gsas_server.SimulationResponse(**native).model_dump()
    assert {k: dumped[k] for k in native} == native
