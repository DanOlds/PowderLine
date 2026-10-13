"""Tests for powderline.gateways.topas.writer (plan §7).

Covers: golden-file INP comparison for both examples, the refined-set invariant,
validation errors, the LaB6-Bz refined-coordinate variant, .xye round-trip,
determinism, and the no-GSAS-II-import guarantee.
"""

from __future__ import annotations

import copy
import json
import math
import re
import sys
from pathlib import Path

import pytest

from powderline.gateways.topas.writer import render_topas, write_topas_inp
from powderline.gateways.topas.errors import TopasTranslationError
from subprocess_utils import run_subprocess_utf8

REPO = Path(__file__).resolve().parent.parent
EXAMPLES = REPO / "tests" / "data" / "legacy_026"  # 0.26.0 snapshots of the examples (re/07, P-16)
GOLDEN = REPO / "tests" / "data" / "topas"

# Refined-name sets derived directly from each recipe's refine=true flags.
LAB6_REFINED = {
    "chebyshev_bkg",                                              # named Chebyshev background
    "inst_U", "inst_V", "inst_W", "inst_X", "inst_Y", "inst_Z",  # all six broadening refined
    "LaB6_scale",
    "bkgpk0_pos", "bkgpk0_int", "bkgpk0_sig", "bkgpk0_gam",       # single peak: all four refined
}
DRX33_REFINED = {
    "chebyshev_bkg",
    "DRX_33_scale", "DRX_33_a",
    "DRX_33_size_um", "DRX_33_size_eta", "DRX_33_strain", "DRX_33_strain_eta",
    "Li4MgWO6_SG12_scale",
    "Li4MgWO6_SG12_a", "Li4MgWO6_SG12_b", "Li4MgWO6_SG12_c", "Li4MgWO6_SG12_beta",
    "Li4MgWO6_SG12_size_um", "Li4MgWO6_SG12_size_eta",
    "Li4MgWO6_SG12_strain", "Li4MgWO6_SG12_strain_eta",
}


def _recipe(name: str) -> dict:
    return json.loads((EXAMPLES / name / "input.json").read_text())


# --- golden comparison ------------------------------------------------------


@pytest.mark.parametrize("name,expected_refined", [
    ("example_LaB6", LAB6_REFINED),
    ("example_DRX_33", DRX33_REFINED),
])
def test_inp_matches_golden(name, expected_refined):
    rendered = render_topas(_recipe(name), name)
    golden = (GOLDEN / f"{name}.inp").read_text()
    assert rendered.inp_text == golden
    assert set(rendered.refined_names) == expected_refined


@pytest.mark.parametrize("name", ["example_LaB6", "example_DRX_33"])
def test_xye_matches_golden(name):
    rendered = render_topas(_recipe(name), name)
    assert rendered.xye_text == (GOLDEN / f"{name}.xye").read_text()


# The declarative atom-refine / anisotropic-ADP examples: lock the writer output
# (INP + xye) so the permissive-coord and Uaniso translations stay byte-stable.
@pytest.mark.parametrize("name", ["example_DRX_33_atomrefine", "example_DRX_33_anisoADP"])
def test_declarative_examples_inp_and_xye_match_golden(name):
    rendered = render_topas(_recipe(name), name)
    assert rendered.inp_text == (GOLDEN / f"{name}.inp").read_text()
    assert rendered.xye_text == (GOLDEN / f"{name}.xye").read_text()
    # declarative recipes emit no symmetry-breaking warnings (multiplicity only)
    assert not any("breaks site symmetry" in w for w in rendered.warnings)


# --- refined-set invariant (parse the INP) ----------------------------------

_FIXED_RE = re.compile(r"!([A-Za-z_]\w*)")


def _fixed_names_in(inp: str) -> set[str]:
    return set(_FIXED_RE.findall(inp))


@pytest.mark.parametrize("name,expected_refined", [
    ("example_LaB6", LAB6_REFINED),
    ("example_DRX_33", DRX33_REFINED),
])
def test_refined_invariant_against_inp_text(name, expected_refined):
    rendered = render_topas(_recipe(name), name)
    inp = rendered.inp_text
    fixed = _fixed_names_in(inp)

    # 1. tracker <-> recipe flags
    assert set(rendered.refined_names) == expected_refined

    # 2. every refined name appears bare in the INP and is NEVER '!'-prefixed
    for nm in rendered.refined_names:
        assert re.search(rf"(?<![!\w]){re.escape(nm)}\b", inp), f"{nm} missing bare in INP"
        assert nm not in fixed, f"refined name {nm} appears as fixed !{nm}"

    # 3. no fixed name is also claimed refined (disjoint sets)
    assert fixed.isdisjoint(rendered.refined_names)


