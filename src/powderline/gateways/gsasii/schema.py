"""GSAS-II engine schemas ``gsasii.rietveld`` and ``gsasii.spf`` (engine schema 1.0.0).

Built on the core schema (:mod:`powderline.schema_core`): a recipe is a
:class:`~powderline.schema_core.CoreRecipe`, a phase a
``Phase[RefinableParameter]`` with the gsasii fields flat in the same block
(A93). Values are in GSAS-II's native units (A4); every refinable is
``[value, refine_flag]``: gsasii 1.0.0 exposes no bounds (A84). Invalid recipes
raise pydantic's ``ValidationError`` with every problem at its location (A70).

Engine-free: imports no GSAS-II (enforced by an import-block test), so
``validate()`` runs in the core-only environment. The gateway's runtime layer
consumes the validated model directly (A39).

GSAS-II rules checked here (engine-behavior register entries in brackets):

- the space group must be one GSAS-II reads as the same setting (EB-03, EB-41);
- cell refinement groups are coarser than symmetry for oblique cells, so a
  refined strict subset of one is an error (A95);
- SH/L >= 0.002 (A91, EB-37); background-peak floors (A86, EB-35); Peak List
  floors (A90);
- wavelength > 0 and crystallite size > 0 (GSAS-II fails otherwise; A126);
- background and Peak List peak positions lie inside the fit window (A86, A126);
- single peak fitting: the instrument profile and the peaks' own widths are
  never both requested, since GSAS-II silently ignores one of them (A127, EB-53);
- simulation (``refinement_cycles == 1``) refines nothing (A88).

Documented defaults (A119): size/strain left out = 10 um / 0, isotropic, fixed,
reported by a structured warning (A87, A107); background left out = none (one
fixed Chebyshev term 0.0, D1).
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Optional, Union

import gemmi
from pydantic import Field, StrictBool, TypeAdapter, ValidationError, field_validator, model_serializer, model_validator
from pydantic_core import InitErrorDetails, PydanticCustomError

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
    FitRange,
    Phase,
    RefinableParameter,
    XRDData,
    absent_not_null_schema,
    check_fit_range_within_data,
    check_names_unique_ignoring_case,
    name_keyed,
)
from powderline.symmetry import cell_tie_groups

# --- versions (A65, A81) ----------------------------------------------------

ENGINE_SCHEMA_VERSION = "1.0.0"
SUPPORTED_ENGINE_SCHEMAS = "==1.0.0"
REQUIRES_CORE_SCHEMA = "==1.0.0"

#: The gateway's schema declarations (``capabilities()`` keys, A65).
DECLARATIONS = {
    "engine_schema_version": ENGINE_SCHEMA_VERSION,
    "supported_engine_schemas": SUPPORTED_ENGINE_SCHEMAS,
    "requires_core_schema": REQUIRES_CORE_SCHEMA,
}

SCHEMA_NAMES = ("gsasii.rietveld", "gsasii.spf")

# --- units (GSAS-II native, A4) ----------------------------------------------

UNIT_CENTIDEG = "centidegree 2theta"
UNIT_CENTIDEG2 = "centidegree^2 2theta"
UNIT_MICROMETRE = "micrometre"
UNIT_MICROSTRAIN = "microstrain"
UNIT_DIMENSIONLESS = "dimensionless"


def _unit(unit: str) -> dict:
    return {"unit": unit}


def _never_null(v: Any, info) -> Any:
    """A block that may be left out is never ``null`` (the A96 convention)."""
    if v is None:
        raise ValueError(f"{info.field_name} must not be null; leave it out instead")
    return v


# --- space group (A105, EB-03, EB-41) -----------------------------------------

#: Canonical names GSAS-II 5.7.9 cannot express: ``SpcGroup`` rejects the symbol, or reads it as
#: another setting (a general position's orbit differs). Found by the orbit test over every
#: canonical name (``tests/test_gsasii_schema_engine.py``); ``:1`` origins are refused separately.
GSASII_UNREADABLE_SETTINGS = frozenset({
    "P 21212(a)", "C 2 2 21a)", "C 2 2 2a", "F 2 2 2a", "I 2 2 2a", "P 42 21 2a", "I 2 3a",
    "P 21 n m", "A b a m",
})


def gsasii_space_group(name: str) -> str:
    """GSAS-II's symbol for a canonical (gemmi ``xhm``) space-group name.

    GSAS-II reads spaced Hermann-Mauguin symbols, hexagonal axes and origin
    choice 2 by default, and rhombohedral axes from a trailing ``" R"``
    (EB-41). Raises ``ValueError`` for a setting it cannot express: origin
    choice 1 (EB-03) or one of :data:`GSASII_UNREADABLE_SETTINGS`.
    """
    sg = gemmi.find_spacegroup_by_name(name)
    if sg is None or sg.xhm() != name:
        raise ValueError(f"{name!r} is not a canonical space-group name")
    if sg.ext == "1":
        raise ValueError(
            f"GSAS-II cannot use origin choice 1 ({name!r}); state the structure in origin choice 2 "
            f"({name[:-2]}:2) (GSAS-II quirk EB-03)")
    if name in GSASII_UNREADABLE_SETTINGS:
        raise ValueError(
            f"GSAS-II cannot express the setting {name!r} (it does not read the symbol, or reads it as "
            "another setting); transform the structure to a standard setting (GSAS-II quirk EB-41)")
    return sg.hm + " R" if sg.ext == "R" else sg.hm


def gsasii_cell_groups(space_group: str) -> tuple[tuple[str, ...], ...]:
    """GSAS-II's cell refinement groups: tied or coupled parameters that refine together.

    GSAS-II varies the reciprocal metric tensor, so an oblique cell's direct
    parameters cannot be held separately: monoclinic {a, c, beta} (b-unique;
    {b, c, alpha} a-unique; {a, b, gamma} c-unique), and all six for triclinic
    and rhombohedral axes. Otherwise they are the symmetry tie groups (A95;
    ``gateways/gsasii/constraints.py`` ``cell_dof_groups``).
    """
    ties = cell_tie_groups(space_group)
    lengths = ("a", "b", "c")
    if ties.crystal_system == "triclinic" or space_group.endswith(":R"):
        return (lengths + ("alpha", "beta", "gamma"),)
    if ties.crystal_system == "monoclinic":
        u = ties.unique_axis
        angle = {"a": "alpha", "b": "beta", "c": "gamma"}[u]
        return ((u,), tuple(n for n in lengths if n != u) + (angle,))
    return tuple(g.members for g in ties.groups)


# --- instrument (A99, A103) --------------------------------------------------


class Radiation(CoreModel):
    """Radiation: GSAS-II type ``PXC`` (powder X-ray, constant wavelength) only; one wavelength."""

    type: Literal["PXC"] = Field(description="GSAS-II instrument type; 'PXC' only (Kα doublets, TOF and "
                                             "neutron are not supported)")
    wavelength: RefinableParameter = Field(json_schema_extra=_unit(UNIT_ANGSTROM),
                                           description="Wavelength (GSAS-II 'Lam'), > 0")

    @field_validator("wavelength")
    @classmethod
    def _gsasii_wavelength(cls, v: RefinableParameter) -> RefinableParameter:
        if not v.value > 0:
            raise ValueError(f"wavelength must be > 0, got {v.value}")
        return v


class Geometry(CoreModel):
    """Detector geometry: GSAS-II ``Bank`` and ``Azimuth`` (plain values)."""

    bank: CoreInt = Field(description="GSAS-II 'Bank'")
    azimuth: CoreFloat = Field(json_schema_extra=_unit(UNIT_DEGREE), description="GSAS-II 'Azimuth'")


#: GSAS-II computes Bragg (Rietveld) and Peak List profiles with SH/L >= 0.002 (EB-37).
AXIAL_DIVERGENCE_MIN = 0.002


class Corrections(CoreModel):
    """Instrument corrections: zero shift, polarization, axial divergence (GSAS-II 'SH/L')."""

    zero_shift: RefinableParameter = Field(json_schema_extra=_unit(UNIT_CENTIDEG), description="GSAS-II 'Zero'")
    polarization: RefinableParameter = Field(json_schema_extra=_unit(UNIT_DIMENSIONLESS),
                                             description="GSAS-II 'Polariz.'")
    axial_divergence: RefinableParameter = Field(
        json_schema_extra=_unit(UNIT_DIMENSIONLESS),
        description=f"GSAS-II 'SH/L' (asymmetry); at least {AXIAL_DIVERGENCE_MIN} (GSAS-II's own floor, A91)")

    @field_validator("axial_divergence")
    @classmethod
    def _sh_l_floor(cls, v: RefinableParameter) -> RefinableParameter:
        if v.value < AXIAL_DIVERGENCE_MIN:
            raise ValueError(
                f"axial_divergence (SH/L) must be at least {AXIAL_DIVERGENCE_MIN}, got {v.value}: GSAS-II "
                f"computes Bragg and Peak List profiles with SH/L >= {AXIAL_DIVERGENCE_MIN} whatever is stated "
                "(GSAS-II quirk EB-37)")
        return v


class InstrumentBroadening(CoreModel):
    """Instrument profile (Caglioti U, V, W; Lorentzian X, Y, Z)."""

    U: RefinableParameter = Field(json_schema_extra=_unit(UNIT_CENTIDEG2))
    V: RefinableParameter = Field(json_schema_extra=_unit(UNIT_CENTIDEG2))
    W: RefinableParameter = Field(json_schema_extra=_unit(UNIT_CENTIDEG2))
    X: RefinableParameter = Field(json_schema_extra=_unit(UNIT_CENTIDEG))
    Y: RefinableParameter = Field(json_schema_extra=_unit(UNIT_CENTIDEG))
    Z: RefinableParameter = Field(json_schema_extra=_unit(UNIT_CENTIDEG))


class Instrument(CoreModel):
    """The instrument, single source (A99, A103): the gateway builds GSAS-II's instrument parameters from it."""

    description: Optional[str] = Field(default=None, json_schema_extra=absent_not_null_schema(),
                                       description="Free text, e.g. the beamline")
    radiation: Radiation
    geometry: Geometry
    corrections: Corrections
    broadening: InstrumentBroadening

    _not_null = field_validator("description", mode="before")(_never_null)

    @model_serializer(mode="wrap")
    def _omit_absent(self, handler):
        data = handler(self)
        if self.description is None:
            data.pop("description", None)
        return data


