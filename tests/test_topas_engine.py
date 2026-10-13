"""Tests for the engine dispatcher + GSAS-II-free TOPAS run() adapter.

The 0.26.0 recipes reach the TOPAS translation path only through the deprecated
``engine="topas"`` keyword (a DeprecationWarning each call), until that path is deleted.
"""


from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pandas as pd
import pytest

import powderline
from powderline import engine as dispatcher
from powderline.gateways.topas import engine as te
from powderline.gateways.topas import runner as tc_runner
from powderline.gateways.topas.errors import TopasTranslationError
from subprocess_utils import run_subprocess_utf8

REPO = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.filterwarnings("ignore:powderline.run\\(engine=:DeprecationWarning")
EXAMPLES = REPO / "tests" / "data" / "legacy_026"  # 0.26.0 snapshots of the examples (re/07, P-16)
LEGACY_TOPAS_OUT = REPO / "examples"  # held the TOPAS 7.25 goldens (examples/*/output/topas/) until re/07, P-02
_LEGACY_GOLDENS_GONE = pytest.mark.skip(reason="reads the TOPAS 7.25 goldens that examples/*/output/topas/ carried; removed at re/07 (P-02), the test goes with the legacy TOPAS path")

# run() dict keys locked by tests/test_api.py (must match across engines)
_STRUCTURED = ("fit_profile", "unit_cell_data", "peak_list_data", "refined_parameters")
_REFINED_9 = {
    "parameter_name", "descriptive_name", "phase_name", "phase_idx",
    "atom_name", "atom_idx", "value", "esd", "category",
}


def _recipe(name="example_LaB6"):
    return json.loads((EXAMPLES / name / "input.json").read_text())


def _mock_topas_run(name):
    """A fake runner that drops the committed TOPAS outputs into the run dir,
    renamed to the INP's <base> (which run_topas_recipe derives from output_dir)."""
    committed = LEGACY_TOPAS_OUT / name / "output" / "topas"

    def fake_run(inp_path, topas_dir=None, topas_version=None):
        base = Path(inp_path).stem
        parent = Path(inp_path).parent
        shutil.copy(committed / f"{name}_results.csv", parent / f"{base}_results.csv")
        shutil.copy(committed / f"{name}_topas_profile.txt", parent / f"{base}_topas_profile.txt")
        for peaks in committed.glob("*_topas_peaks.txt"):
            shutil.copy(peaks, parent / peaks.name)
        return tc_runner.RunResult("ran", 0, Path("/fake/tc.exe"), ["tc"])

    return fake_run


# --- dispatcher -------------------------------------------------------------


def test_run_is_the_dispatcher():
    assert powderline.run.__module__ == "powderline.api"
    assert dispatcher.run is powderline.run  # the deprecated import path is the same function


def test_topas_engine_runs_without_gsasii(tmp_path):
    # The TOPAS engine path must run with GSAS-II *unavailable* (Windows scenario).
    # A subprocess blocks the GSASII import so this holds even when GSAS-II is
    # installed in the dev env (Linux regression dependency).
    import textwrap

    recipe_path = EXAMPLES / "example_LaB6" / "input.json"
    output_dir = tmp_path / "topas_no_gsasii_guard"
    script = textwrap.dedent(
        f"""
        import sys, importlib.abc, json

        class _Block(importlib.abc.MetaPathFinder):
            def find_spec(self, name, path, target=None):
                if name == "GSASII" or name.startswith("GSASII."):
                    raise ImportError("GSASII blocked for the TOPAS-engine guard test")
                return None

        sys.meta_path.insert(0, _Block())
        import powderline
        recipe = json.loads(open({str(recipe_path)!r}).read())
        # tc.exe absent -> generate-only; still must not touch GSAS-II
        powderline.run(recipe, {str(output_dir)!r}, engine="topas")
        assert "GSASII" not in sys.modules, "TOPAS engine pulled GSAS-II into sys.modules"
        print("OK")
        """
    )
    proc = run_subprocess_utf8([sys.executable, "-c", script], capture_output=True, text=True)
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert "OK" in proc.stdout


def test_unknown_engine_raises():
    with pytest.warns(DeprecationWarning), pytest.raises(ValueError, match="unknown engine"):
        dispatcher.run(_recipe(), "/tmp/x", engine="bogus")


