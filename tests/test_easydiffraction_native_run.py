"""Native easydiffraction runner (re/06 Phase 4): strict reads, run warnings, statistics, reports, result dict.

Everything that can be pure is tested here without easydiffraction (fake
parameter objects, recorded-style arrays); the tests that run the engine are
skipped where it is not installed.
"""

from __future__ import annotations

import importlib.util
import math
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from powderline.exceptions import EngineExecutionError
from powderline.gateways.easydiffraction import native_run as nr
from powderline.gateways.easydiffraction.schema import RefinementControls
from test_easydiffraction_native_builder import P, _declare_schema_versions, _recipe, _validated  # noqa: F401

_HAS_ED = importlib.util.find_spec("easydiffraction") is not None
needs_ed = pytest.mark.skipif(not _HAS_ED, reason="easydiffraction not installed")


class _Param:
    """An easydiffraction parameter as the runner reads it: ``value``, ``uncertainty``, settable attributes."""

    def __init__(self, value, uncertainty=None, **attrs):
        self.value = value
        self.uncertainty = uncertainty
        for k, v in attrs.items():
            setattr(self, k, v)


def _entry(path, value, *, kind="cell", unit="angstrom", lo=None, hi=None, shift=0.0, final=None, esd=None):
    """A manifest entry (A185) whose live parameter holds ``final`` (default: the start)."""
    return {"path": path, "unit": unit, "kind": kind, "value": value, "min": lo, "max": hi, "shift": shift,
            "parameter": _Param(value if final is None else final, esd)}


def test_module_imports_no_engine():
    assert "easydiffraction" not in sys.modules or _HAS_ED


# ---------------------------------------------------------------------------
# 4.1 strict reads (A130, A154; N5)
# ---------------------------------------------------------------------------


def _arrays(n=5, **override):
    x = np.linspace(10.0, 14.0, n)
    arrays = {"x": x, "meas": np.full(n, 100.0), "meas_su": np.full(n, 10.0), "calc": np.full(n, 101.0),
              "bkg": np.full(n, 90.0), "diff": np.full(n, -1.0)}
    arrays.update(override)
    return arrays


def test_check_arrays_returns_the_handed_points_in_order():
    a = _arrays()
    x, y_obs, y_calc, y_bkg, sigma = nr.check_arrays(a, a["x"])
    assert x.tolist() == a["x"].tolist() and y_obs.tolist() == [100.0] * 5
    assert y_calc.tolist() == [101.0] * 5 and y_bkg.tolist() == [90.0] * 5 and sigma.tolist() == [10.0] * 5


@pytest.mark.parametrize("key, what", [("calc", "the calculated pattern"), ("bkg", "the background")])
def test_check_arrays_non_finite_is_the_divergence_error(key, what):
    a = _arrays()
    a[key] = a[key].copy()
    a[key][2] = np.nan
    with pytest.raises(EngineExecutionError, match=rf"refinement diverged: {what} is not finite \(A130\)"):
        nr.check_arrays(a, a["x"])


def test_check_arrays_requires_exactly_the_handed_points():
    a = _arrays()
    with pytest.raises(EngineExecutionError, match=r"holds 5 data point\(s\) but 6 were handed"):
        nr.check_arrays(a, np.linspace(10.0, 15.0, 6))
    with pytest.raises(EngineExecutionError, match="not read as written"):
        nr.check_arrays(a, a["x"] + 1e-3)
    with pytest.raises(EngineExecutionError, match="returned no bkg array"):
        nr.check_arrays(_arrays(bkg=None), a["x"])