def test_chebyshev_background_named_when_refined():
    # background refine_flag true -> named `bkg chebyshev_bkg` block (D11), so
    # TOPAS names the coefficients chebyshev_bkg_bkg0__.. for a clean round-trip
    rendered = render_topas(_recipe("example_LaB6"), "example_LaB6")
    assert re.search(r"^\s*bkg chebyshev_bkg ", rendered.inp_text, re.M)


def test_fit_profile_and_peak_list_blocks_present():
    # Stage 2: headerless numeric intermediates via xdd_out / phase_out, using
    # only maintainer-confirmed variables (X/Yobs/SigmaYobs/Ycalc/Get(bkg);
    # H/K/L/M/D_spacing/2 Rad Th/I_no_scale_pks/I_after_scale_pks)
    inp = render_topas(_recipe("example_LaB6"), "example_LaB6").inp_text
    assert 'xdd_out "example_LaB6_topas_profile.txt" load out_record out_fmt out_eqn' in inp
    assert '" %.8g" = 1 / SigmaYobs^2;' in inp
    assert '" %.8g\\n" = Get(bkg);' in inp
    assert 'phase_out "LaB6_topas_peaks.txt" load out_record out_fmt out_eqn' in inp
    assert '" %.8g" = 2 Rad Th;' in inp
    # Stage 2b: F-column source variables (confirmed valid via probe_peaklist.inp)
    assert '" %.8g" = Iobs_no_scale_pks;' in inp
    assert '" %.8g" = A01;' in inp
    assert '" %.8g\\n" = B11;' in inp
    # out_prm_vals_on_convergence removed (results.csv is the single source)
    assert "out_prm_vals_on_convergence" not in inp


def test_drx33_peak_list_per_phase():
    inp = render_topas(_recipe("example_DRX_33"), "example_DRX_33").inp_text
    assert 'phase_out "DRX_33_topas_peaks.txt"' in inp
    assert 'phase_out "Li4MgWO6_SG12_topas_peaks.txt"' in inp


def test_results_export_block_uses_raw_primitives():
    # comment-free results CSV via RAW out_record primitives, not Out/Out_String
    # macros (those comma-split the CSV format strings and crash TOPAS, findings D.9)
    inp = render_topas(_recipe("example_LaB6"), "example_LaB6").inp_text
    assert 'out "example_LaB6_results.csv"' in inp
    assert 'out_record out_fmt "parameter,value,esd\\n"' in inp
    # fit scalars: value only (no out_fmt_err), trailing comma keeps 3 columns
    assert 'out_record out_eqn = Get(r_wp); out_fmt "r_wp,%11.8g,\\n"' in inp
    # refined prm: value via out_fmt, ESD via out_fmt_err
    assert 'out_record out_eqn = inst_U; out_fmt "inst_U,%11.8g," out_fmt_err "%11.8g\\n"' in inp
    # the Out/Out_String macros must NOT appear (they crash on the comma args)
    assert "Out_String(" not in inp
    assert "Out(" not in inp


# --- S5: negative-FWHM guard, zero-convolution skipping, defaults, scale ------


def test_all_convolution_equations_are_max_guarded():
    # every emitted phase lor_fwhm/gauss_fwhm (and the instrument lor) must be
    # wrapped in Max(..., 1e-9) so a refining term can't produce a negative FWHM
    inp = render_topas(_recipe("example_DRX_33"), "example_DRX_33").inp_text
    for m in re.finditer(r"^\s*(lor_fwhm|gauss_fwhm)\s*=\s*(.+);$", inp, re.M):
        rhs = m.group(2)
        # instrument gaussian guards inside the Sqrt instead of wrapping the whole RHS
        assert "Max(" in rhs, f"unguarded convolution: {m.group(0)}"


