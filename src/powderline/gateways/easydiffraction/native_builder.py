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

- **Data (A158):** easydiffraction is handed exactly the window's weighted
  points (core :func:`~powderline.schema_core.fit_limits_on_data`, A149/A154;
  ``w > 0``), with sigma = 1/sqrt(w) — the same points core scores (A131), so
  the Chebyshev basis spans the window and lmfit's N is core's N. Handing the
  full pattern with weightless points would understate every ESD by
  sqrt((N_window - P)/(N_all - P)) and span the basis over the data (A158
  evidence; the tail loss from reflections beyond the window is <= 3e-4 sigma).
  The data go in as a CIF loop at full precision, positional notation (EB-72,
  EB-89; A182).
- **Names:** a phase's datablock is its name lowercased (A112 guarantees
  uniqueness ignoring case; EB-45).
- **Space groups:** easydiffraction's short spaced symbol + ``coord_system_code``
  from :mod:`.space_group_support`, per calculator (A163, A174).
- **Atom order (A169 b):** anisotropic atoms are handed first (easydiffraction
  0.21.1 CrysPy refines a Uij wrongly when an isotropic atom precedes it,
  EB-86); reports stay keyed by label.
- **Background (A189, D1):** the payload states the experiment's Chebyshev
  background (easydiffraction has one per experiment); left out = one fixed
  0.0 term, which is handed as such.
- **Site check (EB-47; A176, A164, A174):** after the build, every atom's
  easydiffraction Wyckoff multiplicity must equal core's, and the held position
  must be the stated one modulo a lattice translation (accepted with the
  informative warning ``easydiffraction_position_translated``); any other move
  is an error. With CrysPy, a special position with a free coordinate must be
  written in the pattern of easydiffraction's Wyckoff template (EB-77), and the
  nine origin-choice-1 settings it computes wrongly are refused; the error names
  the position to write, never substitutes it (the A117 pattern).
- **Peak widths (A171, A178; EB-87):** a negative Gaussian FWHM² (U tan²θ +
  V tanθ + W) or Lorentzian FWHM (X tanθ + Y/cosθ) gives a wrong pattern without
  a message, and easydiffraction computes reflections just outside the handed
  points. The stated widths are checked on a 0.01° grid over the window widened
  by ``WIDTH_MARGIN`` (1°) plus the stated zero, displacement and transparency
  corrections; a negative width there is an error naming the 2θ range
  (:func:`check_peak_widths`). The runner re-checks the refined widths.
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
from powderline.gateways.easydiffraction.schema import (
    UNIT_DEG2_FWHM,
    UNIT_DEG_FWHM,
    EasydiffractionPhase,
    EasydiffractionRecipe,
)
from powderline.gateways.easydiffraction.space_group_support import (
    CRYSPY_ORIGIN1_UNSUPPORTED,
    map_space_group,
)
from powderline.schema_core import (
    UNIT_ANGSTROM,
    UNIT_ANGSTROM2,
    UNIT_ARBITRARY,
    UNIT_DEG_2THETA,
    UNIT_DEGREE,
    fit_limits_on_data,
)
from powderline.symmetry import SiteAnalysis, Ties, cell_tie_groups, coupling_groups, orbit

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
#: Unit of a fractional coordinate in the manifest.
UNIT_FRACTIONAL = "fractional"
#: Two fractional coordinates are the same (modulo 1) within this (A186): well below core's
#: special-position tolerance (5e-6) and easydiffraction's Wyckoff tolerance (1e-3), above float noise.
POSITION_TOL = 1e-6
#: Peak widths are checked over the fit window widened by this (degrees 2θ) plus the stated
#: zero, displacement and transparency corrections (A178): a reflection just outside the
#: window still reaches it (its tail moved the first window point by 12σ in the S5 probe).
WIDTH_MARGIN = 1.0
#: Step of the 2θ grid the widths are checked on (degrees).
WIDTH_GRID_STEP = 0.01
#: The two peak widths easydiffraction computes from U, V, W, X, Y (EB-87): name -> formula text.
WIDTH_FORMULAS = {"gaussian": "Gaussian FWHM^2 = U tan^2(theta) + V tan(theta) + W",
                  "lorentzian": "Lorentzian FWHM = X tan(theta) + Y / cos(theta)"}