def test_read_values_subtracts_the_translation_and_keeps_undetermined_esds_empty():
    manifest = {
        "structures.p.cell.length_a": _entry("payload.phases.P.unit_cell.a", 4.0, final=4.1, esd=0.01),
        "structures.p.atom_sites.B.fract_x": _entry("payload.phases.P.atoms.B.x", 0.863, kind="coordinate",
                                                     unit="fractional", shift=-1.0, final=-0.135, esd=None),
        "experiments.powderline.linked_structures.p.scale": _entry("payload.phases.P.scale", 1.0, kind="scale",
                                                                    final=0.9, esd=math.inf),
    }
    values = nr.read_values(manifest)
    assert values["structures.p.cell.length_a"] == (4.1, 0.01)
    assert values["structures.p.atom_sites.B.fract_x"][0] == pytest.approx(0.865)  # held -0.135, reported +1 (A176)
    assert values["structures.p.atom_sites.B.fract_x"][1] is None                   # "Could not estimate error-bars"
    assert values["experiments.powderline.linked_structures.p.scale"][1] is None    # a non-finite ESD is undetermined


def test_read_values_non_finite_value_is_the_divergence_error():
    manifest = {"structures.p.cell.length_a": _entry("payload.phases.P.unit_cell.a", 4.0, final=math.nan)}
    with pytest.raises(EngineExecutionError, match=r"the refined value of payload.phases.P.unit_cell.a is not finite"):
        nr.read_values(manifest)


def _refln(**override):
    cols = {"structure_id": np.array(["lab6", "si", "lab6"], dtype=object),
            "index_h": np.array([1.0, 1.0, 1.0]), "index_k": np.array([0.0, 1.0, 1.0]),
            "index_l": np.array([0.0, 1.0, 0.0]), "d_spacing": np.array([4.15, 3.13, 2.93]),
            "two_theta": np.array([21.3, 28.4, 30.4]), "f_squared_calc": np.array([1200.0, 800.0, 950.0])}
    cols.update(override)
    return SimpleNamespace(**cols)


def test_reflection_rows_filters_the_phase_and_types_the_indices():
    rows = nr.reflection_rows(_refln(), "lab6", "LaB6")
    assert [tuple(r[k] for k in ("h", "k", "l")) for r in rows] == [(1, 0, 0), (1, 1, 0)]
    assert all(isinstance(r["h"], int) for r in rows)
    assert rows[1]["F_calc_squared"] == 950.0 and rows[0]["2theta"] == 21.3
    assert list(rows[0]) == list(nr.PEAK_LIST_COLUMNS)


def test_reflection_rows_non_finite_is_the_divergence_error():
    with pytest.raises(EngineExecutionError, match=r"reflection list of phase 'LaB6' \(F_calc_squared\)"):
        nr.reflection_rows(_refln(f_squared_calc=np.array([1200.0, 800.0, np.nan])), "lab6", "LaB6")
    # the other phase's NaN does not concern this phase
    assert len(nr.reflection_rows(_refln(f_squared_calc=np.array([1200.0, np.nan, 950.0])), "lab6", "LaB6")) == 2


# ---------------------------------------------------------------------------
# 4.1 minimizer controls (A162, A190) and run warnings (A166, A171, A175, A165, A157 B7)
# ---------------------------------------------------------------------------


def _controls(**kw):
    return RefinementControls(calculator=kw.pop("calculator", "cryspy"), peak_type=kw.pop("peak_type", "pseudo-voigt"),
                              **kw)


def test_configure_minimizer_hands_max_iterations_and_only_exposed_tolerances():
    minimizer = SimpleNamespace(type="other", max_iterations=1000, ftol=1e-8)  # no xtol / gtol
    handed = nr.configure_minimizer(SimpleNamespace(minimizer=minimizer), _controls(max_iterations=7))
    assert minimizer.type == nr.MINIMIZER and minimizer.max_iterations == 7
    assert minimizer.ftol == 0.001 and not hasattr(minimizer, "xtol")
    assert handed == {"chi_square_change_tolerance": True, "parameter_change_tolerance": False,
                      "gradient_tolerance": False}


def test_configure_minimizer_refuses_an_unknown_attribute_map():
    with pytest.raises(AttributeError, match="has no attribute 'max_iterations'"):
        nr.configure_minimizer(SimpleNamespace(minimizer=SimpleNamespace(type="x")), _controls())