def test_lab6_defaults_no_strain_and_10um_size():
    # kicker.py defaults: size 10 um (negligible), strain 0 (skipped entirely)
    inp = render_topas(_recipe("example_LaB6"), "example_LaB6").inp_text
    assert "!LaB6_size_um 10" in inp
    assert "LaB6_strain" not in inp          # strain=0 fixed -> no prm, no convolution
    assert "LaB6_size_eta" in inp            # size lorentzian survives
    # eta fixed at 1 -> the size Gaussian share ((1 - eta)) is zero and skipped
    assert "1 - LaB6_size_eta" not in inp
    assert "Sqrt(Max(inst_U" in inp          # instrument gaussian still present


def test_drx33_eta_refined_keeps_all_four_convolutions():
    # size/strain/eta all refined -> Gaussian shares are live (not skipped),
    # just Max-guarded so the eta=1 starting point is safe
    inp = render_topas(_recipe("example_DRX_33"), "example_DRX_33").inp_text
    per_phase = inp.split("phase_name")[1]  # first str block
    assert per_phase.count("lor_fwhm") == 3   # instrument + size + strain
    assert per_phase.count("gauss_fwhm") == 3 # instrument + size + strain


def test_scale_starts_at_recipe_value_times_1e6():
    inp = render_topas(_recipe("example_LaB6"), "example_LaB6").inp_text
    # recipe scale 1 -> TOPAS start 1e-06 (documented GSAS-II->TOPAS magnitude)
    assert re.search(r"scale LaB6_scale 1e-06", inp)


# --- validation errors ------------------------------------------------------


def test_unsupported_schema_rejected():
    # GSASII_Rietveld and GSASII_SPF are supported; anything else errors
    recipe = _recipe("example_LaB6")
    recipe["schema_name"] = "GSASII_Something"
    with pytest.raises(TopasTranslationError, match="schema_name"):
        render_topas(recipe, "x")


# TOPAS is permissive: symmetry-breaking refine flags WARN + emit, never raise
# (the writer does not arbitrate recipe correctness).


def test_refine_symmetry_fixed_coordinate_warns_and_emits():
    # La sits at (0,0,0) in Pm-3m: every axis is symmetry-fixed
    recipe = _recipe("example_LaB6")
    recipe["payload"]["phases"]["LaB6"]["parameterization"]["atoms"]["La"]["x"] = [None, True, None, None]
    rendered = render_topas(recipe, "x")
    assert any("symmetry-fixed coordinate 'x'" in w for w in rendered.warnings)
    assert "LaB6_La_x" in rendered.refined_names  # emitted refined despite breaking symmetry
    assert re.search(r"site La x LaB6_La_x 0", rendered.inp_text)


def test_refine_symmetry_fixed_angle_warns_and_emits():
    recipe = _recipe("example_LaB6")
    recipe["payload"]["phases"]["LaB6"]["parameterization"]["unit_cell"]["alpha"] = [None, True, None, None]
    rendered = render_topas(recipe, "x")
    assert any("symmetry-fixed angle 'alpha'" in w for w in rendered.warnings)
    assert "LaB6_alpha" in rendered.refined_names
    assert re.search(r"al LaB6_alpha 90", rendered.inp_text)


def test_refine_coupled_coordinate_warns_and_emits():
    # Synthetic P4/mmm (x,x,z) diagonal-mirror site: x is COUPLED
    recipe = _recipe("example_LaB6")
    phase = recipe["payload"]["phases"]["LaB6"]
    phase["structure"]["space_group"] = "P 4/m m m"
    phase["structure"]["unit_cell"] = {
        "a": 4.0, "b": 4.0, "c": 5.0, "alpha": 90.0, "beta": 90.0, "gamma": 90.0,
    }
    phase["structure"]["atoms"] = {
        "M": {"ADP": "Uiso", "Multiplicity": 8, "Uiso": 0.01, "element": "Fe",
              "occupancy": 1.0, "x": 0.3, "y": 0.3, "z": 0.2},
    }
    phase["parameterization"]["atoms"] = {
        "M": {"Uiso": [None, False, None, None], "occupancy": [None, False, None, None],
              "x": [None, True, None, None], "y": [None, False, None, None],
              "z": [None, False, None, None], "ADP": "Uiso"},
    }
    for k in ("a", "b", "c"):
        phase["parameterization"]["unit_cell"][k] = [None, False, None, None]
    rendered = render_topas(recipe, "x")
    assert any("symmetry-coupled coordinate 'x'" in w for w in rendered.warnings)
    assert "LaB6_M_x" in rendered.refined_names


