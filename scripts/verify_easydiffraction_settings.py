"""Verify easydiffraction's space-group settings per calculator; write ``space_group_support.py`` (A163, A170, A174).

**UNEXECUTED.** This script was written on 2026-10-12 from the devkit probes that
ran on easydiffraction 0.21.1 (cryspy 0.13.0, crysfml 0.8.0) and from the
decision rows A163/A170/A174, in a checkout without easydiffraction installed.
It has not been run. Every easydiffraction/cryspy name it uses goes through the
small wrappers in :class:`EasydiffractionApi` / :func:`_attr` / :func:`_set`, which
raise :class:`ApiDrift` naming the probe that supplied the name, so a renamed
attribute fails at one place instead of being recorded as a calculator crash.
The first thing the sweep does is compute ``P m -3 m`` and ``P 1`` with both
calculators; a failure there is fatal (environment or API drift), not a verdict.

Mapping (A105, EB-42). For every setting of gemmi's table (564), the easydiffraction
entry of the same IT number whose operator set (``SPACE_GROUPS[(number, code)]['symop']``,
read as affine operations modulo lattice translations) equals gemmi's operations;
several entries share one operator set (``1abc``/``1ba-c`` of ``C c c e``,
``b2``/``-b1`` of ``C 2``), the lowest ``setting`` index is taken. ``name_h_m`` is
cryspy's short symbol for the number (``get_name_hm_short_by_it_number``; it is
``'P 6/m '`` with a trailing space for 175 and uses the ``e`` glide: ``C m c e``).
This reproduces the 531 pairs the probes used (33 gemmi settings have no
easydiffraction entry: the ``A/B/C/F/I 1`` triclinic centrings, ``C 4 2 2``-type
non-conventional settings, ``I 1 21 1``, ``C 1 21 1``, ...).

Criteria, each calculator on its own (A170), cells generic for the crystal system
(``cell_for``; see A196):

* CrysPy, general position (A163/A170): per-reflection F² of one Fe atom at
  (0.137, 0.271, 0.389) (``experiment.refln``) against the ITC reference computed
  here from gemmi's operations of the canonical setting (IT92 form factor, Uiso
  0.005), R after a least-squares scale (the form-factor tables differ by a
  constant few percent) < 0.01. Strict and hand-sensitive: ``P 31`` stays refused
  (CrysPy computes it with P 32's operators, EB-80).
* CrysFML, general position (A170, N7): no reflection list, so its pattern against
  its own pattern of the orbit written out in ``P 1``, raw R (no scale) < 1e-3.
* Both, every special Wyckoff site of easydiffraction's table (A174): the site's
  template evaluated at generic values, pattern against the calculator's own
  ``P 1`` pattern of the orbit, raw R < 1e-3. One failing site excludes the
  setting. CrysPy fails template-form special sites in nine origin-choice-1
  settings (EB-77); they are emitted as ``CRYSPY_ORIGIN1_UNSUPPORTED`` from the
  computed exclusions, and the known nine (A174) must be a subset.
* A calculator crash (EB-84: 53 CrysPy crashes) refuses the setting for that
  calculator only (CrysFML computes ``P 6/m`` and ``F d -3 c:2``, A170).

Expected on 0.21.1 (A170, A174): CrysPy 428 (437 - 9), CrysFML 230. The counts are
printed and compared; a difference is a warning, not an error (a newer
easydiffraction may compute more), but the origin-1 subset check is an error.

Usage (PowderLine root; the ``easydiff`` environment)::

    python -I scripts/verify_easydiffraction_settings.py [--out PATH] [--jobs N] [--report PATH]
    python -I scripts/verify_easydiffraction_settings.py --check [--sample N] [--calculator cryspy|crysfml]

``--check`` re-verifies a deterministic sample (``random.Random(0)``) plus
``P 31``, ``P 6/m``, ``F d -3 c:2`` and ``F d d d:1`` against the committed module
and exits 1 on any difference (``tests/test_easydiffraction_settings_sample.py``
runs ``--check --sample 8`` where easydiffraction is installed). Runtime of a full
sweep: about an hour for the general positions (EB-80) plus the ~4000 special
sites; use ``--jobs``.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import importlib
import importlib.metadata
import importlib.util
import json
import math
import random
import re
import shlex
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Optional

import gemmi
import numpy as np

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:  # ``python -I`` ignores PYTHONPATH
    sys.path.insert(0, str(REPO / "src"))

from powderline.symmetry import orbit  # noqa: E402

DEFAULT_OUT = REPO / "src" / "powderline" / "gateways" / "easydiffraction" / "space_group_support.py"

# --- probe provenance (where each easydiffraction/cryspy name comes from) ----------
P_SETTING = "devkit probes/re06/setting_probe.py"
P_PATTERN = "devkit probes/re06/setting_pattern_probe.py"
P_SPECIAL = "devkit probes/re06_pr_review/special_sites_scan.py"
P_TEMPLATE = "devkit probes/re06_pr_review/template_rule.py"
P_SOURCE = ("easydiffraction 0.21.1 source (crystallography/crystallography.py, "
            "crystallography/space_groups.py; read, not run)")

# --- the probes' structure, grids and thresholds ---------------------------------
GEN = (0.137, 0.271, 0.389)  # general position (setting_probe.py, template_rule.py GEN)
U_ISO = 0.005
WAVELENGTH = 0.5
ELEMENT = "Fe"
F2_GRID = (5.0, 30.0, 0.005)      # setting_probe.py
PATTERN_GRID = (3.0, 30.0, 0.01)  # special_sites_scan.py, scale_only_check.py
PROFILE = {"broad_gauss_u": 0.0, "broad_gauss_v": 0.0, "broad_gauss_w": 0.002,
           "broad_lorentz_x": 0.0, "broad_lorentz_y": 0.0}  # special_sites_scan.py
PEAK_TYPE = "cwl-pseudo-voigt"
F2_R_MAX = 0.01       # representative_probe.py / template_rule.py: "wrong" above 0.01 (EB-80: passes <= 0.009)
RAW_R_MAX = 1e-3      # A174 / N7
ABSENT_REL = 1e-6     # reference reflections below this fraction of the maximum are systematic absences
CELL_ATTRS = ("length_a", "length_b", "length_c", "angle_alpha", "angle_beta", "angle_gamma")
CELLS = {"cubic": (5.1, 5.1, 5.1, 90, 90, 90), "tetragonal": (5.1, 5.1, 7.3, 90, 90, 90),
         "hexagonal": (5.1, 5.1, 7.3, 90, 90, 120), "trigonal": (5.1, 5.1, 7.3, 90, 90, 120),
         "orthorhombic": (5.1, 6.2, 7.3, 90, 90, 90), "triclinic": (5.1, 6.2, 7.3, 81, 103, 95)}
RHOMBOHEDRAL_CELL = (5.6, 5.6, 5.6, 77, 77, 77)
MONOCLINIC_ANGLES = {"a": (103, 90, 90), "b": (90, 103, 90), "c": (90, 90, 103)}

CALCULATORS = ("cryspy", "crysfml")
EXPECTED_COUNTS = {"cryspy": 428, "crysfml": 230}  # A170, A174 on easydiffraction 0.21.1
VERIFIED_ON = {"easydiffraction": "0.21.1", "cryspy": "0.13.0", "crysfml": "0.8.0"}
#: A174: CrysPy computes template-form special sites wrongly in these origin-choice-1 settings (EB-77).
KNOWN_CRYSPY_ORIGIN1 = ("F d d d:1", "I 41/a:1", "P 4/n n c:1", "P 42/n n m:1", "I 41/a m d:1",
                        "I 41/a c d:1", "F d -3:1", "P n -3 m:1", "F d -3 m:1")
#: Always re-verified by ``--check`` (A170 sample; the task adds ``F d d d:1``).
CHECK_ALWAYS = ("P 31", "P 6/m", "F d -3 c:2", "F d d d:1")
SMOKE_SETTINGS = ("P m -3 m", "P 1")


# --- API wrappers: every engine name fails loudly at one place ---------------------


class ApiDrift(AttributeError):
    """An easydiffraction/cryspy name this script relies on is missing (the API moved)."""


def _import(module: str, probe: str):
    try:
        return importlib.import_module(module)
    except ImportError as exc:
        raise ApiDrift(f"cannot import {module!r} (name from {probe}): {exc}") from exc


def _attr(obj, name: str, probe: str):
    if not hasattr(obj, name):
        raise ApiDrift(f"{type(obj).__name__!r} has no attribute {name!r} (name from {probe})")
    return getattr(obj, name)


def _set(obj, name: str, value, probe: str) -> None:
    """``setattr`` that refuses to create an attribute easydiffraction does not have."""
    if not hasattr(obj, name):
        raise ApiDrift(f"{type(obj).__name__!r} has no attribute {name!r} to set (name from {probe})")
    setattr(obj, name, value)


def _field(record: dict, key: str, probe: str):
    try:
        return record[key]
    except (KeyError, TypeError) as exc:
        raise ApiDrift(f"SPACE_GROUPS record has no field {key!r} (name from {probe})") from exc


def _version(dist: str, module: str) -> str:
    try:
        return importlib.metadata.version(dist)
    except importlib.metadata.PackageNotFoundError:
        pass
    try:
        return str(getattr(importlib.import_module(module), "__version__", "unknown"))
    except ImportError:
        return "not installed"


class EasydiffractionApi:
    """The easydiffraction and cryspy names this script uses, resolved once, with their provenance."""

    def __init__(self) -> None:
        ed = _import("easydiffraction", P_SETTING)
        self.StructureFactory = _attr(ed, "StructureFactory", P_SETTING)
        self.ExperimentFactory = _attr(ed, "ExperimentFactory", P_SETTING)
        self.Project = _attr(ed, "Project", P_SETTING)
        logging = _import("easydiffraction.utils.logging", P_SETTING)
        logger = _attr(logging, "Logger", P_SETTING)
        raise_reaction = _attr(_attr(logger, "Reaction", P_SETTING), "RAISE", P_SETTING)
        _attr(logger, "configure", P_SETTING)(reaction=raise_reaction)  # EB-48: errors raise, nothing is swallowed
        space_groups = _import("easydiffraction.crystallography.space_groups", P_SOURCE)
        table = _attr(space_groups, "SPACE_GROUPS", P_SOURCE)
        self.records = list(table.values()) if isinstance(table, dict) else list(table)
        ecr = _import("easydiffraction.crystallography.crystallography", P_SPECIAL)
        self.wyckoff_table = _attr(ecr, "space_group_wyckoff_table", P_SPECIAL)
        sg_functions = _import("cryspy.A_functions_base.function_2_space_group", P_SOURCE)
        self.short_name_by_number = _attr(sg_functions, "get_name_hm_short_by_it_number", P_SOURCE)
        self.versions = {"easydiffraction": _version("easydiffraction", "easydiffraction"),
                         "cryspy": _version("cryspy", "cryspy"),
                         "crysfml": _version("crysfml", "crysfml"),
                         "gemmi": _version("gemmi", "gemmi")}

    # --- building and computing (setting_probe.py, special_sites_scan.py) ---

    def structure(self, name: str, code: str, cell, atoms):
        s = _attr(self.StructureFactory, "from_scratch", P_SETTING)(name="t")
        group = _attr(s, "space_group", P_SETTING)
        _set(group, "name_h_m", name, P_SETTING)
        if code:
            _set(group, "coord_system_code", code, P_SETTING)
        cell_obj = _attr(s, "cell", P_SETTING)
        for key, value in zip(CELL_ATTRS, cell):
            _set(cell_obj, key, float(value), P_SETTING)
        sites = _attr(s, "atom_sites", P_SETTING)
        create = _attr(sites, "create", P_SETTING)
        for i, xyz in enumerate(atoms):
            label = f"Fe{i}"
            create(id=label, type_symbol=ELEMENT, fract_x=float(xyz[0]), fract_y=float(xyz[1]),
                   fract_z=float(xyz[2]), occupancy=1.0)
            site = sites[label]
            _set(site, "adp_type", "Uiso", P_SETTING)
            _set(site, "adp_iso", U_ISO, P_SETTING)
        return s

    def experiment(self, calculator: str, data_path: Path):
        e = _attr(self.ExperimentFactory, "from_data_path", P_SETTING)(
            name="e", data_path=str(data_path), sample_form="powder", beam_mode="constant wavelength",
            radiation_probe="xray", scattering_type="bragg")
        _set(_attr(e, "calculator", P_SETTING), "type", calculator, P_SETTING)
        peak = _attr(e, "peak", P_SETTING)
        _set(peak, "type", PEAK_TYPE, P_SETTING)
        for key, value in PROFILE.items():
            _set(peak, key, value, P_SETTING)
        _set(_attr(e, "instrument", P_SETTING), "setup_wavelength", WAVELENGTH, P_SETTING)
        _attr(_attr(e, "linked_structures", P_SETTING), "create", P_SETTING)(structure_id="t", scale=1.0)
        return e

    def calculate(self, calculator: str, name: str, code: str, cell, atoms, data_path: Path):
        """Build structure + experiment, run ``analysis.calculate()``; return the experiment."""
        s = self.structure(name, code, cell, atoms)
        e = self.experiment(calculator, data_path)
        project = self.Project()
        _attr(_attr(project, "structures", P_SETTING), "add", P_SETTING)(s)
        _attr(_attr(project, "experiments", P_SETTING), "add", P_SETTING)(e)
        _attr(_attr(project, "analysis", P_SETTING), "calculate", P_SETTING)()
        return e

    def f_squared(self, experiment) -> tuple[list[tuple[int, int, int]], np.ndarray]:
        """CrysPy's reflection list: ``[(h, k, l), ...]`` and F² (``experiment.refln``; EB-79: None for CrysFML)."""
        refln = _attr(experiment, "refln", P_SETTING)
        if refln is None:
            raise ApiDrift("experiment.refln is None; the per-reflection criterion needs CrysPy's list (EB-79)")
        h, k, l = (_attr(refln, n, P_SETTING) for n in ("index_h", "index_k", "index_l"))
        hkls = [(int(a), int(b), int(c)) for a, b, c in zip(h, k, l)]
        return hkls, np.asarray(_attr(refln, "f_squared_calc", P_SETTING), dtype=float)

    def pattern(self, experiment) -> np.ndarray:
        """The calculated pattern without background (``data.fit_data_arrays()``; setting_probe.py)."""
        arrays = _attr(_attr(experiment, "data", P_SETTING), "fit_data_arrays", P_SETTING)()
        try:
            return np.asarray(arrays["calc"], dtype=float) - np.asarray(arrays["bkg"], dtype=float)
        except (KeyError, TypeError) as exc:
            raise ApiDrift(f"fit_data_arrays() has no 'calc'/'bkg' entries (names from {P_SETTING})") from exc


