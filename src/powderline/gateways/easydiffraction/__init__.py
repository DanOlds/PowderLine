"""easydiffraction gateway for PowderLine (imports **zero** GSAS-II).

The native ``easydiffraction.*`` path (:mod:`.schema`, :mod:`.native_builder`,
:mod:`.native_run`, :mod:`.space_group_support`, :mod:`.gateway`) targets
easydiffraction (https://github.com/easyscience/diffraction-lib) and needs the
optional ``easydiff`` pixi environment (Python >=3.12 + easydiffraction).
Importing this package loads none of the 0.26.0 translation modules
(``engine``, ``builder``, ``policy``, ``conversions``); the legacy branch of
:mod:`.gateway` imports ``engine`` by name where it runs (re/07 B).
"""

from .errors import EasyDiffractionTranslationError

__all__ = ["EasyDiffractionTranslationError"]
