"""Space group support for easydiffraction calculators (A163, A170, A174).

PLACEHOLDER, NOT YET GENERATED. The full table is written by
``scripts/verify_easydiffraction_settings.py`` (run in the ``easydiff`` environment:
``python -I scripts/verify_easydiffraction_settings.py``), which replaces this file
wholesale, keeps this module's API (``CRYSPY_SETTINGS``, ``CRYSFML_SETTINGS``,
``CRYSPY_ORIGIN1_UNSUPPORTED``, ``map_space_group``) and adds a ``VERIFICATION``
record (versions, date, counts) that its ``--check`` mode and
``tests/test_easydiffraction_settings_sample.py`` read. The script has not been run
in this checkout (easydiffraction is not installed here); until it is, this module
holds only the settings the native-builder fixtures use. Maps canonical gemmi space
group names to easydiffraction's short names + coord_system_code.

Expected from the generator on easydiffraction 0.21.1 (A170, A174):
CrysPy: 428 settings (437 - 9 origin-1 failures, A174)
CrysFML: 230 settings
"""

from __future__ import annotations

# Placeholder until scripts/verify_easydiffraction_settings.py is run (see the docstring).
# Minimal mappings for testing (the settings the native-builder fixtures use; the
# generated table replaces this dict). ``short_name`` is what easydiffraction's
# ``space_group.name_h_m`` accepts: its short *spaced* symbol ("P m -3 m", "C 2/m";
# EB-42; cryspy's table, ``'P 6/m '`` with a trailing space for 175), with the
# setting in ``coord_system_code``.

CRYSPY_SETTINGS = {
    "P m -3 m": {"short_name": "P m -3 m", "coord_system_code": ""},
    "F m -3 m": {"short_name": "F m -3 m", "coord_system_code": ""},
    "C 1 2/m 1": {"short_name": "C 2/m", "coord_system_code": "b1"},
    "P 63/m m c": {"short_name": "P 63/m m c", "coord_system_code": ""},
    "R -3 c:H": {"short_name": "R -3 c", "coord_system_code": "h"},
    "P -1": {"short_name": "P -1", "coord_system_code": ""},
    # ... full 428 settings added in commit 6.2
}

CRYSFML_SETTINGS = {
    "P m -3 m": {"short_name": "P m -3 m", "coord_system_code": ""},
    "F m -3 m": {"short_name": "F m -3 m", "coord_system_code": ""},
    "C 1 2/m 1": {"short_name": "C 2/m", "coord_system_code": "b1"},
    "P 63/m m c": {"short_name": "P 63/m m c", "coord_system_code": ""},
    "R -3 c:H": {"short_name": "R -3 c", "coord_system_code": "h"},
    "P -1": {"short_name": "P -1", "coord_system_code": ""},
    # ... full 230 settings added in commit 6.2
}

#: Origin-choice-1 settings in which CrysPy computes template-form special positions
#: wrongly (EB-77); refused for CrysPy (A174). The ``:2`` setting or CrysFML is the remedy.
CRYSPY_ORIGIN1_UNSUPPORTED = (
    "F d d d:1",
    "I 41/a:1",
    "P 4/n n c:1",
    "P 42/n n m:1",
    "I 41/a m d:1",
    "I 41/a c d:1",
    "F d -3:1",
    "P n -3 m:1",
    "F d -3 m:1",
)


def map_space_group(canonical: str, calculator: str) -> tuple[str, str]:
    """Map canonical name → easydiffraction (short_name, coord_system_code).

    Args:
        canonical: Canonical gemmi space group name (e.g., "P m -3 m")
        calculator: "cryspy" or "crysfml"

    Returns:
        (short_name, coord_system_code) tuple

    Raises:
        ValueError: If space group not supported for this calculator
    """
    settings = CRYSPY_SETTINGS if calculator == "cryspy" else CRYSFML_SETTINGS

    if canonical not in settings:
        raise ValueError(
            f"Space group {canonical!r} not supported by {calculator}. "
            f"See space_group_support.py for supported settings."
        )

    mapping = settings[canonical]
    return mapping["short_name"], mapping["coord_system_code"]
