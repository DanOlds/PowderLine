"""Native easydiffraction builder (re/06 Phase 3): mapping, data, site check, refinement flags.

The pure parts run in the core-only environment; tests that build an
easydiffraction ``Project`` are skipped where easydiffraction is not installed.
"""

from __future__ import annotations

import copy
import importlib.util

import numpy as np
import pytest

from powderline.gateways.easydiffraction import native_builder as nb
from powderline.gateways.easydiffraction.errors import EasyDiffractionTranslationError
from powderline.gateways.easydiffraction.schema import EasydiffractionRecipe
from powderline.schema_core import fit_limits_on_data

_HAS_ED = importlib.util.find_spec("easydiffraction") is not None
needs_ed = pytest.mark.skipif(not _HAS_ED, reason="easydiffraction not installed")


@pytest.fixture(autouse=True)
def _declare_schema_versions(monkeypatch):
    """Until the gateway adoption commit adds ``schema.DECLARATIONS`` to ``gateway.capabilities()``,
    a full native recipe cannot pass its version check (A65); declare them here so the
    builder tests validate complete recipes."""
    from powderline.gateways.easydiffraction import gateway, schema

    if "supported_engine_schemas" not in gateway.capabilities():
        base = gateway.capabilities
        monkeypatch.setattr(gateway, "capabilities", lambda: {**base(), **schema.DECLARATIONS})


def P(v, f=False, lo=None, hi=None):
    return [v, f, lo, hi]


def _lab6_phase(scale_flag=False):
    return {
        "space_group": "P m -3 m",
        "unit_cell": {"a": P(4.1568), "b": P(4.1568), "c": P(4.1568),
                      "alpha": P(90.0), "beta": P(90.0), "gamma": P(90.0)},
        "atoms": {
            "La1": {"element": "La", "x": P(0.0), "y": P(0.0), "z": P(0.0), "occupancy": P(1.0),
                    "ADP": "Uiso", "Uiso": P(0.005)},
            "B1": {"element": "B", "x": P(0.1997), "y": P(0.5), "z": P(0.5), "occupancy": P(1.0),
                   "ADP": "Uiso", "Uiso": P(0.006)},
        },
        "scale": P(1.0, scale_flag, 0.0, None),
        "background": {"num_coefficients": 2, "coefficients": [100.0, 0.0], "refine_flag": False},
    }


def _recipe(**phases):
    """A small native recipe: 551 points 5..60 deg, window [10, 55], LaB6 unless phases are given."""
    tth = np.linspace(5.0, 60.0, 551)
    itth = 100.0 + 50.0 * np.exp(-((tth - 30.0) ** 2))
    weights = np.full(551, 0.01)
    weights[20] = 0.0  # an excluded point inside the data span
    return {
        "core_schema_version": "1.0.0", "engine_schema_version": "1.0.0",
        "schema_name": "easydiffraction.rietveld",
        "payload": {
            "xrd_data": {"tth": tth.tolist(), "Itth": itth.tolist(), "Itth_weights": weights.tolist()},
            "fit_range": [10.0, 55.0],
            "phases": phases or {"LaB6": _lab6_phase()},
            "instrument": {
                "radiation": {"wavelength": P(1.5406)},
                "corrections": {"zero": P(0.0), "polarization": P(0.5)},
                "broadening": {"u": P(0.01), "v": P(-0.001), "w": P(0.002), "x": P(0.01), "y": P(0.0)},
            },
            "refinement_controls": {"calculator": "cryspy", "peak_type": "pseudo-voigt"},
        },
    }


def _validated(recipe=None, **phases) -> EasydiffractionRecipe:
    return EasydiffractionRecipe.model_validate(recipe if recipe is not None else _recipe(**phases))


# ---------------------------------------------------------------------------
# 3.1 space-group mapping, names, data
# ---------------------------------------------------------------------------


def test_resolve_space_group_maps_to_easydiffraction_symbol_and_code():
    assert nb.resolve_space_group("P m -3 m", "cryspy") == ("P m -3 m", "")
    assert nb.resolve_space_group("C 1 2/m 1", "crysfml") == ("C 2/m", "b1")
    assert nb.resolve_space_group("R -3 c:H", "cryspy") == ("R -3 c", "h")


