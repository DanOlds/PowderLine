"""Tests for powderline.gateways.topas.roundtrip (D10 round-trip parser)."""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path

import pytest

from powderline.gateways.topas import conversions as cv
from powderline.gateways.topas import roundtrip as rt

REPO = Path(__file__).resolve().parent.parent
EXAMPLES = REPO / "tests" / "data" / "legacy_026"  # 0.26.0 snapshots of the examples (re/07, P-16)
LEGACY_TOPAS_OUT = REPO / "examples"  # held the TOPAS 7.25 goldens (examples/*/output/topas/) until re/07, P-02
_LEGACY_GOLDENS_GONE = pytest.mark.skip(reason="reads the TOPAS 7.25 goldens that examples/*/output/topas/ carried; removed at re/07 (P-02), the test goes with the legacy TOPAS path")
DATA = REPO / "tests" / "data" / "topas"


def _recipe(name):
    return json.loads((EXAMPLES / name / "input.json").read_text())


def _results(name):
    return (DATA / f"{name}_results.csv").read_text()


# --- low-level CSV parsing --------------------------------------------------


def test_parse_results_csv_separates_stats_and_values():
    text = "parameter,value,esd\nr_wp,8.5,\ninst_U,22.7,12.3\nLaB6_scale,2.6e-06,6e-09\n"
    fit, values = rt.parse_results_csv(text)
    assert fit == {"r_wp": 8.5}
    assert values["inst_U"] == (22.7, 12.3)
    assert values["LaB6_scale"] == (2.6e-06, 6e-09)


def test_parse_ignores_header_and_blanks():
    fit, values = rt.parse_results_csv("\nparameter,value,esd\n\ngof,0.68,\n")
    assert fit == {"gof": 0.68}
    assert values == {}


# --- mapping via param_index ------------------------------------------------


def test_lab6_roundtrip_maps_categories_and_fit():
    # values are the maintainer's real round-3 TOPAS run (tests/data fixtures)
    res = rt.build_roundtrip(_recipe("example_LaB6"), _results("example_LaB6"), "example_LaB6")
    assert res.fit["r_wp"] == pytest.approx(8.519116)
    assert res.fit["gof"] == pytest.approx(0.68423175)
    by_name = {r["parameter_name"]: r for r in res.refined_parameters}
    # instrument term: GSAS-II units, descriptive + category mapped
    assert by_name["inst_Y"]["category"] == "instrument_broadening"
    assert by_name["inst_Y"]["descriptive_name"] == "instrument_broadening_Y"
    assert by_name["inst_Y"]["value"] == pytest.approx(-8.9664426)
    assert by_name["inst_Y"]["esd"] == pytest.approx(2.1826167)
    # scale carries the phase + idx
    assert by_name["LaB6_scale"]["phase_name"] == "LaB6"
    assert by_name["LaB6_scale"]["category"] == "scale"
    # named background coefficient
    assert by_name["chebyshev_bkg_bkg0__"]["descriptive_name"] == "background_coefficient_0"
    assert by_name["chebyshev_bkg_bkg0__"]["category"] == "background"


