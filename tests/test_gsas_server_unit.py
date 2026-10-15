"""Unit tests for gsas_server process-management helpers.

Covers the cross-platform liveness probe (_pid_alive), is_server_running()
and stop_server(), plus the multi-user safety layer: the per-user state
directory checks, the kernel-held start lock, request/response signing on
every route, and the POWDERLINE_NO_SERVER switch. All os.kill interactions are
mocked and state files live under tmp_path, so NO real server is ever started
and no signals reach real processes.

Import note: gsas_server.py does NOT import GSAS-II at module top (only stdlib
+ pydantic); GSAS-II is imported lazily inside GSASServer.__init__ / the
request handlers. A plain module import is therefore safe without GSAS-II.
"""
import json
import os
import secrets
import signal
import subprocess
import sys
import time

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
    """Point the state dir at a throwaway tmp dir (state files absent by default)."""
    monkeypatch.setattr(gsas_server, "state_dir", lambda: tmp_path)
    yield tmp_path / "server.pid", tmp_path / "server.json"
    gsas_server._release_state_files()  # drop a lock a test may have claimed


@pytest.fixture
def other_holder(isolated_pid_files):
    """Hold the start lock through a separate descriptor, like another server would."""
    fd = gsas_server._open_state_file(gsas_server.LOCK_NAME, os.O_RDWR | os.O_CREAT)
    assert gsas_server._try_lock(fd)
    yield
    gsas_server._unlock(fd)
    os.close(fd)


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


# --- is_server_running: holding the start lock is the definition ---

def test_is_server_running_false_without_any_state(isolated_pid_files):
    assert gsas_server.is_server_running() is False


def test_is_server_running_true_while_lock_held(other_holder):
    assert gsas_server.is_server_running() is True


def test_is_server_running_ignores_stale_or_recycled_pid(isolated_pid_files):
    # A leftover PID file naming a live (recycled) PID does not make a dead
    # server look alive: nobody holds the lock.
    pid_file, _ = isolated_pid_files
    pid_file.write_text(str(os.getpid()))
    assert gsas_server.is_server_running() is False


def test_is_server_running_probe_does_not_keep_the_lock(isolated_pid_files):
    assert gsas_server.is_server_running() is False
    assert gsas_server._claim_server_lock(attempts=1) is True


# --- stop_server ---

def test_stop_server_when_not_running(isolated_pid_files, capsys):
    # No server at all -> graceful no-op, returns False, no exception.
    result = gsas_server.stop_server()
    assert result is False
    assert "not running" in capsys.readouterr().out.lower()


def test_stop_server_never_signals_a_stale_pid(isolated_pid_files, monkeypatch, capsys):
    # PID file left by a crashed server (its PID possibly recycled by another
    # process): no lock holder -> "not running", and nothing is signalled.
    pid_file, _ = isolated_pid_files
    pid_file.write_text(str(os.getpid()))

    def _boom(pid, sig):
        raise AssertionError("os.kill must not be called without a lock holder")

    monkeypatch.setattr(gsas_server.os, "kill", _boom)

    result = gsas_server.stop_server()
    assert result is False
    assert "not running" in capsys.readouterr().out.lower()


def test_stop_server_while_starting_up(other_holder, monkeypatch, capsys):
    # Lock held but no PID recorded yet: nothing to signal, report and return.
    monkeypatch.setattr(gsas_server.os, "kill",
                        lambda pid, sig: pytest.fail("nothing to signal yet"))
    assert gsas_server.stop_server() is False
    assert "starting up" in capsys.readouterr().out.lower()


def test_stop_server_sends_sigterm_to_live_pid(isolated_pid_files, monkeypatch, capsys):
    # Running server: stop_server should signal it, then observe it gone on next poll.
    pid_file, _ = isolated_pid_files
    pid_file.write_text("4242")

    calls = {"signals": []}

    def fake_kill(pid, sig):
        calls["signals"].append((pid, sig))

    # First is_server_running() (guard) True; after SIGTERM, report stopped.
    running_states = iter([True, False])
    monkeypatch.setattr(gsas_server, "_lock_held", lambda: next(running_states))
    monkeypatch.setattr(gsas_server.os, "kill", fake_kill)
    monkeypatch.setattr(gsas_server.time, "sleep", lambda s: None)

    result = gsas_server.stop_server()
    assert result is True
    assert calls["signals"], "expected at least one signal to be sent"
    assert calls["signals"][0][0] == 4242
    assert calls["signals"][0][1] == signal.SIGTERM
    assert "stopped" in capsys.readouterr().out.lower()
    # A terminated server (e.g. TerminateProcess on Windows) cannot clean up;
    # stop does it, under the lock.
    assert not pid_file.exists()


