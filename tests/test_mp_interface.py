"""Tests for powderline.mp_interface.MPInterface (mocked — no live API calls).

MPRester is replaced at the module boundary (powderline.mp_interface.MPRester)
with a fake client. Structures attached to the fake summary documents are REAL
pymatgen Structures so the SpacegroupAnalyzer standardization and per-species
site extraction paths are genuinely exercised.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.gsasii

pymatgen_core = pytest.importorskip("pymatgen.core")
from pymatgen.core import Lattice, Structure  # noqa: E402

import powderline.mp_interface as mp_interface  # noqa: E402
from powderline.mp_interface import (  # noqa: E402
    MPInterface,
    MPInterfaceError,
    MPAuthError,
    MPNotFoundError,
    MPConnectionError,
)

VALID_KEY = "0123456789abcdef0123456789abcdef"  # 32-char dummy


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeMPID:
    """Mimics emmet-core's MPID: not a str, but stringifies to one."""

    def __init__(self, value: str):
        self._value = value

    def __str__(self) -> str:
        return self._value


class FakeSummaryEndpoint:
    def __init__(self, docs):
        self.docs = docs
        self.calls = []

    def search(self, **kwargs):
        self.calls.append(kwargs)
        result = self.docs
        if callable(result):
            result = result(**kwargs)
        if isinstance(result, Exception):
            raise result
        return result


class FakeClient:
    def __init__(self, docs):
        self.materials = SimpleNamespace(summary=FakeSummaryEndpoint(docs))
        self.session = SimpleNamespace(closed=False)
        self.session.close = lambda: setattr(self.session, "closed", True)


@pytest.fixture
def make_interface(monkeypatch):
    """Build an MPInterface whose MPRester returns the given docs (or raises)."""

    def _make(docs):
        client = FakeClient(docs)
        monkeypatch.setattr(mp_interface, "MPRester", lambda api_key: client)
        iface = MPInterface(api_key=VALID_KEY)
        return iface, client

    return _make


def _summary_doc(structure, material_id="mp-2680", formula="LaB6",
                 symbol="Pm-3m", crystal_system="Cubic",
                 energy_above_hull=0.0, theoretical=False, nelements=2):
    return SimpleNamespace(
        material_id=FakeMPID(material_id),
        formula_pretty=formula,
        structure=structure,
        symmetry=SimpleNamespace(symbol=symbol, crystal_system=crystal_system),
        energy_above_hull=energy_above_hull,
        theoretical=theoretical,
        nelements=nelements,
    )


def _lab6_structure() -> Structure:
    """Ordered LaB6 (Pm-3m), full conventional cell from spacegroup + basis."""
    return Structure.from_spacegroup(
        "Pm-3m", Lattice.cubic(4.15682),
        ["La", "B"], [[0.0, 0.0, 0.0], [0.5, 0.5, 0.2021]],
    )


def _nacl_primitive_structure() -> Structure:
    """NaCl in its PRIMITIVE rhombohedral setting (2 atoms, a != conventional)."""
    half = 5.69169 / 2
    lattice = Lattice([[0, half, half], [half, 0, half], [half, half, 0]])
    return Structure(lattice, ["Na", "Cl"], [[0, 0, 0], [0.5, 0.5, 0.5]])


def _disordered_structure() -> Structure:
    """BCC cell with a 50/50 Fe/Ni site (disordered)."""
    return Structure(
        Lattice.cubic(2.87),
        [{"Fe": 0.5, "Ni": 0.5}, {"Fe": 0.5, "Ni": 0.5}],
        [[0, 0, 0], [0.5, 0.5, 0.5]],
    )


# ---------------------------------------------------------------------------
# Key validation / availability
# ---------------------------------------------------------------------------

