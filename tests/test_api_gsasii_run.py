"""API tests that execute a GSAS-II refinement (``powderline.run()`` / ``GSASClient``).

The execution half of ``test_api.py`` (re/09 split): every test here runs the
LaB6 example through the gateway in-process or via the GSAS-II server fallback
and compares against the committed references, so the module is ``gsasii`` +
``slow``. The engine-free API tests stay in ``test_api.py``.
"""

import json
import uuid
from unittest.mock import patch
import pytest
import pandas as pd
from pathlib import Path

pytestmark = [pytest.mark.gsasii, pytest.mark.slow]

pytest.importorskip("GSASII.GSASIIscriptable")

import powderline  # noqa: E402
from powderline.gateways.gsasii.client import GSASClient  # noqa: E402
from subprocess_utils import unit_cell_match, extract_rwp_from_lst, REFINED_PARAM_COLUMNS  # noqa: E402


# ─── Constants (must match test_example_LaB6_regression.py tolerances) ────────

RWP_TOLERANCE = 0.1          # percent — same as CLI regression test
REPO = Path(__file__).resolve().parent.parent  # the repo, whatever the cwd (review item 17)
REFERENCE_DIR = REPO / "examples" / "example_LaB6" / "output"


# ─── Shared LaB6 subprocess run ───────────────────────────────────────────────

@pytest.fixture(scope="module")
def lab6_subprocess_run(tmp_path_factory):
    """Run the LaB6 subprocess refinement ONCE and share it across read-only tests.

    Returns ``(result, output_dir)``. Tests that only inspect a successful run's
    result dict / output files consume this instead of each launching their own
    (slow) subprocess refinement. Tests that need a fresh or second independent
    run (e.g. run_id uniqueness) still call powderline.run() themselves.
    """
    recipe = json.loads((REPO / "examples" / "example_LaB6" / "input.json").read_text(encoding="utf-8"))
    output_dir = tmp_path_factory.mktemp("lab6_shared_run")
    result = powderline.run(recipe, output_dir, execution_mode="subprocess")
    return result, output_dir


# ─── run() subprocess mode tests ──────────────────────────────────────────────


def test_run_result_has_run_id(lab6_subprocess_run):
    """run() result includes a valid UUID4 run_id for every actual refinement."""
    result, _ = lab6_subprocess_run
    assert result["success"] is True, f"Refinement failed: {result.get('error')}"
    assert "run_id" in result, "result dict must contain 'run_id'"
    # Raises ValueError if not a valid UUID4
    parsed = uuid.UUID(result["run_id"], version=4)
    assert str(parsed) == result["run_id"], "run_id must be a canonical UUID4 string"


def test_run_result_run_id_is_unique(recipe_LaB6_dict, tmp_path):
    """Each call to run() produces a distinct run_id."""
    result_a = powderline.run(
        recipe_LaB6_dict,
        tmp_path / "out_a",
        execution_mode="subprocess",
    )
    result_b = powderline.run(
        recipe_LaB6_dict,
        tmp_path / "out_b",
        execution_mode="subprocess",
    )
    assert result_a["success"] is True
    assert result_b["success"] is True
    assert result_a["run_id"] != result_b["run_id"], (
        "Two separate runs must produce different run_ids"
    )

def test_run_subprocess_succeeds(lab6_subprocess_run):
    """run(execution_mode='subprocess') completes successfully."""
    result, _ = lab6_subprocess_run
    assert result["success"] is True, f"Refinement failed: {result.get('error')}"
    assert result["method"] == "subprocess"
    assert result["elapsed_time"] > 0


def test_run_subprocess_matches_cli_rwp(lab6_subprocess_run):
    """run() subprocess Rwp matches the committed reference within tolerance."""
    result, _ = lab6_subprocess_run
    assert result["success"] is True, f"Refinement failed: {result.get('error')}"
    assert result.get("rwp") is not None, "No Rwp returned from subprocess run"

    ref_rwp = extract_rwp_from_lst(REFERENCE_DIR / "dummy.lst")
    assert abs(result["rwp"] - ref_rwp) < RWP_TOLERANCE, (
        f"API Rwp ({result['rwp']:.3f}%) differs from CLI reference "
        f"({ref_rwp:.3f}%) by more than {RWP_TOLERANCE}%"
    )


