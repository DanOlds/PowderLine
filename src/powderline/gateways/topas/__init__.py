"""TOPAS gateway for PowderLine (imports **zero** GSAS-II).

The native ``topas.*`` path (:mod:`.schema`, :mod:`.native_writer`,
:mod:`.native_run`, :mod:`.peak_list`, :mod:`.conversions`, :mod:`.runner`,
:mod:`.gateway`) must run on Windows, where GSAS-II does not install cleanly.
Importing this package loads none of the 0.26.0 translation modules
(``writer``, ``roundtrip``, ``engine``, ``kicker``); they are imported by
name where the legacy branch of :mod:`.gateway` still runs (re/07 B).

Citation notation used throughout this subpackage: ``findings §X`` / ``plan §Y``
/ ``D-numbers`` cite the internal TOPAS design record (empirical findings and
decision log kept with the maintainers' development notes); they label *why* a
formula or behavior is the way it is and appear verbatim in some emitted INP
headers, so they are stable identifiers - do not renumber them.
"""

from __future__ import annotations

from .errors import TopasTranslationError

__all__ = ["TopasTranslationError"]
