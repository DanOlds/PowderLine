"""End-to-end: LaB6 example recipe refined by the easydiffraction engine.

Spike-verified expectations: Rwp ~9.6% (TCH/FCJ asymmetry from SH/L; residual gap
vs GSAS-II is background peaks + profile details), cell a -> 4.1575 +/- 0.0001 vs NIST 4.15682.
"""
import json
from pathlib import Path

import pytest

ed = pytest.importorskip("easydiffraction")

from powderline.gateways.easydiffraction.errors import EasyDiffractionTranslationError
from powderline.engine import run

EXAMPLE = Path("examples/example_LaB6_easydiff/input.json")
STOCK = Path("examples/example_LaB6/input.json")


@pytest.fixture(scope="module")
def lab6_result(tmp_path_factory):
    recipe = json.loads(EXAMPLE.read_text())
    out = tmp_path_factory.mktemp("easydiff_lab6")
    return run(recipe, out, engine="easydiffraction"), out


def test_refinement_succeeds_with_sane_rwp(lab6_result):
    result, _ = lab6_result
    assert result["success"] is True, result["error"]
    assert result["method"] == "easydiffraction"
    assert 5.0 < result["rwp"] < 15.0          # percent; TCH spike: ~9.6
    assert result["gof"] is not None and result["gof"] < 10.0


def test_lattice_parameter_close_to_nist(lab6_result):
    result, _ = lab6_result
    df = result["refined_parameters"]
    row = df[df["parameter_name"] == "0::a"].iloc[0]
    assert row["value"] == pytest.approx(4.15682, abs=0.002)   # NIST SRM 660
    assert row["esd"] > 0


def test_report_files_written(lab6_result):
    result, out = lab6_result
    for name in ("refined_parameters.csv", "fit_profile.txt",
                 "LaB6_unit_cell_report.csv", "LaB6_peak_list_report.csv"):
        assert (out / name).exists(), name
    assert len(result["fit_profile"]) == 4096  # full original grid, padded


def test_stock_lab6_recipe_rejected_loudly(tmp_path):
    # The stock recipe refines Z and background peaks -> per the honesty rule
    # this engine must refuse, not silently drop them.
    recipe = json.loads(STOCK.read_text())
    with pytest.raises(EasyDiffractionTranslationError):
        run(recipe, tmp_path, engine="easydiffraction")
