"""Deprecated import path — ``powderline.run`` (:mod:`powderline.api`) dispatches on ``schema_name``.

The ``engine=`` dispatcher that lived here is gone (re/07, P-06): the engine
is named by the recipe's ``schema_name``, and ``run(engine=...)`` is accepted
only as a deprecated keyword that must agree with the recipe. This module and
the keyword are removed with the 0.26.0 schema.
"""

from __future__ import annotations

from powderline.api import run  # noqa: F401

#: The in-repo gateways (kept for the transitional tests of the ``engine=`` shim).
_ENGINES = ("gsasii", "topas", "easydiffraction")
