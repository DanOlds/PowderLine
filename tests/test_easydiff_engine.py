"""Result-contract tests for the easydiffraction engine adapter."""
import os

import numpy as np
import pandas as pd
import pytest

ed = pytest.importorskip("easydiffraction")

from powderline.gateways.easydiffraction.engine import run_easydiffraction_recipe

from test_easydiff_builder import rich_recipe
from test_easydiff_policy import base_recipe

RESULT_KEYS = {
    "success", "run_id", "rwp", "r_exp", "gof", "elapsed_time", "method",
    "output_files", "fit_profile", "unit_cell_data", "peak_list_data",
    "refined_parameters", "spf_peaks", "spf_convergence_diagnostics",
    "error", "traceback",
}
REFINED_COLUMNS = ["parameter_name", "descriptive_name", "phase_name", "phase_idx",
                   "atom_name", "atom_idx", "value", "esd", "category"]
PROFILE_COLUMNS = ["two_theta", "y_obs", "y_weights", "y_calc", "y_diff", "y_bkg",
                   "q_values", "d_spacings"]


def test_validate_only_slim_shape(tmp_path):
    result = run_easydiffraction_recipe(base_recipe(), tmp_path, validate_only=True)
    assert result["success"] is True
    assert result["method"] == "validate_only"
    assert result["rwp"] is None
    assert "run_id" not in result
    assert result["phases"] == 1
    assert result["simulation_mode"] is False  # cycles = 5


def test_synthetic_fit_result_contract(tmp_path):
    result = run_easydiffraction_recipe(rich_recipe(), tmp_path)
    assert set(result) == RESULT_KEYS
    assert result["success"] is True, result["error"]
    assert result["method"] == "easydiffraction"
    assert result["rwp"] is not None and 0 < result["rwp"] < 100  # percent
    assert list(result["refined_parameters"].columns) == REFINED_COLUMNS
    assert len(result["refined_parameters"]) == 5  # scale, 2 cheb, U, cell a
    assert list(result["fit_profile"].columns) == PROFILE_COLUMNS
    assert len(result["fit_profile"]) == 200  # full grid
    assert "LaB6" in result["unit_cell_data"]
    uc = result["unit_cell_data"]["LaB6"]
    # GSAS-II/TOPAS parity: unit-cell reports carry a cell_volume row
    vol = uc.loc[uc["parameter"] == "cell_volume", "value"]
    assert not vol.empty
    assert float(vol.iloc[0]) == pytest.approx(4.157 ** 3, rel=0.02)  # cubic V=a^3
    assert isinstance(result["spf_peaks"], pd.DataFrame) and result["spf_peaks"].empty
    assert result["error"] is None
    out_names = {os.path.basename(p) for p in result["output_files"]}
    assert {"refined_parameters.csv", "fit_profile.txt",
            "LaB6_unit_cell_report.csv"} <= out_names


def test_simulation_mode_when_nothing_refined(tmp_path):
    r = rich_recipe()
    pz = r["payload"]["phases"]["LaB6"]["parameterization"]
    pz["scale"] = [1, False, None, None]
    pz["unit_cell"]["a"] = [None, False, None, None]
    r["payload"]["instrument"]["parameterization"]["broadening"]["U"] = [None, False, None, None]
    r["payload"]["background"]["chebyshev"]["refine_flag"] = False
    result = run_easydiffraction_recipe(r, tmp_path)
    assert result["success"] is True
    assert result["method"] == "easydiffraction_simulation"
    assert result["rwp"] is None
    assert result["refined_parameters"].empty
    assert list(result["refined_parameters"].columns) == REFINED_COLUMNS
    assert (result["fit_profile"]["y_calc"] != 0).any()  # pattern actually calculated


def test_unique_run_ids(tmp_path):
    import uuid
    r1 = run_easydiffraction_recipe(rich_recipe(), tmp_path / "a")
    r2 = run_easydiffraction_recipe(rich_recipe(), tmp_path / "b")
    assert uuid.UUID(r1["run_id"]) != uuid.UUID(r2["run_id"])


def test_output_files_cross_platform(tmp_path):
    """output_files uses Path().name for cross-platform compatibility."""
    from pathlib import Path
    result = run_easydiffraction_recipe(rich_recipe(), tmp_path)
    assert result["success"] is True
    # Extract just filenames to verify no "/" in names
    for path_str in result["output_files"]:
        filename = Path(path_str).name
        assert "/" not in filename
        assert "\\" not in filename


def test_post_build_fit_failure(tmp_path, monkeypatch):
    """Fit failure after build returns success=False with error/traceback."""
    from unittest.mock import MagicMock
    import powderline.gateways.easydiffraction.builder as builder_module

    original_build = builder_module.build_project

    def mock_build(recipe, workdir):
        br = original_build(recipe, workdir)
        # Store original fit method
        original_fit = br.project.analysis.fit

        def failing_fit():
            raise RuntimeError("Simulated fit failure for testing")

        # Bypass easydiffraction's guards using object.__setattr__
        object.__setattr__(br.project.analysis, 'fit', failing_fit)
        return br

    monkeypatch.setattr(builder_module, "build_project", mock_build)

    result = run_easydiffraction_recipe(rich_recipe(), tmp_path)

    # Should have all 17 keys
    assert set(result) == RESULT_KEYS
    assert result["success"] is False
    assert result["method"] == "easydiffraction"
    assert isinstance(result["refined_parameters"], pd.DataFrame)
    assert isinstance(result["fit_profile"], pd.DataFrame)
    assert isinstance(result["error"], str) and len(result["error"]) > 0
    assert "Simulated fit failure" in result["error"]
    assert isinstance(result["traceback"], str) and len(result["traceback"]) > 0
    assert "RuntimeError" in result["traceback"]