def gsasii_iparm1(instrument: Instrument) -> dict:
    """GSAS-II's instrument parameters (``Iparm1``) built from the instrument block (A99, A103).

    Native form ``{key: [value, value, False]}``, keys in the order of the
    committed examples (N2); the setter then writes the values and refine flags.
    Pure: no GSAS-II import.
    """
    r, g, c, b = instrument.radiation, instrument.geometry, instrument.corrections, instrument.broadening
    values = {
        "Azimuth": g.azimuth, "Bank": g.bank, "Lam": r.wavelength.value, "Polariz.": c.polarization.value,
        "SH/L": c.axial_divergence.value, "Type": r.type,
        "U": b.U.value, "V": b.V.value, "W": b.W.value, "X": b.X.value, "Y": b.Y.value, "Z": b.Z.value,
        "Zero": c.zero_shift.value,
    }
    return {key: [v, v, False] for key, v in values.items()}


# --- background (A22.6-A22.8, A86, A89) ---------------------------------------

#: GSAS-II evaluates background peaks with these silent floors (``GSASIIpwd.py:1094-1096``, EB-35).
BACKGROUND_PEAK_FLOORS = {"intensities": 0.1, "pv_gaussian_sigma_sq": 0.01, "pv_lorentzian_gamma": 0.1}