def test_resolve_space_group_unsupported_names_calculator_and_remedy():
    with pytest.raises(EasyDiffractionTranslationError) as exc:
        nb.resolve_space_group("P 1 21/c 1", "crysfml")
    message = str(exc.value)
    assert "'crysfml'" in message and "'P 1 21/c 1'" in message
    assert "calculator 'cryspy'" in message
    # an origin-choice-1 setting refused for CrysPy suggests the :2 origin (A174)
    with pytest.raises(EasyDiffractionTranslationError) as exc:
        nb.resolve_space_group("F d -3 m:1", "cryspy")
    assert "'F d -3 m:2'" in str(exc.value) and "'crysfml'" in str(exc.value)


def test_datablock_name_is_phase_name_lowercased():
    assert nb.datablock_name("LaB6") == "lab6"
    assert nb.datablock_name("Al2O3_corundum") == "al2o3_corundum"


def test_window_mask_is_window_and_positive_weight():
    tth = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    w = np.array([4.0, 0.0, 4.0, 16.0, 4.0])
    mask = nb.window_mask(tth, w, 2.0, 4.0)
    assert mask.tolist() == [False, False, True, True, False]  # zero-weight point inside the window is out


def test_prepare_data_crops_to_the_points_core_scores():
    model = _validated()
    payload = model.payload
    tth, itth, sigma, mask = nb.prepare_data(payload)
    lo, hi = fit_limits_on_data(payload.fit_range, payload.xrd_data)
    all_tth = np.asarray(payload.xrd_data.tth)
    expected = (all_tth >= lo) & (all_tth <= hi) & (np.asarray(payload.xrd_data.Itth_weights) > 0)
    assert mask.tolist() == expected.tolist()
    assert len(mask) == len(all_tth)                      # mask indexes the full pattern
    assert len(tth) == len(itth) == len(sigma) == mask.sum()  # only the window's weighted points are handed (A158)
    assert mask[20] is np.False_
    assert tth.tolist() == all_tth[mask].tolist()
    assert sigma == pytest.approx(10.0)
    assert not hasattr(nb, "SIGMA_WEIGHTLESS")


@pytest.mark.parametrize("value", [12.345678901234567, 5e-05, 1e-7, 0.1 + 0.2, 123456789012345.67, 1.0, 0.0])
def test_cif_number_round_trips_full_precision_without_exponent(value):
    text = nb.cif_number(value)
    assert float(text) == value
    assert "e" not in text.lower()  # EB-89: the loop reader cannot parse exponents


def test_data_cif_has_experiment_type_and_full_precision_loop():
    tth = np.array([2.0 + 1.234567890123e-7, 2.5])
    cif = nb.data_cif(tth, np.array([10.0, 5e-5]), np.array([1.0, 0.5]))
    lines = cif.splitlines()
    assert lines[0] == "data_powderline"
    assert "_experiment_type.beam_mode \"constant wavelength\"" in lines
    assert lines[-2:] == [f"{float(tth[0])!r} 10 1", "2.5 0.00005 0.5"]
    assert float(lines[-2].split()[0]) == tth[0]


def test_background_phase_single_or_none_or_error():
    model = _validated()
    assert nb.background_phase(model.payload) == "LaB6"
    two = _recipe()
    two["payload"]["phases"]["Second"] = copy.deepcopy(two["payload"]["phases"]["LaB6"])
    with pytest.raises(EasyDiffractionTranslationError, match="one background per experiment"):
        nb.background_phase(_validated(two).payload)
    two["payload"]["phases"]["Second"].pop("background")
    assert nb.background_phase(_validated(two).payload) == "LaB6"


def test_atom_order_hands_anisotropic_atoms_first():
    recipe = _recipe()
    atoms = recipe["payload"]["phases"]["LaB6"]["atoms"]
    atoms["B1"] = {**atoms["B1"], "ADP": "Uaniso",
                   "Uaniso": {"U11": P(0.006), "U22": P(0.008), "U33": P(0.008),
                              "U12": P(0.0), "U13": P(0.0), "U23": P(0.0)}}
    atoms["B1"].pop("Uiso")
    phase = _validated(recipe).payload.phases["LaB6"]
    assert [label for label, _ in nb.atom_order(phase)] == ["B1", "La1"]


