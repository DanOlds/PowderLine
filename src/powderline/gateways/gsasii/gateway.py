"""Engine-free factory for the GSAS-II gateway (entry point ``powderline.gateways:gsasii``).

- **Schema layer** — ``validate`` / ``capabilities``: engine-free; runs with
  GSAS-II not installed. ``gsasii.*`` recipes validate against the native
  schemas (``gateways.gsasii.schema``, A39); 0.26.0 ``GSASII_*`` recipes keep
  the legacy validation (``gateways.gsasii.validation``) until re/07 removes them.
- **Runtime layer** — ``run``: the GSAS-II refinement entry point (moved here
  from ``powderline.kicker.run`` in re/07, P-06, behaviour unchanged). It
  validates, enforces ``ENGINE_VERSION_SPEC`` against the installed GSAS-II,
  then executes in this process (``execution_mode='subprocess'``) or through
  the persistent server (``'server'`` / ``'auto'``, via ``GSASClient``), and
  returns the standardized result dict with its tables as DataFrames.

GSAS-II is imported lazily, by ``routing.run_refinement`` (the executors) and
``installed_engine_version``. Never import the gateway's runtime modules
(helpers, project, setters, executors, extractors) at module top: they import
GSAS-II. Enforced by an import-block test.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import pandas as pd

from powderline.exceptions import EngineNotAvailableError
from powderline.gateways.gsasii import schema
from powderline.gateways.gsasii.routing import run_refinement
from powderline.gateways.gsasii.validation import validate as _validate_recipe
from powderline.registry import Gateway, check_engine_version
from powderline.schema_core import collect_warnings

NAME = "gsasii"
#: Supported GSAS-II runtime versions (A41/A44); pixi pins align in re/08.
ENGINE_VERSION_SPEC = "==5.7.9"
#: Workflows served: the native schemas, and the 0.26.0 names until re/07 removes them.
WORKFLOWS = (*schema.SCHEMA_NAMES, "GSASII_Rietveld", "GSASII_SPF")
#: pixi environment providing GSAS-II + how to install it (names final in re/08).
ENV = "default"
INSTALL_COMMAND = "pixi install"

EXECUTION_MODES = ('auto', 'server', 'subprocess')

#: The 9 ``refined_parameters`` columns, for the empty frame when nothing was refined.
REFINED_PARAMETER_COLUMNS = [
    'parameter_name', 'descriptive_name', 'phase_name', 'phase_idx',
    'atom_name', 'atom_idx', 'value', 'esd', 'category',
]


def capabilities() -> dict:
    return {"name": NAME, "workflows": WORKFLOWS, "engine_version_spec": ENGINE_VERSION_SPEC,
            **schema.DECLARATIONS}


def validate(recipe, *, verbose: bool = False):
    """Validate a recipe (dict or model); return the validated model (A50, A59).

    A ``gsasii.*`` recipe gives its native model (``GsasiiRietveldRecipe`` /
    ``GsasiiSpfRecipe``); a 0.26.0 one the legacy ``RecipeModel``.
    """
    return _validate_recipe(recipe, verbose=verbose)


def installed_engine_version() -> str:
    """The GSAS-II **runtime** version (``GSASII.__version__``), never distribution metadata.

    The pixi-built conda distribution reports ``gsas-ii 2.0.0`` (the fork recipe's
    version); the runtime reports ``5.7.9``. Fallback: ``GSASIIpath.GetVersionTag()``
    (``v5.7.9``) with the ``v`` stripped.
    """
    try:
        import GSASII
    except ImportError as exc:
        raise EngineNotAvailableError(gateway=NAME, dependency="GSAS-II", env=ENV,
                                      install_command=INSTALL_COMMAND) from exc
    version = getattr(GSASII, "__version__", None)
    if not version:
        from GSASII import GSASIIpath
        version = GSASIIpath.GetVersionTag().lstrip("v")
    return str(version)


def run(
    recipe,
    output_dir,
    *,
    verbose: bool = False,
    validate_only: bool = False,
    execution_mode: Literal['auto', 'server', 'subprocess'] = 'auto',
    allow_unsupported_engine_version: bool = False,
) -> dict:
    """Execute a GSAS-II refinement from an in-memory recipe.

    The full pipeline: schema validation, the engine version check (A44),
    output directory creation, and refinement execution with automatic
    backend selection. Reached through ``powderline.run()`` for a ``gsasii.*``
    (or 0.26.0 ``GSASII_*``) recipe, which forwards ``execution_mode``.

    Note: the CLI's template-file guard is **not** applied here. When calling
    programmatically the caller is responsible for ensuring the recipe
    contains real data in ``payload.xrd_data``.

    Args:
        recipe: a recipe dict or a validated model. XRD data must already be
            present in ``payload.xrd_data``.
        output_dir: directory where output files will be written
            (``dummy.gpx``, ``dummy.lst``, CSV reports, ``fit_profile.txt``).
            Created if it does not exist.
        verbose: if True, print detailed progress to stdout.
        validate_only: if True, validate the recipe and return a summary
            dict without running a refinement. No output files are written
            and the engine version is not checked.
        execution_mode: the backend used to execute the refinement:

            ``'auto'`` *(default)*
                Try the persistent GSAS-II server first (auto-starting it if
                needed); fall back to in-process execution if unavailable.
            ``'server'``
                Use the server only. Fails with an error result if the
                server cannot be started.
            ``'subprocess'``
                Skip the server entirely; always run in this process.
                Suitable for batch/HPC workflows where each refinement is
                isolated.
        allow_unsupported_engine_version: test-only bypass of the engine
            version check (A53). Not reachable through ``powderline.run()``.

    Returns:
        **Normal refinement** (``validate_only=False``):

        - ``success`` (bool)
        - ``run_id`` (str) — UUID4 string uniquely identifying this run
        - ``rwp`` (float | None) — final weighted-profile R factor
        - ``elapsed_time`` (float) — wall-clock seconds
        - ``method`` (str) — ``'server'`` or ``'subprocess'``
        - ``fit_profile`` (pd.DataFrame) — columns: two_theta, y_obs,
          y_weights, y_calc, y_diff, y_bkg, q_values, d_spacings
        - ``unit_cell_data`` (dict) — ``{phase_name: pd.DataFrame}``
          with columns: parameter, value, esd
        - ``peak_list_data`` (dict) — ``{phase_name: pd.DataFrame}``
          with columns: h, k, l, multiplicity, d_spacing, 2theta, …
        - ``refined_parameters`` (pd.DataFrame) — 9 columns:
          parameter_name, descriptive_name, phase_name, phase_idx,
          atom_name, atom_idx, value, esd, category
          (empty DataFrame with all 9 columns when nothing was refined)
        - ``spf_peaks`` (pd.DataFrame) — SPF peak report (empty for
          non-SPF runs)
        - ``spf_convergence_diagnostics`` (pd.DataFrame) — per-peak
          diagnostics for peaks with convergence issues (empty when all
          converged or for non-SPF runs)
        - ``output_files`` (list[str]) — paths written to ``output_dir``
        - ``error`` (str | None) — error message when ``success=False``
        - ``traceback`` (str | None)
        - native ``gsasii.*`` recipes also carry ``r_exp``, ``gof``,
          ``chi2_red``, ``simulation_mode``, ``engine_details`` and the
          structured ``warnings`` list (A125, A129, A115)

        **Validate-only** (``validate_only=True``) — a slim summary dict, no
        refinement executed, no output files written, no ``run_id``:
        ``success``, ``rwp`` (None), ``elapsed_time`` (0.0), ``method``
        (``'validate_only'``), ``schema_name``, ``phases``,
        ``refinement_cycles``, ``simulation_mode``; plus
        ``core_schema_version`` / ``engine_schema_version`` / ``warnings``
        for a native recipe, ``schema_version`` for a 0.26.0 one.

    Raises:
        pydantic.ValidationError: the recipe fails schema validation.
        ValueError: an invalid ``execution_mode``, or a 0.26.0 simulation-mode
            violation.
        EngineNotAvailableError: GSAS-II is not importable (not for ``validate_only``).
        EngineVersionError: the installed GSAS-II is outside ``ENGINE_VERSION_SPEC``.
        OSError: ``output_dir`` cannot be created.
    """
    # 0. Validate execution_mode early to catch typos
    if execution_mode not in EXECUTION_MODES:
        raise ValueError(
            f"Invalid execution_mode={execution_mode!r}. "
            f"Must be one of: {', '.join(EXECUTION_MODES)}"
        )

    # 1. The installed engine must be a supported version (skipped for validate_only)
    if not validate_only:
        check_engine_version(gateway=NAME, engine="GSAS-II", installed=installed_engine_version(),
                             spec=ENGINE_VERSION_SPEC,
                             allow_unsupported=allow_unsupported_engine_version)

    # 2. Validate (raises ValidationError / ValueError on failure)
    recipe = validate(recipe, verbose=verbose)
    # A native gsasii.* recipe's structured warnings come from validating the
    # user's recipe and are carried to the result (A115, A119): here for
    # validate_only and the in-process run, by GSASClient on its paths
    native = schema.is_native_recipe(recipe)
    warnings = collect_warnings(recipe) if native else None

    # 3. Validate_only short-circuit
    if validate_only:
        controls = recipe.payload.refinement_controls
        phases = getattr(recipe.payload, 'phases', None)
        summary = {
            'success': True,
            'rwp': None,
            'elapsed_time': 0.0,
            'method': 'validate_only',
            'schema_name': recipe.schema_name,
            'phases': len(phases) if phases else 0,
            'refinement_cycles': controls.refinement_cycles,
            'simulation_mode': controls.refinement_cycles == 1,
        }
        if native:
            summary.update(core_schema_version=recipe.core_schema_version,
                           engine_schema_version=recipe.engine_schema_version, warnings=warnings)
        else:
            summary['schema_version'] = recipe.schema_version
        return summary

    # 4. Ensure output directory exists
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # 5. Dispatch by execution_mode
    if execution_mode == 'subprocess':
        result = run_refinement(recipe, output_dir, verbose=verbose, method='subprocess')
    else:
        from powderline.gateways.gsasii.client import GSASClient
        fallback = execution_mode != 'server'
        client = GSASClient(fallback_to_subprocess=fallback)
        result = client.submit_simulation(
            recipe=recipe,
            output_dir=output_dir,
            verbose=verbose,
            auto_start_server=True,
        )

    if native and execution_mode == 'subprocess':  # GSASClient adds them on its own paths
        result['warnings'] = warnings + list(result.get('warnings') or [])
    elif not native:  # the server's response model carries the native-only keys as null; a 0.26.0 result never had them
        for key in ('r_exp', 'gof', 'chi2_red', 'simulation_mode', 'engine_details', 'warnings'):
            if key in result and result[key] is None:
                del result[key]

    return _frames(result)


def _frames(result: dict) -> dict:
    """Normalise the structured fields to DataFrames and guarantee the ``error``/``traceback`` keys.

    ``run_refinement()`` and the server return JSON-serializable primitives;
    converting here ensures callers always receive DataFrames regardless of
    ``execution_mode``, without touching the HTTP serialization boundary.
    """
    fit_profile_raw = result.get('fit_profile')
    result['fit_profile'] = (
        pd.DataFrame(fit_profile_raw) if fit_profile_raw else pd.DataFrame()
    )
    # `or {}` (not a .get default): early-failure results serialize these
    # tables as an explicit null, which .get(key, {}) passes through as None.
    result['unit_cell_data'] = {
        phase: pd.DataFrame(records)
        for phase, records in (result.get('unit_cell_data') or {}).items()
    }
    result['peak_list_data'] = {
        phase: pd.DataFrame(records)
        for phase, records in (result.get('peak_list_data') or {}).items()
    }
    refined_params_raw = result.get('refined_parameters')
    result['refined_parameters'] = (
        pd.DataFrame(refined_params_raw)
        if refined_params_raw
        else pd.DataFrame(columns=REFINED_PARAMETER_COLUMNS)
    )
    spf_peaks_raw = result.get('spf_peaks')
    result['spf_peaks'] = (
        pd.DataFrame(spf_peaks_raw) if spf_peaks_raw else pd.DataFrame()
    )
    spf_diag_raw = result.get('spf_convergence_diagnostics')
    result['spf_convergence_diagnostics'] = (
        pd.DataFrame(spf_diag_raw) if spf_diag_raw else pd.DataFrame()
    )

    # Ensure 'error' key is always present (None on success) so callers can
    # safely use result['error'] without KeyError regardless of success state.
    # Similarly normalise 'traceback' so callers never hit a KeyError.
    result.setdefault('error', None)
    result.setdefault('traceback', None)
    return result


def get_gateway() -> Gateway:
    return Gateway(name=NAME, capabilities=capabilities, validate=validate, run=run)