# --- TOPAS engine: full run() dict shape ------------------------------------


@_LEGACY_GOLDENS_GONE
def test_topas_run_success_returns_locked_shape(tmp_path, monkeypatch):
    monkeypatch.setattr(te, "run_topas", _mock_topas_run("example_LaB6"))
    out = tmp_path / "example_LaB6" / "output" / "topas"  # -> base_name "example_LaB6"
    result = powderline.run(_recipe(), str(out), engine="topas")

    assert result["success"] is True
    assert result["method"] == "topas"
    assert result["run_id"]
    # fit stats returned (maintainer request)
    assert result["rwp"] == pytest.approx(8.519116)
    assert result["r_exp"] == pytest.approx(12.45063)
    assert result["gof"] == pytest.approx(0.68423175)
    # structured types match the GSAS-II contract
    assert isinstance(result["refined_parameters"], pd.DataFrame)
    assert set(result["refined_parameters"].columns) == _REFINED_9
    assert len(result["refined_parameters"]) == 17
    assert isinstance(result["fit_profile"], pd.DataFrame) and not result["fit_profile"].empty
    assert isinstance(result["unit_cell_data"]["LaB6"], pd.DataFrame)
    assert isinstance(result["peak_list_data"]["LaB6"], pd.DataFrame)
    assert result["spf_peaks"].empty and result["spf_convergence_diagnostics"].empty
    assert result["error"] is None
    # bare GSAS-II-matching filenames written
    names = {Path(f).name for f in result["output_files"]}
    assert {"refined_parameters.csv", "fit_profile.txt", "LaB6_unit_cell_report.csv",
            "LaB6_peak_list_report.csv"} <= names
    # (GSAS-II-free guarantee is covered by test_topas_engine_runs_without_gsasii,
    # which blocks the import in a subprocess; an inline sys.modules check here is
    # unreliable now that GSAS-II is a Linux regression dependency.)


def test_topas_run_tc_absent_generate_only(tmp_path, monkeypatch):
    monkeypatch.setattr(
        te, "run_topas", lambda *a, **k: tc_runner.RunResult("tc_not_found", None, None, [])
    )
    result = powderline.run(_recipe(), str(tmp_path / "out"), engine="topas")
    assert result["success"] is False
    assert result["method"] == "topas_generate_only"
    assert "not found" in result["error"]
    # structured keys still present + normalised to empty DataFrames
    for key in _STRUCTURED:
        val = result[key]
        assert isinstance(val, (pd.DataFrame, dict))
    assert set(result["refined_parameters"].columns) == _REFINED_9
    # the INP + xye were still written
    assert any(Path(f).suffix in (".inp", ".xye") for f in result["output_files"])


def _mock_simulation_run(name):
    """Fake runner for a simulation: drops the committed profile + peaks but NO
    _results.csv (a calculate-only TOPAS run produces none)."""
    committed = LEGACY_TOPAS_OUT / name / "output" / "topas"

    def fake_run(inp_path, topas_dir=None, topas_version=None):
        base = Path(inp_path).stem
        parent = Path(inp_path).parent
        shutil.copy(committed / f"{name}_topas_profile.txt", parent / f"{base}_topas_profile.txt")
        for pk in committed.glob("*_topas_peaks.txt"):
            shutil.copy(pk, parent / pk.name)
        return tc_runner.RunResult("ran", 0, Path("/fake/tc.exe"), ["tc"])

    return fake_run


@_LEGACY_GOLDENS_GONE
def test_topas_run_simulation_no_results_csv_is_success(tmp_path, monkeypatch):
    name = "example_DRX_33_simulation"
    monkeypatch.setattr(te, "run_topas", _mock_simulation_run(name))
    recipe = _recipe(name)
    out = tmp_path / name / "output" / "topas"
    result = powderline.run(recipe, str(out), engine="topas")

    # calculate-only success: no _results.csv is expected, not a failure
    assert result["success"] is True
    assert result["method"] == "topas_simulation"
    assert result["error"] is None
    # no meaningful fit stats or refined params for a simulation
    assert result["rwp"] is None and result["gof"] is None
    assert result["refined_parameters"].empty
    assert set(result["refined_parameters"].columns) == _REFINED_9
    # the calculated pattern + peak lists ARE produced
    assert not result["fit_profile"].empty
    assert set(result["peak_list_data"]) == {"DRX_33", "Li4MgWO6_SG12"}
    names = {Path(f).name for f in result["output_files"]}
    assert "fit_profile.txt" in names