def test_module_imports_no_engine():
    import sys
    assert "easydiffraction" not in sys.modules or _HAS_ED  # the import is lazy
    assert "GSASII" not in sys.modules or "powderline.kicker" in sys.modules


@needs_ed
def test_build_project_values_only(tmp_path):
    model = _validated()
    result = nb.build_project(model, tmp_path)
    assert result.datablock_names == {"LaB6": "lab6"}
    assert (tmp_path / nb.DATA_CIF_NAME).exists()
    structure = result.project.structures["lab6"]
    assert structure.cell.length_a.value == pytest.approx(4.1568)
    assert structure.cell.length_b.value == pytest.approx(4.1568)
    assert structure.atom_sites["B1"].fract_x.value == pytest.approx(0.1997)
    assert structure.atom_sites["B1"].adp_iso.value == pytest.approx(0.006)
    expt = result.project.experiments["powderline"]
    assert len(expt.data.x) == 551
    assert expt.calculator.type == "cryspy"
    assert expt.peak.type == "cwl-pseudo-voigt"
    assert expt.instrument.setup_wavelength.value == pytest.approx(1.5406)
    assert result.window_mask.sum() == int(((np.asarray(model.payload.xrd_data.tth) >= 10.0)
                                            & (np.asarray(model.payload.xrd_data.tth) <= 55.0)).sum()) - 1


# ---------------------------------------------------------------------------
# 3.2 site check and special positions (pure, with a duck-typed structure)
# ---------------------------------------------------------------------------


class _Value:
    def __init__(self, value):
        self.value = value


class _FakeSite:
    def __init__(self, xyz, multiplicity, letter="f"):
        self.fract_x, self.fract_y, self.fract_z = (_Value(v) for v in xyz)
        self.multiplicity = _Value(multiplicity)
        self.wyckoff_letter = _Value(letter)


class _FakeStructure:
    """What ``site_problems`` reads of an easydiffraction structure: ``atom_sites[label]``."""

    def __init__(self, sites: dict):
        self.atom_sites = sites


def _hex_phase(x=0.137, y=0.863):
    """P 63/m m c with Zn on 6h (x, -x, 1/4), stated with y = 1 - x (in [0, 1))."""
    return {
        "space_group": "P 63/m m c",
        "unit_cell": {"a": P(5.2), "b": P(5.2), "c": P(8.6), "alpha": P(90.0), "beta": P(90.0), "gamma": P(120.0)},
        "atoms": {
            "Mg1": {"element": "Mg", "x": P(0.333333), "y": P(0.666667), "z": P(0.0625), "occupancy": P(1.0),
                    "ADP": "Uiso", "Uiso": P(0.01)},
            "Zn1": {"element": "Zn", "x": P(x), "y": P(y), "z": P(0.25), "occupancy": P(1.0),
                    "ADP": "Uiso", "Uiso": P(0.01)},
        },
        "scale": P(1.0),
    }


def test_compare_site_same_translated_moved_multiplicity():
    assert nb._compare_site((0.1997, 0.5, 0.5), (0.1997, 0.5, 0.5), 6, 6) == nb.SiteComparison("same")
    found = nb._compare_site((0.137, 0.863, 0.25), (0.137, -0.137, 0.25), 6, 6)
    assert found.status == "translated" and found.shift == (0, -1, 0)
    assert nb._compare_site((0.137, 0.863, 0.25), (0.137, 0.863 + 2e-3, 0.25), 6, 6).status == "moved"
    assert nb._compare_site((0.137, 0.863, 0.25), (0.5, 0.863, 0.25), 6, 6).status == "moved"
    assert nb._compare_site((0.47, 0.5, 0.5), (0.47, 0.5, 0.5), 6, 24).status == "multiplicity"
    # float noise below POSITION_TOL is the same position
    assert nb._compare_site((1 / 3, 2 / 3, 0.25), (0.33333333333, 0.66666666667, 0.25), 2, 2).status == "same"


