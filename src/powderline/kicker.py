"""PowderLine: Automated powder X-ray diffraction Rietveld refinements using GSAS-II.

File-less architecture (since schema 0.25):
- All data (XRD patterns, structures, instrument parameters) embedded in JSON
- No external file dependencies (CIF, CHI, INSTPRM)
- File handling occurs upstream of PowderLine

This module orchestrates the complete refinement workflow: JSON parsing, GSAS-II
project initialization, parameter setting, refinement execution, and output generation.
"""
from __future__ import annotations
import sys
import uuid
import yaml
import json
import argparse
import random as ran
import numpy as np
import pandas as pd
from typing import Any, Dict, List, Literal, Optional, Sequence, Union
from pathlib import Path
from pydantic import ValidationError
from GSASII import GSASIIscriptable as G2  # type: ignore
from GSASII import GSASIIlattice as G2lat
from GSASII import GSASIImapvars as G2mv
from GSASII import GSASIIobj as G2obj
from GSASII import GSASIIspc as G2spc
from GSASII import GSASIIElem as G2elem
from GSASII import GSASIIpwd as G2pwd
from powderline.schema import RecipeModel, RefinementControls
from powderline.constraints import atom_refinement_plan, cell_refinement_plan
from powderline._status import CHECK, CROSS, INFO, WARN, emoji
from dataclasses import dataclass

# Backward-compatible re-exports: the GSAS-II implementation moved to
# powderline.gateways.gsasii (multi-engine refactor, re/01). These keep every
# existing ``powderline.kicker.<name>`` import working; removed in a later branch.
from powderline.gateways.gsasii.helpers import (  # noqa: F401
    OutputNamingConfig,
    OUTPUT_NAMING,
    DEFAULT_HIST_SCALE_VAL,
    DEFAULT_HIST_SCALE_REFINE_FLAG,
    DEFAULT_PHASE_CRYSTALLITE_SIZE,
    DEFAULT_PHASE_MICROSTRAIN,
    DEFAULT_SPF_SIGMA_MIN,
    DEFAULT_SPF_GAMMA_MIN,
    unpack_refinement_parameter,
    require_phase,
    calculate_peak_widths,
    has_active_refinement_parameter,
)
from powderline.gateways.gsasii.project import (  # noqa: F401
    InstParmValue,
    InstParmDict,
    G2InstParmDict,
    _to_gsasii_instparm_dict,
    add_powder_histogram_from_arrays,
    add_phase_from_cif_dict,
    add_phases_from_dict,
)
from powderline.gateways.gsasii.setters import (  # noqa: F401
    set_hist_scale,
    set_fit_range_hist,
    set_chebyshev_background,
    set_single_peak_background,
    set_single_peaks,
    set_phase_scale,
    set_phase_unit_cell,
    set_phase_size_broadening,
    _set_isotropic_size_broadening,
    set_phase_strain_broadening,
    _set_isotropic_strain_broadening,
    set_phase_atom_parameters,
    set_phase_parameterization,
    set_instrument_parameterization,
    set_refinement_cycles,
)
from powderline.gateways.gsasii.executors import (  # noqa: F401
    _refine_with_message,
    execute_rietveld_refinement,
    execute_spf_refinement,
    SCHEMA_EXECUTORS,
)
# Native gsasii.* models -> executors.run_refinement; 0.26.0 RecipeModel -> the legacy copy (re/04)
from powderline.gateways.gsasii.routing import run_refinement  # noqa: F401
from powderline.gateways.gsasii.schema import is_native_recipe
from powderline.schema_core import collect_warnings
from powderline.gateways.gsasii.extractors import (  # noqa: F401
    calculate_cell_esds_from_A_matrix,
    extract_refined_params_from_project,
    extract_refined_params_from_lst,
    get_descriptive_param_name,
    parse_parameter_associations,
    build_phase_name_mapping,
    build_atom_name_mapping,
    export_refined_parameters_csv,
    _extract_fit_profile,
    _extract_spf_peak_report,
    _extract_phase_reports,
    _extract_refined_parameters,
)

from powderline.gateways.gsasii.validation import (  # noqa: F401
    validate_simulation_mode_parameters,
    validate,
)


# Moved to the engine-free core (re/07, P-06); re-exported for existing imports.
from powderline.recipes import load_recipe_asset  # noqa: F401


