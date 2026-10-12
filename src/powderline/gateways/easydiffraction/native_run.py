"""Run a native ``easydiffraction.rietveld`` recipe and read the engine strictly (re/06, Phase 4).

Steps: :func:`~powderline.gateways.easydiffraction.native_builder.build_project`
(easydiffraction's errors raise, EB-48), then either lmfit ``leastsq`` through
``project.analysis.fit()`` or, when no parameter is freed, a single
``project.analysis.calculate()`` (a simulation, A129: never lmfit, never a
convergence warning, A166). What the engine holds afterwards is read into a
:class:`RunOutcome`, strictly (A130, A154): any non-finite number in the
calculated pattern, the background, a refined value or a reflection list is the
divergence error ``EngineExecutionError``, never a result; a non-finite ESD is
``None`` (lmfit's "Could not estimate error-bars", EB-90).

Rules implemented (decision log numbers in brackets):

- **Stopping criteria (A162, A190, A195):** ``max_iterations`` is handed to
  the minimizer (lmfit ``max_nfev``); each of the three required tolerances is
  handed where the minimizer exposes ``ftol``/``xtol``/``gtol``; otherwise
  easydiffraction 0.21.1 passes fixed values (1e-8, 1e-8, 0; EB-81) and a
  stated value that differs gets the informative warning
  ``easydiffraction_stopping_criteria_not_handed``.
- **Not converged (A166, A172):** lmfit stopping without success is a success
  with values and the warning ``easydiffraction_fit_not_converged``.
- **CrysFML refinements (A175, EB-90):** every CrysFML fit carries
  ``easydiffraction_crysfml_start_dependent``.
- **Refined widths (A171, A178):** a refined profile with a negative width on
  the window-plus-margin grid gets ``easydiffraction_negative_width``, values
  kept.
- **Reflections (A165, EB-79):** CrysFML reports none: the warning
  ``easydiffraction_reflections_not_available``, no side calculation.
- **A phase contributing nothing (A157 B7):** informative
  ``easydiffraction_phase_contributes_nothing``, never an error.
- **At a limit (A147 Q-L4, A157 B4, A181):** a refined value within
  ``max(3 ESD, 1e-6 |start|, 1e-15)`` of a stated bound, or of the physical
  limit easydiffraction applies on an unbounded side (EB-71), gets
  ``easydiffraction_parameter_at_limit`` from PowderLine's own check
  (easydiffraction's own boundary messages are only logged).
- **Native artifacts (A11):** the handed data loop (builder) and the
  easydiffraction project files are kept beside the reports.

easydiffraction is imported lazily (through the builder); everything that can
be pure (checks, warning construction, the outcome's shape) is pure and tested
in the core-only environment. Attribute names come from probes run on 0.21.1
and the library source (A184); every ``_set`` refuses an unknown attribute.
"""

from __future__ import annotations

import math
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from powderline import reports
from powderline.exceptions import EngineExecutionError, StructuredWarning
from powderline.fitstats import compute_fit_statistics
from powderline.gateways.easydiffraction.native_builder import (
    CELL_ATTRS,
    EXPERIMENT_NAME,
    BuildResult,
    WIDTH_FORMULAS,
    _set,
    build_project,
    negative_width_ranges,
    width_check_points,
)
from powderline.gateways.easydiffraction.schema import EasydiffractionRecipe
from powderline.schema_core import collect_warnings, parameters_requested
from powderline.symmetry import cell_tie_groups

#: The minimizer easydiffraction runs (A162: the only one in 1.0.0; P and the ESDs come from lmfit).
MINIMIZER = "lmfit (leastsq)"
#: Recipe tolerance -> (lmfit keyword, the fixed value easydiffraction 0.21.1 passes; EB-81).
TOLERANCES = {"chi_square_change_tolerance": ("ftol", 1e-8),
              "parameter_change_tolerance": ("xtol", 1e-8),
              "gradient_tolerance": ("gtol", 0.0)}
#: Columns of ``<phase>_peak_list_report.csv`` (what easydiffraction's reflection list holds).
PEAK_LIST_COLUMNS = ("h", "k", "l", "d_spacing", "2theta", "F_calc_squared")
#: ``fit_data_arrays()`` keys read (0.21.1 ``PdDataBase.fit_data_arrays``: x, meas, meas_su, calc, diff, bkg).
_ARRAY_KEYS = ("x", "meas", "meas_su", "calc", "bkg")
#: Directory (under the output directory) the easydiffraction project is saved to (A11).
PROJECT_DIR = "easydiffraction_project"


