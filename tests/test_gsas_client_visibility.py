"""Unit tests for GSASClient.submit_simulation.

Primarily the server output-visibility guard: a GSAS-II server can be
reachable over localhost yet have a different filesystem view than the client
— e.g. running on another cluster node, or inside a sandbox/container with a
private /tmp. Such a server reports success while its output files never
appear for the client. submit_simulation must detect this (fit_profile.txt
not freshly written after a "successful" server run — a stale file from a
previous run into the same output_dir does not count), warn, and fall back
to in-process execution — or return a structured error when fallback is
disabled. Also covers the use_server bypass, the keyword-signature
regression guard, server identity (a server must prove it holds this user's
token) and the POWDERLINE_NO_SERVER switch.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from powderline.gateways.gsasii import client as gsas_client, server as gsas_server
from powderline.gateways.gsasii.client import GSASClient

pytestmark = pytest.mark.core

#: Never validated on these paths (the submit methods are monkeypatched); the shape is enough.
RECIPE = {"schema_name": "gsasii.rietveld", "payload": {}}


@pytest.fixture(autouse=True)
def _server_enabled(monkeypatch):
    """Tests assume server use is allowed unless they opt out explicitly."""
    monkeypatch.delenv("POWDERLINE_NO_SERVER", raising=False)


@pytest.fixture
def client(monkeypatch):
    c = GSASClient()
    monkeypatch.setattr(c, "is_server_available", lambda: True)
    return c


def test_server_success_without_visible_files_falls_back(client, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(client, "_submit_to_server",
                        lambda rd, od: {"success": True})
    calls = []
    monkeypatch.setattr(client, "_submit_via_subprocess",
                        lambda rd, od, verbose: calls.append(1) or {"success": True})

    result = client.submit_simulation(RECIPE, tmp_path)

    assert calls, "expected fallback to in-process execution"
    assert result["method"] == "subprocess"
    out = capsys.readouterr().out
    assert "did not write output files visible to this process" in out
    assert "gsas-server restart" in out


def test_server_success_with_visible_files_is_returned(client, monkeypatch, tmp_path):
    def fake_server_run(rd, od):
        (tmp_path / "fit_profile.txt").write_text("two_theta\ty_calc\n")
        return {"success": True}

    monkeypatch.setattr(client, "_submit_to_server", fake_server_run)
    monkeypatch.setattr(client, "_submit_via_subprocess",
                        lambda rd, od, verbose: pytest.fail("must not fall back"))

    result = client.submit_simulation(RECIPE, tmp_path)
    assert result["success"] is True
    assert result["method"] == "server"


def test_stale_fit_profile_does_not_mask_divergent_server(client, monkeypatch, tmp_path, capsys):
    """A fit_profile.txt left by a PREVIOUS run into the same output_dir must
    not satisfy the guard: if the server 'succeeds' without (re)writing the
    file, the run falls back in-process."""
    (tmp_path / "fit_profile.txt").write_text("two_theta\ty_calc\n1.0\t10.0\n")

    monkeypatch.setattr(client, "_submit_to_server",
                        lambda rd, od: {"success": True})  # touches nothing
    calls = []
    monkeypatch.setattr(client, "_submit_via_subprocess",
                        lambda rd, od, verbose: calls.append(1) or {"success": True})

    result = client.submit_simulation(RECIPE, tmp_path)

    assert calls, "stale fit_profile.txt must not pass the freshness check"
    assert result["method"] == "subprocess"
    assert "did not write output files visible" in capsys.readouterr().out


def test_rewritten_fit_profile_counts_as_fresh(client, monkeypatch, tmp_path):
    """Re-running into a used output_dir with a healthy server: the rewritten
    fit_profile.txt (changed stat) must pass the guard."""
    (tmp_path / "fit_profile.txt").write_text("two_theta\ty_calc\n1.0\t10.0\n")

    def fake_server_run(rd, od):
        # New content with a different size — deterministic stat change even
        # on filesystems with coarse mtime granularity
        (tmp_path / "fit_profile.txt").write_text(
            "two_theta\ty_calc\n1.0\t10.0\n2.0\t20.0\n")
        return {"success": True}

    monkeypatch.setattr(client, "_submit_to_server", fake_server_run)
    monkeypatch.setattr(client, "_submit_via_subprocess",
                        lambda rd, od, verbose: pytest.fail("must not fall back"))

    result = client.submit_simulation(RECIPE, tmp_path)
    assert result["success"] is True
    assert result["method"] == "server"


def test_server_error_result_not_subject_to_file_check(client, monkeypatch, tmp_path):
    """A server-reported failure is returned as-is (no file check, no fallback
    beyond the existing error path)."""
    from powderline import results
    monkeypatch.setattr(client, "_submit_to_server",
                        lambda rd, od: results.failure_envelope("engine", "diverged", "boom"))
    monkeypatch.setattr(client, "_submit_via_subprocess",
                        lambda rd, od, verbose: pytest.fail("must not fall back"))

    result = client.submit_simulation(RECIPE, tmp_path)
    assert result["status"] == "failed" and result["failure"]["reason"] == "diverged"
    assert result["method"] == "server"


def test_use_server_false_skips_server_entirely(monkeypatch, tmp_path):
    c = GSASClient()
    monkeypatch.setattr(c, "is_server_available",
                        lambda: pytest.fail("server must not be consulted"))
    monkeypatch.setattr(c, "_submit_via_subprocess",
                        lambda rd, od, verbose: {"success": True})

    result = c.submit_simulation(RECIPE, tmp_path, use_server=False)
    assert result["success"] is True
    assert result["method"] == "subprocess"


def test_use_server_false_with_fallback_disabled_errors_honestly(monkeypatch, tmp_path):
    """Both execution paths disabled by the caller is a legal (if odd) state:
    it must return a structured error naming BOTH flags — not the misleading
    'Server not available' message (the server was never consulted)."""
    c = GSASClient(fallback_to_subprocess=False)
    monkeypatch.setattr(c, "is_server_available",
                        lambda: pytest.fail("server must not be consulted"))

    result = c.submit_simulation(RECIPE, tmp_path, use_server=False)
    assert result["status"] == "failed" and result["failure"]["kind"] == "backend"
    assert result["failure"]["reason"] == "server_unavailable"
    assert result["method"] == "none"
    assert "use_server=False" in result["failure"]["message"]
    assert "fallback_to_subprocess=False" in result["failure"]["message"]
    assert "Server not available" not in result["failure"]["message"]


def test_invisible_output_with_fallback_disabled_returns_error(monkeypatch, tmp_path, capsys):
    """With fallback_to_subprocess=False (execution_mode='server'), invisible
    server output must yield a structured error naming the filesystem-view
    mismatch — not the misleading 'server not available' message, and no
    promise of a fallback that won't happen."""
    c = GSASClient(fallback_to_subprocess=False)
    monkeypatch.setattr(c, "is_server_available", lambda: True)
    monkeypatch.setattr(c, "_submit_to_server", lambda rd, od: {"success": True})
    monkeypatch.setattr(c, "_submit_via_subprocess",
                        lambda rd, od, verbose: pytest.fail("must not fall back"))

    result = c.submit_simulation(RECIPE, tmp_path)

    assert result["status"] == "failed" and result["failure"]["reason"] == "output_not_visible"
    assert result["method"] == "server"
    assert "did not write output files visible" in result["failure"]["message"]
    out = capsys.readouterr().out
    assert "fallback is disabled" in out
    assert "Falling back" not in out