@pytest.mark.parametrize("name", [
    "example_DRX_33_simulation", "example_LaB6_simulation",
])
def test_simulation_emits_iters_zero_no_do_errors(name):
    # A simulation (refinement_cycles=1, all flags locked) has zero refined prms;
    # TOPAS should just calculate the pattern: iters 0, no do_errors, no results CSV.
    rendered = render_topas(_recipe(name), name)
    assert rendered.refined_names == frozenset()
    lines = rendered.inp_text.splitlines()
    assert "iters 0" in lines
    assert not any(l.strip() == "do_errors" for l in lines)
    assert "iters 100000" not in rendered.inp_text
    assert "_results.csv" not in rendered.inp_text  # nothing refined -> no export block


def test_refinement_still_emits_iters_and_do_errors():
    # A normal refinement keeps the iteration cap + error calculation.
    rendered = render_topas(_recipe("example_LaB6"), "example_LaB6")
    assert rendered.refined_names  # non-empty
    assert "iters 100000" in rendered.inp_text
    assert any(l.strip() == "do_errors" for l in rendered.inp_text.splitlines())


def test_rhombohedral_R_setting_rejected():
    recipe = _recipe("example_LaB6")
    recipe["payload"]["phases"]["LaB6"]["structure"]["space_group"] = "R -3 m :R"
    with pytest.raises(TopasTranslationError, match="rhombohedral"):
        render_topas(recipe, "x")


@pytest.mark.parametrize("space_group, match", [
    ("NotASpaceGroup", "unrecognized"),
    ("F d -3 m", "two origin"),
])
def test_core_symmetry_error_becomes_translation_error(space_group, match):
    # A61: core raises SymmetryError; the TOPAS gateway raises its own type,
    # chaining the core error.
    from powderline.exceptions import SymmetryError

    recipe = _recipe("example_LaB6")
    recipe["payload"]["phases"]["LaB6"]["structure"]["space_group"] = space_group
    with pytest.raises(TopasTranslationError, match=match) as info:
        render_topas(recipe, "x")
    assert isinstance(info.value.__cause__, SymmetryError)


def test_template_skeleton_rejected_cleanly():
    # The fill-in-the-blanks example_template (null instrument.parameterization,
    # empty tth) must fail with a clear TopasTranslationError, not a raw crash.
    recipe = _recipe("example_template")
    with pytest.raises(TopasTranslationError, match="incomplete|template|empty|null"):
        render_topas(recipe, "example_template")


# --- anisotropic ADP (Uaniso) -----------------------------------------------


def test_anisoadp_emits_six_uij_no_symmetry_warnings():
    # anisoADP example: O1 (C2/m general position) refines all six u_ij.
    recipe = _recipe("example_DRX_33_anisoADP")
    rendered = render_topas(recipe, "example_DRX_33_anisoADP")
    inp = rendered.inp_text
    # site line carries u11..u23 with the descriptive refined names
    assert re.search(
        r"site O1 .*u11 Li4MgWO6_SG12_O1_U11 0\.01 u22 Li4MgWO6_SG12_O1_U22 0\.012 "
        r"u33 Li4MgWO6_SG12_O1_U33 0\.008 u12 Li4MgWO6_SG12_O1_U12 0\.002 "
        r"u13 Li4MgWO6_SG12_O1_U13 0\.001 u23 Li4MgWO6_SG12_O1_U23 0\.003",
        inp,
    )
    for suffix in ("U11", "U22", "U33", "U12", "U13", "U23"):
        assert f"Li4MgWO6_SG12_O1_{suffix}" in rendered.refined_names
    # declarative recipe -> no symmetry-breaking ADP warnings (only multiplicity)
    assert not any("breaks site symmetry" in w for w in rendered.warnings)


def test_uaniso_on_symmetry_restricted_component_warns_and_emits():
    # Force Uaniso onto the Fm-3m Li site (0,0,0): off-diagonals are FIXED by
    # symmetry -> refining u12 warns (permissive) but still emits.
    recipe = _recipe("example_DRX_33_anisoADP")
    li = recipe["payload"]["phases"]["DRX_33"]["parameterization"]["atoms"]["Li"]
    li["ADP"] = "Uaniso"
    li["Uaniso"] = {"U12": [0.003, True, None, None]}
    rendered = render_topas(recipe, "example_DRX_33_anisoADP")
    assert any(
        "symmetry-restricted ADP U12" in w and "breaks site symmetry" in w
        for w in rendered.warnings
    )
    assert "DRX_33_Li_U12" in rendered.refined_names
    assert re.search(r"u12 DRX_33_Li_U12 0.003", rendered.inp_text)