@dataclass(frozen=True)
class RunOutcome:
    """What the engine holds after the run, read strictly; the input of the result builder."""

    build: BuildResult
    simulation: bool
    #: Arrays on the handed points (``window_mask`` indexes them in the full pattern).
    x: np.ndarray
    y_obs: np.ndarray
    y_calc: np.ndarray
    y_bkg: np.ndarray
    sigma: np.ndarray
    #: easydiffraction parameter path -> (refined value in the recipe's writing, ESD or None).
    values: dict = field(default_factory=dict)
    #: phase name -> reflection rows (``PEAK_LIST_COLUMNS``); empty when the calculator reports none.
    reflections: dict = field(default_factory=dict)
    reflections_available: bool = True
    #: lmfit's varied-parameter count (``fit_result.n_free_parameters``); 0 for a simulation.
    n_varied: int = 0
    #: lmfit's success flag (``None`` for a simulation) and message.
    converged: Optional[bool] = None
    message: Optional[str] = None
    #: easydiffraction's own statistics: Rwp and Rexp in percent, lmfit's reduced chi^2 (``None`` for a simulation).
    engine_rwp: Optional[float] = None
    engine_rexp: Optional[float] = None
    engine_chi2_red: Optional[float] = None
    #: ``u, v, w, x, y`` after the run.
    widths: dict = field(default_factory=dict)
    #: phase name -> True when its calculated contribution has a non-zero point.
    contributes: dict = field(default_factory=dict)
    #: The final wavelength (refined if freed).
    wavelength: float = 1.0
    #: phase name -> {member: (value, esd or None)} for a..gamma as the engine holds them after the run.
    cells: dict = field(default_factory=dict)
    #: Warnings raised while running (after the builder's).
    warnings: list = field(default_factory=list)
    #: Installed easydiffraction version.
    engine_version: Optional[str] = None


def _diverged(what: str) -> EngineExecutionError:
    return EngineExecutionError(
        f"the refinement diverged: {what} is not finite (A130); no result is reported. The easydiffraction project "
        "in the output directory shows the diverged parameters; refine fewer or less correlated parameters, or "
        "start closer to the solution")


# --- pure checks and readers -------------------------------------------------


def check_arrays(arrays: dict, tth_handed) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """``(x, y_obs, y_calc, y_bkg, sigma)`` from ``fit_data_arrays()``, read strictly.

    The engine must hold exactly the handed points, in order (A158); a
    non-finite calculated or background value is the divergence error (A130,
    A154).
    """
    missing = [k for k in _ARRAY_KEYS if arrays.get(k) is None]
    if missing:
        raise EngineExecutionError(f"easydiffraction returned no {', '.join(missing)} array(s) after the run")
    x, y_obs, sigma, y_calc, y_bkg = (np.asarray(arrays[k], dtype=float) for k in _ARRAY_KEYS)
    handed = np.asarray(tth_handed, dtype=float)
    if x.shape != handed.shape or not np.allclose(x, handed, rtol=1e-9, atol=1e-9):
        raise EngineExecutionError(
            f"easydiffraction holds {x.size} data point(s) but {handed.size} were handed (A158): the pattern was not "
            "read as written")
    if not np.isfinite(y_calc).all():
        raise _diverged("the calculated pattern")
    if not np.isfinite(y_bkg).all():
        raise _diverged("the background")
    return x, y_obs, y_calc, y_bkg, sigma


def _esd(value) -> Optional[float]:
    """An ESD as a float, or ``None`` when the engine has none or it is not finite (not a divergence, A154)."""
    if value is None:
        return None
    v = float(getattr(value, "value", value))
    return v if math.isfinite(v) else None


def read_values(manifest: dict) -> dict:
    """``{path: (value - shift, esd)}`` from the manifest's live parameters (A176, A185); a non-finite value diverged."""
    values = {}
    for key, entry in manifest.items():
        param = entry["parameter"]
        v = float(getattr(param.value, "value", param.value))
        if not math.isfinite(v):
            raise _diverged(f"the refined value of {entry['path']}")
        values[key] = (v - float(entry.get("shift", 0.0)), _esd(getattr(param, "uncertainty", None)))
    return values