def test_topas_run_failed_exit_code(tmp_path, monkeypatch):
    monkeypatch.setattr(
        te, "run_topas", lambda *a, **k: tc_runner.RunResult("failed", 3, Path("/fake/tc"), ["tc"])
    )
    result = powderline.run(_recipe(), str(tmp_path / "out"), engine="topas")
    assert result["success"] is False and result["method"] == "topas"
    assert "code 3" in result["error"]


# --- cross-engine key/type parity (Task 3) ----------------------------------

# The canonical GSAS-II success dict (kicker.run, after the DataFrame
# normalisation) -> the exact key -> Python/pandas type each must carry so a
# caller is engine-agnostic. r_exp/gof are additive TOPAS extras (not asserted
# here / not forbidden). This is a shape/type check only: GSAS-II cannot run on
# Windows, so a live cross-engine value comparison belongs on Linux (test_api.py
# already locks the GSAS-II side there).
_GSASII_CONTRACT = {
    "success": bool,
    "run_id": str,
    "rwp": (float, type(None)),
    "elapsed_time": float,
    "method": str,
    "output_files": list,
    "fit_profile": pd.DataFrame,
    "unit_cell_data": dict,
    "peak_list_data": dict,
    "refined_parameters": pd.DataFrame,
    "spf_peaks": pd.DataFrame,
    "spf_convergence_diagnostics": pd.DataFrame,
    "error": type(None),   # None on success
    "traceback": type(None),  # None on success
}


@pytest.mark.parametrize("name", ["example_LaB6", "example_LaB6_singlepeakfit"])
@_LEGACY_GOLDENS_GONE
def test_topas_result_matches_gsasii_key_and_type_contract(name, tmp_path, monkeypatch):
    """A TOPAS success dict carries every GSAS-II key with the matching type."""
    monkeypatch.setattr(te, "run_topas", _mock_topas_run(name))
    out = tmp_path / name / "output" / "topas"  # -> base_name == name
    result = powderline.run(_recipe(name), str(out), engine="topas")

    assert result["success"] is True
    for key, expected_type in _GSASII_CONTRACT.items():
        assert key in result, f"missing GSAS-II contract key {key!r}"
        assert isinstance(result[key], expected_type), (
            f"{key!r} is {type(result[key]).__name__}, expected "
            f"{getattr(expected_type, '__name__', expected_type)}"
        )
    # dict-of-DataFrame values carry DataFrames (GSAS-II per-phase contract)
    for phase_frame in result["unit_cell_data"].values():
        assert isinstance(phase_frame, pd.DataFrame)
    for phase_frame in result["peak_list_data"].values():
        assert isinstance(phase_frame, pd.DataFrame)
    # empty SPF frames match GSAS-II's column-less pd.DataFrame() (parity, not
    # SPF_PEAK_COLUMNS) for a Rietveld recipe
    if name == "example_LaB6":
        assert result["spf_peaks"].empty
        assert list(result["spf_peaks"].columns) == []
        assert list(result["spf_convergence_diagnostics"].columns) == []


# --- validate_only ----------------------------------------------------------


def test_topas_validate_only_shape():
    result = powderline.run(_recipe(), "/tmp/unused", engine="topas", validate_only=True)
    assert result["method"] == "validate_only"
    assert result["rwp"] is None
    assert result["schema_name"] == "GSASII_Rietveld"
    assert result["phases"] == 1
    assert "run_id" not in result  # matches the GSAS-II validate_only contract


def test_topas_validate_only_raises_on_unsupported(tmp_path):
    recipe = _recipe()
    # rhombohedral :R setting is genuinely untranslatable (permissive path only
    # covers symmetry-breaking refine flags, which now warn rather than raise)
    recipe["payload"]["phases"]["LaB6"]["structure"]["space_group"] = "R -3 m :R"
    with pytest.raises(TopasTranslationError):
        powderline.run(recipe, str(tmp_path), engine="topas", validate_only=True)