def test_stopping_criteria_warning_only_when_stated_values_differ_from_the_fixed_ones():
    none_handed = {k: False for k in nr.TOLERANCES}
    w = nr.stopping_criteria_warning(_controls(), none_handed)  # schema defaults 0.001 / 0.001 / 1e-6
    assert w["code"] == "easydiffraction_stopping_criteria_not_handed"
    assert w["field_path"] == "payload.refinement_controls"
    for name in nr.TOLERANCES:
        assert name in w["message"]
    assert "max_iterations was handed" in w["message"]
    fixed = _controls(chi_square_change_tolerance=1e-8, parameter_change_tolerance=1e-8, gradient_tolerance=1e-300)
    w2 = nr.stopping_criteria_warning(fixed, none_handed)
    assert w2 is not None and "gradient_tolerance" in w2["message"] and "chi_square_change_tolerance" not in w2["message"]
    assert nr.stopping_criteria_warning(_controls(), {k: True for k in nr.TOLERANCES}) is None


def _run_warnings(**kw):
    base = dict(simulation=False, calculator="cryspy", n_varied=3, converged=True, message="Fit succeeded.",
                widths={"u": 0.01, "v": -0.001, "w": 0.002, "x": 0.01, "y": 0.0}, grid=np.arange(9.0, 56.0, 0.01),
                reflections_available=True, contributes={"LaB6": True})
    base.update(kw)
    return [w["code"] for w in nr.run_warnings(**base)], nr.run_warnings(**base)


def test_converged_fit_with_cryspy_has_no_run_warnings():
    assert _run_warnings()[0] == []


def test_not_converged_is_a_warning_with_the_message_and_values_kept():
    codes, warnings = _run_warnings(converged=False, message="Number of function evaluations exceeded max_nfev")
    assert codes == ["easydiffraction_fit_not_converged"]
    assert "max_nfev" in warnings[0]["message"] and "values reached are reported" in warnings[0]["message"]


def test_simulation_never_warns_about_convergence_or_crysfml():
    codes, _ = _run_warnings(simulation=True, n_varied=0, converged=False, calculator="crysfml")
    assert codes == []


def test_every_crysfml_refinement_warns_start_dependent():
    codes, warnings = _run_warnings(calculator="crysfml")
    assert codes == ["easydiffraction_crysfml_start_dependent"]
    assert warnings[0]["field_path"] == "payload.refinement_controls.calculator" and "EB-90" in warnings[0]["message"]
    assert _run_warnings(calculator="crysfml", n_varied=0)[0] == []  # a calculation is not a refinement


def test_refined_negative_width_is_a_warning_naming_the_range():
    import math as m
    w = -0.001 * m.tan(m.radians(12.0 / 2))  # FWHM^2 = 0.001 (tan theta - tan 6 deg): negative below 12 deg
    codes, warnings = _run_warnings(widths={"u": 0.0, "v": 0.001, "w": w, "x": 0.01, "y": 0.0})
    assert codes == ["easydiffraction_negative_width"]
    assert "Gaussian FWHM^2" in warnings[0]["message"] and "[9, 12] degrees" in warnings[0]["message"]
    assert warnings[0]["field_path"] == "payload.instrument.broadening"


def test_no_reflections_and_empty_phase_are_informative_warnings():
    codes, warnings = _run_warnings(reflections_available=False, contributes={"LaB6": True, "Si": False})
    assert codes == ["easydiffraction_reflections_not_available", "easydiffraction_phase_contributes_nothing"]
    assert warnings[1]["field_path"] == "payload.phases.Si"


# ---------------------------------------------------------------------------
# engine runs (skipped without easydiffraction)
# ---------------------------------------------------------------------------


@needs_ed
def test_execute_simulation_reads_every_handed_point(tmp_path):
    outcome = nr.execute(_validated(), tmp_path)
    assert outcome.simulation is True and outcome.n_varied == 0 and outcome.converged is None
    assert outcome.x.size == int(outcome.build.window_mask.sum())
    assert np.isfinite(outcome.y_calc).all() and np.isfinite(outcome.y_bkg).all()
    assert outcome.reflections_available and len(outcome.reflections["LaB6"]) > 0
    assert (tmp_path / nr.PROJECT_DIR).exists() or any(w["code"] == "easydiffraction_project_not_saved"
                                                       for w in outcome.warnings)


