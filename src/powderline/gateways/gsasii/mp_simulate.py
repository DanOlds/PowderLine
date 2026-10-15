#!/usr/bin/env python
"""CLI tool for generating simulated diffraction patterns from Materials Project structures.

This standalone tool fetches crystal structures from the Materials Project database
and generates simulated powder diffraction patterns using PowderLine's simulation
mode. It ships with the GSAS-II gateway (A43): the recipe it builds is a
``gsasii.rietveld`` 1.0.0 recipe (:mod:`.simulation_builder`) and the pattern
is calculated by GSAS-II. ``powderline.mp_interface`` (the Materials Project
API wrapper) and ``powderline.config_loader`` stay engine-agnostic in core.

Usage:
    pixi run mp-simulate --material-id mp-1234 --output patterns/
    pixi run mp-simulate --formula LaB6 --output patterns/
    pixi run mp-simulate --material-id mp-1234 --wavelength 0.7 --output patterns/
"""

import argparse
import sys
import json
from pathlib import Path
import tempfile

from powderline.exceptions import PowderLineError
from powderline._status import CHECK, CROSS, emoji
from powderline.config_loader import ConfigLoader
from powderline.mp_interface import (
    MPInterface,
    MPInterfaceError,
    MPAuthError,
    MPNotFoundError,
    MPConnectionError,
)
from powderline.gateways.gsasii.simulation_builder import SimulationRecipeBuilder
from powderline.gateways.gsasii.pattern_exporter import PatternExporter
from powderline.gateways.gsasii.client import GSASClient