def test_drx33_roundtrip_cell_and_multiphase():
    res = rt.build_roundtrip(_recipe("example_DRX_33"), _results("example_DRX_33"), "example_DRX_33")
    by_name = {r["parameter_name"]: r for r in res.refined_parameters}
    assert by_name["DRX_33_a"]["category"] == "cell"
    # refined cubic a ~ 4.183 (loose: exact value drifts slightly between re-runs)
    assert by_name["DRX_33_a"]["value"] == pytest.approx(4.1827, rel=1e-3)
    assert by_name["Li4MgWO6_SG12_beta"]["phase_idx"] == 1

    # cubic phase (TOPAS-authoritative, D10-update): a=b=c with a *shared* ESD
    # because all three reference the same TOPAS prm, and a covariance-propagated
    # volume ESD -- mirroring what GSAS-II returns.
    cubic = {r["parameter"]: r for r in res.unit_cell["DRX_33"]}
    a = cubic["cell_a"]["value"]
    assert cubic["cell_b"]["value"] == a
    assert cubic["cell_c"]["value"] == a
    esd_a = cubic["cell_a"]["esd"]
    assert esd_a > 0
    assert cubic["cell_b"]["esd"] == esd_a
    assert cubic["cell_c"]["esd"] == esd_a
    assert cubic["cell_alpha"]["value"] == 90
    assert cubic["cell_alpha"]["esd"] == 0
    assert cubic["cell_volume"]["value"] == pytest.approx(a ** 3, rel=1e-4)
    assert cubic["cell_volume"]["esd"] > 0  # covariance-propagated, not 0

    # monoclinic phase: independent a,b,c, beta refined (alpha=gamma=90 fixed)
    cell = {r["parameter"]: r for r in res.unit_cell["Li4MgWO6_SG12"]}
    a2, b2, c2 = cell["cell_a"]["value"], cell["cell_b"]["value"], cell["cell_c"]["value"]
    beta = cell["cell_beta"]["value"]
    assert a2 != b2 and b2 != c2  # independent lengths (not collapsed to equal)
    assert cell["cell_alpha"]["value"] == 90
    assert cell["cell_gamma"]["value"] == 90
    assert cell["cell_beta"]["esd"] > 0
    # monoclinic volume = a b c sin(beta), with a real ESD from TOPAS
    v = a2 * b2 * c2 * math.sin(math.radians(beta))
    assert cell["cell_volume"]["value"] == pytest.approx(v, rel=1e-4)
    assert cell["cell_volume"]["esd"] > 0


def test_simulation_cell_report_falls_back_to_recipe_values():
    # A simulation (calculate-only) writes no _results.csv, so no cell rows: the
    # per-phase report falls back to the recipe structure values with ESD 0.
    name = "example_DRX_33_simulation"
    recipe = _recipe(name)
    res = rt.build_roundtrip(recipe, "", name)  # empty results text == simulation
    phases = recipe["payload"]["phases"]
    for phase_name, phase in phases.items():
        ucell = phase["structure"]["unit_cell"]
        report = {r["parameter"]: r for r in res.unit_cell[phase_name]}
        for key in ("a", "b", "c", "alpha", "beta", "gamma"):
            assert report[f"cell_{key}"]["value"] == ucell[key]
            assert report[f"cell_{key}"]["esd"] == 0.0
        assert report["cell_volume"]["esd"] == 0.0
        assert report["cell_volume"]["value"] > 0



# --- units transform --------------------------------------------------------


def test_beq_to_uiso_transform():
    # a LaB6 variant that refines B's Uiso -> beq prm; round-trip reports Uiso
    recipe = _recipe("example_LaB6")
    recipe["payload"]["phases"]["LaB6"]["parameterization"]["atoms"]["B"]["Uiso"] = [None, True, None, None]
    beq_value, beq_esd = 0.79, 0.08  # a plausible refined beq + esd
    results = f"parameter,value,esd\nr_wp,8.0,\nLaB6_B_beq,{beq_value},{beq_esd}\n"
    res = rt.build_roundtrip(recipe, results, "example_LaB6")
    row = next(r for r in res.refined_parameters if r["parameter_name"] == "LaB6_B_beq")
    assert row["category"] == "atom_adp"
    assert row["atom_name"] == "B"
    assert row["value"] == pytest.approx(beq_value / cv.EIGHT_PI_SQ)
    assert row["esd"] == pytest.approx(beq_esd / cv.EIGHT_PI_SQ)


def test_uaniso_roundtrip_atom_adp_no_transform():
    # anisoADP: refined u_ij come back as atom_adp rows in Angstrom^2 (no beq
    # transform -- u_ij are already Uaniso units).
    recipe = _recipe("example_DRX_33_anisoADP")
    results = (
        "parameter,value,esd\nr_wp,9.0,\n"
        "Li4MgWO6_SG12_O1_U11,0.0107,0.0009\n"
        "Li4MgWO6_SG12_O1_U23,0.0031,0.0004\n"
    )
    res = rt.build_roundtrip(recipe, results, "example_DRX_33_anisoADP")
    by_name = {r["parameter_name"]: r for r in res.refined_parameters}
    u11 = by_name["Li4MgWO6_SG12_O1_U11"]
    assert u11["category"] == "atom_adp"
    assert u11["atom_name"] == "O1"
    assert u11["value"] == pytest.approx(0.0107)  # unchanged, not divided by 8pi^2
    assert u11["esd"] == pytest.approx(0.0009)
    assert by_name["Li4MgWO6_SG12_O1_U23"]["value"] == pytest.approx(0.0031)


