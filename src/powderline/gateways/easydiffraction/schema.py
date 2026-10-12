"""easydiffraction engine schema ``easydiffraction.rietveld`` (engine schema 1.0.0).

Built on the core schema (:mod:`powderline.schema_core`): a recipe is a
:class:`~powderline.schema_core.CoreRecipe`, a phase a
``Phase[BoundedRefinableParameter]`` with the engine's fields flat in the same
block (A93). Values are in easydiffraction's native units (A4) and **the recipe
keys of the instrument are easydiffraction's own attribute names** (A160, A195:
``setup_wavelength``, ``calib_twotheta_offset``, ``broad_gauss_u``, ...), so the
builder hands every value under the name the recipe states. Every refinable is
``[value, refine_flag, min, max]`` (:class:`~powderline.schema_core.BoundedRefinableParameter`,
A116). Invalid recipes raise pydantic's ``ValidationError`` with every problem at
its location (A70).

Engine-free: imports no easydiffraction (enforced by an import-block test), so
``validate()`` runs in the core-only environment. The gateway's runtime layer
consumes the validated model directly (A39).

Shape of the payload (the hand-derived fixtures in
``tests/data/easydiffraction/native/`` are the reference, A137, A157 B8):

- ``instrument`` (A160): ``description``; ``radiation`` (``setup_wavelength``,
  optional Kα2 pair ``setup_wavelength_2`` + ``setup_wavelength_2_to_1_ratio``);
  optional ``polarization`` (``setup_polarization_coefficient`` p,
  ``setup_monochromator_twotheta``; plain fixed values, EB-75); ``corrections``
  (``calib_twotheta_offset``, optional ``calib_sample_displacement``,
  ``calib_sample_transparency``; degrees 2θ); optional ``absorption``
  (``type: "cylinder-hewat"``, ``mu_r``); ``broadening`` (``peak_type``, optional
  ``cutoff_fwhm``, ``parameters`` whose keys depend on the peak type).
- ``background``: ``{"chebyshev": {...}}``, the experiment's (A189); left out =
  one fixed 0.0 term (D1).
- ``phases``: core phases + ``scale`` (>= 0, A161).
- ``refinement_controls`` (A162; required, no defaults): ``calculator``,
  ``minimizer``, ``max_iterations``, ``chi_square_change_tolerance``,
  ``parameter_change_tolerance``, ``gradient_tolerance``.
- ``fit_range`` may be left out (the data's full weighted span).

easydiffraction rules checked here (engine-behavior register entries in brackets):

- ``peak_type`` per calculator (EB-76): CrysPy computes ``cwl-pseudo-voigt`` and
  ``cwl-pseudo-voigt-berar-baldinozzi-asymmetry``; CrysFML ``cwl-pseudo-voigt``
  and ``cwl-thompson-cox-hastings``; ``cutoff_fwhm`` acts in CrysPy only;
- a left-out optional slot is not modelled; a stated one is never ``null`` (A96);
- the Kα2 pair goes together (one alone does nothing, EB-75);
- wavelength > 0 (A126); polarization p in [0, 1] (EB-75); ``mu_r`` >= 0 (EB-75);
- the stated widths must not be negative (A171: checked at a representative
  angle here, over the whole window with its margin by the builder, A178);
- negative starting Uiso/Uij is an error (EB-46); CrysFML + Uaniso is an error (A169 a, EB-23);
- a negative intensity on a handed point is an error (A177, EB-91); a weight
  above 1e8 on a handed point is an error (A159, EB-73);
- a cell length over 30 Å with no stated max is an error (A157 B4, EB-71);
- ``:R`` settings are refused (hexagonal axes only);
- simulation (nothing flagged) is allowed (A129).

Documented defaults (A119): ``background`` left out = one fixed 0.0 Chebyshev term.
"""

from __future__ import annotations

import math
from typing import Annotated, Any, Literal, Optional, Union

from pydantic import Discriminator, Field, Tag, ValidationError, field_validator, model_serializer, model_validator
from pydantic_core import InitErrorDetails, PydanticCustomError

from powderline.compat import check_engine_schema_version
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
    XRDData,
    FitRange,
    name_keyed,
    check_fit_range_within_data,
    check_names_unique_ignoring_case,
    fit_limits_on_data,
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

