"""Unit tests for powderline.reports (shared report writers).

Tests cover byte-identical round-trip against committed GSAS-II references,
mapping vs. sequence row handling, None → empty field, UTF-8 non-ASCII, LF-only
line endings, output_dir creation, and the engine-free guarantee (no GSASII import).
"""

from __future__ import annotations

import csv
import subprocess
import sys
from pathlib import Path

import pytest

from powderline import reports

pytestmark = pytest.mark.core


# Discover all reference files in examples/*/output/ (excluding topas/ subdirs)
_EXAMPLES_ROOT = Path(__file__).parent.parent / "examples"


def _discover_reference_files(pattern: str) -> list[Path]:
    """Find all files matching pattern in examples/*/output/, excluding topas/."""
    found = []
    for path in _EXAMPLES_ROOT.rglob(pattern):
        # Exclude files under topas/ subdirectories
        if "topas" in path.parts:
            continue
        # Only include files under output/ directories
        if "output" in path.parts:
            found.append(path)
    return sorted(found)


_REFINED_PARAMS_FILES = _discover_reference_files("refined_parameters.csv")
_UNIT_CELL_FILES = _discover_reference_files("*_unit_cell_report.csv")
_FIT_PROFILE_FILES = _discover_reference_files("fit_profile.txt")
_PEAK_LIST_FILES = _discover_reference_files("*_peak_list_report.csv")


# --- Byte round-trip tests ---


@pytest.mark.parametrize(
    "ref_path",
    _REFINED_PARAMS_FILES,
    ids=[str(p.relative_to(_EXAMPLES_ROOT)) for p in _REFINED_PARAMS_FILES],
)
def test_refined_parameters_roundtrip(ref_path, tmp_path):
    """Byte-identical round-trip for refined_parameters.csv."""
    original_bytes = ref_path.read_bytes()

    # Read with csv module as strings
    with ref_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    # Write through the shared writer
    written_path = reports.write_refined_parameters(tmp_path, rows)
    written_bytes = written_path.read_bytes()

    assert written_bytes == original_bytes, (
        f"Round-trip failed for {ref_path.name}. "
        f"First difference at byte {_first_diff_idx(original_bytes, written_bytes)}"
    )


@pytest.mark.parametrize(
    "ref_path",
    _UNIT_CELL_FILES,
    ids=[str(p.relative_to(_EXAMPLES_ROOT)) for p in _UNIT_CELL_FILES],
)
def test_unit_cell_report_roundtrip(ref_path, tmp_path):
    """Byte-identical round-trip for *_unit_cell_report.csv."""
    original_bytes = ref_path.read_bytes()

    # Extract phase name from filename (remove _unit_cell_report.csv suffix)
    phase_name = ref_path.stem.replace("_unit_cell_report", "")

    # Read with csv module as strings
    with ref_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    # Write through the shared writer
    written_path = reports.write_unit_cell_report(tmp_path, phase_name, rows)
    written_bytes = written_path.read_bytes()

    assert written_bytes == original_bytes, (
        f"Round-trip failed for {ref_path.name}. "
        f"First difference at byte {_first_diff_idx(original_bytes, written_bytes)}"
    )


@pytest.mark.parametrize(
    "ref_path",
    _FIT_PROFILE_FILES,
    ids=[str(p.relative_to(_EXAMPLES_ROOT)) for p in _FIT_PROFILE_FILES],
)
def test_fit_profile_roundtrip(ref_path, tmp_path):
    """Byte-identical round-trip for fit_profile.txt (tab-delimited)."""
    original_bytes = ref_path.read_bytes()

    # Read with csv module as strings (tab-delimited)
    with ref_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        rows = list(reader)

    # Write through the shared writer
    written_path = reports.write_fit_profile(tmp_path, rows)
    written_bytes = written_path.read_bytes()

    assert written_bytes == original_bytes, (
        f"Round-trip failed for {ref_path.name}. "
        f"First difference at byte {_first_diff_idx(original_bytes, written_bytes)}"
    )


