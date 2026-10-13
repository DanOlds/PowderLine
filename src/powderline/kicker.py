"""PowderLine command line: run or validate a recipe with the engine its ``schema_name`` names.

::

    powderline <recipe.json> [--output DIR] [--validate-only] [--verbose]
                             [--use-server | --no-server] [--topas-dir DIR] [--topas-version V]

(``pixi run kicker ...`` / ``python src/powderline/kicker.py ...`` run the same
``main``; re/08 binds the task names.) The CLI loads the recipe
(:func:`powderline.load_recipe_asset`), refuses template files, maps the
engine flags onto the gateway's run options and calls :func:`powderline.run`;
the engine is the one named by the recipe's ``schema_name``, so the same
command serves ``gsasii.*``, ``topas.*`` and ``easydiffraction.*`` recipes.
An engine flag given for a recipe of another engine is an error. Exit status 0 on success, 1 otherwise.

Engine-free: this module imports no engine; GSAS-II is imported by the gsasii
gateway when a gsasii recipe runs.

**Transitional facade (removed with the 0.26.0 schema):** the GSAS-II
implementation names that ``powderline.kicker`` used to re-export
(``run_refinement``, the setters/extractors, ``G2``, ...) still resolve, lazily,
through the module ``__getattr__`` below, so existing imports and test patches
keep working without this module importing GSAS-II itself.
"""

from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from powderline import api
from powderline._status import CHECK, CROSS, INFO, WARN, emoji
from powderline.api import run, validate  # noqa: F401  (the CLI's entry points; also ``powderline.kicker.run``)
from powderline.exceptions import PowderLineError
from powderline.recipes import load_recipe_asset

#: CLI flag (argparse dest) -> the gateway it belongs to; any other recipe rejects it.
_FLAG_GATEWAYS = {
    "use_server": "gsasii",
    "no_server": "gsasii",
    "topas_dir": "topas",
    "topas_version": "topas",
}
_FLAG_NAMES = {"use_server": "--use-server", "no_server": "--no-server",
               "topas_dir": "--topas-dir", "topas_version": "--topas-version"}


def is_template_file(recipe_dict: dict, input_path: Path) -> tuple[bool, str | None]:
    """Detect a recipe file that is a template and must not be run directly (CLI-only guard).

    Two-level detection:

    1. the path contains "template" (case-insensitive);
    2. the payload is missing, or lacks the fields the workflow needs: a
       ``*.rietveld`` recipe needs ``xrd_data``, ``instrument``, ``phases`` and
       ``refinement_controls``; a ``*.spf`` one ``single_peaks`` instead of
       ``phases``; any other schema name is a template only when all of
       ``xrd_data``, ``instrument`` and ``refinement_controls`` are absent.

    Returns:
        ``(is_template, reason)``; ``(False, None)`` for a runnable recipe.

    Examples:
        >>> is_template_file({}, Path("example_template/input.json"))
        (True, "filename or path contains 'template'")
        >>> is_template_file({"schema_name": "gsasii.rietveld", "payload": {"xrd_data": {...}, ...}}, Path("in.json"))
        (False, None)
    """
    if "template" in str(input_path).lower():
        return True, "filename or path contains 'template'"

    payload = recipe_dict.get('payload', {}) if isinstance(recipe_dict, dict) else {}
    if not payload:
        return True, "missing payload"

    schema_name = recipe_dict.get('schema_name')
    workflow = None
    if isinstance(schema_name, str):
        if schema_name.endswith('.rietveld'):
            workflow = ('rietveld', ['xrd_data', 'instrument', 'phases', 'refinement_controls'])
        elif schema_name.endswith('.spf'):
            workflow = ('single-peak-fitting', ['xrd_data', 'instrument', 'single_peaks', 'refinement_controls'])
    if workflow is not None:
        label, required_fields = workflow
        missing = [field for field in required_fields if field not in payload or payload[field] is None]
        if missing:
            return True, f"{schema_name} ({label}) schema missing required fields: {missing}"
    else:
        core_fields = ['xrd_data', 'instrument', 'refinement_controls']
        missing_core = [field for field in core_fields if field not in payload]
        if len(missing_core) == len(core_fields):
            return True, "missing all required fields in payload (xrd_data, instrument, refinement_controls)"

    return False, None