def test_check_sites_accepts_lattice_translation_with_warning():
    phase = _validated(Zn=_hex_phase()).payload.phases["Zn"]
    structure = _FakeStructure({"Mg1": _FakeSite((1 / 3, 2 / 3, 0.0625), 4, "f"),
                                "Zn1": _FakeSite((0.137, -0.137, 0.25), 6, "h")})
    warnings = nb.check_sites("Zn", phase, "crysfml", structure)
    assert [w["code"] for w in warnings] == ["easydiffraction_position_translated"]
    assert warnings[0]["field_path"] == "payload.phases.Zn.atoms.Zn1"
    assert "(0, -1, 0)" in warnings[0]["message"] and "(0.137, -0.137, 0.25)" in warnings[0]["message"]


def test_check_sites_rejects_move_that_is_not_a_translation():
    phase = _validated(Zn=_hex_phase()).payload.phases["Zn"]
    structure = _FakeStructure({"Mg1": _FakeSite((1 / 3, 2 / 3, 0.0625), 4),
                                "Zn1": _FakeSite((0.137, 0.137, 0.25), 6)})
    with pytest.raises(nb.SiteError) as exc:
        nb.check_sites("Zn", phase, "crysfml", structure)
    assert "payload.phases.Zn.atoms.Zn1 at (0.137, 0.863, 0.25)" in str(exc.value)
    assert "holds it at (0.137, 0.137, 0.25)" in str(exc.value)
    assert isinstance(exc.value, (EasyDiffractionTranslationError, ValueError))


def test_check_sites_rejects_multiplicity_mismatch_naming_both():
    phase = _validated().payload.phases["LaB6"]
    structure = _FakeStructure({"La1": _FakeSite((0.0, 0.0, 0.0), 1, "a"),
                                "B1": _FakeSite((0.1997, 0.5, 0.5), 24, "l")})
    with pytest.raises(nb.SiteError) as exc:
        nb.check_sites("LaB6", phase, "crysfml", structure)
    message = str(exc.value)
    assert "B1" in message and "multiplicity 24" in message and "multiplicity in 'P m -3 m' is 6" in message
    assert "equivalent positions" in message and "(0.5, 0.1997, 0.5)" in message
    assert "La1" not in message.split("- ")[1]  # only the problem atom is listed


def test_origin1_refused_for_cryspy_only():
    from powderline.symmetry import analyze_site

    special = analyze_site("F d -3 m:1", (0.137, 0.25, 0.25))  # 48f
    general = analyze_site("F d -3 m:1", (0.137, 0.271, 0.389))
    why = nb.origin1_problem("F d -3 m:1", "cryspy", special)
    assert why is not None and "'F d -3 m:2'" in why and "crysfml" in why
    assert nb.origin1_problem("F d -3 m:1", "crysfml", special) is None
    assert nb.origin1_problem("F d -3 m:1", "cryspy", general) is None
    assert nb.origin1_problem("F d -3 m:2", "cryspy", analyze_site("F d -3 m:2", (0.137, 0.125, 0.125))) is None
    assert nb.origin1_problem("P m -3 m", "cryspy", analyze_site("P m -3 m", (0.1997, 0.5, 0.5))) is None


def test_check_sites_origin1_error_before_reading_the_structure(monkeypatch):
    """The origin-1 refusal needs no easydiffraction reading of the atom (the setting is the problem)."""
    from powderline.gateways.easydiffraction import space_group_support as sgs

    monkeypatch.setitem(sgs.CRYSPY_SETTINGS, "F d -3 m:1", {"short_name": "F d -3 m", "coord_system_code": "1"})
    phase = _validated(Spinel={
        "space_group": "F d -3 m:1",
        "unit_cell": {k: P(8.08) for k in "abc"} | {k: P(90.0) for k in ("alpha", "beta", "gamma")},
        "atoms": {"O1": {"element": "O", "x": P(0.137), "y": P(0.25), "z": P(0.25), "occupancy": P(1.0),
                         "ADP": "Uiso", "Uiso": P(0.01)}},
        "scale": P(1.0)}).payload.phases["Spinel"]
    with pytest.raises(nb.SiteError, match="origin-choice-1"):
        nb.check_sites("Spinel", phase, "cryspy", _FakeStructure({}))


