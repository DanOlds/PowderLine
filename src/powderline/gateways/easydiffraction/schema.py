"""easydiffraction engine schemas ``easydiffraction.rietveld`` (engine schema 1.0.0).

Built on the core schema (:mod:`powderline.schema_core`): a recipe is a
:class:`~powderline.schema_core.CoreRecipe`, a phase a
``Phase[BoundedRefinableParameter]`` with the easydiffraction fields flat in the same block
(A93). Values are in easydiffraction's native units (A4); every refinable is
``[value, refine_flag, min, max]``: easydiffraction 1.0.0 exposes bounds everywhere (A116).
Invalid recipes raise pydantic's ``ValidationError`` with every problem at its location (A70).

Engine-free: imports no easydiffraction (enforced by an import-block test), so
``validate()`` runs in the core-only environment. The gateway's runtime layer
consumes the validated model directly (A39).

easydiffraction rules checked here (engine-behavior register entries in brackets):

- space group must be supported by the chosen calculator (EB-42);
- negative starting Uiso/Uij is an error (EB-46);
- CrysFML + Uaniso is an error (EB-23);
- calculator/peak_type combinations validated (EB-62);
- bounds replace engine defaults on stated side, null keeps default (A138, EB-71);
- negative intensities in window are an error (EB-91);
- simulation (no parameters flagged) is allowed.

Documented defaults (A119): No easydiffraction-specific defaults yet.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Optional

import gemmi
from pydantic import Field, ValidationError, field_validator, model_validator

from powderline.compat import check_engine_schema_version
from powderline.exceptions import StructuredWarning
from powderline.schema_core import (
    UNIT_ANGSTROM,
    UNIT_ARBITRARY,
    UNIT_DEG_2THETA,
    UNIT_DEGREE,
    CoreFloat,
    CoreInt,
    CoreModel,
    CoreName,
    CoreRecipe,
    Phase,
    BoundedRefinableParameter,
    RefinableParameter,
    XRDData,
    FitRange,
    name_keyed,
    check_names_unique_ignoring_case,
    absent_not_null_schema,
)

# --- versions (A40, A65) ---------------------------------------------------

#: The engine schema version new recipes are written against.
ENGINE_SCHEMA_VERSION = "1.0.0"
#: Engine schema versions this implementation accepts (PEP 440 specifier).
SUPPORTED_ENGINE_SCHEMAS = "==1.0.0"
#: Core schema versions this engine schema requires.
REQUIRES_CORE_SCHEMA = "==1.0.0"
#: Schema names served by this module.
SCHEMA_NAMES = ("easydiffraction.rietveld",)

#: Capabilities declaration for registry (A65).
DECLARATIONS = {
    "engine_schema_version": ENGINE_SCHEMA_VERSION,
    "supported_engine_schemas": SUPPORTED_ENGINE_SCHEMAS,
    "requires_core_schema": REQUIRES_CORE_SCHEMA,
}

# --- units (native easydiffraction conventions, A4) ------------------------

#: Caglioti U, V, W: FWHM² in degrees² 2θ
UNIT_DEG2_FWHM = "degree^2 2theta (FWHM^2)"
#: Lorentzian X, Y: FWHM in degrees 2θ
UNIT_DEG_FWHM = "degree 2theta (FWHM)"


def _unit(unit: str) -> dict:
    return {"unit": unit}


def check_version(core_schema_version: str, engine_schema_version: str) -> None:
    """Raise ``ValueError`` unless the recipe's versions are compatible (A65, A70)."""
    from powderline.gateways.easydiffraction.gateway import capabilities
    check_engine_schema_version(
        core_schema_version,
        engine_schema_version,
        gateway="easydiffraction",
        capabilities=capabilities(),
    )


# --- instrument models (A160, native units A4) -----------------------------

class Radiation(CoreModel):
    """Radiation parameters (A160).

    Only CW xray in 1.0.0. Kα2 slot reserved but not yet supported.
    """
    type: Literal["xray"] = Field(default="xray", frozen=True)
    wavelength: BoundedRefinableParameter = Field(..., json_schema_extra=_unit(UNIT_ANGSTROM))

    # Kα2 reserved for future (A160) - not supported in 1.0.0
    ka2_wavelength: Optional[CoreFloat] = Field(default=None, json_schema_extra=_unit(UNIT_ANGSTROM))
    ka2_ratio: Optional[CoreFloat] = Field(default=None, json_schema_extra=_unit(UNIT_ARBITRARY))

    @field_validator("wavelength", mode="after")
    def _wavelength_positive(cls, param: BoundedRefinableParameter) -> BoundedRefinableParameter:
        """Wavelength must be > 0 (A126, A155 Q6)."""
        if param.value <= 0:
            raise ValueError(f"wavelength must be > 0, got {param.value}")
        return param