# --- calculators and peak types (A162, EB-76) --------------------------------

#: easydiffraction's calculators (A162).
CALCULATORS = ("cryspy", "crysfml")
#: easydiffraction's CW peak types (``peak.type`` codes; A160, EB-76).
PEAK_TYPE_PSEUDO_VOIGT = "cwl-pseudo-voigt"
PEAK_TYPE_TCH = "cwl-thompson-cox-hastings"
PEAK_TYPE_BEBA = "cwl-pseudo-voigt-berar-baldinozzi-asymmetry"
PEAK_TYPES = (PEAK_TYPE_PSEUDO_VOIGT, PEAK_TYPE_TCH, PEAK_TYPE_BEBA)
#: The peak types each calculator computes (EB-76): the calculator x peak-type matrix.
CALCULATOR_PEAK_TYPES = {
    "cryspy": (PEAK_TYPE_PSEUDO_VOIGT, PEAK_TYPE_BEBA),
    "crysfml": (PEAK_TYPE_PSEUDO_VOIGT, PEAK_TYPE_TCH),
}
#: Calculators ``cutoff_fwhm`` acts in (EB-76: CrysFML uses a fixed 30-FWHM window).
CUTOFF_FWHM_CALCULATORS = ("cryspy",)
#: A weight above this on a handed point gives sigma < 1e-4, which easydiffraction replaces by 1.0 silently (EB-73, A159).
MAX_WEIGHT = 1e8
#: The 2θ angle (degrees) the schema-level width check uses (the builder checks the window, A178).
WIDTH_CHECK_TWO_THETA = 90.0


def _unit(unit: str) -> dict:
    return {"unit": unit}


def _never_null(v: Any, info) -> Any:
    """A field that may be left out is never ``null`` (the A96 convention)."""
    if v is None:
        raise ValueError(f"{info.field_name} must not be null; leave it out instead")
    return v


def check_version(core_schema_version: str, engine_schema_version: str) -> None:
    """Raise ``ValueError`` unless the recipe's versions are compatible (A65, A70)."""
    from powderline.gateways.easydiffraction.gateway import capabilities
    check_engine_schema_version(
        core_schema_version,
        engine_schema_version,
        gateway="easydiffraction",
        capabilities=capabilities(),
    )


# --- instrument models (A160, A195; native names and units A4) --------------

class Radiation(CoreModel):
    """CW X-ray radiation: ``setup_wavelength`` and the optional Kα2 pair (EB-75).

    ``setup_wavelength_2`` and ``setup_wavelength_2_to_1_ratio`` are fixed
    values that go together: one alone does nothing in easydiffraction.
    """
    setup_wavelength: BoundedRefinableParameter = Field(..., json_schema_extra=_unit(UNIT_ANGSTROM))
    setup_wavelength_2: Optional[CoreFloat] = Field(
        default=None, gt=0, json_schema_extra=absent_not_null_schema(UNIT_ANGSTROM),
        description="Kα2 wavelength; left out = no doublet (requires setup_wavelength_2_to_1_ratio)")
    setup_wavelength_2_to_1_ratio: Optional[CoreFloat] = Field(
        default=None, gt=0, le=1, json_schema_extra=absent_not_null_schema(UNIT_ARBITRARY),
        description="Kα2/Kα1 intensity ratio in (0, 1]; left out = no doublet (requires setup_wavelength_2)")

    _not_null = field_validator("setup_wavelength_2", "setup_wavelength_2_to_1_ratio", mode="before")(_never_null)

    @field_validator("setup_wavelength", mode="after")
    def _wavelength_positive(cls, param: BoundedRefinableParameter) -> BoundedRefinableParameter:
        """Wavelength must be > 0 (A126, A155 Q6)."""
        if param.value <= 0:
            raise ValueError(f"setup_wavelength must be > 0, got {param.value}")
        return param

    @model_validator(mode="after")
    def _ka2_pair(self) -> "Radiation":
        """The Kα2 pair goes together (EB-75: one alone does nothing)."""
        stated = {k: getattr(self, k) is not None for k in ("setup_wavelength_2", "setup_wavelength_2_to_1_ratio")}
        if any(stated.values()) and not all(stated.values()):
            missing = next(k for k, v in stated.items() if not v)
            raise ValueError(f"the Kα2 pair goes together: {missing} is missing (one alone does nothing, EB-75)")
        return self

    @model_serializer(mode="wrap")
    def _omit_absent(self, handler):
        data = handler(self)
        for key in ("setup_wavelength_2", "setup_wavelength_2_to_1_ratio"):
            if getattr(self, key) is None:
                data.pop(key, None)
        return data


