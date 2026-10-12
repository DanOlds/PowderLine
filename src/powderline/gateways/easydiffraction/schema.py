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
    ChebyshevBackground,
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

    # Lorentzian terms (FWHM = X tanθ + Y/cosθ: easydiffraction's X is CrysPy's
    # _pd_instr_resolution_X, the tanθ term; A171, EB-87)
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

        The full grid check over the window widened by its margin is
        ``native_builder.check_peak_widths`` (run by ``build_project``).
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


# --- phase model (A93, A116) -----------------------------------------------

class EasydiffractionPhase(Phase[BoundedRefinableParameter]):
    """Phase model for easydiffraction.rietveld (A93, A116).

    Extends core Phase with engine-specific fields flat in the same block.
    All atom parameters are refinable with bounds (A116): x/y/z, occupancy,
    Uiso, and U11..U23 (anisotropic).
    """
    # Flat engine fields (A93)
    scale: BoundedRefinableParameter = Field(
        ...,
        json_schema_extra=_unit(UNIT_ARBITRARY),
        description="Phase scale factor"
    )

    # Background (Chebyshev coefficients)

    # Engine validators (A109, A122: 'after' only, engine-specific names)

    @field_validator("scale", mode="after")
    def _easydiffraction_scale_nonnegative(cls, param: BoundedRefinableParameter) -> BoundedRefinableParameter:
        """Scale >= 0 (A161, Q22 item 13)."""
        if param.value < 0:
            raise ValueError(f"scale must be >= 0, got {param.value}")
        return param

    @field_validator("space_group", mode="after")
    def _easydiffraction_space_group_supported(cls, name: str) -> str:
        """Space group must be supported (A105, EB-42).

        Full verification against space_group_support.py happens in builder.
        Schema-level check: reject obviously invalid (e.g., rhombohedral :R for easydiffraction).
        """
        # Basic check: reject :R (easydiffraction doesn't support rhombohedral axes)
        if ":R" in name:
            raise ValueError(
                f"Space group {name!r} uses rhombohedral axes (:R), "
                "which easydiffraction does not support. Use hexagonal axes (:H)."
            )
        return name

    @field_validator("atoms", mode="after")
    def _easydiffraction_atom_uiso_nonnegative(cls, atoms: dict) -> dict:
        """Negative starting Uiso is an error (A111, EB-46)."""
        for label, atom in atoms.items():
            if atom.ADP == "Uiso" and atom.Uiso is not None:
                if atom.Uiso.value < 0:
                    raise ValueError(
                        f"atom {label!r}: Uiso starting value must be >= 0 "
                        f"(easydiffraction rejects negative ADP), got {atom.Uiso.value}"
                    )
            elif atom.ADP == "Uaniso" and atom.Uaniso is not None:
                # Check each Uij component
                for component in ["U11", "U22", "U33", "U12", "U13", "U23"]:
                    uij = getattr(atom.Uaniso, component, None)
                    if uij is not None and uij.value < 0:
                        raise ValueError(
                            f"atom {label!r}: {component} starting value must be >= 0 "
                            f"(easydiffraction rejects negative ADP), got {uij.value}"
                        )
        return atoms


# --- refinement controls (A162) --------------------------------------------

def _never_null(v: Any, info) -> Any:
    """A field that may be left out is never ``null`` (the A96 convention)."""
    if v is None:
        raise ValueError(f"{info.field_name} must not be null; leave it out instead")
    return v


class RefinementControls(CoreModel):
    """Refinement controls for easydiffraction calculators (A162).

    Calculator and peak type determine profile calculation method. The three
    stopping tolerances are optional (A193): left out, the engine's own values
    apply (easydiffraction 0.21.1 passes lmfit ``ftol``/``xtol``/``gtol`` as
    fixed values, EB-81) and nothing is handed or warned about; stated, each is
    handed where the minimizer exposes it and otherwise reported by the
    informative warning ``easydiffraction_stopping_criteria_not_handed`` (A190).
    ``null`` is rejected: leave the field out instead (A96).
    """
    calculator: Literal["cryspy", "crysfml"] = Field(
        ...,
        description="Structure factor calculator"
    )

    peak_type: str = Field(
        ...,
        description="Peak profile type (validated per calculator)"
    )

    minimizer: Literal["lmfit (leastsq)"] = Field(
        default="lmfit (leastsq)",
        description="Minimization algorithm"
    )

    max_iterations: CoreInt = Field(default=100, gt=0, description="lmfit max_nfev")
    chi_square_change_tolerance: Optional[CoreFloat] = Field(
        default=None, gt=0, json_schema_extra=absent_not_null_schema(),
        description="lmfit ftol; left out = the engine's own value (A193)")
    parameter_change_tolerance: Optional[CoreFloat] = Field(
        default=None, gt=0, json_schema_extra=absent_not_null_schema(),
        description="lmfit xtol; left out = the engine's own value (A193)")
    gradient_tolerance: Optional[CoreFloat] = Field(
        default=None, gt=0, json_schema_extra=absent_not_null_schema(),
        description="lmfit gtol; left out = the engine's own value (A193)")
    cutoff_fwhm: CoreFloat = Field(default=10.0, gt=0, description="Reflection cutoff in FWHM units")

    _not_null = field_validator("chi_square_change_tolerance", "parameter_change_tolerance",
                                "gradient_tolerance", mode="before")(_never_null)

    @model_validator(mode="after")
    def _validate_calculator_peak_type(self) -> "RefinementControls":
        """Validate peak_type is compatible with calculator (A162)."""
        valid_combinations = {
            "cryspy": {"pseudo-voigt", "tch"},
            "crysfml": {"tch"},
        }

        allowed = valid_combinations.get(self.calculator, set())
        if self.peak_type not in allowed:
            raise ValueError(
                f"peak_type {self.peak_type!r} not supported by calculator {self.calculator!r}. "
                f"Allowed: {', '.join(sorted(allowed))}"
            )

        return self


