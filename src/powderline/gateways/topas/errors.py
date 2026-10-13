"""The TOPAS gateway's exception type (re-exported from ``powderline.gateways.topas``).

The ``topas.*`` schema refuses what TOPAS cannot express at validation
(pydantic errors), so nothing raises this today; it stays as the package's
exception export (callers of the deleted translation path caught it) and for
a future translation error that is not a schema rule.
"""

from __future__ import annotations


class TopasTranslationError(ValueError):
    """A recipe cannot be faithfully translated to a TOPAS INP."""
