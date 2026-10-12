"""Schema validation tests for ``easydiffraction.rietveld`` (A160, A162, A171, A195).

The recipe shape is the hand-derived fixtures' (``tests/data/easydiffraction/native/``):
the instrument keys are easydiffraction's attribute names, ``background`` holds
``chebyshev``, ``refinement_controls`` is required with no defaults.
"""
import sys

import numpy as np
import pytest
from pydantic import ValidationError

from powderline.gateways.easydiffraction import schema as s
from powderline.gateways.easydiffraction.schema import (
    ENGINE_SCHEMA_VERSION,
    EasydiffractionRecipe,
)


def P(v, f=False, lo=None, hi=None):
    return [v, f, lo, hi]


def _widths(**over):
    base = {"broad_gauss_u": P(1.0), "broad_gauss_v": P(0.0), "broad_gauss_w": P(1.0),
            "broad_lorentz_x": P(0.0), "broad_lorentz_y": P(0.0)}
    base.update(over)
    return base


def _instrument(peak_type="cwl-pseudo-voigt", parameters=None, **extra):
    inst = {"radiation": {"setup_wavelength": P(1.5406)},
            "corrections": {"calib_twotheta_offset": P(0.0)},
            "broadening": {"peak_type": peak_type, "parameters": parameters or _widths()}}
    inst.update(extra)
    return inst


def _controls(calculator="cryspy", **over):
    base = {"calculator": calculator, "minimizer": "lmfit (leastsq)", "max_iterations": 100,
            "chi_square_change_tolerance": 1e-8, "parameter_change_tolerance": 1e-8, "gradient_tolerance": 0.0}
    base.update(over)
    return base


def _atom(element="Fe", xyz=(0.0, 0.0, 0.0), **over):
    atom = {"element": element, "x": P(xyz[0]), "y": P(xyz[1]), "z": P(xyz[2]), "occupancy": P(1.0),
            "ADP": "Uiso", "Uiso": P(0.01)}
    atom.update(over)
    return atom


def _phase(space_group="P m -3 m", cell=(4.15, 4.15, 4.15, 90.0, 90.0, 90.0), atoms=None, scale=P(1.0)):
    return {"space_group": space_group,
            "unit_cell": {k: P(v) for k, v in zip(("a", "b", "c", "alpha", "beta", "gamma"), cell)},
            "atoms": atoms or {"Fe1": _atom()}, "scale": scale}


def _payload(**over):
    payload = {"xrd_data": {"tth": [10.0, 20.0, 30.0], "Itth": [100.0, 200.0, 150.0], "Itth_weights": [1.0, 1.0, 1.0]},
               "fit_range": [10.0, 30.0], "phases": {"TestPhase": _phase()}, "instrument": _instrument(),
               "refinement_controls": _controls()}
    payload.update(over)
    return payload


def _recipe(**over):
    return {"schema_name": "easydiffraction.rietveld", "core_schema_version": "1.0.0",
            "engine_schema_version": "1.0.0", "payload": _payload(**over)}


def _errors(recipe) -> list[tuple]:
    with pytest.raises(ValidationError) as exc:
        s.validate_recipe(recipe)
    return [(e["loc"], e["msg"]) for e in exc.value.errors()]


# --- module ------------------------------------------------------------------


def test_schema_name_required():
    """Schema name must be easydiffraction.rietveld."""
    with pytest.raises(ValidationError) as exc:
        EasydiffractionRecipe.model_validate({
            "core_schema_version": "1.0.0",
            "engine_schema_version": "1.0.0",
            "schema_name": "wrong.name",  # Wrong!
        })
    assert "schema_name" in str(exc.value)


def test_version_constants():
    assert ENGINE_SCHEMA_VERSION == "1.0.0"


def test_imports_no_easydiffraction():
    """Schema imports no easydiffraction (engine-free)."""
    assert "easydiffraction" not in sys.modules


def test_valid_recipe_in_the_fixtures_shape_validates():
    model = s.validate_recipe(_recipe())
    inst = model.payload.instrument
    assert inst.radiation.setup_wavelength.value == 1.5406
    assert inst.broadening.peak_type == "cwl-pseudo-voigt"
    assert isinstance(inst.broadening.parameters, s.PseudoVoigtParameters)
    assert inst.broadening.parameters.broad_gauss_u.value == 1.0
    assert inst.polarization is None and inst.absorption is None and inst.broadening.cutoff_fwhm is None
    assert model.payload.refinement_controls.max_iterations == 100
    assert "easydiffraction" not in sys.modules


