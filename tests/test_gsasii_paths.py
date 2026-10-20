"""re/20b (KI-04): GSAS-II parameter names -> recipe paths, proven on the committed regression outputs (engine-free)."""
import json
from pathlib import Path

import pandas as pd
import pytest

from powderline import roundtrip
from powderline.gateways.gsasii import paths, schema

pytestmark = pytest.mark.core

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def _case(name):
    """(model, result-like dict) from a committed example and its tracked output."""
    model = schema.validate_recipe(json.loads((EXAMPLES / name / "input.json").read_text(encoding="utf-8-sig")))
    out = EXAMPLES / name / "output"
    result = {"refined_parameters": pd.read_csv(out / "refined_parameters.csv") if (out / "refined_parameters.csv").exists()
              else pd.DataFrame(columns=["parameter_name", "descriptive_name", "phase_name", "phase_idx", "atom_name",
                                         "atom_idx", "value", "esd", "category"]),
              "unit_cell_data": {}, "spf_peaks": pd.DataFrame()}
    phases = getattr(model.payload, "phases", None) or {}
    for phase in phases:
        f = out / f"{phase}_unit_cell_report.csv"
        if f.exists():
            result["unit_cell_data"][phase] = pd.read_csv(f)
    spf = out / "single_peaks_report.txt"
    if spf.exists():
        result["spf_peaks"] = pd.read_csv(spf, sep="\t", comment="#")
    return model, result


def _value(recipe, path):
    node = recipe
    for key in path.split("."):
        node = node[int(key) if key.isdigit() else key]
    return node if "chebyshev.coefficients" in path else node[0]


def test_lab6_background_instrument_and_scale_map_to_their_paths():
    model, result = _case("example_LaB6")
    refined, not_varied, at_limit, problems = paths.refined_by_path(model, result)
    assert refined["payload.background.chebyshev.coefficients.0"] == (pytest.approx(30.02431), pytest.approx(6.768e-2, rel=1e-3))
    assert refined["payload.instrument.broadening.U"][0] == pytest.approx(49.11908, rel=1e-6)
    assert refined["payload.phases.LaB6.scale"][0] == pytest.approx(3.773081e-02, rel=1e-6)
    assert at_limit == set()
    assert not problems
    out = roundtrip.build_output(model, refined, not_varied=not_varied, validator=schema.validate_recipe)
    assert out.valid, out.problems
    rows = {r["path"]: r for r in out.dispositions}
    assert rows["payload.instrument.broadening.U"]["disposition"] == "varied"
    assert rows["payload.phases.LaB6.unit_cell.a"]["disposition"] == "held_by_user"


def test_drx33_cell_comes_from_the_unit_cell_report():
    model, result = _case("example_DRX_33")
    refined, not_varied, _, problems = paths.refined_by_path(model, result)
    assert refined["payload.phases.DRX_33.unit_cell.a"] == (pytest.approx(4.17153245), pytest.approx(0.00330186))
    assert "payload.phases.DRX_33.unit_cell.alpha" not in refined  # symmetry-fixed: the model's value, no ESD
    out = roundtrip.build_output(model, refined, not_varied=not_varied, validator=schema.validate_recipe)
    assert _value(out.recipe, "payload.phases.DRX_33.unit_cell.c") == pytest.approx(4.17153245)
    # the committed reference refined the second phase's crystallite size to -1355 um: the schema refuses it,
    # so the output recipe is written but not valid, and says exactly that (P-43 D5)
    assert not out.valid and any("Li4MgWO6_SG12.peak_broadening.size_broadening.isotropic_size" in p for p in out.problems)


def test_atomrefine_coordinates_are_recipe_plus_shift_and_unphysical_values_are_flagged():
    """GSAS-II varies dAx/dAy/dAz, the shift from the starting coordinate (ApplyXYZshifts adds it once at the
    end); occupancies refined below 0 make the output recipe honestly invalid (P-43 D5)."""
    model, result = _case("example_DRX_33_atomrefine")
    refined, not_varied, _, problems = paths.refined_by_path(model, result)
    o2_x = model.payload.phases["Li4MgWO6_SG12"].atoms["O2"].x.value
    assert refined["payload.phases.Li4MgWO6_SG12.atoms.O2.x"][0] == pytest.approx(o2_x + 8.903197e-02)
    assert refined["payload.phases.DRX_33.atoms.Li.occupancy"][0] == pytest.approx(-3.514487e-01)
    out = roundtrip.build_output(model, refined, not_varied=not_varied, validator=schema.validate_recipe)
    assert _value(out.recipe, "payload.phases.Li4MgWO6_SG12.atoms.O2.x") == pytest.approx(o2_x + 8.903197e-02)
    assert not out.valid and any("occupancy" in p for p in out.problems)


