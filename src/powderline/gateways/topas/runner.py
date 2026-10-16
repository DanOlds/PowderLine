"""Drive ``tc.exe`` (TOPAS) from Python — Windows execution, Linux-testable.

The refinement itself runs only where TOPAS is installed (Windows). This module
is the pure discovery half: where ``tc.exe`` is. :func:`native_run.run_tc` runs
it with an injectable ``runner``, so the generate → run → parse path is
exercisable on a box without TOPAS (see test_topas_runner, test_topas_native).

Where ``tc.exe`` is looked for (P-35, 2026-10-15): the explicit ``topas_dir``
argument, else ``.powderline_config.yaml`` ``topas.dir`` -- and nowhere else.
The former sweep of ``C:/TOPAS8 … 5`` and the ``C:/TOPAS<version>`` guess are
gone: TOPAS carries no readable version, so a guessed directory could silently
run a different TOPAS than the one the recipe is written for (A145). With no
directory declared a run is generate-only. No GSAS-II import.
"""

from __future__ import annotations

from pathlib import Path

import yaml

#: Same files ConfigLoader searches (config_loader.ConfigLoader.DEFAULT_CONFIG_PATHS).
_CONFIG_PATHS = (".powderline_config.yaml", "~/.powderline_config.yaml")

#: tc executable names to probe within a TOPAS directory.
_TC_NAMES = ("tc.exe", "tc")


def _read_topas_config() -> dict:
    """Return the ``topas:`` section of the first ``.powderline_config.yaml`` found.

    Quiet (unlike ``ConfigLoader``, which prints) so the CLI stays clean when no
    config is present. Raises ``ValueError`` when the file is present but
    malformed: a typo must not silently become "TOPAS not configured".
    """
    for candidate in _CONFIG_PATHS:
        path = Path(candidate).expanduser()
        if path.exists():
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError as exc:
                raise ValueError(f"{path}: not valid YAML ({exc})") from exc
            if not isinstance(data, dict):
                raise ValueError(f"{path}: the config must be a mapping, got {type(data).__name__}")
            return data.get("topas", {}) or {}
    return {}


def _tc_in(directory: Path) -> Path | None:
    for name in _TC_NAMES:
        candidate = directory / name
        if candidate.exists():
            return candidate
    return None


def _candidate_dirs(topas_dir, cfg: dict) -> list[Path]:
    """The only directories ``tc.exe`` is looked for in (P-35): the explicit one, else the configured one."""
    if topas_dir:
        return [Path(topas_dir).expanduser()]
    if cfg.get("dir"):
        return [Path(str(cfg["dir"])).expanduser()]
    return []


def discover_tc_exe(topas_dir=None, topas_version=None) -> Path | None:
    """Locate ``tc.exe`` in the declared TOPAS directory; ``None`` when none is declared or it has no ``tc``.

    ``topas_version`` is accepted for signature compatibility and does not take
    part in discovery (P-35): the version is checked against the recipe by the
    gateway (A145), never used to guess a directory.
    """
    for directory in _candidate_dirs(topas_dir, _read_topas_config()):
        tc = _tc_in(directory)
        if tc is not None:
            return tc
    return None