class TestInit:

    def test_no_key_raises_auth_error(self):
        with pytest.raises(MPAuthError, match="API key required") as excinfo:
            MPInterface(api_key=None)
        # MPAuthError participates in the MPInterfaceError taxonomy
        assert isinstance(excinfo.value, MPInterfaceError)

    def test_placeholder_key_raises_auth_error(self):
        with pytest.raises(MPAuthError, match="placeholder"):
            MPInterface(api_key="YOUR_API_KEY_HERE")

    def test_short_key_raises_auth_error(self):
        with pytest.raises(MPAuthError, match="too short"):
            MPInterface(api_key="short-key")

    def test_mp_api_unavailable_raises_import_error(self, monkeypatch):
        monkeypatch.setattr(mp_interface, "MP_API_AVAILABLE", False)
        with pytest.raises(ImportError, match="mp-api not installed"):
            MPInterface(api_key=VALID_KEY)

    def test_context_manager_closes_session(self, make_interface):
        iface, client = make_interface([])
        with iface as mp:
            assert mp is iface
        assert client.session.closed


# ---------------------------------------------------------------------------
# get_structure
# ---------------------------------------------------------------------------

class TestGetStructure:

    def test_ordered_lab6(self, make_interface):
        iface, client = make_interface([_summary_doc(_lab6_structure())])
        data = iface.get_structure("mp-2680")

        assert data["material_id"] == "mp-2680"
        assert data["formula"] == "LaB6"
        assert data["space_group"] == "Pm-3m"
        assert data["is_ordered"] is True
        assert data["cif"]  # non-empty CIF of the conventional cell

        cell = data["unit_cell"]
        assert cell["a"] == pytest.approx(4.15682)
        assert cell["alpha"] == pytest.approx(90.0)

        # LaB6: 1 La + 6 B in the conventional cell, one entry per site
        assert len(data["sites"]) == 7
        assert all(s["occupancy"] == 1.0 for s in data["sites"])
        labels = [s["label"] for s in data["sites"]]
        assert len(labels) == len(set(labels)), "site labels must be unique"

        meta = data["metadata"]
        assert meta["space_group_number"] == 221
        assert meta["crystal_system"] == "cubic"
        assert meta["nelements"] == 2
        assert meta["num_atoms"] == pytest.approx(7.0)
        assert meta["energy_above_hull"] == 0.0
        assert meta["theoretical"] is False

    def test_primitive_cell_standardized_to_conventional(self, make_interface):
        """MP often stores primitive cells; the emitted cell/symbol must be
        the conventional setting (guards the centered-lattice bug)."""
        doc = _summary_doc(_nacl_primitive_structure(), material_id="mp-22862",
                           formula="NaCl", symbol="Fm-3m", nelements=2)
        iface, client = make_interface([doc])
        data = iface.get_structure("mp-22862")

        assert data["space_group"] == "Fm-3m"
        cell = data["unit_cell"]
        # Conventional cubic cell, not the 2-atom rhombohedral primitive
        assert cell["a"] == pytest.approx(5.69169, abs=1e-3)
        assert cell["alpha"] == pytest.approx(90.0)
        assert cell["a"] == pytest.approx(cell["b"]) == pytest.approx(cell["c"])
        assert len(data["sites"]) == 8  # 4 Na + 4 Cl

    def test_screw_axis_symbol_underscores_stripped(self, make_interface):
        """spglib returns screw-axis symbols like 'I4_1/amd'; GSAS-II needs
        'I41/amd' — the underscore must be stripped."""
        anatase = Structure.from_spacegroup(
            "I4_1/amd", Lattice.tetragonal(3.785, 9.514),
            ["Ti", "O"], [[0.0, 0.75, 0.125], [0.0, 0.75, 0.3331]],
        )
        doc = _summary_doc(anatase, material_id="mp-390", formula="TiO2",
                           symbol="I4_1/amd", crystal_system="Tetragonal")
        iface, _ = make_interface([doc])
        data = iface.get_structure("mp-390")

        assert "_" not in data["space_group"]
        assert data["space_group"] == "I41/amd"
        assert data["metadata"]["space_group_number"] == 141

    def test_disordered_partial_occupancy(self, make_interface):
        doc = _summary_doc(_disordered_structure(), material_id="mp-9999",
                           formula="FeNi", symbol="Im-3m", nelements=2)
        iface, client = make_interface([doc])
        data = iface.get_structure("mp-9999")

        assert data["is_ordered"] is False
        assert all(s["occupancy"] == pytest.approx(0.5) for s in data["sites"])
        # Each disordered site expands to one entry per species,
        # co-located but with distinct labels
        labels = [s["label"] for s in data["sites"]]
        assert len(labels) == len(set(labels))
        elements = {s["element"] for s in data["sites"]}
        assert elements == {"Fe", "Ni"}

    def test_id_prefix_normalization(self, make_interface):
        iface, client = make_interface([_summary_doc(_lab6_structure())])
        data = iface.get_structure("2680")
        assert client.materials.summary.calls[0]["material_ids"] == ["mp-2680"]
        assert data["material_id"] == "mp-2680"

    def test_mpid_like_input_stringified(self, make_interface):
        iface, client = make_interface([_summary_doc(_lab6_structure())])
        data = iface.get_structure(FakeMPID("mp-2680"))
        assert client.materials.summary.calls[0]["material_ids"] == ["mp-2680"]
        assert isinstance(data["material_id"], str)

    def test_canonical_id_returned_for_merged_entry(self, make_interface):
        """Querying a deprecated/merged alias returns the doc filed under its
        canonical ID; the result must report the canonical ID (matching
        search_by_formula), not echo the requested alias."""
        doc = _summary_doc(_lab6_structure(), material_id="mp-2680")
        iface, client = make_interface([doc])
        data = iface.get_structure("mp-999999")  # alias of mp-2680
        assert client.materials.summary.calls[0]["material_ids"] == ["mp-999999"]
        assert data["material_id"] == "mp-2680"
        assert data["metadata"]["material_id"] == "mp-2680"

    def test_not_found_raises(self, make_interface):
        iface, _ = make_interface([])
        with pytest.raises(MPNotFoundError, match="mp-404") as excinfo:
            iface.get_structure("mp-404")
        # Raised as-is by _map_error, not re-wrapped into the base class
        assert type(excinfo.value) is MPNotFoundError


