"""Unit tests for GSASClient.submit_simulation.

Primarily the server output-visibility guard: a GSAS-II server can be
reachable over localhost yet have a different filesystem view than the client
— e.g. running on another cluster node, or inside a sandbox/container with a
private /tmp. Such a server reports success while its output files never
appear for the client. submit_simulation must detect this (fit_profile.txt
not freshly written after a "successful" server run — a stale file from a
previous run into the same output_dir does not count), warn, and fall back
to in-process execution — or return a structured error when fallback is
disabled. Also covers the use_server bypass and the keyword-signature
regression guard.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from powderline.gateways.gsasii.client import GSASClient

#: Never validated on these paths (the submit methods are monkeypatched); the shape is enough.
RECIPE = {"schema_name": "gsasii.rietveld", "payload": {}}


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
    monkeypatch.setattr(client, "_submit_to_server",
                        lambda rd, od: {"success": False, "error": "boom"})
    monkeypatch.setattr(client, "_submit_via_subprocess",
                        lambda rd, od, verbose: pytest.fail("must not fall back"))

    result = client.submit_simulation(RECIPE, tmp_path)
    assert result["success"] is False
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
    assert result["success"] is False
    assert result["method"] == "none"
    assert "use_server=False" in result["error"]
    assert "fallback_to_subprocess=False" in result["error"]
    assert "Server not available" not in result["error"]


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

    assert result["success"] is False
    assert result["method"] == "server"
    assert "did not write output files visible" in result["error"]
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