# --- instrument (A160) -------------------------------------------------------


def test_wavelength_positive():
    """Wavelength must be > 0 (A126)."""
    for bad in (0.0, -0.5):
        with pytest.raises(ValidationError) as exc:
            s.Radiation(setup_wavelength=P(bad))
        assert "setup_wavelength must be > 0" in str(exc.value)


def test_ka2_pair_goes_together_and_is_never_null():
    """EB-75: one of the Kα2 pair alone does nothing, so it is an error; null is rejected (A96)."""
    both = s.Radiation(setup_wavelength=P(1.5406), setup_wavelength_2=1.5444, setup_wavelength_2_to_1_ratio=0.5)
    assert both.setup_wavelength_2 == 1.5444
    assert "setup_wavelength_2" not in s.Radiation(setup_wavelength=P(1.5406)).model_dump()
    with pytest.raises(ValidationError, match="goes together"):
        s.Radiation(setup_wavelength=P(1.5406), setup_wavelength_2=1.5444)
    with pytest.raises(ValidationError, match="goes together"):
        s.Radiation(setup_wavelength=P(1.5406), setup_wavelength_2_to_1_ratio=0.5)
    with pytest.raises(ValidationError, match="leave it out instead"):
        s.Radiation(setup_wavelength=P(1.5406), setup_wavelength_2=None, setup_wavelength_2_to_1_ratio=0.5)
    with pytest.raises(ValidationError):
        s.Radiation(setup_wavelength=P(1.5406), setup_wavelength_2=1.5444, setup_wavelength_2_to_1_ratio=1.5)


def test_polarization_is_fixed_values_in_range():
    """EB-75: p in [0, 1] and the monochromator angle are plain floats, not refinable."""
    pol = s.Polarization(setup_polarization_coefficient=0.01, setup_monochromator_twotheta=0.0)
    assert pol.setup_polarization_coefficient == 0.01
    with pytest.raises(ValidationError):
        s.Polarization(setup_polarization_coefficient=1.5, setup_monochromator_twotheta=0.0)
    with pytest.raises(ValidationError):
        s.Polarization(setup_polarization_coefficient=P(0.01), setup_monochromator_twotheta=0.0)
    recipe = _recipe(instrument=_instrument(polarization=None))
    assert any(loc == ("payload", "instrument", "polarization") and "leave it out" in msg for loc, msg in _errors(recipe))


def test_corrections_optional_slots_absent_not_null():
    corr = s.Corrections(calib_twotheta_offset=P(0.01), calib_sample_displacement=P(0.001))
    assert corr.calib_sample_transparency is None and "calib_sample_transparency" not in corr.model_dump()
    assert corr.model_dump()["calib_sample_displacement"] == [0.001, False, None, None]
    with pytest.raises(ValidationError, match="leave it out instead"):
        s.Corrections(calib_twotheta_offset=P(0.0), calib_sample_transparency=None)


def test_absorption_is_cylinder_hewat_with_mu_r_nonnegative():
    absorption = s.Absorption(type="cylinder-hewat", mu_r=P(0.3, True, 0.0, 2.0))
    assert absorption.mu_r.refine_flag is True
    with pytest.raises(ValidationError, match="mu_r must be >= 0"):
        s.Absorption(type="cylinder-hewat", mu_r=P(-0.1))
    with pytest.raises(ValidationError):
        s.Absorption(type="sphere", mu_r=P(0.1))


def test_every_slot_stated_validates():
    """A160: every slot easydiffraction models can be stated (the slots-simulation fixture's shape)."""
    recipe = _recipe(instrument=_instrument(
        "cwl-pseudo-voigt-berar-baldinozzi-asymmetry",
        {**_widths(), "asym_beba_a0": P(0.01), "asym_beba_b0": P(0.002), "asym_beba_a1": P(-0.005), "asym_beba_b1": P(0.001)},
        description="every slot",
        polarization={"setup_polarization_coefficient": 0.01, "setup_monochromator_twotheta": 0.0},
        absorption={"type": "cylinder-hewat", "mu_r": P(0.3)}))
    inst = recipe["payload"]["instrument"]
    inst["radiation"].update({"setup_wavelength_2": 1.5444, "setup_wavelength_2_to_1_ratio": 0.5})
    inst["corrections"].update({"calib_sample_displacement": P(0.001), "calib_sample_transparency": P(-0.0005)})
    inst["broadening"]["cutoff_fwhm"] = 20.0
    model = s.validate_recipe(recipe)
    assert isinstance(model.payload.instrument.broadening.parameters, s.BerarBaldinozziParameters)
    assert model.payload.instrument.absorption.mu_r.value == 0.3
    assert model.payload.instrument.broadening.cutoff_fwhm == 20.0
    assert model.model_dump()["payload"]["instrument"] == inst  # round-trips as written (A121)