def test_clear_stale_state_files_never_touches_a_live_server(isolated_pid_files, other_holder):
    pid_file, endpoint_file = isolated_pid_files
    pid_file.write_text("4242")
    endpoint_file.write_text("{}")
    assert gsas_server._clear_stale_state_files() is False
    assert pid_file.exists() and endpoint_file.exists()


def test_clear_stale_state_files_after_crash(isolated_pid_files):
    pid_file, endpoint_file = isolated_pid_files
    pid_file.write_text("4242")
    endpoint_file.write_text("{}")
    assert gsas_server._clear_stale_state_files() is True
    assert not pid_file.exists() and not endpoint_file.exists()
    assert gsas_server.is_server_running() is False  # lock released again


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

    # Running until the second (force) signal has been sent, then gone.
    monkeypatch.setattr(gsas_server, "_lock_held", lambda: len(calls["signals"]) < 2)
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

    monkeypatch.setattr(gsas_server, "_lock_held", lambda: True)
    monkeypatch.setattr(gsas_server.os, "kill", fake_kill)
    monkeypatch.setattr(gsas_server.time, "sleep", lambda s: None)

    result = gsas_server.stop_server()
    assert result is False
    assert calls["signals"] == [(4242, signal.SIGTERM), (4242, _FORCE_SIGNAL)]
    assert "failed to stop" in capsys.readouterr().out.lower()


def test_response_model_keeps_native_result_keys():
    """A native gsasii.* result crosses the server with its standard statistics and warnings (re/04, A125)."""
    native = {"status": "ok", "success": True, "converged": False, "envelope_version": "1",
              "rwp": 6.53, "r_exp": 12.4, "gof": 0.52, "chi2_red": 0.27,
              "engine_details": {"engine": "GSAS-II", "parameters_varied": 13},
              "warnings": [{"code": "c", "message": "m", "field_path": None}]}
    dumped = gsas_server.SimulationResponse(**native).model_dump()
    assert {k: dumped[k] for k in native} == native


def test_response_model_carries_a_failed_envelope():
    """re/12: a failed envelope (no `success` key) crosses the response model with its failure record."""
    from powderline import results
    env = results.failure_envelope("engine", "diverged", "m", run_id="r", method="server")
    dumped = gsas_server.SimulationResponse(**env).model_dump()
    assert dumped["status"] == "failed" and dumped["failure"]["reason"] == "diverged" and dumped["success"] is None


# --- POWDERLINE_NO_SERVER ---

@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", " Yes ", "on", "ON"])
def test_server_disabled_truthy(monkeypatch, value):
    monkeypatch.setenv("POWDERLINE_NO_SERVER", value)
    assert gsas_server.server_disabled() is True