def reflection_rows(refln, slug: str, phase: str) -> list[dict]:
    """One phase's reflection rows from easydiffraction's ``refln`` (arrays per column), read strictly (N5, A154)."""
    ids = np.asarray(refln.structure_id).astype(str)
    mask = ids == slug
    columns = {"h": refln.index_h, "k": refln.index_k, "l": refln.index_l, "d_spacing": refln.d_spacing,
               "2theta": refln.two_theta, "F_calc_squared": refln.f_squared_calc}
    data = {}
    for name, col in columns.items():
        arr = np.asarray(col, dtype=float)[mask]
        if not np.isfinite(arr).all():
            raise _diverged(f"a value in the reflection list of phase {phase!r} ({name})")
        data[name] = arr
    rows = []
    for i in range(int(mask.sum())):
        rows.append({"h": int(round(data["h"][i])), "k": int(round(data["k"][i])), "l": int(round(data["l"][i])),
                     "d_spacing": float(data["d_spacing"][i]), "2theta": float(data["2theta"][i]),
                     "F_calc_squared": float(data["F_calc_squared"][i])})
    return rows


def stopping_criteria_warning(controls, handed: dict) -> Optional[StructuredWarning]:
    """A190: the tolerances the minimizer could not take, when they differ from the engine's fixed values.

    ``handed`` is :func:`configure_minimizer`'s ``{field: handed?}``; a value
    equal to the engine's fixed one is never warned about.
    """
    differing = [f"{name} {getattr(controls, name)!r} (easydiffraction passes lmfit {kw}={fixed!r})"
                 for name, (kw, fixed) in TOLERANCES.items()
                 if name in handed and not handed[name] and float(getattr(controls, name)) != fixed]
    if not differing:
        return None
    return StructuredWarning(
        code="easydiffraction_stopping_criteria_not_handed",
        message=("the installed easydiffraction exposes no setter for these stopping criteria, so the fit ran with "
                 "its fixed values (EB-81), not the recipe's: " + "; ".join(differing)
                 + "; max_iterations was handed"),
        field_path="payload.refinement_controls")


def configure_minimizer(analysis, controls) -> dict:
    """Select the recipe's minimizer and hand the stopping criteria; return ``{tolerance field: handed?}``.

    ``max_iterations`` is always handed (``minimizer.max_iterations`` = lmfit
    ``max_nfev``, EB-81; mutation R-q of the review). Each tolerance (required
    by the schema, A162/A195) is handed only where the minimizer object exposes
    the lmfit keyword as an attribute (A190).
    """
    _set(analysis.minimizer, "type", controls.minimizer)
    minimizer = analysis.minimizer
    _set(minimizer, "max_iterations", int(controls.max_iterations))
    handed = {}
    for name, (kw, _fixed) in TOLERANCES.items():
        if hasattr(minimizer, kw):
            _set(minimizer, kw, float(getattr(controls, name)))
            handed[name] = True
        else:
            handed[name] = False
    return handed


def run_warnings(*, simulation: bool, calculator: str, n_varied: int, converged: Optional[bool],
                 message: Optional[str], widths: dict, grid, reflections_available: bool,
                 contributes: dict) -> list:
    """The run's structured warnings (pure): A166/A172, A175, A171, A165, A157 B7."""
    warnings: list = []
    if not simulation and converged is False:
        warnings.append(StructuredWarning(
            code="easydiffraction_fit_not_converged",
            message=(f"lmfit stopped without converging ({message or 'no message'}): poorly conditioned parameters "
                     "or too few iterations (payload.refinement_controls.max_iterations); the values reached are "
                     "reported (A166)"),
            field_path="payload.refinement_controls"))
    if not simulation and calculator == "crysfml" and n_varied > 0:
        warnings.append(StructuredWarning(
            code="easydiffraction_crysfml_start_dependent",
            message=("CrysFML computes the pattern in single precision, below lmfit's finite-difference step, so the "
                     "refined values, Rwp and ESDs of a CrysFML refinement can depend on the starting values "
                     "(EB-90, A175); CrysPy does not have this limitation"),
            field_path="payload.refinement_controls.calculator"))
    if widths:
        for name, (lo, hi) in negative_width_ranges(widths, grid).items():
            warnings.append(StructuredWarning(
                code="easydiffraction_negative_width",
                message=(f"after the run, {WIDTH_FORMULAS[name]} is negative for 2theta in [{lo:.4g}, {hi:.4g}] "
                         "degrees: easydiffraction computes a wrong pattern for such reflections without a message "
                         "(EB-87); the values are reported as refined (A171)"),
                field_path="payload.instrument.broadening"))
    if not reflections_available:
        warnings.append(StructuredWarning(
            code="easydiffraction_reflections_not_available",
            message=(f"the {calculator!r} calculator reports no reflection list, so no peak list report is written "
                     "(EB-79; A165: no side calculation)"),
            field_path="payload.refinement_controls.calculator"))
    for phase, any_signal in contributes.items():
        if not any_signal:
            warnings.append(StructuredWarning(
                code="easydiffraction_phase_contributes_nothing",
                message=(f"phase {phase!r} contributes nothing to the calculated pattern (all its calculated "
                         "intensities are zero): its scale is 0, or none of its reflections reaches the window "
                         "(A157 B7)"),
                field_path=f"payload.phases.{phase}"))
    return warnings


