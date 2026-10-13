"""GSAS-II gateway — route a validated recipe to its executor (re/04, A39).

A native ``gsasii.rietveld`` / ``gsasii.spf`` model runs through
``executors.run_refinement``, which consumes the model directly; a 0.26.0
``GSASII_*`` ``RecipeModel`` runs through the transient verbatim copy in
``_legacy_executor`` until re/07 removes the 0.26.0 schema.
The gateway's ``run`` (``execution_mode='subprocess'``), the GSAS-II server and
the client's in-process fallback all call this function.
"""

from __future__ import annotations

from pathlib import Path

from powderline.gateways.gsasii.schema import is_native_recipe, validate_recipe


def run_refinement(recipe, output_dir: Path, verbose: bool = False, method: str = 'server') -> dict:
    """Run a validated recipe in this process; see ``executors.run_refinement`` for the result dict."""
    if is_native_recipe(recipe):
        from powderline.gateways.gsasii.executors import run_refinement as run
    else:
        from powderline.gateways.gsasii._legacy_executor import run_refinement as run
    return run(recipe, output_dir, verbose=verbose, method=method)


def model_from_request(recipe_dict: dict):
    """The validated model for a recipe dict received by the server or the in-process fallback.

    A native recipe validates against its schema; a 0.26.0 one as before
    (``RecipeModel.model_validate``, without ``validate()``'s simulation check).
    Raises pydantic's ``ValidationError``.
    """
    if is_native_recipe(recipe_dict):
        return validate_recipe(recipe_dict)
    from powderline.schema import RecipeModel
    return RecipeModel.model_validate(recipe_dict)