# --- mapping: gemmi setting -> easydiffraction (name_h_m, coord_system_code) ------------


def op_key(op: gemmi.Op) -> tuple:
    """An affine operation modulo lattice translations, hashable."""
    return (tuple(int(v) for row in op.rot for v in row), tuple(int(t) % op.DEN for t in op.tran))


def gemmi_op_set(sg: gemmi.SpaceGroup) -> frozenset:
    return frozenset(op_key(op) for op in sg.operations())


def ed_op_set(symops: Iterable[str]) -> frozenset:
    return frozenset(op_key(gemmi.Op(str(triplet))) for triplet in symops)


@dataclass(frozen=True)
class Setting:
    canonical: str   # gemmi xhm
    number: int
    short_name: str  # easydiffraction name_h_m
    code: str        # easydiffraction coord_system_code ("" when none)


def match_record(sg: gemmi.SpaceGroup, records: list[dict]) -> Optional[dict]:
    """The easydiffraction record of ``sg``'s number with gemmi's operator set; ties -> lowest ``setting``."""
    wanted = gemmi_op_set(sg)
    candidates = [r for r in records
                  if _field(r, "IT_number", P_SOURCE) == sg.number and ed_op_set(_field(r, "symop", P_SOURCE)) == wanted]
    if not candidates:
        return None
    return min(candidates, key=lambda r: int(_field(r, "setting", P_SOURCE)))