# --- runtime layer (easydiffraction objects) --------------------------------


def _installed_version() -> Optional[str]:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("easydiffraction")
    except PackageNotFoundError:
        return None


def _param_value(p) -> float:
    return float(getattr(p.value, "value", p.value))


def refined_widths(expt) -> dict:
    """``u, v, w, x, y`` as the experiment holds them after the run."""
    return {k: _param_value(getattr(expt.peak, f"broad_gauss_{k}")) for k in "uvw"} | \
           {k: _param_value(getattr(expt.peak, f"broad_lorentz_{k}")) for k in "xy"}


def read_cell(structure) -> dict:
    """``{member: (value, esd or None)}`` for a..gamma from an easydiffraction structure after the run."""
    cell = structure.cell
    return {member: (_param_value(getattr(cell, attr)), _esd(getattr(getattr(cell, attr), "uncertainty", None)))
            for member, attr in CELL_ATTRS.items()}


def phase_contributions(project, expt, names: dict, y_calc: np.ndarray, y_bkg: np.ndarray) -> dict:
    """phase name -> whether its calculated contribution has a non-zero point (A157 B7, A191).

    One phase: its contribution is ``calc - bkg``. Several: each phase is
    calculated alone (the other scales set to 0, one ``calculate()`` per
    phase), then every scale is restored and the pattern recalculated, so the
    project is left as the fit left it. ``expt.refln`` is not changed by this
    (review probe ``refln_two_phase.py``).
    """
    if len(names) == 1:
        return {next(iter(names)): bool(np.any(y_calc - y_bkg != 0))}
    scales = {name: expt.linked_structures[slug].scale for name, slug in names.items()}
    original = {name: _param_value(p) for name, p in scales.items()}
    found = {}
    try:
        for name in names:
            for other, p in scales.items():
                _set(p, "value", original[other] if other == name else 0.0)
            project.analysis.calculate()
            arrays = expt.data.fit_data_arrays()
            found[name] = bool(np.any(np.asarray(arrays["calc"], dtype=float) - np.asarray(arrays["bkg"], dtype=float)
                                      != 0))
    finally:
        for name, p in scales.items():
            _set(p, "value", original[name])
        project.analysis.calculate()
    return found


def save_project(project, output_dir) -> Optional[StructuredWarning]:
    """Keep easydiffraction's own project files beside the reports (A11); a failure to save is a warning, not an error."""
    target = Path(output_dir) / PROJECT_DIR
    try:
        project.save_as(str(target), overwrite=True)
    except Exception as exc:  # noqa: BLE001 - the artifact is diagnostic; the result stands without it
        return StructuredWarning(
            code="easydiffraction_project_not_saved",
            message=f"easydiffraction could not save its project files to {target}: {exc}",
            field_path=None)
    return None


