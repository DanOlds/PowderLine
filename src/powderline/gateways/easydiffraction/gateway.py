"""Engine-free factory for the easydiffraction gateway (entry point
``powderline.gateways:easydiffraction``).

- **Schema layer** — ``validate`` / ``capabilities``: builds the ``RecipeModel``
  then runs the policy pre-flight, exactly the check ``validate_only`` runs today
  (raises ``EasyDiffractionTranslationError``; no easydiffraction import).
- **Runtime layer** — ``run``: a thin wrapper over
  ``powderline.gateways.easydiffraction.engine.run_easydiffraction_recipe`` (behavior unchanged).
  On a real run it first reads the installed easydiffraction version: missing ⇒
  ``EngineNotAvailableError``; outside ``ENGINE_VERSION_SPEC`` ⇒
  ``EngineVersionError``.
"""

from __future__ import annotations

from powderline.exceptions import EngineNotAvailableError
from powderline.registry import Gateway, check_engine_version
from powderline.schema import RecipeModel

NAME = "easydiffraction"
#: Supported easydiffraction versions (A41/A44); mirrors the pixi pin.
ENGINE_VERSION_SPEC = ">=0.20.1,<0.21"
#: Workflows served (0.26.0 schema name until re/06; no SPF — A13.3).
WORKFLOWS = ("GSASII_Rietveld",)
#: pixi environment providing easydiffraction + how to install it (renamed in re/08, A42).
ENV = "easydiff"
INSTALL_COMMAND = "pixi install -e easydiff"


def capabilities() -> dict:
    return {"name": NAME, "workflows": WORKFLOWS, "engine_version_spec": ENGINE_VERSION_SPEC}


def validate(recipe, *, verbose: bool = False):
    """Validate a recipe (dict or ``RecipeModel``); return the ``RecipeModel`` (A50, A59).

    ``verbose`` is part of the uniform gateway contract; this gateway prints
    nothing extra today.
    """
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
    """Run through ``run_easydiffraction_recipe``; see its docstring for the result dict.

    ``allow_unsupported_engine_version`` is the test-only bypass of the engine
    version check (A53); the check is skipped for ``validate_only``.
    """
    if not validate_only:
        check_engine_version(gateway=NAME, engine="easydiffraction",
                             installed=installed_engine_version(), spec=ENGINE_VERSION_SPEC,
                             allow_unsupported=allow_unsupported_engine_version)
    # Bind the module (not the function) so tests can monkeypatch
    # ``run_easydiffraction_recipe`` on it.
    from powderline.gateways.easydiffraction import engine as _easydiff_engine

    return _easydiff_engine.run_easydiffraction_recipe(recipe, output_dir, verbose=verbose,
                                                       validate_only=validate_only)


def get_gateway() -> Gateway:
    return Gateway(name=NAME, capabilities=capabilities, validate=validate, run=run)
