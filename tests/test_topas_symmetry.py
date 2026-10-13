"""Tests for powderline.symmetry (plan §5 targets + error cases)."""

from __future__ import annotations

import pytest

from powderline import symmetry as sym
from powderline.exceptions import PowderLineError, SymmetryError

pytestmark = pytest.mark.core


# --- (a) cell rules ---------------------------------------------------------


def test_cubic_cell_rules():
    r = sym.cell_constraints("P m -3 m")
    assert r.crystal_system == "cubic"
    assert r.length_groups == (("a", "b", "c"),)
    assert r.fixed_angles == ("alpha", "beta", "gamma")
    assert r.free_angles == ()
    assert r.unique_axis is None


def test_cubic_accepts_spaceless_symbol():
    assert sym.cell_constraints("Pm-3m").length_groups == (("a", "b", "c"),)
    assert sym.cell_constraints("Fm-3m").length_groups == (("a", "b", "c"),)


def test_monoclinic_unique_b_cell_rules():
    r = sym.cell_constraints("C 1 2/m 1")
    assert r.crystal_system == "monoclinic"
    assert r.length_groups == (("a",), ("b",), ("c",))
    assert r.unique_axis == "b"
    assert r.free_angles == ("beta",)
    assert set(r.fixed_angles) == {"alpha", "gamma"}


def test_monoclinic_c2overm_shorthand():
    assert sym.cell_constraints("C2/m").unique_axis == "b"


def test_monoclinic_unique_a_and_c():
    assert sym.cell_constraints("P 2/m 1 1").unique_axis == "a"
    assert sym.cell_constraints("P 2/m 1 1").free_angles == ("alpha",)
    assert sym.cell_constraints("P 1 1 2/m").unique_axis == "c"
    assert sym.cell_constraints("P 1 1 2/m").free_angles == ("gamma",)


# Mirror-only groups (Pm, Pc, Cm, Cc: no proper 2-fold, the axis is the mirror
# normal) and 2-fold groups, in all three unique-axis settings and several cell
# choices (A102: these four groups raised "could not determine the monoclinic
# unique axis" before).
@pytest.mark.parametrize("space_group, axis", [
    ("P 1 m 1", "b"), ("P 1 1 m", "c"), ("P m 1 1", "a"),
    ("P 1 c 1", "b"), ("P 1 n 1", "b"), ("P 1 1 a", "c"), ("P 1 1 n", "c"),
    ("P b 1 1", "a"), ("P c 1 1", "a"),
    ("C 1 m 1", "b"), ("A 1 1 m", "c"), ("B m 1 1", "a"), ("I 1 m 1", "b"),
    ("C 1 c 1", "b"), ("I 1 a 1", "b"), ("A 1 1 a", "c"), ("I 1 1 b", "c"),
    ("B b 1 1", "a"), ("C c 1 1", "a"),
    ("P 1 2 1", "b"), ("P 1 1 2", "c"), ("P 2 1 1", "a"),
    ("P 1 21/c 1", "b"), ("P 1 1 21/a", "c"), ("P 21/b 1 1", "a"),
    ("C 1 2/c 1", "b"), ("I 1 2/a 1", "b"), ("A 1 1 2/a", "c"), ("B 2/b 1 1", "a"),
])
def test_monoclinic_unique_axis_every_setting(space_group, axis):
    r = sym.cell_constraints(space_group)
    free_angle = {"a": "alpha", "b": "beta", "c": "gamma"}[axis]
    assert r.crystal_system == "monoclinic"
    assert r.unique_axis == axis
    assert r.length_groups == (("a",), ("b",), ("c",))
    assert r.free_angles == (free_angle,)
    assert r.fixed_angles == tuple(a for a in ("alpha", "beta", "gamma") if a != free_angle)