def execute(recipe: EasydiffractionRecipe, output_dir, *, verbose: bool = False) -> RunOutcome:
    """Build, run (fit or calculate) and read the engine strictly; the project files are saved under ``output_dir``.

    Raises ``ImportError`` without easydiffraction, the builder's errors for a
    recipe easydiffraction cannot use, and ``EngineExecutionError`` when the
    refinement diverged (A130).
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    payload = recipe.payload
    controls = payload.refinement_controls
    build = build_project(recipe, out)  # configures Logger RAISE before any object is built (EB-48)
    project = build.project
    expt = project.experiments[EXPERIMENT_NAME]
    simulation = not build.manifest  # A129: nothing freed
    warnings: list = []

    if simulation:
        project.analysis.calculate()
        n_varied, converged, message = 0, None, None
        engine_rwp = engine_rexp = engine_chi2 = None
    else:
        handed = configure_minimizer(project.analysis, controls)
        project.analysis.fit()
        results = project.analysis.fit_results  # lmfit's outcome: success, message, reduced_chi_square
        fr = project.analysis.fit_result       # easydiffraction's statistics: n_free_parameters, prof_wr_*
        n_varied = int(fr.n_free_parameters.value)
        converged = bool(results.success)
        message = " ".join(str(results.message or "").split()) or None
        percent = lambda d: None if d is None or d.value is None else 100.0 * float(d.value)  # noqa: E731
        engine_rwp, engine_rexp = percent(fr.prof_wr_factor), percent(fr.prof_wr_expected)
        chi2 = results.reduced_chi_square
        engine_chi2 = None if chi2 is None else float(chi2)
        w = stopping_criteria_warning(controls, handed)
        if w is not None:
            warnings.append(w)
    if verbose:
        print(f"[easydiffraction] {'calculated' if simulation else 'fitted'}: {n_varied} parameter(s) varied"
              + (f", lmfit: {message}" if message else ""))

    tth_all = np.asarray(payload.xrd_data.tth, dtype=float)
    x, y_obs, y_calc, y_bkg, sigma = check_arrays(expt.data.fit_data_arrays(), tth_all[build.window_mask])
    values = read_values(build.manifest)
    refln = getattr(expt, "refln", None)
    reflections = {}
    if refln is not None:
        reflections = {name: reflection_rows(refln, slug, name) for name, slug in build.datablock_names.items()}
    widths = refined_widths(expt)
    cells = {name: read_cell(project.structures[slug]) for name, slug in build.datablock_names.items()}
    contributes = phase_contributions(project, expt, build.datablock_names, y_calc, y_bkg)
    warnings += run_warnings(simulation=simulation, calculator=controls.calculator, n_varied=n_varied,
                             converged=converged, message=message, widths=widths,
                             grid=width_check_points(payload), reflections_available=refln is not None,
                             contributes=contributes)
    saved = save_project(project, out)
    if saved is not None:
        warnings.append(saved)
    return RunOutcome(build=build, simulation=simulation, x=x, y_obs=y_obs, y_calc=y_calc, y_bkg=y_bkg,
                      sigma=sigma, values=values, reflections=reflections, reflections_available=refln is not None,
                      n_varied=n_varied, converged=converged, message=message, engine_rwp=engine_rwp,
                      engine_rexp=engine_rexp, engine_chi2_red=engine_chi2, widths=widths, contributes=contributes,
                      wavelength=_param_value(expt.instrument.setup_wavelength), warnings=warnings,
                      cells=cells, engine_version=_installed_version())


# --- result assembly (A23, A41, A125, A128, A129; pure given a RunOutcome) ---

#: Manifest ``kind`` -> ``refined_parameters.csv`` category (GSAS-II's vocabulary where it has one).
CATEGORIES = {"cell": "unit_cell", "coordinate": "atom_position", "occupancy": "atom_occupancy",
              "scale": "scale", "background": "background", "instrument": "instrument"}
#: Unit-cell report rows (the GSAS-II report's names).
CELL_ROW_NAMES = {"a": "cell_a", "b": "cell_b", "c": "cell_c", "alpha": "cell_alpha", "beta": "cell_beta",
                  "gamma": "cell_gamma"}


def _location(model, path: str) -> tuple[str, Optional[int], str, Optional[int]]:
    """``(phase_name, phase_idx, atom_name, atom_idx)`` of a recipe field path; empty where it is not a phase's."""
    parts = path.split(".")
    if len(parts) < 3 or parts[1] != "phases":
        return "", None, "", None
    phases = list(model.payload.phases)
    phase = parts[2]
    atom, atom_idx = "", None
    if len(parts) > 4 and parts[3] == "atoms":
        atom = parts[4]
        atoms = list(model.payload.phases[phase].atoms)
        atom_idx = atoms.index(atom) if atom in atoms else None
    return phase, phases.index(phase) if phase in phases else None, atom, atom_idx


def descriptive_name(entry: dict) -> str:
    """A readable name from the recipe path: ``cell_a``, ``x``, ``Uiso``, ``U11``, ``scale``, ``background_coefficient_2``, ``setup_wavelength``, ``broad_gauss_w``."""
    last = entry["path"].split(".")[-1]
    if entry["kind"] == "cell":
        return f"cell_{last}"
    if entry["kind"] == "background":
        return f"background_coefficient_{last}"
    return last


def category(entry: dict) -> str:
    if entry["kind"] == "adp":
        return "atom_displacement_anisotropic" if ".Uaniso." in entry["path"] else "atom_displacement_isotropic"
    if entry["kind"] == "instrument" and ".broadening.parameters." in entry["path"]:
        return "instrument_broadening"
    return CATEGORIES[entry["kind"]]


