"""``space_group_support.py`` and its generator ``scripts/verify_easydiffraction_settings.py`` (A163, A170, A174).

Engine-free: the committed module's API shape, and the generator's pure parts
(mapping rule, cells, Wyckoff templates, ITC reference, metrics, module writer).
With easydiffraction installed: ``--check --sample 8`` re-verifies a deterministic
sample plus ``P 31``, ``P 6/m``, ``F d -3 c:2`` and ``F d d d:1`` against the committed
module (exit 1 on any difference).
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import gemmi
import numpy as np
import pytest

from powderline.gateways.easydiffraction import space_group_support as sgs

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "verify_easydiffraction_settings.py"
_HAS_ED = importlib.util.find_spec("easydiffraction") is not None
needs_ed = pytest.mark.skipif(not _HAS_ED, reason="easydiffraction not installed")


@pytest.fixture(scope="module")
def verify():
    spec = importlib.util.spec_from_file_location("verify_easydiffraction_settings", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve the module's postponed annotations through sys.modules
    spec.loader.exec_module(module)
    return module


# --- the committed module (engine-free) ---------------------------------------------


def test_module_api_shape():
    for table in (sgs.CRYSPY_SETTINGS, sgs.CRYSFML_SETTINGS):
        assert isinstance(table, dict) and table
        for canonical, entry in table.items():
            sg = gemmi.find_spacegroup_by_name(canonical)
            assert sg is not None and sg.xhm() == canonical, canonical  # keys are canonical gemmi names (A105)
            assert set(entry) == {"short_name", "coord_system_code"}, canonical
            assert isinstance(entry["short_name"], str) and entry["short_name"].strip()
            assert isinstance(entry["coord_system_code"], str)
    assert isinstance(sgs.CRYSPY_ORIGIN1_UNSUPPORTED, tuple)
    assert "easydiffraction" not in sys.modules or _HAS_ED


def test_origin1_unsupported_are_origin1_settings_absent_from_cryspy():
    for name in sgs.CRYSPY_ORIGIN1_UNSUPPORTED:
        assert name.endswith(":1"), name
        assert gemmi.find_spacegroup_by_name(name).xhm() == name
        assert name not in sgs.CRYSPY_SETTINGS, name
        assert name[:-2] + ":2" in gemmi.find_spacegroup_by_name(name[:-2] + ":2").xhm()  # the remedy exists (A174)


def test_map_space_group_round_trip_and_refusal():
    for calculator, table in (("cryspy", sgs.CRYSPY_SETTINGS), ("crysfml", sgs.CRYSFML_SETTINGS)):
        canonical = next(iter(table))
        assert sgs.map_space_group(canonical, calculator) == (table[canonical]["short_name"],
                                                               table[canonical]["coord_system_code"])
    with pytest.raises(ValueError, match="not supported"):
        sgs.map_space_group("R -3 m:R", "crysfml")  # EB-80: CrysFML misreads every :R setting


# --- the generator's pure parts (engine-free) ----------------------------------------


def _record(number, code, setting, hall_or_xhm):
    sg = gemmi.find_spacegroup_by_name(hall_or_xhm)
    return {"IT_number": number, "IT_coordinate_system_code": code, "setting": setting,
            "symop": [op.triplet() for op in sg.operations()]}


def test_mapping_rule_matches_operator_sets_and_prefers_the_lowest_setting(verify):
    # the shape of easydiffraction 0.21.1's SPACE_GROUPS records (crystallography/space_groups.py)
    records = [_record(221, "1", 0, "P m -3 m"),
               _record(5, "b1", 0, "C 1 2 1"), _record(5, "b2", 1, "A 1 2 1"), _record(5, "-b1", 12, "A 1 2 1"),
               _record(68, "1abc", 0, "C c c a:1"), _record(68, "1ba-c", 2, "C c c b:1"),
               _record(68, "2abc", 1, "C c c a:2"), _record(68, "2ba-c", 3, "C c c b:2"),
               _record(1, None, 0, "P 1")]
    names = {221: "P m -3 m", 5: "C 2", 68: "C c c e", 1: "P 1"}
    mapping = verify.build_mapping(records, lambda n: names[n])
    assert mapping["P m -3 m"].short_name == "P m -3 m" and mapping["P m -3 m"].code == "1"
    assert (mapping["C 1 2 1"].code, mapping["A 1 2 1"].code) == ("b1", "b2")   # -b1 has the same operators, higher setting
    assert (mapping["C c c a:1"].code, mapping["C c c b:1"].code) == ("1abc", "1abc")  # origin 1: one operator set
    assert (mapping["C c c a:2"].code, mapping["C c c b:2"].code) == ("2abc", "2ba-c")
    assert mapping["P 1"].code == "" and "A 1" not in mapping and "F m -3 m" not in mapping


def test_cells_are_generic_for_the_system_and_monoclinic_axis(verify):
    assert verify.cell_for(gemmi.SpaceGroup("P 1 1 2")) == (5.1, 6.2, 7.3, 90, 90, 103)
    assert verify.cell_for(gemmi.SpaceGroup("P 2 1 1")) == (5.1, 6.2, 7.3, 103, 90, 90)
    assert verify.cell_for(gemmi.SpaceGroup("P 1 2/m 1")) == (5.1, 6.2, 7.3, 90, 103, 90)
    assert verify.cell_for(gemmi.SpaceGroup("R -3 m:R")) == verify.RHOMBOHEDRAL_CELL
    assert verify.cell_for(gemmi.SpaceGroup("P 4/n:1")) == (5.1, 5.1, 7.3, 90, 90, 90)  # not the cubic metric (A196)
    for sg in gemmi.spacegroup_table():
        cell = gemmi.UnitCell(*verify.cell_for(sg))
        assert cell.is_compatible_with_spacegroup(sg, 1e-6), sg.xhm()


@pytest.mark.parametrize("template, expected", [
    ("(x,1/2,1/2)", (0.137, 0.5, 0.5)),
    ("(x,2x,1/4)", (0.137, 0.274, 0.25)),
    ("(x,-x,z)", (0.137, -0.137, 0.389)),
    ("(x,x+1/2,z)", (0.137, 0.637, 0.389)),
    ("(0,0,0)", (0.0, 0.0, 0.0)),
])
def test_evaluate_template(verify, template, expected):
    assert verify.evaluate_template(template) == pytest.approx(expected)


def test_evaluate_template_refuses_non_arithmetic(verify):
    with pytest.raises(ValueError):
        verify.evaluate_template("(x,__import__('os'),z)")


def test_reference_f2_is_the_orbit_sum(verify):
    hkls = [(1, 0, 0), (1, 1, 0), (2, 0, 0)]
    one = verify.reference_f2("P m -3 m", (5.1, 5.1, 5.1, 90, 90, 90), (0.0, 0.0, 0.0), hkls)
    assert np.all(one > 0) and one[0] > one[2]  # f(s) falls with s
    # two atoms (orbit of 1a in P -1 is one point; a general position gives two) interfere:
    pair = verify.reference_f2("P -1", (5.1, 6.2, 7.3, 81, 103, 95), (0.25, 0.0, 0.0), [(2, 0, 0), (1, 0, 0)])
    assert pair[1] < 1e-6 * pair[0]  # cos(2 pi 0.25) terms cancel for h = 1


def test_metrics(verify):
    b = np.array([1.0, 2.0, 3.0])
    r, s = verify.ls_r(1.03 * b, b)
    assert r == pytest.approx(0.0, abs=1e-12) and s == pytest.approx(1.03)
    assert verify.raw_r(1.03 * b, b) == pytest.approx(0.03)


def test_render_module_keeps_the_public_api(verify, tmp_path):
    S = verify.Setting
    supported = {"cryspy": {"P m -3 m": S("P m -3 m", 221, "P m -3 m", "1"), "P 1": S("P 1", 1, "P 1", "")},
                 "crysfml": {"P m -3 m": S("P m -3 m", 221, "P m -3 m", "1")}}
    counts = {c: {"mapped": 2, "supported": 1, "crashed": 0, "general_failed": 0, "special_site_failed": 1,
                  "special_sites_checked": 5} for c in ("cryspy", "crysfml")}
    text = verify.render_module(supported, ["F d -3 m:1"], {"date": "2026-10-12", "versions": {
        "easydiffraction": "x", "cryspy": "y", "crysfml": "z", "gemmi": "g"}, "counts": counts, "command": "cmd"})
    out = tmp_path / "space_group_support.py"
    out.write_text(text, encoding="utf-8")
    module = verify.load_module(out)
    assert list(module.CRYSPY_SETTINGS) == ["P 1", "P m -3 m"]  # sorted by IT number
    assert module.CRYSPY_SETTINGS["P m -3 m"] == {"short_name": "P m -3 m", "coord_system_code": "1"}
    assert module.CRYSPY_ORIGIN1_UNSUPPORTED == ("F d -3 m:1",)
    assert module.map_space_group("P m -3 m", "crysfml") == ("P m -3 m", "1")
    with pytest.raises(ValueError, match="not supported"):
        module.map_space_group("P 1", "crysfml")
    assert module.VERIFICATION["counts"]["cryspy"]["supported"] == 1
    assert "GENERATED" in module.__doc__ and "UNEXECUTED" not in module.__doc__
    assert text == verify.render_module(supported, ["F d -3 m:1"], {"date": "2026-10-12", "versions": {
        "easydiffraction": "x", "cryspy": "y", "crysfml": "z", "gemmi": "g"}, "counts": counts, "command": "cmd"})


def test_check_refuses_the_placeholder_or_a_sample_without_check(verify, tmp_path):
    with pytest.raises(SystemExit):
        verify.parse_args(["--sample", "8"])
    placeholder = tmp_path / "placeholder.py"
    placeholder.write_text("CRYSPY_SETTINGS = {}\nCRYSFML_SETTINGS = {}\nCRYSPY_ORIGIN1_UNSUPPORTED = ()\n",
                           encoding="utf-8")
    assert verify.check(verify.parse_args(["--check", "--out", str(placeholder)])) == 1


# --- with easydiffraction: the sample re-verification -----------------------------------


@needs_ed
def test_check_sample_8_agrees_with_the_committed_module():
    proc = subprocess.run([sys.executable, "-I", str(SCRIPT), "--check", "--sample", "8"],
                          capture_output=True, text=True, cwd=REPO, timeout=3600)
    assert proc.returncode == 0, proc.stdout[-4000:] + proc.stderr[-4000:]