class Polarization(CoreModel):
    """Lorentz-polarization 1 − p + p·cos²2θ_m·cos²2θ (EB-75): fixed values.

    ``setup_polarization_coefficient`` is easydiffraction's p in [0, 1]:
    GSAS-II's P is 1 − p.
    """
    setup_polarization_coefficient: CoreFloat = Field(..., ge=0, le=1, json_schema_extra=_unit(UNIT_ARBITRARY))
    setup_monochromator_twotheta: CoreFloat = Field(..., json_schema_extra=_unit(UNIT_DEG_2THETA))


class Corrections(CoreModel):
    """Position corrections (EB-75): zero offset, optional displacement (·cos2θ) and transparency (·sin2θ), degrees 2θ."""
    calib_twotheta_offset: BoundedRefinableParameter = Field(..., json_schema_extra=_unit(UNIT_DEG_2THETA))
    calib_sample_displacement: Optional[BoundedRefinableParameter] = Field(
        default=None, json_schema_extra=absent_not_null_schema(UNIT_DEG_2THETA),
        description="Left out: no displacement correction")
    calib_sample_transparency: Optional[BoundedRefinableParameter] = Field(
        default=None, json_schema_extra=absent_not_null_schema(UNIT_DEG_2THETA),
        description="Left out: no transparency correction")

    _not_null = field_validator("calib_sample_displacement", "calib_sample_transparency", mode="before")(_never_null)

    @model_serializer(mode="wrap")
    def _omit_absent(self, handler):
        data = handler(self)
        for key in ("calib_sample_displacement", "calib_sample_transparency"):
            if getattr(self, key) is None:
                data.pop(key, None)
        return data


class Absorption(CoreModel):
    """Cylinder absorption (Hewat; EB-75): ``mu_r`` >= 0, refinable."""
    type: Literal["cylinder-hewat"] = Field(...)
    mu_r: BoundedRefinableParameter = Field(..., json_schema_extra=_unit(UNIT_ARBITRARY))

    @field_validator("mu_r", mode="after")
    def _mu_r_nonnegative(cls, param: BoundedRefinableParameter) -> BoundedRefinableParameter:
        if param.value < 0:
            raise ValueError(f"mu_r must be >= 0, got {param.value}")
        return param


class PseudoVoigtParameters(CoreModel):
    """``cwl-pseudo-voigt``: Caglioti U, V, W (FWHM² deg²) and Lorentzian X, Y (FWHM deg).

    Gaussian FWHM² = U tan²θ + V tanθ + W; Lorentzian FWHM = X tanθ + Y/cosθ
    (EB-76, A171: GSAS-II's X/cosθ + Y tanθ swaps the roles).
    """
    broad_gauss_u: BoundedRefinableParameter = Field(..., json_schema_extra=_unit(UNIT_DEG2_FWHM))
    broad_gauss_v: BoundedRefinableParameter = Field(..., json_schema_extra=_unit(UNIT_DEG2_FWHM))
    broad_gauss_w: BoundedRefinableParameter = Field(..., json_schema_extra=_unit(UNIT_DEG2_FWHM))
    broad_lorentz_x: BoundedRefinableParameter = Field(..., json_schema_extra=_unit(UNIT_DEG_FWHM))
    broad_lorentz_y: BoundedRefinableParameter = Field(..., json_schema_extra=_unit(UNIT_DEG_FWHM))


class ThompsonCoxHastingsParameters(PseudoVoigtParameters):
    """``cwl-thompson-cox-hastings``: the five widths plus FCJ axial asymmetry S/L, H/L (CrysFML; EB-76)."""
    asym_fcj_1: BoundedRefinableParameter = Field(..., json_schema_extra=_unit(UNIT_ARBITRARY))
    asym_fcj_2: BoundedRefinableParameter = Field(..., json_schema_extra=_unit(UNIT_ARBITRARY))