def refined_rows(model, manifest: dict, values: dict) -> list[dict]:
    """``refined_parameters.csv`` rows: one per freed easydiffraction parameter, named by its easydiffraction path."""
    rows = []
    for key, entry in manifest.items():
        value, esd = values[key]
        phase, phase_idx, atom, atom_idx = _location(model, entry["path"])
        rows.append({"parameter_name": key, "descriptive_name": descriptive_name(entry), "phase_name": phase,
                     "phase_idx": phase_idx, "atom_name": atom, "atom_idx": atom_idx, "value": value, "esd": esd,
                     "category": category(entry)})
    return rows


def cell_volume(a, b, c, alpha, beta, gamma) -> float:
    ca, cb, cg = (math.cos(math.radians(v)) for v in (alpha, beta, gamma))
    arg = 1.0 - ca * ca - cb * cb - cg * cg + 2.0 * ca * cb * cg
    return a * b * c * math.sqrt(max(arg, 0.0))


def cell_rows(space_group: str, cell: dict, manifest: dict, slug: str) -> list[dict]:
    """``<phase>_unit_cell_report.csv`` rows (pure): a..gamma as the engine holds them, then the volume.

    ``cell`` is ``{member: (value, esd or None)}``. A member's ESD is the
    engine's; a tied member without one takes its group's first member's; a
    member that was not refined has 0.0; a refined one the engine gave no ESD
    stays empty. The volume ESD is first-order propagation, one perturbation
    per independent parameter (a tie group moves together) summed in
    quadrature; GSAS-II uses the covariance between groups, so it can differ.
    """
    ties = cell_tie_groups(space_group)
    rep = {m: g.members[0] for g in ties.groups for m in g.members}
    refined = {m for m in CELL_ATTRS if f"structures.{slug}.cell.{CELL_ATTRS[rep.get(m, m)]}" in manifest}
    rows, values, esds = [], {}, {}
    for member in CELL_ATTRS:
        value, esd = cell[member]
        if esd is None and member in refined:
            esd = cell[rep.get(member, member)][1]
        if esd is None and member not in refined:
            esd = 0.0
        rows.append({"parameter": CELL_ROW_NAMES[member], "value": value, "esd": esd})
        values[member], esds[member] = value, esd or 0.0
    vol = cell_volume(*values.values())
    independent = [tuple(g.members) for g in ties.groups] + [(m,) for m in CELL_ATTRS if m not in rep]
    var = 0.0
    for group in independent:
        if esds[group[0]]:
            moved = {m: v + (esds[m] if m in group else 0.0) for m, v in values.items()}
            var += (cell_volume(*moved.values()) - vol) ** 2
    rows.append({"parameter": "cell_volume", "value": vol, "esd": math.sqrt(var)})
    return rows


def profile_rows(x, y_obs, weights, y_calc, y_bkg, wavelength: float) -> list[dict]:
    """``fit_profile.txt`` rows on the handed points (what the engine calculated, A158); q and d from the final wavelength."""
    rows = []
    for xi, yo, w, yc, yb in zip(x, y_obs, weights, y_calc, y_bkg):
        s = math.sin(math.radians(float(xi) / 2.0))
        rows.append({"two_theta": float(xi), "y_obs": float(yo), "y_weights": float(w), "y_calc": float(yc),
                     "y_diff": float(yo) - float(yc), "y_bkg": float(yb), "q_values": 4.0 * math.pi * s / wavelength,
                     "d_spacings": wavelength / (2.0 * s) if s else math.inf})
    return rows


def _undefined(v) -> Optional[float]:
    """A plain float, or ``None`` where a statistic is undefined (A129: never NaN)."""
    return None if v is None or not math.isfinite(v) else float(v)