def test_run_subprocess_output_files_exist(lab6_subprocess_run):
    """run() subprocess creates all expected output files."""
    result, out = lab6_subprocess_run
    assert result["success"] is True

    for fname in ["dummy.gpx", "dummy.lst", "LaB6_unit_cell_report.csv",
                  "LaB6_peak_list_report.csv", "fit_profile.txt"]:
        assert (out / fname).exists(), f"Expected output file missing: {fname}"


def test_run_subprocess_unit_cell_matches_reference(lab6_subprocess_run):
    """run() subprocess unit_cell CSV matches the committed reference."""
    result, out = lab6_subprocess_run
    assert result["success"] is True

    test_df = pd.read_csv(out / "LaB6_unit_cell_report.csv")
    ref_df = pd.read_csv(REFERENCE_DIR / "LaB6_unit_cell_report.csv")
    assert unit_cell_match(test_df, ref_df), (
        f"Unit cell report mismatch:\nTest:\n{test_df}\nReference:\n{ref_df}"
    )


# ─── run() structured output tests (all modes return identical structure) ──────

def test_run_result_has_structured_data_keys(lab6_subprocess_run):
    """run() result dict always contains the expected structured-data keys."""
    result, _ = lab6_subprocess_run
    assert result["success"] is True
    for key in ("fit_profile", "unit_cell_data", "peak_list_data", "refined_parameters"):
        assert key in result, f"Expected key '{key}' missing from result dict"


def test_run_subprocess_structured_data_is_dataframes(lab6_subprocess_run):
    """run(execution_mode='subprocess') returns non-empty DataFrames in result."""
    result, _ = lab6_subprocess_run
    assert result["success"] is True

    assert isinstance(result["fit_profile"], pd.DataFrame)
    assert not result["fit_profile"].empty, "fit_profile DataFrame must not be empty"

    assert isinstance(result["unit_cell_data"], dict)
    assert len(result["unit_cell_data"]) > 0, "unit_cell_data dict must not be empty"
    for phase, df in result["unit_cell_data"].items():
        assert isinstance(df, pd.DataFrame), f"unit_cell_data['{phase}'] must be a DataFrame"
        assert not df.empty

    assert isinstance(result["peak_list_data"], dict)
    assert len(result["peak_list_data"]) > 0, "peak_list_data dict must not be empty"
    for phase, df in result["peak_list_data"].items():
        assert isinstance(df, pd.DataFrame), f"peak_list_data['{phase}'] must be a DataFrame"
        assert not df.empty

    assert isinstance(result["refined_parameters"], pd.DataFrame)
    assert not result["refined_parameters"].empty, "refined_parameters DataFrame must not be empty"


def test_run_refined_parameters_has_nine_columns(lab6_subprocess_run):
    """refined_parameters DataFrame contains all 9 expected columns in all modes."""
    expected_cols = set(REFINED_PARAM_COLUMNS)
    result, _ = lab6_subprocess_run
    assert result["success"] is True
    actual_cols = set(result["refined_parameters"].columns)
    assert expected_cols == actual_cols, (
        f"refined_parameters columns mismatch.\nExpected: {sorted(expected_cols)}\nGot: {sorted(actual_cols)}"
    )


def test_run_auto_fallback_returns_full_structured_data(recipe_LaB6_dict, tmp_path):
    """GSASClient fallback (server down) returns full structured DataFrames identical to subprocess mode."""
    client = GSASClient(fallback_to_subprocess=True)
    result = client.submit_simulation(
        recipe=recipe_LaB6_dict,
        output_dir=tmp_path / "out",
        auto_start_server=False,  # force fallback path
    )
    # After run() DataFrame conversion the result should have the same structure
    # as any other mode — convert manually since submit_simulation() bypasses run()
    result["fit_profile"] = pd.DataFrame(result.get("fit_profile") or {})
    result["unit_cell_data"] = {
        k: pd.DataFrame(v) for k, v in result.get("unit_cell_data", {}).items()
    }
    result["peak_list_data"] = {
        k: pd.DataFrame(v) for k, v in result.get("peak_list_data", {}).items()
    }
    refined_raw = result.get("refined_parameters")
    result["refined_parameters"] = (
        pd.DataFrame(refined_raw) if refined_raw
        else pd.DataFrame(columns=list(REFINED_PARAM_COLUMNS))
    )

    assert result["success"] is True
    assert result.get("rwp") is not None, "rwp must be present in fallback result"
    assert isinstance(result["fit_profile"], pd.DataFrame)
    assert not result["fit_profile"].empty
    assert isinstance(result["unit_cell_data"], dict) and len(result["unit_cell_data"]) > 0
    assert isinstance(result["refined_parameters"], pd.DataFrame)
    assert not result["refined_parameters"].empty