@pytest.mark.parametrize("value", [None, "", "0", "false", "no", "off"])
def test_server_disabled_falsy(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("POWDERLINE_NO_SERVER", raising=False)
    else:
        monkeypatch.setenv("POWDERLINE_NO_SERVER", value)
    assert gsas_server.server_disabled() is False


@pytest.mark.parametrize("action", ["start", "restart"])
def test_main_start_refuses_when_disabled(monkeypatch, capsys, action):
    monkeypatch.setenv("POWDERLINE_NO_SERVER", "1")
    monkeypatch.setattr(sys, "argv", ["gsas_server.py", action])
    monkeypatch.setattr(gsas_server, "GSASServer",
                        lambda *a, **k: pytest.fail("server must not be created"))
    monkeypatch.setattr(gsas_server, "stop_server",
                        lambda: pytest.fail("restart must not stop the server first"))
    with pytest.raises(SystemExit) as exc:
        gsas_server.main()
    assert exc.value.code == 1
    assert "POWDERLINE_NO_SERVER" in capsys.readouterr().out


# --- Per-user state directory ---

@posix_only
def test_state_dir_uses_private_xdg_runtime_dir(monkeypatch, tmp_path):
    runtime = tmp_path / "runtime"
    runtime.mkdir(mode=0o700)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    path = gsas_server.state_dir()
    assert path == runtime / "powderline"
    assert (path.stat().st_mode & 0o777) == 0o700


@posix_only
def test_state_dir_falls_back_to_per_uid_tempdir(monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    monkeypatch.setattr(gsas_server.tempfile, "gettempdir", lambda: str(tmp_path))
    path = gsas_server.state_dir()
    assert path == tmp_path / f"powderline-{os.getuid()}"
    assert (path.stat().st_mode & 0o777) == 0o700


@posix_only
def test_state_dir_ignores_shared_xdg_runtime_dir(monkeypatch, tmp_path):
    # An XDG_RUNTIME_DIR others can write to (e.g. inherited after su) is not used.
    shared = tmp_path / "shared"
    shared.mkdir()
    shared.chmod(0o777)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(shared))
    monkeypatch.setattr(gsas_server.tempfile, "gettempdir", lambda: str(tmp_path))
    assert gsas_server.state_dir() == tmp_path / f"powderline-{os.getuid()}"


@posix_only
def test_state_dir_path_is_distinct_per_uid(monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    monkeypatch.setattr(gsas_server.tempfile, "gettempdir", lambda: str(tmp_path))
    paths = set()
    for uid in (1001, 1002):
        monkeypatch.setattr(gsas_server.os, "getuid", lambda uid=uid: uid)
        paths.add(gsas_server._state_dir_path())
    assert paths == {tmp_path / "powderline-1001", tmp_path / "powderline-1002"}


@posix_only
def test_state_dir_refuses_dir_owned_by_another_user(monkeypatch, tmp_path):
    # The dir exists (created by "someone else"): we are not its owner.
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    monkeypatch.setattr(gsas_server.tempfile, "gettempdir", lambda: str(tmp_path))
    other_uid = os.getuid() + 1
    (tmp_path / f"powderline-{other_uid}").mkdir(mode=0o700)
    monkeypatch.setattr(gsas_server.os, "getuid", lambda: other_uid)
    # Isolate the directory-owner check from the parent check.
    monkeypatch.setattr(gsas_server, "_check_tempdir_parent", lambda path: None)
    with pytest.raises(gsas_server.ServerStateError, match="not by you"):
        gsas_server.state_dir()


@posix_only
def test_state_dir_refuses_symlink(monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    monkeypatch.setattr(gsas_server.tempfile, "gettempdir", lambda: str(tmp_path))
    target = tmp_path / "elsewhere"
    target.mkdir(mode=0o700)
    (tmp_path / f"powderline-{os.getuid()}").symlink_to(target)
    with pytest.raises(gsas_server.ServerStateError, match="symlink"):
        gsas_server.state_dir()


@posix_only
@pytest.mark.parametrize("mode", [0o750, 0o705, 0o777])
def test_state_dir_refuses_group_or_other_access(monkeypatch, tmp_path, mode):
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    monkeypatch.setattr(gsas_server.tempfile, "gettempdir", lambda: str(tmp_path))
    path = tmp_path / f"powderline-{os.getuid()}"
    path.mkdir()
    path.chmod(mode)
    with pytest.raises(gsas_server.ServerStateError, match="accessible to other users"):
        gsas_server.state_dir()


@pytest.mark.skipif(os.name != "nt", reason="Windows state-dir location")
def test_state_dir_windows_uses_localappdata(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert gsas_server.state_dir() == tmp_path / "powderline"


@posix_only
def test_state_dir_ignores_symlinked_xdg_runtime_dir(monkeypatch, tmp_path):
    # A symlink (which its owner could retarget later) is never trusted, even
    # if it currently points at a private directory of ours.
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    link = tmp_path / "runtime-link"
    link.symlink_to(private)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(link))
    monkeypatch.setattr(gsas_server.tempfile, "gettempdir", lambda: str(tmp_path))
    assert gsas_server.state_dir() == tmp_path / f"powderline-{os.getuid()}"


@posix_only
def test_state_dir_refuses_shared_non_sticky_tempdir(monkeypatch, tmp_path):
    # Others could rename/replace our directory in a world-writable parent
    # that lacks the sticky bit.
    shared = tmp_path / "shared"
    shared.mkdir()
    shared.chmod(0o777)
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    monkeypatch.setattr(gsas_server.tempfile, "gettempdir", lambda: str(shared))
    with pytest.raises(gsas_server.ServerStateError, match="sticky"):
        gsas_server.state_dir()


@posix_only
def test_state_dir_accepts_sticky_shared_tempdir(monkeypatch, tmp_path):
    shared = tmp_path / "tmp"
    shared.mkdir()
    shared.chmod(0o1777)  # like /tmp
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    monkeypatch.setattr(gsas_server.tempfile, "gettempdir", lambda: str(shared))
    assert gsas_server.state_dir() == shared / f"powderline-{os.getuid()}"


@posix_only
def test_state_dir_refuses_tempdir_owned_by_another_user(monkeypatch, tmp_path):
    # The owner of a parent directory can always rename entries in it.
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    monkeypatch.setattr(gsas_server.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(gsas_server.os, "getuid", lambda: os.stat(tmp_path).st_uid + 1)
    with pytest.raises(gsas_server.ServerStateError, match="is owned by uid"):
        gsas_server.state_dir()


@posix_only
def test_state_files_never_opened_through_a_swapped_symlink(monkeypatch, tmp_path):
    # Even if the directory is replaced by a symlink after state_dir() checked
    # it, files are opened relative to an O_NOFOLLOW, fstat-verified handle.
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir(mode=0o700)
    (elsewhere / "server.json").write_text('{"pid": 1, "port": 2, "token": "planted"}')
    swapped = tmp_path / "state"
    swapped.symlink_to(elsewhere)
    monkeypatch.setattr(gsas_server, "state_dir", lambda: swapped)
    with pytest.raises(gsas_server.ServerStateError):
        gsas_server.read_endpoint()


@posix_only
def test_write_private_is_owner_only(isolated_pid_files):
    _, target = isolated_pid_files
    target.write_text("old")
    target.chmod(0o644)
    gsas_server._write_private(gsas_server.ENDPOINT_NAME, "new")
    assert target.read_text() == "new"
    assert (target.stat().st_mode & 0o777) == 0o600


def test_read_endpoint_absent_or_garbage_is_none(isolated_pid_files):
    _, endpoint_file = isolated_pid_files
    assert gsas_server.read_endpoint() is None
    endpoint_file.write_text("{not json")
    assert gsas_server.read_endpoint() is None
    endpoint_file.write_text('{"pid": 1, "port": 2}')  # no token
    assert gsas_server.read_endpoint() is None
    endpoint_file.write_text('{"pid": 1, "port": 2, "token": "t"}')
    assert gsas_server.read_endpoint() == {"pid": 1, "port": 2, "token": "t"}


# --- Start lock / state-file lifecycle ---

def test_claim_server_lock_when_free(isolated_pid_files):
    pid_file, _ = isolated_pid_files
    assert gsas_server._claim_server_lock(attempts=1) is True
    assert pid_file.read_text() == str(os.getpid())
    assert gsas_server.is_server_running() is True


def test_claim_server_lock_refused_while_held(isolated_pid_files, other_holder):
    pid_file, _ = isolated_pid_files
    pid_file.write_text("4242")
    assert gsas_server._claim_server_lock(attempts=2, delay=0) is False
    assert pid_file.read_text() == "4242"


def test_claim_server_lock_replaces_stale_files(isolated_pid_files):
    # A crashed server left its files behind; the lock is free, so they are stale.
    pid_file, endpoint_file = isolated_pid_files
    pid_file.write_text("4242")
    endpoint_file.write_text('{"pid": 4242, "port": 1, "token": "old"}')
    assert gsas_server._claim_server_lock(attempts=1) is True
    assert pid_file.read_text() == str(os.getpid())
    assert not endpoint_file.exists()


def test_release_state_files_only_when_holding_the_lock(isolated_pid_files):
    pid_file, endpoint_file = isolated_pid_files
    pid_file.write_text("4242")
    endpoint_file.write_text("{}")
    gsas_server._release_state_files()  # not the holder: touches nothing
    assert pid_file.exists() and endpoint_file.exists()

    assert gsas_server._claim_server_lock(attempts=1) is True
    endpoint_file.write_text("{}")
    gsas_server._release_state_files()
    assert not pid_file.exists() and not endpoint_file.exists()
    assert gsas_server.is_server_running() is False


_CLAIM_SCRIPT = r"""
import importlib.util, sys, time
spec = importlib.util.spec_from_file_location("gs", sys.argv[1])
gs = importlib.util.module_from_spec(spec); spec.loader.exec_module(gs)
while time.time() < float(sys.argv[2]):
    pass
won = gs._claim_server_lock(attempts=1)
print("WON" if won else "LOST", flush=True)
if won:
    sys.stdin.read()  # hold the lock until the test has every result
"""


def test_concurrent_starts_yield_exactly_one_holder(tmp_path):
    # Many starters at once, with stale files from a crashed server present
    # (the case that defeated a PID-file lock): exactly one may win.
    state = tmp_path / "powderline"
    state.mkdir(mode=0o700)
    (state / "server.pid").write_text("999999")
    (state / "server.json").write_text('{"pid": 999999, "port": 1, "token": "old"}')
    env = {**os.environ, "XDG_RUNTIME_DIR": str(tmp_path), "LOCALAPPDATA": str(tmp_path)}
    n = 8
    go = time.time() + 2.0  # start together; a late starter still finds the lock held
    procs = [subprocess.Popen(
        [sys.executable, "-c", _CLAIM_SCRIPT, gsas_server.__file__, str(go)],
        env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        for _ in range(n)]
    try:
        results = [p.stdout.readline().strip() for p in procs]
    finally:
        for p in procs:
            p.stdin.close()
            p.wait(timeout=60)
    assert sorted(results) == ["LOST"] * (n - 1) + ["WON"], results


# --- Signed requests on the ASGI app ---

TOKEN = "s3cret-token"


def _signed_headers(method, path, body=b"", *, token=TOKEN, nonce=None, ts=None):
    nonce = nonce or secrets.token_hex(16)
    ts = ts if ts is not None else str(int(time.time()))
    return nonce, {
        gsas_server.NONCE_HEADER: nonce,
        gsas_server.TIMESTAMP_HEADER: ts,
        gsas_server.SIGNATURE_HEADER: gsas_server.sign_request(
            token, method, path, b"", nonce, ts, body),
        "content-type": "application/json",
    }


def _response_signed(resp, nonce, token=TOKEN):
    expected = gsas_server.sign_response(token, nonce, resp.status_code, resp.content)
    return resp.headers.get(gsas_server.RESPONSE_SIGNATURE_HEADER) == expected


@pytest.fixture
def app_client():
    """TestClient for the real app, without importing GSAS-II or binding a port."""
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    import logging
    from fastapi.testclient import TestClient

    server = gsas_server.GSASServer.__new__(gsas_server.GSASServer)
    server.request_count = 0
    server.start_time = None
    server.token = TOKEN
    server.logger = logging.getLogger("powderline.server.test")
    calls = []
    server._run_simulation = lambda req: calls.append(req) or {
        "success": False, "error": "stub", "method": "server"}
    return TestClient(server.create_app()), calls


def _job_body(out):
    return json.dumps({"recipe_data": {}, "output_dir": str(out)}).encode()


@pytest.mark.parametrize("case", [
    "unsigned", "bearer_token", "wrong_token", "stale_timestamp", "future_timestamp",
    "tampered_body", "wrong_path", "non_hex_nonce", "short_nonce", "non_ascii_signature",
])
def test_unauthenticated_requests_rejected(app_client, case, tmp_path):
    client, calls = app_client
    out = tmp_path / "out"
    body = _job_body(out)
    if case == "unsigned":
        headers = {}
    elif case == "bearer_token":
        headers = {"Authorization": f"Bearer {TOKEN}"}  # the token alone is not enough
    elif case == "wrong_token":
        _, headers = _signed_headers("POST", "/simulate", body, token="other-token")
    elif case == "stale_timestamp":
        _, headers = _signed_headers("POST", "/simulate", body, ts=str(int(time.time()) - 3600))
    elif case == "future_timestamp":
        _, headers = _signed_headers("POST", "/simulate", body, ts=str(int(time.time()) + 3600))
    elif case == "tampered_body":
        _, headers = _signed_headers("POST", "/simulate", body)
        body = _job_body(tmp_path / "elsewhere")
    elif case == "wrong_path":
        _, headers = _signed_headers("POST", "/health", body)
    elif case == "non_hex_nonce":
        _, headers = _signed_headers("POST", "/simulate", body, nonce="z" * 32)
    elif case == "short_nonce":
        _, headers = _signed_headers("POST", "/simulate", body, nonce="ab")
    else:
        _, headers = _signed_headers("POST", "/simulate", body)
        headers[gsas_server.SIGNATURE_HEADER] = "s\u00e9".encode("latin-1")
    resp = client.post("/simulate", headers=headers, content=body)
    assert resp.status_code == 401
    assert gsas_server.RESPONSE_SIGNATURE_HEADER not in resp.headers
    assert calls == [], "no job may run without a valid signature"
    assert not out.exists()


def test_signed_requests_accepted_and_responses_signed(app_client, tmp_path):
    client, calls = app_client
    body = _job_body(tmp_path)
    nonce, headers = _signed_headers("POST", "/simulate", body)
    resp = client.post("/simulate", headers=headers, content=body)
    assert resp.status_code == 200
    assert len(calls) == 1
    assert _response_signed(resp, nonce)
    assert not _response_signed(resp, nonce, token="other-token")

    nonce, headers = _signed_headers("GET", "/health")
    resp = client.get("/health", headers=headers)
    assert resp.status_code == 200 and resp.json()["status"] == "ok"
    assert _response_signed(resp, nonce)


def test_replayed_request_rejected(app_client, tmp_path):
    client, calls = app_client
    body = _job_body(tmp_path)
    _, headers = _signed_headers("POST", "/simulate", body)
    assert client.post("/simulate", headers=headers, content=body).status_code == 200
    assert client.post("/simulate", headers=headers, content=body).status_code == 401
    assert len(calls) == 1


def test_concurrent_replays_run_at_most_once():
    """Two copies of one signed request arriving together: the nonce is
    re-checked after the body is read, so only one reaches the app."""
    import asyncio

    calls = []

    async def app(scope, receive, send):
        calls.append(scope["path"])
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})

    middleware = gsas_server.SignedRequestMiddleware(app, TOKEN)
    body = b'{"recipe_data": {}, "output_dir": "x"}'
    _, headers = _signed_headers("POST", "/simulate", body)
    scope = {"type": "http", "method": "POST", "path": "/simulate", "query_string": b"",
             "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()]}

    async def main():
        body_ready = asyncio.Event()
        statuses = []

        async def receive():
            await body_ready.wait()  # both copies are suspended here together
            return {"type": "http.request", "body": body, "more_body": False}

        def sender():
            async def send(message):
                if message["type"] == "http.response.start":
                    statuses.append(message["status"])
            return send

        tasks = [asyncio.create_task(middleware(dict(scope), receive, sender()))
                 for _ in range(2)]
        await asyncio.sleep(0.05)
        body_ready.set()
        await asyncio.gather(*tasks)
        return statuses

    statuses = asyncio.run(main())
    assert sorted(statuses) == [200, 401]
    assert calls == ["/simulate"]


@pytest.mark.parametrize("method, path, body, status", [
    ("GET", "/nope", b"", 404),
    ("PUT", "/simulate", b"", 405),
    ("POST", "/simulate", b"not json", 422),
    ("GET", "/docs", b"", 404),
    ("GET", "/redoc", b"", 404),
    ("GET", "/openapi.json", b"", 404),
])
def test_every_route_and_error_needs_a_signature(app_client, method, path, body, status):
    client, calls = app_client
    assert client.request(method, path, content=body).status_code == 401
    nonce, headers = _signed_headers(method, path, body)
    resp = client.request(method, path, headers=headers, content=body)
    assert resp.status_code == status
    assert _response_signed(resp, nonce)  # error responses are signed too
    assert calls == []


def test_oversized_request_refused_unread(app_client, monkeypatch):
    client, calls = app_client
    monkeypatch.setattr(gsas_server, "MAX_BODY_BYTES", 16)
    body = b"x" * 64
    _, headers = _signed_headers("POST", "/simulate", body)
    assert client.post("/simulate", headers=headers, content=body).status_code == 413
    assert calls == []


@posix_only
def test_state_dir_uncreatable_raises_state_error(monkeypatch, tmp_path):
    # e.g. an unwritable/missing tempdir: callers must get ServerStateError, not OSError.
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    monkeypatch.setattr(gsas_server.tempfile, "gettempdir",
                        lambda: str(tmp_path / "missing" / "deeper"))
    with pytest.raises(gsas_server.ServerStateError, match="Cannot use"):
        gsas_server.state_dir()
    readonly = tmp_path / "readonly"
    readonly.mkdir(mode=0o500)
    monkeypatch.setattr(gsas_server.tempfile, "gettempdir", lambda: str(readonly))
    with pytest.raises(gsas_server.ServerStateError, match="Cannot create"):
        gsas_server.state_dir()


def test_response_model_keeps_warning_level():
    """P-34: an info-level warning crosses the server round trip with its level intact."""
    native = {"success": True, "warnings": [
        {"code": "symmetry_value_derived", "message": "m", "field_path": "payload.phases.P.atoms.A.y", "level": "info"},
        {"code": "gsasii_background_default_applied", "message": "n", "field_path": "payload.background"}]}
    dumped = gsas_server.SimulationResponse(**native).model_dump()
    assert dumped["warnings"] == native["warnings"]