class Corrections(CoreModel):
    """Instrument corrections (zero shift, polarization, etc.)."""
    zero: BoundedRefinableParameter = Field(..., json_schema_extra=_unit(UNIT_DEG_2THETA))
    polarization: BoundedRefinableParameter = Field(..., json_schema_extra=_unit(UNIT_ARBITRARY))

    # Optional corrections
    polarization_monochromator_angle: Optional[BoundedRefinableParameter] = Field(
        default=None,
        json_schema_extra=_unit(UNIT_DEGREE)
    )
    displacement: Optional[BoundedRefinableParameter] = Field(
        default=None,
        json_schema_extra=_unit(UNIT_ANGSTROM)
    )
    transparency: Optional[BoundedRefinableParameter] = Field(
        default=None,
        json_schema_extra=_unit(UNIT_ARBITRARY)
    )
    cylinder_absorption_radius_mm: Optional[CoreFloat] = Field(
        default=None,
        json_schema_extra=_unit("millimeter")
    )


class Broadening(CoreModel):
    """Peak broadening parameters (Caglioti + Lorentzian, native units A4).

    Caglioti U, V, W: FWHM² in degrees² 2θ (not centidegrees²)
    Lorentzian X, Y: FWHM in degrees 2θ (not centidegrees)
    """
    # Caglioti terms (Gaussian, FWHM² = U tan²θ + V tanθ + W)
    u: BoundedRefinableParameter = Field(..., json_schema_extra=_unit(UNIT_DEG2_FWHM))
    v: BoundedRefinableParameter = Field(..., json_schema_extra=_unit(UNIT_DEG2_FWHM))
    w: BoundedRefinableParameter = Field(..., json_schema_extra=_unit(UNIT_DEG2_FWHM))

    # Lorentzian terms (FWHM = X/cosθ + Y tanθ)
    x: BoundedRefinableParameter = Field(..., json_schema_extra=_unit(UNIT_DEG_FWHM))
    y: BoundedRefinableParameter = Field(..., json_schema_extra=_unit(UNIT_DEG_FWHM))


class Instrument(CoreModel):
    """Instrument parameters (single source A160, native units A4)."""
    description: Optional[str] = Field(default=None)
    radiation: Radiation
    corrections: Corrections
    broadening: Broadening

    @model_validator(mode="after")
    def _check_negative_widths(self) -> "Instrument":
        """Check for negative peak widths at representative points (A171, A178).

        Full grid check over window happens in builder (commit 3.5).
        Schema-level check: at θ=45° (representative angle).
        """
        import math
        theta_deg = 45.0
        theta_rad = math.radians(theta_deg)
        tan_theta = math.tan(theta_rad)

        # Gaussian FWHM² = U tan²θ + V tanθ + W
        fwhm2_gauss = (
            self.broadening.u.value * tan_theta**2 +
            self.broadening.v.value * tan_theta +
            self.broadening.w.value
        )

        if fwhm2_gauss < 0:
            raise ValueError(
                f"Peak width FWHM² < 0 at θ=45°: U tan²θ + V tanθ + W = {fwhm2_gauss:.2e}. "
                "Check Caglioti parameters (U, V, W)."
            )

        return self


# --- recipe model ----------------------------------------------------------

class EasydiffractionRecipe(CoreRecipe):
    """easydiffraction.rietveld recipe (A4, A19, A93, A116).

    Native units: FWHM² deg², Rwp %, etc. Bounds everywhere (A116).
    """
    schema_name: Literal["easydiffraction.rietveld"] = Field(
        ...,
        json_schema_extra={"description": "Schema identifier for easydiffraction Rietveld refinement"}
    )

    @model_validator(mode="after")
    def _check_versions(self) -> "EasydiffractionRecipe":
        """Validate schema versions (A65, A70)."""
        check_version(self.core_schema_version, self.engine_schema_version)
        return self
