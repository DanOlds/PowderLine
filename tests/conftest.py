"""Pytest configuration and fixtures for PowderLine tests."""

import json
import pytest
from pathlib import Path
import tempfile
import shutil


@pytest.fixture
def example_LaB6_dir():
    """Path to example_LaB6 directory."""
    return Path("examples/example_LaB6")


@pytest.fixture
def example_LaB6_input(example_LaB6_dir):
    """Path to example_LaB6 input.json file."""
    return example_LaB6_dir / "input.json"


@pytest.fixture
def example_LaB6_reference_lst(example_LaB6_dir):
    """Path to example_LaB6 reference .lst output file."""
    return example_LaB6_dir / "output" / "dummy.lst"  # Fixed filename in schema 0.25


@pytest.fixture
def example_LaB6_reference_unit_cell_csv(example_LaB6_dir):
    """Path to example_LaB6 reference unit cell CSV file."""
    return example_LaB6_dir / "output" / "LaB6_unit_cell_report.csv"


@pytest.fixture
def example_LaB6_reference_peak_csv(example_LaB6_dir):
    """Path to example_LaB6 reference peak list CSV file."""
    return example_LaB6_dir / "output" / "LaB6_peak_list_report.csv"


@pytest.fixture
def example_LaB6_reference_refined_params_csv(example_LaB6_dir):
    """Path to example_LaB6 reference refined_parameters.csv file."""
    return example_LaB6_dir / "output" / "refined_parameters.csv"


# LaB6 Simulation Example Fixtures
@pytest.fixture
def example_LaB6_simulation_dir():
    """Path to example_LaB6_simulation directory."""
    return Path("examples/example_LaB6_simulation")


@pytest.fixture
def example_LaB6_simulation_input(example_LaB6_simulation_dir):
    """Path to example_LaB6_simulation input.json file."""
    return example_LaB6_simulation_dir / "input.json"


@pytest.fixture
def example_LaB6_simulation_reference_lst(example_LaB6_simulation_dir):
    """Path to example_LaB6_simulation reference .lst output file."""
    return example_LaB6_simulation_dir / "output" / "dummy.lst"


@pytest.fixture
def example_LaB6_simulation_reference_unit_cell_csv(example_LaB6_simulation_dir):
    """Path to example_LaB6_simulation reference unit cell CSV file."""
    return example_LaB6_simulation_dir / "output" / "LaB6_unit_cell_report.csv"


@pytest.fixture
def example_LaB6_simulation_reference_peak_csv(example_LaB6_simulation_dir):
    """Path to example_LaB6_simulation reference peak list CSV file."""
    return example_LaB6_simulation_dir / "output" / "LaB6_peak_list_report.csv"


@pytest.fixture
def example_LaB6_simulation_reference_refined_params_csv(example_LaB6_simulation_dir):
    """Path to example_LaB6_simulation reference refined_parameters.csv file."""
    return example_LaB6_simulation_dir / "output" / "refined_parameters.csv"


# ─── API test fixtures ────────────────────────────────────────────────────────

@pytest.fixture
def recipe_LaB6_dict():
    """example_LaB6 recipe loaded as a plain dict (no model validation)."""
    return json.loads(Path("examples/example_LaB6/input.json").read_text())


@pytest.fixture
def recipe_LaB6_model(recipe_LaB6_dict):
    """example_LaB6 recipe as a validated RecipeModel."""
    import powderline
    return powderline.validate(recipe_LaB6_dict).recipe


@pytest.fixture
def recipe_LaB6_singlepeakfit_dict():
    """example_LaB6_singlepeakfit recipe loaded as a plain dict."""
    return json.loads(Path("examples/example_LaB6_singlepeakfit/input.json").read_text())


# ─── Shared tmp_output_dir ────────────────────────────────────────────────────

@pytest.fixture
def tmp_output_dir(tmp_path):
    """Create a temporary output directory that auto-cleans."""
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    yield output_dir
    # Cleanup handled by tmp_path fixture


# DRX_33 Multi-Phase Example Fixtures
@pytest.fixture
def example_DRX_33_dir():
    """Path to example_DRX_33 directory."""
    return Path("examples/example_DRX_33")


@pytest.fixture
def example_DRX_33_input(example_DRX_33_dir):
    """Path to example_DRX_33 input.json file."""
    return example_DRX_33_dir / "input.json"


@pytest.fixture
def example_DRX_33_reference_lst(example_DRX_33_dir):
    """Path to example_DRX_33 reference .lst output file."""
    return example_DRX_33_dir / "output" / "dummy.lst"


@pytest.fixture
def example_DRX_33_reference_unit_cell_csvs(example_DRX_33_dir):
    """Paths to example_DRX_33 reference unit cell CSV files (both phases)."""
    return {
        "DRX_33": example_DRX_33_dir / "output" / "DRX_33_unit_cell_report.csv",
        "Li4MgWO6_SG12": example_DRX_33_dir / "output" / "Li4MgWO6_SG12_unit_cell_report.csv"
    }