_PEAK_LISTS = ("positions", "intensities", "pv_gaussian_sigma_sq", "pv_lorentzian_gamma")


def _peak_list_problems(peaks: CoreModel, floors: dict, quirk: str) -> list[InitErrorDetails]:
    """Same length for all four lists; each floored value at least its floor; errors per entry."""
    problems = []
    lengths = {name: len(getattr(peaks, name)) for name in _PEAK_LISTS}
    if len(set(lengths.values())) > 1:
        problems.append(InitErrorDetails(
            type=PydanticCustomError("peak_lists", "all four peak lists must have the same length, got {lengths}",
                                     {"lengths": ", ".join(f"{k}={v}" for k, v in lengths.items())}),
            loc=(), input=None))
    for name, floor in floors.items():
        for i, p in enumerate(getattr(peaks, name)):
            if not p.value >= floor:
                problems.append(InitErrorDetails(
                    type=PydanticCustomError(
                        "below_engine_floor",
                        "{name}[{i}] = {value} is below {floor}, the smallest value GSAS-II computes with; "
                        "it would silently use {floor} (GSAS-II quirk {quirk})",
                        {"name": name, "i": i, "value": p.value, "floor": floor, "quirk": quirk}),
                    loc=(name, i), input=p.model_dump()))
    return problems


