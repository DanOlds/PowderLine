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

- **Stopping criteria (A162):** ``max_iterations`` is handed to the minimizer
  (lmfit ``max_nfev``); the three tolerances are handed when the minimizer
  exposes ``ftol``/``xtol``/``gtol``, otherwise easydiffraction 0.21.1 passes
  fixed values (1e-8, 1e-8, 0; EB-81) and a recipe stating others gets the
  informative warning ``easydiffraction_stopping_criteria_not_handed`` (A190).
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
- **Native artifacts (A11):** the handed data loop (builder) and the
  easydiffraction project files are kept beside the reports.

easydiffraction is imported lazily (through the builder); everything that can
be pure (checks, warning construction, the outcome's shape) is pure and tested
in the core-only environment. Attribute names come from probes run on 0.21.1
and the library source (A184); every ``_set`` refuses an unknown attribute.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import numpy as np

from powderline.exceptions import EngineExecutionError, StructuredWarning
from powderline.gateways.easydiffraction.native_builder import (
    EXPERIMENT_NAME,
    BuildResult,
    WIDTH_FORMULAS,
    _set,
    build_project,
    negative_width_ranges,
    width_check_points,
)
from powderline.gateways.easydiffraction.schema import EasydiffractionRecipe

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
    """A190: the tolerances the minimizer could not take, when the recipe states other than the engine's fixed values."""
    differing = [f"{name} {getattr(controls, name)!r} (easydiffraction passes lmfit {kw}={fixed!r})"
                 for name, (kw, fixed) in TOLERANCES.items()
                 if not handed.get(name) and float(getattr(controls, name)) != fixed]
    if not differing:
        return None
    return StructuredWarning(
        code="easydiffraction_stopping_criteria_not_handed",
        message=("the installed easydiffraction exposes no setter for these stopping criteria, so the fit ran with "
                 "its fixed values (EB-81), not the recipe's: " + "; ".join(differing)
                 + "; max_iterations was handed"),
        field_path="payload.refinement_controls")


def configure_minimizer(analysis, controls) -> dict:
    """Select lmfit leastsq and hand the stopping criteria; return ``{tolerance field: handed?}``.

    ``max_iterations`` is always handed (``minimizer.max_iterations`` = lmfit
    ``max_nfev``, EB-81; mutation R-q of the review). A tolerance is handed only
    where the minimizer object exposes the lmfit keyword as an attribute.
    """
    _set(analysis.minimizer, "type", MINIMIZER)
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
                      engine_version=_installed_version())