class SiteError(EasyDiffractionTranslationError, ValueError):
    """easydiffraction cannot use an atom position as stated (EB-47, EB-77); the message names the remedy."""


class NegativeWidthError(EasyDiffractionTranslationError, ValueError):
    """The stated profile makes a peak width negative where easydiffraction computes reflections (A171, A178)."""


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


def window_mask(tth, weights, lo: float, hi: float) -> np.ndarray:
    """True where ``lo <= tth <= hi`` and ``w > 0``: the points core scores (A131) and the only points handed to easydiffraction (A158)."""
    tth = np.asarray(tth, dtype=float)
    w = np.asarray(weights, dtype=float)
    return (tth >= lo) & (tth <= hi) & (w > 0)


def prepare_data(payload) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """``(tth, intensity, sigma, mask)``: the window's weighted points only (A158, crop), ``sigma = 1/sqrt(w)``.

    ``mask`` indexes the handed points in the full pattern (for core ``fitstats``
    and the fit-profile report).
    """
    lo, hi = fit_limits_on_data(payload.fit_range, payload.xrd_data)
    tth = np.asarray(payload.xrd_data.tth, dtype=float)
    itth = np.asarray(payload.xrd_data.Itth, dtype=float)
    w = np.asarray(payload.xrd_data.Itth_weights, dtype=float)
    mask = window_mask(tth, w, lo, hi)
    return tth[mask], itth[mask], 1.0 / np.sqrt(w[mask]), mask


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


# --- peak widths (A171, A178; EB-87) ----------------------------------------


def width_margin(payload) -> float:
    """``WIDTH_MARGIN`` plus the stated zero, displacement and transparency corrections (A178 (a))."""
    corr = payload.instrument.corrections
    stated = [corr.zero.value] + [p.value for p in (corr.displacement, corr.transparency) if p is not None]
    return WIDTH_MARGIN + sum(abs(float(v)) for v in stated)


def width_check_points(payload) -> np.ndarray:
    """The 2θ grid the widths are checked on: ``[lo - margin, hi + margin]`` in ``WIDTH_GRID_STEP`` steps.

    ``(lo, hi)`` are the window's limits on the data (core
    :func:`~powderline.schema_core.fit_limits_on_data`); the grid stays inside
    (0°, 180°), where tan θ and 1/cos θ are finite.
    """
    lo, hi = fit_limits_on_data(payload.fit_range, payload.xrd_data)
    pad = width_margin(payload)
    start = max(lo - pad, WIDTH_GRID_STEP)
    stop = min(hi + pad, 180.0 - WIDTH_GRID_STEP)
    n = int(np.floor((stop - start) / WIDTH_GRID_STEP + 1e-9)) + 1
    return start + WIDTH_GRID_STEP * np.arange(max(n, 1))


def negative_width_ranges(widths: dict, tth) -> dict:
    """``{"gaussian": (lo, hi), "lorentzian": (lo, hi)}``: the 2θ ranges of ``tth`` where a width is negative (pure).

    ``widths`` holds ``u, v, w, x, y`` in easydiffraction's units (FWHM² deg²,
    FWHM deg). An empty dict means every width is >= 0 on ``tth``.
    """
    theta = np.radians(np.asarray(tth, dtype=float) / 2.0)
    t, c = np.tan(theta), np.cos(theta)
    gaussian = widths["u"] * t**2 + widths["v"] * t + widths["w"]
    lorentzian = widths["x"] * t + widths["y"] / c
    found = {}
    for name, values in (("gaussian", gaussian), ("lorentzian", lorentzian)):
        neg = values < 0
        if neg.any():
            where = np.asarray(tth, dtype=float)[neg]
            found[name] = (float(where.min()), float(where.max()))
    return found