def main():
    """Main entry point for mp-simulate CLI tool."""
    parser = argparse.ArgumentParser(
        description="Generate simulated diffraction patterns from Materials Project structures",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Simulate pattern for specific material
  pixi run mp-simulate --material-id mp-1234 --output patterns/

  # Search by formula: lists all polymorphs and simulates the most stable
  # one (lowest energy above hull)
  pixi run mp-simulate --formula LaB6 --output patterns/

  # Override wavelength (default: 0.4133 Å / 30 keV)
  pixi run mp-simulate --material-id mp-1234 --wavelength 0.7 --output patterns/

  # Use custom config file
  pixi run mp-simulate --material-id mp-1234 --config my_config.yaml --output patterns/

  # Skip the persistent GSAS-II server (run the simulation in-process)
  pixi run mp-simulate --material-id mp-1234 --no-server --output patterns/

Setup:
  1. Get free API key: https://next-gen.materialsproject.org/api
  2. Copy .powderline_config.yaml.example to .powderline_config.yaml
     and add your API key, or set the MP_API_KEY environment variable
        """
    )

    # Input options
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument(
        '--material-id',
        type=str,
        help='Materials Project ID (e.g., mp-1234 or just 1234)'
    )
    input_group.add_argument(
        '--formula',
        type=str,
        help='Chemical formula to search (e.g., LaB6, Al2O3)'
    )

    # Output options
    parser.add_argument(
        '--output',
        type=Path,
        default=Path('mp_simulation_output'),
        help='Output directory for simulated patterns (default: mp_simulation_output/)'
    )

    # Configuration options
    parser.add_argument(
        '--config',
        type=Path,
        help='Path to config file (default: searches .powderline_config.yaml)'
    )
    parser.add_argument(
        '--wavelength',
        type=float,
        help='Override wavelength in Angstroms (default: 0.4133 Å / 30 keV)'
    )

    # Behavior options
    parser.add_argument(
        '--keep-recipe',
        action='store_true',
        help='Keep generated recipe JSON file for inspection'
    )
    parser.add_argument(
        '--keep-output',
        action='store_true',
        help='Keep full PowderLine output (gpx, lst files)'
    )
    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Show detailed output from refinement'
    )
    parser.add_argument(
        '--no-server',
        action='store_true',
        help='Skip the persistent GSAS-II server; run the simulation in-process'
    )

    args = parser.parse_args()

    # Load configuration
    try:
        config = ConfigLoader(config_path=args.config)
    except Exception as e:
        print(f"\n{CROSS} Error loading configuration: {e}")
        sys.exit(1)

    # Get API key (config file first, then MP_API_KEY environment variable)
    api_key = config.get_mp_api_key()
    if not api_key or api_key == "YOUR_API_KEY_HERE":
        print(f"\n{CROSS} No Materials Project API key found!")
        print("\nSetup instructions:")
        print("  1. Get free API key: https://next-gen.materialsproject.org/api")
        print("  2. Copy .powderline_config.yaml.example to .powderline_config.yaml")
        print("     and replace YOUR_API_KEY_HERE with your actual key,")
        print("     or set the MP_API_KEY environment variable")
        print("  3. Keep .powderline_config.yaml private (it's in .gitignore)\n")
        sys.exit(1)

    # Search (if requested) and fetch structure from Materials Project
    try:
        with MPInterface(api_key=api_key) as mp:
            print(f"{CHECK} Connected to Materials Project API")

            if args.formula:
                print(f"\n{emoji('🔍', '>>')} Searching Materials Project for formula: {args.formula}")
                results = mp.search_by_formula(args.formula)
                if not results:
                    print(f"{CROSS} No materials found for formula '{args.formula}'")
                    sys.exit(1)

                # Results are sorted by energy above hull (most stable first)
                print(f"Found {len(results)} materials:")
                print(f"  {'#':>3}  {'material_id':<14} {'formula':<12} "
                      f"{'space group':<12} {'E_hull (eV/atom)':<17} {'theoretical'}")
                for i, result in enumerate(results, 1):
                    e_hull = result.get('energy_above_hull')
                    e_hull_str = f"{e_hull:.4f}" if e_hull is not None else "N/A"
                    theo = result.get('theoretical')
                    theo_str = "yes" if theo else ("no" if theo is not None else "N/A")
                    print(f"  {i:>3}. {result['material_id']:<14} "
                          f"{result['formula']:<12} "
                          f"{str(result.get('space_group') or 'N/A'):<12} "
                          f"{e_hull_str:<17} {theo_str}")

                selected = results[0]
                material_id = selected['material_id']
                e_hull = selected.get('energy_above_hull')
                e_hull_str = (f"{e_hull:.4f} eV/atom" if e_hull is not None
                              else "unknown energy above hull")
                print(f"\n{CHECK} Selected {material_id} "
                      f"(lowest energy above hull: {e_hull_str})")
            else:
                material_id = args.material_id

            print(f"\n{emoji('📥', '>>')} Fetching structure for {material_id}...")
            structure_data = mp.get_structure(material_id)
            # Normalized by MPInterface (e.g. '2680' -> 'mp-2680'); used in filenames
            material_id = structure_data['material_id']

        formula = structure_data['formula']
        space_group = structure_data['space_group']
        print(f"{CHECK} Retrieved structure: {formula} ({space_group})")
        if not structure_data.get('is_ordered', True):
            print(f"   Note: structure is disordered (partial site occupancies); "
                  f"occupancies are carried into the simulation")
    except MPAuthError as e:
        print(f"\n{CROSS} Materials Project authentication failed: {e}")
        print("\nCheck your API key:")
        print("  - Get a key at https://next-gen.materialsproject.org/api")
        print("  - Set it in .powderline_config.yaml or the MP_API_KEY environment variable\n")
        sys.exit(1)
    except MPNotFoundError as e:
        print(f"\n{CROSS} {e}")
        sys.exit(1)
    except MPConnectionError as e:
        print(f"\n{CROSS} Could not reach the Materials Project API: {e}")
        print("Check your network connection and try again.")
        sys.exit(1)
    except MPInterfaceError as e:
        print(f"\n{CROSS} Materials Project request failed: {e}")
        sys.exit(1)
    except ImportError as e:
        print(f"\n{CROSS} {e}")
        sys.exit(1)

    # Build simulation recipe
    print(f"\n{emoji('⚙️', '')}  Building simulation recipe...")

    # The builder validates and merges the config over the built-in defaults,
    # so read the effective wavelength from its defaults rather than the raw
    # config dict
    try:
        builder = SimulationRecipeBuilder(config.get_simulation_defaults())
    except ValueError as e:
        print(f"\n{CROSS} {e}")
        sys.exit(1)

    # Override wavelength if provided
    if args.wavelength:
        builder.defaults['instrument_defaults']['wavelength'] = args.wavelength
        print(f"   Using custom wavelength: {args.wavelength:.4f} Å")
    wavelength = builder.defaults['instrument_defaults']['wavelength']
    if not args.wavelength:
        print(f"   Using wavelength: {wavelength:.4f} Å "
              f"(~{12.398 / wavelength:.0f} keV)")

    recipe = builder.build_recipe_from_mp_structure(structure_data)

    # Create temporary directory for recipe and intermediate outputs
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        recipe_path = temp_path / "simulation_recipe.json"
        kicker_output = temp_path / "kicker_output"

        # Save recipe
        with open(recipe_path, 'w', encoding='utf-8') as f:
            json.dump(recipe, f, indent=2)
        print(f"{CHECK} Recipe created: {recipe_path}")

        # Run GSAS-II simulation using client (server if available, subprocess fallback)
        print(f"\n{emoji('🚀', '>>')} Running GSAS-II simulation...")

        client = GSASClient()

        # Note: We don't check server availability here because auto-start will handle it
        # The submit_simulation method will print the actual mode used

        try:
            result = client.submit_simulation(
                recipe=recipe,
                output_dir=kicker_output,
                verbose=args.verbose,
                auto_start_server=not args.no_server,
                use_server=not args.no_server
            )

            from powderline import results as envelopes
            envelopes.raise_if_failed(result)  # re/12: a failed envelope raises

            elapsed = result.get('elapsed_time', 0)
            method = result.get('method', 'unknown')
            print(f"{CHECK} Simulation complete ({elapsed:.1f}s via {method})")

        except PowderLineError as e:
            print(f"{CROSS} Simulation failed ({getattr(e, 'reason', 'error')}): {e}")
            if not args.verbose:
                print("\nRun with --verbose for detailed output")
            sys.exit(1)
        except Exception as e:
            print(f"{CROSS} Failed to run simulation: {e}")
            sys.exit(1)

        # Extract and export pattern
        print(f"\n{emoji('📊', '')} Extracting calculated pattern...")
        try:
            args.output.mkdir(parents=True, exist_ok=True)

            # Export from the in-band fit_profile carried in the result —
            # no dependency on the output directory's filesystem state
            chi_path = PatternExporter.export_simulation_results(
                output_dir=kicker_output,
                formula=formula,
                material_id=material_id,
                wavelength=wavelength,
                fit_profile_data=result.get('fit_profile') or {}
            )

            # Copy CHI file to output directory
            final_chi_path = args.output / chi_path.name
            import shutil
            shutil.copy(chi_path, final_chi_path)
            print(f"{CHECK} Pattern saved to: {final_chi_path}")

            # Optionally keep recipe
            if args.keep_recipe:
                recipe_output_path = args.output / f"{material_id}_{formula}_recipe.json"
                shutil.copy(recipe_path, recipe_output_path)
                print(f"{CHECK} Recipe saved to: {recipe_output_path}")

            # Optionally keep full output
            if args.keep_output:
                output_subdir = args.output / f"{material_id}_{formula}_full_output"
                shutil.copytree(kicker_output, output_subdir)
                print(f"{CHECK} Full output saved to: {output_subdir}")

        except Exception as e:
            print(f"{CROSS} Failed to export pattern: {e}")
            sys.exit(1)

    # Success summary
    print(f"\n{'='*60}")
    print(f"{emoji('✨', '')} Success! Simulated diffraction pattern generated.")
    print(f"{'='*60}")
    print(f"Material: {formula} ({material_id})")
    print(f"Space group: {space_group}")
    print(f"Wavelength: {wavelength:.4f} Å")
    print(f"Output: {final_chi_path}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