# --- broadening: peak types and their parameters (A160, EB-76) ---------------


@pytest.mark.parametrize("peak_type, extra", [
    ("cwl-pseudo-voigt", {}),
    ("cwl-thompson-cox-hastings", {"asym_fcj_1": P(0.001), "asym_fcj_2": P(0.001)}),
    ("cwl-pseudo-voigt-berar-baldinozzi-asymmetry",
     {"asym_beba_a0": P(0.01), "asym_beba_b0": P(0.002), "asym_beba_a1": P(-0.005), "asym_beba_b1": P(0.001)}),
])
def test_parameters_per_peak_type(peak_type, extra):
    b = s.Broadening(peak_type=peak_type, parameters={**_widths(), **extra})
    assert type(b.parameters) is s.PEAK_PARAMETERS[peak_type]
    assert set(type(b.parameters).model_fields) == set(s.WIDTH_PARAMETERS) | set(extra)
    assert b.model_dump() == {"peak_type": peak_type, "parameters": {**_widths(), **extra}}  # cutoff left out


def test_parameters_extra_forbid_and_mismatch_with_peak_type():
    """A key of another peak type is refused at its location; parameters of another type are refused."""
    with pytest.raises(ValidationError) as exc:
        s.Broadening(peak_type="cwl-pseudo-voigt", parameters={**_widths(), "asym_fcj_1": P(0.001), "asym_fcj_2": P(0.001)})
    assert "parameters are written for peak_type 'cwl-thompson-cox-hastings', but peak_type is 'cwl-pseudo-voigt'" in str(exc.value)
    with pytest.raises(ValidationError) as exc:
        s.Broadening(peak_type="cwl-thompson-cox-hastings", parameters=_widths())
    assert "it takes asym_fcj_1, asym_fcj_2" in str(exc.value)
    with pytest.raises(ValidationError) as exc:
        s.Broadening(peak_type="cwl-thompson-cox-hastings",
                     parameters={**_widths(), "asym_fcj_1": P(0.001), "asym_fcj_2": P(0.001), "asym_beba_a0": P(0.01)})
    assert any(e["type"] == "extra_forbidden" and e["loc"][-1] == "asym_beba_a0" for e in exc.value.errors())
    with pytest.raises(ValidationError) as exc:
        s.Broadening(peak_type="cwl-thompson-cox-hastings", parameters={**_widths(), "asym_fcj_1": P(0.001)})
    assert [e["loc"][-1] for e in exc.value.errors() if e["type"] == "missing"] == ["asym_fcj_2"]
    with pytest.raises(ValidationError):
        s.Broadening(peak_type="tch", parameters=_widths())  # the legacy short code is not a peak type


def test_cutoff_fwhm_nonnegative_and_never_null():
    assert s.Broadening(peak_type="cwl-pseudo-voigt", cutoff_fwhm=0.0, parameters=_widths()).cutoff_fwhm == 0.0
    with pytest.raises(ValidationError):
        s.Broadening(peak_type="cwl-pseudo-voigt", cutoff_fwhm=-1.0, parameters=_widths())
    with pytest.raises(ValidationError, match="leave it out instead"):
        s.Broadening(peak_type="cwl-pseudo-voigt", cutoff_fwhm=None, parameters=_widths())


def test_negative_width_at_the_representative_angle_is_an_error():
    """A171, EB-87: the schema checks 2theta = 90 deg; the builder checks the window with its margin (A178)."""
    with pytest.raises(ValidationError) as exc:
        s.Broadening(peak_type="cwl-pseudo-voigt", parameters=_widths(broad_gauss_u=P(-100.0)))
    assert "Gaussian FWHM² < 0" in str(exc.value)
    with pytest.raises(ValidationError) as exc:
        s.Broadening(peak_type="cwl-pseudo-voigt", parameters=_widths(broad_lorentz_y=P(-0.01)))
    assert "Lorentzian FWHM < 0" in str(exc.value)


# --- calculator x peak type (A162, EB-76) -------------------------------------