def engine_options_from_args(args: argparse.Namespace, gateway_name: str) -> dict:
    """Map the engine flags onto the gateway's ``run`` options; refuse a flag for another engine.

    ``--no-server`` / ``--use-server`` → gsasii ``execution_mode`` (``'subprocess'``
    / ``'server'``; neither → the gateway's default, ``'auto'``);
    ``--topas-dir`` / ``--topas-version`` → topas ``topas_dir`` / ``topas_version``.

    Raises:
        ValueError: a flag that belongs to a gateway other than ``gateway_name``.
    """
    given = {dest: getattr(args, dest, None) for dest in _FLAG_GATEWAYS}
    for dest, value in given.items():
        if value and _FLAG_GATEWAYS[dest] != gateway_name:
            raise ValueError(
                f"{_FLAG_NAMES[dest]} applies to {_FLAG_GATEWAYS[dest]!r} recipes only; this recipe's "
                f"schema_name names the {gateway_name!r} engine")
    options: dict[str, Any] = {}
    if given["no_server"]:
        options["execution_mode"] = "subprocess"
    elif given["use_server"]:
        options["execution_mode"] = "server"
    if given["topas_dir"] is not None:
        options["topas_dir"] = given["topas_dir"]
    if given["topas_version"] is not None:
        options["topas_version"] = given["topas_version"]
    return options


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="powderline",
        description="Run (or validate) a PowderLine recipe with the engine its schema_name names",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Engine flags (accepted only for a recipe of that engine):
  gsasii.*  default:      try the GSAS-II server, auto-start it, fall back to in-process
            --use-server: server only (fail if it cannot be started)
            --no-server:  in-process only (skip the server)
  topas.*   --topas-dir DIR / --topas-version V: the TOPAS installation to run
            (a run needs a declared version: --topas-version or .powderline_config.yaml)