def test_invisible_output_guard_in_auto_start_branch(monkeypatch, tmp_path, capsys):
    """The guard must also cover the auto-start branch (server initially
    unavailable, then started and answering with invisible output)."""
    c = GSASClient()
    availability = iter([False, True])  # unavailable, then up after auto-start
    monkeypatch.setattr(c, "is_server_available", lambda: next(availability))
    monkeypatch.setattr(c, "_start_server_background", lambda verbose: True)
    monkeypatch.setattr(c, "_submit_to_server", lambda rd, od: {"success": True})
    calls = []
    monkeypatch.setattr(c, "_submit_via_subprocess",
                        lambda rd, od, verbose: calls.append(1) or {"success": True})

    result = c.submit_simulation(RECIPE, tmp_path, auto_start_server=True)

    assert calls, "expected fallback to in-process execution"
    assert result["method"] == "subprocess"
    assert ("did not write output files visible to this process"
            in capsys.readouterr().out)


def test_submit_simulation_wrong_keyword_raises():
    """Regression guard: 'recipe_path' keyword no longer exists on
    submit_simulation (mp_simulate.py once passed recipe_path=..., causing a
    runtime TypeError; the correct keyword is recipe=)."""
    client = GSASClient()
    with pytest.raises(TypeError, match="recipe_path"):
        client.submit_simulation(recipe_path=Path("dummy.json"), output_dir=Path("."))