# --- LaB6-Bz refined-coordinate variant -------------------------------------


def test_lab6_bz_variant_refines_z_only():
    recipe = _recipe("example_LaB6")
    # B sits at (1/2, 1/2, 0.2021) in Pm-3m: z is FREE, x=y symmetry-fixed
    recipe["payload"]["phases"]["LaB6"]["parameterization"]["atoms"]["B"]["z"] = [None, True, None, None]
    rendered = render_topas(recipe, "example_LaB6")
    inp = rendered.inp_text
    assert "LaB6_B_z" in rendered.refined_names
    assert re.search(r"z LaB6_B_z 0.2021", inp)
    # x, y stay bare numeric constants (not refined, not named)
    assert re.search(r"site B x 0.5 y 0.5 z LaB6_B_z", inp)
    assert "LaB6_B_x" not in inp and "LaB6_B_y" not in inp


# --- .xye round-trip --------------------------------------------------------


def test_xye_sigma_is_inverse_sqrt_weight():
    recipe = _recipe("example_LaB6")
    rendered = render_topas(recipe, "example_LaB6")
    weights = recipe["payload"]["xrd_data"]["Itth_weights"]
    tth = recipe["payload"]["xrd_data"]["tth"]
    rows = rendered.xye_text.strip().splitlines()
    assert len(rows) == sum(1 for w in weights if w and w > 0)
    # spot-check the first row's sigma
    x0, y0, s0 = rows[0].split()
    assert float(s0) == pytest.approx(1.0 / math.sqrt(weights[0]), rel=1e-6)
    assert float(x0) == pytest.approx(tth[0], rel=1e-9)
    assert rendered.dropped_points == 0


def test_xye_negative_weight_raises():
    # Malformed data (a negative or non-finite weight) is REJECTED, not silently
    # dropped — mirroring PowderLine's strict, no-repair xrd_data policy.
    recipe = _recipe("example_LaB6")
    xrd = recipe["payload"]["xrd_data"]
    xrd["tth"] = [1.0, 2.0, 3.0]
    xrd["Itth"] = [10.0, 20.0, 30.0]
    xrd["Itth_weights"] = [0.25, -1.0, 1.0]  # negative weight is malformed
    with pytest.raises(TopasTranslationError, match="negative or non-finite"):
        render_topas(recipe, "x")


def test_xye_zero_weight_point_excluded():
    # A weight of exactly 0 is a legitimate "exclude this point" marker that the TOPAS
    # xye_format cannot represent (sigma = 1/sqrt(0) is infinite), so such points are
    # omitted and counted transparently rather than raising.
    recipe = _recipe("example_LaB6")
    xrd = recipe["payload"]["xrd_data"]
    xrd["tth"] = [1.0, 2.0, 3.0, 4.0]
    xrd["Itth"] = [10.0, 20.0, 30.0, 40.0]
    xrd["Itth_weights"] = [0.25, 0.0, 0.0, 1.0]
    rendered = render_topas(recipe, "x")
    rows = rendered.xye_text.strip().splitlines()
    assert len(rows) == 2                 # two zero-weight points excluded
    assert rendered.dropped_points == 2
    assert rows == ["1 10 2", "4 40 1"]   # survivors; sigma = 1/sqrt(w)


def _recipe_with_xrd(tth, itth, weights):
    recipe = _recipe("example_LaB6")
    xrd = recipe["payload"]["xrd_data"]
    xrd["tth"], xrd["Itth"], xrd["Itth_weights"] = tth, itth, weights
    return recipe


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
@pytest.mark.parametrize("column", ["tth", "Itth", "Itth_weights"])
def test_xye_non_finite_value_raises(column, bad):
    # Every non-finite value is rejected regardless of which array carries it.
    # The writer is the only guard on the raw-dict path (topas-kicker CLI): the
    # TOPAS package imports no pydantic, so it cannot rely on the schema validator.
    recipe = _recipe_with_xrd([1.0, 2.0, 3.0], [10.0, 20.0, 30.0], [0.25, 1.0, 1.0])
    recipe["payload"]["xrd_data"][column][1] = bad
    with pytest.raises(TopasTranslationError, match="non-finite"):
        render_topas(recipe, "x")