class BerarBaldinozziParameters(PseudoVoigtParameters):
    """``cwl-pseudo-voigt-berar-baldinozzi-asymmetry``: the five widths plus the empirical asymmetry (CrysPy; EB-76)."""
    asym_beba_a0: BoundedRefinableParameter = Field(..., json_schema_extra=_unit(UNIT_ARBITRARY))
    asym_beba_b0: BoundedRefinableParameter = Field(..., json_schema_extra=_unit(UNIT_ARBITRARY))
    asym_beba_a1: BoundedRefinableParameter = Field(..., json_schema_extra=_unit(UNIT_ARBITRARY))
    asym_beba_b1: BoundedRefinableParameter = Field(..., json_schema_extra=_unit(UNIT_ARBITRARY))


#: ``peak_type`` -> its ``parameters`` model.
PEAK_PARAMETERS = {
    PEAK_TYPE_PSEUDO_VOIGT: PseudoVoigtParameters,
    PEAK_TYPE_TCH: ThompsonCoxHastingsParameters,
    PEAK_TYPE_BEBA: BerarBaldinozziParameters,
}
#: The five width parameters every peak type has.
WIDTH_PARAMETERS = ("broad_gauss_u", "broad_gauss_v", "broad_gauss_w", "broad_lorentz_x", "broad_lorentz_y")


def _parameters_tag(value: Any) -> Optional[str]:
    """Which ``parameters`` model a stated block is written for, from its asymmetry keys (the peak type is checked after)."""
    if isinstance(value, PseudoVoigtParameters):
        return next(t for t, m in PEAK_PARAMETERS.items() if type(value) is m)
    if not isinstance(value, dict):
        return None
    if any(k.startswith("asym_fcj_") for k in value):
        return PEAK_TYPE_TCH
    if any(k.startswith("asym_beba_") for k in value):
        return PEAK_TYPE_BEBA
    return PEAK_TYPE_PSEUDO_VOIGT


PeakParameters = Annotated[
    Union[
        Annotated[PseudoVoigtParameters, Tag(PEAK_TYPE_PSEUDO_VOIGT)],
        Annotated[ThompsonCoxHastingsParameters, Tag(PEAK_TYPE_TCH)],
        Annotated[BerarBaldinozziParameters, Tag(PEAK_TYPE_BEBA)],
    ],
    Discriminator(_parameters_tag),
]