@needs_ed
def test_execute_fit_reads_lmfit_outcome(tmp_path):
    recipe = _recipe()
    recipe["payload"]["phases"]["LaB6"]["scale"] = P(1.0, True, 0.0, None)
    recipe["payload"]["refinement_controls"]["max_iterations"] = 3
    outcome = nr.execute(_validated(recipe), tmp_path)
    assert outcome.simulation is False and outcome.n_varied == 1
    assert isinstance(outcome.converged, bool) and outcome.message
    (value, esd), = outcome.values.values()
    assert math.isfinite(value) and (esd is None or esd >= 0)
    assert outcome.engine_rwp is not None


# ---------------------------------------------------------------------------
# 4.2 statistics, reports, result dict (A23, A41, A125, A128, A129, A152/A157 B5)
# ---------------------------------------------------------------------------

import pandas as pd  # noqa: E402

from powderline import reports  # noqa: E402
from powderline.gateways.easydiffraction import native_builder as nb  # noqa: E402
from powderline.schema_core import parameters_requested  # noqa: E402

_GSASII_CONTRACT = {
    "success": bool, "run_id": str, "rwp": (float, type(None)), "elapsed_time": float, "method": str,
    "output_files": list, "fit_profile": pd.DataFrame, "unit_cell_data": dict, "peak_list_data": dict,
    "refined_parameters": pd.DataFrame, "spf_peaks": pd.DataFrame, "spf_convergence_diagnostics": pd.DataFrame,
    "error": type(None), "traceback": type(None),
}


def _refined_recipe():
    """LaB6 with the cell (one tie group), B1.x, the scale and W refined: 4 requested parameters."""
    recipe = _recipe()
    phase = recipe["payload"]["phases"]["LaB6"]
    for k in "abc":
        phase["unit_cell"][k] = P(4.1568, True, 4.0, 4.3)
    phase["atoms"]["B1"]["x"] = P(0.1997, True, 0.15, 0.25)
    phase["scale"] = P(1.0, True, 0.0, None)
    recipe["payload"]["instrument"]["broadening"]["w"] = P(0.002, True, 0.0, 0.1)
    return recipe


def _manifest(model, finals=None, esds=None):
    """The builder's manifest for ``model`` with fake live parameters (A185 shape)."""
    finals, esds = finals or {}, esds or {}
    manifest = {}
    for name, phase in model.payload.phases.items():
        for fp in nb.free_parameter_plan(name, phase):
            manifest[fp.ed_attr_path] = _entry(fp.path, fp.value, kind=fp.kind, unit=fp.unit, lo=fp.min, hi=fp.max,
                                               final=finals.get(fp.ed_attr_path), esd=esds.get(fp.ed_attr_path))
    for fp in nb.experiment_plan(model.payload):
        manifest[fp.ed_attr_path] = _entry(fp.path, fp.value, kind=fp.kind, unit=fp.unit, lo=fp.min, hi=fp.max,
                                           final=finals.get(fp.ed_attr_path), esd=esds.get(fp.ed_attr_path))
    return manifest


