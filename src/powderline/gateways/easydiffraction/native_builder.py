"""Native ``easydiffraction.rietveld`` recipe -> easydiffraction ``Project`` (re/06, Phase 3).

Consumes the **validated** :class:`~powderline.gateways.easydiffraction.schema.EasydiffractionRecipe`
(A39), never the recipe dict: every value handed to easydiffraction is the
model's exact value (A120, A121), in easydiffraction's native units (A4), so
there is no unit conversion here and no GSAS-II import anywhere in this module.

easydiffraction itself is imported lazily, inside the functions that build the
``Project`` (runtime layer only): the pure parts (space-group mapping, datablock
naming, window weighting, the CIF data text) run in the core-only environment
and are unit-tested there.

Rules implemented (decision log numbers in brackets):

- **Data (A158):** every data point is handed to easydiffraction; a point in the
  fit window (core :func:`~powderline.schema_core.fit_limits_on_data`, A149/A154)
  with a positive weight gets sigma = 1/sqrt(w), every other point
  sigma = :data:`SIGMA_WEIGHTLESS` (1e8), so reflection tails that cross the
  window are calculated but only the window is fitted. The data go in as a CIF
  loop at full precision, positional notation (EB-72, EB-89; A182).
- **Names:** a phase's datablock is its name lowercased (A112 guarantees
  uniqueness ignoring case; EB-45).
- **Space groups:** easydiffraction's short spaced symbol + ``coord_system_code``
  from :mod:`.space_group_support`, per calculator (A163, A174).
- **Atom order (A169 b):** anisotropic atoms are handed first (easydiffraction
  0.21.1 CrysPy refines a Uij wrongly when an isotropic atom precedes it,
  EB-86); reports stay keyed by label.
- **Background (A183):** the schema states a Chebyshev background per phase;
  easydiffraction has one per experiment, so at most one phase may state one.
- **Site check (EB-47; A176, A164, A174):** after the build, every atom's
  easydiffraction Wyckoff multiplicity must equal core's, and the held position
  must be the stated one modulo a lattice translation (accepted with the
  informative warning ``easydiffraction_position_translated``); any other move
  is an error. With CrysPy, a special position with a free coordinate must be
  written in the pattern of easydiffraction's Wyckoff template (EB-77), and the
  nine origin-choice-1 settings it computes wrongly are refused; the error names
  the position to write, never substitutes it (the A117 pattern).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from fractions import Fraction
from itertools import product
from pathlib import Path
from typing import Any, Optional

import numpy as np

from powderline.exceptions import StructuredWarning
from powderline.gateways.easydiffraction.errors import EasyDiffractionTranslationError
from powderline.gateways.easydiffraction.schema import EasydiffractionPhase, EasydiffractionRecipe
from powderline.gateways.easydiffraction.space_group_support import (
    CRYSPY_ORIGIN1_UNSUPPORTED,
    map_space_group,
)
from powderline.schema_core import fit_limits_on_data
from powderline.symmetry import SiteAnalysis, orbit

#: sigma handed for a data point outside the fit window or with zero weight (A158).
SIGMA_WEIGHTLESS = 1e8
#: Name of the single experiment datablock (``data_powderline`` in the CIF).
EXPERIMENT_NAME = "powderline"
#: File the handed data loop is written to (a record of exactly what easydiffraction read).
DATA_CIF_NAME = "easydiffraction_data.cif"
#: Recipe ``peak_type`` -> easydiffraction ``peak.type`` (A162, EB-76).
PEAK_TYPES = {"pseudo-voigt": "cwl-pseudo-voigt", "tch": "cwl-thompson-cox-hastings"}
#: Core cell field -> easydiffraction ``cell`` attribute.
CELL_ATTRS = {"a": "length_a", "b": "length_b", "c": "length_c",
              "alpha": "angle_alpha", "beta": "angle_beta", "gamma": "angle_gamma"}
#: Core Uij component -> easydiffraction ``atom_site_aniso`` attribute.
UIJ_ATTRS = {"U11": "adp_11", "U22": "adp_22", "U33": "adp_33", "U12": "adp_12", "U13": "adp_13", "U23": "adp_23"}
#: Two fractional coordinates are the same (modulo 1) within this (A186): well below core's
#: special-position tolerance (5e-6) and easydiffraction's Wyckoff tolerance (1e-3), above float noise.
POSITION_TOL = 1e-6


class SiteError(EasyDiffractionTranslationError, ValueError):
    """easydiffraction cannot use an atom position as stated (EB-47, EB-77); the message names the remedy."""


@dataclass(frozen=True)
class BuildResult:
    """What :func:`build_project` produced."""

    project: Any
    #: easydiffraction parameter path -> ``{"path": recipe field path, "unit": ..., ...}`` (one per freed parameter).
    manifest: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)  # list[StructuredWarning]
    #: True for every data point in the fit window with a positive weight (the points core scores, A131).
    window_mask: Optional[np.ndarray] = None
    #: phase name -> easydiffraction datablock (structure) name.
    datablock_names: dict = field(default_factory=dict)


# --- pure helpers (core-only environment) ----------------------------------


def resolve_space_group(canonical: str, calculator: str) -> tuple[str, str]:
    """easydiffraction's ``(name_h_m, coord_system_code)`` for a canonical gemmi name, per calculator.

    Wraps :func:`~powderline.gateways.easydiffraction.space_group_support.map_space_group`;
    an unsupported setting raises :class:`EasyDiffractionTranslationError` naming the
    calculator and the remedy: the other calculator, or the ``:2`` origin for an
    origin-choice-1 setting refused for CrysPy (A174).
    """
    try:
        return map_space_group(canonical, calculator)
    except ValueError as exc:
        other = "crysfml" if calculator == "cryspy" else "cryspy"
        remedy = f"use calculator {other!r} if it supports this setting"
        if canonical.endswith(":1"):
            remedy = f"state the origin-choice-2 setting {canonical[:-2] + ':2'!r}, or {remedy}"
        raise EasyDiffractionTranslationError(
            f"space group {canonical!r} is not supported by the {calculator!r} calculator "
            f"(payload.refinement_controls.calculator): {remedy} (A163, A174)"
        ) from exc


def datablock_name(phase_name: str) -> str:
    """The easydiffraction structure (datablock) name of a phase: its name lowercased (A112, EB-45)."""
    return phase_name.lower()


def window_sigma(tth, weights, lo: float, hi: float) -> tuple[np.ndarray, np.ndarray]:
    """``(mask, sigma)`` for every data point (A158).

    ``mask`` is True where ``lo <= tth <= hi`` and ``w > 0``; there
    ``sigma = 1/sqrt(w)``, elsewhere :data:`SIGMA_WEIGHTLESS`.
    """
    tth = np.asarray(tth, dtype=float)
    w = np.asarray(weights, dtype=float)
    mask = (tth >= lo) & (tth <= hi) & (w > 0)
    sigma = np.full(tth.shape, SIGMA_WEIGHTLESS, dtype=float)
    sigma[mask] = 1.0 / np.sqrt(w[mask])
    return mask, sigma


def prepare_data(payload) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """``(tth, intensity, sigma, mask)`` of the full pattern, weighted per :func:`window_sigma`."""
    lo, hi = fit_limits_on_data(payload.fit_range, payload.xrd_data)
    tth = np.asarray(payload.xrd_data.tth, dtype=float)
    itth = np.asarray(payload.xrd_data.Itth, dtype=float)
    mask, sigma = window_sigma(tth, payload.xrd_data.Itth_weights, lo, hi)
    return tth, itth, sigma, mask


def cif_number(v: float) -> str:
    """A number for the CIF data loop: shortest digits that round-trip, positional notation.

    ``repr`` would write ``5e-05``, which easydiffraction's loop reader cannot parse (EB-89).
    """
    return np.format_float_positional(float(v), unique=True, trim="-")


def data_cif(tth, itth, sigma, name: str = EXPERIMENT_NAME) -> str:
    """The experiment CIF handed to ``ExperimentFactory.from_cif_str``: type + full-precision data loop (EB-72)."""
    head = [f"data_{name}",
            "_experiment_type.sample_form powder",
            '_experiment_type.beam_mode "constant wavelength"',
            "_experiment_type.radiation_probe xray",
            "_experiment_type.scattering_type bragg",
            "",
            "loop_",
            "_data.two_theta",
            "_data.intensity_meas",
            "_data.intensity_meas_su"]
    rows = [f"{cif_number(x)} {cif_number(y)} {cif_number(s)}" for x, y, s in zip(tth, itth, sigma)]
    return "\n".join(head + rows) + "\n"


def atom_order(phase: EasydiffractionPhase) -> list[tuple[str, Any]]:
    """``(label, atom)`` pairs in the order handed to easydiffraction: anisotropic atoms first (A169 b, EB-86)."""
    return sorted(phase.atoms.items(), key=lambda item: item[1].Uaniso is None)


def background_phase(payload) -> Optional[str]:
    """The name of the one phase stating a background (the experiment's), or ``None`` (A183).

    Raises :class:`EasyDiffractionTranslationError` when more than one phase states one:
    easydiffraction has a single background per experiment.
    """
    stated = [name for name, phase in payload.phases.items() if phase.background is not None]
    if len(stated) > 1:
        raise EasyDiffractionTranslationError(
            "easydiffraction has one background per experiment, but phases "
            f"{', '.join(repr(n) for n in stated)} each state one: state it in one phase only "
            "(payload.phases.<name>.background)"
        )
    return stated[0] if stated else None


# --- site check (EB-47, A164, A174, A176) -----------------------------------


@dataclass(frozen=True)
class SiteComparison:
    """How easydiffraction holds an atom against the recipe's statement.

    ``status``: ``"same"``; ``"translated"`` (the same position, moved by the lattice
    vector ``shift`` = held - stated; A176); ``"moved"`` (another position); or
    ``"multiplicity"`` (easydiffraction's site multiplicity differs from core's, EB-47).
    """

    status: str
    shift: tuple = (0, 0, 0)


def _compare_site(stated_xyz, held_xyz, core_mult: int, ed_mult: int) -> SiteComparison:
    """Pure comparison of the stated and held positions (modulo 1) and multiplicities."""
    if int(core_mult) != int(ed_mult):
        return SiteComparison("multiplicity")
    delta = np.asarray(held_xyz, dtype=float) - np.asarray(stated_xyz, dtype=float)
    shift = np.round(delta)
    if np.max(np.abs(delta - shift)) > POSITION_TOL:
        return SiteComparison("moved")
    shift = tuple(int(v) for v in shift)
    return SiteComparison("translated" if any(shift) else "same", shift)


def _coordinate(v: float) -> str:
    """A coordinate as a recipe states it (A120, A121): exactly when it has a finite decimal form, else to 6 decimals."""
    frac = Fraction(v).limit_denominator(48)
    if abs(float(frac) - v) < 1e-9:
        d = frac.denominator
        while d % 2 == 0:
            d //= 2
        while d % 5 == 0:
            d //= 5
        return repr(float(frac)) if d == 1 else f"{float(frac):.6f}"  # 1/5 -> 0.2; 1/3 -> 0.333333
    return repr(round(v, 10))


def _fmt_xyz(xyz) -> str:
    return "(" + ", ".join(_coordinate(float(v)) for v in xyz) + ")"


_TERM = re.compile(r"([+-]?)(\d+(?:/\d+)?)?\*?([xyz])?")


def _linear_form(expr: str) -> tuple[dict, Fraction]:
    """``"-2x+1/2"`` -> ``({"x": -2}, 1/2)``: coefficients per variable and the constant."""
    coefficients: dict = {}
    constant = Fraction(0)
    for sign, number, var in _TERM.findall(expr.replace(" ", "")):
        if not number and not var:
            continue
        k = Fraction(number) if number else Fraction(1)
        if sign == "-":
            k = -k
        if var:
            coefficients[var] = coefficients.get(var, Fraction(0)) + k
        else:
            constant += k
    return coefficients, constant


def _matches_template(template: str, xyz, tol: float = POSITION_TOL) -> bool:
    """True when ``xyz`` is written in the pattern of easydiffraction's Wyckoff ``template`` (modulo 1).

    ``template`` is one entry of easydiffraction's table, e.g. ``"(x,1/2,1/2)"``,
    ``"(x,-x,z)"``, ``"(x,2x,1/4)"``. The free variables are read from the
    components where they stand alone (``k·v + c``; ``v = (comp - c + n)/k`` for
    the ``|k|`` candidates ``n``), the template is evaluated at them and compared
    with ``xyz`` modulo 1. Pure: no easydiffraction.
    """
    forms = [_linear_form(part) for part in template.strip().strip("()").split(",")]
    if len(forms) != 3:
        return False
    xyz = [float(v) for v in xyz]
    candidates: dict = {}
    for var in "xyz":
        options = []
        for (coefs, const), value in zip(forms, xyz):
            if set(coefs) == {var}:
                k = coefs[var]
                options = [(value - float(const) + n) / float(k) for n in range(abs(k.numerator))]
                if k == 1:
                    break
        if options:
            candidates[var] = options
        elif any(var in coefs for coefs, _ in forms):
            return False  # the variable never stands alone: not a template form we can read
    names = list(candidates)
    for values in product(*(candidates[n] for n in names)):
        assignment = dict(zip(names, values))
        for (coefs, const), value in zip(forms, xyz):
            evaluated = float(const) + sum(float(k) * assignment[v] for v, k in coefs.items())
            d = evaluated - value
            if abs(d - round(d)) > tol:
                break
        else:
            return True
    return False


def _ed_wyckoff_template(name_h_m: str, code: str, letter: str) -> Optional[str]:
    """easydiffraction's template for a Wyckoff letter (``"(x,1/2,1/2)"``), or ``None`` when it has none."""
    from easydiffraction.crystallography.crystallography import space_group_wyckoff_table

    entry = space_group_wyckoff_table(name_h_m, code or None).get(letter)
    return None if entry is None else entry["coords_xyz"][0]


def origin1_problem(space_group: str, calculator: str, site: SiteAnalysis) -> Optional[str]:
    """The A174 refusal: CrysPy computes special positions wrongly in nine origin-choice-1 settings (EB-77)."""
    if calculator != "cryspy" or site.stabilizer_order == 1 or space_group not in CRYSPY_ORIGIN1_UNSUPPORTED:
        return None
    return (f"it is on a special position (multiplicity {site.multiplicity}) of {space_group!r}, an origin-choice-1 "
            f"setting in which CrysPy computes special positions wrongly (EB-77); state the origin-choice-2 setting "
            f"{space_group[:-2] + ':2'!r} (transforming the coordinates), or use calculator 'crysfml' (A174)")


def _held(ed_atom) -> tuple:
    return (float(ed_atom.fract_x.value), float(ed_atom.fract_y.value), float(ed_atom.fract_z.value))


def site_problems(phase_name: str, phase: EasydiffractionPhase, calculator: str, ed_phase) -> tuple[list, list]:
    """``(problems, warnings)`` of a built structure's atoms against the validated phase.

    ``ed_phase`` is the easydiffraction structure built from ``phase`` (its atom
    sites expose ``fract_x/y/z``, ``multiplicity`` and ``wyckoff_letter``).
    """
    problems: list[str] = []
    warnings: list[StructuredWarning] = []
    sg = phase.space_group
    name_h_m, code = resolve_space_group(sg, calculator)
    for label, atom in phase.atoms.items():
        site = phase.site(label)
        stated = (atom.x.value, atom.y.value, atom.z.value)
        where = f"payload.phases.{phase_name}.atoms.{label} at {_fmt_xyz(stated)}"
        why = origin1_problem(sg, calculator, site)
        if why is not None:
            problems.append(f"{where}: {why}")
            continue
        ed_atom = ed_phase.atom_sites[label]
        held = _held(ed_atom)
        ed_mult = int(ed_atom.multiplicity.value)
        found = _compare_site(stated, held, site.multiplicity, ed_mult)
        if found.status == "multiplicity":
            others = [p for p in orbit(sg, stated) if _compare_site(stated, p, 1, 1).status == "moved"][:3]
            hint = (f"; equivalent positions of the same site: {', '.join(_fmt_xyz(p) for p in others)}"
                    if others else "")
            problems.append(f"{where}: easydiffraction reads it as Wyckoff {ed_atom.wyckoff_letter.value!r} with "
                            f"multiplicity {ed_mult}, but its multiplicity in {sg!r} is {site.multiplicity}: the site "
                            f"weight, so the intensities, would be wrong (EB-47){hint}")
            continue
        if found.status == "moved":
            problems.append(f"{where}: easydiffraction holds it at {_fmt_xyz(held)}, which is not the stated position "
                            f"modulo a lattice translation (EB-47); state a position easydiffraction keeps")
            continue
        if found.status == "translated":
            warnings.append(StructuredWarning(
                code="easydiffraction_position_translated",
                message=(f"{where}: easydiffraction holds it at {_fmt_xyz(held)}, the same position translated by "
                         f"the lattice vector {found.shift}; the pattern is identical and results are reported in "
                         "the recipe's writing (A176)"),
                field_path=f"payload.phases.{phase_name}.atoms.{label}"))
        if calculator == "cryspy" and site.stabilizer_order > 1 and any(a != "FIXED" for a in site.axes):
            template = _ed_wyckoff_template(name_h_m, code, ed_atom.wyckoff_letter.value)
            if template is not None and not _matches_template(template, stated):
                suggestion = next((p for p in orbit(sg, stated) if _matches_template(template, p)), None)
                remedy = (f"state the equivalent position {_fmt_xyz(suggestion)} instead" if suggestion is not None
                          else "no equivalent position in the template's pattern was found")
                aniso = ("; its Uaniso must be transformed by the same symmetry operation (U' = R U R^T)"
                         if atom.Uaniso is not None else "")
                problems.append(f"{where}: with CrysPy, a special position with a free coordinate must be written in "
                                f"the pattern of easydiffraction's Wyckoff template {template} for site "
                                f"{ed_atom.wyckoff_letter.value!r}; another image of the orbit gives wrong structure "
                                f"factors (EB-77); {remedy}{aniso} (A164)")
    return problems, warnings


def check_sites(phase_name: str, phase: EasydiffractionPhase, calculator: str, ed_phase) -> list:
    """Raise :class:`SiteError` listing every problem :func:`site_problems` finds; return its warnings."""
    problems, warnings = site_problems(phase_name, phase, calculator, ed_phase)
    if problems:
        raise SiteError("easydiffraction cannot use these atom positions as stated:\n"
                        + "\n".join(f"  - {p}" for p in problems))
    return warnings


# --- runtime layer (imports easydiffraction) -------------------------------


def _import_easydiffraction():
    """The easydiffraction names the builder uses; raises ``ImportError`` with the install hint."""
    try:
        from easydiffraction import ExperimentFactory, Project, StructureFactory
        from easydiffraction.utils.logging import Logger
    except ImportError as exc:
        raise ImportError(
            "easydiffraction is not installed. Install PowderLine's optional engine "
            "environment: `pixi install -e easydiff` and run via `pixi run -e easydiff ...`"
        ) from exc
    return ExperimentFactory, Project, StructureFactory, Logger


def _set(obj: Any, attr: str, value: Any) -> None:
    """``setattr`` that refuses an attribute easydiffraction does not have (a typo would otherwise be silent)."""
    if not hasattr(obj, attr):
        raise AttributeError(
            f"easydiffraction {type(obj).__name__} has no attribute {attr!r} "
            "(PowderLine's attribute map is out of date for this easydiffraction version)"
        )
    setattr(obj, attr, value)


def _build_structure(StructureFactory, name: str, phase: EasydiffractionPhase, calculator: str):
    """One easydiffraction structure from a validated phase: space group, cell and atoms, values only."""
    short_name, code = resolve_space_group(phase.space_group, calculator)
    structure = StructureFactory.from_scratch(name=datablock_name(name))
    _set(structure.space_group, "name_h_m", short_name)
    if code:
        _set(structure.space_group, "coord_system_code", code)
    for member, attr in CELL_ATTRS.items():
        _set(structure.cell, attr, getattr(phase.unit_cell, member).value)
    for label, atom in atom_order(phase):
        structure.atom_sites.create(id=label, type_symbol=atom.element,
                                    fract_x=atom.x.value, fract_y=atom.y.value, fract_z=atom.z.value,
                                    occupancy=atom.occupancy.value)
        site = structure.atom_sites[label]
        if atom.ADP == "Uiso":
            _set(site, "adp_type", "Uiso")
            _set(site, "adp_iso", atom.Uiso.value)
        else:
            _set(site, "adp_type", "Uani")
            aniso = structure.atom_site_aniso[label]
            for component, attr in UIJ_ATTRS.items():
                _set(aniso, attr, getattr(atom.Uaniso, component).value)
    return structure


def _build_experiment(ExperimentFactory, payload, cif: str, names: dict):
    """The experiment: data, calculator and peak type, instrument values, linked phases, background."""
    controls = payload.refinement_controls
    expt = ExperimentFactory.from_cif_str(cif)
    # The calculator must be chosen before the peak type (the setter validates against the
    # active calculator), and both before any peak parameter (switching the type resets them).
    _set(expt.calculator, "type", controls.calculator)
    _set(expt.peak, "type", PEAK_TYPES[controls.peak_type])
    if controls.calculator == "cryspy":  # the reflection cutoff is a CrysPy parameter (A162)
        _set(expt.peak, "cutoff_fwhm", controls.cutoff_fwhm)

    inst = payload.instrument
    _set(expt.instrument, "setup_wavelength", inst.radiation.wavelength.value)
    if inst.radiation.ka2_wavelength is not None:
        _set(expt.instrument, "setup_wavelength_2", inst.radiation.ka2_wavelength)
        _set(expt.instrument, "setup_wavelength_2_to_1_ratio", inst.radiation.ka2_ratio)
    corr = inst.corrections
    _set(expt.instrument, "calib_twotheta_offset", corr.zero.value)
    _set(expt.instrument, "setup_polarization_coefficient", corr.polarization.value)
    if corr.polarization_monochromator_angle is not None:
        _set(expt.instrument, "setup_monochromator_twotheta", corr.polarization_monochromator_angle.value)
    if corr.displacement is not None:
        _set(expt.instrument, "calib_sample_displacement", corr.displacement.value)
    if corr.transparency is not None:
        _set(expt.instrument, "calib_sample_transparency", corr.transparency.value)
    broad = inst.broadening
    for key in ("u", "v", "w"):
        _set(expt.peak, f"broad_gauss_{key}", getattr(broad, key).value)
    for key in ("x", "y"):
        _set(expt.peak, f"broad_lorentz_{key}", getattr(broad, key).value)

    for name, phase in payload.phases.items():
        expt.linked_structures.create(structure_id=names[name], scale=phase.scale.value)

    bg_phase = background_phase(payload)
    if bg_phase is not None:
        _set(expt.background, "type", "chebyshev")
        for k, coef in enumerate(payload.phases[bg_phase].background.coefficients):
            expt.background.create(id=str(k), order=k, coef=coef)
    return expt


def build_project(recipe: EasydiffractionRecipe, workdir) -> BuildResult:
    """Build the easydiffraction ``Project`` of a validated native recipe.

    Writes the handed data loop to ``workdir / DATA_CIF_NAME``. Values and the
    site check here; the refinement flags are applied by the next step.

    Raises ``ImportError`` without easydiffraction,
    :class:`EasyDiffractionTranslationError` for an unsupported setting and
    :class:`SiteError` for a position easydiffraction cannot use as stated.
    """
    ExperimentFactory, Project, StructureFactory, Logger = _import_easydiffraction()
    Logger.configure(reaction=Logger.Reaction.RAISE)  # easydiffraction's errors raise instead of logging (EB-48)
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    payload = recipe.payload
    calculator = payload.refinement_controls.calculator

    tth, itth, sigma, mask = prepare_data(payload)
    cif = data_cif(tth, itth, sigma)
    (workdir / DATA_CIF_NAME).write_text(cif, encoding="utf-8", newline="\n")

    names = {name: datablock_name(name) for name in payload.phases}
    structures = [_build_structure(StructureFactory, name, phase, calculator)
                  for name, phase in payload.phases.items()]
    expt = _build_experiment(ExperimentFactory, payload, cif, names)

    project = Project()
    for structure in structures:
        project.structures.add(structure)
    project.experiments.add(expt)

    warnings: list[StructuredWarning] = []
    for name, phase in payload.phases.items():  # easydiffraction detects the Wyckoff sites at build time (EB-74)
        warnings += check_sites(name, phase, calculator, project.structures[names[name]])

    return BuildResult(project=project, manifest={}, warnings=warnings, window_mask=mask, datablock_names=names)