class BackgroundPeaks(CoreModel):
    """Background single peaks (pseudo-Voigt): all four lists, the same length (A22.8, A86).

    ``pv_gaussian_sigma_sq`` is the Gaussian variance (centideg², GSAS-II's
    native quantity, A89); ``pv_lorentzian_gamma`` the Lorentzian width
    (centideg). Each position lies inside the fit window (checked by the
    payload).
    """

    positions: list[RefinableParameter] = Field(min_length=1, json_schema_extra=_unit(UNIT_DEG_2THETA))
    intensities: list[RefinableParameter] = Field(min_length=1, json_schema_extra=_unit(UNIT_ARBITRARY))
    pv_gaussian_sigma_sq: list[RefinableParameter] = Field(min_length=1, json_schema_extra=_unit(UNIT_CENTIDEG2))
    pv_lorentzian_gamma: list[RefinableParameter] = Field(min_length=1, json_schema_extra=_unit(UNIT_CENTIDEG))

    @model_validator(mode="after")
    def _lists(self) -> "BackgroundPeaks":
        problems = _peak_list_problems(self, BACKGROUND_PEAK_FLOORS, "EB-35")
        if problems:
            raise ValidationError.from_exception_data(type(self).__name__, problems)
        return self


def _no_background() -> ChebyshevBackground:
    """Background left out = none: one fixed Chebyshev term 0.0 (D1, A119)."""
    return ChebyshevBackground(num_coefficients=1, coefficients=[0.0], refine_flag=False)


class Background(CoreModel):
    """Background: a Chebyshev polynomial (GSAS-II ``chebyschev-1``) and optional single peaks.

    Left out, ``chebyshev`` is one fixed term 0.0 (no background), replacing
    GSAS-II's own constant 1.0 (EB-36, A119). Coefficients are GSAS-II's: the
    polynomial is in a scaled 2theta variable and coefficient *i* multiplies
    the Chebyshev polynomial of order *i* (A22.7).
    """

    chebyshev: ChebyshevBackground = Field(default_factory=_no_background)
    single_peaks: Optional[BackgroundPeaks] = Field(default=None, json_schema_extra=absent_not_null_schema(),
                                                   description="Left out: no background peaks")

    _not_null = field_validator("chebyshev", "single_peaks", mode="before")(_never_null)

    @model_serializer(mode="wrap")
    def _omit_absent(self, handler):
        data = handler(self)
        if self.single_peaks is None:
            data.pop("single_peaks", None)
        return data


# --- Peak List single peaks (gsasii.spf; A90) --------------------------------

#: GSAS-II evaluates Peak List peaks with these floors (``GSASIIpwd.py:1662, 1668``).
PEAK_LIST_FLOORS = {"pv_gaussian_sigma_sq": 0.001, "pv_lorentzian_gamma": 0.001}