# ─── GSASClient direct dict/model input tests ─────────────────────────────────

def test_gsasclient_accepts_dict(recipe_LaB6_dict, tmp_path):
    """GSASClient.submit_simulation() accepts a plain dict (no file required)."""
    client = GSASClient(fallback_to_subprocess=True)
    result = client.submit_simulation(
        recipe=recipe_LaB6_dict,
        output_dir=tmp_path / "out",
        auto_start_server=False,  # force subprocess for determinism in CI
    )
    assert result["success"] is True, f"GSASClient dict submission failed: {result.get('error')}"


def test_gsasclient_accepts_recipemodel(recipe_LaB6_model, tmp_path):
    """GSASClient.submit_simulation() accepts a validated model instance."""
    client = GSASClient(fallback_to_subprocess=True)
    result = client.submit_simulation(
        recipe=recipe_LaB6_model,
        output_dir=tmp_path / "out",
        auto_start_server=False,
    )
    assert result["success"] is True, f"GSASClient model submission failed: {result.get('error')}"


def test_gsasclient_accepts_path(tmp_path):
    """GSASClient.submit_simulation() still accepts a Path (backward compat)."""
    client = GSASClient(fallback_to_subprocess=True)
    result = client.submit_simulation(
        recipe=REPO / "examples" / "example_LaB6" / "input.json",
        output_dir=tmp_path / "out",
        auto_start_server=False,
    )
    assert result["success"] is True, f"GSASClient path submission failed: {result.get('error')}"


# ─── error key contract tests ──────────────────────────────────────────────────

def test_ok_result_has_status_and_alias_but_no_error_key(lab6_subprocess_run):
    """re/12 (review focus 5): an ok result carries status/converged/envelope_version and the deprecated
    success alias; the pre-0.2.0 error/traceback keys are gone (a failure raises instead)."""
    result, _ = lab6_subprocess_run
    assert result["status"] == "ok" and result["success"] is True and result["envelope_version"] == "1"
    assert result["converged"] in (True, False)
    assert "error" not in result and "traceback" not in result


def test_run_failure_result_normalization(recipe_LaB6_dict, tmp_path):
    """run() correctly normalises an early-exit failure from run_refinement().

    When run_refinement() returns an early-exit dict (e.g. histogram load fails)
    with only a subset of keys, run() must not raise a KeyError and must still
    produce all expected output keys as empty DataFrames.
    """
    minimal_failure = {
        "success": False,
        "run_id": "test-run-id",
        "error": "Simulated histogram load failure",
        "elapsed_time": 0.5,
        "method": "subprocess",
    }

    with patch("powderline.gateways.gsasii.gateway.run_refinement", return_value=minimal_failure):
        result = powderline.run(
            recipe_LaB6_dict,
            tmp_path / "out",
            execution_mode="subprocess",
        )

    assert result["success"] is False
    assert result["error"] == "Simulated histogram load failure"

    # All DataFrame fields must be present and be valid (empty) DataFrames
    assert isinstance(result["fit_profile"], pd.DataFrame)
    assert isinstance(result["unit_cell_data"], dict)
    assert isinstance(result["peak_list_data"], dict)
    assert isinstance(result["refined_parameters"], pd.DataFrame)
    assert isinstance(result["spf_peaks"], pd.DataFrame)
    assert isinstance(result["spf_convergence_diagnostics"], pd.DataFrame)

    # refined_parameters empty DataFrame must preserve the 9-column schema
    expected_cols = set(REFINED_PARAM_COLUMNS)
    assert set(result["refined_parameters"].columns) == expected_cols


# ─── execution_mode='auto' fallback tests ──────────────────────────────────────

def test_run_auto_mode_falls_back_to_subprocess_when_server_unavailable(
    recipe_LaB6_dict, tmp_path
):
    """run(execution_mode='auto') falls back to subprocess when server cannot be started."""
    from powderline.gateways.gsasii import client as _gc

    with patch.object(_gc.GSASClient, "is_server_available", return_value=False), \
         patch.object(_gc.GSASClient, "_start_server_background", return_value=False):
        result = powderline.run(
            recipe_LaB6_dict,
            tmp_path / "out",
            execution_mode="auto",
        )

    assert result["success"] is True, f"Refinement failed: {result.get('error')}"
    assert result["method"] == "subprocess", (
        f"Expected 'subprocess' fallback but got method='{result['method']}'"
    )


# ─── execution_mode='server' failure normalization ────────────────────────────

