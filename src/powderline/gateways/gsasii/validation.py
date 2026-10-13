"""Engine-free recipe validation for the GSAS-II gateway.

``validate`` and ``validate_simulation_mode_parameters`` moved here verbatim from
``powderline.kicker`` (multi-engine refactor, re/02, A51) so the gateway's schema
layer can validate a recipe without GSAS-II installed. The gateway's ``validate``
is this ``validate``; the public ``powderline.validate`` (re/07, P-06) dispatches
to it for ``gsasii.*`` and 0.26.0 recipes and wraps the model in a
``ValidationResult``. Both names go with the 0.26.0 schema (the native rule is in
the schema, A88).

This module must never import GSAS-II (enforced by an import-block test).
"""
from __future__ import annotations
from typing import List
from pydantic import ValidationError
from powderline.schema import RecipeModel
from powderline._status import CHECK, CROSS, WARN


def validate_simulation_mode_parameters(recipe: RecipeModel, verbose: bool = False) -> tuple[bool, List[str]]:
    """
    Validate that simulation mode examples have all refinement parameters locked.

    Simulation mode (refinement_cycles=1 with all parameters fixed) must have deterministic
    output. This function checks for any `refine_flag: true` in structural or background
    parameters that would violate determinism.

    Args:
        recipe: Validated RecipeModel instance (uses payload structure)
        verbose: If True, print detailed parameter checks

    Returns:
        (is_valid, warnings): is_valid is True if all parameters are properly locked,
                             warnings is a list of issues found (empty if valid).

    Examples:
        >>> is_valid, warnings = validate_simulation_mode_parameters(recipe)
        >>> if not is_valid:
        ...     for warning in warnings:
        ...         print(f"WARNING: {warning}")
    """
    is_valid = True
    warnings: List[str] = []

    # Get refinement controls (in payload)
    controls = recipe.payload.refinement_controls

    # Only check simulation mode (refinement_cycles == 1)
    if controls.refinement_cycles != 1:
        return True, []  # Not simulation mode, skip validation

    if verbose:
        print("Validating simulation mode constraints...")

    # Check background parameters
    if recipe.payload.background is not None:
        if recipe.payload.background.chebyshev is not None:
            if recipe.payload.background.chebyshev.refine_flag:
                msg = "CRITICAL: Chebyshev background has refine_flag=true in simulation mode (refinement_cycles=1). " \
                      "This violates determinism - all parameters must be locked. Set refine_flag=false."
                warnings.append(msg)
                is_valid = False
                if verbose:
                    print(f"  {CROSS} {msg}")

        if recipe.payload.background.single_peaks is not None:
            # Check single peak parameters
            param_lists = [
                (recipe.payload.background.single_peaks.positions, "single_peaks.positions"),
                (recipe.payload.background.single_peaks.intensities, "single_peaks.intensities"),
                (recipe.payload.background.single_peaks.pv_gaussian_sigma, "single_peaks.pv_gaussian_sigma"),
                (recipe.payload.background.single_peaks.pv_lorentzian_gamma, "single_peaks.pv_lorentzian_gamma"),
            ]
            for param_list, param_name in param_lists:
                if param_list is not None:
                    for i, param in enumerate(param_list):
                        if param[1]:  # refine_flag is second element
                            msg = f"WARNING: {param_name}[{i}] has refine_flag=true in simulation mode. " \
                                  "Set refine_flag=false for deterministic output."
                            warnings.append(msg)
                            is_valid = False
                            if verbose:
                                print(f"  {WARN}  {msg}")

    # Check instrument parameterization
    if recipe.payload.instrument.parameterization is not None:
        param_dict = recipe.payload.instrument.parameterization.model_dump(mode='json')

        # Check wavelength
        if param_dict.get('wavelength') and param_dict['wavelength'][1]:
            msg = "WARNING: instrument.parameterization.wavelength has refine_flag=true in simulation mode"
            warnings.append(msg)
            is_valid = False
            if verbose:
                print(f"  {WARN}  {msg}")

        # Check broadening parameters
        if param_dict.get('broadening'):
            for param_key, param_value in param_dict['broadening'].items():
                if param_value and param_value[1]:  # refine_flag
                    msg = f"WARNING: instrument.parameterization.broadening.{param_key} has refine_flag=true in simulation mode"
                    warnings.append(msg)
                    is_valid = False
                    if verbose:
                        print(f"  {WARN}  {msg}")

        # Check corrections
        if param_dict.get('corrections'):
            for param_key, param_value in param_dict['corrections'].items():
                if param_value and param_value[1]:  # refine_flag
                    msg = f"WARNING: instrument.parameterization.corrections.{param_key} has refine_flag=true in simulation mode"
                    warnings.append(msg)
                    is_valid = False
                    if verbose:
                        print(f"  {WARN}  {msg}")

    # Check phase-level parameters (only if phases exist)
    if recipe.payload.phases is not None:
        phases_dict = recipe.payload.model_dump(mode='json')['phases']
        for phase_name, phase_info in phases_dict.items():
            param_dict = phase_info.get('parameterization', {})

            # Check scale
            if param_dict.get('scale') and param_dict['scale'][1]:
                msg = f"WARNING: phases.{phase_name}.parameterization.scale has refine_flag=true in simulation mode"
                warnings.append(msg)
                is_valid = False
                if verbose:
                    print(f"  {WARN}  {msg}")

            # Check unit cell
            unit_cell = param_dict.get('unit_cell', {})
            if unit_cell:
                for cell_param, param_value in unit_cell.items():
                    if param_value and param_value[1]:  # refine_flag
                        msg = f"WARNING: phases.{phase_name}.parameterization.unit_cell.{cell_param} has refine_flag=true in simulation mode"
                        warnings.append(msg)
                        is_valid = False
                        if verbose:
                            print(f"  {WARN}  {msg}")

            # Check peak broadening
            peak_broadening = param_dict.get('peak_broadening', {})
            if peak_broadening:
                # Check size broadening
                if peak_broadening.get('size_broadening'):
                    for param_key, param_value in peak_broadening['size_broadening'].items():
                        if param_key == 'model':  # Skip model field (string literal, not [value, refine_flag])
                            continue
                        if param_value and param_value[1]:  # refine_flag
                            msg = f"WARNING: phases.{phase_name}.parameterization.peak_broadening.size_broadening.{param_key} has refine_flag=true"
                            warnings.append(msg)
                            is_valid = False
                            if verbose:
                                print(f"  {WARN}  {msg}")

                # Check strain broadening
                if peak_broadening.get('strain_broadening'):
                    for param_key, param_value in peak_broadening['strain_broadening'].items():
                        if param_key == 'model':  # Skip model field (string literal, not [value, refine_flag])
                            continue
                        if param_value and param_value[1]:  # refine_flag
                            msg = f"WARNING: phases.{phase_name}.parameterization.peak_broadening.strain_broadening.{param_key} has refine_flag=true"
                            warnings.append(msg)
                            is_valid = False
                            if verbose:
                                print(f"  {WARN}  {msg}")

            # Check atom parameters (using same logic as has_active_refinement_parameter for consistency)
            atoms = param_dict.get('atoms', {})
            if atoms:
                for atom_label, atom_params in atoms.items():
                    for param_key, param_value in atom_params.items():
                        # Skip ADP field (it's a string, not a RefinementParameter)
                        if param_key == 'ADP':
                            continue

                        # Handle Uaniso nested dict
                        if param_key == 'Uaniso' and isinstance(param_value, dict):
                            for uaniso_key, uaniso_value in param_value.items():
                                if isinstance(uaniso_value, list) and len(uaniso_value) >= 2 and uaniso_value[1]:
                                    msg = f"WARNING: phases.{phase_name}.parameterization.atoms.{atom_label}.Uaniso.{uaniso_key} has refine_flag=true in simulation mode"
                                    warnings.append(msg)
                                    is_valid = False
                                    if verbose:
                                        print(f"  {WARN}  {msg}")

                        # Check regular RefinementParameter lists (use elif to avoid checking lists after Uaniso)
                        elif isinstance(param_value, list) and len(param_value) >= 2 and param_value[1]:
                            msg = f"WARNING: phases.{phase_name}.parameterization.atoms.{atom_label}.{param_key} has refine_flag=true in simulation mode"
                            warnings.append(msg)
                            is_valid = False
                            if verbose:
                                print(f"  {WARN}  {msg}")

    if verbose:
        if is_valid:
            print(f"{CHECK} All parameters properly locked for simulation mode determinism")

    return is_valid, warnings