def test_calculator_peak_type_matrix_is_eb76():
    assert s.CALCULATOR_PEAK_TYPES == {
        "cryspy": ("cwl-pseudo-voigt", "cwl-pseudo-voigt-berar-baldinozzi-asymmetry"),
        "crysfml": ("cwl-pseudo-voigt", "cwl-thompson-cox-hastings"),
    }
    assert s.CUTOFF_FWHM_CALCULATORS == ("cryspy",)


_TCH = {**_widths(), "asym_fcj_1": P(0.001), "asym_fcj_2": P(0.001)}
_BEBA = {**_widths(), "asym_beba_a0": P(0.01), "asym_beba_b0": P(0.002), "asym_beba_a1": P(-0.005), "asym_beba_b1": P(0.001)}


@pytest.mark.parametrize("calculator, peak_type, parameters, ok", [
    ("cryspy", "cwl-pseudo-voigt", _widths(), True),
    ("cryspy", "cwl-pseudo-voigt-berar-baldinozzi-asymmetry", _BEBA, True),
    ("cryspy", "cwl-thompson-cox-hastings", _TCH, False),
    ("crysfml", "cwl-pseudo-voigt", _widths(), True),
    ("crysfml", "cwl-thompson-cox-hastings", _TCH, True),
    ("crysfml", "cwl-pseudo-voigt-berar-baldinozzi-asymmetry", _BEBA, False),
])
def test_peak_type_validated_per_calculator(calculator, peak_type, parameters, ok):
    recipe = _recipe(instrument=_instrument(peak_type, parameters), refinement_controls=_controls(calculator))
    if ok:
        model = s.validate_recipe(recipe)
        assert model.payload.instrument.broadening.peak_type == peak_type
        return
    errors = _errors(recipe)
    assert [loc for loc, _ in errors] == [("payload", "instrument", "broadening", "peak_type")]
    other = "crysfml" if calculator == "cryspy" else "cryspy"
    assert f"not computed by calculator {calculator!r}" in errors[0][1] and f"calculator {other!r} computes it" in errors[0][1]


def test_cutoff_fwhm_is_cryspy_only():
    recipe = _recipe(refinement_controls=_controls("crysfml"))
    recipe["payload"]["instrument"]["broadening"]["cutoff_fwhm"] = 20.0
    errors = _errors(recipe)
    assert [loc for loc, _ in errors] == [("payload", "instrument", "broadening", "cutoff_fwhm")]
    assert "cryspy only" in errors[0][1] and "30-FWHM" in errors[0][1]
    recipe["payload"]["refinement_controls"]["calculator"] = "cryspy"
    assert s.validate_recipe(recipe).payload.instrument.broadening.cutoff_fwhm == 20.0


# --- refinement controls (A162, A195: required, no defaults) ------------------


def test_refinement_controls_required_no_defaults():
    controls = s.RefinementControls(**_controls())
    assert (controls.calculator, controls.minimizer, controls.max_iterations) == ("cryspy", "lmfit (leastsq)", 100)
    assert controls.gradient_tolerance == 0.0  # EB-81: what easydiffraction itself passes
    for name in _controls():
        with pytest.raises(ValidationError) as exc:
            s.RefinementControls(**{k: v for k, v in _controls().items() if k != name})
        assert [e["loc"] for e in exc.value.errors()] == [(name,)]
        assert exc.value.errors()[0]["type"] == "missing"
    props = s.RefinementControls.model_json_schema()["properties"]
    assert all("default" not in prop for prop in props.values())
    assert "peak_type" not in props and "cutoff_fwhm" not in props  # those are the instrument's (A160)


def test_refinement_controls_values():
    with pytest.raises(ValidationError):
        s.RefinementControls(**_controls(minimizer="scipy"))
    with pytest.raises(ValidationError):
        s.RefinementControls(**_controls(calculator="gsas"))
    for name in ("chi_square_change_tolerance", "parameter_change_tolerance"):
        for bad in (0.0, -1e-6):
            with pytest.raises(ValidationError) as exc:
                s.RefinementControls(**_controls(**{name: bad}))
            assert [e["loc"] for e in exc.value.errors()] == [(name,)]
    with pytest.raises(ValidationError):
        s.RefinementControls(**_controls(gradient_tolerance=-1e-6))
    with pytest.raises(ValidationError):
        s.RefinementControls(**_controls(max_iterations=0))


# --- background (A189, D1) ----------------------------------------------------


