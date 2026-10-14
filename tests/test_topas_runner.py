"""Tests for powderline.gateways.topas.runner (tc.exe discovery + injected run)."""

from __future__ import annotations

from pathlib import Path

import pytest

from powderline.gateways.topas import runner

pytestmark = pytest.mark.topas_gen


def _make_tc(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    tc = directory / "tc.exe"
    tc.write_text("")  # dummy executable
    return tc


# --- discovery --------------------------------------------------------------


def test_discover_explicit_dir(tmp_path):
    tc = _make_tc(tmp_path / "TOPAS8")
    assert runner.discover_tc_exe(topas_dir=tmp_path / "TOPAS8") == tc


def test_discover_returns_none_when_absent(tmp_path, monkeypatch):
    # no config, no default dirs on a Linux box -> None
    monkeypatch.setattr(runner, "_DEFAULT_TOPAS_DIRS", ())
    monkeypatch.setattr(runner, "_read_topas_config", lambda: {})
    assert runner.discover_tc_exe() is None
    assert runner.discover_tc_exe(topas_dir=tmp_path / "nope") is None


def test_discover_from_config(tmp_path, monkeypatch):
    tc = _make_tc(tmp_path / "topas_home")
    monkeypatch.setattr(runner, "_read_topas_config", lambda: {"dir": str(tmp_path / "topas_home")})
    assert runner.discover_tc_exe() == tc


def test_explicit_dir_beats_config(tmp_path, monkeypatch):
    cfg_tc = _make_tc(tmp_path / "cfg")
    arg_tc = _make_tc(tmp_path / "arg")
    monkeypatch.setattr(runner, "_read_topas_config", lambda: {"dir": str(tmp_path / "cfg")})
    assert runner.discover_tc_exe(topas_dir=tmp_path / "arg") == arg_tc
    assert cfg_tc.exists()  # (sanity: config path is real but not chosen)


# --- run_topas (injected subprocess) ---------------------------------------


def test_run_topas_not_found(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "_DEFAULT_TOPAS_DIRS", ())
    monkeypatch.setattr(runner, "_read_topas_config", lambda: {})
    res = runner.run_topas(tmp_path / "x.inp")
    assert res.status == "tc_not_found"
    assert res.tc_path is None


def test_run_topas_invokes_with_cwd_and_full_path(tmp_path):
    tc = _make_tc(tmp_path / "TOPAS8")
    inp = tmp_path / "job" / "example.inp"
    inp.parent.mkdir()
    inp.write_text("' inp")
    calls = {}

    def fake_runner(command, cwd=None):
        calls["command"] = command
        calls["cwd"] = cwd
        return type("CP", (), {"returncode": 0})()

    res = runner.run_topas(inp, topas_dir=tmp_path / "TOPAS8", runner=fake_runner)
    assert res.status == "ran"
    assert res.returncode == 0
    assert calls["command"] == [str(tc), str(inp.resolve())]  # full input path
    assert calls["cwd"] == str(tc.parent)                     # cwd = TOPAS dir


def test_run_topas_nonzero_is_failed(tmp_path):
    _make_tc(tmp_path / "TOPAS8")
    inp = tmp_path / "x.inp"
    inp.write_text("")
    res = runner.run_topas(
        inp,
        topas_dir=tmp_path / "TOPAS8",
        runner=lambda command, cwd=None: type("CP", (), {"returncode": 2})(),
    )
    assert res.status == "failed"
    assert res.returncode == 2


def test_read_topas_config_malformed_raises(tmp_path, monkeypatch):
    cfg = tmp_path / ".powderline_config.yaml"
    cfg.write_text("topas: {version: 6, dir: [oops\n", encoding="utf-8")
    monkeypatch.setattr(runner, "_CONFIG_PATHS", [str(cfg)])
    with pytest.raises(ValueError, match=r"powderline_config\.yaml.*not valid YAML"):
        runner._read_topas_config()


def test_read_topas_config_absent_is_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "_CONFIG_PATHS", [str(tmp_path / "nope.yaml")])
    assert runner._read_topas_config() == {}


def test_read_topas_config_utf8_dir(tmp_path, monkeypatch):
    cfg = tmp_path / ".powderline_config.yaml"
    cfg.write_text("topas:\n  version: '6'\n  dir: 'C:/Progrämme/TOPAS6'\n", encoding="utf-8")
    monkeypatch.setattr(runner, "_CONFIG_PATHS", [str(cfg)])
    assert runner._read_topas_config() == {"version": "6", "dir": "C:/Progrämme/TOPAS6"}