def build_result(model, outcome: RunOutcome, output_dir, *, validated_warnings=None, elapsed: float = 0.0) -> dict:
    """The standard result dict and report files from a :class:`RunOutcome` (pure: tested without the engine).

    Statistics are core :mod:`powderline.fitstats` over exactly the handed
    points (``window_mask``, A131) with the recipe's weights and P = lmfit's
    varied count (A41); the engine's own numbers go under ``engine_details``.
    ``y_bkg`` is the engine's ``intensity_bkg`` (A152's principle, A157 B5).
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    p = model.payload
    build = outcome.build
    mask = np.asarray(build.window_mask, dtype=bool)
    tth = np.asarray(p.xrd_data.tth, dtype=float)
    yobs = np.asarray(p.xrd_data.Itth, dtype=float)
    w = np.asarray(p.xrd_data.Itth_weights, dtype=float)
    ycalc_full = np.zeros_like(tth)
    ycalc_full[mask] = outcome.y_calc
    fs = compute_fit_statistics(yobs, ycalc_full, w, outcome.n_varied, mask=mask)
    requested = parameters_requested(model)

    warnings = list(validated_warnings if validated_warnings is not None else collect_warnings(model))
    warnings += list(build.warnings) + list(outcome.warnings)
    if not outcome.simulation and outcome.n_varied < requested:
        warnings.append(StructuredWarning(
            code="easydiffraction_parameters_not_varied",
            message=(f"lmfit varied {outcome.n_varied} parameter(s), but the recipe refines {requested} (one per "
                     "symmetry tie group); compare refined_parameters with the recipe's refine flags (A128)"),
            field_path=None))
    warnings += limit_hits(build.manifest, outcome.values)

    rows = refined_rows(model, build.manifest, outcome.values)
    unit_cells = {name: cell_rows(phase.space_group, outcome.cells[name], build.manifest, build.datablock_names[name])
                  for name, phase in p.phases.items() if name in outcome.cells}
    peak_lists = dict(outcome.reflections) if outcome.reflections_available else {}
    profile = profile_rows(outcome.x, outcome.y_obs, w[mask], outcome.y_calc, outcome.y_bkg, outcome.wavelength)

    reports.write_refined_parameters(out, rows)
    reports.write_fit_profile(out, profile)
    for name, cell in unit_cells.items():
        reports.write_unit_cell_report(out, name, cell)
    for name, refl in peak_lists.items():
        reports.write_peak_list_report(out, name, refl, PEAK_LIST_COLUMNS)

    controls = p.refinement_controls
    return {
        "success": True,
        "run_id": str(uuid.uuid4()),
        "rwp": _undefined(fs.rwp),
        "r_exp": _undefined(fs.rexp),
        "gof": _undefined(fs.gof),
        "chi2_red": _undefined(fs.chi2_red),
        "simulation_mode": outcome.simulation,
        "engine_details": {
            "engine": "easydiffraction",
            "engine_version": outcome.engine_version,
            "calculator": controls.calculator,
            "peak_type": p.instrument.broadening.peak_type,
            "minimizer": controls.minimizer,
            "rwp": _undefined(outcome.engine_rwp),
            "r_exp": _undefined(outcome.engine_rexp),
            "chi2_red": _undefined(outcome.engine_chi2_red),
            "converged": outcome.converged,
            "message": outcome.message,
            "n_points": fs.n_points,
            "parameters_requested": requested,
            "parameters_varied": outcome.n_varied,
            "parameters_varied_source": "fit_result.n_free_parameters (the parameters handed to lmfit, EB-81)",
        },
        "warnings": warnings,
        "elapsed_time": elapsed,
        "method": "easydiffraction_simulation" if outcome.simulation else "easydiffraction",
        "output_files": sorted(str(f) for f in out.rglob("*") if f.is_file()),
        "fit_profile": pd.DataFrame(profile, columns=list(reports.FIT_PROFILE_COLUMNS)),
        "unit_cell_data": {k: pd.DataFrame(v, columns=list(reports.UNIT_CELL_COLUMNS)) for k, v in unit_cells.items()},
        "peak_list_data": {k: pd.DataFrame(v, columns=list(PEAK_LIST_COLUMNS)) for k, v in peak_lists.items()},
        "refined_parameters": pd.DataFrame(rows, columns=list(reports.REFINED_PARAMETERS_COLUMNS)),
        "spf_peaks": pd.DataFrame(),  # easydiffraction has no single-peak workflow (A13.3)
        "spf_convergence_diagnostics": pd.DataFrame(),
        "error": None,
        "traceback": None,
    }


# --- at-limit warnings (A147 Q-L4, A157 B4, A181; EB-71) ---------------------

#: Limits easydiffraction applies to an unbounded side when a fit starts (EB-71), as the schema documents them:
#: cell lengths [0, 30] A, angles [0, 180] deg, occupancy [0, 1], Uiso and U11/U22/U33 [0, 10] A^2,
#: scale and wavelength >= 0; everything else (coordinates, off-diagonal Uij, background, other instrument) unbounded.
PHYSICAL_LIMITS = {"cell_length": (0.0, 30.0), "cell_angle": (0.0, 180.0), "occupancy": (0.0, 1.0),
                   "adp_diagonal": (0.0, 10.0), "scale": (0.0, None), "wavelength": (0.0, None)}
#: Below this, a limit is reached "exactly" (A181: only matters for a start at exactly 0 with no ESD).
LIMIT_TOL_FLOOR = 1e-15


def physical_limits(entry: dict) -> tuple:
    """``(lo, hi)`` easydiffraction imposes on ``entry``'s parameter where the recipe states no bound; ``None`` = open."""
    last = entry["path"].split(".")[-1]
    kind = entry["kind"]
    if kind == "cell":
        return PHYSICAL_LIMITS["cell_angle" if last in ("alpha", "beta", "gamma") else "cell_length"]
    if kind == "occupancy":
        return PHYSICAL_LIMITS["occupancy"]
    if kind == "adp" and last in ("Uiso", "U11", "U22", "U33"):
        return PHYSICAL_LIMITS["adp_diagonal"]
    if kind == "scale":
        return PHYSICAL_LIMITS["scale"]
    if kind == "instrument" and last == "setup_wavelength":
        return PHYSICAL_LIMITS["wavelength"]
    return (None, None)