class SinglePeaks(CoreModel):
    """Peak List peaks for single peak fitting: all four lists, the same length.

    Widths as for background peaks (σ² in centideg², γ in centideg), each at
    least GSAS-II's floor 0.001 (A90).
    """

    positions: list[RefinableParameter] = Field(min_length=1, json_schema_extra=_unit(UNIT_DEG_2THETA))
    intensities: list[RefinableParameter] = Field(min_length=1, json_schema_extra=_unit(UNIT_ARBITRARY))
    pv_gaussian_sigma_sq: list[RefinableParameter] = Field(min_length=1, json_schema_extra=_unit(UNIT_CENTIDEG2))
    pv_lorentzian_gamma: list[RefinableParameter] = Field(min_length=1, json_schema_extra=_unit(UNIT_CENTIDEG))

    @model_validator(mode="after")
    def _lists(self) -> "SinglePeaks":
        problems = _peak_list_problems(self, PEAK_LIST_FLOORS, "EB-37")
        if problems:
            raise ValidationError.from_exception_data(type(self).__name__, problems)
        return self


# --- phase (A93, A95, A107) ---------------------------------------------------

#: Size/strain applied when a recipe leaves them out (A87, A107): no sample broadening.
DEFAULT_CRYSTALLITE_SIZE = 10.0
DEFAULT_MICROSTRAIN = 0.0
DEFAULT_LG_ETA = 1.0


class SizeBroadening(CoreModel):
    """Crystallite-size broadening; isotropic only in gsasii 1.0.0."""

    model: Literal["isotropic"] = Field(description="'isotropic' (uniaxial and ellipsoidal are not supported)")
    isotropic_size: RefinableParameter = Field(json_schema_extra=_unit(UNIT_MICROMETRE), description="> 0")
    LG_eta: RefinableParameter = Field(json_schema_extra=_unit(UNIT_DIMENSIONLESS),
                                       description="Lorentzian fraction of the size profile (1 = Lorentzian)")

    @field_validator("isotropic_size")
    @classmethod
    def _gsasii_size(cls, v: RefinableParameter) -> RefinableParameter:
        if not v.value > 0:
            raise ValueError(f"isotropic_size must be > 0 (a crystallite has a size), got {v.value}")
        return v


class StrainBroadening(CoreModel):
    """Microstrain broadening; isotropic only in gsasii 1.0.0."""

    model: Literal["isotropic"] = Field(description="'isotropic' (uniaxial and generalized are not supported)")
    isotropic_strain: RefinableParameter = Field(json_schema_extra=_unit(UNIT_MICROSTRAIN),
                                                 description="Microstrain, Δd/d × 10^6")
    LG_eta: RefinableParameter = Field(json_schema_extra=_unit(UNIT_DIMENSIONLESS),
                                       description="Lorentzian fraction of the strain profile (1 = Lorentzian)")


def _default_size() -> SizeBroadening:
    return SizeBroadening(model="isotropic", isotropic_size=[DEFAULT_CRYSTALLITE_SIZE, False],
                          LG_eta=[DEFAULT_LG_ETA, False])


def _default_strain() -> StrainBroadening:
    return StrainBroadening(model="isotropic", isotropic_strain=[DEFAULT_MICROSTRAIN, False],
                            LG_eta=[DEFAULT_LG_ETA, False])


class PeakBroadening(CoreModel):
    """Sample broadening. Each part left out is 10 um / 0 microstrain, isotropic, fixed, with a warning (A107)."""

    size_broadening: SizeBroadening = Field(default_factory=_default_size)
    strain_broadening: StrainBroadening = Field(default_factory=_default_strain)

    _not_null = field_validator("size_broadening", "strain_broadening", mode="before")(_never_null)

    def defaults_applied(self) -> tuple[str, ...]:
        """The parts the recipe left out (the A107 default holds them)."""
        return tuple(n for n in ("size_broadening", "strain_broadening") if n not in self.model_fields_set)


