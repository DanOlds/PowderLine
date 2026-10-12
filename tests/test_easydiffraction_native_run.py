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