class Broadening(CoreModel):
    """Peak profile: ``peak_type`` (easydiffraction's code), optional ``cutoff_fwhm``, and the type's ``parameters``.

    ``parameters`` holds exactly the keys of the peak type (``extra='forbid'``):
    the five widths for ``cwl-pseudo-voigt``, plus ``asym_fcj_1/2`` for
    ``cwl-thompson-cox-hastings``, plus ``asym_beba_a0/b0/a1/b1`` for
    ``cwl-pseudo-voigt-berar-baldinozzi-asymmetry``. ``cutoff_fwhm`` (reflection
    cutoff in FWHM units, 0 = none) acts in CrysPy only (EB-76; checked by the
    payload against the calculator); left out = the engine's own value.
    """
    peak_type: Literal[PEAK_TYPES] = Field(...)
    cutoff_fwhm: Optional[CoreFloat] = Field(
        default=None, ge=0, json_schema_extra=absent_not_null_schema(UNIT_ARBITRARY),
        description="Reflection cutoff in FWHM units (CrysPy only; 0 = no cutoff); left out = the engine's")
    parameters: PeakParameters = Field(...)

    _not_null = field_validator("cutoff_fwhm", mode="before")(_never_null)

    @model_validator(mode="after")
    def _parameters_match_peak_type(self) -> "Broadening":
        expected = PEAK_PARAMETERS[self.peak_type]
        if type(self.parameters) is not expected:
            stated = next(t for t, m in PEAK_PARAMETERS.items() if type(self.parameters) is m)
            needed = ", ".join(k for k in expected.model_fields if k not in WIDTH_PARAMETERS) or "the five widths only"
            raise ValueError(
                f"parameters are written for peak_type {stated!r}, but peak_type is {self.peak_type!r}: "
                f"it takes {needed}")
        return self

    @model_validator(mode="after")
    def _check_negative_widths(self) -> "Broadening":
        """The stated widths at 2θ = 90° must be >= 0 (A171, EB-87).

        The full check over the window widened by its margin is
        ``native_builder.check_peak_widths`` (run by ``build_project``).
        """
        p = self.parameters
        theta = math.radians(WIDTH_CHECK_TWO_THETA / 2.0)
        t, c = math.tan(theta), math.cos(theta)
        gauss = p.broad_gauss_u.value * t**2 + p.broad_gauss_v.value * t + p.broad_gauss_w.value
        lorentz = p.broad_lorentz_x.value * t + p.broad_lorentz_y.value / c
        if gauss < 0:
            raise ValueError(
                f"Gaussian FWHM² < 0 at 2theta = {WIDTH_CHECK_TWO_THETA:g}°: broad_gauss_u tan²θ + broad_gauss_v tanθ + "
                f"broad_gauss_w = {gauss:.2e}; easydiffraction computes a wrong pattern without a message (EB-87)")
        if lorentz < 0:
            raise ValueError(
                f"Lorentzian FWHM < 0 at 2theta = {WIDTH_CHECK_TWO_THETA:g}°: broad_lorentz_x tanθ + "
                f"broad_lorentz_y / cosθ = {lorentz:.2e}; easydiffraction computes a wrong pattern without a message (EB-87)")
        return self

    @model_serializer(mode="wrap")
    def _omit_absent(self, handler):
        data = handler(self)
        if self.cutoff_fwhm is None:
            data.pop("cutoff_fwhm", None)
        return data


class Instrument(CoreModel):
    """Instrument block (A160): every correction easydiffraction models is a slot; a left-out optional slot is not modelled."""
    description: Optional[str] = Field(default=None, json_schema_extra=absent_not_null_schema())
    radiation: Radiation
    polarization: Optional[Polarization] = Field(default=None, json_schema_extra=absent_not_null_schema(),
                                                 description="Left out: no Lorentz-polarization correction")
    corrections: Corrections
    absorption: Optional[Absorption] = Field(default=None, json_schema_extra=absent_not_null_schema(),
                                             description="Left out: no absorption correction")
    broadening: Broadening

    _not_null = field_validator("description", "polarization", "absorption", mode="before")(_never_null)

    @model_serializer(mode="wrap")
    def _omit_absent(self, handler):
        data = handler(self)
        for key in ("description", "polarization", "absorption"):
            if getattr(self, key) is None:
                data.pop(key, None)
        return data


# --- background (A189, D1) --------------------------------------------------

def _no_background() -> ChebyshevBackground:
    """Background left out = none: one fixed Chebyshev term 0.0 (D1, A119)."""
    return ChebyshevBackground(num_coefficients=1, coefficients=[0.0], refine_flag=False)


class Background(CoreModel):
    """The experiment's background (easydiffraction has one per experiment, A189): a Chebyshev polynomial.

    Left out, ``chebyshev`` is one fixed term 0.0 (no background, D1).
    """
    chebyshev: ChebyshevBackground = Field(default_factory=_no_background)

    _not_null = field_validator("chebyshev", mode="before")(_never_null)


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


# --- refinement controls (A162, A195) ----------------------------------------

class RefinementControls(CoreModel):
    """Refinement controls (A162): required, no defaults.

    ``calculator`` chooses the structure-factor and profile calculator (they
    compute different intensities and offer different peak types, EB-76..80);
    ``minimizer`` is ``lmfit (leastsq)`` only in 1.0.0 (P and the ESDs come
    from lmfit); ``max_iterations`` is lmfit ``max_nfev``; the three tolerances
    are lmfit ``ftol``, ``xtol``, ``gtol`` (``gradient_tolerance`` may be 0,
    which is what easydiffraction 0.21.1 itself passes, EB-81). A tolerance is
    handed where the minimizer exposes it, else reported by the informative
    warning ``easydiffraction_stopping_criteria_not_handed`` (A190).
    """
    calculator: Literal[CALCULATORS] = Field(..., description="Structure factor and profile calculator")
    minimizer: Literal["lmfit (leastsq)"] = Field(..., description="Minimization algorithm")
    max_iterations: CoreInt = Field(..., gt=0, description="lmfit max_nfev")
    chi_square_change_tolerance: CoreFloat = Field(..., gt=0, description="lmfit ftol")
    parameter_change_tolerance: CoreFloat = Field(..., gt=0, description="lmfit xtol")
    gradient_tolerance: CoreFloat = Field(..., ge=0, description="lmfit gtol (0 = not used)")