def is_template_file(recipe_dict: dict, input_path: Path) -> tuple[bool, str | None]:
    """
    Detect if the recipe file is a template that shouldn't be run directly.

    Uses two-level detection strategy:
    1. Path contains "template" (case-insensitive)
    2. Missing required fields based on schema_name (for GSASII_Rietveld or GSASII_SPF)

    Args:
        recipe_dict: Recipe dictionary loaded from JSON
        input_path: Path to the JSON file

    Returns:
        tuple[bool, str | None]: A 2-tuple ``(is_template, reason)``. ``is_template``
        is True if this appears to be a template, and ``reason`` explains why it
        was detected as a template. If not a template, returns ``(False, None)``.

    Examples:
        >>> is_template_file({}, Path("example_template/input.json"))
        (True, "filename or path contains 'template'")

        >>> is_template_file({"schema_name": "GSASII_Rietveld", "payload": {"xrd_data": {...}}}, Path("example_LaB6/input.json"))
        (False, None)
    """
    # Check 1: Path contains "template"
    if "template" in str(input_path).lower():
        return True, "filename or path contains 'template'"

    # Check 2: Missing payload or schema-specific required fields
    payload = recipe_dict.get('payload', {})
    if not payload:
        return True, "missing payload"

    # Check for schema-specific required fields
    schema_name = recipe_dict.get('schema_name')
    if schema_name == 'GSASII_Rietveld':
        required_fields = ['xrd_data', 'instrument', 'phases', 'refinement_controls']
        missing = [field for field in required_fields if field not in payload or payload[field] is None]
        if missing:
            return True, f"GSASII_Rietveld schema missing required fields: {missing}"
    elif schema_name == 'GSASII_SPF':
        required_fields = ['xrd_data', 'instrument', 'single_peaks', 'refinement_controls']
        missing = [field for field in required_fields if field not in payload or payload[field] is None]
        if missing:
            return True, f"GSASII_SPF schema missing required fields: {missing}"
    else:
        # Fallback: check for common core fields if schema unknown
        core_fields = ['xrd_data', 'instrument', 'refinement_controls']
        missing_core = [field for field in core_fields if field not in payload]
        if len(missing_core) == len(core_fields):
            return True, "missing all required fields in payload (xrd_data, instrument, refinement_controls)"

    return False, None


########################################
# Public programmatic API
########################################