def test_background_is_chebyshev_block_defaulting_to_one_fixed_zero_term():
    stated = s.validate_recipe(_recipe(background={"chebyshev": {"num_coefficients": 2, "coefficients": [30.0, 0.6],
                                                                 "refine_flag": True}}))
    assert stated.payload.background.chebyshev.coefficients == [30.0, 0.6]
    default = s.validate_recipe(_recipe()).payload.background.chebyshev
    assert (default.num_coefficients, default.coefficients, default.refine_flag) == (1, [0.0], False)
    assert [loc for loc, msg in _errors(_recipe(background=None)) if "leave it out" in msg] == [("payload", "background")]
    assert [loc for loc, msg in _errors(_recipe(background={"chebyshev": None})) if "leave it out" in msg] == \
        [("payload", "background", "chebyshev")]
    # the flat 0.26-style block is not accepted (the payload level states the experiment's background)
    errors = _errors(_recipe(background={"num_coefficients": 1, "coefficients": [0.0], "refine_flag": False}))
    assert all(loc[:2] == ("payload", "background") for loc, _ in errors)


# --- phases (A161, EB-46) ------------------------------------------------------


def test_phase_scale_nonnegative():
    """Scale must be >= 0 (A161); 0 is allowed (a phase contributing nothing, A157 B7)."""
    assert s.EasydiffractionPhase(**_phase(scale=P(0.0))).scale.value == 0.0
    with pytest.raises(ValidationError) as exc:
        s.EasydiffractionPhase(**_phase(scale=P(-1.0)))
    assert "scale must be >= 0" in str(exc.value)


def test_phase_rhombohedral_rejected():
    with pytest.raises(ValidationError) as exc:
        s.EasydiffractionPhase(**_phase("R -3 c:R", cell=(4.15, 4.15, 4.15, 90.0, 90.0, 120.0)))
    assert "rhombohedral axes" in str(exc.value) and ":H" in str(exc.value)


def test_phase_negative_uiso_and_uij_rejected():
    with pytest.raises(ValidationError) as exc:
        s.EasydiffractionPhase(**_phase(atoms={"Fe1": _atom(Uiso=P(-0.01))}))
    assert "Uiso starting value must be >= 0" in str(exc.value)
    aniso = {"U11": P(0.01), "U22": P(0.01), "U33": P(-0.01), "U12": P(0.0), "U13": P(0.0), "U23": P(0.0)}
    atom = _atom(ADP="Uaniso", Uaniso=aniso)
    atom.pop("Uiso")
    with pytest.raises(ValidationError) as exc:
        s.EasydiffractionPhase(**_phase(atoms={"Fe1": atom}))
    assert "U33 starting value must be >= 0" in str(exc.value)


def test_phase_with_atom_refinement():
    """Phase with refinable atoms (A116)."""
    phase = s.EasydiffractionPhase(**_phase(
        cell=(4.15682,) * 3 + (90.0,) * 3,
        atoms={"La1": _atom("La", occupancy=P(1.0, True, 0.0, 1.0), Uiso=P(0.005, True, 0.0, 0.1)),
               "B1": _atom("B", (0.19827, 0.5, 0.5), x=P(0.19827, True, 0.0, 0.5))},
        scale=P(1.0, True, 0.0, 1e6)))
    assert phase.scale.refine_flag is True
    assert phase.atoms["La1"].occupancy.refine_flag is True and phase.atoms["B1"].x.refine_flag is True


def test_phase_has_no_background():
    """The background is the experiment's (A189): a per-phase block is refused."""
    with pytest.raises(ValidationError) as exc:
        s.EasydiffractionPhase(**_phase(), background={"chebyshev": {"num_coefficients": 1, "coefficients": [0.0],
                                                                      "refine_flag": False}})
    assert exc.value.errors()[0]["type"] == "extra_forbidden"


# --- payload rules ------------------------------------------------------------


def test_crysfml_uaniso_rejected_at_the_atom():
    """CrysFML + Uaniso is an error (A169 a, EB-23), located at the atom's ADP."""
    aniso = {"U11": P(0.01), "U22": P(0.01), "U33": P(0.01), "U12": P(0.0), "U13": P(0.0), "U23": P(0.0)}
    atom = _atom(ADP="Uaniso", Uaniso=aniso)
    atom.pop("Uiso")
    recipe = _recipe(phases={"TestPhase": _phase(atoms={"Fe1": atom})}, refinement_controls=_controls("crysfml"))
    errors = _errors(recipe)
    assert [loc for loc, _ in errors] == [("payload", "phases", "TestPhase", "atoms", "Fe1", "ADP")]
    assert "CrysFML calculator does not support anisotropic thermal parameters" in errors[0][1]
    assert "Use ADP='Uiso'" in errors[0][1]
    recipe["payload"]["refinement_controls"]["calculator"] = "cryspy"
    s.validate_recipe(recipe)