def validate(recipe: RecipeModel | dict, verbose: bool = False) -> RecipeModel:
    """Validate a PowderLine recipe without running a refinement.

    Performs full Pydantic schema validation and simulation-mode consistency
    checks. Raises ``pydantic.ValidationError`` immediately on any schema
    violation so callers receive structured, actionable error information.

    This function is useful when batch-creating recipes and you want to
    confirm each one is valid before committing to a refinement run.

    Note: The ``is_template_file()`` guard is **not** applied here. That
    check is CLI-only (guards against accidentally running bare template
    files from disk). When calling ``validate()`` programmatically the
    caller is responsible for ensuring the recipe contains real data.

    Args:
        recipe: A fully-populated ``RecipeModel`` instance or equivalent
            dict. XRD data must already be present in
            ``recipe.payload.xrd_data``.
        verbose: If True, print simulation-mode warnings to stdout.

    Returns:
        Validated ``RecipeModel`` instance; for a native ``gsasii.rietveld`` /
        ``gsasii.spf`` recipe, its ``GsasiiRietveldRecipe`` / ``GsasiiSpfRecipe``.

    Raises:
        pydantic.ValidationError: If the recipe fails schema validation.
        ValueError: If simulation-mode constraints are violated (all
            ``refine_flag`` fields must be ``false`` when
            ``refinement_cycles == 1``).

    Example::

        import json
        import powderline

        recipe_dict = json.load(open("my_recipe.json"))
        recipe_model = powderline.validate(recipe_dict)
        print(f"Schema: {recipe_model.schema_name}, phases: {len(recipe_model.payload.phases or [])}")
    """
    from pydantic import ValidationError  # already imported at module level, re-stated for clarity

    # A native ``gsasii.*`` recipe validates against its own schema (re/04, A39);
    # its simulation rule is part of that schema (A88). 0.26.0 recipes below.
    from powderline.gateways.gsasii.schema import is_native_recipe, validate_recipe
    if is_native_recipe(recipe):
        return validate_recipe(recipe)

    if not isinstance(recipe, RecipeModel):
        recipe = RecipeModel.model_validate(recipe)  # raises ValidationError on failure

    is_sim_valid, sim_warnings = validate_simulation_mode_parameters(recipe, verbose=verbose)
    if not is_sim_valid:
        raise ValueError(
            "Simulation mode validation failed. All refine_flag fields must be false "
            f"when refinement_cycles == 1.\nConstraints violated:\n"
            + "\n".join(f"  - {w}" for w in sim_warnings)
        )

    return recipe