def stated_widths(payload) -> dict:
    """``u, v, w, x, y`` as the recipe states them."""
    return {k: float(getattr(payload.instrument.broadening, k).value) for k in "uvwxy"}


def check_peak_widths(payload) -> None:
    """Raise :class:`NegativeWidthError` when a stated width is negative on :func:`width_check_points` (A171, A178).

    The message is reported at ``payload.instrument.broadening`` and names the
    2θ range, the formula and the margin, so the user sees why a value outside
    the window matters.
    """
    found = negative_width_ranges(stated_widths(payload), width_check_points(payload))
    if not found:
        return
    lo, hi = fit_limits_on_data(payload.fit_range, payload.xrd_data)
    parts = [f"{WIDTH_FORMULAS[name]} is negative for 2theta in [{a:.4g}, {b:.4g}] degrees"
             for name, (a, b) in found.items()]
    raise NegativeWidthError(
        "payload.instrument.broadening: " + "; ".join(parts)
        + f". easydiffraction computes a wrong pattern for such reflections without a message (EB-87) and "
        f"computes reflections beyond the fit window [{lo:.4g}, {hi:.4g}], so the widths must be >= 0 within "
        f"{width_margin(payload):.4g} degrees of it (1 degree plus the stated zero, displacement and transparency "
        "corrections, A178); change U, V, W, X, Y or the fit_range (A171)")


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


# --- refinement flags (A94, A98, F2, A138, A176) ---------------------------


@dataclass(frozen=True)
class FreeParam:
    """One easydiffraction parameter to free, with its bounds, and the recipe field it comes from."""

    path: str          # recipe field path, e.g. "payload.phases.LaB6.unit_cell.a"
    ed_attr_path: str  # from the Project, e.g. "structures.lab6.cell.length_a"
    value: float
    min: Optional[float]
    max: Optional[float]
    unit: str
    kind: str          # cell | coordinate | occupancy | adp | scale | background | instrument


def _representatives(ties: Ties, params: dict, context: str) -> list[str]:
    """The flagged representatives of ``ties``; asserts core's flag rules (F1, F2) on ``params``."""
    for name in ties.fixed:
        if params[name].refine_flag:
            raise AssertionError(f"{context}: {name} is fixed by symmetry but flagged for refinement; "
                                 "core validation refuses this (F2), so the model is not a validated one")
    chosen = []
    for group in ties.groups:
        flags = {params[m].refine_flag for m in group.members}
        if len(flags) > 1:
            raise AssertionError(f"{context}: the tie group {group.members} has mixed refine flags; core validation "
                                 "refuses this (F1), so the model is not a validated one")
        if flags.pop():
            chosen.append(group.members[0])
    return chosen


def _param(path: str, ed_path: str, p, unit: str, kind: str) -> FreeParam:
    return FreeParam(path, ed_path, p.value, p.min, p.max, unit, kind)