def limit_tolerance(start: float, esd: Optional[float]) -> float:
    """A181: ``max(3 * esd, 1e-6 * |start|, 1e-15)``; ``esd`` counts as 0 when the engine gave none."""
    return max(3.0 * (esd or 0.0), 1e-6 * abs(float(start)), LIMIT_TOL_FLOOR)


def _limit_hits(entries) -> list:
    """``easydiffraction_parameter_at_limit`` for every entry whose final value is within tolerance of a limit (pure).

    Each entry: ``path``, ``kind``, ``value`` (the start), ``min``/``max`` (the
    stated bounds, ``None`` = the engine's physical limit on that side),
    ``final``, ``esd`` (``None`` when undetermined).
    """
    found = []
    for entry in entries:
        final, esd = entry["final"], entry.get("esd")
        if final is None or not math.isfinite(final):
            continue
        tol = limit_tolerance(entry["value"], esd)
        physical = physical_limits(entry)
        for side, stated, phys in (("min", entry.get("min"), physical[0]), ("max", entry.get("max"), physical[1])):
            lim = stated if stated is not None else phys
            if lim is None or abs(final - lim) > tol:
                continue
            whose = "the recipe's stated bound" if stated is not None else "easydiffraction's physical limit (EB-71)"
            esd_text = f"ESD {esd:.3g}" if esd is not None else "no ESD"
            found.append(StructuredWarning(
                code="easydiffraction_parameter_at_limit",
                message=(f"{descriptive_name(entry)} ended at its {side} limit {lim:g} ({whose}): value {final:.6g}, "
                         f"{esd_text}, within the tolerance {tol:.3g} (3 ESD, or 1e-6 of the start, A181); the "
                         "refinement may have been stopped by the limit"),
                field_path=entry["path"]))
    return found


def limit_hits(manifest: dict, values: dict) -> list:
    """:func:`_limit_hits` over the manifest's freed parameters with their refined values (in the recipe's writing)."""
    entries = []
    for key, entry in manifest.items():
        final, esd = values.get(key, (None, None))
        entries.append({**{k: entry[k] for k in ("path", "kind", "value", "min", "max")}, "final": final, "esd": esd})
    return _limit_hits(entries)


def validate_only_result(model: EasydiffractionRecipe, validated_warnings: list) -> dict:
    """The ``validate_only`` summary: the gsasii native shape, with this engine's controls (A194).

    ``simulation_mode`` is A129's rule: nothing flagged for refinement means a
    single calculation, never lmfit.
    """
    p = model.payload
    return {
        "success": True, "rwp": None, "elapsed_time": 0.0, "method": "validate_only",
        "schema_name": model.schema_name, "core_schema_version": model.core_schema_version,
        "engine_schema_version": model.engine_schema_version,
        "phases": len(p.phases), "max_iterations": p.refinement_controls.max_iterations,
        "simulation_mode": parameters_requested(model) == 0,
        "warnings": validated_warnings,
    }


def run_native_validate(model: EasydiffractionRecipe) -> dict:
    """``validate_only`` for a native recipe (no easydiffraction import)."""
    return validate_only_result(model, collect_warnings(model))


def run_refinement(recipe: EasydiffractionRecipe, output_dir, *, verbose: bool = False) -> dict:
    """Run a validated native recipe end to end and return the standard result dict.

    ``recipe`` is the model validated from the user's recipe (A115): its
    validation warnings are carried into the result once, here.
    """
    started = time.time()
    validated_warnings = collect_warnings(recipe)
    outcome = execute(recipe, output_dir, verbose=verbose)
    return build_result(recipe, outcome, output_dir, validated_warnings=validated_warnings,
                        elapsed=time.time() - started)