# --- Server identity: only this user's server, proven by its token ---

class _FakeResponse:
    """Minimal httpx.Response stand-in."""

    def __init__(self, status_code, body, headers=None):
        self.status_code = status_code
        self._body = body
        self.content = json.dumps(body).encode()
        self.headers = headers or {}

    def json(self):
        return self._body

    def raise_for_status(self):
        pass


def _signed_response(token, request_headers, body, status_code=200):
    """A response signed with ``token`` for the request that carried ``request_headers``."""
    resp = _FakeResponse(status_code, body)
    resp.headers = {gsas_server.RESPONSE_SIGNATURE_HEADER: gsas_server.sign_response(
        token, request_headers[gsas_server.NONCE_HEADER], status_code, resp.content)}
    return resp


@pytest.fixture
def own_endpoint(monkeypatch, tmp_path):
    monkeypatch.setattr(gsas_server, "state_dir", lambda: tmp_path)
    # A live PID: endpoints of dead servers are ignored before any probe.
    (tmp_path / "server.json").write_text(
        json.dumps({"pid": os.getpid(), "port": 50123, "token": "my-token"}))
    return tmp_path


def _fake_health(monkeypatch, respond):
    """Patch httpx.get; ``respond(headers) -> _FakeResponse``. Returns call log."""
    import httpx
    calls = []

    def fake_get(url, headers=None, **kwargs):
        calls.append({"url": url, "headers": headers, **kwargs})
        return respond(headers)

    monkeypatch.setattr(httpx, "get", fake_get)
    return calls


def _assert_token_not_sent(call):
    sent = json.dumps({k: v for k, v in call.items() if k != "content"}, default=str)
    sent += str(call.get("content", b""))
    assert "my-token" not in sent, "the token itself must never be sent"


def test_server_unavailable_without_endpoint(monkeypatch, tmp_path):
    monkeypatch.setattr(gsas_server, "state_dir", lambda: tmp_path)
    calls = _fake_health(monkeypatch, lambda h: pytest.fail("must not probe"))
    assert GSASClient().is_server_available() is False
    assert calls == []


def test_endpoint_of_dead_server_is_not_probed(monkeypatch, own_endpoint):
    monkeypatch.setattr(gsas_server, "_pid_alive", lambda pid: False)
    calls = _fake_health(monkeypatch, lambda h: pytest.fail("must not probe"))
    assert GSASClient().is_server_available() is False
    assert calls == []


def test_server_available_when_response_signed_with_token(monkeypatch, own_endpoint):
    calls = _fake_health(monkeypatch, lambda h: _signed_response("my-token", h, {"status": "ok"}))
    assert GSASClient().is_server_available() is True
    (call,) = calls
    assert call["url"] == "http://127.0.0.1:50123/health"
    assert call["trust_env"] is False  # never sent through a proxy
    _assert_token_not_sent(call)
    h = call["headers"]
    assert h[gsas_server.SIGNATURE_HEADER] == gsas_server.sign_request(
        "my-token", "GET", "/health", b"", h[gsas_server.NONCE_HEADER],
        h[gsas_server.TIMESTAMP_HEADER], b"")