@pytest.fixture
def example_DRX_33_reference_peak_csvs(example_DRX_33_dir):
    """Paths to example_DRX_33 reference peak list CSV files (both phases)."""
    return {
        "DRX_33": example_DRX_33_dir / "output" / "DRX_33_peak_list_report.csv",
        "Li4MgWO6_SG12": example_DRX_33_dir / "output" / "Li4MgWO6_SG12_peak_list_report.csv"
    }


@pytest.fixture
def example_DRX_33_reference_refined_params_csv(example_DRX_33_dir):
    """Path to example_DRX_33 reference refined_parameters.csv file."""
    return example_DRX_33_dir / "output" / "refined_parameters.csv"


# ─── MP Pipeline fixtures ─────────────────────────────────────────────────────

@pytest.fixture
def mp_lab6_structure_data():
    """Synthetic structure data in MPInterface.get_structure() format for LaB6 (mp-2680).

    Uses LaB6 lattice parameters from examples/example_LaB6/input.json.
    No Materials Project API call is made — this fixture is safe to use in CI.
    """
    return {
        'material_id': 'mp-2680',
        'cif': '',  # Not used by SimulationRecipeBuilder
        'formula': 'LaB6',
        # Condensed spglib-style symbol, as emitted by MPInterface;
        # kicker normalizes it via G2spc.StandardizeSpcName
        'space_group': 'Pm-3m',
        'unit_cell': {
            'a': 4.15682,
            'b': 4.15682,
            'c': 4.15682,
            'alpha': 90.0,
            'beta': 90.0,
            'gamma': 90.0,
            'volume': 71.82632674659455,
        },
        'sites': [
            {'element': 'La', 'x': 0.0, 'y': 0.0, 'z': 0.0,
             'occupancy': 1.0, 'label': 'La1'},
            {'element': 'B', 'x': 0.5, 'y': 0.5, 'z': 0.2021,
             'occupancy': 1.0, 'label': 'B1'},
        ],
        'is_ordered': True,
        'metadata': {
            'material_id': 'mp-2680',
            'formula': 'LaB6',
            'space_group': 'Pm-3m',
            'space_group_number': 221,
            'crystal_system': 'cubic',
            'nelements': 2,
            'num_atoms': 7.0,
            'energy_above_hull': 0.0,
            'theoretical': False,
        },
    }


# DRX_33 Simulation Example Fixtures
@pytest.fixture
def example_DRX_33_simulation_dir():
    """Path to example_DRX_33_simulation directory."""
    return Path("examples/example_DRX_33_simulation")


@pytest.fixture
def example_DRX_33_simulation_input(example_DRX_33_simulation_dir):
    """Path to example_DRX_33_simulation input.json file."""
    return example_DRX_33_simulation_dir / "input.json"


@pytest.fixture
def example_DRX_33_simulation_reference_lst(example_DRX_33_simulation_dir):
    """Path to example_DRX_33_simulation reference .lst output file."""
    return example_DRX_33_simulation_dir / "output" / "dummy.lst"


@pytest.fixture
def example_DRX_33_simulation_reference_unit_cell_csvs(example_DRX_33_simulation_dir):
    """Paths to example_DRX_33_simulation reference unit cell CSV files (both phases)."""
    return {
        "DRX_33": example_DRX_33_simulation_dir / "output" / "DRX_33_unit_cell_report.csv",
        "Li4MgWO6_SG12": example_DRX_33_simulation_dir / "output" / "Li4MgWO6_SG12_unit_cell_report.csv"
    }


@pytest.fixture
def example_DRX_33_simulation_reference_peak_csvs(example_DRX_33_simulation_dir):
    """Paths to example_DRX_33_simulation reference peak list CSV files (both phases)."""
    return {
        "DRX_33": example_DRX_33_simulation_dir / "output" / "DRX_33_peak_list_report.csv",
        "Li4MgWO6_SG12": example_DRX_33_simulation_dir / "output" / "Li4MgWO6_SG12_peak_list_report.csv"
    }


@pytest.fixture
def example_DRX_33_simulation_reference_refined_params_csv(example_DRX_33_simulation_dir):
    """Path to example_DRX_33_simulation reference refined_parameters.csv file."""
    return example_DRX_33_simulation_dir / "output" / "refined_parameters.csv"


# LaB6 single-peak-fit example fixtures
@pytest.fixture
def example_LaB6_singlepeakfit_dir():
    """Path to example_LaB6_singlepeakfit directory."""
    return Path("examples/example_LaB6_singlepeakfit")


@pytest.fixture
def example_LaB6_singlepeakfit_input(example_LaB6_singlepeakfit_dir):
    """Path to example_LaB6_singlepeakfit input.json file."""
    return example_LaB6_singlepeakfit_dir / "input.json"