def test_run_server_mode_failure_normalizes_result(recipe_LaB6_dict, tmp_path):
    """run(execution_mode='server') failure returns all DataFrame keys as empty DataFrames.

    When the server is unavailable and fallback is disabled, the error result
    must still pass through DataFrame normalization so callers can safely
    access result['fit_profile'], result['refined_parameters'], etc.
    """
    from powderline.gateways.gsasii import client as _gc

    with patch.object(_gc.GSASClient, "is_server_available", return_value=False), \
         patch.object(_gc.GSASClient, "_start_server_background", return_value=False):
        result = powderline.run(
            recipe_LaB6_dict,
            tmp_path / "out",
            execution_mode="server",
        )

    assert result["success"] is False
    assert result["error"] is not None

    # All DataFrame fields must be present and be valid (empty) DataFrames
    assert isinstance(result["fit_profile"], pd.DataFrame)
    assert isinstance(result["unit_cell_data"], dict)
    assert isinstance(result["peak_list_data"], dict)
    assert isinstance(result["refined_parameters"], pd.DataFrame)
    assert isinstance(result["spf_peaks"], pd.DataFrame)
    assert isinstance(result["spf_convergence_diagnostics"], pd.DataFrame)

    # refined_parameters empty DataFrame must preserve the 9-column schema
    expected_cols = set(REFINED_PARAM_COLUMNS)
    assert set(result["refined_parameters"].columns) == expected_cols


# ─── SPF Rwp via programmatic API ────────────────────────────────────────────

def test_run_spf_returns_rwp_directly(recipe_LaB6_singlepeakfit_dict, tmp_path):
    """run() for gsasii.spf returns Rwp in the result dict (not only in stdout).

    This closes the gap where the CLI regression test could not assert Rwp when
    running via the server (stdout is captured server-side). The API always
    returns result['rwp'] regardless of execution backend.
    """
    result = powderline.run(
        recipe_LaB6_singlepeakfit_dict,
        tmp_path / "out",
        execution_mode="subprocess",
    )
    assert result["success"] is True, f"SPF refinement failed: {result.get('error')}"
    assert result.get("rwp") is not None, (
        "gsasii.spf run must return result['rwp'] from the programmatic API"
    )
    # Observed Rwp 1.805% on the pinned GSAS-II (v5.7.9); band is the same
    # ~±11% convention the original DRX-based test used ([40, 50] around 45.26),
    # absorbing cross-build/BLAS drift without accepting a wrong result.
    assert 1.6 < result["rwp"] < 2.0, (
        f"SPF Rwp ({result['rwp']:.3f}%) outside expected range [1.6, 2.0]"
    )


# ─── POWDERLINE_NO_SERVER (deployment off switch, v0.1.2) ─────────────────────

def _no_server_paths():
    """Patches that fail the test if any server path is touched."""
    from powderline.gateways.gsasii import client as _gc

    def _fail(*a, **k):
        raise AssertionError("server path used despite POWDERLINE_NO_SERVER")

    return (patch.object(_gc.GSASClient, "is_server_available", side_effect=_fail),
            patch.object(_gc.GSASClient, "_start_server_background", side_effect=_fail),
            patch.object(_gc.GSASClient, "_submit_to_server", side_effect=_fail))


def test_run_auto_mode_with_no_server_env_runs_in_process(
    recipe_LaB6_dict, tmp_path, monkeypatch
):
    """POWDERLINE_NO_SERVER: run(execution_mode='auto') never probes or starts a server."""
    monkeypatch.setenv("POWDERLINE_NO_SERVER", "1")
    probe, start, submit = _no_server_paths()
    with probe, start, submit:
        result = powderline.run(recipe_LaB6_dict, tmp_path / "out", execution_mode="auto")

    assert result["success"] is True, f"Refinement failed: {result.get('error')}"
    assert result["method"] == "subprocess"


def test_run_server_mode_with_no_server_env_errors(recipe_LaB6_dict, tmp_path, monkeypatch):
    """POWDERLINE_NO_SERVER: run(execution_mode='server') returns a clear error result."""
    monkeypatch.setenv("POWDERLINE_NO_SERVER", "true")
    probe, start, submit = _no_server_paths()
    with probe, start, submit:
        result = powderline.run(recipe_LaB6_dict, tmp_path / "out", execution_mode="server")

    assert result["success"] is False
    assert "POWDERLINE_NO_SERVER" in result["error"]
    assert isinstance(result["fit_profile"], pd.DataFrame)