def test_nonce_is_fresh_per_request(monkeypatch, own_endpoint):
    calls = _fake_health(monkeypatch, lambda h: _signed_response("my-token", h, {}))
    client = GSASClient()
    client.is_server_available()
    client.is_server_available()
    assert calls[0]["headers"][gsas_server.NONCE_HEADER] != \
        calls[1]["headers"][gsas_server.NONCE_HEADER]


def _echo_impostor(headers):
    """A listener without the token that signs with whatever secret it was sent
    (a bearer token, if the client ever sent one again)."""
    key = headers.get("Authorization", "").removeprefix("Bearer ") \
        or headers.get(gsas_server.SIGNATURE_HEADER, "")
    return _signed_response(key, headers, {"status": "ok"})


@pytest.mark.parametrize("respond", [
    lambda h: _FakeResponse(200, {"status": "ok"}),                 # unsigned / old server
    lambda h: _FakeResponse(200, {"status": "ok"},
                            {gsas_server.RESPONSE_SIGNATURE_HEADER: "0" * 64}),
    lambda h: _signed_response("other-token", h, {"status": "ok"}),  # another user's server
    lambda h: _signed_response("my-token", {gsas_server.NONCE_HEADER: "replayed"}, {}),
    lambda h: _FakeResponse(401, {"detail": "Unauthorized"}),
    _echo_impostor,                                                  # port squatter
])
def test_server_unavailable_without_valid_response_signature(monkeypatch, own_endpoint, respond):
    _fake_health(monkeypatch, respond)
    assert GSASClient().is_server_available() is False


def test_unsafe_state_dir_means_no_server(monkeypatch, capsys):
    def unsafe():
        raise gsas_server.ServerStateError("unsafe state dir")

    monkeypatch.setattr(gsas_server, "state_dir", unsafe)
    _fake_health(monkeypatch, lambda h: pytest.fail("must not probe"))
    c = GSASClient()
    assert c.is_server_available() is False
    assert c.is_server_available() is False
    assert c._start_server_background() is False
    assert capsys.readouterr().out.count("unsafe state dir") == 1


def _fake_post(monkeypatch, respond):
    import httpx
    seen = {}

    def fake_post(url, content=None, headers=None, **kwargs):
        seen.update(url=url, content=content, headers=headers, **kwargs)
        return respond(headers)

    monkeypatch.setattr(httpx, "post", fake_post)
    return seen


def test_submit_is_signed_and_response_verified(monkeypatch, own_endpoint, tmp_path):
    seen = _fake_post(monkeypatch, lambda h: _signed_response("my-token", h, {"success": True}))
    assert GSASClient()._submit_to_server(RECIPE, tmp_path / "out") == {"success": True}
    assert seen["url"] == "http://127.0.0.1:50123/simulate"
    assert seen["trust_env"] is False
    _assert_token_not_sent(seen)
    h = seen["headers"]
    assert h[gsas_server.SIGNATURE_HEADER] == gsas_server.sign_request(
        "my-token", "POST", "/simulate", b"", h[gsas_server.NONCE_HEADER],
        h[gsas_server.TIMESTAMP_HEADER], seen["content"])


def test_forged_server_result_is_never_trusted(monkeypatch, own_endpoint, tmp_path):
    """A listener that took over the port answers 'success' without the token:
    the run must not report success from it."""
    _fake_post(monkeypatch, lambda h: _FakeResponse(200, {"success": True, "rwp": 1.0}))
    monkeypatch.setattr(GSASClient, "is_server_available", lambda self: True)
    c = GSASClient(fallback_to_subprocess=False)
    with pytest.raises(gsas_client.ServerIdentityError):
        c._submit_to_server(RECIPE, tmp_path / "out")
    result = c.submit_simulation(RECIPE, tmp_path / "out", auto_start_server=False)
    assert result["status"] == "failed" and result["failure"]["reason"] == "communication_failed"
    assert "did not prove" in result["failure"]["message"]