def _outcome(model, manifest=None, *, n_varied=None, converged=True, cell_a=4.1568, cell_a_esd=None, **over):
    """A RunOutcome as execute() would read it, from fake arrays: calc = 1.01 obs, bkg = 100 (the stated background)."""
    tth, itth, sigma, mask = nb.prepare_data(model.payload)
    manifest = {} if manifest is None else manifest
    simulation = not manifest
    build = nb.BuildResult(project=None, manifest=manifest, warnings=[], window_mask=mask,
                           datablock_names={n: nb.datablock_name(n) for n in model.payload.phases})
    cells = {name: {m: (cell_a if m in "abc" else 90.0, cell_a_esd if m in "abc" else None) for m in nb.CELL_ATTRS}
             for name in model.payload.phases}
    reflections = {name: [{"h": 1, "k": 0, "l": 0, "d_spacing": 4.1568, "2theta": 21.36, "F_calc_squared": 1234.5},
                          {"h": 1, "k": 1, "l": 0, "d_spacing": 2.9393, "2theta": 30.39, "F_calc_squared": 987.6}]
                   for name in model.payload.phases}
    fields = dict(build=build, simulation=simulation, x=tth, y_obs=itth, y_calc=itth * 1.01,
                  y_bkg=np.full_like(itth, 100.0), sigma=sigma, values=nr.read_values(manifest),
                  reflections=reflections, reflections_available=True,
                  n_varied=(0 if simulation else len(manifest)) if n_varied is None else n_varied,
                  converged=None if simulation else converged, message=None if simulation else "Fit succeeded.",
                  engine_rwp=None if simulation else 7.5, engine_rexp=None if simulation else 5.0,
                  engine_chi2_red=None if simulation else 2.25, widths=nb.stated_widths(model.payload),
                  contributes={n: True for n in model.payload.phases}, wavelength=1.5406, warnings=[], cells=cells,
                  engine_version="0.21.1")
    fields.update(over)
    return nr.RunOutcome(**fields)


def test_result_matches_the_gsasii_key_and_type_contract(tmp_path):
    model = _validated(_refined_recipe())
    result = nr.build_result(model, _outcome(model, _manifest(model)), tmp_path, elapsed=0.5)
    assert result["success"] is True
    for key, expected in _GSASII_CONTRACT.items():
        assert key in result, key
        assert isinstance(result[key], expected), (key, type(result[key]))
    for extra in ("r_exp", "gof", "chi2_red", "simulation_mode", "warnings", "engine_details"):
        assert extra in result
    assert result["spf_peaks"].empty and list(result["spf_peaks"].columns) == []
    assert list(result["spf_convergence_diagnostics"].columns) == []
    for frame in list(result["unit_cell_data"].values()) + list(result["peak_list_data"].values()):
        assert isinstance(frame, pd.DataFrame)
    import json
    json.dumps(result["engine_details"])  # JSON-safe (the server boundary)
    json.dumps(result["warnings"])


def test_reports_are_written_through_the_core_writers(tmp_path):
    model = _validated(_refined_recipe())
    result = nr.build_result(model, _outcome(model, _manifest(model)), tmp_path)
    names = {f.name for f in tmp_path.iterdir()}
    assert {"refined_parameters.csv", "fit_profile.txt", "LaB6_unit_cell_report.csv",
            "LaB6_peak_list_report.csv"} <= names
    header = (tmp_path / "refined_parameters.csv").read_text(encoding="utf-8").splitlines()[0]
    assert header == ",".join(reports.REFINED_PARAMETERS_COLUMNS)
    profile_header = (tmp_path / "fit_profile.txt").read_text(encoding="utf-8").splitlines()[0]
    assert profile_header == "\t".join(reports.FIT_PROFILE_COLUMNS)
    peaks = (tmp_path / "LaB6_peak_list_report.csv").read_text(encoding="utf-8").splitlines()
    assert peaks[0] == ",".join(nr.PEAK_LIST_COLUMNS) and peaks[1].startswith("1,0,0,4.1568,21.36,1234.5")
    assert all(str(tmp_path / n) in result["output_files"] for n in ("refined_parameters.csv", "fit_profile.txt"))
    assert b"\r" not in (tmp_path / "fit_profile.txt").read_bytes()