@pytest.mark.parametrize(
    "ref_path",
    _PEAK_LIST_FILES,
    ids=[str(p.relative_to(_EXAMPLES_ROOT)) for p in _PEAK_LIST_FILES],
)
def test_peak_list_report_roundtrip(ref_path, tmp_path):
    """Byte-identical round-trip for *_peak_list_report.csv."""
    original_bytes = ref_path.read_bytes()

    # Extract phase name from filename (remove _peak_list_report.csv suffix)
    phase_name = ref_path.stem.replace("_peak_list_report", "")

    # Read with csv module as strings
    with ref_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        columns = reader.fieldnames
        rows = list(reader)

    # Write through the shared writer
    written_path = reports.write_peak_list_report(tmp_path, phase_name, rows, columns)
    written_bytes = written_path.read_bytes()

    assert written_bytes == original_bytes, (
        f"Round-trip failed for {ref_path.name}. "
        f"First difference at byte {_first_diff_idx(original_bytes, written_bytes)}"
    )


def _first_diff_idx(a: bytes, b: bytes) -> int:
    """Return the index of the first differing byte, or -1 if equal."""
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return i
    if len(a) != len(b):
        return min(len(a), len(b))
    return -1


# --- Mapping vs sequence rows ---


def test_refined_parameters_mapping_vs_sequence(tmp_path):
    """Mapping rows and sequence rows produce identical output."""
    # Write using mapping rows
    mapping_rows = [
        {
            "parameter_name": "test_param",
            "descriptive_name": "Test Parameter",
            "phase_name": "TestPhase",
            "phase_idx": "1",
            "atom_name": "TestAtom",
            "atom_idx": "2",
            "value": "1.234",
            "esd": "0.056",
            "category": "test_category",
        }
    ]
    path1 = reports.write_refined_parameters(tmp_path / "mapping", mapping_rows)

    # Write using sequence rows (in REFINED_PARAMETERS_COLUMNS order)
    sequence_rows = [
        ["test_param", "Test Parameter", "TestPhase", "1", "TestAtom", "2", "1.234", "0.056", "test_category"]
    ]
    path2 = reports.write_refined_parameters(tmp_path / "sequence", sequence_rows)

    assert path1.read_bytes() == path2.read_bytes()


def test_unit_cell_mapping_vs_sequence(tmp_path):
    """Mapping rows and sequence rows produce identical output."""
    mapping_rows = [{"parameter": "cell_a", "value": "4.123", "esd": "0.001"}]
    path1 = reports.write_unit_cell_report(tmp_path / "mapping", "Phase1", mapping_rows)

    sequence_rows = [["cell_a", "4.123", "0.001"]]
    path2 = reports.write_unit_cell_report(tmp_path / "sequence", "Phase1", sequence_rows)

    assert path1.read_bytes() == path2.read_bytes()


def test_fit_profile_mapping_vs_sequence(tmp_path):
    """Mapping rows and sequence rows produce identical output."""
    mapping_rows = [
        {
            "two_theta": "1.0",
            "y_obs": "2.0",
            "y_weights": "3.0",
            "y_calc": "4.0",
            "y_diff": "5.0",
            "y_bkg": "6.0",
            "q_values": "7.0",
            "d_spacings": "8.0",
        }
    ]
    path1 = reports.write_fit_profile(tmp_path / "mapping", mapping_rows)

    sequence_rows = [["1.0", "2.0", "3.0", "4.0", "5.0", "6.0", "7.0", "8.0"]]
    path2 = reports.write_fit_profile(tmp_path / "sequence", sequence_rows)

    assert path1.read_bytes() == path2.read_bytes()


def test_peak_list_mapping_vs_sequence(tmp_path):
    """Mapping rows and sequence rows produce identical output."""
    columns = ["h", "k", "l", "d_spacing"]
    mapping_rows = [{"h": "1", "k": "2", "l": "3", "d_spacing": "4.56"}]
    path1 = reports.write_peak_list_report(tmp_path / "mapping", "Phase1", mapping_rows, columns)

    sequence_rows = [["1", "2", "3", "4.56"]]
    path2 = reports.write_peak_list_report(tmp_path / "sequence", "Phase1", sequence_rows, columns)

    assert path1.read_bytes() == path2.read_bytes()


# --- None → empty field ---


