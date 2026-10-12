"""GSAS-II-free easydiffraction engine for ``powderline.run(engine="easydiffraction")``.

Translates unmodified GSASII_Rietveld recipes into easydiffraction
(https://github.com/easyscience/diffraction-lib) refinements. Requires the
optional ``easydiff`` pixi environment (Python >=3.12 + easydiffraction).
"""

from .engine import run_easydiffraction_recipe
from .errors import EasyDiffractionTranslationError

__all__ = ["run_easydiffraction_recipe", "EasyDiffractionTranslationError"]