# --- payload (complete recipe payload) -------------------------------------

class EasydiffractionPayload(CoreModel):
    """Complete payload for easydiffraction.rietveld recipe."""
    xrd_data: XRDData
    fit_range: Optional[FitRange] = Field(default=None, json_schema_extra=absent_not_null_schema(),
                                          description="Left out: the data's full weighted 2theta range")
    phases: Annotated[dict[CoreName, EasydiffractionPhase], name_keyed] = Field(
        ...,
        description="Phase definitions keyed by name"
    )
    instrument: Instrument
    refinement_controls: RefinementControls
    background: Background = Field(
        default_factory=Background,
        description="The experiment's Chebyshev background (easydiffraction has one per experiment, A189); "
                    "left out = none (one fixed 0.0 term, D1)",
    )

    _not_null = field_validator("fit_range", "background", mode="before")(_never_null)

    @model_serializer(mode="wrap")
    def _omit_absent(self, handler):
        data = handler(self)
        if self.fit_range is None:
            data.pop("fit_range", None)
        return data

    @model_validator(mode="after")
    def _check_names_unique(self) -> "EasydiffractionPayload":
        """Phase names must be unique ignoring case (A112)."""
        check_names_unique_ignoring_case(self.phases, "phases")
        return self

    @model_validator(mode="after")
    def _payload_rules(self) -> "EasydiffractionPayload":
        """Rules that need more than one block, each reported at its location (A70)."""
        problems: list[InitErrorDetails] = []
        try:
            check_fit_range_within_data(self.fit_range, self.xrd_data)
        except ValueError as exc:
            problems.append(InitErrorDetails(type=PydanticCustomError("fit_range", str(exc)), loc=("fit_range",),
                                             input=None if self.fit_range is None else self.fit_range.model_dump()))
        else:
            problems += self._data_problems()
        problems += self._calculator_problems()
        if problems:
            raise ValidationError.from_exception_data(type(self).__name__, problems)
        return self

    def _calculator_problems(self) -> list[InitErrorDetails]:
        """Peak type and ``cutoff_fwhm`` per calculator (A162, EB-76); CrysFML + Uaniso (A169 a, EB-23)."""
        problems = []
        calculator = self.refinement_controls.calculator
        broadening = self.instrument.broadening
        allowed = CALCULATOR_PEAK_TYPES[calculator]
        if broadening.peak_type not in allowed:
            others = [c for c, types in CALCULATOR_PEAK_TYPES.items() if broadening.peak_type in types]
            problems.append(InitErrorDetails(
                type=PydanticCustomError(
                    "peak_type_calculator",
                    "peak_type {peak_type} is not computed by calculator {calculator} (EB-76); it computes {allowed}"
                    "{remedy}",
                    {"peak_type": repr(broadening.peak_type), "calculator": repr(calculator),
                     "allowed": ", ".join(repr(t) for t in allowed),
                     "remedy": f"; calculator {others[0]!r} computes it" if others else ""}),
                loc=("instrument", "broadening", "peak_type"), input=broadening.peak_type))
        if broadening.cutoff_fwhm is not None and calculator not in CUTOFF_FWHM_CALCULATORS:
            problems.append(InitErrorDetails(
                type=PydanticCustomError(
                    "cutoff_fwhm_calculator",
                    "cutoff_fwhm acts in {where} only (EB-76: {calculator} uses a fixed 30-FWHM window); "
                    "leave it out with calculator {calculator}",
                    {"where": ", ".join(CUTOFF_FWHM_CALCULATORS), "calculator": repr(calculator)}),
                loc=("instrument", "broadening", "cutoff_fwhm"), input=broadening.cutoff_fwhm))
        if calculator == "crysfml":
            for phase_name, phase in self.phases.items():
                for atom_label, atom in phase.atoms.items():
                    if atom.ADP == "Uaniso":
                        problems.append(InitErrorDetails(
                            type=PydanticCustomError(
                                "crysfml_uaniso",
                                "CrysFML calculator does not support anisotropic thermal parameters: easydiffraction "
                                "hands it only B = 8 pi^2 Ueq, so the tensor has no effect (A169 a, EB-23). "
                                "Use ADP='Uiso' or switch to calculator='cryspy'"),
                            loc=("phases", phase_name, "atoms", atom_label, "ADP"), input=atom.ADP))
        return problems

    def _data_problems(self) -> list[InitErrorDetails]:
        """Negative intensities (A177, EB-91) and weights above 1e8 (A159, EB-73) on the handed points."""
        import numpy as np

        # The points core scores and the gateway hands: inside the window's first/last weighted points, w > 0.
        lo, hi = fit_limits_on_data(self.fit_range, self.xrd_data)
        tth = np.asarray(self.xrd_data.tth, dtype=float)
        weights = np.asarray(self.xrd_data.Itth_weights, dtype=float)
        intensities = np.asarray(self.xrd_data.Itth, dtype=float)
        handed = (tth >= lo) & (tth <= hi) & (weights > 0)
        problems = []
        for i in np.flatnonzero(handed & (intensities < 0)):
            problems.append(InitErrorDetails(
                type=PydanticCustomError(
                    "negative_intensity",
                    "Negative intensities in fit window: Itth[{i}] = {value} at 2theta {tth}; easydiffraction rejects "
                    "negative intensity data (EB-91, A177). Check xrd_data.Itth values",
                    {"i": int(i), "value": float(intensities[i]), "tth": float(tth[i])}),
                loc=("xrd_data", "Itth", int(i)), input=float(intensities[i])))
        for i in np.flatnonzero(handed & (weights > MAX_WEIGHT)):
            problems.append(InitErrorDetails(
                type=PydanticCustomError(
                    "weight_too_large",
                    "weight {value} at 2theta {tth} is above {limit} (sigma < 1e-4): easydiffraction would replace "
                    "its sigma by 1.0 silently (EB-73, A159); rescale the intensities and weights",
                    {"value": float(weights[i]), "tth": float(tth[i]), "limit": f"{MAX_WEIGHT:g}"}),
                loc=("xrd_data", "Itth_weights", int(i)), input=float(weights[i])))
        return problems

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
        # Wavelength > 0 already checked in Radiation; scale >= 0 in the phase.

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


