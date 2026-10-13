"""The easydiffraction gateway's exception type (re-exported from ``powderline.gateways.easydiffraction``).

The ``easydiffraction.rietveld`` schema refuses what the engine cannot
represent at validation (pydantic errors), so nothing raises this today; it
stays as the package's exception export (callers of the deleted translation
path caught it) and for a future translation error that is not a schema rule.
"""


class EasyDiffractionTranslationError(Exception):
    """The recipe uses a feature the easydiffraction engine cannot represent."""