def test_non_finite_esd_blanks_only_esd_keeps_value():
    # MSVC/TOPAS may emit 1.#INF / 1.#QNAN for undetermined ESDs; the value row
    # must survive with esd=None rather than being dropped entirely
    for bad in ("1.#INF", "1.#QNAN", "nan", "inf"):
        fit, values = rt.parse_results_csv(f"parameter,value,esd\ninst_Y,-8.97,{bad}\n")
        assert values["inst_Y"][0] == pytest.approx(-8.97)
        assert values["inst_Y"][1] is None


def test_blank_esd_stays_blank_no_out_fallback():
    # results.csv is the SINGLE source: an undetermined ESD stays None, and there
    # is no .OUT fallback signature on build_roundtrip (maintainer, 2026-07-28)
    import inspect

    assert "out_text" not in inspect.signature(rt.build_roundtrip).parameters
    res = rt.build_roundtrip(_recipe("example_LaB6"), "parameter,value,esd\ninst_U,22.7,\n", "example_LaB6")
    row = next(r for r in res.refined_parameters if r["parameter_name"] == "inst_U")
    assert row["esd"] is None


def test_unmapped_name_is_flagged_not_dropped():
    res = rt.build_roundtrip(_recipe("example_LaB6"), "parameter,value,esd\nmystery_prm,1.0,0.1\n", "example_LaB6")
    row = next(r for r in res.refined_parameters if r["parameter_name"] == "mystery_prm")
    assert row["category"] == "unmapped"


# --- CSV serialisation matches the GSAS-II schema ---------------------------


def test_refined_parameters_csv_schema():
    res = rt.build_roundtrip(_recipe("example_LaB6"), _results("example_LaB6"), "example_LaB6")
    csv_text = rt.refined_parameters_csv(res)
    header = csv_text.splitlines()[0]
    assert header == (
        "parameter_name,descriptive_name,phase_name,phase_idx,"
        "atom_name,atom_idx,value,esd,category"
    )
    # deterministic: same input -> same bytes
    assert rt.refined_parameters_csv(res) == csv_text


@_LEGACY_GOLDENS_GONE
def test_committed_roundtrip_artifacts_match():
    """examples/**/output/topas/refined_parameters.csv are tracked and reproducible."""
    for name in ("example_LaB6", "example_DRX_33"):
        res = rt.build_roundtrip(_recipe(name), _results(name), name)
        committed = (LEGACY_TOPAS_OUT / name / "output" / "topas" / "refined_parameters.csv").read_text()
        assert rt.refined_parameters_csv(res) == committed


def _committed_results(name):
    return (LEGACY_TOPAS_OUT / name / "output" / "topas" / f"{name}_results.csv").read_text()


@_LEGACY_GOLDENS_GONE
@pytest.mark.parametrize("name", ["example_DRX_33_atomrefine", "example_DRX_33_anisoADP"])
def test_declarative_examples_roundtrip_reproduce_committed(name):
    # Validate the permissive-coord / Uaniso round-trip against the maintainer's
    # real Windows TOPAS output committed under examples/**/output/topas/.
    res = rt.build_roundtrip(_recipe(name), _committed_results(name), name)
    committed = (LEGACY_TOPAS_OUT / name / "output" / "topas" / "refined_parameters.csv").read_text()
    assert rt.refined_parameters_csv(res) == committed