def test_none_becomes_empty_field(tmp_path):
    """None values are written as empty fields."""
    rows = [
        {
            "parameter_name": "param1",
            "descriptive_name": None,
            "phase_name": "",
            "phase_idx": None,
            "atom_name": None,
            "atom_idx": None,
            "value": "1.0",
            "esd": None,
            "category": "test",
        }
    ]
    path = reports.write_refined_parameters(tmp_path, rows)
    content = path.read_text(encoding="utf-8")

    # Check the data row (second line after header)
    lines = content.strip().split("\n")
    assert len(lines) == 2
    data_line = lines[1]
    # descriptive_name (None), phase_name (""), phase_idx (None), atom_name (None), atom_idx (None), esd (None) should be empty
    assert data_line == "param1,,,,,,1.0,,test"


# --- LF-only line endings ---


def test_lf_only_no_crlf(tmp_path):
    """Output files contain only LF line endings, never CRLF."""
    rows = [{"parameter": "cell_a", "value": "4.0", "esd": "0.001"}]
    path = reports.write_unit_cell_report(tmp_path, "TestPhase", rows)
    content_bytes = path.read_bytes()

    assert b"\r" not in content_bytes, "File contains CRLF (\\r\\n) but should be LF-only"


# --- UTF-8 non-ASCII round-trip ---


def test_utf8_non_ascii_roundtrip(tmp_path):
    """UTF-8 non-ASCII characters (e.g., subscripts) round-trip correctly."""
    rows = [
        {
            "parameter_name": "test_param",
            "descriptive_name": "Test",
            "phase_name": "Fe₂O₃",  # Unicode subscripts
            "phase_idx": "1",
            "atom_name": "Fe",
            "atom_idx": "1",
            "value": "1.0",
            "esd": "0.01",
            "category": "test",
        }
    ]
    path = reports.write_refined_parameters(tmp_path, rows)

    # Read back and verify the phase name
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        row = next(reader)
        assert row["phase_name"] == "Fe₂O₃"


# --- output_dir creation ---


def test_output_dir_created(tmp_path):
    """Output directory is created if it doesn't exist."""
    nested_dir = tmp_path / "nested" / "path" / "output"
    assert not nested_dir.exists()

    rows = [{"parameter": "cell_a", "value": "4.0", "esd": "0.001"}]
    path = reports.write_unit_cell_report(nested_dir, "Phase1", rows)

    assert nested_dir.exists()
    assert path.exists()
    assert path == nested_dir / "Phase1_unit_cell_report.csv"


# --- Engine-free guarantee (no GSASII import) ---


def test_no_gsasii_import():
    """Importing powderline.reports does not import GSASII."""
    # Run in a subprocess to get a fresh import environment
    code = """
import sys
import importlib.abc

# Block GSASII import using MetaPathFinder
class _BlockGSASII(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path, target=None):
        if name == "GSASII" or name.startswith("GSASII."):
            raise ImportError(f"Blocked import of {name} (should not be needed)")
        return None

sys.meta_path.insert(0, _BlockGSASII())

# Now try to import powderline.reports
import powderline.reports

# Verify GSASII was not imported
assert "GSASII" not in sys.modules, "GSASII was imported (should not happen)"
print("OK")
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).parent.parent,
        env={**subprocess.os.environ, "PYTHONPATH": str(Path(__file__).parent.parent / "src")},
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, (
        f"Subprocess failed:\n"
        f"STDOUT: {result.stdout}\n"
        f"STDERR: {result.stderr}"
    )
    assert "OK" in result.stdout


# --- Column constants ---


def test_refined_parameters_columns_constant():
    """REFINED_PARAMETERS_COLUMNS has the expected 9 columns in order."""
    assert reports.REFINED_PARAMETERS_COLUMNS == (
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


def test_unit_cell_columns_constant():
    """UNIT_CELL_COLUMNS has the expected 3 columns in order."""
    assert reports.UNIT_CELL_COLUMNS == ("parameter", "value", "esd")


def test_fit_profile_columns_constant():
    """FIT_PROFILE_COLUMNS has the expected 8 columns in order."""
    assert reports.FIT_PROFILE_COLUMNS == (
        "two_theta",
        "y_obs",
        "y_weights",
        "y_calc",
        "y_diff",
        "y_bkg",
        "q_values",
        "d_spacings",
    )


def test_sequence_row_length_mismatch_raises(tmp_path):
    with pytest.raises(ValueError, match="row has 2 values, expected 3"):
        reports.write_unit_cell_report(tmp_path, "LaB6", [("a", "1.0")])