def free_parameter_plan(phase_name: str, phase: EasydiffractionPhase) -> list[FreeParam]:
    """Which of a phase's parameters are freed, with which bounds: one per flagged tie group (pure)."""
    slug = datablock_name(phase_name)
    base, ed_base = f"payload.phases.{phase_name}", f"structures.{slug}"
    plan: list[FreeParam] = []
    cell = phase.unit_cell
    cell_params = {k: getattr(cell, k) for k in CELL_ATTRS}
    for rep in _representatives(cell_tie_groups(phase.space_group), cell_params, f"{phase_name} unit cell"):
        plan.append(_param(f"{base}.unit_cell.{rep}", f"{ed_base}.cell.{CELL_ATTRS[rep]}", cell_params[rep],
                           UNIT_DEGREE if rep in ("alpha", "beta", "gamma") else UNIT_ANGSTROM, "cell"))
    for label, atom in phase.atoms.items():
        apath, ed_atom = f"{base}.atoms.{label}", f"{ed_base}.atom_sites.{label}"
        ties = coupling_groups(phase.space_group, (atom.x.value, atom.y.value, atom.z.value))
        xyz = {k: getattr(atom, k) for k in "xyz"}
        for rep in _representatives(ties.xyz, xyz, f"{phase_name} atom {label!r} coordinates"):
            plan.append(_param(f"{apath}.{rep}", f"{ed_atom}.fract_{rep}", xyz[rep], UNIT_FRACTIONAL, "coordinate"))
        if atom.occupancy.refine_flag:
            plan.append(_param(f"{apath}.occupancy", f"{ed_atom}.occupancy", atom.occupancy, UNIT_ARBITRARY,
                               "occupancy"))
        if atom.Uiso is not None and atom.Uiso.refine_flag:
            plan.append(_param(f"{apath}.Uiso", f"{ed_atom}.adp_iso", atom.Uiso, UNIT_ANGSTROM2, "adp"))
        if atom.Uaniso is not None:
            uij = {k: getattr(atom.Uaniso, k) for k in UIJ_ATTRS}
            for rep in _representatives(ties.uij, uij, f"{phase_name} atom {label!r} Uaniso"):
                plan.append(_param(f"{apath}.Uaniso.{rep}", f"{ed_base}.atom_site_aniso.{label}.{UIJ_ATTRS[rep]}",
                                   uij[rep], UNIT_ANGSTROM2, "adp"))
    return plan


#: Instrument fields -> (easydiffraction attribute path under the experiment, unit).
_INSTRUMENT_ATTRS = {
    ("radiation", "wavelength"): ("instrument.setup_wavelength", UNIT_ANGSTROM),
    ("corrections", "zero"): ("instrument.calib_twotheta_offset", UNIT_DEG_2THETA),
    ("corrections", "polarization"): ("instrument.setup_polarization_coefficient", UNIT_ARBITRARY),
    ("corrections", "polarization_monochromator_angle"): ("instrument.setup_monochromator_twotheta", UNIT_DEGREE),
    ("corrections", "displacement"): ("instrument.calib_sample_displacement", UNIT_ANGSTROM),
    ("corrections", "transparency"): ("instrument.calib_sample_transparency", UNIT_ARBITRARY),
    ("broadening", "u"): ("peak.broad_gauss_u", UNIT_DEG2_FWHM),
    ("broadening", "v"): ("peak.broad_gauss_v", UNIT_DEG2_FWHM),
    ("broadening", "w"): ("peak.broad_gauss_w", UNIT_DEG2_FWHM),
    ("broadening", "x"): ("peak.broad_lorentz_x", UNIT_DEG_FWHM),
    ("broadening", "y"): ("peak.broad_lorentz_y", UNIT_DEG_FWHM),
}


def experiment_plan(payload) -> list[FreeParam]:
    """The experiment's freed parameters: phase scales, background coefficients, instrument (pure)."""
    ed_base = f"experiments.{EXPERIMENT_NAME}"
    plan: list[FreeParam] = []
    for name, phase in payload.phases.items():
        if phase.scale.refine_flag:
            plan.append(_param(f"payload.phases.{name}.scale", f"{ed_base}.linked_structures.{datablock_name(name)}.scale",
                               phase.scale, UNIT_ARBITRARY, "scale"))
    if payload.background.refine_flag:
        for k, coef in enumerate(payload.background.coefficients):
            plan.append(FreeParam(f"payload.background.coefficients.{k}",
                                  f"{ed_base}.background.{k}.coef", coef, None, None, UNIT_ARBITRARY, "background"))
    for (block, name), (ed_path, unit) in _INSTRUMENT_ATTRS.items():
        p = getattr(getattr(payload.instrument, block), name)
        if p is not None and p.refine_flag:
            plan.append(_param(f"payload.instrument.{block}.{name}", f"{ed_base}.{ed_path}", p, unit, "instrument"))
    return plan