@_LEGACY_GOLDENS_GONE
def test_anisoadp_uij_roundtrip_from_real_topas():
    # The six O1 u_ij come back as atom_adp rows (Angstrom^2, no beq transform)
    # with real refined values + ESDs from TOPAS.
    name = "example_DRX_33_anisoADP"
    res = rt.build_roundtrip(_recipe(name), _committed_results(name), name)
    adp = {r["parameter_name"]: r for r in res.refined_parameters if r["category"] == "atom_adp"}
    expected = {f"Li4MgWO6_SG12_O1_{u}" for u in ("U11", "U22", "U33", "U12", "U13", "U23")}
    assert set(adp) == expected
    for row in adp.values():
        assert row["atom_name"] == "O1"
        assert row["esd"] is not None and math.isfinite(row["esd"])


# --- fit profile & peak list (Stage 2) --------------------------------------


def test_recipe_wavelength():
    assert rt.recipe_wavelength(_recipe("example_LaB6")) == pytest.approx(0.1665)


def test_parse_fit_profile_computes_q_and_d():
    wl = 0.1665
    # X Yobs 1/sig^2 Ycalc Yobs-Ycalc bkg
    text = " 2.29512 130.5 0.02 128.3 2.2 30.2\n"
    (row,) = rt.parse_fit_profile(text, wl)
    assert row["two_theta"] == pytest.approx(2.29512)
    assert row["y_obs"] == pytest.approx(130.5)
    assert row["y_weights"] == pytest.approx(0.02)
    assert row["y_calc"] == pytest.approx(128.3)
    assert row["y_diff"] == pytest.approx(2.2)
    assert row["y_bkg"] == pytest.approx(30.2)
    theta = math.radians(2.29512 / 2)
    assert row["d_spacings"] == pytest.approx(wl / (2 * math.sin(theta)))
    assert row["q_values"] == pytest.approx(4 * math.pi * math.sin(theta) / wl)


def test_fit_profile_txt_matches_gsasii_header_and_is_tab_delimited():
    rows = rt.parse_fit_profile(" 1 30 0.03 30.1 -0.1 30\n", 0.1665)
    text = rt.fit_profile_txt(rows)
    header = text.splitlines()[0]
    assert header == "two_theta\ty_obs\ty_weights\ty_calc\ty_diff\ty_bkg\tq_values\td_spacings"
    assert "\t" in text.splitlines()[1] and "," not in text


def test_parse_peak_list_derives_f_obs_and_f_calc():
    # 13 cols: H K L M d 2theta I_no I_after Iobs A01 B01 A11 B11
    # F_calc^2 = A01^2+B01^2+A11^2+B11^2; F_obs^2 = F_calc^2 * Iobs/I_no
    text = " 1 0 0 6 4.15682 2.29512 0.0010494 0.0010494 0.0010491 37.405 0 0 0\n"
    (row,) = rt.parse_peak_list(text, "LaB6")
    assert row["F_calc_squared"] == pytest.approx(37.405**2, rel=1e-6)
    # F_calc^2 ~ GSAS-II F_calc_squared(100) = 1395.2 (different engine, ~0.3%)
    assert row["F_calc_squared"] == pytest.approx(1395.2, rel=0.01)
    assert row["F_obs_squared"] == pytest.approx(37.405**2 * (0.0010491 / 0.0010494), rel=1e-6)
    assert row["phase"] == "LaB6"


def test_peak_list_report_csv_header_and_f_columns():
    text = " 1 0 0 6 4.15682 2.29512 0.001 0.001 0.001 37.405 0 0 0\n"
    csv_text = rt.peak_list_report_csv(rt.parse_peak_list(text, "LaB6"))
    assert csv_text.splitlines()[0] == (
        "h,k,l,multiplicity,d_spacing,2theta,F_obs_squared,F_calc_squared,"
        "I_no_scale_pks,I_after_scale_pks,phase"
    )
    assert csv_text.splitlines()[1].endswith(",LaB6")


def test_f_obs_zero_when_i_no_scale_nonpositive():
    text = " 1 0 0 6 4.1 2.3 0 0.001 0.5 10 0 0 0\n"  # I_no_scale_pks = 0
    (row,) = rt.parse_peak_list(text, "P")
    assert row["F_obs_squared"] == 0.0
    assert row["F_calc_squared"] == pytest.approx(100.0)


