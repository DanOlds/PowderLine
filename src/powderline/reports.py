"""Shared writers for PowderLine's standardized report files.

This module provides engine-free writers for the standardized report files
emitted by all PowderLine engines: ``refined_parameters.csv``,
``<phase>_unit_cell_report.csv``, ``fit_profile.txt``, and
``<phase>_peak_list_report.csv``.

The writers own file naming, column order, header, delimiter, and quoting
(csv QUOTE_MINIMAL, like pandas). All files are written with UTF-8 encoding
and LF line endings (``encoding="utf-8", newline=""`` on open with csv
``lineterminator="\\n"``), ensuring deterministic output on all platforms.

**Number formatting stays with the caller**: engines format values differently,
so this module does not impose a format. A value that is a ``str`` is written
as-is, ``None`` is written as an empty field, and anything else is converted
via ``str(value)``.

Usage example::

    from powderline.reports import write_refined_parameters, REFINED_PARAMETERS_COLUMNS

    rows = [
        {
            "parameter_name": ":0:Scale",
            "descriptive_name": "scale",
            "phase_name": "",
            "phase_idx": None,
            "atom_name": "",
            "atom_idx": None,
            "value": "1.234567e+00",
            "esd": "2.345678e-03",
            "category": "scale",
        },
    ]
    path = write_refined_parameters(output_dir, rows)
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable, Mapping, Sequence, Union

#: Column order for refined_parameters.csv (9 columns).
REFINED_PARAMETERS_COLUMNS = (
    "parameter_name",
    "descriptive_name",
    "phase_name",
    "phase_idx",
    "atom_name",
    "atom_idx",
    "value",
    "esd",
    "category",
)

#: Column order for parameter_dispositions.csv (re/20, KI-04): how each value of the output recipe came about.
DISPOSITION_COLUMNS = ("path", "value", "esd", "disposition", "at_limit")

#: Column order for <phase>_unit_cell_report.csv (3 columns).
UNIT_CELL_COLUMNS = ("parameter", "value", "esd")

#: Column order for fit_profile.txt (8 columns, tab-delimited).
FIT_PROFILE_COLUMNS = (
    "two_theta",
    "y_obs",
    "y_weights",
    "y_calc",
    "y_diff",
    "y_bkg",
    "q_values",
    "d_spacings",
)


def _write_table(
    path: Path,
    columns: Sequence[str],
    rows: Iterable[Union[Mapping[str, object], Sequence[object]]],
    delimiter: str,
) -> None:
    """Write a table to disk with the specified columns, rows, and delimiter.

    Args:
        path: Output file path.
        columns: Column names in order.
        rows: Iterable of mappings (column -> value) or sequences (in column order).
        delimiter: Field delimiter (',' or '\\t').

    Notes:
        - Values are written as-is if already strings, None becomes empty, else str(value).
        - UTF-8 encoding with LF line endings on all platforms.
        - CSV QUOTE_MINIMAL (only when necessary).
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, delimiter=delimiter, lineterminator="\n")
        writer.writerow(columns)

        for row in rows:
            if isinstance(row, Mapping):
                values = [row.get(col) for col in columns]
            else:
                values = list(row)
                if len(values) != len(columns):
                    raise ValueError(
                        f"{path.name}: row has {len(values)} values, expected "
                        f"{len(columns)} ({', '.join(columns)})"
                    )

            # Convert values: str as-is, None -> "", else str(value)
            formatted = [
                "" if v is None else (v if isinstance(v, str) else str(v))
                for v in values
            ]
            writer.writerow(formatted)


def write_refined_parameters(
    output_dir: Path,
    rows: Iterable[Union[Mapping[str, object], Sequence[object]]],
) -> Path:
    """Write refined_parameters.csv with the standardized 9-column schema.

    Args:
        output_dir: Directory to write the file (created if needed).
        rows: Iterable of mappings (column -> value) or sequences in
              REFINED_PARAMETERS_COLUMNS order.

    Returns:
        Path to the written file (output_dir / "refined_parameters.csv").
    """
    path = Path(output_dir) / "refined_parameters.csv"
    _write_table(path, REFINED_PARAMETERS_COLUMNS, rows, delimiter=",")
    return path


def write_unit_cell_report(
    output_dir: Path,
    phase_name: str,
    rows: Iterable[Union[Mapping[str, object], Sequence[object]]],
) -> Path:
    """Write <phase>_unit_cell_report.csv with the 3-column schema.

    Args:
        output_dir: Directory to write the file (created if needed).
        phase_name: Phase name (used in the filename).
        rows: Iterable of mappings (column -> value) or sequences in
              UNIT_CELL_COLUMNS order.

    Returns:
        Path to the written file (output_dir / "<phase>_unit_cell_report.csv").
    """
    path = Path(output_dir) / f"{phase_name}_unit_cell_report.csv"
    _write_table(path, UNIT_CELL_COLUMNS, rows, delimiter=",")
    return path


def write_fit_profile(
    output_dir: Path,
    rows: Iterable[Union[Mapping[str, object], Sequence[object]]],
) -> Path:
    """Write fit_profile.txt with the 8-column tab-delimited schema.

    Args:
        output_dir: Directory to write the file (created if needed).
        rows: Iterable of mappings (column -> value) or sequences in
              FIT_PROFILE_COLUMNS order.

    Returns:
        Path to the written file (output_dir / "fit_profile.txt").
    """
    path = Path(output_dir) / "fit_profile.txt"
    _write_table(path, FIT_PROFILE_COLUMNS, rows, delimiter="\t")
    return path


def write_peak_list_report(
    output_dir: Path,
    phase_name: str,
    rows: Iterable[Union[Mapping[str, object], Sequence[object]]],
    columns: Sequence[str],
) -> Path:
    """Write <phase>_peak_list_report.csv with engine-specific columns.

    Args:
        output_dir: Directory to write the file (created if needed).
        phase_name: Phase name (used in the filename).
        rows: Iterable of mappings (column -> value) or sequences in
              the columns order.
        columns: Column names in order (varies per engine).

    Returns:
        Path to the written file (output_dir / "<phase>_peak_list_report.csv").
    """
    path = Path(output_dir) / f"{phase_name}_peak_list_report.csv"
    _write_table(path, columns, rows, delimiter=",")
    return path


def write_output_recipe(output_dir: Path, recipe: Mapping[str, object]) -> Path:
    """Write ``output_recipe.json`` (re/20, KI-04): the resolved recipe of the run, UTF-8, LF, 2-space indent."""
    import json

    path = Path(output_dir) / "output_recipe.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(recipe, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    return path


def write_parameter_dispositions(output_dir: Path, rows: Iterable[Mapping[str, object]]) -> Path:
    """Write ``parameter_dispositions.csv`` with :data:`DISPOSITION_COLUMNS`."""
    path = Path(output_dir) / "parameter_dispositions.csv"
    _write_table(path, DISPOSITION_COLUMNS, rows, delimiter=",")
    return path
