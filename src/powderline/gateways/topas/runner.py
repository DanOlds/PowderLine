"""Drive ``tc.exe`` (TOPAS) from Python — Windows execution, Linux-testable.

The refinement itself runs only where TOPAS is installed (Windows). This module
keeps discovery pure and the subprocess call injectable so the whole pipeline is
exercisable on a box without TOPAS: point discovery at a directory containing a
dummy ``tc.exe``, pass a fake ``runner`` that drops the expected output files,
and the CLI's generate → run → parse path runs end to end (see test_topas_runner).

Discovery precedence (D-runner, 2026-07-27): explicit ``topas_dir`` argument →
``.powderline_config.yaml`` (``topas.dir`` / ``topas.version``) → a version's
default install dir → the default Windows dirs (newest first). No GSAS-II import.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import yaml

#: Default Windows TOPAS install directories, newest first.
_DEFAULT_TOPAS_DIRS = ("C:/TOPAS8", "C:/TOPAS7", "C:/TOPAS6", "C:/TOPAS5")

#: Same files ConfigLoader searches (config_loader.ConfigLoader.DEFAULT_CONFIG_PATHS).
_CONFIG_PATHS = (".powderline_config.yaml", "~/.powderline_config.yaml")

#: tc executable names to probe within a TOPAS directory.
_TC_NAMES = ("tc.exe", "tc")


@dataclass
class RunResult:
    """Outcome of a (possibly skipped) TOPAS run.

    ``status`` is ``"ran"`` (rc 0), ``"failed"`` (tc ran, rc != 0), or
    ``"tc_not_found"`` (no executable discovered — the caller degrades to
    generate-only, exit 0).
    """

    status: str
    returncode: int | None
    tc_path: Path | None
    command: list[str]


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


def discover_tc_exe(topas_dir=None, topas_version=None) -> Path | None:
    """Locate a ``tc`` executable, or ``None`` if TOPAS is not installed here."""
    cfg = _read_topas_config()
    if topas_version is None:
        topas_version = cfg.get("version")

    candidates: list[Path] = []
    if topas_dir:
        candidates.append(Path(topas_dir).expanduser())
    if not topas_dir and cfg.get("dir"):
        candidates.append(Path(str(cfg["dir"])).expanduser())
    if topas_version is not None:
        candidates.append(Path(f"C:/TOPAS{topas_version}"))
    candidates.extend(Path(d) for d in _DEFAULT_TOPAS_DIRS)

    for directory in candidates:
        tc = _tc_in(directory)
        if tc is not None:
            return tc
    return None


def run_topas(inp_path, topas_dir=None, topas_version=None, runner=subprocess.run) -> RunResult:
    """Run ``tc "<inp_path>"`` with ``cwd`` set to the TOPAS directory.

    Mirrors the maintainer's proven invocation (full INP path; TOPAS dir as the
    working directory) but uses ``cwd=`` instead of ``os.chdir`` — no global cwd
    mutation, exception-safe, thread-safe. ``runner`` (default ``subprocess.run``)
    is injected for testing. Returns a :class:`RunResult`; never raises on a
    missing tc (that is ``tc_not_found``).
    """
    inp_path = Path(inp_path).resolve()
    tc = discover_tc_exe(topas_dir, topas_version)
    if tc is None:
        return RunResult(status="tc_not_found", returncode=None, tc_path=None, command=[])

    command = [str(tc), str(inp_path)]
    completed = runner(command, cwd=str(tc.parent))
    returncode = int(getattr(completed, "returncode", 0) or 0)
    status = "ran" if returncode == 0 else "failed"
    return RunResult(status=status, returncode=returncode, tc_path=tc, command=command)