@pytest.mark.parametrize("template, xyz, expected", [
    ("(x,1/2,1/2)", (0.1997, 0.5, 0.5), True),
    ("(x,1/2,1/2)", (0.5, 0.5, 0.1997), False),      # another image of the orbit (EB-77)
    ("(x,-x,z)", (0.137, 0.863, 0.25), True),         # modulo 1 (EB-74)
    ("(x,-x,z)", (0.137, -0.137, 0.25), True),
    ("(x,2x,1/4)", (0.863, 0.726, 0.25), True),
    ("(x,2x,1/4)", (0.274, 0.137, 0.25), False),
    ("(1/4,1/4,z)", (0.25, 0.25, 0.389), True),
    ("(1/4,1/4,z)", (0.75, 0.25, 0.389), False),
    ("(x,1/2-x,z)", (0.137, 0.363, 0.3), True),
    ("(2x,x,z)", (0.274, 0.137, 0.3), True),
])
def test_matches_template(template, xyz, expected):
    assert nb._matches_template(template, xyz) is expected


def test_check_sites_cryspy_template_rule_names_the_position_to_write(monkeypatch):
    """CrysPy: B on 6f written as (1/2, 1/2, x) is refused with (x, 1/2, 1/2) suggested (A164)."""
    recipe = _recipe()
    b1 = recipe["payload"]["phases"]["LaB6"]["atoms"]["B1"]
    b1["x"], b1["y"], b1["z"] = P(0.5), P(0.5), P(0.1997)
    phase = _validated(recipe).payload.phases["LaB6"]
    structure = _FakeStructure({"La1": _FakeSite((0.0, 0.0, 0.0), 1, "a"),
                                "B1": _FakeSite((0.5, 0.5, 0.1997), 6, "f")})
    monkeypatch.setattr(nb, "_ed_wyckoff_template", lambda name, code, letter: {"f": "(x,1/2,1/2)"}.get(letter))
    with pytest.raises(nb.SiteError) as exc:
        nb.check_sites("LaB6", phase, "cryspy", structure)
    assert "template (x,1/2,1/2)" in str(exc.value) and "(0.1997, 0.5, 0.5)" in str(exc.value)
    # CrysFML has no such rule, and CrysPy accepts the template's own pattern
    assert nb.check_sites("LaB6", phase, "crysfml", structure) == []
    phase_ok = _validated().payload.phases["LaB6"]
    structure_ok = _FakeStructure({"La1": _FakeSite((0.0, 0.0, 0.0), 1, "a"),
                                   "B1": _FakeSite((0.1997, 0.5, 0.5), 6, "f")})
    assert nb.check_sites("LaB6", phase_ok, "cryspy", structure_ok) == []


@needs_ed
def test_build_project_runs_site_check(tmp_path):
    result = nb.build_project(_validated(Zn=_hex_phase()), tmp_path)
    assert [w["code"] for w in result.warnings] in ([], ["easydiffraction_position_translated"])
    zn = result.project.structures["zn"].atom_sites["Zn1"]
    assert int(zn.multiplicity.value) == 6


# ---------------------------------------------------------------------------
# 3.3 refinement flags: one free parameter per tie group (pure plans)
# ---------------------------------------------------------------------------


class _FakeParam:
    def __init__(self, value):
        self.value = value
        self.free = False
        self.fit_min = None
        self.fit_max = None


class _Obj:
    """Attribute bag; ``__getitem__`` serves collections keyed by id."""

    def __init__(self, **attrs):
        self.__dict__.update(attrs)

    def __getitem__(self, key):
        return self.__dict__[key]


def _plan_by_path(plan):
    return {fp.ed_attr_path: fp for fp in plan}