def test_monoclinic_unique_axis_all_gemmi_settings():
    """Every monoclinic setting in gemmi's table: the axis is the H-M symbol's non-'1' slot."""
    import gemmi

    checked = 0
    for sg in gemmi.spacegroup_table():
        if sg.crystal_system_str() != "monoclinic":
            continue
        slots = sg.hm.split()[1:]
        expected = "abc"[[i for i, s in enumerate(slots) if s != "1"][0]]
        assert sym.cell_constraints(sg.xhm()).unique_axis == expected, sg.xhm()
        checked += 1
    assert checked == 115


def test_tetragonal_hexagonal_ortho_triclinic():
    assert sym.cell_constraints("P 4/m m m").length_groups == (("a", "b"), ("c",))
    hexr = sym.cell_constraints("P 6/m m m")
    assert hexr.length_groups == (("a", "b"), ("c",))
    assert hexr.fixed_angles == ("alpha", "beta", "gamma")
    orth = sym.cell_constraints("P m m m")
    assert orth.length_groups == (("a",), ("b",), ("c",))
    tri = sym.cell_constraints("P -1")
    assert tri.free_angles == ("alpha", "beta", "gamma")
    assert tri.fixed_angles == ()


def test_trigonal_hex_setting_is_hexagonal_cell():
    r = sym.cell_constraints("R -3 m")  # defaults to :H
    assert r.length_groups == (("a", "b"), ("c",))
    assert r.fixed_angles == ("alpha", "beta", "gamma")


# --- error cases ------------------------------------------------------------


def test_unknown_symbol_errors():
    with pytest.raises(SymmetryError, match="unrecognized"):
        sym.cell_constraints("NotASpaceGroup")


def test_rhombohedral_R_setting_accepted_by_core():
    # A62: core resolves both rhombohedral settings; engines that cannot handle
    # :R (TOPAS, easydiffraction) reject it in their own layer.
    assert sym.resolve_space_group("R -3 m :R").ext == "R"
    assert sym.site_dof("R -3 m :R", (0.0, 0.0, 0.0)).orbit_size == 1
    assert sym.site_dof("R -3 m :H", (0.0, 0.0, 0.0)).orbit_size == 3


def test_rhombohedral_R_cell_rules_not_implemented():
    # alpha=beta=gamma ties are not expressible as CellRules: refuse, don't guess.
    with pytest.raises(SymmetryError, match="not implemented"):
        sym.cell_constraints("R -3 m :R")


def test_symmetry_error_is_a_core_value_error():
    # A61: a ValueError (pydantic reports it with the field location inside a
    # validator) and a PowderLineError; no gateway error type involved.
    with pytest.raises(SymmetryError) as info:
        sym.resolve_space_group("NotASpaceGroup")
    assert isinstance(info.value, ValueError)
    assert isinstance(info.value, PowderLineError)
    assert type(info.value).__module__ == "powderline.exceptions"


def test_two_origin_without_selector_errors():
    with pytest.raises(SymmetryError, match="two origin"):
        sym.cell_constraints("F d -3 m")
    # explicit origin is accepted
    assert sym.cell_constraints("F d -3 m:2").crystal_system == "cubic"


# --- (b) site DOF -----------------------------------------------------------


def test_pm3m_origin_all_fixed_orbit1():
    d = sym.site_dof("P m -3 m", (0.0, 0.0, 0.0))
    assert d.axes == ("FIXED", "FIXED", "FIXED")
    assert d.orbit_size == 1
    assert d.coupled_axes == ()


def test_pm3m_6f_site_z_free_orbit6():
    d = sym.site_dof("P m -3 m", (0.5, 0.5, 0.2021))
    assert d.axes == ("FIXED", "FIXED", "FREE")
    assert d.orbit_size == 6
    assert d.is_free("z") and not d.is_free("x")


def test_fm3m_origin_all_fixed_orbit4():
    d = sym.site_dof("F m -3 m", (0.0, 0.0, 0.0))
    assert d.axes == ("FIXED", "FIXED", "FIXED")
    assert d.orbit_size == 4  # matches DRX_33 recipe Multiplicity


