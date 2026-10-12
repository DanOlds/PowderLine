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
