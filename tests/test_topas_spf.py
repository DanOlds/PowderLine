"""Tests for the single-peak-fitting (GSASII_SPF) TOPAS path."""

from __future__ import annotations

import json
import math
import shutil
from pathlib import Path

import pandas as pd
import pytest

import powderline
from powderline.gateways.topas import conversions as cv
from powderline.gateways.topas import engine as te
from powderline.gateways.topas import roundtrip as rt
from powderline.gateways.topas.writer import render_topas
from powderline.gateways.topas import runner as tc_runner
from powderline.gateways.topas.errors import TopasTranslationError

REPO = Path(__file__).resolve().parent.parent
EXAMPLES = REPO / "examples"
GOLDEN = REPO / "tests" / "data" / "topas"
SPF = "example_LaB6_singlepeakfit"


def _recipe(name=SPF):
    return json.loads((EXAMPLES / name / "input.json").read_text())


# --- conversions ------------------------------------------------------------


def test_spf_gauss_eq_takes_sqrt_of_variance():
    assert cv.spf_gauss_fwhm_eq("spfpk0_sigsq") == "0.0235482 Sqrt(spfpk0_sigsq)"


def test_peak_widths_math():
    fg, fl, fpv, ibg, ibl, ibpv = cv.peak_widths(0.1, 0.05)
    assert fg == pytest.approx(2.0 * math.sqrt(2 * math.log(2)) * 0.1)
    assert fl == pytest.approx(0.1)  # 2 * HWHM
    assert fg < fpv and fl < fpv  # pV FWHM exceeds each component
    assert ibg == pytest.approx(fg * math.sqrt(math.pi / (4 * math.log(2))))


# --- writer -----------------------------------------------------------------


def test_spf_inp_has_peaks_and_no_str():
    inp = render_topas(_recipe(), SPF).inp_text
    assert inp == (GOLDEN / f"{SPF}.inp").read_text()  # golden match
    assert "\n    str\n" not in inp and "phase_name" not in inp  # no phases
    assert "xo spfpk0_pos 2.29" in inp
    assert "gauss_fwhm = 0.0235482 Sqrt(spfpk0_sigsq);" in inp  # variance -> sqrt
    assert "' Note: when a recipe refines multiple correlated profile" not in inp  # Rietveld-only note


def test_spf_use_instrument_profile_true_errors():
    recipe = _recipe()
    recipe["payload"]["refinement_controls"]["single_peak_fitting_mode"]["use_instrument_profile"] = True
    with pytest.raises(TopasTranslationError, match="use_instrument_profile"):
        render_topas(recipe, SPF)


# --- round-trip -------------------------------------------------------------


def test_spf_refined_parameters_excludes_peaks():
    # peak params go to spf_peaks; refined_parameters holds only the background.
    # Values are the maintainer's real round-6 TOPAS SPF run.
    res = rt.build_roundtrip(_recipe(), (GOLDEN / f"{SPF}_results.csv").read_text(), SPF)
    cats = {r["category"] for r in res.refined_parameters}
    assert cats == {"background"}
    assert res.fit["r_wp"] == pytest.approx(2.1032227)
    assert res.fit["gof"] == pytest.approx(0.17200188)


def test_spf_parse_spf_peaks_shape_and_widths():
    spf_rows, diag = rt.parse_spf_peaks(_recipe(), (GOLDEN / f"{SPF}_results.csv").read_text())
    assert len(spf_rows) == 36
    assert not diag  # all converged in the real fit
    row0 = spf_rows[0]
    assert list(rt.SPF_PEAK_COLUMNS) == list(row0.keys())
    assert row0["position_2theta"] == pytest.approx(2.29399, abs=1e-4)  # refined from 2.29
    assert row0["sigma"] == pytest.approx(math.sqrt(row0["sigma_squared"]))
    assert row0["converged"] is True


def test_spf_committed_reports_reproduce():
    """examples/<spf>/output/topas/single_peaks_report.csv is tracked + reproducible."""
    for ex in (SPF,):
        recipe = json.loads((EXAMPLES / ex / "input.json").read_text())
        results = (GOLDEN / f"{ex}_results.csv").read_text()
        spf_rows, _ = rt.parse_spf_peaks(recipe, results)
        committed = (EXAMPLES / ex / "output" / "topas" / "single_peaks_report.csv").read_text()
        assert rt.spf_peaks_report_csv(spf_rows) == committed


def test_spf_peaks_report_csv_header():
    rows, _ = rt.parse_spf_peaks(_recipe(), (GOLDEN / f"{SPF}_results.csv").read_text())
    header = rt.spf_peaks_report_csv(rows).splitlines()[0]
    assert header.startswith("position_2theta,intensity,sigma,sigma_squared,gamma,fwhm_gaussian")
    assert header.endswith("converged,convergence_detail")


def test_spf_negative_gamma_flags_diagnostic():
    results = "parameter,value,esd\nr_wp,10,\nspfpk0_gam,-1.0,0.1\n"
    rows, diag = rt.parse_spf_peaks(_recipe(), results)
    assert rows[0]["convergence_detail"] == "negative_gamma_warning"
    assert rows[0]["converged"] is False
    assert diag[0]["peak_index"] == 0


# --- engine (run(engine="topas")) -------------------------------------------


def test_spf_run_returns_spf_peaks(tmp_path, monkeypatch):
    results = (GOLDEN / f"{SPF}_results.csv").read_text()

    def fake_run(inp_path, topas_dir=None, topas_version=None):
        Path(inp_path).with_name(f"{Path(inp_path).stem}_results.csv").write_text(results)
        return tc_runner.RunResult("ran", 0, Path("/fake/tc.exe"), ["tc"])

    monkeypatch.setattr(te, "run_topas", fake_run)
    out = tmp_path / SPF / "output" / "topas"  # -> base_name SPF
    result = powderline.run(_recipe(), str(out), engine="topas")
    assert result["success"] is True and result["method"] == "topas"
    assert result["rwp"] == pytest.approx(2.1032227)
    # SPF peaks populated; Rietveld tables empty
    assert isinstance(result["spf_peaks"], pd.DataFrame)
    assert len(result["spf_peaks"]) == 36
    assert list(result["spf_peaks"].columns) == list(rt.SPF_PEAK_COLUMNS)
    assert result["unit_cell_data"] == {} and result["peak_list_data"] == {}
    assert (out / "single_peaks_report.csv").exists()