# --- POWDERLINE_NO_SERVER ---

@pytest.fixture
def no_server(monkeypatch):
    monkeypatch.setenv("POWDERLINE_NO_SERVER", "1")


def _forbid_server(monkeypatch, c):
    for name in ("is_server_available", "_submit_to_server"):
        monkeypatch.setattr(c, name, lambda *a, **k: pytest.fail(f"{name} called"))
    monkeypatch.setattr(c, "_start_server_background",
                        lambda *a, **k: pytest.fail("server auto-started"))


def test_no_server_env_auto_runs_in_process(no_server, monkeypatch, tmp_path, capsys):
    c = GSASClient()
    _forbid_server(monkeypatch, c)
    monkeypatch.setattr(c, "_submit_via_subprocess",
                        lambda rd, od, verbose: {"success": True})
    result = c.submit_simulation(RECIPE, tmp_path, auto_start_server=True)
    assert result == {"success": True, "method": "subprocess"}
    assert "POWDERLINE_NO_SERVER" in capsys.readouterr().out


def test_no_server_env_server_mode_errors(no_server, monkeypatch, tmp_path):
    c = GSASClient(fallback_to_subprocess=False)
    _forbid_server(monkeypatch, c)
    monkeypatch.setattr(c, "_submit_via_subprocess",
                        lambda *a: pytest.fail("must not run in-process"))
    result = c.submit_simulation(RECIPE, tmp_path)
    assert result["status"] == "failed" and result["failure"]["reason"] == "server_disabled"
    assert "POWDERLINE_NO_SERVER" in result["failure"]["message"]


def test_no_server_env_blocks_background_start(no_server, monkeypatch):
    import subprocess as sp
    monkeypatch.setattr(sp, "Popen", lambda *a, **k: pytest.fail("server spawned"))
    assert GSASClient()._start_server_background() is False


def test_no_server_env_direct_probe_contacts_nothing(no_server, monkeypatch, own_endpoint):
    _fake_health(monkeypatch, lambda h: pytest.fail("must not contact a server"))
    assert GSASClient().is_server_available() is False


# --- re/12: the client returns envelopes; a failed one is never followed by an in-process re-run ------

def test_server_failed_envelope_is_raised_not_rerun(client, monkeypatch, tmp_path):
    """Review focus 3: a failed envelope from the server is returned as-is; the client never re-runs in-process."""
    from powderline import results
    failed = results.failure_envelope("engine", "diverged", "server says diverged", method="server")
    monkeypatch.setattr(client, "is_server_available", lambda: True)
    monkeypatch.setattr(client, "_submit_to_server", lambda *a, **k: dict(failed))
    calls = []
    monkeypatch.setattr(client, "_submit_via_subprocess", lambda *a, **k: calls.append(1) or {})
    result = client.submit_simulation(RECIPE, tmp_path)
    assert result["status"] == "failed" and result["failure"]["reason"] == "diverged"
    assert result["method"] == "server" and calls == []


def test_post_timeout_is_a_backend_failure_without_fallback(client, monkeypatch, tmp_path):
    import httpx
    monkeypatch.setattr(client, "is_server_available", lambda: True)

    def timeout(*a, **k):
        raise httpx.ReadTimeout("300 s")
    monkeypatch.setattr(client, "_submit_to_server", timeout)
    calls = []
    monkeypatch.setattr(client, "_submit_via_subprocess", lambda *a, **k: calls.append(1) or {})
    result = client.submit_simulation(RECIPE, tmp_path)   # fallback_to_subprocess=True by default
    assert result["status"] == "failed" and result["failure"]["reason"] == "timeout"
    assert calls == [], "a timed-out request must never fall back: the server may still be writing"