def test_statistics_are_core_fitstats_over_the_handed_points_with_lmfit_p(tmp_path):
    from powderline.fitstats import compute_fit_statistics

    model = _validated(_refined_recipe())
    manifest = _manifest(model)
    outcome = _outcome(model, manifest)
    result = nr.build_result(model, outcome, tmp_path)
    p = model.payload
    tth = np.asarray(p.xrd_data.tth)
    yobs = np.asarray(p.xrd_data.Itth)
    w = np.asarray(p.xrd_data.Itth_weights)
    ycalc = np.zeros_like(tth)
    ycalc[outcome.build.window_mask] = outcome.y_calc
    fs = compute_fit_statistics(yobs, ycalc, w, len(manifest), mask=outcome.build.window_mask)
    assert result["rwp"] == pytest.approx(fs.rwp) and result["r_exp"] == pytest.approx(fs.rexp)
    assert result["gof"] == pytest.approx(fs.gof) and result["chi2_red"] == pytest.approx(fs.chi2_red)
    d = result["engine_details"]
    assert d["n_points"] == int(outcome.build.window_mask.sum()) == fs.n_points
    assert d["parameters_requested"] == parameters_requested(model) == 4
    assert d["parameters_varied"] == 4 and d["rwp"] == 7.5 and d["engine"] == "easydiffraction"
    assert d["calculator"] == "cryspy" and d["peak_type"] == "pseudo-voigt" and d["engine_version"] == "0.21.1"
    assert d["converged"] is True and d["message"] == "Fit succeeded."
    assert [w["code"] for w in result["warnings"]] == []


def test_fewer_parameters_varied_than_requested_warns(tmp_path):
    model = _validated(_refined_recipe())
    result = nr.build_result(model, _outcome(model, _manifest(model), n_varied=3), tmp_path)
    codes = [w["code"] for w in result["warnings"]]
    assert codes == ["easydiffraction_parameters_not_varied"]
    assert "lmfit varied 3 parameter(s), but the recipe refines 4" in result["warnings"][0]["message"]


def test_y_bkg_is_the_engines_background_and_the_profile_is_the_handed_points(tmp_path):
    model = _validated(_refined_recipe())
    outcome = _outcome(model, _manifest(model), y_bkg=np.linspace(90.0, 110.0, int(nb.prepare_data(model.payload)[3].sum())))
    result = nr.build_result(model, outcome, tmp_path)
    profile = result["fit_profile"]
    assert len(profile) == int(outcome.build.window_mask.sum())
    assert profile["y_bkg"].to_numpy().tolist() == outcome.y_bkg.tolist()
    assert profile["two_theta"].to_numpy().tolist() == outcome.x.tolist()
    assert profile["y_diff"].to_numpy() == pytest.approx(outcome.y_obs - outcome.y_calc)
    assert profile["d_spacings"].iloc[0] == pytest.approx(1.5406 / (2 * math.sin(math.radians(outcome.x[0] / 2))))


def test_refined_rows_carry_values_in_the_recipes_writing_and_empty_undetermined_esds(tmp_path):
    model = _validated(_refined_recipe())
    key_x = "structures.lab6.atom_sites.B1.fract_x"
    manifest = _manifest(model, finals={key_x: -0.8, "structures.lab6.cell.length_a": 4.157},
                         esds={"structures.lab6.cell.length_a": 2e-5})
    manifest[key_x]["shift"] = -1.0  # held translated by -1 (A176)
    result = nr.build_result(model, _outcome(model, manifest), tmp_path)
    rp = result["refined_parameters"].set_index("parameter_name")
    assert rp.loc[key_x, "value"] == pytest.approx(0.2) and pd.isna(rp.loc[key_x, "esd"])
    assert rp.loc[key_x, "descriptive_name"] == "x" and rp.loc[key_x, "category"] == "atom_position"
    assert rp.loc[key_x, "phase_name"] == "LaB6" and rp.loc[key_x, "atom_name"] == "B1" and rp.loc[key_x, "atom_idx"] == 1
    a = rp.loc["structures.lab6.cell.length_a"]
    assert a["value"] == 4.157 and a["esd"] == 2e-5 and a["descriptive_name"] == "cell_a" and a["category"] == "unit_cell"
    w = rp.loc["experiments.powderline.peak.broad_gauss_w"]
    assert w["category"] == "instrument_broadening" and w["phase_name"] == "" and w["descriptive_name"] == "w"
    assert rp.loc["experiments.powderline.linked_structures.lab6.scale", "category"] == "scale"
    import csv
    with (tmp_path / "refined_parameters.csv").open(encoding="utf-8", newline="") as fh:
        row = {r["parameter_name"]: r for r in csv.DictReader(fh)}[key_x]
    assert float(row["value"]) == pytest.approx(0.2) and row["esd"] == "" and row["atom_idx"] == "1"