def test_cubic_cell_flagged_frees_only_length_a():
    recipe = _recipe()
    cell = recipe["payload"]["phases"]["LaB6"]["unit_cell"]
    for k in "abc":  # every tie member states the flag and the bounds (A94, F4)
        cell[k] = P(4.1568, True, 4.0, 4.3)
    plan = nb.free_parameter_plan("LaB6", _validated(recipe).payload.phases["LaB6"])
    cell_params = [fp for fp in plan if fp.kind == "cell"]
    assert [fp.ed_attr_path for fp in cell_params] == ["structures.lab6.cell.length_a"]
    fp = cell_params[0]
    assert (fp.path, fp.value, fp.min, fp.max, fp.unit) == ("payload.phases.LaB6.unit_cell.a", 4.1568, 4.0, 4.3,
                                                             "angstrom")


def test_hexagonal_cell_frees_a_and_c_not_b():
    phase = _hex_phase()
    for k in "abc":
        phase["unit_cell"][k] = P(phase["unit_cell"][k][0], True, None, None)
    plan = nb.free_parameter_plan("Zn", _validated(Zn=phase).payload.phases["Zn"])
    assert [fp.ed_attr_path for fp in plan if fp.kind == "cell"] == ["structures.zn.cell.length_a",
                                                                       "structures.zn.cell.length_c"]


def test_monoclinic_cell_frees_beta_only_when_flagged():
    recipe = _recipe(Mono={
        "space_group": "C 1 2/m 1",
        "unit_cell": {"a": P(5.1), "b": P(6.2), "c": P(7.3, True, 7.0, 7.6), "alpha": P(90.0),
                      "beta": P(103.0, True, 100.0, 106.0), "gamma": P(90.0)},
        "atoms": {"Fe1": {"element": "Fe", "x": P(0.0), "y": P(0.0), "z": P(0.0), "occupancy": P(1.0),
                          "ADP": "Uiso", "Uiso": P(0.01)}},
        "scale": P(1.0)})
    plan = nb.free_parameter_plan("Mono", _validated(recipe).payload.phases["Mono"])
    assert [(fp.ed_attr_path, fp.unit) for fp in plan] == [("structures.mono.cell.length_c", "angstrom"),
                                                            ("structures.mono.cell.angle_beta", "degree")]


def test_special_position_atom_frees_one_coordinate():
    recipe = _recipe()
    recipe["payload"]["phases"]["LaB6"]["atoms"]["B1"]["x"] = P(0.1997, True, 0.15, 0.25)  # 6f: (x, 1/2, 1/2)
    plan = nb.free_parameter_plan("LaB6", _validated(recipe).payload.phases["LaB6"])
    coords = [fp for fp in plan if fp.kind == "coordinate"]
    assert [fp.ed_attr_path for fp in coords] == ["structures.lab6.atom_sites.B1.fract_x"]
    assert (coords[0].min, coords[0].max, coords[0].unit) == (0.15, 0.25, "fractional")
    # a coupled pair (x, -x, z): both flagged with mapped bounds, one free parameter (A94, A98)
    hexp = _hex_phase()
    hexp["atoms"]["Zn1"]["x"] = P(0.137, True, 0.1, 0.2)
    hexp["atoms"]["Zn1"]["y"] = P(0.863, True, 0.8, 0.9)
    plan = nb.free_parameter_plan("Zn", _validated(Zn=hexp).payload.phases["Zn"])
    assert [fp.ed_attr_path for fp in plan if fp.kind == "coordinate"] == ["structures.zn.atom_sites.Zn1.fract_x"]


def test_uaniso_cubic_site_frees_one_uij():
    recipe = _recipe()
    la = recipe["payload"]["phases"]["LaB6"]["atoms"]["La1"]
    la["ADP"] = "Uaniso"
    la.pop("Uiso")
    la["Uaniso"] = {"U11": P(0.005, True, 0.0, 0.05), "U22": P(0.005, True, 0.0, 0.05), "U33": P(0.005, True, 0.0, 0.05),
                    "U12": P(0.0), "U13": P(0.0), "U23": P(0.0)}
    plan = nb.free_parameter_plan("LaB6", _validated(recipe).payload.phases["LaB6"])
    adps = [fp for fp in plan if fp.kind == "adp"]
    assert [fp.ed_attr_path for fp in adps] == ["structures.lab6.atom_site_aniso.La1.adp_11"]
    assert (adps[0].path, adps[0].min, adps[0].max, adps[0].unit) == ("payload.phases.LaB6.atoms.La1.Uaniso.U11",
                                                                      0.0, 0.05, "angstrom^2")