def run(
    recipe: RecipeModel | dict,
    output_dir: Path,
    *,
    verbose: bool = False,
    validate_only: bool = False,
    execution_mode: Literal['auto', 'server', 'subprocess'] = 'auto',
) -> dict:
    """Execute a PowderLine refinement from an in-memory recipe.

    This is the primary public API for programmatic use. It encapsulates
    the full pipeline: schema validation, simulation-mode checks, output
    directory creation, and refinement execution with automatic backend
    selection.

    Note: The ``is_template_file()`` guard is **not** applied here.
    That check is CLI-only (guards against accidentally running bare
    template JSON files from disk). When calling ``run()``
    programmatically the caller is responsible for ensuring the recipe
    contains real data in ``payload.xrd_data``.

    Args:
        recipe: A fully-populated ``RecipeModel`` instance or equivalent
            dict. XRD data must already be injected into
            ``recipe.payload.xrd_data`` before calling.
        output_dir: Directory where output files will be written
            (``dummy.gpx``, ``dummy.lst``, CSV reports,
            ``fit_profile.txt``). Created if it does not exist.
        verbose: If True, print detailed progress to stdout.
        validate_only: If True, validate the recipe and return a summary
            dict without running a refinement. No output files are
            written in this mode.
        execution_mode: Controls the backend used to execute the
            refinement. One of:

            ``'auto'`` *(default)*
                Try the persistent GSAS-II server first (auto-starting
                it if needed); fall back to subprocess if unavailable.
            ``'server'``
                Use the server only. Fails with an error result if the
                server cannot be started.
            ``'subprocess'``
                Skip the server entirely; always run in a fresh
                subprocess. Suitable for batch/HPC workflows where each
                refinement is isolated.

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
        - ``spf_peaks`` (pd.DataFrame) — SPF peak report with columns:
          position_2theta, intensity, sigma, sigma_squared, gamma,
          fwhm_gaussian, fwhm_lorentzian, fwhm_pseudovoigt,
          integral_breadth_gaussian, integral_breadth_lorentzian,
          integral_breadth_pseudovoigt, fwhm_gsas_verification,
          converged, convergence_detail
          (empty DataFrame for non-SPF runs)
        - ``spf_convergence_diagnostics`` (pd.DataFrame) — per-peak
          diagnostic info for peaks with convergence issues; columns:
          peak_index, position_2theta, final_sigma_sq, final_gamma,
          status, notes (empty DataFrame when all peaks converged or
          for non-SPF runs)
        - ``output_files`` (list[str]) — paths written to ``output_dir``
          (informational; secondary to the DataFrame fields above)
        - ``error`` (str | None) — error message when ``success=False``

        **Validate-only** (``validate_only=True``) — a slim summary dict,
        no refinement executed, no output files written, no ``run_id``:

        - ``success`` (bool)
        - ``rwp`` (None)
        - ``elapsed_time`` (0.0)
        - ``method`` (``'validate_only'``)
        - ``schema_name`` (str)
        - ``schema_version`` (str)
        - ``phases`` (int) — number of phases in payload
        - ``refinement_cycles`` (int)
        - ``simulation_mode`` (bool)

    Raises:
        pydantic.ValidationError: If the recipe fails schema validation.
        ValueError: If simulation-mode constraints are violated.
        OSError: If ``output_dir`` cannot be created (e.g. permission denied).

    Example::

        import json
        from pathlib import Path
        import powderline

        recipe_dict = json.load(open("my_recipe.json"))
        result = powderline.run(recipe_dict, Path("output/"))
        if not result["success"]:
            raise RuntimeError(f"Refinement failed: {result['error']}")
        print(f"Rwp = {result['rwp']:.3f}%  [{result['method']} mode]")
        # Access structured results directly as DataFrames:
        unit_cell_df = result["unit_cell_data"].get("LaB6", pd.DataFrame())
    """
    # 0. Validate execution_mode early to catch typos
    _VALID_EXECUTION_MODES = ('auto', 'server', 'subprocess')
    if execution_mode not in _VALID_EXECUTION_MODES:
        raise ValueError(
            f"Invalid execution_mode={execution_mode!r}. "
            f"Must be one of: {', '.join(_VALID_EXECUTION_MODES)}"
        )

    # 1. Validate (raises ValidationError / ValueError on failure)
    recipe = validate(recipe, verbose=verbose)
    # A native gsasii.* recipe's structured warnings come from validating the
    # user's recipe and are carried to the result (A115, A119): here for
    # validate_only and the in-process run, by GSASClient on its paths
    native = is_native_recipe(recipe)
    warnings = collect_warnings(recipe) if native else None

    # 2. Validate_only short-circuit
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

    # 3. Ensure output directory exists
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # 4. Dispatch by execution_mode
    if execution_mode == 'subprocess':
        result = run_refinement(recipe, output_dir, verbose=verbose, method='subprocess')
    else:
        from powderline.gsas_client import GSASClient
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

    # Normalise structured data fields to DataFrames for a consistent API.
    # run_refinement() and the server return JSON-serializable primitives;
    # converting here ensures callers always receive DataFrames regardless
    # of execution_mode, without touching the HTTP serialization boundary.
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
        else pd.DataFrame(columns=[
            'parameter_name', 'descriptive_name', 'phase_name', 'phase_idx',
            'atom_name', 'atom_idx', 'value', 'esd', 'category'
        ])
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