def test_xye_empty_arrays_raise():
    recipe = _recipe_with_xrd([], [], [])
    with pytest.raises(TopasTranslationError, match="empty"):
        render_topas(recipe, "x")


@pytest.mark.parametrize(
    "tth",
    [
        [1.0, 2.0, 2.0],   # duplicate -> not strictly increasing
        [1.0, 3.0, 2.0],   # out of order
    ],
)
def test_xye_non_monotonic_tth_raises(tth):
    recipe = _recipe_with_xrd(tth, [10.0, 20.0, 30.0], [1.0, 1.0, 1.0])
    with pytest.raises(TopasTranslationError, match="strictly increasing"):
        render_topas(recipe, "x")


def test_xye_all_zero_weights_raise():
    # Each 0 weight is a legitimate exclusion, but excluding *every* point leaves
    # nothing to fit — rejected rather than writing an empty .xye body.
    recipe = _recipe_with_xrd([1.0, 2.0, 3.0], [10.0, 20.0, 30.0], [0.0, 0.0, 0.0])
    with pytest.raises(TopasTranslationError, match="nothing to fit"):
        render_topas(recipe, "x")


def test_xye_negative_intensity_written_faithfully():
    # Negative (background-subtracted) intensities are valid; they must be written
    # as-is and never dropped or zeroed.
    recipe = _recipe("example_LaB6")
    xrd = recipe["payload"]["xrd_data"]
    xrd["tth"] = [1.0, 2.0, 3.0]
    xrd["Itth"] = [-5.0, 20.0, -1.0]
    xrd["Itth_weights"] = [1.0, 4.0, 1.0]
    rendered = render_topas(recipe, "x")
    rows = rendered.xye_text.strip().splitlines()
    assert len(rows) == 3  # nothing dropped
    assert rendered.dropped_points == 0
    assert [float(r.split()[1]) for r in rows] == [-5.0, 20.0, -1.0]  # intensities preserved
    assert [float(r.split()[2]) for r in rows] == [1.0, 0.5, 1.0]     # sigma = 1/sqrt(w)


# --- determinism ------------------------------------------------------------


@pytest.mark.parametrize("name", ["example_LaB6", "example_DRX_33"])
def test_render_is_byte_stable(name):
    a = render_topas(_recipe(name), name)
    b = render_topas(_recipe(name), name)
    assert a.inp_text == b.inp_text
    assert a.xye_text == b.xye_text


def test_write_topas_inp_roundtrip(tmp_path):
    out = write_topas_inp(_recipe("example_LaB6"), str(tmp_path), base_name="example_LaB6")
    inp = Path(out["inp_path"])
    xye = Path(out["xye_path"])
    assert inp.exists() and xye.exists()
    assert inp.read_text() == (GOLDEN / "example_LaB6.inp").read_text()
    assert out["dropped_points"] == 0


# --- no GSAS-II imports on the TOPAS path -----------------------------------


def test_topas_package_has_no_gsasii_imports():
    import powderline.gateways.topas as topas_pkg

    pkg_dir = Path(topas_pkg.__file__).parent
    offenders = []
    for src in pkg_dir.glob("*.py"):
        text = src.read_text()
        if re.search(r"\bimport\s+GSASII|\bfrom\s+GSASII", text):
            offenders.append(src.name)
    assert not offenders, f"GSAS-II imports found in TOPAS package: {offenders}"


def test_topas_import_does_not_load_gsasii():
    # The TOPAS path must work with GSAS-II *unavailable* (the Windows scenario).
    # Run in a fresh subprocess that blocks the GSASII import so this holds even
    # when GSAS-II is installed in the dev env (Linux regression dependency).
    import textwrap

    script = textwrap.dedent(
        """
        import sys, importlib.abc, importlib.machinery

        class _Block(importlib.abc.MetaPathFinder):
            def find_spec(self, name, path, target=None):
                if name == "GSASII" or name.startswith("GSASII."):
                    raise ImportError("GSASII blocked for the TOPAS-path guard test")
                return None

        sys.meta_path.insert(0, _Block())
        import powderline               # degrades gracefully when GSAS-II is absent
        import powderline.gateways.topas
        import powderline.gateways.topas.writer
        assert "GSASII" not in sys.modules, "TOPAS path pulled GSAS-II into sys.modules"
        print("OK")
        """
    )
    proc = run_subprocess_utf8([sys.executable, "-c", script], capture_output=True, text=True)
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert "OK" in proc.stdout