Examples:
  powderline examples/example_LaB6/input.json                     # engine from schema_name
  powderline examples/example_LaB6/input.json --no-server --verbose
  powderline recipe.json --validate-only                          # no engine needed
  powderline topas_recipe.json --topas-version 6 --output out/
        """
    )
    parser.add_argument("input_json", type=Path, help="Path to the recipe file (.json / .yaml)")
    parser.add_argument("--output", type=Path, default=None,
                        help="Output directory (default: <recipe dir>/output)")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose output")
    parser.add_argument("--validate-only", action="store_true",
                        help="Validate the recipe and report its summary; run nothing")
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument('--use-server', action='store_true',
                            help='gsasii: server mode only (auto-start enabled, fail if still unavailable)')
    mode_group.add_argument('--no-server', action='store_true',
                            help='gsasii: in-process mode only (skip the server entirely)')
    parser.add_argument("--topas-dir", type=Path, default=None, help="topas: directory containing tc.exe")
    parser.add_argument("--topas-version", default=None, help="topas: the installed TOPAS version to run")
    return parser


def _print_validate_only(result: dict, input_path: Path) -> None:
    print(f"{CHECK} Recipe validation successful: {input_path}")
    print(f"   Schema name: {result.get('schema_name')}")
    if result.get('core_schema_version') is not None:
        print(f"   Core schema version: {result['core_schema_version']}")
    if result.get('engine_schema_version') is not None:
        print(f"   Engine schema version: {result['engine_schema_version']}")
    if result.get('engine_version') is not None:
        print(f"   Engine version: {result['engine_version']}")
    print(f"   Phases: {result.get('phases', 0)}")
    if 'refinement_cycles' in result:
        print(f"   Refinement cycles: {result['refinement_cycles']}")
    if 'iters' in result:
        print(f"   Iterations: {result['iters']}")
    if result.get('calculator') is not None:
        print(f"   Calculator: {result['calculator']}")
    if result.get('simulation_mode'):
        print(f"   Mode: Simulation (all parameters locked)")
    _print_warnings(result.get('warnings'))


def _print_warnings(warnings) -> None:
    if not warnings:
        return
    print(f"{WARN} {len(warnings)} warning(s):")
    for w in warnings:
        where = f" ({w['field_path']})" if w.get('field_path') else ""
        print(f"   - {w['code']}: {w['message']}{where}")


def main(argv: list[str] | None = None) -> int:
    """Entry point of the ``powderline`` command; returns the exit status."""
    # Ensure emoji/Unicode output works on Windows consoles that default to a
    # legacy code page (e.g. cp1252), which cannot encode the status emoji below.
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            try:
                _reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass

    args = build_parser().parse_args(argv)
    output_dir = args.output if args.output else args.input_json.parent / "output"

    try:
        recipe_dict = load_recipe_asset(args.input_json)
    except OSError as e:
        print(f"\n{CROSS} Cannot read recipe {args.input_json}: {e}\n")
        return 1

    if args.verbose:
        print(f"This is the recipe:\n\n{recipe_dict}\n\n")

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
        return 1

    try:
        gateway_name = api.gateway_name_for(api.schema_name_of(recipe_dict))
        engine_options = engine_options_from_args(args, gateway_name)
        if not args.validate_only:
            output_dir.mkdir(parents=True, exist_ok=True)
        if args.verbose:
            print(f"{CHECK} Template check passed. Engine: {gateway_name}. Starting refinement...\n")

        result = run(
            recipe_dict,
            output_dir,
            verbose=args.verbose,
            validate_only=args.validate_only,
            **engine_options,
        )

    except ValidationError as e:
        print(f"\n{CROSS} Recipe validation failed: {args.input_json}\n")
        print("Validation errors:")
        for error in e.errors():
            location = " -> ".join(str(loc) for loc in error['loc'])
            print(f"  • {location}: {error['msg']}")
        print(f"\nPlease fix the errors above and try again.")
        print(f"See examples/example_LaB6/input.json for a working example.\n")
        return 1

    except (ValueError, PowderLineError) as e:
        print(f"\n{CROSS} {e}\n")
        return 1

    except Exception as e:
        print(f"\n{CROSS} Unexpected error during refinement:")
        print(f"   {str(e)}")
        print(f"\nTry running with --verbose for more details:")
        print(f"  pixi run kicker {args.input_json} --verbose\n")
        return 1

    if args.validate_only:
        _print_validate_only(result, args.input_json)
        return 0

    method = result.get('method', 'unknown')
    mode_emoji = emoji("🚀" if method == 'server' else "🐢")
    print(f"\n{INFO} Executed using {method} mode {mode_emoji} ({result.get('elapsed_time', 0):.1f}s)")
    if args.verbose and 'run_id' in result:
        print(f"{INFO} Run ID: {result['run_id']}")

    if not result.get('success'):
        print(f"\n{CROSS} Refinement failed:")
        print(f"   {result.get('error', 'Unknown error')}")
        hint = f"{output_dir}/*.lst" if gateway_name == "gsasii" else str(output_dir)
        print(f"\nCheck the engine's log for details: {hint}\n")
        return 1

    _print_warnings(result.get('warnings'))
    if result.get('rwp') is not None:
        print(f"{CHECK} Refinement complete. Final Rwp: {result['rwp']:.3f}%\n")
    else:
        print(f"{CHECK} Refinement complete.\n")
    return 0


# --- transitional facade: ``powderline.kicker.<GSAS-II implementation name>`` -----------------------

_GSASII_MODULES = {
    "G2": "GSASII.GSASIIscriptable", "G2lat": "GSASII.GSASIIlattice", "G2mv": "GSASII.GSASIImapvars",
    "G2obj": "GSASII.GSASIIobj", "G2spc": "GSASII.GSASIIspc", "G2elem": "GSASII.GSASIIElem",
    "G2pwd": "GSASII.GSASIIpwd",
}
_FACADE_NAMES = {
    "powderline.gateways.gsasii.helpers": (
        "OutputNamingConfig", "OUTPUT_NAMING", "DEFAULT_HIST_SCALE_VAL", "DEFAULT_HIST_SCALE_REFINE_FLAG",
        "DEFAULT_PHASE_CRYSTALLITE_SIZE", "DEFAULT_PHASE_MICROSTRAIN", "DEFAULT_SPF_SIGMA_MIN",
        "DEFAULT_SPF_GAMMA_MIN", "unpack_refinement_parameter", "require_phase", "calculate_peak_widths",
        "has_active_refinement_parameter"),
    "powderline.gateways.gsasii.project": (
        "InstParmValue", "InstParmDict", "G2InstParmDict", "_to_gsasii_instparm_dict",
        "add_powder_histogram_from_arrays", "add_phase_from_cif_dict", "add_phases_from_dict"),
    "powderline.gateways.gsasii.setters": (
        "set_hist_scale", "set_fit_range_hist", "set_chebyshev_background", "set_single_peak_background",
        "set_single_peaks", "set_phase_scale", "set_phase_unit_cell", "set_phase_size_broadening",
        "_set_isotropic_size_broadening", "set_phase_strain_broadening", "_set_isotropic_strain_broadening",
        "set_phase_atom_parameters", "set_phase_parameterization", "set_instrument_parameterization",
        "set_refinement_cycles"),
    "powderline.gateways.gsasii.executors": (
        "_refine_with_message", "execute_rietveld_refinement", "execute_spf_refinement", "SCHEMA_EXECUTORS"),
    "powderline.gateways.gsasii.routing": ("run_refinement",),
    "powderline.gateways.gsasii.extractors": (
        "calculate_cell_esds_from_A_matrix", "extract_refined_params_from_project",
        "extract_refined_params_from_lst", "get_descriptive_param_name", "parse_parameter_associations",
        "build_phase_name_mapping", "build_atom_name_mapping", "export_refined_parameters_csv",
        "_extract_fit_profile", "_extract_spf_peak_report", "_extract_phase_reports",
        "_extract_refined_parameters"),
    "powderline.gateways.gsasii.validation": ("validate_simulation_mode_parameters",),
    "powderline.gateways.gsasii.schema": ("is_native_recipe",),
    "powderline.schema_core": ("collect_warnings",),
    "powderline.schema": ("RecipeModel", "RefinementControls"),
    "powderline.constraints": ("atom_refinement_plan", "cell_refinement_plan"),
}
_FACADE = {name: (module, name) for module, names in _FACADE_NAMES.items() for name in names}
_FACADE.update({alias: (module, None) for alias, module in _GSASII_MODULES.items()})


def __getattr__(name: str):
    """Resolve a legacy ``powderline.kicker.<name>`` lazily (PEP 562); imports GSAS-II only when asked for."""
    try:
        module_name, attr = _FACADE[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    module = importlib.import_module(module_name)
    return module if attr is None else getattr(module, attr)


if __name__ == "__main__":
    sys.exit(main())