class GsasiiPhase(Phase[RefinableParameter]):
    """A gsasii phase: core's space group, cell and atoms, plus ``scale`` and ``peak_broadening`` (A93).

    The phase name is the key in ``payload.phases`` (A100). GSAS-II-specific
    rules: the space group must be a setting GSAS-II reads as itself (EB-03,
    EB-41), and a refined cell must refine whole GSAS-II cell groups (A95).
    """

    scale: RefinableParameter = Field(json_schema_extra=_unit(UNIT_ARBITRARY),
                                      description="Phase scale factor, >= 0 (0 allowed for reusable recipes)")
    peak_broadening: PeakBroadening = Field(
        default_factory=PeakBroadening,
        description="Left out (in whole or part): 10 um, 0 microstrain, isotropic, fixed, with a warning (A107)")

    _gsasii_not_null = field_validator("peak_broadening", mode="before")(_never_null)

    @field_validator("scale")
    @classmethod
    def _gsasii_scale(cls, v: RefinableParameter) -> RefinableParameter:
        if v.value < 0:
            raise ValueError(f"scale must be >= 0, got {v.value}")
        return v

    @field_validator("space_group")
    @classmethod
    def _gsasii_setting(cls, v: str) -> str:
        gsasii_space_group(v)
        return v

    @model_validator(mode="after")
    def _gsasii_cell_groups(self) -> "GsasiiPhase":
        problems = []
        for group in gsasii_cell_groups(self.space_group):
            flags = [getattr(self.unit_cell, n).refine_flag for n in group]
            if len(set(flags)) > 1:
                refined = [n for n, f in zip(group, flags) if f]
                for name, flag in zip(group, flags):
                    if flag != flags[0]:
                        problems.append(InitErrorDetails(
                            type=PydanticCustomError(
                                "gsasii_cell_group",
                                "GSAS-II refines {group} together (it varies the reciprocal metric tensor), but "
                                "only {refined} is refined; refine all of {group} or none",
                                {"group": ", ".join(group), "refined": ", ".join(refined)}),
                            loc=("unit_cell", name), input=getattr(self.unit_cell, name).model_dump()))
        if problems:
            raise ValidationError.from_exception_data(type(self).__name__, problems)
        return self


# --- payloads and recipes ---------------------------------------------------


class RietveldControls(CoreModel):
    """Refinement controls; ``refinement_cycles == 1`` is a simulation (nothing refined, A88)."""

    refinement_cycles: CoreInt = Field(default=5, ge=1, description="GSAS-II least-squares cycles")


class SinglePeakFittingMode(CoreModel):
    use_instrument_profile: StrictBool = Field(
        description="True: peak widths from the instrument profile U..Z (GSAS-II 'useIP'; the peaks' own "
                    "sigma^2/gamma are not used and may not be refined); False: each peak's own widths, which may "
                    "be refined ('hold'; U..Z have no effect and may not be refined)")


class SpfControls(CoreModel):
    """Single-peak-fitting controls."""

    refinement_cycles: CoreInt = Field(default=5, ge=1, description="GSAS-II peak-fit cycles")
    single_peak_fitting_mode: SinglePeakFittingMode


def _refined_flags(obj: Any, loc: tuple = ()) -> list[tuple]:
    """Locations of every refine flag set to true in a validated model tree (A88)."""
    if isinstance(obj, RefinableParameter):
        return [loc] if obj.refine_flag else []
    if isinstance(obj, ChebyshevBackground):
        return [loc + ("refine_flag",)] if obj.refine_flag else []
    if isinstance(obj, CoreModel):
        return [hit for name in type(obj).model_fields for hit in _refined_flags(getattr(obj, name), loc + (name,))]
    if isinstance(obj, dict):
        return [hit for k, v in obj.items() for hit in _refined_flags(v, loc + (k,))]
    if isinstance(obj, list):
        return [hit for i, v in enumerate(obj) for hit in _refined_flags(v, loc + (i,))]
    return []