@pytest.fixture
def example_LaB6_singlepeakfit_reference_single_peaks_txt(example_LaB6_singlepeakfit_dir):
    """Path to example_LaB6_singlepeakfit reference single_peaks_report.txt file."""
    return example_LaB6_singlepeakfit_dir / "output" / "single_peaks_report.txt"


@pytest.fixture
def example_LaB6_singlepeakfit_reference_convergence_txt(example_LaB6_singlepeakfit_dir):
    """Path to example_LaB6_singlepeakfit reference peak_convergence_diagnostics.txt file."""
    return example_LaB6_singlepeakfit_dir / "output" / "peak_convergence_diagnostics.txt"

# ============================================================================
# example_DRX_33_atomrefine fixtures
# ============================================================================

@pytest.fixture
def example_DRX_33_atomrefine_dir():
    """Path to example_DRX_33_atomrefine directory."""
    return Path("examples/example_DRX_33_atomrefine")


@pytest.fixture
def example_DRX_33_atomrefine_input(example_DRX_33_atomrefine_dir):
    """Path to example_DRX_33_atomrefine input.json file."""
    return example_DRX_33_atomrefine_dir / "input.json"


@pytest.fixture
def example_DRX_33_atomrefine_reference_lst(example_DRX_33_atomrefine_dir):
    """Path to example_DRX_33_atomrefine reference .lst output file."""
    return example_DRX_33_atomrefine_dir / "output" / "dummy.lst"


@pytest.fixture
def example_DRX_33_atomrefine_reference_unit_cell_csvs(example_DRX_33_atomrefine_dir):
    """Paths to example_DRX_33_atomrefine reference unit cell CSV files (both phases)."""
    return {
        "DRX_33": example_DRX_33_atomrefine_dir / "output" / "DRX_33_unit_cell_report.csv",
        "Li4MgWO6_SG12": example_DRX_33_atomrefine_dir / "output" / "Li4MgWO6_SG12_unit_cell_report.csv"
    }


@pytest.fixture
def example_DRX_33_atomrefine_reference_peak_csvs(example_DRX_33_atomrefine_dir):
    """Paths to example_DRX_33_atomrefine reference peak list CSV files (both phases)."""
    return {
        "DRX_33": example_DRX_33_atomrefine_dir / "output" / "DRX_33_peak_list_report.csv",
        "Li4MgWO6_SG12": example_DRX_33_atomrefine_dir / "output" / "Li4MgWO6_SG12_peak_list_report.csv"
    }


@pytest.fixture
def example_DRX_33_atomrefine_reference_refined_params_csv(example_DRX_33_atomrefine_dir):
    """Path to example_DRX_33_atomrefine reference refined_parameters.csv file."""
    return example_DRX_33_atomrefine_dir / "output" / "refined_parameters.csv"


# ============================================================================
# example_DRX_33_anisoADP fixtures
# ============================================================================

@pytest.fixture
def example_DRX_33_anisoADP_dir():
    """Path to example_DRX_33_anisoADP directory."""
    return Path("examples/example_DRX_33_anisoADP")


@pytest.fixture
def example_DRX_33_anisoADP_input(example_DRX_33_anisoADP_dir):
    """Path to example_DRX_33_anisoADP input.json file."""
    return example_DRX_33_anisoADP_dir / "input.json"


@pytest.fixture
def example_DRX_33_anisoADP_reference_lst(example_DRX_33_anisoADP_dir):
    """Path to example_DRX_33_anisoADP reference .lst output file."""
    return example_DRX_33_anisoADP_dir / "output" / "dummy.lst"


@pytest.fixture
def example_DRX_33_anisoADP_reference_unit_cell_csvs(example_DRX_33_anisoADP_dir):
    """Paths to example_DRX_33_anisoADP reference unit cell CSV files (both phases)."""
    return {
        "DRX_33": example_DRX_33_anisoADP_dir / "output" / "DRX_33_unit_cell_report.csv",
        "Li4MgWO6_SG12": example_DRX_33_anisoADP_dir / "output" / "Li4MgWO6_SG12_unit_cell_report.csv"
    }


@pytest.fixture
def example_DRX_33_anisoADP_reference_peak_csvs(example_DRX_33_anisoADP_dir):
    """Paths to example_DRX_33_anisoADP reference peak list CSV files (both phases)."""
    return {
        "DRX_33": example_DRX_33_anisoADP_dir / "output" / "DRX_33_peak_list_report.csv",
        "Li4MgWO6_SG12": example_DRX_33_anisoADP_dir / "output" / "Li4MgWO6_SG12_peak_list_report.csv"
    }


@pytest.fixture
def example_DRX_33_anisoADP_reference_refined_params_csv(example_DRX_33_anisoADP_dir):
    """Path to example_DRX_33_anisoADP reference refined_parameters.csv file."""
    return example_DRX_33_anisoADP_dir / "output" / "refined_parameters.csv"