def _resolve(root: Any, path: str) -> Any:
    """Walk a dotted path: attributes, or ``[]`` lookup for a collection keyed by id (``atom_sites.B1``)."""
    obj = root
    for seg in path.split("."):
        if hasattr(obj, seg):
            obj = getattr(obj, seg)
        else:
            try:
                obj = obj[seg]
            except (KeyError, TypeError, IndexError) as exc:
                raise AttributeError(f"easydiffraction object has no {seg!r} on the path {path!r}") from exc
    return obj


def _free(param: Any, fp: FreeParam, shift: float = 0.0) -> dict:
    """Free ``param`` with the stated bounds (moved by ``shift``, A176); return its manifest entry."""
    _set(param, "free", True)
    if fp.min is not None:
        _set(param, "fit_min", fp.min + shift)
    if fp.max is not None:
        _set(param, "fit_max", fp.max + shift)
    return {"path": fp.path, "unit": fp.unit, "kind": fp.kind, "value": fp.value, "min": fp.min, "max": fp.max,
            "shift": shift, "parameter": param}


def apply_refinement_flags(phase_name: str, phase: EasydiffractionPhase, ed_phase) -> dict:
    """Execute :func:`free_parameter_plan` on the built structure; return ``{ed_attr_path: manifest entry}``."""
    prefix = f"structures.{datablock_name(phase_name)}."
    manifest = {}
    for fp in free_parameter_plan(phase_name, phase):
        param = _resolve(ed_phase, fp.ed_attr_path[len(prefix):])
        shift = 0.0
        if fp.kind == "coordinate":  # a position held translated by a lattice vector (A176): bounds move with it
            shift = float(round(float(param.value) - fp.value))
        manifest[fp.ed_attr_path] = _free(param, fp, shift)
    return manifest


def apply_experiment_flags(payload, expt) -> dict:
    """Execute :func:`experiment_plan` on the built experiment; return ``{ed_attr_path: manifest entry}``."""
    prefix = f"experiments.{EXPERIMENT_NAME}."
    return {fp.ed_attr_path: _free(_resolve(expt, fp.ed_attr_path[len(prefix):]), fp)
            for fp in experiment_plan(payload)}


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

    _set(expt.background, "type", "chebyshev")
    for k, coef in enumerate(payload.background.coefficients):
        expt.background.create(id=str(k), order=k, coef=coef)
    return expt


def build_project(recipe: EasydiffractionRecipe, workdir) -> BuildResult:
    """Build the easydiffraction ``Project`` of a validated native recipe.

    Writes the handed data loop to ``workdir / DATA_CIF_NAME``; values, the
    site check, then the refinement flags (the manifest has one entry per
    freed easydiffraction parameter, keyed by its path from the ``Project``).

    Raises ``ImportError`` without easydiffraction,
    :class:`EasyDiffractionTranslationError` for an unsupported setting,
    :class:`NegativeWidthError` for a stated profile with a negative width near
    the window and :class:`SiteError` for a position easydiffraction cannot use
    as stated.
    """
    check_peak_widths(recipe.payload)  # before anything is built: the recipe itself is wrong (A171, A178)
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
    manifest: dict = {}
    for name, phase in payload.phases.items():  # easydiffraction detects the Wyckoff sites at build time (EB-74)
        structure = project.structures[names[name]]
        warnings += check_sites(name, phase, calculator, structure)
        manifest.update(apply_refinement_flags(name, phase, structure))
    manifest.update(apply_experiment_flags(payload, expt))

    return BuildResult(project=project, manifest=manifest, warnings=warnings, window_mask=mask,
                       datablock_names=names)