def build_mapping(records: list[dict], short_name_by_number) -> dict[str, Setting]:
    """Every gemmi setting easydiffraction can express, keyed by canonical name (531 on 0.21.1)."""
    mapping: dict[str, Setting] = {}
    for sg in gemmi.spacegroup_table():
        record = match_record(sg, records)
        if record is None:
            continue
        name = short_name_by_number(sg.number)
        if not isinstance(name, str) or not name:
            raise ApiDrift(f"get_name_hm_short_by_it_number({sg.number}) returned {name!r} (name from {P_SOURCE})")
        code = _field(record, "IT_coordinate_system_code", P_SOURCE) or ""
        mapping[sg.xhm()] = Setting(sg.xhm(), sg.number, name, str(code))
    return mapping


# --- the ITC side (core gemmi; setting_probe.py reference_f2, template_rule.py evaluate) ---


def cell_for(sg: gemmi.SpaceGroup) -> tuple:
    """A cell generic for the crystal system (setting_probe.py ``cell_for``; A196).

    ``template_rule.cell_for`` took the most symmetric cell gemmi accepts, which is
    cubic for monoclinic, orthorhombic, tetragonal and ``:R`` groups: under such a
    metric a pattern is blind to axis permutations, so it is not used here.
    """
    system = sg.crystal_system_str()
    if system == "trigonal" and sg.ext == "R":
        return RHOMBOHEDRAL_CELL
    if system == "monoclinic":
        axis = sg.monoclinic_unique_axis()
        return (5.1, 6.2, 7.3, *MONOCLINIC_ANGLES[axis if axis in MONOCLINIC_ANGLES else "b"])
    return CELLS[system]


