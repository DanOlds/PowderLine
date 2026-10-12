"""Schema validation tests for easydiffraction.rietveld."""
import pytest
from pydantic import ValidationError

from powderline.gateways.easydiffraction.schema import (
    EasydiffractionRecipe,
    ENGINE_SCHEMA_VERSION,
)


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
    """Version constants are defined."""
    assert ENGINE_SCHEMA_VERSION == "1.0.0"


def test_imports_no_easydiffraction():
    """Schema imports no easydiffraction (engine-free)."""
    import sys
    import powderline.gateways.easydiffraction.schema

    # Should be importable without easydiffraction installed
    assert "easydiffraction" not in sys.modules


def test_instrument_wavelength_positive():
    """Wavelength must be > 0 (A126)."""
    from powderline.gateways.easydiffraction.schema import Radiation

    with pytest.raises(ValidationError) as exc:
        Radiation(wavelength=[0.0, False, None, None])  # Zero wavelength
    assert "wavelength must be > 0" in str(exc.value)

    with pytest.raises(ValidationError) as exc:
        Radiation(wavelength=[-0.5, False, None, None])  # Negative wavelength
    assert "wavelength must be > 0" in str(exc.value)


def test_instrument_negative_width():
    """Negative peak width detected at representative angle (A171, A178)."""
    from powderline.gateways.easydiffraction.schema import Instrument, Radiation, Corrections, Broadening

    # U=-100 gives negative FWHM² at 45°
    with pytest.raises(ValidationError) as exc:
        Instrument(
            radiation=Radiation(wavelength=[1.5406, False, None, None]),
            corrections=Corrections(
                zero=[0.0, False, None, None],
                polarization=[0.99, False, None, None]
            ),
            broadening=Broadening(
                u=[-100.0, False, None, None],  # Negative!
                v=[0.0, False, None, None],
                w=[1.0, False, None, None],
                x=[0.0, False, None, None],
                y=[0.0, False, None, None]
            )
        )
    assert "FWHM² < 0" in str(exc.value)


def test_instrument_valid():
    """Valid instrument parameters accepted."""
    from powderline.gateways.easydiffraction.schema import Instrument, Radiation, Corrections, Broadening

    inst = Instrument(
        description="Cu Kα",
        radiation=Radiation(wavelength=[1.5406, False, None, None]),
        corrections=Corrections(
            zero=[0.0, True, -0.1, 0.1],
            polarization=[0.99, False, 0.0, 1.0]
        ),
        broadening=Broadening(
            u=[18.7, True, 0.0, 100.0],
            v=[0.6, True, -50.0, 50.0],
            w=[1.1, True, 0.0, 10.0],
            x=[0.28, True, 0.0, 5.0],
            y=[0.001, True, 0.0, 1.0]
        )
    )

    assert inst.radiation.wavelength.value == 1.5406
    assert inst.broadening.u.value == 18.7


def test_phase_scale_nonnegative():
    """Scale must be >= 0 (A161)."""
    from powderline.gateways.easydiffraction.schema import EasydiffractionPhase
    from powderline.schema_core import Atom, BoundedRefinableParameter

    with pytest.raises(ValidationError) as exc:
        EasydiffractionPhase(
            space_group="P m -3 m",
            unit_cell={
                "a": [4.15, False, None, None],
                "b": [4.15, False, None, None],
                "c": [4.15, False, None, None],
                "alpha": [90.0, False, None, None],
                "beta": [90.0, False, None, None],
                "gamma": [90.0, False, None, None],
            },
            atoms={
                "dummy": Atom[BoundedRefinableParameter](
                    element="Fe",
                    x=[0.0, False, None, None],
                    y=[0.0, False, None, None],
                    z=[0.0, False, None, None],
                    occupancy=[1.0, False, None, None],
                    ADP="Uiso",
                    Uiso=[0.01, False, None, None],
                )
            },
            scale=[-1.0, False, None, None],  # Negative!
        )
    assert "scale must be >= 0" in str(exc.value)