def test_anisoadp_uij_and_broadening_map():
    model, result = _case("example_DRX_33_anisoADP")
    refined, not_varied, _, problems = paths.refined_by_path(model, result)
    uij = {k: v for k, v in refined.items() if ".Uaniso." in k}
    assert uij, "the anisotropic example refines Uij"
    # units: every mapped name is written by setters.py from the recipe value and read back unconverted, so no
    # scale applies here (A^2, um, microstrain, centideg); the values below are the committed reference's
    size = {k: v for k, v in refined.items() if "peak_broadening" in k}
    assert any(k.endswith("size_broadening.isotropic_size") for k in size) or any(k.endswith("strain_broadening.isotropic_strain") for k in size)
    out = roundtrip.build_output(model, refined, not_varied=not_varied, validator=schema.validate_recipe)
    u11 = next(k for k in refined if k.endswith(".Uaniso.U11"))
    assert _value(out.recipe, u11) == pytest.approx(refined[u11][0])
    # the reference's DRX_33 crystallite size came out at -9.68 um: not valid, and named
    assert not out.valid and any("DRX_33.peak_broadening.size_broadening.isotropic_size" in p for p in out.problems)


def test_singlepeakfit_peaks_map_by_index():
    model, result = _case("example_LaB6_singlepeakfit")
    refined, not_varied, _, problems = paths.refined_by_path(model, result)
    n = len(model.payload.single_peaks.positions)
    assert sum(1 for k in refined if k.startswith("payload.single_peaks.positions.")) == n
    assert refined["payload.single_peaks.positions.0"][0] == pytest.approx(2.29520897)
    assert refined["payload.single_peaks.pv_gaussian_sigma_sq.0"][0] == pytest.approx(1.09898814)
    assert refined["payload.single_peaks.pv_lorentzian_gamma.0"][0] == pytest.approx(0.58135191)
    out = roundtrip.build_output(model, refined, not_varied=not_varied, validator=schema.validate_recipe)
    assert out.valid, out.problems


def test_background_peak_rows_map_by_index():
    model, _ = _case("example_LaB6")
    rows = pd.DataFrame([{"parameter_name": ":0:BkPkpos;0", "descriptive_name": "", "phase_name": "", "phase_idx": None,
                          "atom_name": "", "atom_idx": None, "value": 10.5, "esd": 0.01, "category": "background_peak"},
                         {"parameter_name": ":0:BkPksig;0", "descriptive_name": "", "phase_name": "", "phase_idx": None,
                          "atom_name": "", "atom_idx": None, "value": 2.0, "esd": None, "category": "background_peak"}])
    refined, _, _, problems = paths.refined_by_path(model, {"refined_parameters": rows, "unit_cell_data": {}, "spf_peaks": pd.DataFrame()})
    assert not problems
    assert refined["payload.background.single_peaks.positions.0"] == (10.5, 0.01)
    assert refined["payload.background.single_peaks.pv_gaussian_sigma_sq.0"] == (2.0, None)


def test_unknown_name_is_reported_not_silently_dropped():
    model, _ = _case("example_LaB6")
    rows = pd.DataFrame([{"parameter_name": ":0:Mystery", "descriptive_name": "", "phase_name": "", "phase_idx": None,
                          "atom_name": "", "atom_idx": None, "value": 1.0, "esd": None, "category": "other"}])
    refined, _, _, problems = paths.refined_by_path(model, {"refined_parameters": rows, "unit_cell_data": {}, "spf_peaks": pd.DataFrame()})
    assert refined == {} and len(problems) == 1 and "Mystery" in problems[0]


def test_instrument_profile_peak_fit_keeps_flags_and_says_the_profile_is_not_reported():
    """use_instrument_profile: GSAS-II overwrites the peak widths from U..Z (held in the recipe) and refines U..Z,
    which it does not report back: only flagged peak parameters are mapped, and the mode is a problem (D5)."""
    import copy
    r = json.loads((EXAMPLES / "example_LaB6_singlepeakfit" / "input.json").read_text(encoding="utf-8-sig"))
    r = copy.deepcopy(r)
    r["payload"]["refinement_controls"]["single_peak_fitting_mode"]["use_instrument_profile"] = True
    for k in ("pv_gaussian_sigma_sq", "pv_lorentzian_gamma"):
        for p in r["payload"]["single_peaks"][k]:
            p[1] = False
    for k in ("U", "V", "W", "X", "Y", "Z"):
        r["payload"]["instrument"]["broadening"][k][1] = True
    model = schema.validate_recipe(r)
    _, result = _case("example_LaB6_singlepeakfit")
    refined, not_varied, _, problems = paths.refined_by_path(model, result)
    assert not any(".pv_" in k for k in refined) and any(k.startswith("payload.single_peaks.positions.") for k in refined)
    assert problems and "use_instrument_profile" in problems[0]
    out = roundtrip.build_output(model, refined, not_varied=not_varied, validator=schema.validate_recipe)
    assert not [p for p in out.problems if "is held" in p], out.problems  # no spurious "held but changed"