def evaluate_template(template: str, xyz=GEN) -> tuple[float, float, float]:
    """easydiffraction's Wyckoff template (``"(x,2x,1/4)"``) at generic values (template_rule.py)."""
    values = dict(zip("xyz", (float(v) for v in xyz)))
    out = []
    for part in template.strip().strip("()").split(","):
        expr = re.sub(r"(\d)([xyz])", r"\1*\2", part.strip())
        if not re.fullmatch(r"[xyz0-9+\-*/. ()]+", expr):
            raise ValueError(f"unexpected Wyckoff template component {part!r} in {template!r}")
        out.append(float(eval(expr, {"__builtins__": {}}, values)))  # noqa: S307 - vetted arithmetic only
    if len(out) != 3:
        raise ValueError(f"Wyckoff template {template!r} does not have three components")
    return (out[0], out[1], out[2])


def reference_f2(canonical: str, cell, xyz, hkls) -> np.ndarray:
    """|F|² per hkl for one Fe atom at ``xyz`` and its orbit under gemmi's operations (IT92, Uiso)."""
    points = np.asarray(orbit(canonical, xyz), dtype=float)
    uc = gemmi.UnitCell(*cell)
    ff = gemmi.Element(ELEMENT).it92
    out = np.empty(len(hkls))
    for i, hkl in enumerate(hkls):
        s2 = 1.0 / (4.0 * uc.calculate_d(list(hkl)) ** 2)
        f = ff.calculate_sf(s2) * math.exp(-8.0 * math.pi ** 2 * U_ISO * s2)
        phase = 2.0 * math.pi * points @ np.asarray(hkl, dtype=float)
        out[i] = abs(f * np.exp(1j * phase).sum()) ** 2
    return out


def ls_r(a: np.ndarray, b: np.ndarray) -> tuple[float, float]:
    """R of ``a`` against ``b`` after the least-squares scale ``s`` (``a ~ s b``; setting_probe.py)."""
    s = float(np.dot(a, b) / np.dot(b, b))
    return float(np.sum(np.abs(a - s * b)) / np.sum(np.abs(s * b))), s


def raw_r(a: np.ndarray, b: np.ndarray) -> float:
    """R of ``a`` against ``b`` with no scale (special_sites_scan.py; N7)."""
    return float(np.sum(np.abs(a - b)) / np.sum(np.abs(b)))


def write_grid(path: Path, grid) -> Path:
    start, stop, step = grid
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for x in np.arange(start, stop, step):
            f.write(f"{x:.4f} 1.0 1.0\n")
    return path


