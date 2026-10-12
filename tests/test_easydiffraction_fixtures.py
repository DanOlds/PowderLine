"""The hand-derived native easydiffraction fixtures (A137, A157 B8) validate and plan engine-free.

``tests/data/easydiffraction/native/*.json`` are written by the devkit's
``make_native_fixtures.py``; their README records the derivations. These tests
run in the core-only environment (no easydiffraction import).
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

from powderline.gateways.easydiffraction import gateway, native_builder as nb, schema
from powderline.schema_core import parameters_requested

FIXTURES = Path(__file__).parent / "data" / "easydiffraction" / "native"
NAMES = ["lab6_crysfml_tch.json", "lab6_cryspy_pv.json", "lab6_slots_simulation.json", "drx33_two_phase_cryspy.json"]
G = 8 * math.log(2) * 1e-4


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_the_four_fixtures_and_their_readme_are_committed():
    assert sorted(p.name for p in FIXTURES.glob("*.json")) == sorted(NAMES)
    assert (FIXTURES / "README.md").exists()


@pytest.mark.parametrize("name", NAMES)
def test_fixture_validates_through_the_gateway_without_the_engine(name):
    model = gateway.validate(_load(name))
    assert isinstance(model, schema.EasydiffractionRecipe)
    assert model.schema_name == "easydiffraction.rietveld"
    assert "easydiffraction" not in sys.modules


@pytest.mark.parametrize("name", NAMES)
def test_fixture_plans_and_width_check_run(name, tmp_path):
    """``free_parameter_plan`` / ``experiment_plan`` run on every fixture; the plans add up to the requested count."""
    model = gateway.validate(_load(name))
    payload = model.payload
    planned = sum(len(nb.free_parameter_plan(n, ph)) for n, ph in payload.phases.items())
    planned += len(nb.experiment_plan(payload))
    assert planned == parameters_requested(model)
    nb.check_peak_widths(payload)  # the stated profile is >= 0 over the window with its margin (A171, A178)
    for n, ph in payload.phases.items():
        nb.resolve_space_group(ph.space_group, payload.refinement_controls.calculator)
    summary = gateway.run(_load(name), tmp_path, validate_only=True)
    assert summary["success"] and summary["max_iterations"] == 1000
    assert summary["simulation_mode"] is (parameters_requested(model) == 0)


def test_lab6_refinement_fixtures_request_13_parameters():
    for name, calculator, peak_type in [("lab6_crysfml_tch.json", "crysfml", "cwl-thompson-cox-hastings"),
                                        ("lab6_cryspy_pv.json", "cryspy", "cwl-pseudo-voigt")]:
        model = gateway.validate(_load(name))
        p = model.payload
        assert parameters_requested(model) == 13  # a, scale, 6 background, U V W X Y
        assert p.refinement_controls.calculator == calculator and p.instrument.broadening.peak_type == peak_type
        assert p.fit_range.min == 1.0 and p.fit_range.max == 15.0
        assert [fp.ed_attr_path for fp in nb.free_parameter_plan("LaB6", p.phases["LaB6"])] == ["structures.lab6.cell.length_a"]


def test_slots_simulation_requests_nothing_and_states_every_slot():
    model = gateway.validate(_load("lab6_slots_simulation.json"))
    p = model.payload
    assert parameters_requested(model) == 0
    assert nb.experiment_plan(p) == [] and nb.free_parameter_plan("LaB6", p.phases["LaB6"]) == []
    inst = p.instrument
    assert inst.radiation.setup_wavelength_2 == 0.1669 and inst.radiation.setup_wavelength_2_to_1_ratio == 0.5
    assert inst.polarization.setup_polarization_coefficient == pytest.approx(0.01)  # p = 1 - P (P = 0.99)
    assert inst.corrections.calib_sample_displacement.value == 0.001
    assert inst.corrections.calib_sample_transparency.value == -0.0005
    assert inst.absorption.type == "cylinder-hewat" and inst.absorption.mu_r.value == 0.3
    assert inst.broadening.cutoff_fwhm == 20.0
    assert isinstance(inst.broadening.parameters, schema.BerarBaldinozziParameters)
    assert p.fit_range is None and p.background.chebyshev.coefficients == [0.0]  # D1 default
    # every stated slot reaches the builder's hand-off list (A160: slots are live)
    handed = {ed for _, ed, _, _ in nb.instrument_parameters(inst)}
    assert {"peak.asym_beba_a0", "peak.asym_beba_b1", "instrument.calib_sample_displacement",
            "instrument.calib_sample_transparency", "absorption.mu_r"} <= handed
    assert nb.width_margin(p) == pytest.approx(1.0 + 0.0 + 0.001 + 0.0005)


def test_two_phase_fixture_maps_the_monoclinic_setting():
    model = gateway.validate(_load("drx33_two_phase_cryspy.json"))
    p = model.payload
    assert list(p.phases) == ["DRX_33", "Li4MgWO6_SG12"]
    assert nb.resolve_space_group("C 1 2/m 1", "cryspy") == ("C 2/m", "b1")
    assert nb.resolve_space_group("F m -3 m", "cryspy") == ("F m -3 m", "")
    assert p.phases["Li4MgWO6_SG12"].space_group == "C 1 2/m 1"
    mono = [fp.ed_attr_path for fp in nb.free_parameter_plan("Li4MgWO6_SG12", p.phases["Li4MgWO6_SG12"])]
    assert mono == ["structures.li4mgwo6_sg12.cell.length_a", "structures.li4mgwo6_sg12.cell.length_b",
                    "structures.li4mgwo6_sg12.cell.length_c", "structures.li4mgwo6_sg12.cell.angle_beta"]
    assert [fp.ed_attr_path for fp in nb.free_parameter_plan("DRX_33", p.phases["DRX_33"])] == \
        ["structures.drx_33.cell.length_a"]
    scales = [fp.ed_attr_path for fp in nb.experiment_plan(p) if fp.kind == "scale"]
    assert scales == ["experiments.powderline.linked_structures.drx_33.scale",
                      "experiments.powderline.linked_structures.li4mgwo6_sg12.scale"]
    assert parameters_requested(model) == 18


def test_fixtures_carry_the_documented_derivations():
    """The README's rules against the GSAS-II example: G, the X/Y swap, p = 1 - P, Zero / 100, SH/L split."""
    example = json.loads((Path(__file__).parent.parent / "examples" / "example_LaB6_easydiff" / "input.json")
                         .read_text(encoding="utf-8"))["payload"]
    ip = example["instrument"]["initialization"][0]
    for name in ("lab6_crysfml_tch.json", "lab6_cryspy_pv.json"):
        inst = _load(name)["payload"]["instrument"]
        widths = inst["broadening"]["parameters"]
        assert widths["broad_gauss_u"][0] == pytest.approx(ip["U"][1] * G)
        assert widths["broad_gauss_w"][0] == pytest.approx(ip["W"][1] * G)
        assert widths["broad_lorentz_x"][0] == pytest.approx(ip["Y"][1] / 100)  # the roles swap (A171)
        assert widths["broad_lorentz_y"][0] == pytest.approx(ip["X"][1] / 100)
        assert inst["polarization"]["setup_polarization_coefficient"] == pytest.approx(1 - ip["Polariz."][1])
        assert inst["corrections"]["calib_twotheta_offset"][0] == ip["Zero"][1] / 100
        assert inst["radiation"]["setup_wavelength"][0] == ip["Lam"][1]
    tch = _load("lab6_crysfml_tch.json")["payload"]["instrument"]["broadening"]["parameters"]
    assert tch["asym_fcj_1"][0] == tch["asym_fcj_2"][0] == 0.002 / 2  # GSAS-II's SH/L floor, S = H
