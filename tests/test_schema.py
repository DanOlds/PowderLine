"""Direct negative/positive tests for powderline.schema validators.

schema.py is pure pydantic (no GSAS-II import) so these run anywhere. Most
validators are exercised by constructing the small standalone sub-models
directly; the positive case validates a real schema-0.26 example recipe.
"""
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from powderline.schema import (
    EXPECTED_SCHEMA_VERSION,
    PayloadModel,
    RecipeModel,
    SinglePeaks,
    SinglePeaksBackground,
    UnitCellParameters,
    XRDDataModel,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _param(value):
    """Build a [value, refine_flag, min, max] RefinementParameter tuple."""
    return (value, False, None, None)


# --- validate_cell_lengths ---

@pytest.mark.parametrize("bad", [0.0, -1.0, -5.4])
def test_cell_length_must_be_positive(bad):
    with pytest.raises(ValidationError, match="must be positive"):
        UnitCellParameters(a=_param(bad))


def test_cell_length_positive_ok():
    m = UnitCellParameters(a=_param(4.15))
    assert m.a[0] == 4.15


# --- validate_cell_angles ---

@pytest.mark.parametrize("bad", [0.0, 180.0, 200.0, -30.0])
def test_cell_angle_out_of_range(bad):
    with pytest.raises(ValidationError, match="between 0 and 180"):
        UnitCellParameters(beta=_param(bad))


def test_cell_angle_in_range_ok():
    m = UnitCellParameters(alpha=_param(90.0), beta=_param(120.0))
    assert m.beta[0] == 120.0


# --- validate_fit_range (length check) ---

def _minimal_payload_kwargs():
    """Minimal valid kwargs for PayloadModel (xrd_data + refinement_controls)."""
    return {
        "xrd_data": {"tth": [1.0, 2.0], "Itth": [10.0, 20.0], "Itth_weights": [1.0, 1.0]},
        "refinement_controls": {"refinement_cycles": 3},
    }


def test_fit_range_wrong_length_raises():
    kwargs = _minimal_payload_kwargs()
    kwargs["fit_range"] = [5.0, 10.0, 15.0]  # length 3, must be 2
    with pytest.raises(ValidationError, match="exactly 2 elements"):
        PayloadModel(**kwargs)


def test_fit_range_length_two_ok():
    kwargs = _minimal_payload_kwargs()
    kwargs["fit_range"] = [5.0, 40.0]
    payload = PayloadModel(**kwargs)
    assert payload.fit_range == [5.0, 40.0]


# --- validate_all_arrays_same_length (XRDDataModel) ---

def test_xrd_arrays_length_mismatch_raises():
    with pytest.raises(ValidationError, match="same length"):
        XRDDataModel(tth=[1.0, 2.0, 3.0], Itth=[10.0, 20.0], Itth_weights=[1.0, 1.0])


def test_xrd_arrays_equal_length_ok():
    m = XRDDataModel(tth=[1.0, 2.0], Itth=[10.0, 20.0], Itth_weights=[1.0, 1.0])
    assert len(m.tth) == 2


# --- validate_arrays_wellformed (XRDDataModel) ---

def test_xrd_empty_arrays_raise():
    with pytest.raises(ValidationError, match="non-empty"):
        XRDDataModel(tth=[], Itth=[], Itth_weights=[])


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_xrd_non_finite_tth_raises(bad):
    with pytest.raises(ValidationError, match="non-finite"):
        XRDDataModel(tth=[1.0, bad, 3.0], Itth=[10.0, 20.0, 30.0], Itth_weights=[1.0, 1.0, 1.0])


def test_xrd_non_finite_intensity_raises():
    with pytest.raises(ValidationError, match="non-finite"):
        XRDDataModel(tth=[1.0, 2.0], Itth=[10.0, float("nan")], Itth_weights=[1.0, 1.0])


def test_xrd_infinite_weight_raises():
    # e.g. a weight computed as 1/esd**2 with esd == 0
    with pytest.raises(ValidationError, match="non-finite"):
        XRDDataModel(tth=[1.0, 2.0], Itth=[10.0, 20.0], Itth_weights=[1.0, float("inf")])


@pytest.mark.parametrize(
    "tth",
    [
        [1.0, 1.0, 2.0],   # duplicate -> not strictly increasing
        [1.0, 3.0, 2.0],   # out of order
        [3.0, 2.0, 1.0],   # descending
    ],
)
def test_xrd_non_monotonic_tth_raises(tth):
    with pytest.raises(ValidationError, match="strictly increasing"):
        XRDDataModel(tth=tth, Itth=[10.0, 20.0, 30.0], Itth_weights=[1.0, 1.0, 1.0])


def test_xrd_negative_weight_raises():
    with pytest.raises(ValidationError, match=">= 0"):
        XRDDataModel(tth=[1.0, 2.0], Itth=[10.0, 20.0], Itth_weights=[1.0, -0.5])


def test_xrd_all_zero_weights_raise():
    with pytest.raises(ValidationError, match="all zero"):
        XRDDataModel(tth=[1.0, 2.0], Itth=[10.0, 20.0], Itth_weights=[0.0, 0.0])


def test_xrd_zero_weight_point_ok():
    # A single 0 weight is fine (excludes that point) as long as one weight is > 0.
    m = XRDDataModel(tth=[1.0, 2.0], Itth=[10.0, 20.0], Itth_weights=[0.0, 1.0])
    assert m.Itth_weights == [0.0, 1.0]


def test_xrd_negative_intensity_ok():
    # Background-subtracted data legitimately dips below zero: intensities are
    # checked for finiteness only, never sign. This must validate.
    m = XRDDataModel(tth=[1.0, 2.0, 3.0], Itth=[-5.0, 20.0, -1.0], Itth_weights=[1.0, 1.0, 1.0])
    assert m.Itth[0] == -5.0


def test_xrd_single_point_ok():
    # A one-point pattern is degenerate but well-formed: the strictly-increasing
    # check must be vacuously satisfied (np.diff of one element is empty).
    m = XRDDataModel(tth=[10.0], Itth=[100.0], Itth_weights=[2.0])
    assert m.tth == [10.0]


def test_xrd_nan_weight_raises():
    # Non-finiteness in Itth_weights specifically covers NaN, not just inf.
    with pytest.raises(ValidationError, match="non-finite"):
        XRDDataModel(tth=[1.0, 2.0], Itth=[10.0, 20.0], Itth_weights=[1.0, float("nan")])


def test_xrd_negative_zero_weight_ok():
    # -0.0 compares equal to 0.0: it is an exclusion marker, not a negative weight.
    m = XRDDataModel(tth=[1.0, 2.0], Itth=[10.0, 20.0], Itth_weights=[-0.0, 1.0])
    assert m.Itth_weights[0] == 0.0


def test_xrd_all_negative_zero_weights_raise():
    # ... but all -0.0 weights still means nothing to fit.
    with pytest.raises(ValidationError, match="all zero"):
        XRDDataModel(tth=[1.0, 2.0], Itth=[10.0, 20.0], Itth_weights=[-0.0, -0.0])


# --- peak-list length validators ---

def test_single_peaks_mismatched_lengths_raise():
    with pytest.raises(ValidationError, match="same length"):
        SinglePeaks(
            positions=[_param(10.0), _param(20.0)],
            intensities=[_param(100.0)],
        )


def test_single_peaks_background_mismatched_lengths_raise():
    with pytest.raises(ValidationError, match="same length"):
        SinglePeaksBackground(
            positions=[_param(10.0), _param(20.0)],
            intensities=[_param(100.0)],
        )


def test_single_peaks_matched_lengths_ok():
    m = SinglePeaks(
        positions=[_param(10.0), _param(20.0)],
        intensities=[_param(100.0), _param(50.0)],
    )
    assert len(m.positions) == 2


# --- schema_version / schema_name guards ---

def test_wrong_schema_version_raises():
    example = REPO_ROOT / "tests" / "data" / "legacy_026" / "example_LaB6" / "input.json"
    recipe = json.loads(example.read_text())
    recipe["schema_version"] = "0.24"
    with pytest.raises(ValidationError, match="Schema version mismatch"):
        RecipeModel.model_validate(recipe)


def test_missing_schema_version_raises():
    example = REPO_ROOT / "tests" / "data" / "legacy_026" / "example_LaB6" / "input.json"
    recipe = json.loads(example.read_text())
    del recipe["schema_version"]
    with pytest.raises(ValidationError, match="schema_version"):
        RecipeModel.model_validate(recipe)


def test_spf_recipe_missing_single_peaks_raises():
    """A GSASII_SPF recipe without the required single_peaks field is rejected."""
    recipe = {
        "schema_name": "GSASII_SPF",
        "schema_version": EXPECTED_SCHEMA_VERSION,
        "payload": {
            "xrd_data": {
                "tth": [10.0, 11.0, 12.0, 13.0, 14.0],
                "Itth": [100.0, 150.0, 200.0, 120.0, 90.0],
                "Itth_weights": [1.0, 1.0, 1.0, 1.0, 1.0],
            },
            "instrument": {
                "description": "Test instrument",
                "initialization": [{"Type": ["PXC", "PXC", False]}, {}],
            },
            "refinement_controls": {"refinement_cycles": 1},
            # Missing single_peaks field required for GSASII_SPF!
        },
    }
    with pytest.raises(ValidationError, match="single_peaks"):
        RecipeModel.model_validate(recipe)


# --- positive: a real schema-0.26 recipe validates end to end ---

def test_valid_example_recipe_validates():
    example = REPO_ROOT / "tests" / "data" / "legacy_026" / "example_LaB6" / "input.json"
    recipe = json.loads(example.read_text())
    assert recipe["schema_version"] == EXPECTED_SCHEMA_VERSION
    model = RecipeModel.model_validate(recipe)
    assert model.schema_name == "GSASII_Rietveld"
    assert model.payload.phases is not None


# --- validate_simulation_mode_parameters (public API; backed by kicker.py) ---
#
# Unlike the pure-pydantic tests above, the simulation-mode validator lives in
# powderline.kicker, which imports GSAS-II at module top; skip (rather than
# fail) where GSAS-II is unavailable so the rest of this file still runs.

@pytest.fixture
def validate_simulation_mode_parameters():
    import powderline

    if not hasattr(powderline, "validate_simulation_mode_parameters"):
        pytest.skip("powderline.validate_simulation_mode_parameters requires GSAS-II")
    return powderline.validate_simulation_mode_parameters


def _load_simulation_recipe_dict():
    example = REPO_ROOT / "tests" / "data" / "legacy_026" / "example_LaB6_simulation" / "input.json"
    return json.loads(example.read_text())


def test_simulation_mode_valid_recipe_passes(validate_simulation_mode_parameters):
    """A properly-locked simulation recipe (refinement_cycles=1) is valid."""
    model = RecipeModel.model_validate(_load_simulation_recipe_dict())
    is_valid, warnings = validate_simulation_mode_parameters(model)
    assert is_valid is True
    assert warnings == []


def test_non_simulation_recipe_skips_simulation_checks(validate_simulation_mode_parameters):
    """refinement_cycles > 1 is not simulation mode: refine flags are allowed."""
    example = REPO_ROOT / "tests" / "data" / "legacy_026" / "example_LaB6" / "input.json"
    recipe = json.loads(example.read_text())
    # sanity: the standard LaB6 example really does refine parameters
    assert recipe["payload"]["refinement_controls"]["refinement_cycles"] > 1
    assert recipe["payload"]["phases"]["LaB6"]["parameterization"]["scale"][1] is True
    model = RecipeModel.model_validate(recipe)
    is_valid, warnings = validate_simulation_mode_parameters(model)
    assert is_valid is True
    assert warnings == []


def _violate_scale(recipe_dict):
    recipe_dict["payload"]["phases"]["LaB6"]["parameterization"]["scale"][1] = True


def _violate_chebyshev(recipe_dict):
    recipe_dict["payload"]["background"]["chebyshev"]["refine_flag"] = True


def _violate_wavelength(recipe_dict):
    instrument = recipe_dict["payload"]["instrument"]
    if instrument.get("parameterization") is None:
        instrument["parameterization"] = {}
    instrument["parameterization"]["wavelength"] = [0.1665, True, None, None]


@pytest.mark.parametrize(
    "mutate, marker",
    [(_violate_scale, "scale"), (_violate_chebyshev, "chebyshev"),
     (_violate_wavelength, "wavelength")],
    ids=["scale", "chebyshev", "wavelength"],
)
def test_simulation_mode_refine_violation_fails(validate_simulation_mode_parameters,
                                                mutate, marker):
    """A single refine_flag=true in simulation mode -> (False, [violation, ...])."""
    recipe = _load_simulation_recipe_dict()
    mutate(recipe)
    model = RecipeModel.model_validate(recipe)
    is_valid, warnings = validate_simulation_mode_parameters(model)
    assert is_valid is False
    assert warnings, "expected at least one violation message"
    assert all(isinstance(w, str) for w in warnings)
    assert any(marker in w.lower() for w in warnings), (
        f"no violation message mentions {marker!r}: {warnings}"
    )