class _Payload(CoreModel):
    """Fields both gsasii workflows share."""

    xrd_data: XRDData
    instrument: Instrument
    fit_range: Optional[FitRange] = Field(default=None, json_schema_extra=absent_not_null_schema(),
                                          description="Left out: the data's full 2theta range")
    background: Background = Field(default_factory=Background)

    _not_null = field_validator("fit_range", "background", mode="before")(_never_null)

    @model_serializer(mode="wrap")
    def _omit_absent(self, handler):
        data = handler(self)
        if self.fit_range is None:
            data.pop("fit_range", None)
        return data

    @model_validator(mode="after")
    def _payload_rules(self) -> "_Payload":
        problems = []
        try:
            check_fit_range_within_data(self.fit_range, self.xrd_data)
        except ValueError as exc:
            problems.append(InitErrorDetails(type=PydanticCustomError("fit_range", str(exc)), loc=("fit_range",),
                                             input=None if self.fit_range is None else self.fit_range.model_dump()))
        if self.background.single_peaks is not None:
            problems += self._outside_fit_window(self.background.single_peaks, ("background", "single_peaks"),
                                                 "background peak")
        problems += self._workflow_problems()
        if self.refinement_controls.refinement_cycles == 1:  # simulation: nothing refined (A88)
            for loc in _refined_flags(self):
                problems.append(InitErrorDetails(
                    type=PydanticCustomError(
                        "simulation_refines", "refinement_cycles is 1 (a simulation), so nothing may be "
                        "refined; set this refine flag to false"),
                    loc=loc, input=True))
        if problems:
            raise ValidationError.from_exception_data(type(self).__name__, problems)
        return self

    def _outside_fit_window(self, peaks, loc: tuple, what: str) -> list[InitErrorDetails]:
        """Peak positions outside the fit window (fit_range; open ends are the data limits; A86, A126)."""
        lo, hi = self.xrd_data.tth[0], self.xrd_data.tth[-1]
        if self.fit_range is not None:
            lo = lo if self.fit_range.min is None else self.fit_range.min
            hi = hi if self.fit_range.max is None else self.fit_range.max
        return [InitErrorDetails(
                    type=PydanticCustomError(
                        "outside_fit_window", "{what} position {value} is outside the fit window "
                        "[{lo}, {hi}] (fit_range; open ends are the data limits)",
                        {"what": what, "value": p.value, "lo": lo, "hi": hi}),
                    loc=loc + ("positions", i), input=p.model_dump())
                for i, p in enumerate(peaks.positions) if not lo <= p.value <= hi]

    def _workflow_problems(self) -> list[InitErrorDetails]:
        """Rules of one workflow's own fields (located from the payload)."""
        return []


class RietveldPayload(_Payload):
    """``gsasii.rietveld`` payload."""

    phases: dict[CoreName, GsasiiPhase] = Field(min_length=1, json_schema_extra=name_keyed)
    refinement_controls: RietveldControls

    @field_validator("phases")
    @classmethod
    def _phase_names(cls, v: dict) -> dict:
        check_names_unique_ignoring_case(v, "phase")
        return v

    def warnings(self) -> list[StructuredWarning]:
        """The size/strain defaults applied (A107), paths relative to the payload."""
        found = []
        for name, phase in self.phases.items():
            pb = phase.peak_broadening
            for part in pb.defaults_applied():
                if part == "size_broadening":
                    what = (f"isotropic crystallite size {DEFAULT_CRYSTALLITE_SIZE:g} um", "1 um")
                else:
                    what = (f"isotropic microstrain {DEFAULT_MICROSTRAIN:g}", "1000 microstrain")
                found.append(StructuredWarning(
                    code="gsasii_broadening_default_applied",
                    message=(f"phase {name!r}: {part} left out, so PowderLine applies {what[0]}, LG_eta "
                             f"{DEFAULT_LG_ETA:g}, fixed (no sample broadening) instead of GSAS-II's own default "
                             f"({what[1]}, which broadens peaks); state {part} to choose"),
                    field_path=f"phases.{name}.peak_broadening.{part}",
                ))
        return found


#: Instrument profile terms that set Peak List peak widths (GSAS-II ``getCWsig`` / ``getCWgam``).
PROFILE_TERMS = ("U", "V", "W", "X", "Y", "Z")