# --- verdicts ------------------------------------------------------------------------


@dataclass
class SiteResult:
    letter: str
    multiplicity: int
    template: str
    r: Optional[float] = None
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.r is not None and self.r < RAW_R_MAX


@dataclass
class SettingResult:
    canonical: str
    number: int
    calculator: str
    short_name: str
    code: str
    general_metric: str = ""          # "f2_ls" (CrysPy) or "pattern_raw" (CrysFML)
    general_r: Optional[float] = None
    general_scale: Optional[float] = None
    general_extra: int = 0            # reflections CrysPy lists where the reference is absent
    crash: Optional[str] = None
    sites: list = field(default_factory=list)

    @property
    def general_ok(self) -> bool:
        if self.crash is not None or self.general_r is None or self.general_extra:
            return False
        return self.general_r < (F2_R_MAX if self.general_metric == "f2_ls" else RAW_R_MAX)

    @property
    def sites_ok(self) -> bool:
        return all(s.ok for s in self.sites)

    @property
    def supported(self) -> bool:
        return self.crash is None and self.general_ok and self.sites_ok

    @property
    def origin1_special_failure(self) -> bool:
        """The A174 exclusion: an origin-choice-1 setting whose general position passes but a special site fails."""
        return self.canonical.endswith(":1") and self.general_ok and not self.sites_ok

    def summary(self) -> str:
        if self.crash is not None:
            return f"CRASH {self.crash}"
        parts = [f"general {self.general_metric} R={self.general_r:.3g}"
                 + (f" s={self.general_scale:.4f}" if self.general_scale is not None else "")
                 + (f" extra={self.general_extra}" if self.general_extra else "")]
        if self.sites:
            bad = [s for s in self.sites if not s.ok]
            parts.append(f"{len(self.sites)} special sites, {len(bad)} wrong"
                         + (": " + ", ".join(f"{s.letter} {s.template} R={s.r:.3g}" if s.error is None
                                              else f"{s.letter} {s.template} ERROR {s.error}" for s in bad[:4])
                            if bad else ""))
        return ("OK " if self.supported else "REFUSED ") + "; ".join(parts)