if __name__ == "__main__":
    # Ensure emoji/Unicode output works on Windows consoles that default to a
    # legacy code page (e.g. cp1252), which cannot encode the status emoji below.
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            try:
                _reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass

    # Parse command line arguments
    parser = argparse.ArgumentParser(
        description="Run GSAS-II refinement from JSON recipe",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Execution modes:
  Default (no flags):  Try server → auto-start if needed → fall back to subprocess
  --use-server:        Use server only (fail if unavailable, auto-start enabled)
  --no-server:         Use subprocess only (skip server entirely)

Examples:
  pixi run kicker recipe.json                    # Smart mode (auto-detect)
  pixi run kicker recipe.json --use-server       # Server only (with auto-start)
  pixi run kicker recipe.json --no-server        # Subprocess only
  pixi run kicker recipe.json --verbose          # Show detailed output
        """
    )

    parser.add_argument("input_json", type=Path, help="Path to input JSON recipe file")
    parser.add_argument("--output", type=Path, default=None, help="Output directory (default: input_json_parent/output)")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose output")
    parser.add_argument("--validate-only", action="store_true", help="Validate recipe without running refinement")

    # Execution mode flags
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        '--use-server',
        action='store_true',
        help='Force server mode (auto-start enabled, fail if still unavailable)'
    )
    mode_group.add_argument(
        '--no-server',
        action='store_true',
        help='Force subprocess mode (skip server entirely)'
    )

    args = parser.parse_args()

    # Set output directory
    output_dir = args.output if args.output else args.input_json.parent / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Load recipe
    recipe_dict = load_recipe_asset(args.input_json)

    if args.verbose:
        print(f"This is the recipe:\n\n{recipe_dict}\n\n")

    # Check if this is a template file
    is_template, template_reason = is_template_file(recipe_dict, args.input_json)
    if is_template:
        print(f"\n{CROSS} Error: This appears to be a template file ({template_reason}).")
        print(f"\nTemplate files are not meant to be run directly.")
        print(f"To create a working example:")
        print(f"  1. Copy the template directory:")
        print(f"     cp -r examples/example_template")
        print(f"  2. Edit examples/example_template/input.json with your refinement parameters")
        print(f"  3. See examples/example_template/DESCRIPTION.md for guidance")
        print(f"\nOr run an existing example:")
        print(f"     pixi run kicker examples/example_LaB6/input.json\n")
        sys.exit(1)

    # Validate recipe, check constraints, and execute via run()
    try:
        # Map CLI flags to execution_mode
        if args.no_server:
            execution_mode = 'subprocess'
        elif args.use_server:
            execution_mode = 'server'
        else:
            execution_mode = 'auto'

        if args.verbose:
            print(f"{CHECK} Template check passed. Starting refinement...\n")

        result = run(
            recipe=recipe_dict,
            output_dir=output_dir,
            verbose=args.verbose,
            validate_only=args.validate_only,
            execution_mode=execution_mode,
        )

    except ValidationError as e:
        print(f"\n{CROSS} Recipe validation failed: {args.input_json}\n")
        print("Validation errors:")
        for error in e.errors():
            location = " -> ".join(str(loc) for loc in error['loc'])
            print(f"  • {location}: {error['msg']}")
        print(f"\nPlease fix the errors above and try again.")
        print(f"See examples/example_LaB6/input.json for a working example.\n")
        sys.exit(1)

    except ValueError as e:
        print(f"\n{CROSS} {e}\n")
        sys.exit(1)

    except Exception as e:
        print(f"\n{CROSS} Unexpected error during refinement:")
        print(f"   {str(e)}")
        print(f"\nTry running with --verbose for more details:")
        print(f"  pixi run kicker {args.input_json} --verbose\n")
        sys.exit(1)

    # Handle validate_only result
    if args.validate_only:
        print(f"{CHECK} Recipe validation successful: {args.input_json}")
        print(f"   Schema name: {result.get('schema_name')}")
        print(f"   Schema version: {result.get('schema_version')}")
        print(f"   Phases: {result.get('phases', 0)}")
        print(f"   Refinement cycles: {result.get('refinement_cycles')}")
        if result.get('simulation_mode'):
            print(f"   Mode: Simulation (all parameters locked)")
        sys.exit(0)

    # Show execution mode and timing
    mode_emoji = emoji("🚀" if result.get('method') == 'server' else "🐢")
    print(f"\n{INFO} Executed using {result.get('method', 'unknown')} mode {mode_emoji} ({result.get('elapsed_time', 0):.1f}s)")
    if args.verbose and 'run_id' in result:
        print(f"{INFO} Run ID: {result['run_id']}")

    # Check for success
    if not result.get('success'):
        print(f"\n{CROSS} Refinement failed:")
        print(f"   {result.get('error', 'Unknown error')}")
        print(f"\nCheck .lst file for detailed error messages: {output_dir}/*.lst\n")
        sys.exit(1)

    # Report final Rwp
    if result.get('rwp') is not None:
        print(f"{CHECK} Refinement complete. Final Rwp: {result['rwp']:.3f}%\n")
    else:
        print(f"{CHECK} Refinement complete.\n")