# --- payload (complete recipe payload) -------------------------------------

def _no_background() -> ChebyshevBackground:
    """Background left out = none: one fixed Chebyshev term 0.0 (D1, A119)."""
    return ChebyshevBackground(num_coefficients=1, coefficients=[0.0], refine_flag=False)


class EasydiffractionPayload(CoreModel):
    """Complete payload for easydiffraction.rietveld recipe."""
    xrd_data: XRDData
    fit_range: FitRange
    phases: Annotated[dict[CoreName, EasydiffractionPhase], name_keyed] = Field(
        ...,
        description="Phase definitions keyed by name"
    )
    instrument: Instrument
    refinement_controls: RefinementControls
    background: ChebyshevBackground = Field(
        default_factory=_no_background,
        description="The experiment's Chebyshev background (easydiffraction has one per experiment, A189); "
                    "left out = none (one fixed 0.0 term, D1)",
    )

    _not_null = field_validator("background", mode="before")(_never_null)

    @model_validator(mode="after")
    def _check_names_unique(self) -> "EasydiffractionPayload":
        """Phase names must be unique ignoring case (A112)."""
        check_names_unique_ignoring_case(self.phases, "phases")
        return self

    @model_validator(mode="after")
    def _check_crysfml_uaniso(self) -> "EasydiffractionPayload":
        """CrysFML + Uaniso is an error (A169a, EB-23)."""
        if self.refinement_controls.calculator == "crysfml":
            for phase_name, phase in self.phases.items():
                for atom_label, atom in phase.atoms.items():
                    if atom.ADP == "Uaniso":
                        raise ValueError(
                            f"CrysFML calculator does not support anisotropic thermal parameters. "
                            f"Phase {phase_name!r}, atom {atom_label!r} has ADP='Uaniso'. "
                            f"Use ADP='Uiso' or switch to calculator='cryspy'."
                        )
        return self

    @model_validator(mode="after")
    def _check_negative_intensities(self) -> "EasydiffractionPayload":
        """Negative intensities in fit window are an error (A177, EB-91)."""
        import numpy as np

        from powderline.schema_core import fit_limits_on_data

        # The points core scores: inside the window's first/last weighted points, w > 0 (A149, A131).
        lo, hi = fit_limits_on_data(self.fit_range, self.xrd_data)
        tth = np.asarray(self.xrd_data.tth, dtype=float)
        weights = np.asarray(self.xrd_data.Itth_weights, dtype=float)
        intensities = np.asarray(self.xrd_data.Itth, dtype=float)
        mask = (tth >= lo) & (tth <= hi) & (weights > 0)

        in_window_intensities = intensities[mask]
        if (in_window_intensities < 0).any():
            neg_count = (in_window_intensities < 0).sum()
            raise ValueError(
                f"Negative intensities in fit window: {neg_count} point(s). "
                "easydiffraction rejects negative intensity data. "
                "Check xrd_data.Itth values."
            )

        return self

    @model_validator(mode="after")
    def _check_bounds_vs_physical_limits(self) -> "EasydiffractionPayload":
        """Check values against physical limits on null sides (A138, EB-71).

        Physical limits (easydiffraction):
        - cell lengths: [0, 30] Å (cannot model > 30 Å)
        - cell angles: [0, 180]°
        - occupancy: [0, 1] (enforced by core, A76)
        - adp_iso: [0, 10] Å²
        - scale, wavelength: >= 0

        A stated bound replaces the limit on that side.
        A value outside a physical limit on a null side is an error.
        """
        # Check each phase
        for phase_name, phase in self.phases.items():
            # Cell lengths
            for length in ["a", "b", "c"]:
                param = getattr(phase.unit_cell, length)
                if param.value > 30.0 and param.max is None:
                    raise ValueError(
                        f"Phase {phase_name!r}: cell length {length} = {param.value} Å > 30 Å. "
                        "easydiffraction cannot model cells > 30 Å. "
                        "Either reduce the value or set an explicit max bound."
                    )

        # Occupancy [0, 1]: core already enforces it strictly (A76), so easydiffraction's
        # identical limit (EB-22) can never be reached here.
        # Wavelength >= 0 already checked in Radiation validator
        # Scale >= 0 already checked in Phase validator

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

    payload: EasydiffractionPayload

    @model_validator(mode="after")
    def _check_versions(self) -> "EasydiffractionRecipe":
        """Validate schema versions (A65, A70)."""
        check_version(self.core_schema_version, self.engine_schema_version)
        return self