def test_negative_intensity_on_a_handed_point_rejected_at_the_point():
    """A177, EB-91: located at xrd_data.Itth[i]; points outside the window or weightless are not checked."""
    data = {"tth": [10.0, 20.0, 30.0, 40.0], "Itth": [100.0, -50.0, 150.0, -1.0], "Itth_weights": [1.0, 1.0, 1.0, 1.0]}
    errors = _errors(_recipe(xrd_data=data, fit_range=[10.0, 40.0]))
    assert [loc for loc, _ in errors] == [("payload", "xrd_data", "Itth", 1), ("payload", "xrd_data", "Itth", 3)]
    assert "Negative intensities in fit window" in errors[0][1] and "easydiffraction rejects negative intensity data" in errors[0][1]
    s.validate_recipe(_recipe(xrd_data=data, fit_range=[25.0, 35.0]))  # outside the window
    data["Itth_weights"] = [1.0, 0.0, 1.0, 0.0]
    s.validate_recipe(_recipe(xrd_data=data, fit_range=[10.0, 40.0]))  # weightless


def test_weight_above_1e8_on_a_handed_point_rejected(caplog):
    """A159, EB-73: sigma < 1e-4 would be replaced by 1.0 silently."""
    data = {"tth": [10.0, 20.0, 30.0], "Itth": [100.0, 200.0, 150.0], "Itth_weights": [1.0, 2e8, 1.0]}
    errors = _errors(_recipe(xrd_data=data))
    assert [loc for loc, _ in errors] == [("payload", "xrd_data", "Itth_weights", 1)]
    assert "rescale the intensities and weights" in errors[0][1]
    data["Itth_weights"] = [1.0, 1e8, 1.0]
    s.validate_recipe(_recipe(xrd_data=data))  # at the limit is fine
    data["Itth_weights"] = [1.0, 2e8, 1.0]
    s.validate_recipe(_recipe(xrd_data=data, fit_range=[25.0, 30.0]))  # not handed


def test_fit_range_optional_never_null_and_within_data():
    recipe = _recipe()
    recipe["payload"].pop("fit_range")
    model = s.validate_recipe(recipe)
    assert model.payload.fit_range is None and "fit_range" not in model.payload.model_dump()
    assert [loc for loc, msg in _errors(_recipe(fit_range=None)) if "leave it out" in msg] == [("payload", "fit_range")]
    errors = _errors(_recipe(fit_range=[10.0, 50.0]))
    assert [loc for loc, _ in errors] == [("payload", "fit_range")] and "outside the data" in errors[0][1]


def test_bounds_cell_over_30_angstrom():
    """Cell length > 30 Å with null max is rejected (A138, EB-71)."""
    recipe = _recipe(phases={"TestPhase": _phase("P 1", cell=(35.0, 4.15, 4.15, 90.0, 90.0, 90.0))})
    recipe["payload"]["phases"]["TestPhase"]["unit_cell"]["a"] = P(35.0, True, None, None)
    with pytest.raises(ValidationError) as exc:
        s.validate_recipe(recipe)
    assert "cell length a = 35.0 Å > 30 Å" in str(exc.value)
    assert "easydiffraction cannot model cells > 30 Å" in str(exc.value)


def test_bounds_occupancy_over_1():
    """Occupancy > 1.0 is rejected by core (A76) before easydiffraction's limit (EB-22) applies."""
    recipe = _recipe(phases={"TestPhase": _phase(atoms={"Fe1": _atom(occupancy=P(1.5, True, None, None))})})
    with pytest.raises(ValidationError) as exc:
        s.validate_recipe(recipe)
    assert "occupancy must be between 0 and 1" in str(exc.value)


def test_simulation_recipe_nothing_flagged_is_allowed():
    from powderline.schema_core import parameters_requested

    assert parameters_requested(s.validate_recipe(_recipe())) == 0


def test_unknown_native_workflow_reported_at_schema_name():
    recipe = _recipe()
    recipe["schema_name"] = "easydiffraction.reitveld"
    errors = _errors(recipe)
    assert [loc for loc, _ in errors] == [("schema_name",)] and "easydiffraction.rietveld" in errors[0][1]
    assert s.is_native_recipe(recipe) and not s.is_native_recipe({"schema_name": "GSASII_Rietveld"})