def _describe(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {str(exc)[:160]}"


def verify_setting(api: EasydiffractionApi, calculator: str, setting: Setting, f2_data: Path,
                   pattern_data: Path) -> SettingResult:
    """The full criterion for one setting and calculator (general position, then every special site)."""
    sg = gemmi.find_spacegroup_by_name(setting.canonical)
    cell = cell_for(sg)
    result = SettingResult(setting.canonical, setting.number, calculator, setting.short_name, setting.code)
    try:
        if calculator == "cryspy":
            experiment = api.calculate(calculator, setting.short_name, setting.code, cell, [GEN], f2_data)
            hkls, fc = api.f_squared(experiment)
            fr = reference_f2(setting.canonical, cell, GEN, hkls)
            keep = fr > ABSENT_REL * fr.max()
            result.general_metric = "f2_ls"
            result.general_r, result.general_scale = ls_r(fc[keep], fr[keep])
            # A reflection CrysPy computes where the ITC structure has none is a misread setting too.
            result.general_extra = int(np.sum(fc[~keep] > 1e-3 * fc.max()))
        else:
            y = api.pattern(api.calculate(calculator, setting.short_name, setting.code, cell, [GEN], pattern_data))
            y1 = api.pattern(api.calculate(calculator, "P 1", "", cell, orbit(setting.canonical, GEN), pattern_data))
            result.general_metric = "pattern_raw"
            result.general_r = raw_r(y, y1)
    except ApiDrift:
        raise
    except Exception as exc:  # noqa: BLE001 - a calculator crash is a verdict (EB-84)
        result.crash = _describe(exc)
        return result
    if not result.general_ok:
        return result
    table = api.wyckoff_table(setting.short_name, setting.code or None)
    if table is None:
        result.crash = "space_group_wyckoff_table returned None for a setting that computed"
        return result
    general_multiplicity = len(orbit(setting.canonical, GEN))
    for letter, info in sorted(table.items(), key=lambda kv: (int(_field(kv[1], "multiplicity", P_SPECIAL)), kv[0])):
        multiplicity = int(_field(info, "multiplicity", P_SPECIAL))
        if multiplicity >= general_multiplicity:
            continue
        template = str(_field(info, "coords_xyz", P_SPECIAL)[0])
        site = SiteResult(letter, multiplicity, template)
        try:
            xyz = evaluate_template(template)
            y = api.pattern(api.calculate(calculator, setting.short_name, setting.code, cell, [xyz], pattern_data))
            y1 = api.pattern(api.calculate(calculator, "P 1", "", cell, orbit(setting.canonical, xyz), pattern_data))
            site.r = raw_r(y, y1)
        except ApiDrift:
            raise
        except Exception as exc:  # noqa: BLE001
            site.error = _describe(exc)
        result.sites.append(site)
    return result


# --- parallel driver -------------------------------------------------------------------

_WORKER: dict = {}


def _init_worker(workdir: str) -> None:
    _WORKER["api"] = EasydiffractionApi()
    _WORKER["f2"] = Path(workdir) / "f2_grid.xye"
    _WORKER["pattern"] = Path(workdir) / "pattern_grid.xye"


def _work(item: tuple) -> SettingResult:
    calculator, setting = item
    return verify_setting(_WORKER["api"], calculator, setting, _WORKER["f2"], _WORKER["pattern"])


def verify_many(items: list[tuple], workdir: Path, jobs: int, api: Optional[EasydiffractionApi] = None,
                echo=print) -> list[SettingResult]:
    """Verify ``(calculator, Setting)`` items, ``jobs`` processes; results in a deterministic order."""
    write_grid(workdir / "f2_grid.xye", F2_GRID)
    write_grid(workdir / "pattern_grid.xye", PATTERN_GRID)
    results: list[SettingResult] = []
    if jobs <= 1:
        _init_worker(str(workdir))
        if api is not None:
            _WORKER["api"] = api
        stream = map(_work, items)
    else:
        pool = ProcessPoolExecutor(max_workers=jobs, initializer=_init_worker, initargs=(str(workdir),))
        stream = pool.map(_work, items, chunksize=1)
    for n, result in enumerate(stream, 1):
        echo(f"[{n}/{len(items)}] {result.calculator:8s} {result.canonical:16s} {result.summary()}")
        results.append(result)
    if jobs > 1:
        pool.shutdown()
    results.sort(key=lambda r: (r.calculator, r.number, r.canonical))
    return results


def smoke_test(api: EasydiffractionApi, mapping: dict[str, Setting], workdir: Path) -> None:
    """``P m -3 m`` and ``P 1`` must compute with both calculators; otherwise nothing below is a verdict."""
    f2 = write_grid(workdir / "f2_grid.xye", F2_GRID)
    pattern = write_grid(workdir / "pattern_grid.xye", PATTERN_GRID)
    for canonical in SMOKE_SETTINGS:
        setting = mapping.get(canonical)
        if setting is None:
            raise ApiDrift(f"{canonical!r} did not map to an easydiffraction setting; the operator matching is broken")
        for calculator in CALCULATORS:
            result = verify_setting(api, calculator, setting, f2, pattern)
            if result.crash is not None or not result.general_ok:
                raise SystemExit(f"smoke test failed: {calculator} {canonical}: {result.summary()}\n"
                                 "(environment or API drift, not a setting verdict)")


# --- the module ------------------------------------------------------------------------

MAP_FUNCTION = '''

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
'''


def _counts(results: list[SettingResult], calculator: str) -> dict:
    mine = [r for r in results if r.calculator == calculator]
    return {"mapped": len(mine), "supported": sum(r.supported for r in mine),
            "crashed": sum(r.crash is not None for r in mine),
            "general_failed": sum(r.crash is None and not r.general_ok for r in mine),
            "special_site_failed": sum(r.general_ok and not r.sites_ok for r in mine),
            "special_sites_checked": sum(len(r.sites) for r in mine)}


def _render_dict(name: str, entries: dict[str, Setting], comment: str) -> str:
    lines = [f"#: {comment}", f"{name} = {{"]
    for canonical, setting in sorted(entries.items(), key=lambda kv: (kv[1].number, kv[0])):
        lines.append(f'    {canonical!r}: {{"short_name": {setting.short_name!r}, '
                     f'"coord_system_code": {setting.code!r}}},')
    lines.append("}")
    return "\n".join(lines)


def render_module(supported: dict[str, dict[str, Setting]], origin1: list[str], meta: dict) -> str:
    """The text of ``space_group_support.py`` (deterministic: sorted by IT number, then name)."""
    counts = meta["counts"]
    versions = meta["versions"]
    header = f'''"""Space group support for easydiffraction calculators (A163, A170, A174).

GENERATED by scripts/verify_easydiffraction_settings.py on {meta["date"]}; do not edit by hand.
Maps canonical gemmi space group names to easydiffraction's short names + coord_system_code.

Verified with easydiffraction {versions["easydiffraction"]}, cryspy {versions["cryspy"]}, \
crysfml {versions["crysfml"]}, gemmi {versions["gemmi"]}.
Command: {meta["command"]}

Criteria (each calculator on its own, A170; see the script's docstring):
- CrysPy general position: per-reflection F² against the ITC reference (gemmi operators,
  IT92), R after a least-squares scale < {F2_R_MAX:g} (hand-sensitive; P 31 refused, EB-80);
- CrysFML general position: pattern against its own pattern of the orbit written in P 1,
  raw R < {RAW_R_MAX:g} (N7);
- both: every special Wyckoff site of easydiffraction's table, written as its template, pattern
  against the calculator's own P 1 pattern, raw R < {RAW_R_MAX:g} (A174);
- a calculator crash refuses the setting for that calculator (EB-84).

CrysPy: {counts["cryspy"]["supported"]} settings of {counts["cryspy"]["mapped"]} mapped \
({counts["cryspy"]["crashed"]} crashed, {counts["cryspy"]["general_failed"]} failed the general position, \
{counts["cryspy"]["special_site_failed"]} failed a special site)
CrysFML: {counts["crysfml"]["supported"]} settings of {counts["crysfml"]["mapped"]} mapped \
({counts["crysfml"]["crashed"]} crashed, {counts["crysfml"]["general_failed"]} failed the general position, \
{counts["crysfml"]["special_site_failed"]} failed a special site)
"""

from __future__ import annotations

#: How this table was made (versions, date, counts); ``--check`` compares the versions.
VERIFICATION = {json.dumps({"date": meta["date"], "versions": versions, "counts": counts,
                               "thresholds": {"f2_ls_r_max": F2_R_MAX, "raw_r_max": RAW_R_MAX},
                               "carried_over": meta.get("carried_over", [])}, indent=4, sort_keys=True)}

'''
    body = [_render_dict("CRYSPY_SETTINGS", supported["cryspy"],
                         "Settings the CrysPy calculator computes correctly (A163, A170, A174), by canonical gemmi name."),
            "",
            _render_dict("CRYSFML_SETTINGS", supported["crysfml"],
                         "Settings the CrysFML calculator computes correctly (A170, A174), by canonical gemmi name."),
            "",
            "#: Origin-choice-1 settings in which CrysPy computes template-form special positions",
            "#: wrongly (EB-77); refused for CrysPy (A174). The ``:2`` setting or CrysFML is the remedy.",
            "#: Computed: general position passed, a special Wyckoff site failed.",
            "CRYSPY_ORIGIN1_UNSUPPORTED = (",
            *[f"    {name!r}," for name in origin1],
            ")",
            MAP_FUNCTION.rstrip("\n") + "\n"]
    return header + "\n".join(body)


def load_module(path: Path):
    spec = importlib.util.spec_from_file_location("space_group_support_committed", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sorted_names(names: Iterable[str]) -> list[str]:
    return sorted(set(names), key=lambda n: (gemmi.find_spacegroup_by_name(n).number, n))


# --- commands -------------------------------------------------------------------------


def generate(args: argparse.Namespace) -> int:
    api = EasydiffractionApi()
    print("versions:", api.versions)
    for name, expected in VERIFIED_ON.items():
        if api.versions.get(name) != expected:
            print(f"WARNING: {name} {api.versions.get(name)} differs from {expected}, the version the criteria "
                  "and expected counts were established on (A163, A170, A174)")
    mapping = build_mapping(api.records, api.short_name_by_number)
    print(f"{len(mapping)} of {sum(1 for _ in gemmi.spacegroup_table())} gemmi settings map to easydiffraction "
          f"(531 expected on 0.21.1)")
    calculators = [args.calculator] if args.calculator else list(CALCULATORS)
    with tempfile.TemporaryDirectory(prefix="verify_easydiffraction_") as tmp:
        workdir = Path(tmp)
        smoke_test(api, mapping, workdir)
        items = [(calc, setting) for calc in calculators
                 for _, setting in sorted(mapping.items(), key=lambda kv: (kv[1].number, kv[0]))]
        results = verify_many(items, workdir, args.jobs, api=api)

    supported = {calc: {r.canonical: mapping[r.canonical] for r in results if r.calculator == calc and r.supported}
                 for calc in calculators}
    origin1 = _sorted_names(r.canonical for r in results if r.calculator == "cryspy" and r.origin1_special_failure)
    counts = {calc: _counts(results, calc) for calc in calculators}
    carried_over = []
    if args.calculator:  # keep the other calculator's committed table
        other = "crysfml" if args.calculator == "cryspy" else "cryspy"
        committed = load_module(args.out)
        table = getattr(committed, f"{other.upper()}_SETTINGS")
        supported[other] = {k: Setting(k, gemmi.find_spacegroup_by_name(k).number, v["short_name"],
                                       v["coord_system_code"]) for k, v in table.items()}
        counts[other] = dict(getattr(committed, "VERIFICATION", {}).get("counts", {}).get(other, {}) or
                             {"mapped": len(table), "supported": len(table), "crashed": -1, "general_failed": -1,
                              "special_site_failed": -1, "special_sites_checked": -1})
        if other == "cryspy":
            origin1 = list(getattr(committed, "CRYSPY_ORIGIN1_UNSUPPORTED", ()))
        carried_over.append(other)

    for calc in calculators:
        got = counts[calc]["supported"]
        note = "" if got == EXPECTED_COUNTS[calc] else f"  <-- WARNING: {EXPECTED_COUNTS[calc]} expected (A170/A174)"
        print(f"{calc}: {got} supported of {counts[calc]['mapped']} mapped; {counts[calc]['crashed']} crashed, "
              f"{counts[calc]['general_failed']} failed the general position, "
              f"{counts[calc]['special_site_failed']} failed a special site "
              f"({counts[calc]['special_sites_checked']} sites checked){note}")
    if args.report:
        Path(args.report).write_text(json.dumps([asdict(r) for r in results], indent=1), encoding="utf-8")
        print(f"per-setting report written to {args.report}")
    if "cryspy" in calculators:
        print(f"CrysPy origin-1 special-site exclusions ({len(origin1)}): {origin1}")
        missing = sorted(set(KNOWN_CRYSPY_ORIGIN1) - set(origin1))
        extra = sorted(set(origin1) - set(KNOWN_CRYSPY_ORIGIN1))
        if extra:
            print(f"NOTE: excluded beyond the nine of A174: {extra}")
        if missing:
            print(f"ERROR: the A174 origin-1 settings {missing} were NOT excluded for CrysPy; the module is not "
                  "written. If a newer easydiffraction fixed EB-77, update KNOWN_CRYSPY_ORIGIN1 and A174 first.")
            return 1

    meta = {"date": _dt.date.today().isoformat(), "versions": api.versions, "counts": counts,
            "command": "python -I scripts/verify_easydiffraction_settings.py " + " ".join(
                shlex.quote(a) for a in sys.argv[1:]), "carried_over": carried_over}
    text = render_module(supported, origin1, meta)
    Path(args.out).write_text(text, encoding="utf-8", newline="\n")
    written = load_module(Path(args.out))  # the module must import and keep its API
    for name in ("CRYSPY_SETTINGS", "CRYSFML_SETTINGS", "CRYSPY_ORIGIN1_UNSUPPORTED", "map_space_group"):
        assert hasattr(written, name), name
    print(f"written {args.out}")
    return 0


def check(args: argparse.Namespace) -> int:
    committed = load_module(Path(args.out))
    tables = {"cryspy": dict(committed.CRYSPY_SETTINGS), "crysfml": dict(committed.CRYSFML_SETTINGS)}
    if not hasattr(committed, "VERIFICATION"):
        print(f"ERROR: {args.out} is the ungenerated placeholder (no VERIFICATION); run the script without --check")
        return 1
    api = EasydiffractionApi()
    print("versions:", api.versions, "| committed:", committed.VERIFICATION.get("versions"))
    for name, version in committed.VERIFICATION.get("versions", {}).items():
        if api.versions.get(name) != version:
            print(f"WARNING: {name} {api.versions.get(name)} differs from the committed table's {version}")
    mapping = build_mapping(api.records, api.short_name_by_number)
    differences: list[str] = []
    for calc, table in tables.items():
        for canonical, entry in table.items():
            setting = mapping.get(canonical)
            if setting is None:
                differences.append(f"{calc} {canonical!r}: committed but does not map to easydiffraction now")
            elif (entry["short_name"], entry["coord_system_code"]) != (setting.short_name, setting.code):
                differences.append(f"{calc} {canonical!r}: committed ({entry['short_name']!r}, "
                                   f"{entry['coord_system_code']!r}), mapped now ({setting.short_name!r}, "
                                   f"{setting.code!r})")
    for name in committed.CRYSPY_ORIGIN1_UNSUPPORTED:
        if name in tables["cryspy"]:
            differences.append(f"{name!r} is in CRYSPY_ORIGIN1_UNSUPPORTED and in CRYSPY_SETTINGS")
    universe = sorted(mapping)
    chosen = random.Random(0).sample(universe, min(args.sample, len(universe))) if args.sample else []
    sample = _sorted_names(chosen + [n for n in CHECK_ALWAYS if n in mapping])
    calculators = [args.calculator] if args.calculator else list(CALCULATORS)
    print(f"re-verifying {len(sample)} settings x {calculators}: {sample}")
    with tempfile.TemporaryDirectory(prefix="verify_easydiffraction_") as tmp:
        workdir = Path(tmp)
        smoke_test(api, mapping, workdir)
        results = verify_many([(c, mapping[n]) for c in calculators for n in sample], workdir, args.jobs, api=api)
    for r in results:
        committed_supported = r.canonical in tables[r.calculator]
        if committed_supported != r.supported:
            differences.append(f"{r.calculator} {r.canonical!r}: committed {'supported' if committed_supported else 'refused'}, "
                               f"now {r.summary()}")
        if r.calculator == "cryspy" and r.canonical.endswith(":1"):
            listed = r.canonical in committed.CRYSPY_ORIGIN1_UNSUPPORTED
            if listed != r.origin1_special_failure:
                differences.append(f"cryspy {r.canonical!r}: CRYSPY_ORIGIN1_UNSUPPORTED says {listed}, "
                                   f"now origin1_special_failure={r.origin1_special_failure}: {r.summary()}")
    if differences:
        print(f"{len(differences)} difference(s) from the committed module:")
        for d in differences:
            print("  - " + d)
        return 1
    print("committed module agrees with the re-verification")
    return 0


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT,
                        help=f"the module to write / check (default {DEFAULT_OUT.relative_to(REPO)})")
    parser.add_argument("--check", action="store_true", help="re-verify a sample against the committed module")
    parser.add_argument("--sample", type=int, default=0, help="with --check: size of the deterministic sample")
    parser.add_argument("--calculator", choices=CALCULATORS, help="only this calculator")
    parser.add_argument("--jobs", type=int, default=1, help="worker processes (default 1)")
    parser.add_argument("--report", type=Path, help="write every per-setting/per-site result as JSON here")
    args = parser.parse_args(argv)
    if args.sample and not args.check:
        parser.error("--sample needs --check (a sample must not be written as the table)")
    return args


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    return check(args) if args.check else generate(args)


if __name__ == "__main__":
    sys.exit(main())
