"""The public API: ``validate`` and ``run`` dispatch on the recipe's ``schema_name`` (re/07, P-06).

A recipe names its engine in ``schema_name`` (``<engine>.<workflow>``, e.g.
``gsasii.rietveld``); the engine prefix is the gateway's registry name
(:mod:`powderline.registry`). Both functions resolve the gateway from the
recipe and hand the recipe to it:

- :func:`validate` uses the gateway's **schema layer** only, so it works in an
  environment with no engine installed (the re/08 precondition). It returns a
  :class:`ValidationResult`: the gateway's validated model plus the structured
  warnings the validation produced.
- :func:`run` uses the gateway's **runtime layer**; engine options are
  forwarded to the gateway unchanged.

This module imports no engine and no gateway module at import time (enforced
by an import-block test).

**Transitional (removed with the 0.26.0 schema):** the legacy names
``GSASII_Rietveld`` / ``GSASII_SPF`` resolve to the ``gsasii`` gateway, and
``run`` still accepts ``engine=`` as a deprecated keyword that must agree with
the recipe.
"""

from __future__ import annotations

import warnings as _warnings
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError
from pydantic_core import InitErrorDetails, PydanticCustomError

from powderline import registry
from powderline.exceptions import StructuredWarning
from powderline.schema_core import CoreRecipe, collect_warnings

#: 0.26.0 schema names and the gateway that serves them (transitional, removed with ``powderline.schema``).
LEGACY_SCHEMA_GATEWAYS = {"GSASII_Rietveld": "gsasii", "GSASII_SPF": "gsasii"}

#: Gateway ``run()`` options the public ``run()`` refuses to forward (A53: test-only).
_NOT_PUBLIC_RUN_OPTIONS = ("allow_unsupported_engine_version",)


@dataclass(frozen=True)
class ValidationResult:
    """What :func:`validate` returns: the validated recipe and its warnings.

    - ``recipe``: the gateway's validated model (e.g. ``GsasiiRietveldRecipe``,
      ``TopasRietveldRecipe``; a 0.26.0 recipe gives the legacy ``RecipeModel``
      until that schema is removed). Validation never changes a recipe (A115),
      so the model is the recipe as written plus the schema's defaults.
    - ``warnings``: the structured warnings (:class:`~powderline.exceptions.StructuredWarning`
      dicts: ``code``, ``message``, ``field_path``) collected from that model
      once, in the same call -- a default applied (A107, A119), a symmetry
      remark (``Phase.warnings()``, A118). Empty for a legacy recipe.

    Why a result object rather than the bare model: the warnings are a product
    of validating the user's recipe (what the recipe left out), which a dump of
    the validated model (which now states the default) no longer shows; so the
    only way to expose them without re-validating is to return them with the
    model. Frozen, because it is a record of one validation, not a mutable
    recipe builder: edit the recipe dict and validate again.
    """

    recipe: Any
    warnings: list[StructuredWarning] = field(default_factory=list)


def _schema_name_error(recipe, error_type: str, message: str | None = None) -> ValidationError:
    name = recipe.get("schema_name") if isinstance(recipe, dict) else getattr(recipe, "schema_name", None)
    if message is None:
        detail = InitErrorDetails(type=error_type, loc=("schema_name",), input=recipe)
    else:
        detail = InitErrorDetails(type=PydanticCustomError(error_type, message, {"name": name}),
                                  loc=("schema_name",), input=name)
    return ValidationError.from_exception_data("Recipe", [detail])


def schema_name_of(recipe) -> str:
    """The ``schema_name`` of a recipe dict or validated model.

    Raises pydantic's ``ValidationError`` (at ``schema_name``) when it is missing
    or not a string, so a caller sees the same error type as for any other
    invalid recipe.
    """
    if isinstance(recipe, dict):
        name = recipe.get("schema_name")
        if name is None:
            raise _schema_name_error(recipe, "missing")
    else:
        name = getattr(recipe, "schema_name", None)
        if name is None:
            raise _schema_name_error(recipe, "missing")
    if not isinstance(name, str):
        raise _schema_name_error(recipe, "string_type")
    return name


def gateway_name_for(schema_name: str) -> str:
    """The registry name of the gateway that serves ``schema_name``.

    ``<engine>.<workflow>`` gives ``<engine>`` (whether or not such a gateway is
    installed: :func:`powderline.registry.get` reports that). The 0.26.0 names
    (``GSASII_Rietveld``, ``GSASII_SPF``) give ``gsasii``. Anything else raises
    pydantic's ``ValidationError`` at ``schema_name``.
    """
    if schema_name in LEGACY_SCHEMA_GATEWAYS:
        return LEGACY_SCHEMA_GATEWAYS[schema_name]
    engine, sep, workflow = schema_name.partition(".")
    if not sep or not engine or not workflow:
        raise _schema_name_error(
            {"schema_name": schema_name}, "schema_name",
            "schema_name '{name}' does not name an engine: expected '<engine>.<workflow>', e.g. 'gsasii.rietveld'")
    return engine


def gateway_for(recipe) -> registry.Gateway:
    """The gateway serving ``recipe`` (dict or model); loads only its engine-free factory."""
    return registry.get(gateway_name_for(schema_name_of(recipe)))


def validate(recipe, *, verbose: bool = False) -> ValidationResult:
    """Validate a recipe with the gateway its ``schema_name`` names; return a :class:`ValidationResult`.

    Engine-free: only the gateway's schema layer runs, so this works where no
    engine is installed. A recipe that is already a validated model passes
    through (idempotent, A50). Nothing is written and nothing is printed unless
    ``verbose`` (forwarded to the gateway).

    Args:
        recipe: a recipe dict (as loaded by :func:`powderline.load_recipe_asset`)
            or a validated model.
        verbose: print the gateway's validation progress, where it has any.

    Returns:
        ``ValidationResult(recipe=<validated model>, warnings=[...])``.

    Raises:
        pydantic.ValidationError: the recipe is invalid (every problem listed
            with its field location); also when ``schema_name`` is missing or
            malformed.
        GatewayNotInstalledError: no gateway of the name ``schema_name`` starts
            with is installed.
        ValueError: a 0.26.0 recipe violates a check outside the schema (the
            simulation-mode rule), as before.
    """
    model = gateway_for(recipe).validate(recipe, verbose=verbose)
    # Native models subclass CoreRecipe and carry warnings(); a legacy RecipeModel has none.
    warnings = collect_warnings(model) if isinstance(model, CoreRecipe) else []
    return ValidationResult(recipe=model, warnings=warnings)