def test_phase_rhombohedral_rejected():
    """Rhombohedral axes :R rejected (easydiffraction doesn't support)."""
    from powderline.gateways.easydiffraction.schema import EasydiffractionPhase
    from powderline.schema_core import Atom, BoundedRefinableParameter

    with pytest.raises(ValidationError) as exc:
        EasydiffractionPhase(
            space_group="R -3 c:R",  # Rhombohedral!
            unit_cell={
                "a": [4.15, False, None, None],
                "b": [4.15, False, None, None],
                "c": [4.15, False, None, None],
                "alpha": [90.0, False, None, None],
                "beta": [90.0, False, None, None],
                "gamma": [120.0, False, None, None],
            },
            atoms={
                "dummy": Atom[BoundedRefinableParameter](
                    element="Fe",
                    x=[0.0, False, None, None],
                    y=[0.0, False, None, None],
                    z=[0.0, False, None, None],
                    occupancy=[1.0, False, None, None],
                    ADP="Uiso",
                    Uiso=[0.01, False, None, None],
                )
            },
            scale=[1.0, False, None, None],
        )
    assert "rhombohedral axes" in str(exc.value)
    assert ":H" in str(exc.value)  # Suggests hexagonal


def test_phase_negative_uiso_rejected():
    """Negative starting Uiso rejected (A111, EB-46)."""
    from powderline.gateways.easydiffraction.schema import EasydiffractionPhase
    from powderline.schema_core import Atom, BoundedRefinableParameter

    with pytest.raises(ValidationError) as exc:
        EasydiffractionPhase(
            space_group="P m -3 m",
            unit_cell={
                "a": [4.15, False, None, None],
                "b": [4.15, False, None, None],
                "c": [4.15, False, None, None],
                "alpha": [90.0, False, None, None],
                "beta": [90.0, False, None, None],
                "gamma": [90.0, False, None, None],
            },
            atoms={
                "Fe1": Atom[BoundedRefinableParameter](
                    element="Fe",
                    x=[0.0, False, None, None],
                    y=[0.0, False, None, None],
                    z=[0.0, False, None, None],
                    occupancy=[1.0, False, None, None],
                    ADP="Uiso",
                    Uiso=[-0.01, False, None, None],  # Negative!
                )
            },
            scale=[1.0, False, None, None],
        )
    assert "Uiso starting value must be >= 0" in str(exc.value)


def test_phase_with_atom_refinement():
    """Phase with refinable atoms (A116)."""
    from powderline.gateways.easydiffraction.schema import EasydiffractionPhase
    from powderline.schema_core import Atom, BoundedRefinableParameter

    phase = EasydiffractionPhase(
        space_group="P m -3 m",
        unit_cell={
            # Cubic: a, b, c must all have same refine flag and bounds
            "a": [4.15682, True, 4.0, 4.5],
            "b": [4.15682, True, 4.0, 4.5],
            "c": [4.15682, True, 4.0, 4.5],
            "alpha": [90.0, False, None, None],
            "beta": [90.0, False, None, None],
            "gamma": [90.0, False, None, None],
        },
        atoms={
            "La1": Atom[BoundedRefinableParameter](
                element="La",
                x=[0.0, False, None, None],
                y=[0.0, False, None, None],
                z=[0.0, False, None, None],
                occupancy=[1.0, True, 0.0, 1.0],  # Refinable occupancy!
                ADP="Uiso",
                Uiso=[0.005, True, 0.0, 0.1],  # Refinable Uiso!
            ),
            "B1": Atom[BoundedRefinableParameter](
                element="B",
                x=[0.5, False, None, None],
                y=[0.5, False, None, None],
                z=[0.19827, True, 0.0, 0.5],  # Refinable z!
                occupancy=[1.0, False, None, None],
                ADP="Uiso",
                Uiso=[0.003, False, None, None],
            ),
        },
        scale=[1.0, True, 0.0, 1e6],  # Refinable scale
    )

    assert phase.scale.value == 1.0
    assert phase.scale.refine_flag is True
    assert phase.atoms["La1"].occupancy.refine_flag is True
    assert phase.atoms["B1"].z.refine_flag is True