def test_occupancy_and_uiso_flags_and_bounds():
    recipe = _recipe()
    b1 = recipe["payload"]["phases"]["LaB6"]["atoms"]["B1"]
    b1["occupancy"] = P(0.9, True, 0.5, 1.0)
    b1["Uiso"] = P(0.006, True, None, 0.05)
    plan = _plan_by_path(nb.free_parameter_plan("LaB6", _validated(recipe).payload.phases["LaB6"]))
    assert plan["structures.lab6.atom_sites.B1.occupancy"].min == 0.5
    uiso = plan["structures.lab6.atom_sites.B1.adp_iso"]
    assert (uiso.min, uiso.max) == (None, 0.05)  # None keeps the engine's physical limit (A138)


def test_experiment_plan_scale_background_instrument_bounds():
    recipe = _recipe()
    recipe["payload"]["phases"]["LaB6"]["scale"] = P(1.0, True, 0.0, None)
    recipe["payload"]["phases"]["LaB6"]["background"]["refine_flag"] = True
    inst = recipe["payload"]["instrument"]
    inst["corrections"]["zero"] = P(0.0, True, -0.1, 0.1)
    inst["broadening"]["w"] = P(0.002, True, 0.0, 0.1)
    inst["corrections"]["displacement"] = P(0.001, True, None, None)
    plan = _plan_by_path(nb.experiment_plan(_validated(recipe).payload))
    assert set(plan) == {"experiments.powderline.linked_structures.lab6.scale",
                         "experiments.powderline.background.0.coef", "experiments.powderline.background.1.coef",
                         "experiments.powderline.instrument.calib_twotheta_offset",
                         "experiments.powderline.peak.broad_gauss_w",
                         "experiments.powderline.instrument.calib_sample_displacement"}
    zero = plan["experiments.powderline.instrument.calib_twotheta_offset"]
    assert (zero.path, zero.min, zero.max, zero.unit) == ("payload.instrument.corrections.zero", -0.1, 0.1,
                                                          "degree 2theta")
    assert plan["experiments.powderline.peak.broad_gauss_w"].unit == "degree^2 2theta (FWHM^2)"
    assert plan["experiments.powderline.background.1.coef"].value == 0.0
    assert plan["experiments.powderline.linked_structures.lab6.scale"].path == "payload.phases.LaB6.scale"


def test_nothing_flagged_gives_empty_plans():
    model = _validated()
    assert nb.free_parameter_plan("LaB6", model.payload.phases["LaB6"]) == []
    assert nb.experiment_plan(model.payload) == []


def test_flagged_but_symmetry_fixed_asserts():
    phase = _validated().payload.phases["LaB6"]
    phase.atoms["B1"].y.refine_flag = True  # behind core's back: y of 6f is fixed at 1/2 (F2)
    with pytest.raises(AssertionError, match="fixed by symmetry"):
        nb.free_parameter_plan("LaB6", phase)
    phase = _validated().payload.phases["LaB6"]
    phase.unit_cell.gamma.refine_flag = True
    with pytest.raises(AssertionError, match="fixed by symmetry"):
        nb.free_parameter_plan("LaB6", phase)
    phase = _validated().payload.phases["LaB6"]
    phase.unit_cell.b.refine_flag = True  # mixed flags in the {a, b, c} group (F1)
    with pytest.raises(AssertionError, match="mixed refine flags"):
        nb.free_parameter_plan("LaB6", phase)