def test_unit_cell_rows_tied_members_and_volume(tmp_path):
    model = _validated(_refined_recipe())
    manifest = _manifest(model, finals={"structures.lab6.cell.length_a": 4.157}, esds={"structures.lab6.cell.length_a": 2e-5})
    cells = {"LaB6": {"a": (4.157, 2e-5), "b": (4.157, None), "c": (4.157, None),
                      "alpha": (90.0, None), "beta": (90.0, None), "gamma": (90.0, None)}}
    result = nr.build_result(model, _outcome(model, manifest, cells=cells), tmp_path)
    cell = result["unit_cell_data"]["LaB6"].set_index("parameter")
    assert list(cell.index) == ["cell_a", "cell_b", "cell_c", "cell_alpha", "cell_beta", "cell_gamma", "cell_volume"]
    assert cell.loc["cell_b", "esd"] == 2e-5          # tied to a: a's ESD
    assert cell.loc["cell_alpha", "esd"] == 0.0        # fixed by symmetry
    assert cell.loc["cell_volume", "value"] == pytest.approx(4.157 ** 3)
    assert cell.loc["cell_volume", "esd"] == pytest.approx(3 * 4.157 ** 2 * 2e-5, rel=1e-3)  # a, b, c move together


def test_simulation_has_statistics_and_says_it_is_one(tmp_path):
    model = _validated()
    result = nr.build_result(model, _outcome(model), tmp_path)
    assert result["simulation_mode"] is True and result["method"] == "easydiffraction_simulation"
    assert isinstance(result["rwp"], float) and isinstance(result["r_exp"], float)
    assert result["engine_details"]["parameters_varied"] == 0 and result["engine_details"]["converged"] is None
    assert result["warnings"] == []  # no not-varied warning for a simulation
    assert result["refined_parameters"].empty
    assert list(result["refined_parameters"].columns) == list(reports.REFINED_PARAMETERS_COLUMNS)


def test_undefined_statistics_are_none_not_nan(tmp_path):
    model = _validated(_refined_recipe())
    n = int(nb.prepare_data(model.payload)[3].sum())
    result = nr.build_result(model, _outcome(model, _manifest(model), n_varied=n), tmp_path)
    assert isinstance(result["rwp"], float)
    assert result["r_exp"] is None and result["gof"] is None and result["chi2_red"] is None


def test_no_reflections_means_no_peak_list_report(tmp_path):
    model = _validated()
    result = nr.build_result(model, _outcome(model, reflections={}, reflections_available=False), tmp_path)
    assert result["peak_list_data"] == {} and not (tmp_path / "LaB6_peak_list_report.csv").exists()


def test_builder_and_outcome_warnings_are_carried_once_in_order(tmp_path):
    model = _validated(_refined_recipe())
    outcome = _outcome(model, _manifest(model), n_varied=3,
                       warnings=[{"code": "easydiffraction_crysfml_start_dependent", "message": "m", "field_path": None}])
    result = nr.build_result(model, outcome, tmp_path, validated_warnings=[{"code": "v", "message": "m", "field_path": "p"}])
    assert [w["code"] for w in result["warnings"]] == ["v", "easydiffraction_crysfml_start_dependent",
                                                       "easydiffraction_parameters_not_varied"]


@needs_ed
def test_run_refinement_end_to_end(tmp_path):
    result = nr.run_refinement(_validated(_refined_recipe()), tmp_path)
    assert result["success"] and isinstance(result["rwp"], float)
    assert result["engine_details"]["parameters_varied"] == 4
    assert (tmp_path / "refined_parameters.csv").exists()