class SpfPayload(_Payload):
    """``gsasii.spf`` payload: single peak fitting, no phases.

    The peak widths come either from the instrument profile
    (``use_instrument_profile: true``: U..Z may be refined, the peaks' own
    sigma^2/gamma are not used and may not be refined) or from each peak
    (``false``: the peaks' widths may be refined, the profile terms have no
    effect and may not be refined). GSAS-II silently ignores a refine flag on
    the side not in use (A127, EB-53), so such a flag is an error.
    """

    single_peaks: SinglePeaks
    refinement_controls: SpfControls

    def _workflow_problems(self) -> list[InitErrorDetails]:
        problems = self._outside_fit_window(self.single_peaks, ("single_peaks",), "Peak List peak")
        use_ip = self.refinement_controls.single_peak_fitting_mode.use_instrument_profile
        if use_ip:
            for name in ("pv_gaussian_sigma_sq", "pv_lorentzian_gamma"):
                for i, p in enumerate(getattr(self.single_peaks, name)):
                    if p.refine_flag:
                        problems.append(InitErrorDetails(
                            type=PydanticCustomError(
                                "spf_width_source",
                                "use_instrument_profile is true, so the peak widths come from the instrument "
                                "profile and GSAS-II would ignore this peak's own {name}; set its refine flag to "
                                "false, or set use_instrument_profile to false to refine each peak's widths "
                                "(GSAS-II quirk EB-53)", {"name": name}),
                            loc=("single_peaks", name, i), input=p.model_dump()))
        else:
            for name in PROFILE_TERMS:
                p = getattr(self.instrument.broadening, name)
                if p.refine_flag:
                    problems.append(InitErrorDetails(
                        type=PydanticCustomError(
                            "spf_width_source",
                            "use_instrument_profile is false, so each peak has its own widths and the instrument "
                            "profile term {name} has no effect: GSAS-II would not refine it; set its refine flag "
                            "to false, or set use_instrument_profile to true (GSAS-II quirk EB-53)",
                            {"name": name}),
                        loc=("instrument", "broadening", name), input=p.model_dump()))
        return problems


class _GsasiiRecipe(CoreRecipe):
    @model_validator(mode="after")
    def _versions(self) -> "_GsasiiRecipe":
        check_engine_schema_version(self.core_schema_version, self.engine_schema_version,
                                    gateway="gsasii", capabilities=DECLARATIONS)
        return self


class GsasiiRietveldRecipe(_GsasiiRecipe):
    """A ``gsasii.rietveld`` recipe."""

    schema_name: Literal["gsasii.rietveld"]
    payload: RietveldPayload


class GsasiiSpfRecipe(_GsasiiRecipe):
    """A ``gsasii.spf`` recipe."""

    schema_name: Literal["gsasii.spf"]
    payload: SpfPayload


GsasiiRecipe = Annotated[Union[GsasiiRietveldRecipe, GsasiiSpfRecipe], Field(discriminator="schema_name")]
_RECIPE = TypeAdapter(GsasiiRecipe, config={"title": "GsasiiRecipe"})
_RECIPE_MODELS = {"gsasii.rietveld": GsasiiRietveldRecipe, "gsasii.spf": GsasiiSpfRecipe}


def is_native_recipe(recipe) -> bool:
    """True for a ``gsasii.*`` recipe (dict or model); False for anything else.

    Any ``gsasii.`` name counts, so a mistyped one (``gsasii.reitveld``) is
    reported against the gsasii schema names.
    """
    if isinstance(recipe, (GsasiiRietveldRecipe, GsasiiSpfRecipe)):
        return True
    name = recipe.get("schema_name") if isinstance(recipe, dict) else None
    return isinstance(name, str) and name.startswith("gsasii.")


def validate_recipe(recipe) -> GsasiiRietveldRecipe | GsasiiSpfRecipe:
    """Validate a ``gsasii.*`` recipe (dict or model); return the model (idempotent, A50).

    Errors are located from the recipe's top level (``payload.phases.<name>...``);
    an unknown ``schema_name`` is reported at ``schema_name``.
    """
    if isinstance(recipe, (GsasiiRietveldRecipe, GsasiiSpfRecipe)):
        return recipe
    if not isinstance(recipe, dict):
        return _RECIPE.validate_python(recipe)
    model = _RECIPE_MODELS.get(recipe.get("schema_name"))
    if model is None:
        raise ValidationError.from_exception_data("GsasiiRecipe", [InitErrorDetails(
            type=PydanticCustomError("schema_name", "schema_name '{name}' is not a gsasii workflow; expected one of "
                                                    "{names}", {"name": recipe.get("schema_name"),
                                                                "names": ", ".join(SCHEMA_NAMES)}),
            loc=("schema_name",), input=recipe.get("schema_name"))])
    return model.model_validate(recipe)