def test_post_timeout_in_auto_start_branch_never_falls_back(monkeypatch, tmp_path):
    """Review (re/12): the auto-start branch had its own bare except; a timeout there fell back in-process."""
    import httpx
    c = GSASClient()
    availability = iter([False, True])  # unavailable, then up after auto-start
    monkeypatch.setattr(c, "is_server_available", lambda: next(availability))
    monkeypatch.setattr(c, "_start_server_background", lambda verbose: True)
    monkeypatch.setattr("time.sleep", lambda s: None)

    def timeout(*a, **k):
        raise httpx.ReadTimeout("300 s")
    monkeypatch.setattr(c, "_submit_to_server", timeout)
    calls = []
    monkeypatch.setattr(c, "_submit_via_subprocess", lambda *a, **k: calls.append(1) or {})
    result = c.submit_simulation(RECIPE, tmp_path, auto_start_server=True)
    assert result["status"] == "failed" and result["failure"]["reason"] == "timeout"
    assert calls == []


def test_communication_failure_in_auto_start_branch_with_fallback_disabled(monkeypatch, tmp_path):
    c = GSASClient(fallback_to_subprocess=False)
    availability = iter([False, True])
    monkeypatch.setattr(c, "is_server_available", lambda: next(availability))
    monkeypatch.setattr(c, "_start_server_background", lambda verbose: True)
    monkeypatch.setattr("time.sleep", lambda s: None)

    def broken(*a, **k):
        raise RuntimeError("connection reset")
    monkeypatch.setattr(c, "_submit_to_server", broken)
    monkeypatch.setattr(c, "_submit_via_subprocess", lambda *a, **k: pytest.fail("must not fall back"))
    result = c.submit_simulation(RECIPE, tmp_path, auto_start_server=True)
    assert result["status"] == "failed" and result["failure"]["reason"] == "communication_failed"
    assert "connection reset" in result["failure"]["message"]


# --- re/14 items 2 and 3: a bad port never spawns a doomed server; unsigned rejections read as such ----

def test_auto_start_with_bad_port_env_fails_without_spawning(monkeypatch, tmp_path):
    import subprocess
    monkeypatch.delenv("POWDERLINE_NO_SERVER", raising=False)
    monkeypatch.setenv("POWDERLINE_SERVER_PORT", "auto")
    monkeypatch.setattr(gsas_server, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(subprocess, "Popen",
                        lambda *a, **k: pytest.fail("a server that cannot bind must not be spawned"))
    c = GSASClient(fallback_to_subprocess=False)
    monkeypatch.setattr(c, "is_server_available", lambda: False)
    result = c.submit_simulation(RECIPE, tmp_path, auto_start_server=True)
    assert result["status"] == "failed" and result["failure"]["reason"] == "server_unavailable"
    assert "POWDERLINE_SERVER_PORT" in result["failure"]["message"]


def test_unsigned_413_is_reported_as_too_large(monkeypatch, own_endpoint, tmp_path):
    _fake_post(monkeypatch, lambda h: _FakeResponse(413, {"detail": "Request too large"}))
    monkeypatch.setattr(GSASClient, "is_server_available", lambda self: True)
    c = GSASClient(fallback_to_subprocess=False)
    result = c.submit_simulation(RECIPE, tmp_path / "out", auto_start_server=False)
    assert result["status"] == "failed" and result["failure"]["reason"] == "communication_failed"
    assert "too large" in result["failure"]["message"] and "did not prove" not in result["failure"]["message"]


def test_unsigned_401_is_reported_as_rejected(monkeypatch, own_endpoint, tmp_path):
    _fake_post(monkeypatch, lambda h: _FakeResponse(401, {"detail": "Unauthorized"}))
    monkeypatch.setattr(GSASClient, "is_server_available", lambda self: True)
    c = GSASClient(fallback_to_subprocess=False)
    result = c.submit_simulation(RECIPE, tmp_path / "out", auto_start_server=False)
    assert result["status"] == "failed" and result["failure"]["reason"] == "communication_failed"
    assert "rejected" in result["failure"]["message"] and "did not prove" not in result["failure"]["message"]
