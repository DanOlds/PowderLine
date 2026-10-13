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


# The GSAS-II ``run`` moved into its gateway (re/07, P-06); re-exported for
# existing ``powderline.kicker.run`` imports until this module becomes the CLI.
from powderline.gateways.gsasii.gateway import run  # noqa: F401


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