# --- routing (the gateway's schema layer, A39) -----------------------------

def is_native_recipe(recipe) -> bool:
    """True for an ``easydiffraction.*`` recipe (dict or model); False for anything else (e.g. 0.26.0 ``GSASII_*``).

    Any ``easydiffraction.`` name counts, so a mistyped one (``easydiffraction.reitveld``)
    is reported against the native schema names, not the 0.26.0 schema.
    """
    if isinstance(recipe, EasydiffractionRecipe):
        return True
    name = recipe.get("schema_name") if isinstance(recipe, dict) else None
    return isinstance(name, str) and name.startswith("easydiffraction.")


def validate_recipe(recipe) -> EasydiffractionRecipe:
    """Validate an ``easydiffraction.*`` recipe (dict or model); return the model (idempotent, A50).

    Errors are located from the recipe's top level (``payload.phases.<name>...``);
    an unknown ``schema_name`` is reported at ``schema_name`` (A133).
    """
    if isinstance(recipe, EasydiffractionRecipe):
        return recipe
    if isinstance(recipe, dict) and recipe.get("schema_name") not in SCHEMA_NAMES:
        raise ValidationError.from_exception_data("EasydiffractionRecipe", [InitErrorDetails(
            type=PydanticCustomError("schema_name", "schema_name '{name}' is not an easydiffraction workflow; "
                                                    "expected one of {names}",
                                     {"name": recipe.get("schema_name"), "names": ", ".join(SCHEMA_NAMES)}),
            loc=("schema_name",), input=recipe.get("schema_name"))])
    return EasydiffractionRecipe.model_validate(recipe)