def test_apply_refinement_flags_sets_free_and_bounds_and_moves_translated_bounds():
    hexp = _hex_phase()
    hexp["atoms"]["Zn1"]["x"] = P(0.137, True, 0.1, 0.2)
    hexp["atoms"]["Zn1"]["y"] = P(0.863, True, 0.8, 0.9)
    for k in "abc":
        hexp["unit_cell"][k] = P(hexp["unit_cell"][k][0], True, 5.0, 9.0)
    phase = _validated(Zn=hexp).payload.phases["Zn"]
    structure = _Obj(
        cell=_Obj(length_a=_FakeParam(5.2), length_b=_FakeParam(5.2), length_c=_FakeParam(8.6)),
        atom_sites=_Obj(Zn1=_Obj(fract_x=_FakeParam(0.137), fract_y=_FakeParam(-0.137), fract_z=_FakeParam(0.25)),
                        Mg1=_Obj()))
    manifest = nb.apply_refinement_flags("Zn", phase, structure)
    assert set(manifest) == {"structures.zn.cell.length_a", "structures.zn.cell.length_c",
                             "structures.zn.atom_sites.Zn1.fract_x"}
    assert structure.cell.length_a.free is True and structure.cell.length_b.free is False
    assert (structure.cell.length_a.fit_min, structure.cell.length_a.fit_max) == (5.0, 9.0)
    x = structure.atom_sites["Zn1"].fract_x
    assert x.free is True and (x.fit_min, x.fit_max) == (0.1, 0.2)
    entry = manifest["structures.zn.atom_sites.Zn1.fract_x"]
    assert entry["path"] == "payload.phases.Zn.atoms.Zn1.x" and entry["shift"] == 0.0 and entry["parameter"] is x
    # the free member itself held translated by -1 (A176): bounds move with it, the manifest records the shift
    structure.atom_sites["Zn1"].fract_x = _FakeParam(-0.863)
    entry = nb.apply_refinement_flags("Zn", phase, structure)["structures.zn.atom_sites.Zn1.fract_x"]
    assert entry["shift"] == -1.0
    assert (entry["parameter"].fit_min, entry["parameter"].fit_max) == pytest.approx((-0.9, -0.8))


def test_apply_experiment_flags_with_none_bounds_leaves_engine_defaults():
    recipe = _recipe()
    recipe["payload"]["phases"]["LaB6"]["scale"] = P(1.0, True, 0.0, None)
    recipe["payload"]["instrument"]["radiation"]["wavelength"] = P(1.5406, True, None, None)
    expt = _Obj(linked_structures=_Obj(lab6=_Obj(scale=_FakeParam(1.0))),
                instrument=_Obj(setup_wavelength=_FakeParam(1.5406)))
    manifest = nb.apply_experiment_flags(_validated(recipe).payload, expt)
    assert set(manifest) == {"experiments.powderline.linked_structures.lab6.scale",
                             "experiments.powderline.instrument.setup_wavelength"}
    scale = expt.linked_structures["lab6"].scale
    assert scale.free is True and scale.fit_min == 0.0 and scale.fit_max is None
    lam = expt.instrument.setup_wavelength
    assert lam.free is True and lam.fit_min is None and lam.fit_max is None


def test_set_refuses_unknown_easydiffraction_attribute():
    with pytest.raises(AttributeError, match="has no attribute 'fit_mni'"):
        nb._set(_FakeParam(1.0), "fit_mni", 0.0)


@needs_ed
def test_build_project_frees_one_parameter_per_tie_group(tmp_path):
    recipe = _recipe()
    phase = recipe["payload"]["phases"]["LaB6"]
    for k in "abc":
        phase["unit_cell"][k] = P(4.1568, True, 4.0, 4.3)
    phase["atoms"]["B1"]["x"] = P(0.1997, True, 0.15, 0.25)
    phase["scale"] = P(1.0, True, 0.0, None)
    recipe["payload"]["instrument"]["broadening"]["w"] = P(0.002, True, 0.0, 0.1)
    result = nb.build_project(_validated(recipe), tmp_path)
    assert set(result.manifest) == {"structures.lab6.cell.length_a", "structures.lab6.atom_sites.B1.fract_x",
                                    "experiments.powderline.linked_structures.lab6.scale",
                                    "experiments.powderline.peak.broad_gauss_w"}
    structure = result.project.structures["lab6"]
    assert structure.cell.length_a.free is True and structure.cell.length_b.free is False
    assert structure.cell.length_a.fit_min == 4.0 and structure.cell.length_a.fit_max == 4.3
    assert structure.atom_sites["B1"].fract_x.free is True and structure.atom_sites["B1"].fract_y.free is False
    for entry in result.manifest.values():
        assert entry["parameter"].free is True
