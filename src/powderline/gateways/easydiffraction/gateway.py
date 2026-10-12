"""Engine-free factory for the easydiffraction gateway (entry point
``powderline.gateways:easydiffraction``).

- **Schema layer** — ``validate`` / ``capabilities``: an ``easydiffraction.*``
  recipe validates against the native schema (:mod:`.schema`, re/06); a 0.26.0
  ``GSASII_Rietveld`` recipe builds the ``RecipeModel`` then runs the policy
  pre-flight, exactly the check ``validate_only`` runs today (raises
  ``EasyDiffractionTranslationError``), until re/07. Neither imports
  easydiffraction (enforced by an import-block test).
- **Runtime layer** — ``run``: a native recipe runs through
  :func:`.native_run.run_refinement`; a 0.26.0 recipe through
  ``powderline.gateways.easydiffraction.engine.run_easydiffraction_recipe``
  (behavior unchanged). On a real run it first reads the installed
  easydiffraction version: missing ⇒ ``EngineNotAvailableError``; outside
  ``ENGINE_VERSION_SPEC`` ⇒ ``EngineVersionError``.
"""

from __future__ import annotations

from powderline.exceptions import EngineNotAvailableError
from powderline.gateways.easydiffraction import schema
from powderline.registry import Gateway, check_engine_version
from powderline.schema import RecipeModel

NAME = "easydiffraction"
#: Supported easydiffraction versions (A41/A44); mirrors the pixi pin.
ENGINE_VERSION_SPEC = ">=0.21.1,<0.22"
#: Workflows served: the native schema, and the 0.26.0 name until re/07 removes it (no SPF — A13.3).
WORKFLOWS = (*schema.SCHEMA_NAMES, "GSASII_Rietveld")
#: pixi environment providing easydiffraction + how to install it (renamed in re/08, A42).
ENV = "easydiff"
INSTALL_COMMAND = "pixi install -e easydiff"


def capabilities() -> dict:
    return {"name": NAME, "workflows": WORKFLOWS, "engine_version_spec": ENGINE_VERSION_SPEC,
            **schema.DECLARATIONS}


def validate(recipe, *, verbose: bool = False):
    """Validate a recipe (dict or model); return the validated model (A50, A59).

    An ``easydiffraction.*`` recipe gives its native model
    (``EasydiffractionRecipe``); a 0.26.0 one the legacy ``RecipeModel``.
    ``verbose`` is part of the uniform gateway contract; this gateway prints
    nothing extra today.
    """
    if schema.is_native_recipe(recipe):
        return schema.validate_recipe(recipe)
    from powderline.gateways.easydiffraction.policy import check_unsupported

    model = recipe if isinstance(recipe, RecipeModel) else RecipeModel.model_validate(recipe)
    check_unsupported(model.model_dump())  # may raise EasyDiffractionTranslationError
    return model


def installed_engine_version() -> str:
    """The installed easydiffraction version (distribution == runtime there)."""
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("easydiffraction")
    except PackageNotFoundError as exc:
        raise EngineNotAvailableError(gateway=NAME, dependency="easydiffraction", env=ENV,
                                      install_command=INSTALL_COMMAND) from exc


def run(recipe, output_dir, *, verbose: bool = False, validate_only: bool = False,
        allow_unsupported_engine_version: bool = False) -> dict:
    """Run a recipe; see :func:`.native_run.run_refinement` (native) or ``run_easydiffraction_recipe`` (0.26.0).

    ``allow_unsupported_engine_version`` is the test-only bypass of the engine
    version check (A53); the check is skipped for ``validate_only`` (A52).
    """
    if not validate_only:
        check_engine_version(gateway=NAME, engine="easydiffraction",
                             installed=installed_engine_version(), spec=ENGINE_VERSION_SPEC,
                             allow_unsupported=allow_unsupported_engine_version)
    if schema.is_native_recipe(recipe):
        from powderline.gateways.easydiffraction import native_run

        model = schema.validate_recipe(recipe)  # the model validated from the user's recipe (A115)
        if validate_only:
            return native_run.run_native_validate(model)
        return native_run.run_refinement(model, output_dir, verbose=verbose)
    # Bind the module (not the function) so tests can monkeypatch
    # ``run_easydiffraction_recipe`` on it.
    from powderline.gateways.easydiffraction import engine as _easydiff_engine

    return _easydiff_engine.run_easydiffraction_recipe(recipe, output_dir, verbose=verbose,
                                                       validate_only=validate_only)


def get_gateway() -> Gateway:
    return Gateway(name=NAME, capabilities=capabilities, validate=validate, run=run)