def test_parse_helpers_skip_garbage_rows():
    # a stray header/short line is skipped, not fatal
    assert rt.parse_fit_profile("header row here\n 1 2 3 4 5 6\n", 0.1665)[0]["two_theta"] == 1
    assert len(rt.parse_peak_list("oops\n1 0 0 6 4.1 2.3 1 1 1 5 0 0 0\n", "P")) == 1


# --- committed derived-report bond: fit_profile.txt + <phase>_peak_list_report ---
#
# These GSAS-II-matching reports are re-derived by the parser from the committed
# headerless TOPAS intermediates (``<base>_topas_profile.txt`` /
# ``<phase>_topas_peaks.txt``). Unlike ``refined_parameters.csv`` and
# ``<phase>_unit_cell_report.csv`` they were NOT bond-tested, which let a corrupted
# LaB6 ``fit_profile.txt`` (overwritten with test-fixture rows) and a truncated
# ``LaB6_peak_list_report.csv`` reach a commit (findings D.17). These tests
# re-derive each committed report from its committed intermediate and assert
# byte-equality -- deterministic, GSAS-II-free, no TOPAS needed.

_TOPAS_OUT_DIRS = sorted(LEGACY_TOPAS_OUT.glob("*/output/topas"))


def _fit_profile_cases():
    cases = []
    for out_dir in _TOPAS_OUT_DIRS:
        if not (out_dir / "fit_profile.txt").exists():
            continue
        profiles = list(out_dir.glob("*_topas_profile.txt"))
        if len(profiles) == 1:  # one whole-pattern profile per example
            ex_name = out_dir.parent.parent.name
            cases.append(pytest.param(ex_name, out_dir, profiles[0], id=ex_name))
    return cases


def _peak_list_cases():
    cases = []
    for out_dir in _TOPAS_OUT_DIRS:
        ex_name = out_dir.parent.parent.name
        for committed in sorted(out_dir.glob("*_peak_list_report.csv")):
            phase = committed.name[: -len("_peak_list_report.csv")]
            raw = out_dir / f"{phase}_topas_peaks.txt"
            if raw.exists():
                cases.append(pytest.param(raw, phase, committed, id=f"{ex_name}-{phase}"))
    return cases


@_LEGACY_GOLDENS_GONE
def test_derived_report_bond_covers_examples():
    # Guard: if the glob discovery ever silently breaks, parametrize would collect
    # zero cases and pass vacuously. Pin a floor so coverage can't vanish unnoticed.
    # Floors set at the public release, when several examples (5 fit-profile,
    # 4 peak-list cases) moved to private storage. Raise these when new
    # examples with committed TOPAS reports are added.
    assert len(_fit_profile_cases()) >= 5
    assert len(_peak_list_cases()) >= 7


@pytest.mark.parametrize("ex_name,out_dir,raw_profile", _fit_profile_cases())
def test_committed_fit_profile_rederives_from_intermediate(ex_name, out_dir, raw_profile):
    """Committed fit_profile.txt == parser output from its committed intermediate."""
    wavelength = rt.recipe_wavelength(_recipe(ex_name))
    rows = rt.parse_fit_profile(raw_profile.read_text(), wavelength)
    derived = rt.fit_profile_txt(rows)
    committed = (out_dir / "fit_profile.txt").read_text()
    assert derived == committed, (
        f"{ex_name}: committed fit_profile.txt drifted from {raw_profile.name} "
        f"(derived {len(rows)} rows) -- re-derive via `--parse-results`"
    )


@pytest.mark.parametrize("raw_peaks,phase,committed", _peak_list_cases())
def test_committed_peak_list_rederives_from_intermediate(raw_peaks, phase, committed):
    """Committed <phase>_peak_list_report.csv == parser output from its intermediate."""
    rows = rt.parse_peak_list(raw_peaks.read_text(), phase)
    derived = rt.peak_list_report_csv(rows)
    assert derived == committed.read_text(), (
        f"{phase}: committed {committed.name} drifted from {raw_peaks.name} "
        f"(derived {len(rows)} reflections) -- re-derive via `--parse-results`"
    )
