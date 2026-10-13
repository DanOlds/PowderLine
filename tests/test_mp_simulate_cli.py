"""CLI-level tests for mp_simulate.main() (mocked — no live API, no GSAS-II).

Covers the argument wiring and error exits that the interface/builder unit
tests cannot see: --no-server forwarding into GSASClient.submit_simulation,
the targeted MPAuthError exit, and the empty-formula-search exit. MPInterface
and GSASClient are replaced in the mp_simulate module namespace; ConfigLoader
runs for real against an empty cwd with a dummy MP_API_KEY (also exercising
the env-var fallback end to end).
"""

from __future__ import annotations

import sys

import pytest

import powderline.gateways.gsasii.mp_simulate as mp_simulate
from powderline.mp_interface import MPAuthError

DUMMY_KEY = "0123456789abcdef0123456789abcdef"


@pytest.fixture
def install_fake_mp(monkeypatch):
    """Install a per-test MPInterface stand-in (no shared class state).

    Returns a function taking the data the fake should serve; each call
    builds a fresh class so nothing leaks between tests.
    """
    def _install(structure_data=None, search_results=None):
        class FakeMP:
            def __init__(self, api_key=None):
                self.api_key = api_key

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def get_structure(self, material_id):
                return structure_data

            def search_by_formula(self, formula):
                return search_results

        monkeypatch.setattr(mp_simulate, "MPInterface", FakeMP)

    return _install


@pytest.fixture
def cli_env(monkeypatch, tmp_path):
    """Isolated cwd (no config file) with the key provided via MP_API_KEY."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MP_API_KEY", DUMMY_KEY)
    # Point ~ at the tmp dir so a real ~/.powderline_config.yaml stays out of play
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    return tmp_path


def _run_main(monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", ["mp_simulate"] + argv)
    mp_simulate.main()


class TestNoServerForwarding:

    @pytest.mark.parametrize("extra_argv,expected", [
        (["--no-server"], False),
        ([], True),
    ])
    def test_no_server_flag_forwarded_to_client(
            self, monkeypatch, cli_env, install_fake_mp, mp_lab6_structure_data,
            extra_argv, expected):
        """--no-server must arrive at submit_simulation as
        use_server=False/auto_start_server=False (and default to True)."""
        install_fake_mp(structure_data=mp_lab6_structure_data)

        captured = {}

        class FakeClient:
            def submit_simulation(self, **kwargs):
                captured.update(kwargs)
                # Fail the run: forwarding is asserted from the captured
                # kwargs; no need to satisfy the export step
                return {"success": False, "error": "stub"}

        monkeypatch.setattr(mp_simulate, "GSASClient", FakeClient)

        with pytest.raises(SystemExit):
            _run_main(monkeypatch, ["--material-id", "mp-2680"] + extra_argv)

        assert captured["use_server"] is expected
        assert captured["auto_start_server"] is expected


class TestErrorExits:

    def test_auth_error_exits_with_setup_hint(self, monkeypatch, cli_env, capsys):
        class AuthFailMP:
            def __init__(self, api_key=None):
                raise MPAuthError("authentication rejected")

        monkeypatch.setattr(mp_simulate, "MPInterface", AuthFailMP)

        with pytest.raises(SystemExit) as excinfo:
            _run_main(monkeypatch, ["--material-id", "mp-2680"])

        assert excinfo.value.code == 1
        out = capsys.readouterr().out
        assert "authentication failed" in out
        assert "MP_API_KEY" in out

    def test_empty_formula_search_exits(self, monkeypatch, cli_env,
                                        install_fake_mp, capsys):
        install_fake_mp(search_results=[])

        with pytest.raises(SystemExit) as excinfo:
            _run_main(monkeypatch, ["--formula", "Xx9Zz"])

        assert excinfo.value.code == 1
        assert "No materials found" in capsys.readouterr().out

    def test_invalid_config_exits_cleanly(self, monkeypatch, cli_env,
                                          install_fake_mp,
                                          mp_lab6_structure_data, capsys):
        """A config with an unknown simulation_defaults key must exit with
        the validation message, not a traceback."""
        install_fake_mp(structure_data=mp_lab6_structure_data)
        (cli_env / ".powderline_config.yaml").write_text(
            "simulation_defaults:\n  sample:\n    crystallite_size: 5.0\n",
            encoding="utf-8",
        )

        with pytest.raises(SystemExit) as excinfo:
            _run_main(monkeypatch, ["--material-id", "mp-2680"])

        assert excinfo.value.code == 1
        out = capsys.readouterr().out
        assert "unknown key 'simulation_defaults.sample'" in out


class TestSuccessPath:

    def test_chi_exported_from_in_band_fit_profile(
            self, monkeypatch, cli_env, install_fake_mp,
            mp_lab6_structure_data, capsys):
        """On success the .chi must be built from result['fit_profile']
        (in-band), independent of any files in the output directory."""
        install_fake_mp(structure_data=mp_lab6_structure_data)

        class FakeClient:
            def submit_simulation(self, **kwargs):
                # Note: writes NO files — in-band data only
                return {
                    "success": True,
                    "elapsed_time": 0.1,
                    "method": "server",
                    "fit_profile": {
                        "two_theta": [10.0, 11.0, 12.0],
                        "y_obs": [1.0, 1.0, 1.0],
                        "y_calc": [5.0, 6.0, 7.0],
                        "residual": [0.0, 0.0, 0.0],
                    },
                }

        monkeypatch.setattr(mp_simulate, "GSASClient", FakeClient)
        out_dir = cli_env / "patterns"

        _run_main(monkeypatch, ["--material-id", "mp-2680",
                                "--output", str(out_dir)])

        chi = out_dir / "mp-2680_LaB6_simulated.chi"
        assert chi.exists()
        data_lines = [ln for ln in chi.read_text(encoding="utf-8").splitlines()
                      if not ln.startswith("#")]
        assert len(data_lines) == 3
        assert float(data_lines[0].split()[1]) == pytest.approx(5.0)
