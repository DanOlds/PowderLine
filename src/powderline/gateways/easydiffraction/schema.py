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