def test_c2m_general_position_all_free_orbit8():
    d = sym.site_dof("C 1 2/m 1", (0.238, 0.343, 0.234))
    assert d.axes == ("FREE", "FREE", "FREE")
    assert d.orbit_size == 8


def test_c2m_special_y_free_xz_fixed():
    # DRX_33 Li1-type (0, 0.33, 0.5) and shared Li2/Mg site (0, 0.167, 0)
    for pos in [(0.0, 0.33, 0.5), (0.0, 0.167, 0.0)]:
        d = sym.site_dof("C 1 2/m 1", pos)
        assert d.axes == ("FIXED", "FREE", "FIXED")
        assert d.orbit_size == 4  # real multiplicity (recipe stores 1 -> writer warns)


def test_coupled_site_detected():
    # P4/mmm (x, x, z): a diagonal mirror couples x and y -> COUPLED
    d = sym.site_dof("P 4/m m m", (0.3, 0.3, 0.2))
    assert d.axes == ("COUPLED", "COUPLED", "FREE")
    assert set(d.coupled_axes) == {"x", "y"}


def test_site_dof_rejects_bad_shape():
    with pytest.raises(ValueError):
        sym.site_dof("P m -3 m", (0.0, 0.0))


# --- anisotropic-ADP DOF (adp_dof) ------------------------------------------


def test_adp_dof_fm3m_origin_isotropic():
    # Cubic m-3m site: u11=u22=u33 coupled (isotropic), off-diagonals fixed to 0.
    d = sym.adp_dof("F m -3 m", (0.0, 0.0, 0.0))
    assert d.classification("u11") == "COUPLED"
    assert d.classification("u22") == "COUPLED"
    assert d.classification("u33") == "COUPLED"
    assert d.classification("u12") == "FIXED"
    assert d.classification("u13") == "FIXED"
    assert d.classification("u23") == "FIXED"
    assert d.free() == ()  # no independently free component


def test_adp_dof_c2m_general_all_free():
    # C2/m general position: all six u_ij independently refinable.
    d = sym.adp_dof("C 1 2/m 1", (0.238, 0.343, 0.234))
    assert d.components == ("FREE",) * 6
    assert set(d.free()) == {"u11", "u22", "u33", "u12", "u13", "u23"}


def test_adp_dof_c2m_unique_b_off_diagonals_fixed():
    # C2/m with 2-fold along b at a special site: u12 and u23 forbidden (=0),
    # u11,u22,u33,u13 free.
    d = sym.adp_dof("C 1 2/m 1", (0.0, 0.33, 0.5))
    assert d.classification("u12") == "FIXED"
    assert d.classification("u23") == "FIXED"
    assert set(d.free()) == {"u11", "u22", "u33", "u13"}


def test_adp_dof_rejects_bad_shape():
    with pytest.raises(ValueError):
        sym.adp_dof("P m -3 m", (0.0, 0.0))


# --- defensive raise paths (unreachable with real gemmi groups; KI-13) -------


class _UnknownSystem:
    """Stand-in space group whose crystal system no rule handles."""

    def crystal_system_str(self):
        return "not-a-system"


def test_unsupported_crystal_system_raises_symmetry_error(monkeypatch):
    monkeypatch.setattr(sym, "resolve_space_group", lambda _sg: _UnknownSystem())
    with pytest.raises(SymmetryError, match="unsupported crystal system"):
        sym.cell_constraints("P1")


def test_empty_stabilizer_raises_symmetry_error(monkeypatch):
    import numpy as np

    # A single translation-only op fixes no point, so the stabiliser is empty.
    monkeypatch.setattr(sym, "_expanded_ops", lambda _sg: [(np.eye(3), np.full(3, 0.5))])
    with pytest.raises(SymmetryError, match="empty stabilizer"):
        sym.site_dof("P1", [0.1, 0.2, 0.3])


def test_monoclinic_unique_axis_failure_raises_symmetry_error():
    import numpy as np

    with pytest.raises(SymmetryError, match="monoclinic unique axis"):
        sym._monoclinic_unique_axis([(np.eye(3), np.zeros(3))])