# ---------------------------------------------------------------------------
# search_by_formula
# ---------------------------------------------------------------------------

class TestSearchByFormula:

    def test_sorted_by_energy_above_hull_none_last(self, make_interface):
        structure = _lab6_structure()
        docs = [
            _summary_doc(structure, material_id="mp-2", energy_above_hull=0.05),
            _summary_doc(structure, material_id="mp-3", energy_above_hull=None),
            _summary_doc(structure, material_id="mp-1", energy_above_hull=0.0),
        ]
        iface, _ = make_interface(docs)
        results = iface.search_by_formula("LaB6")

        assert [r["material_id"] for r in results] == ["mp-1", "mp-2", "mp-3"]
        assert [r["energy_above_hull"] for r in results] == [0.0, 0.05, None]

    def test_search_result_contract(self, make_interface):
        """Each result carries str material_id, formula, space_group,
        energy_above_hull, theoretical."""
        iface, _ = make_interface([_summary_doc(_lab6_structure())])
        result = iface.search_by_formula("LaB6")[0]
        assert isinstance(result["material_id"], str)
        assert result["material_id"] == "mp-2680"
        assert result["formula"] == "LaB6"
        assert result["space_group"] == "Pm-3m"
        assert result["energy_above_hull"] == 0.0
        assert result["theoretical"] is False

    def test_empty_results(self, make_interface):
        iface, _ = make_interface([])
        assert iface.search_by_formula("Xx9Zz") == []


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------

class TestErrorMapping:

    def test_401_maps_to_auth_error(self, make_interface):
        from powderline.mp_interface import MPRestError
        iface, _ = make_interface(
            MPRestError("REST query returned with error status code 401"))
        with pytest.raises(MPAuthError):
            iface.search_by_formula("LaB6")

    def test_connection_error_maps(self, make_interface):
        import requests
        iface, _ = make_interface(requests.exceptions.ConnectionError("refused"))
        with pytest.raises(MPConnectionError):
            iface.get_structure("mp-2680")

    def test_other_error_maps_to_interface_error_with_cause(self, make_interface):
        original = RuntimeError("unexpected")
        iface, _ = make_interface(original)
        with pytest.raises(MPInterfaceError) as excinfo:
            iface.search_by_formula("LaB6")
        assert not isinstance(excinfo.value, (MPAuthError, MPConnectionError))
        assert excinfo.value.__cause__ is original

    def test_not_found_not_rewrapped(self, make_interface):
        iface, _ = make_interface([])
        with pytest.raises(MPNotFoundError) as excinfo:
            iface.get_structure("mp-404")
        assert type(excinfo.value) is MPNotFoundError
