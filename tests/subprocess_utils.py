"""Shared subprocess and regression-comparison helpers for tests."""

from __future__ import annotations

import importlib.util
import os
import re
import subprocess
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


# The 9-column schema of refined_parameters.csv (schema 0.25/0.26), in order.
# Asserted by test_api.py and test_example_LaB6_regression.py.
REFINED_PARAM_COLUMNS = [
    "parameter_name", "descriptive_name", "phase_name", "phase_idx",
    "atom_name", "atom_idx", "value", "esd", "category",
]


# ---------------------------------------------------------------------------
# `pixi run kicker` in the env that has GSAS-II (re/08 §3, spike P-21)
# ---------------------------------------------------------------------------

#: Override the env the GSAS-II regression tests run `kicker` in (e.g. "all").
PIXI_ENV_OVERRIDE_VAR = "POWDERLINE_TEST_PIXI_ENV"


def gsasii_pixi_env(repo_root: Path | None = None) -> str | None:
    """Name of the pixi environment to pass as ``-e`` for a GSAS-II `kicker` run.

    Since re/08 the default env has no engine, so the regression suites run
    ``pixi run -e gsasii kicker ...``. Resolution order:

    1. ``POWDERLINE_TEST_PIXI_ENV`` if set.
    2. The env this test process already runs in (``PIXI_ENVIRONMENT_NAME``)
       when GSAS-II is importable here -- ``pixi run test`` in the dev/all/gsasii
       env re-uses itself instead of needing a second installed env.
    3. ``"gsasii"`` when ``.pixi/envs/gsasii`` is installed.
    4. ``None`` (no ``-e``): TEMPORARY fallback for checkouts whose installed envs
       predate the re/08 re-lock (GSAS-II still in the default env). Remove once
       the maintainer has re-locked and every checkout has a ``gsasii`` env.
    """
    override = os.environ.get(PIXI_ENV_OVERRIDE_VAR)
    if override:
        return override
    current = os.environ.get("PIXI_ENVIRONMENT_NAME")
    if current and importlib.util.find_spec("GSASII") is not None:
        return current
    root = repo_root or Path.cwd()
    if (root / ".pixi" / "envs" / "gsasii").is_dir():
        return "gsasii"
    return None


def kicker_command(*args: str, repo_root: Path | None = None) -> list[str]:
    """``["pixi", "run", ("-e", <env>,) "kicker", "--no-server", *args]``.

    The GSAS-II CLI invocation the regression suites share; ``--no-server`` keeps
    the refinement in-process (no stale server state).
    """
    cmd = ["pixi", "run"]
    env = gsasii_pixi_env(repo_root)
    if env is not None:
        cmd += ["-e", env]
    cmd += ["kicker", "--no-server", *args]
    return cmd


def extract_rwp_from_lst(lst_file: Path) -> float:
    """
    Extract final Rwp value from GSAS-II .lst file.

    Looks for pattern: "wR = X.XXX% on NNNN observations"
    """
    content = lst_file.read_text()

    # Pattern matches: "wR = 6.50% on 3768 observations"
    pattern = r'Final refinement wR = ([\d.]+)% on \d+ observations'
    match = re.search(pattern, content)

    if not match:
        raise ValueError(f"Could not find Rwp value in {lst_file}")

    return float(match.group(1))


def run_subprocess_utf8(*popenargs: Any, **kwargs: Any) -> subprocess.CompletedProcess:
    """Run subprocesses in text mode with UTF-8 decoding.

    This avoids platform-default decoding differences (notably cp1252 on
    Windows) in tests that assert on captured stdout/stderr.
    """
    kwargs.setdefault("text", True)
    if kwargs.get("text") or kwargs.get("universal_newlines"):
        kwargs.setdefault("encoding", "utf-8")
        kwargs.setdefault("errors", "replace")
    return subprocess.run(*popenargs, **kwargs)


# Regression-table comparison helpers.
#
# Refinements are byte-deterministic per machine, but the last digits of refined
# values drift across GSAS-II/scipy/BLAS builds and OSes. Structure (shape,
# columns, ordering, labels) must match exactly; the numeric value/esd columns
# are compared within a tolerance so cross-build noise does not trip a regression
# while a genuine change (far larger) still does.

_NUMERIC_COLS = ("value", "esd")

# Correlated size/strain broadening (GSAS-II Size/Mustrain terms, emitted under
# the "other" category) is ill-conditioned: it is only reproducible to ~1% across
# builds, versus <1e-3 for every other parameter class (see
# docs/regression-tolerance.md). It therefore gets a looser rtol.
_ILL_CONDITIONED_CATEGORIES = ("other",)


def _numeric_frame_match(test_df, ref_df, *, rtol, atol, numeric_cols=_NUMERIC_COLS):
    """True if two report tables match: exact on labels, tolerant on value/esd.

    ``rtol`` may be a scalar or a per-row array (broadcast against the columns).
    """
    if test_df.shape != ref_df.shape or list(test_df.columns) != list(ref_df.columns):
        return False
    numeric = [c for c in numeric_cols if c in ref_df.columns]
    non_numeric = [c for c in ref_df.columns if c not in numeric]
    if not test_df[non_numeric].equals(ref_df[non_numeric]):
        return False
    for col in numeric:
        a = pd.to_numeric(test_df[col], errors="coerce").to_numpy(dtype=float)
        b = pd.to_numeric(ref_df[col], errors="coerce").to_numpy(dtype=float)
        tol = atol + np.asarray(rtol) * np.abs(b)
        ok = (np.abs(a - b) <= tol) | (np.isnan(a) & np.isnan(b))
        if not np.all(ok):
            return False
    return True


def refined_params_match(test_df, ref_df, rtol=1e-3, atol=1e-4, loose_rtol=1e-2):
    """Compare refined_parameters.csv tables within cross-build tolerance.

    All parameter classes use the tight default ``rtol``; only correlated
    size/strain broadening (``_ILL_CONDITIONED_CATEGORIES``) uses ``loose_rtol``,
    since it is not resolvable to better than ~1% across builds.
    """
    if "category" in ref_df.columns and test_df.shape == ref_df.shape:
        cats = ref_df["category"].astype(str).to_numpy()
        row_rtol = np.where(np.isin(cats, _ILL_CONDITIONED_CATEGORIES), loose_rtol, rtol)
    else:
        row_rtol = rtol
    return _numeric_frame_match(test_df, ref_df, rtol=row_rtol, atol=atol)


def unit_cell_match(test_df, ref_df, rtol=1e-4, atol=1e-6):
    """Compare *_unit_cell_report.csv tables within cross-build tolerance.

    rtol=1e-4 matches the pytest.approx(rel=1e-4) convention used by the
    unit-cell regression comparisons.
    """
    return _numeric_frame_match(test_df, ref_df, rtol=rtol, atol=atol)
